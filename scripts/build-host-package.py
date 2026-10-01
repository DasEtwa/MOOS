#!/usr/bin/env python3
"""Build an unsigned MOOS Host Debian package; never install or sign it.

Only an independently authenticated distribution may provision this package.
Public verification material is an explicit publisher input, never a private
key. Personal images and a matching Buildroot Host runtime are optional together.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from moos_host_setup import BASE, FIRMWARE, SetupError, read_file
from moos_verification import (  # noqa: E402
    ARTIFACT_HOST_PACKAGE,
    ROLE_HOST_RELEASE,
    build_manifest,
    canonical_manifest_bytes,
)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def tar_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        # Explicit directories ensure their modes do not depend on build umask.
        dirs = set()
        for name in files:
            dirs.update(str(p) for p in Path(name).parents if str(p) != '.')
        for name in sorted(dirs):
            item = tarfile.TarInfo(name + '/')
            item.type, item.mode = tarfile.DIRTYPE, 0o755
            item.uid = item.gid = item.mtime = 0
            archive.addfile(item)
        for name, (data, mode) in sorted(files.items()):
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(data), mode
            item.uid = item.gid = item.mtime = 0
            archive.addfile(item, io.BytesIO(data))
    return output.getvalue()


def ar_bytes(members):
    result = bytearray(b'!<arch>\n')
    for name, data in members:
        result += f'{name + "/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(data):<10}`\n'.encode('ascii')
        result += data
        if len(data) % 2:
            result += b'\n'
    return result


def library_source(root, path):
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('Runtime source link escapes its declared directory.')
    return read_file(resolved)


def runtime_payload(root):
    """Inspect ELF dependencies; never execute a publisher-provided binary."""
    files = {}
    pending = [('bin/qemu-system-x86_64', root / 'bin/qemu-system-x86_64')]
    while pending:
        relative, source = pending.pop()
        if 'runtime/' + relative in files:
            continue
        data = library_source(root, source)
        if data[:5] != b'\x7fELF\x02' or data[18:20] != b'\x3e\x00':
            raise ValueError('Runtime must contain x86-64 ELF binaries.')
        files['runtime/' + relative] = data
        with tempfile.TemporaryDirectory(prefix='moos-elf-inspect-') as temporary:
            copied = Path(temporary) / 'elf'
            copied.write_bytes(data)
            result = subprocess.run(['/usr/bin/readelf', '-d', str(copied)],
                                    capture_output=True, check=True, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        for raw in re.findall(rb'\(NEEDED\).*?\[([^\]]+)\]', result.stdout):
            name = raw.decode('ascii')
            if name == 'ld-linux-x86-64.so.2':
                continue  # provided by the baseline OS libc6 package
            if not re.fullmatch(r'lib[A-Za-z0-9_+.-]+\.so(?:\.[0-9]+)*', name):
                raise ValueError('Unsafe runtime dependency name.')
            candidate = root / 'lib' / name
            if candidate.exists() or candidate.is_symlink():
                pending.append(('lib/' + name, candidate))
            elif name not in {'libc.so.6', 'libm.so.6', 'libpthread.so.0', 'libdl.so.2',
                              'librt.so.1', 'libresolv.so.2', 'libgcc_s.so.1', 'libstdc++.so.6'}:
                raise ValueError('Runtime dependency missing from Buildroot host/lib: ' + name)
        if len(files) > 100:
            raise ValueError('Runtime dependency closure is too large.')
    for name in sorted(FIRMWARE):
        files['runtime/share/qemu/' + name] = library_source(root, root / 'share/qemu' / name)
    return files


def build(version, public_key, output, images=None, runtime=None):
    if os.geteuid() == 0:
        raise ValueError('Build packages as an ordinary user, never root.')
    if not re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', version):
        raise ValueError('Use a numeric MAJOR.MINOR.PATCH package version.')
    public = read_file(public_key, 16384)
    if not re.fullmatch(rb'-----BEGIN PUBLIC KEY-----\n[A-Za-z0-9+/=\n]+-----END PUBLIC KEY-----\n?', public):
        raise ValueError('Only PEM PUBLIC KEY verification material is accepted.')
    subprocess.run(['/usr/bin/openssl', 'pkey', '-pubin', '-noout'], input=public,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                   env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    if bool(images) != bool(runtime):
        raise ValueError('Supply image and runtime directories together.')
    with tempfile.TemporaryDirectory(prefix='moos-package-build-') as temp:
        bundle = Path(temp) / 'admin.tar'
        module('admin_bundle_builder', ROOT / 'scripts/build-admin-release.py').build_bundle(ROOT, bundle, version)
        payload = {'admin.tar': read_file(bundle), 'public.pem': public,
                   'moos': read_file(ROOT / 'scripts/moos'),
                   'moos_protocol.py': read_file(ROOT / 'host/moos_protocol.py'),
                   'moos_verification.py': read_file(ROOT / 'host/moos_verification.py'),
                   'host-setup.py': read_file(ROOT / 'host/moos_host_setup.py'),
                   'admin-installer.py': read_file(ROOT / 'host/moos_admin_installer.py')}
        if images:
            payload.update({'images/' + name: read_file(images / name) for name in ('bzImage', 'rootfs.ext2')})
            # Validate the copied image, not a possibly changing source. Never
            # mount or run guest code during packaging.
            image = Path(temp) / 'rootfs.ext2'
            image.write_bytes(payload['images/rootfs.ext2'])
            validator = module('release_rootfs_validator', ROOT / 'scripts/validate-release-rootfs.py')
            validator.validate_shadow(validator.read_image_shadow(Path('/usr/sbin/debugfs'), image))
            payload.update(runtime_payload(runtime))
        manifest = build_manifest(
            artifact_type=ARTIFACT_HOST_PACKAGE,
            version=version,
            platform='linux',
            architecture='x86_64',
            channel='stable',
            role=ROLE_HOST_RELEASE,
            files=payload,
        )
        if sum(map(len, payload.values())) > 512 * 1024 * 1024:
            raise ValueError('Package payload exceeds 512 MiB.')
        prefix = 'usr/lib/moos/bootstrap/'
        files = {prefix + name: (data, 0o755 if name == 'moos' or name.startswith('runtime/bin/') else 0o644)
                 for name, data in payload.items()}
        # Existing staging requires the sibling lib directory. A normal
        # Buildroot QEMU carries a nonempty shared-library closure.
        if runtime and not any(n.startswith('runtime/lib/') for n in payload):
            raise ValueError('Runtime must supply its Buildroot shared-library closure.')
        files[prefix + 'manifest.json'] = (canonical_manifest_bytes(manifest), 0o644)
        files['usr/libexec/moos/moos-host-setup'] = (payload['host-setup.py'], 0o755)
        files['usr/libexec/moos/moos-admin-installer'] = (payload['admin-installer.py'], 0o755)
        files['usr/libexec/moos/moos_verification.py'] = (payload['moos_verification.py'], 0o644)
        files['usr/bin/moos'] = (b'#!/bin/sh\nexec /usr/bin/python3 -I /usr/lib/moos/bootstrap/moos "$@"\n', 0o755)
        files['usr/share/doc/moos-host/README.md'] = (read_file(ROOT / 'distribution/host/README.md'), 0o644)
        control = (f'Package: moos-host\nVersion: {version}\nArchitecture: amd64\n'
                   'Maintainer: MOOS maintainers\nSection: admin\nPriority: optional\n'
                   'Depends: python3 (>= 3.12), openssl, systemd (>= 257), bubblewrap, sudo, passwd, util-linux, libc6, libstdc++6, libgcc-s1\n'
                   'Description: MOOS trusted local Host bootstrap\n'
                   ' Requires an independently authenticated distribution channel.\n').encode()
        package = ar_bytes([('debian-binary', b'2.0\n'),
                            ('control.tar.gz', gzip.compress(tar_bytes({'control': (control, 0o644)}), mtime=0)),
                            ('data.tar.gz', gzip.compress(tar_bytes(files), mtime=0))])
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.moos-package-', dir=output.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(package)
                os.fchmod(stream.fileno(), 0o644)
            os.replace(temporary, output)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return hashlib.sha256(package).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--public-key', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--image-dir', type=Path)
    parser.add_argument('--runtime-dir', type=Path)
    args = parser.parse_args()
    try:
        digest = build(args.version, args.public_key, args.output, args.image_dir, args.runtime_dir)
    except (OSError, ValueError, SetupError, subprocess.SubprocessError) as error:
        print('error: ' + str(error), file=sys.stderr)
        return 1
    print('Unsigned package built. This does not establish publisher authenticity.')
    print('sha256: ' + digest)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
