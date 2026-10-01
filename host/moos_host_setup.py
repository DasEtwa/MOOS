#!/usr/bin/python3 -I
"""Narrow first-run operation, provisioned only by an authenticated OS package.

No source paths or executable arguments are accepted. Root ownership checks
protect an already authenticated package; they do not establish its origin.
"""
from __future__ import annotations

import argparse
import fcntl
import grp
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile


MODULE_ROOT = Path(__file__).resolve().parent
if not (MODULE_ROOT / 'moos_verification.py').is_file():
    MODULE_ROOT = Path('/usr/lib/moos/bootstrap')
sys.path.insert(0, str(MODULE_ROOT))
from moos_verification import (  # noqa: E402
    ARTIFACT_ADMIN_RELEASE,
    ARTIFACT_HOST_PACKAGE,
    PACKAGE_MANIFEST_NAME,
    PROVENANCE_PACKAGE_MEMBERSHIP,
    RESULT_INTEGRITY_ONLY,
    ROLE_HOST_RELEASE,
    ROLE_ADMIN_RELEASE,
    STATUS_VALID,
    VerificationPolicy,
    canonical_manifest_bytes,
    package_membership_result,
    parse_manifest,
)

PROGRAM = Path('/usr/libexec/moos/moos-host-setup')
ADMIN = Path('/usr/libexec/moos/moos-admin-installer')
PAYLOAD = Path('/usr/lib/moos/bootstrap')
TRUST = Path('/etc/moos/trust/admin-release.pem')
STATE = Path('/var/lib/moos-bootstrap')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'LANG': 'C'}
BASE = {'admin.tar', 'public.pem', 'moos', 'moos_protocol.py', 'moos_verification.py',
        'host-setup.py', 'admin-installer.py'}
FIRMWARE = {'bios-256k.bin', 'bios.bin', 'vgabios-stdvga.bin',
            'linuxboot_dma.bin', 'pvh.bin', 'kvmvapic.bin', 'efi-e1000.rom'}
LIMIT = 256 * 1024 * 1024


class SetupError(RuntimeError):
    pass


def protected(path: Path, *, directory=False, uid=0):
    """No symlinks in any parent, no writable ancestry, no hardlinked files."""
    current = path
    first = True
    while True:
        st = current.lstat()
        isdir = directory if first else True
        if (not (stat.S_ISDIR(st.st_mode) if isdir else stat.S_ISREG(st.st_mode))
                or st.st_uid != uid or st.st_mode & 0o022
                or (not isdir and st.st_nlink != 1)):
            raise SetupError('Protected MOOS files have unsafe permissions or links.')
        if current == current.parent:
            break
        current, first = current.parent, False


def read_file(path: Path, limit=LIMIT) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise SetupError('Invalid MOOS payload file.')
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if len(data) != before.st_size or identity(before) != identity(after):
        raise SetupError('MOOS payload changed while being read.')
    return data


def allowed_payload(name):
    return (name in BASE or name in {'images/bzImage', 'images/rootfs.ext2',
                                    'runtime/bin/qemu-system-x86_64'}
            or name in {'runtime/share/qemu/' + f for f in FIRMWARE}
            or re.fullmatch(r'runtime/lib/lib[A-Za-z0-9_+.-]+\.so(?:\.[0-9]+)*', name) is not None)


def _legacy_manifest_view(manifest):
    """Keep the small dictionary API used by the existing setup transaction."""
    return {
        'schemaVersion': manifest.schema_version,
        'version': manifest.version,
        'artifactType': manifest.artifact_type,
        'platform': manifest.platform,
        'architecture': manifest.architecture,
        'channel': manifest.channel,
        'role': manifest.role,
        'files': {name: record.sha256 for name, record in manifest.files.items()},
    }


def _check_package_manifest(manifest):
    if (manifest.artifact_type != ARTIFACT_HOST_PACKAGE or manifest.role != ROLE_HOST_RELEASE
            or manifest.platform != 'linux' or manifest.architecture != 'x86_64'
            or manifest.channel != 'stable'):
        raise SetupError('Bootstrap manifest policy is not authorized.')
    names = set(manifest.files)
    if not BASE <= names or len(names) > 128 or any(not allowed_payload(name) for name in names):
        raise SetupError('Incomplete bootstrap payload.')
    personal = names - BASE
    required = {'images/bzImage', 'images/rootfs.ext2', 'runtime/bin/qemu-system-x86_64'}
    required |= {'runtime/share/qemu/' + f for f in FIRMWARE}
    if personal and not required <= personal:
        raise SetupError('Personal image and runtime must be distributed together.')


def validate_manifest(data):
    """Validate through the canonical core and retain the old dict API."""
    try:
        manifest = parse_manifest(canonical_manifest_bytes(data))
        _check_package_manifest(manifest)
    except (TypeError, ValueError) as error:
        raise SetupError('Invalid bootstrap manifest.') from error
    return manifest.as_dict()


def exact_tree(root, names, check=protected):
    """Verify the whole tree that recursive staging will copy, including extras."""
    expected = set(names)
    directories = set()
    for name in expected:
        directories.update(str(p) for p in Path(name).parents if str(p) != '.')
    check(root, directory=True)
    actual = set()
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(parent) / name
            relative = str(path.relative_to(root))
            metadata = path.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                if relative not in directories:
                    raise SetupError('Unexpected directory in the protected runtime payload.')
                check(path, directory=True)
            else:
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or relative not in expected:
                    raise SetupError('Unexpected file or link in the protected runtime payload.')
                check(path)
                actual.add(relative)
    if actual != expected:
        raise SetupError('The protected runtime payload is incomplete.')


def load_payload(root=PAYLOAD, check=protected, copy_to=None):
    check(root / 'manifest.json')
    manifest_bytes = read_file(root / 'manifest.json', 65536)
    try:
        parsed = parse_manifest(manifest_bytes)
        _check_package_manifest(parsed)
    except ValueError as error:
        raise SetupError('Invalid canonical bootstrap manifest.') from error
    names = set(parsed.files)
    if 'images/bzImage' in names:
        exact_tree(root / 'runtime', {n.removeprefix('runtime/') for n in names if n.startswith('runtime/')}, check)
    total = 0
    files = {}
    for name in sorted(names):
        path = root / name
        check(path)
        data = read_file(path)
        total += len(data)
        if total > 512 * 1024 * 1024:
            raise SetupError('MOOS package exceeds its size limit.')
        files[name] = data
    evidence = package_membership_result(
        manifest_bytes,
        files,
        policy=VerificationPolicy(
            expected_artifact_type=ARTIFACT_HOST_PACKAGE,
            expected_role=ROLE_HOST_RELEASE,
            expected_platform='linux',
            expected_architecture='x86_64',
            expected_channel='stable',
        ),
    )
    if evidence.result != RESULT_INTEGRITY_ONLY or evidence.provenance != PROVENANCE_PACKAGE_MEMBERSHIP:
        detail = evidence.issues[0].message if evidence.issues else 'package integrity or policy rejected'
        raise SetupError('MOOS package integrity check failed: ' + detail)
    manifest = _legacy_manifest_view(parsed)
    for name, data in files.items():
        if copy_to is not None:
            target = copy_to / name
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_file(target, data, 0o755 if name.startswith('runtime/bin/') else 0o644)
    # These are public bytes only, never a private-key parser or signing call.
    public = read_file(root / 'public.pem', 16384)
    if not re.fullmatch(rb'-----BEGIN PUBLIC KEY-----\n[A-Za-z0-9+/=\n]+-----END PUBLIC KEY-----\n?', public):
        raise SetupError('The package must contain only a public verification key.')
    if copy_to is not None:
        atomic_file(copy_to / 'manifest.json', manifest_bytes)
    return manifest


def command(argv):
    result = subprocess.run(argv, cwd='/', env=ENV, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            check=False)
    if result.returncode:
        raise SetupError('A protected setup step failed. Run moos doctor; rerun setup after resolving the problem.')


def atomic_file(path, data, mode=0o644):
    fd, temporary = tempfile.mkstemp(prefix='.moos-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_dir(path, mode=0o755):
    if not path.exists() and not path.is_symlink():
        protected(path.parent, directory=True)
        path.mkdir(mode=mode)
        sync_dir(path.parent)
    protected(path, directory=True)


def trust_matches(public, path=TRUST, check=protected):
    if path.exists() or path.is_symlink():
        check(path)
        if read_file(path, 16384) != public:
            raise SetupError('The installed release key differs. Setup will not replace trust. Contact your provider.')
        return True
    return False


def version_guard(version, manifest_digest, state):
    if state is None:
        return
    if (not isinstance(state, dict) or set(state) != {'version', 'manifest'}
            or not re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', str(state['version']))
            or not re.fullmatch('[a-f0-9]{64}', str(state['manifest']))):
        raise SetupError('Bootstrap history is invalid; manual recovery is required.')
    parse = lambda v: tuple(map(int, v.split('.')))
    if parse(version) < parse(state['version']):
        raise SetupError('Setup refuses an older package. Deliberate rollback is an administrator operation.')
    if version == state['version'] and state['manifest'] != manifest_digest:
        raise SetupError('This package reuses a release identity with different contents.')


def local_operator(environment=os.environ):
    value = environment.get('SUDO_UID', '')
    if not re.fullmatch('[0-9]{1,10}', value) or int(value) == 0:
        raise SetupError('Run moos setup from the local account that will use MOOS.')
    account = pwd.getpwuid(int(value))
    if (account.pw_uid < 1000 or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', account.pw_name)
            or account.pw_shell.endswith(('nologin', '/false'))
            or environment.get('SUDO_USER') != account.pw_name):
        raise SetupError('The local operator identity could not be verified.')
    # The environment comes from sudo's root authorization boundary. The CLI
    # cannot pass an alternate user argument; direct root callers are rejected.
    return account


def load_admin(path=ADMIN):
    protected(path)
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader('installed_moos_admin', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def prepare_release(manifest, admin, payload=PAYLOAD):
    root = Path('/usr/lib/moos/admin-releases')
    ensure_dir(root)
    digest = manifest['files']['admin.tar']
    # Setup is not an update manager. Never switch an existing different release.
    if admin.CURRENT_LINK.is_symlink() and admin.CURRENT_LINK.lstat().st_uid != 0:
        raise SetupError('Unsafe administrator release pointer.')
    current = admin._linked_release_target(admin.CURRENT_LINK, root)
    if current is not None:
        exact_tree(current, set(admin.RELEASE_MEMBERS))
    if current is not None and current.name != digest:
        raise SetupError('An existing Host release is different. Setup preserves it; use the administrator update procedure.')
    with tempfile.TemporaryDirectory(prefix='.bootstrap-', dir=root) as tmp:
        extracted = Path(tmp) / 'release'
        extracted.mkdir()
        verification = admin.verify_archive_manifest(
            payload / 'admin.tar',
            policy=admin.VerificationPolicy(
                expected_artifact_type=ARTIFACT_ADMIN_RELEASE,
                expected_role=ROLE_ADMIN_RELEASE,
                expected_platform='linux',
                expected_architecture='x86_64',
                expected_channel='stable',
            ),
            provenance=admin.PROVENANCE_PACKAGE_MEMBERSHIP,
        )
        if verification.manifest != STATUS_VALID or verification.integrity != STATUS_VALID or verification.policy != 'ACCEPT':
            detail = verification.issues[0].message if verification.issues else 'administrator archive manifest rejected'
            raise SetupError('Administrator payload manifest is invalid: ' + detail)
        admin.extract_authenticated_bundle(payload / 'admin.tar', extracted)
        # Reuse is an integrity check, not a pointer switch: preserve rollback.
        if current is not None:
            if not admin._same_release(extracted, current, 0):
                raise SetupError('Installed Host release integrity check failed.')
            return current
        if admin.ROLLBACK_LINK.exists() or admin.ROLLBACK_LINK.is_symlink():
            raise SetupError('Rollback state exists without an active release; administrator recovery is required.')
        # Bootstrap never prunes other protected release directories, including
        # recoverable staging left by an interrupted administrator operation.
        return admin.activate_extracted_release(extracted, digest, root, admin.CURRENT_LINK)


def image_matches(manifest, path=Path('/var/lib/moos/instances/personal')):
    if not path.exists() and not path.is_symlink():
        return False
    protected(path, directory=True)
    if 'images/bzImage' not in manifest['files']:
        raise SetupError('Existing Personal data cannot be verified by this package; it has been preserved.')
    for name in ('bzImage', 'rootfs.ext2'):
        protected(path / name)
        if hashlib.sha256(read_file(path / name)).hexdigest() != manifest['files']['images/' + name]:
            raise SetupError('Existing Personal data differs; setup will not replace it.')
    return True


def runtime_matches(manifest):
    root = Path('/var/lib/moos/runtime/qemu-host')
    if not root.exists() and not root.is_symlink():
        return False
    exact_tree(root, {n.removeprefix('runtime/') for n in manifest['files'] if n.startswith('runtime/')})
    for name, digest in manifest['files'].items():
        if name.startswith('runtime/'):
            target = root / name.removeprefix('runtime/')
            protected(target)
            if hashlib.sha256(read_file(target)).hexdigest() != digest:
                raise SetupError('The staged runtime differs; setup will not replace it.')
    return True


def preflight_paths():
    # The older component installers assume protected state parents. Establish
    # that assumption before letting them mkdir/chmod/copy anything.
    for name in ('/etc/moos/trust', '/usr/lib/moos', '/usr/lib/moos/control-releases',
                 '/var/lib/moos', '/var/lib/moos/instances', '/var/lib/moos/runtime',
                 '/etc/systemd/system', '/usr/local/bin', '/usr/share/doc/moos'):
        path = Path(name)
        while not path.exists() and not path.is_symlink():
            path = path.parent
        protected(path, directory=True)
    launcher = Path('/usr/lib/moos/run-qemu.sh')
    if launcher.exists() or launcher.is_symlink():
        protected(launcher)


def control_ready(release):
    pointer = Path('/usr/lib/moos/control-current')
    if not pointer.exists() and not pointer.is_symlink():
        return False
    if not pointer.is_symlink() or pointer.lstat().st_uid != 0:
        raise SetupError('Unsafe local control installation.')
    target = pointer.resolve(strict=True)
    if target.parent != Path('/usr/lib/moos/control-releases'):
        raise SetupError('Unsafe local control release pointer.')
    protected(target, directory=True)
    mapping = {'host/moos_protocol.py': 'moos_protocol.py', 'host/moos_verification.py': 'moos_verification.py',
               'host/moos_runtime.py': 'moos_runtime.py',
               'host/moosd.py': 'moosd.py', 'scripts/moos': 'moos', 'scripts/moosd.py': 'moosd',
               'scripts/run-instance.sh': 'run-instance.sh', 'systemd/moosd.service': 'moosd.service',
               'systemd/moosd.socket': 'moosd.socket', 'HOST_GUEST_ISOLATION.md': 'HOST_GUEST_ISOLATION.md'}
    for source, name in mapping.items():
        protected(target / name)
        if read_file(release / source) != read_file(target / name):
            raise SetupError('Existing local control differs; setup preserves it. Use the administrator update procedure.')
    for unit in ('moosd.service', 'moosd.socket'):
        path = Path('/etc/systemd/system') / unit
        if not path.exists() and not path.is_symlink():
            return False
        protected(path)
        if read_file(path) != read_file(target / unit):
            raise SetupError('Local service configuration differs; setup will not replace it.')
    result = subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet', 'moosd.socket'],
                            cwd='/', env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=15, check=False)
    return result.returncode == 0


def apply(manifest, operator, payload=PAYLOAD):
    preflight_paths()
    admin = load_admin(payload / 'admin-installer.py')
    public = read_file(payload / 'public.pem', 16384)
    has_trust = trust_matches(public)
    # Preflight before any account or release changes.
    personal_present = image_matches(manifest)
    has_image = 'images/bzImage' in manifest['files']
    if has_image:
        staged_runtime = runtime_matches(manifest)
        if personal_present and not staged_runtime:
            raise SetupError('Personal exists without its runtime. Existing data is preserved; administrator recovery is required.')
    current = admin._linked_release_target(admin.CURRENT_LINK, admin.RELEASE_ROOT)
    if current is not None and current.name != manifest['files']['admin.tar']:
        raise SetupError('An existing Host release is different; setup will not replace it.')
    if not has_trust:
        ensure_dir(Path('/etc/moos'))
        ensure_dir(TRUST.parent)
        atomic_file(TRUST, public)
    try:
        release = prepare_release(manifest, admin, payload)
    except admin.InstallError as error:
        raise SetupError('The administrator payload or release state is invalid; existing data is preserved.') from error
    configured = control_ready(release)
    command([str(release / 'scripts/setup-runtime-user.sh'), '--source-root', str(release)])
    if has_image and not personal_present:
        command([str(release / 'scripts/stage-instance.sh'), '--id', 'personal',
                 '--image-dir', str(payload / 'images'), '--qemu',
                 str(payload / 'runtime/bin/qemu-system-x86_64')])
    if has_image and (not image_matches(manifest) or not runtime_matches(manifest)):
        raise SetupError('Personal staging could not be verified; setup has not completed.')
    # The existing installer owns its transaction and rollback. Repeating it is
    # explicit and safe; it does not touch Personal or Gateway credentials.
    if not configured:
        command([str(release / 'scripts/setup-control-plane.sh'), '--source-root', str(release)])
    group = grp.getgrnam('moos-control')
    if group.gr_gid == 0:
        raise SetupError('Unsafe local control group.')
    if group.gr_gid not in os.getgrouplist(operator.pw_name, operator.pw_gid):
        command(['/usr/sbin/usermod', '-a', '-G', 'moos-control', operator.pw_name])
        if group.gr_gid not in os.getgrouplist(operator.pw_name, operator.pw_gid):
            raise SetupError('The local access grant could not be verified.')
    # Validate service health explicitly, without starting the guest.
    command(['/usr/bin/systemctl', 'is-active', '--quiet', 'moosd.socket'])
    return {'host': 'ready', 'localGrant': 'ready',
            'personalImage': 'ready' if has_image else 'not_available',
            'guestShell': 'not_available'}


def check_host():
    if os.uname().machine != 'x86_64':
        raise SetupError('This bootstrap supports x86-64 Hosts only.')
    system = Path('/etc/os-release').read_text()
    if not re.search(r'^ID=ubuntu$', system, re.M) or not re.search(r'^VERSION_ID="26\.04"$', system, re.M):
        raise SetupError('This bootstrap currently targets Ubuntu 26.04 only.')
    if not Path('/run/systemd/system').is_dir() or not Path('/sys/fs/cgroup/cgroup.controllers').is_file():
        raise SetupError('MOOS needs systemd and unified resource controls.')
    for tool in ('bwrap', 'systemctl', 'systemd-run', 'openssl', 'useradd', 'usermod', 'flock', 'findmnt'):
        if shutil.which(tool, path=ENV['PATH']) is None:
            raise SetupError('A required Host tool is missing. Repair the package dependencies through the OS package manager.')
    result = subprocess.run(['/usr/bin/systemctl', '--version'], cwd='/', env=ENV,
                            capture_output=True, timeout=15, check=False)
    match = re.match(rb'systemd ([0-9]+)', result.stdout)
    if result.returncode or not match or int(match[1]) < 257:
        raise SetupError('MOOS requires systemd 257 or newer for protected component validation.')


def run_transaction(operator, source=PAYLOAD, state=STATE,
                    receipt=Path('/usr/lib/moos/bootstrap-state.json')):
    ensure_dir(state, 0o700)
    lock = state / 'setup.lock'
    if lock.exists() or lock.is_symlink():
        protected(lock)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'rb') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError('Another MOOS setup is running. Wait for it to finish.') from None
        with tempfile.TemporaryDirectory(prefix='.payload-', dir=state) as temporary:
            snapshot = Path(temporary)
            manifest = load_payload(source, copy_to=snapshot)
            digest = hashlib.sha256(read_file(snapshot / 'manifest.json', 65536)).hexdigest()
            history = state / 'accepted.json'
            previous = None
            if history.exists() or history.is_symlink():
                protected(history)
                previous = json.loads(read_file(history, 4096))
            version_guard(manifest['version'], digest, previous)
            # A failed transaction can resume this exact package; it cannot
            # roll back the highest accepted identity through a stale package.
            atomic_file(history, json.dumps({'version': manifest['version'], 'manifest': digest}).encode())
            command(['/usr/bin/openssl', 'pkey', '-pubin', '-in', str(snapshot / 'public.pem'), '-noout'])
            result = apply(manifest, operator, snapshot)
            if receipt.exists() or receipt.is_symlink():
                protected(receipt)
            atomic_file(receipt, json.dumps({'manifest': digest, 'result': result}, sort_keys=True).encode())
            return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--apply', action='store_true', help='prepare the fixed installed Host payload')
    action.add_argument('--check', action='store_true', help='inspect public package and Host prerequisites without mutation')
    args = parser.parse_args()
    if Path(__file__) != PROGRAM or (args.apply and os.geteuid() != 0):
        print('[BLOCKED] Use moos setup from the authenticated installed package.', file=sys.stderr)
        return 1
    try:
        protected(PROGRAM)
        check_host()
        if args.check:
            manifest = load_payload()
            print(json.dumps({'package': 'checked', 'personalImage': 'images/bzImage' in manifest['files'],
                              'guestShell': 'not_available'}))
            return 0
        os.umask(0o022)
        os.chdir('/')
        result = run_transaction(local_operator())
        print(json.dumps(result, sort_keys=True))
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as error:
        message = str(error) if isinstance(error, SetupError) else 'Setup could not finish safely. Run moos doctor; existing data has been preserved.'
        print('[BLOCKED] ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
