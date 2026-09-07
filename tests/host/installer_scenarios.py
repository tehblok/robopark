"""Run the real installer and verifier in a temporary host with fake APT/systemd."""
import io
import json
import os
import fcntl
import pty
import select
import time
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'apps/api/src'))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from robopark_api.services.ops.archives import build_archive, KIND_RELEASE

SECRETS = ('tt_fixture_secret', 'Strong!Fixture123', 'github_fixture_secret')


class InstallerScenarios(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='robopark-installer-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'root'
        (self.root / 'etc').mkdir(parents=True)
        (self.root / 'run/systemd/system').mkdir(parents=True)
        (self.root / 'etc/os-release').write_text('ID=armbian\nID_LIKE=debian\nVERSION_CODENAME=bookworm\n')
        self.bundle = self.base / 'bundle'
        shutil.copytree(REPO / 'deploy/installer', self.bundle)
        self.verifier = self.bundle / 'verifier/robopark_api/services/ops'
        self.verifier.mkdir(parents=True)
        for name in ('archives.py', 'release_signing.py'):
            shutil.copyfile(REPO / 'apps/api/src/robopark_api/services/ops' / name, self.verifier / name)
        self.payload = self.bundle / 'payload/robopark-release.zip'
        self.payload.parent.mkdir()
        self.key = Ed25519PrivateKey.generate()
        (self.bundle / 'keys').mkdir()
        (self.bundle / 'keys/release-public-key.pem').write_bytes(self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        self.source = self.base / 'source'
        self.source.mkdir()
        (self.source / 'VERSION').write_text('1.0.0\n')
        (self.source / 'run.sh').write_text('#!/bin/sh\necho release\n')
        (self.source / 'deploy/host').mkdir(parents=True)
        (self.source / 'deploy/host/robopark').write_text('#!/bin/sh\nexit 0\n')
        self.write_release()
        self.config = self.base / 'answers.env'
        self.config.write_text('TUNA_TOKEN=tt_fixture_secret\nTUNA_SUBDOMAIN=park\nTUNA_LOCATION=ru\nSEED_USERNAME=royal\nSEED_PASSWORD=Strong!Fixture123\nGITHUB_REPOSITORY=example/robopark\nGITHUB_TOKEN=github_fixture_secret\n')
        self.config.chmod(0o600)
        self.env = {'TMPDIR': str(self.base), 'ROBOPARK_TESTING': '1', 'ROBOPARK_ROOT': str(self.root), 'ARCH': 'aarch64', 'FREE_GIB': '12', 'PATH': str(REPO / 'tests/host/fake-bin') + ':' + os.environ['PATH']}
        self.env.pop('PYTHONPATH', None)

    def write_release(self, version='1.0.0'):
        self.payload.write_bytes(build_archive(kind=KIND_RELEASE, source_root=self.source, app_version=version, release_meta={'git_sha': 'a' * 40, 'migration_head': 'initial'}, signing_key=self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())))

    def run_installer(self, *args, success=True, **env):
        result = subprocess.run(['sh', str(self.bundle / 'install.sh'), *(args or ('--non-interactive', str(self.config)))], env={**self.env, **env}, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        for secret in SECRETS:
            self.assertNotIn(secret, result.stdout + result.stderr)
        return result

    def commands(self, name=None):
        path = self.root / 'commands.jsonl'
        values = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [v for v in values if name is None or v['name'] == name]

    def state(self):
        return json.loads((self.root / 'var/lib/robopark/ops/state/install.json').read_text())

    def test_clean_armbian_and_secret_boundary(self):
        self.run_installer()
        for filename in ('host.env', 'tuna.env', 'updater.env'):
            path = self.root / 'etc/robopark' / filename
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_uid, os.getuid())
        key = self.root / 'etc/robopark/release-public-key.pem'
        self.assertEqual(key.read_bytes(), (self.bundle / 'keys/release-public-key.pem').read_bytes())
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o644)
        self.assertEqual(key.stat().st_uid, os.getuid())
        self.assertEqual(self.state()['phase'], 'complete')
        self.assertEqual((self.root / 'opt/robopark/current/VERSION').read_text(), '1.0.0\n')
        self.assertTrue(os.access(self.root / 'opt/robopark/current/run.sh', os.X_OK))
        self.assertEqual((self.root / 'opt/robopark/host-tools').resolve(), self.root / 'opt/robopark/releases/1.0.0/deploy/host')
        for secret in SECRETS:
            self.assertNotIn(secret, json.dumps(self.commands()))
            self.assertNotIn(secret, json.dumps(self.state()))
        self.assertIn('https://repo.tuna.am/apt/', (self.root / 'etc/apt/sources.list.d/tuna.list').read_text())
        self.assertTrue(any('tuna.am' in call['args'] for call in self.commands('apt-get')))

    def test_ubuntu_amd64(self):
        (self.root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_CODENAME=noble\n')
        self.run_installer(ARCH='x86_64')
        self.assertIn('linux/ubuntu', (self.root / 'etc/apt/sources.list.d/docker.list').read_text())

    def test_preflight_failures_never_mutate_host(self):
        for env in ({'ARCH': 'armv7l'}, {'FREE_GIB': '5'}):
            self.run_installer(success=False, **env)
            self.assertFalse((self.root / 'etc/robopark').exists())
            self.assertFalse(self.commands('apt-get'))
        (self.root / 'etc/os-release').write_text('ID=fedora\n')
        self.run_installer(success=False)
        self.assertFalse(self.commands('apt-get'))

    def test_dpkg_repair_precedes_packages(self):
        self.run_installer(DPKG_INTERRUPTED='1')
        calls = self.commands()
        repair = next(i for i, v in enumerate(calls) if v['name'] == 'dpkg' and v['args'] == ['--configure', '-a'])
        apt = next(i for i, v in enumerate(calls) if v['name'] == 'apt-get')
        self.assertLess(repair, apt)

    def test_bounded_failure_and_resume(self):
        self.run_installer(success=False, APT_FAIL='1')
        self.assertEqual(len(self.commands('apt-get')), 4)
        self.assertEqual([v['args'] for v in self.commands('sleep')], [['2'], ['5'], ['10']])
        self.assertEqual(self.state()['phase'], 'packages')
        self.assertEqual(self.state()['status'], 'failed')
        self.run_installer('--resume', '--non-interactive', str(self.config))
        self.assertEqual(self.state()['phase'], 'complete')

    def test_rerun_and_resume_preserve_existing_secrets(self):
        self.run_installer()
        paths = [self.root / 'etc/robopark' / n for n in ('host.env', 'tuna.env', 'updater.env')]
        before = [p.read_bytes() for p in paths]
        self.config.write_text('TUNA_TOKEN=replacement\nSEED_PASSWORD=Replacement!1234\nGITHUB_TOKEN=replacement\n')
        self.run_installer()
        self.assertEqual([p.read_bytes() for p in paths], before)
        self.config.unlink()
        self.run_installer('--resume')
        self.assertEqual([p.read_bytes() for p in paths], before)

    def test_invalid_password_region_and_shell_injection_are_rejected(self):
        for content in ('SEED_PASSWORD=short\n', 'TUNA_LOCATION=moon\n', 'EVIL=$(touch /tmp/robopark-installer-injected)\n'):
            with self.config.open('a') as stream:
                stream.write(content)
            self.run_installer(success=False)
            self.assertFalse((self.root / 'opt/robopark/current').exists())
            self.config.write_text('TUNA_TOKEN=tt_fixture_secret\nTUNA_SUBDOMAIN=park\nSEED_PASSWORD=Strong!Fixture123\n')

    def test_tampering_is_rejected_before_version_directory(self):
        with zipfile.ZipFile(self.payload, 'r') as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        files['VERSION'] = b'tampered'
        with zipfile.ZipFile(self.payload, 'w') as archive:
            for name, value in files.items():
                archive.writestr(name, value)
        self.run_installer(success=False)
        self.assertFalse((self.root / 'opt/robopark/releases/1.0.0').exists())
        self.assertTrue((self.root / 'etc/robopark/release-public-key.pem').exists())
        self.assertEqual(self.state()['phase'], 'release')

    def test_verifier_comes_from_trusted_bootstrap(self):
        evil = self.source / 'apps/api/src/robopark_api/services/ops'
        evil.mkdir(parents=True)
        (evil / 'archives.py').write_text('raise RuntimeError("payload code executed")\n')
        self.write_release()
        self.run_installer()
        self.assertTrue((self.root / 'opt/robopark/current').is_symlink())

    def test_release_resume_reuses_config_and_completed_packages(self):
        valid = self.payload.read_bytes()
        self.payload.write_bytes(b'bad zip')
        self.run_installer(success=False)
        count = len(self.commands('apt-get'))
        self.payload.write_bytes(valid)
        self.config.unlink()
        self.run_installer('--resume')
        self.assertEqual(len(self.commands('apt-get')), count)
        self.assertEqual(self.state()['phase'], 'complete')

    def test_existing_current_outside_release_tree_is_rejected(self):
        (self.root / 'opt/robopark').mkdir(parents=True)
        (self.root / 'opt/robopark/current').symlink_to(self.base)
        self.run_installer(success=False)
        self.assertEqual((self.root / 'opt/robopark/current').resolve(), self.base)


    def test_debian_minimum_disk_boundary(self):
        (self.root / 'etc/os-release').write_text('ID=debian\nVERSION_CODENAME=bookworm\n')
        self.run_installer(FREE_GIB='6')
        self.assertEqual(self.state()['phase'], 'complete')

    def test_accepted_nondefault_region_and_literal_secret(self):
        with self.config.open('a') as stream:
            stream.write('TUNA_LOCATION=eu\nOPERATOR_SHARED_PASSWORD=literal$(false)#value\n')
        self.run_installer()
        text = (self.root / 'etc/robopark/host.env').read_text()
        self.assertIn("OPERATOR_SHARED_PASSWORD='literal$(false)#value'", text)
        self.assertIn("CORS_ORIGINS='https://park.eu.tuna.am'", text)

    def test_rejects_readable_config_and_existing_key_replacement(self):
        self.config.chmod(0o644)
        self.run_installer(success=False)
        self.assertFalse((self.root / 'etc/robopark/host.env').exists())
        self.config.chmod(0o600)
        self.run_installer()
        key = self.root / 'etc/robopark/release-public-key.pem'
        before = key.read_bytes()
        (self.bundle / 'keys/release-public-key.pem').write_bytes(b'other key')
        self.run_installer(success=False)
        self.assertEqual(key.read_bytes(), before)

    def test_rejects_symlinked_host_directory_before_mutation(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (self.root / 'opt').mkdir()
        (self.root / 'opt/robopark').symlink_to(outside, target_is_directory=True)
        self.run_installer(success=False)
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_lock_blocks_install(self):
        ops = self.root / 'var/lib/robopark/ops'
        ops.mkdir(parents=True)
        with (ops / 'install.lock').open('w') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.run_installer(success=False)
        self.assertFalse(self.commands('apt-get'))

    def test_large_profile_requires_sufficient_memory(self):
        (self.root / 'proc').mkdir()
        (self.root / 'proc/meminfo').write_text('MemTotal:        8388608 kB\n')
        with self.config.open('a') as stream:
            stream.write('UVICORN_WORKERS=4\n')
        self.run_installer(success=False)
        (self.root / 'proc/meminfo').write_text('MemTotal:        33554432 kB\n')
        self.run_installer()

    def test_interactive_wizard_never_echoes_secrets(self):
        answers = [SECRETS[0], 'park', '', '', SECRETS[1], '', '', 'example/robopark', SECRETS[2], '']
        child, master = pty.fork()
        if child == 0:
            os.execve('/bin/sh', ['sh', str(self.bundle / 'install.sh')], self.env)
        output, pending = b'', b''
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        _, status = os.waitpid(child, 0)
                        child = None
                        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                        break
                    if not chunk:
                        _, status = os.waitpid(child, 0)
                        child = None
                        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                        break
                    output += chunk
                    pending += chunk
                    if pending.endswith(b': ') and answers:
                        os.write(master, (answers.pop(0) + '\n').encode())
                        pending = b''
                pid, status = os.waitpid(child, os.WNOHANG)
                if pid:
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode())
                    child = None
                    break
            if child:
                pid, status = os.waitpid(child, os.WNOHANG)
                if pid:
                    child = None
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode())
                else:
                    self.fail('interactive wizard timed out; prompt count remaining=' + str(len(answers)))
        finally:
            os.close(master)
            if child:
                os.kill(child, 9)
                os.waitpid(child, 0)
        self.assertFalse(answers)
        for secret in SECRETS:
            self.assertNotIn(secret, output.decode())
        self.assertEqual(self.state()['phase'], 'complete')


if __name__ == '__main__':
    unittest.main(verbosity=2)
