#!/usr/bin/env python3
"""Unprivileged package, onboarding transaction and trust-boundary tests."""
from __future__ import annotations
import contextlib
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
import moos_host_setup as setup


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


cli = load('bootstrap_test_cli', ROOT / 'scripts/moos')
builder = load('bootstrap_test_builder', ROOT / 'scripts/build-host-package.py')
admin_tests = load('bootstrap_admin_tests', ROOT / 'tests/admin_release.py')
import moos_admin_installer as admin
from moos_verification import (  # noqa: E402
    ARTIFACT_HOST_PACKAGE,
    ROLE_HOST_RELEASE,
    build_manifest,
    canonical_manifest_bytes,
)
PUBLIC = ROOT / 'tests/fixtures/moos-admin-release-v1.pub'


def manifest(image=False):
    files = {name: {'sha256': 'a' * 64, 'size': 0} for name in setup.BASE}
    if image:
        files |= {name: {'sha256': 'a' * 64, 'size': 0} for name in ('images/bzImage', 'images/rootfs.ext2', 'runtime/bin/qemu-system-x86_64')}
        files |= {'runtime/share/qemu/' + f: {'sha256': 'a' * 64, 'size': 0} for f in setup.FIRMWARE}
    return build_manifest(
        artifact_type=ARTIFACT_HOST_PACKAGE, version='1.2.3', platform='linux',
        architecture='x86_64', channel='stable', role=ROLE_HOST_RELEASE,
        files=files,
    ).as_dict()


class ValidationTests(unittest.TestCase):
    def test_manifest_contract(self):
        for image in (False, True):
            self.assertEqual(setup.validate_manifest(manifest(image)), manifest(image))
        for bad in ({}, {'schemaVersion': True, 'version': '1.2.3', 'files': manifest()['files']},
                    manifest() | {'version': '../1'}, manifest() | {'other': 1}):
            with self.assertRaises(setup.SetupError): setup.validate_manifest(bad)
        for name in ('../escape', '/etc/passwd', 'runtime/lib/a.pem', 'images/rootfs.ext2'):
            data = manifest()
            data['files'][name] = 'a' * 64
            with self.assertRaises(setup.SetupError): setup.validate_manifest(data)

    def test_stale_and_reused_version_rejected(self):
        state = {'version': '1.2.3', 'manifest': 'a' * 64}
        setup.version_guard('1.2.3', 'a' * 64, state)
        setup.version_guard('1.3.0', 'b' * 64, state)
        setup.version_guard('1.0.0', 'a' * 64, None)
        for version, digest in (('1.2.2', 'a'*64), ('1.2.3', 'b'*64)):
            with self.assertRaises(setup.SetupError): setup.version_guard(version, digest, state)
        with self.assertRaises(setup.SetupError): setup.version_guard('1.2.3', 'a'*64, {})

    def test_opaque_files_and_trust_continuity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            file = root / 'public'
            file.write_bytes(PUBLIC.read_bytes())
            self.assertTrue(setup.trust_matches(file.read_bytes(), file, lambda p: None))
            before = file.read_bytes()
            with self.assertRaises(setup.SetupError): setup.trust_matches(b'other', file, lambda p: None)
            self.assertEqual(file.read_bytes(), before)
            self.assertFalse(setup.trust_matches(before, root/'missing', lambda p: None))
            link = root/'link'; link.symlink_to(file)
            with self.assertRaises(OSError): setup.read_file(link)
            hard = root/'hard'; os.link(file, hard)
            with self.assertRaises(setup.SetupError): setup.read_file(file)
            hard.unlink()
            fifo = root/'fifo'; os.mkfifo(fifo)
            with self.assertRaises(setup.SetupError): setup.read_file(fifo)
            with self.assertRaises(setup.SetupError): setup.read_file(file, 2)

    def test_unsafe_ancestry_and_hardlinks(self):
        good_dir = SimpleNamespace(st_mode=stat.S_IFDIR|0o755, st_uid=0, st_nlink=2)
        good_file = SimpleNamespace(st_mode=stat.S_IFREG|0o644, st_uid=0, st_nlink=1)
        for bad in (SimpleNamespace(st_mode=stat.S_IFDIR|0o777, st_uid=0, st_nlink=2),
                    SimpleNamespace(st_mode=stat.S_IFDIR|0o755, st_uid=1000, st_nlink=2),
                    SimpleNamespace(st_mode=stat.S_IFLNK|0o777, st_uid=0, st_nlink=1)):
            with patch.object(Path, 'lstat', autospec=True, side_effect=lambda p: good_file if str(p)=='/safe/file' else bad):
                with self.assertRaises(setup.SetupError): setup.protected(Path('/safe/file'))
        with patch.object(Path, 'lstat', autospec=True, side_effect=lambda p: good_file if str(p)=='/safe/file' else good_dir):
            setup.protected(Path('/safe/file'))
        good_file.st_nlink = 2
        with patch.object(Path, 'lstat', autospec=True, side_effect=lambda p: good_file if str(p)=='/safe/file' else good_dir):
            with self.assertRaises(setup.SetupError): setup.protected(Path('/safe/file'))

    def test_operator_bound_to_sudo_identity(self):
        account = SimpleNamespace(pw_uid=1000, pw_name='alice', pw_shell='/bin/bash', pw_gid=1000)
        with patch.object(setup.pwd, 'getpwuid', return_value=account):
            self.assertEqual(setup.local_operator({'SUDO_UID':'1000','SUDO_USER':'alice'}), account)
            for env in ({}, {'SUDO_UID':'0'}, {'SUDO_UID':'1000','SUDO_USER':'bob'}, {'SUDO_UID':'x'}):
                with self.assertRaises(setup.SetupError): setup.local_operator(env)

    def test_clean_environment_and_failure_redaction(self):
        with patch.dict(os.environ, {'PATH':'/evil','PYTHONPATH':'/evil','LD_PRELOAD':'/evil'}), \
             patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaises(setup.SetupError) as error: setup.command(['/usr/bin/true'])
            self.assertNotIn('/evil', str(error.exception))
            self.assertEqual(run.call_args.kwargs['env'], setup.ENV)
            self.assertEqual(run.call_args.kwargs['cwd'], '/')

    def test_checkout_entry_cannot_mutate(self):
        result = subprocess.run([sys.executable, '-I', str(ROOT/'host/moos_host_setup.py'), '--apply'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('authenticated installed package', result.stderr)


class FilesystemTests(unittest.TestCase):
    def test_exact_inventory_rejects_extra_files_links_and_missing_members(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'lib').mkdir();(root/'lib/libx.so').write_bytes(b'elf')
            checked=[]
            check=lambda p, **kw:checked.append((p,kw))
            setup.exact_tree(root, {'lib/libx.so'}, check)
            self.assertEqual(checked[0],(root,{'directory':True}))
            extra=root/'lib/extra';extra.write_bytes(b'other')
            with self.assertRaises(setup.SetupError):setup.exact_tree(root,{'lib/libx.so'},check)
            extra.unlink();extra.symlink_to(root/'lib/libx.so')
            with self.assertRaises(setup.SetupError):setup.exact_tree(root,{'lib/libx.so'},check)
            extra.unlink();(root/'lib/libx.so').unlink()
            with self.assertRaises(setup.SetupError):setup.exact_tree(root,{'lib/libx.so'},check)

    def payload(self,root):
        root.mkdir()
        files={name: b'public fixture' for name in setup.BASE}
        files['public.pem']=PUBLIC.read_bytes()
        for name,data in files.items():(root/name).write_bytes(data)
        data = build_manifest(
            artifact_type=ARTIFACT_HOST_PACKAGE, version='1.2.3', platform='linux',
            architecture='x86_64', channel='stable', role=ROLE_HOST_RELEASE,
            files=files,
        ).as_dict()
        (root/'manifest.json').write_bytes(canonical_manifest_bytes(data))
        return data

    def test_private_snapshot_is_the_verified_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';self.payload(source);snapshot=root/'snapshot';snapshot.mkdir()
            data=setup.load_payload(source,check=lambda p,**kw:None,copy_to=snapshot)
            (source/'admin.tar').write_bytes(b'changed after verification')
            self.assertEqual(hashlib.sha256((snapshot/'admin.tar').read_bytes()).hexdigest(),data['files']['admin.tar'])
            self.assertNotEqual((source/'admin.tar').read_bytes(),(snapshot/'admin.tar').read_bytes())

    def test_interrupted_history_retry_downgrade_and_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';data=self.payload(source)
            state=root/'state';state.mkdir();receipt=root/'receipt'
            real_load=setup.load_payload
            def loading(path,copy_to):return real_load(path,check=lambda p,**kw:None,copy_to=copy_to)
            with patch.object(setup,'ensure_dir'),patch.object(setup,'protected'), \
                 patch.object(setup,'load_payload',side_effect=loading),patch.object(setup,'command'), \
                 patch.object(setup,'apply',side_effect=setup.SetupError('interrupted')):
                with self.assertRaises(setup.SetupError):setup.run_transaction(None,source,state,receipt)
            self.assertTrue((state/'accepted.json').is_file());self.assertFalse(receipt.exists())
            self.assertFalse(list(state.glob('.payload-*')))
            result={'host':'ready','localGrant':'ready','personalImage':'not_available','guestShell':'not_available'}
            with patch.object(setup,'ensure_dir'),patch.object(setup,'protected'), \
                 patch.object(setup,'load_payload',side_effect=loading),patch.object(setup,'command'), \
                 patch.object(setup,'apply',return_value=result) as applying:
                self.assertEqual(setup.run_transaction(None,source,state,receipt),result)
                applying.assert_called_once()
                data['version']='1.2.2';(source/'manifest.json').write_bytes(canonical_manifest_bytes(data))
                with self.assertRaises(setup.SetupError):setup.run_transaction(None,source,state,receipt)
                self.assertEqual(applying.call_count,1)
                with (state/'setup.lock').open('rb') as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    with self.assertRaises(setup.SetupError):setup.run_transaction(None,source,state,receipt)

    def test_same_release_reuse_checks_root_and_preserves_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);payload=root/'payload';payload.mkdir()
            fixture=ROOT/'tests/fixtures/moos-admin-release-v1.tar'
            shutil.copy2(fixture,payload/'admin.tar')
            digest=hashlib.sha256(fixture.read_bytes()).hexdigest()
            releases=root/'releases';releases.mkdir();current=releases/digest;current.mkdir()
            admin.extract_authenticated_bundle(fixture,current,allow_legacy=True)
            pointer=root/'current';pointer.symlink_to(current)
            # These files are public fixtures; production protected() ownership
            # checking is exercised separately above. No fixture code is run.
            def same_legacy(left, right, uid):
                return all((left / name).read_bytes() == (right / name).read_bytes()
                           for name in admin.LEGACY_RELEASE_MEMBERS)
            fake=SimpleNamespace(CURRENT_LINK=pointer,ROLLBACK_LINK=root/'rollback',
                 RELEASE_MEMBERS=admin.LEGACY_RELEASE_MEMBERS,_linked_release_target=lambda *a:current,
                 extract_authenticated_bundle=lambda bundle, destination: admin.extract_authenticated_bundle(bundle, destination, allow_legacy=True),
                 _same_release=same_legacy,
                 verify_archive_manifest=lambda *a, **k: SimpleNamespace(manifest='VALID', integrity='VALID', policy='ACCEPT', issues=()),
                 VerificationPolicy=SimpleNamespace,
                 PROVENANCE_PACKAGE_MEMBERSHIP='packageMembership',
                 activate_extracted_release=lambda *a,**k:self.fail('repeat must not activate/prune'))
            data=manifest();data['files']['admin.tar']=digest
            real_temp=tempfile.TemporaryDirectory
            def tempdir(*a,**kw):return real_temp(prefix='.bootstrap-',dir=releases)
            visited=[]
            original_tree=setup.exact_tree
            def tree(path,names):
                visited.append(path);original_tree(path,names,check=lambda p,**kw:None)
            # Pointer owner is injected only at its one lstat check.
            actual_lstat=Path.lstat
            def metadata(path):
                st=actual_lstat(path)
                if path==pointer:
                    fields=list(st);fields[4]=0;return os.stat_result(fields)
                return st
            with patch.object(setup,'ensure_dir'),patch.object(setup,'exact_tree',side_effect=tree), \
                 patch.object(setup.tempfile,'TemporaryDirectory',side_effect=tempdir), \
                 patch.object(Path,'lstat',autospec=True,side_effect=metadata):
                self.assertEqual(setup.prepare_release(data,fake,payload),current)
            self.assertIn(current,visited)
            with patch.object(setup,'ensure_dir'),patch.object(setup,'exact_tree',side_effect=setup.SetupError('unsafe root')), \
                 patch.object(Path,'lstat',autospec=True,side_effect=metadata):
                with self.assertRaises(setup.SetupError):setup.prepare_release(data,fake,payload)

    def test_canonical_versions(self):
        for version in ('01.2.3','1.02.3','1.2.03'):
            with self.assertRaises(setup.SetupError):setup.validate_manifest(manifest()|{'version':version})


class TransactionTests(unittest.TestCase):
    def exercise(self, *, image=False, present=False, configured=False, fail=None, granted=False):
        calls = []
        def command(argv):
            calls.append(argv)
            if fail and fail in argv[0]: raise setup.SetupError('injected failure')
        admin = SimpleNamespace(InstallError=RuntimeError, CURRENT_LINK=Path('/unused/current'), RELEASE_ROOT=Path('/unused/releases'),
                                _linked_release_target=lambda *a: None)
        patches = [patch.object(setup, 'preflight_paths'), patch.object(setup, 'load_admin', return_value=admin),
                   patch.object(setup, 'read_file', return_value=PUBLIC.read_bytes()),
                   patch.object(setup, 'trust_matches', return_value=True),
                   patch.object(setup, 'image_matches', side_effect=[present, True]),
                   patch.object(setup, 'runtime_matches', return_value=True),
                   patch.object(setup, 'prepare_release', return_value=Path('/protected/release')),
                   patch.object(setup, 'control_ready', return_value=configured),
                   patch.object(setup, 'command', side_effect=command),
                   patch.object(setup.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=123)),
                   patch.object(setup.os, 'getgrouplist', side_effect=([123] if granted else [], [123]))]
        with contextlib.ExitStack() as stack:
            for p in patches: stack.enter_context(p)
            try: result = setup.apply(manifest(image), SimpleNamespace(pw_name='alice', pw_gid=1000))
            except setup.SetupError: result = None
        return calls, result

    def test_clean_host_order_and_narrow_grant(self):
        calls, result = self.exercise(image=True)
        self.assertEqual([Path(c[0]).name for c in calls], ['setup-runtime-user.sh', 'stage-instance.sh', 'setup-control-plane.sh', 'usermod', 'systemctl'])
        self.assertEqual(calls[-2], ['/usr/sbin/usermod','-a','-G','moos-control','alice'])
        self.assertEqual(result['guestShell'], 'not_available')

    def test_repeat_preserves_personal_and_active_control(self):
        calls, result = self.exercise(image=True, present=True, configured=True, granted=True)
        self.assertEqual([Path(c[0]).name for c in calls], ['setup-runtime-user.sh', 'systemctl'])
        self.assertEqual(result['personalImage'], 'ready')

    def test_host_only_does_not_claim_personal(self):
        calls, result = self.exercise()
        self.assertNotIn('stage-instance.sh', [Path(c[0]).name for c in calls])
        self.assertEqual(result['personalImage'], 'not_available')

    def test_failures_stop_before_grant_and_retry(self):
        for failure in ('setup-runtime', 'stage-instance', 'setup-control'):
            calls, result = self.exercise(image=True, fail=failure)
            self.assertIsNone(result)
            self.assertNotIn('usermod', [Path(c[0]).name for c in calls])
        calls, result = self.exercise(image=True, present=True, configured=False)
        self.assertIsNotNone(result)
        self.assertNotIn('stage-instance.sh', [Path(c[0]).name for c in calls])


class CliTests(unittest.TestCase):
    def args(self, **kw):
        return SimpleNamespace(command='setup', explain=None, prepare=False, non_interactive=True,
                               socket=cli.DEFAULT_SOCKET, expect_fingerprint=None, remote=False, **kw)

    def test_checkout_prepare_never_calls_sudo(self):
        args=self.args(); args.prepare=True
        with patch.object(cli, 'installed_bootstrap_cli', return_value=False), patch.object(cli.subprocess,'run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.bootstrap_setup(args),1)
            run.assert_not_called()

    def test_missing_bootstrap_and_noninteractive_do_not_elevate(self):
        probe = SimpleNamespace(trusted_file=lambda *a, **k: 'missing', personal=lambda p:'unavailable', command=lambda a:(0,b'{}'))
        with patch.object(cli,'installed_bootstrap_cli',return_value=True), patch.object(cli,'HostProbe',return_value=probe), patch.object(cli.subprocess,'run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.bootstrap_setup(self.args()),1)
            probe.trusted_file=lambda *a, **k:'present'
            with patch.object(cli,'bootstrap_receipt',return_value=None):
                self.assertEqual(cli.bootstrap_setup(self.args()),1)
            run.assert_not_called()

    def test_doctor_never_enters_mutation(self):
        args=self.args(); args.command='doctor'
        with patch.object(cli,'installed_bootstrap_cli',side_effect=AssertionError):
            self.assertIsNone(cli.bootstrap_setup(args))

    def test_repeat_is_read_only_and_reports_release_shell_separately(self):
        receipt={'host':'ready','localGrant':'ready','personalImage':'ready','guestShell':'not_available'}
        probe=SimpleNamespace(trusted_file=lambda *a,**k:'present',personal=lambda p:'stopped',command=lambda a:(0,b'{}'))
        output=io.StringIO()
        with patch.object(cli,'installed_bootstrap_cli',return_value=True), patch.object(cli,'HostProbe',return_value=probe), \
             patch.object(cli,'bootstrap_receipt',return_value=receipt),patch.object(cli.subprocess,'run') as run,contextlib.redirect_stdout(output):
            self.assertEqual(cli.bootstrap_setup(self.args()),0)
            run.assert_not_called()
        self.assertIn('Guest shell: NOT AVAILABLE',output.getvalue())
        self.assertIn('Personal MOOS: stopped',output.getvalue())

    def test_session_refresh_does_not_repeat_grant(self):
        receipt={'host':'ready','localGrant':'ready','personalImage':'ready','guestShell':'not_available'}
        probe=SimpleNamespace(trusted_file=lambda *a,**k:'present',personal=lambda p:'denied',command=lambda a:(0,b'{}'))
        output=io.StringIO()
        with patch.object(cli,'installed_bootstrap_cli',return_value=True),patch.object(cli,'HostProbe',return_value=probe), \
             patch.object(cli,'bootstrap_receipt',return_value=receipt),patch.object(setup.grp,'getgrnam',return_value=SimpleNamespace(gr_gid=123)), \
             patch.object(setup.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='alice',pw_gid=1000)), \
             patch.object(os,'getgrouplist',return_value=[1000,123]),patch.object(os,'getgroups',return_value=[1000]), \
             patch.object(os,'getgid',return_value=1000),patch.object(cli.subprocess,'run') as run,contextlib.redirect_stdout(output):
            self.assertEqual(cli.bootstrap_setup(self.args()),1)
            run.assert_not_called()
        self.assertIn('Log out and back in',output.getvalue())

    def test_missing_dependency_check_blocks_before_authorization(self):
        args=self.args();args.prepare=True
        probe=SimpleNamespace(trusted_file=lambda *a,**k:'present',command=lambda a:(1,b'private raw details'))
        output=io.StringIO()
        with patch.object(cli,'installed_bootstrap_cli',return_value=True),patch.object(cli,'HostProbe',return_value=probe), \
             patch.object(cli.subprocess,'run') as run,contextlib.redirect_stdout(output):
            self.assertEqual(cli.bootstrap_setup(args),1)
            run.assert_not_called()
        self.assertNotIn('private raw details',output.getvalue())

    def test_explicit_remote_checks_do_not_change_local_grants(self):
        args=self.args();args.remote=True
        receipt={'host':'ready','localGrant':'ready','personalImage':'ready','guestShell':'not_available'}
        probe=SimpleNamespace(trusted_file=lambda *a,**k:'present',personal=lambda p:'stopped',command=lambda a:(0,b'{}'))
        checks=[cli.Check('gateway','remote','issue','Gateway is missing')]
        with patch.object(cli,'installed_bootstrap_cli',return_value=True),patch.object(cli,'HostProbe',return_value=probe), \
             patch.object(cli,'bootstrap_receipt',return_value=receipt),patch.object(cli,'collect_checks',return_value=checks), \
             patch.object(cli.subprocess,'run') as run,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.bootstrap_setup(args),1)
            run.assert_not_called()

    def test_explicit_prepare_uses_fixed_sudo_and_clean_environment(self):
        args=self.args();args.prepare=True
        receipt={'host':'ready','localGrant':'ready','personalImage':'ready','guestShell':'not_available'}
        probe=SimpleNamespace(trusted_file=lambda *a, **k:'present',personal=lambda p:'stopped',command=lambda a:(0,b'{}'))
        with patch.object(cli,'installed_bootstrap_cli',return_value=True), patch.object(cli,'HostProbe',return_value=probe), \
             patch.object(cli,'bootstrap_receipt',side_effect=[None,receipt]), \
             patch.object(cli.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.bootstrap_setup(args),0)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/sudo','-n','--',cli.BOOTSTRAP_PROGRAM,'--apply'])
            self.assertEqual(run.call_args.kwargs['cwd'],'/')
            self.assertNotIn('PYTHONPATH',run.call_args.kwargs['env'])


class PackageTests(unittest.TestCase):
    def test_deterministic_real_package_and_tamper(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            first,second=root/'first.deb',root/'second.deb'
            source=root/'source'
            admin_tests.copy_release_source(source)
            for name in ('scripts/build-admin-release.py', 'host/moos_admin_installer.py',
                         'host/moos_host_setup.py', 'host/moos_verification.py', 'distribution/host/README.md'):
                target=source/name;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(ROOT/name,target)
            with patch.object(builder,'ROOT',source):
                self.assertEqual(builder.build('0.1.0',PUBLIC,first),builder.build('0.1.0',PUBLIC,second))
            self.assertEqual(first.read_bytes(),second.read_bytes())
            subprocess.run(['/usr/bin/dpkg-deb','--extract',str(first),str(root/'extract')],check=True,capture_output=True)
            subprocess.run(['/usr/bin/dpkg-deb','--control',str(first),str(root/'control')],check=True,capture_output=True)
            self.assertEqual({p.name for p in (root/'control').iterdir()},{'control'})
            payload=root/'extract/usr/lib/moos/bootstrap'
            installed_verifier=root/'extract/usr/libexec/moos/moos_verification.py'
            self.assertTrue(installed_verifier.is_file())
            self.assertEqual(installed_verifier.read_bytes(), (ROOT/'host/moos_verification.py').read_bytes())
            m=setup.load_payload(payload,check=lambda p:None)
            self.assertEqual(set(m['files']),setup.BASE)
            self.assertFalse((root/'extract/etc/moos/trust/admin-release.pem').exists())
            subprocess.run([sys.executable,'-I',str(payload/'moos'),'--help'],check=True,capture_output=True)
            (payload/'admin.tar').write_bytes(b'tampered')
            with self.assertRaises(setup.SetupError): setup.load_payload(payload,check=lambda p:None)
            (payload/'admin.tar').unlink()
            with self.assertRaises(OSError): setup.load_payload(payload,check=lambda p:None)

    def test_rejects_invalid_public_material_and_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); public=root/'public';public.write_bytes(b'not public material')
            for version,key in (('bad',PUBLIC),('0.1.0',public)):
                with self.assertRaises(ValueError): builder.build(version,key,root/'out.deb')
            with self.assertRaises(ValueError): builder.build('0.1.0',PUBLIC,root/'out.deb',root,None)

    def test_runtime_source_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'runtime').mkdir();(root/'outside').write_bytes(b'no')
            link=root/'runtime/link';link.symlink_to(root/'outside')
            with self.assertRaises(ValueError):builder.library_source(root/'runtime',link)


if __name__=='__main__':unittest.main()
