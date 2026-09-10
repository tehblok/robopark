"""Run the real installer and verifier in a temporary host with fake APT/systemd."""
import io
import runpy
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
from unittest import mock
import zipfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'apps/api/src'))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from robopark_api.services.ops.archives import build_archive, inspect_archive, KIND_RELEASE

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
        shutil.copytree(REPO / 'deploy/host', self.source / 'deploy/host', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(REPO / 'deploy/systemd', self.source / 'deploy/systemd')
        shutil.copyfile(REPO / 'deploy/docker-compose.yml', self.source / 'deploy/docker-compose.yml')
        shutil.copyfile(REPO / 'deploy/tuna-http.sh', self.source / 'deploy/tuna-http.sh')
        self.write_release()
        self.config = self.base / 'answers.env'
        self.config.write_text('TUNA_TOKEN=tt_fixture_secret\nTUNA_SUBDOMAIN=park\nTUNA_LOCATION=ru\nSEED_USERNAME=royal\nSEED_PASSWORD=Strong!Fixture123\nGITHUB_REPOSITORY=example/robopark\nGITHUB_TOKEN=github_fixture_secret\n')
        self.config.chmod(0o600)
        self.env = {'TMPDIR': str(self.base), 'ROBOPARK_TESTING': '1', 'ROBOPARK_ROOT': str(self.root), 'ARCH': 'aarch64', 'FREE_GIB': '12', 'PATH': str(REPO / 'tests/host/fake-bin') + ':' + str(Path(sys.executable).parent) + ':' + os.environ['PATH']}
        self.env.pop('PYTHONPATH', None)

    def write_release(self, version='1.0.0'):
        self.payload.write_bytes(build_archive(kind=KIND_RELEASE, source_root=self.source, app_version=version, release_meta={'git_sha': 'a' * 40, 'migration_head': 'initial'}, signing_key=self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())))

    def run_installer(self, *args, success=True, **env):
        result = subprocess.run(['sh', str(self.bundle / 'install.sh'), *(args or ('--non-interactive', str(self.config)))], env={**self.env, **env}, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        for secret in SECRETS:
            self.assertNotIn(secret, result.stdout + result.stderr)
        return result

    def run_start(self, *args, success=True, **env):
        result = subprocess.run(
            ['sh', str(self.bundle / 'START.sh'), *args],
            env={**self.env, **env}, text=True, capture_output=True,
        )
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

    def test_services_are_installed_and_tuna_starts_after_readiness(self):
        self.run_installer()
        installed = self.root / 'etc/systemd/system'
        for source in (self.source / 'deploy/systemd').iterdir():
            target = installed / source.name
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        calls = self.commands()
        def index(name, args):
            return next(i for i, item in enumerate(calls) if item['name'] == name and item['args'] == args)
        reload = index('systemctl', ['daemon-reload'])
        start = index('systemctl', ['start', 'robopark.service'])
        tunnel = index('systemctl', ['start', 'robopark-tuna.service'])
        ready = next(i for i, item in enumerate(calls) if item['name'] == 'curl' and 'http://127.0.0.1:8080/api/health/ready' in item['args'])
        self.assertLess(reload, start)
        self.assertLess(start, ready)
        self.assertLess(ready, tunnel)
        enabled = [arg for call in self.commands('systemctl') if call['args'][0] == 'enable' for arg in call['args'][1:]]
        for name in ('docker.service', 'robopark.service', 'robopark-tuna.service', 'robopark-updater.service', 'robopark-doctor.timer', 'robopark-watchdog.timer', 'robopark-update-check.timer'):
            self.assertIn(name, enabled)

    def test_start_performs_install_with_defaults_and_enables_autostart(self):
        preset = self.bundle / '.robopark-preset.env'
        preset.write_text(
            'TUNA_TOKEN=tt_fixture_secret\nTUNA_SUBDOMAIN=park\n'
            'SEED_USERNAME=royal\nSEED_PASSWORD=Strong!Fixture123\n'
        )
        preset.chmod(0o600)
        self.run_start('install')
        updater = (self.root / 'etc/robopark/updater.env').read_text()
        host = (self.root / 'etc/robopark/host.env').read_text()
        self.assertIn("GITHUB_REPOSITORY='tehblok/robopark'", updater)
        self.assertIn("GITHUB_ENABLED='true'", updater)
        self.assertIn("UVICORN_WORKERS='2'", host)
        enabled = [arg for call in self.commands('systemctl') if call['args'][0] == 'enable' for arg in call['args'][1:]]
        self.assertIn('robopark.service', enabled)
        self.assertIn('robopark-tuna.service', enabled)
        self.assertIn('robopark-watchdog.timer', enabled)
        for name in ('data', 'api-ops'):
            path = self.root / 'var/lib/robopark' / name
            self.assertTrue(path.is_dir())
            self.assertTrue(any(call['args'] == ['10001:10001', str(path)] for call in self.commands('chown')))
        public = self.root / 'var/lib/robopark/ops/public'
        self.assertEqual(stat.S_IMODE(public.stat().st_mode), 0o755)
        config = self.root / 'var/lib/robopark/ops/state/current-compose.json'
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
        document = json.loads(config.read_text())
        self.assertEqual(document['services']['api']['image'], 'sha256:' + '1' * 64)
        self.assertNotIn('ops-agent', document['services'])
        api = document['services']['api']
        key_path = api['environment']['OPS_RELEASE_PUBLIC_KEY_PATH']
        key_mount = next(mount for mount in api['volumes'] if mount['target'] == key_path)
        self.assertTrue(key_mount['read_only'])
        self.assertEqual(key_mount['source'], str(self.root / 'etc/robopark/release-public-key.pem'))
        self.assertEqual(Path(key_mount['source']).read_bytes(), (self.bundle / 'keys/release-public-key.pem').read_bytes())
        self.assertEqual(stat.S_IMODE(Path(key_mount['source']).stat().st_mode), 0o644)

    def test_doctor_output_directories_are_provisioned_root_only(self):
        self.run_installer()
        for relative in ('var/lib/robopark/diagnostics', 'var/log/robopark'):
            path = self.root / relative
            self.assertTrue(path.is_dir())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            self.assertTrue(any(call['args'] == ['0:0', str(path)] for call in self.commands('chown')))

    def test_stopped_docker_is_started_and_ready_before_bootstrap_build(self):
        self.run_installer(DOCKER_STOPPED='1')
        calls = self.commands()
        start = next(i for i, call in enumerate(calls) if call['name'] == 'systemctl' and call['args'] == ['start', 'docker.service'])
        ready = next(i for i, call in enumerate(calls) if call['name'] == 'docker' and call['args'] == ['info'])
        build = next(i for i, call in enumerate(calls) if call['name'] == 'docker' and 'build' in call['args'])
        self.assertLess(start, ready)
        self.assertLess(ready, build)

    def test_docker_start_failure_never_builds_or_publishes_runtime(self):
        self.run_installer(success=False, DOCKER_STOPPED='1', DOCKER_START_FAIL='1')
        self.assertFalse(any('build' in call['args'] for call in self.commands('docker')))
        self.assertFalse((self.root / 'var/lib/robopark/ops/state/current-compose.json').exists())

    def test_readiness_failure_never_starts_tuna_and_resume_recovers(self):
        self.run_installer(success=False, API_UNREADY='1')
        self.assertEqual(self.state()['phase'], 'services')
        self.assertFalse(any(call['args'] == ['start', 'robopark-tuna.service'] for call in self.commands('systemctl')))
        self.run_installer('--resume')
        self.assertEqual(self.state()['phase'], 'complete')

    def test_public_https_is_verified_after_tuna_start(self):
        self.run_installer()
        calls = self.commands()
        tunnel = next(i for i, call in enumerate(calls) if call['name'] == 'systemctl' and call['args'] == ['start', 'robopark-tuna.service'])
        https = next(i for i, call in enumerate(calls) if call['name'] == 'curl' and call['args'][-1] == 'https://park.ru.tuna.am/')
        self.assertLess(tunnel, https)

    def test_public_https_failure_is_retried_and_resumable(self):
        self.run_installer(success=False, PUBLIC_HTTPS_UNREADY='1')
        public_checks = [call for call in self.commands('curl') if call['args'][-1] == 'https://park.ru.tuna.am/']
        self.assertEqual(len(public_checks), 30)
        self.assertEqual(self.state()['phase'], 'services')
        self.run_installer('--resume')
        self.assertEqual(self.state()['phase'], 'complete')

    def test_failed_image_build_never_publishes_runtime_or_starts_app(self):
        result = self.run_installer(success=False, BUILD_FAIL='1')
        self.assertIn('[5/5] Запуск контейнеров и служб', result.stdout)
        self.assertIn('docker_command_failed', result.stderr)
        self.assertFalse((self.root / 'var/lib/robopark/ops/state/current-compose.json').exists())
        self.assertFalse(any(call['args'] == ['start', 'robopark.service'] for call in self.commands('systemctl')))

    def test_start_automatically_resumes_an_incomplete_install(self):
        self.run_installer(success=False, BUILD_FAIL='1')
        result = self.run_start()
        self.assertIn('Найдена незавершённая установка', result.stdout)
        self.assertEqual(self.state()['phase'], 'complete')

    def test_newer_installer_resumes_before_initial_runtime_was_published(self):
        self.run_installer(success=False, BUILD_FAIL='1')
        self.assertFalse((self.root / 'var/lib/robopark/ops/state/signing-trust.json').exists())
        self.write_release('1.0.1')

        self.run_installer('--resume')

        self.assertEqual((self.root / 'opt/robopark/current').resolve().name, '1.0.1')
        self.assertEqual(self.state()['phase'], 'complete')

    def test_newer_installer_rebuilds_images_after_initial_service_failure(self):
        self.run_installer(success=False, API_UNREADY='1')
        config = self.root / 'var/lib/robopark/ops/state/current-compose.json'
        self.assertEqual(json.loads(config.read_text())['x-robopark-release'].split('/')[-1], '1.0.0')
        builds_before = len([call for call in self.commands('docker') if 'build' in call['args']])
        self.write_release('1.0.1')

        self.run_installer('--resume')

        self.assertEqual(json.loads(config.read_text())['x-robopark-release'].split('/')[-1], '1.0.1')
        builds_after = len([call for call in self.commands('docker') if 'build' in call['args']])
        self.assertEqual(builds_after - builds_before, 2)
        self.assertEqual(self.state()['phase'], 'complete')

    def test_resume_preserves_installed_runtime_and_does_not_rebuild(self):
        self.run_installer()
        config = self.root / 'var/lib/robopark/ops/state/current-compose.json'
        before = config.read_bytes()
        self.run_installer('--resume', BUILD_FAIL='1')
        self.assertEqual(config.read_bytes(), before)

    def test_start_update_uses_candidate_ota_and_preserves_existing_data_and_config(self):
        self.run_installer()
        data = self.root / 'var/lib/robopark/data/operator-state.txt'
        data.write_text('keep-me')
        host_env = self.root / 'etc/robopark/host.env'
        before = host_env.read_bytes()
        (self.source / 'VERSION').write_text('1.0.1\n')
        for name in (
            'apps/api/Dockerfile', 'apps/api/pyproject.toml', 'apps/api/uv.lock',
            'apps/web/Dockerfile', 'apps/web/package.json', 'apps/web/package-lock.json',
            'deploy/Dockerfile.api-tests', 'scripts/verify.sh',
        ):
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        self.write_release('1.0.1')

        result = self.run_start('update')

        self.assertIn('Локальное обновление завершено', result.stdout)
        self.assertIn('Целостность архива проверена', result.stdout)
        self.assertIn('Начинаю сборку', result.stdout)
        self.assertEqual((self.root / 'opt/robopark/current/VERSION').read_text(), '1.0.1\n')
        self.assertEqual(data.read_text(), 'keep-me')
        self.assertEqual(host_env.read_bytes(), before)

    def test_start_update_rejects_tampered_payload_without_changing_install(self):
        self.run_installer()
        current = (self.root / 'opt/robopark/current').resolve()
        data = self.root / 'var/lib/robopark/data/operator-state.txt'
        data.write_text('keep-me')
        raw = bytearray(self.payload.read_bytes())
        raw[-1] ^= 1
        self.payload.write_bytes(raw)

        result = self.run_start('update', success=False)

        self.assertIn('ОБНОВЛЕНИЕ НЕ УСТАНОВЛЕНО', result.stderr)
        self.assertEqual((self.root / 'opt/robopark/current').resolve(), current)
        self.assertEqual(data.read_text(), 'keep-me')

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


    def assert_retained_signed_release(self):
        current = self.root / 'opt/robopark/current'
        with zipfile.ZipFile(self.payload) as archive:
            for name in ('manifest.json', 'manifest.sig'):
                path = current / name
                self.assertTrue(path.is_file(), f'{name} must be retained')
                self.assertEqual(path.read_bytes(), archive.read(name))
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
                self.assertEqual(path.stat().st_uid, os.getuid())
        # A consumer needs no bootstrap ZIP to verify the complete installed tree.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for path in sorted(current.resolve().rglob('*')):
                if path.is_file():
                    archive.writestr(path.relative_to(current.resolve()).as_posix(), path.read_bytes())
        verified = inspect_archive(buffer.getvalue(), expected_kind=KIND_RELEASE, public_key=(self.root / 'etc/robopark/release-public-key.pem').read_bytes())
        self.assertEqual(verified.app_version, '1.0.0')
        self.assertEqual(verified.git_sha, 'a' * 40)

    def test_signed_metadata_survives_clean_install_and_resume(self):
        self.run_installer()
        self.assert_retained_signed_release()
        self.config.unlink()
        self.run_installer('--resume')
        self.assert_retained_signed_release()

    def test_signed_metadata_is_retained_after_failed_release_resume(self):
        valid = self.payload.read_bytes()
        self.payload.write_bytes(b'bad zip')
        self.run_installer(success=False)
        self.payload.write_bytes(valid)
        self.config.unlink()
        self.run_installer('--resume')
        self.assert_retained_signed_release()

    def test_resume_rejects_modified_retained_signature(self):
        self.run_installer()
        signature = self.root / 'opt/robopark/current/manifest.sig'
        self.assertTrue(signature.is_file(), 'authenticated signature must be retained')
        signature.write_bytes(b'tampered signature')
        self.run_installer('--resume', success=False)
        self.assertEqual(signature.read_bytes(), b'tampered signature')

    def test_unrelated_installed_regular_package_cannot_override_bootstrap(self):
        unrelated = self.base / 'unrelated-site-packages'
        package = unrelated / 'robopark_api'
        package.mkdir(parents=True)
        marker = self.base / 'unrelated-package-imported'
        (package / '__init__.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError("unrelated verifier package executed")\n')
        self.run_installer(PYTHONPATH=str(unrelated))
        self.assertFalse(marker.exists())
        self.assert_retained_signed_release()

    def test_preloaded_unrelated_verifier_modules_cannot_override_bootstrap(self):
        self.run_installer()
        marker = self.base / 'unrelated-module-used'
        script = self.base / 'preloaded-verifier.py'
        script.write_text("""import runpy
import sys
import types
from pathlib import Path
module = types.ModuleType('robopark_api.services.ops.archives')
def wrong(*args, **kwargs):
    Path(sys.argv[4]).touch()
    raise RuntimeError('unrelated verifier module used')
module.inspect_archive = wrong
module.unpack_archive = wrong
module.KIND_RELEASE = 'release'
module.MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
sys.modules[module.__name__] = module
helper = sys.argv[1]
sys.argv = [helper, sys.argv[2], sys.argv[3]]
runpy.run_path(helper, run_name='__main__')
""".replace('Path(sys.argv[4])', f'Path({str(marker)!r})'))
        result = subprocess.run([sys.executable, str(script), str(self.bundle / 'lib/install-release.py'), str(self.root), str(self.bundle)], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists())
        self.assert_retained_signed_release()


    def test_low_space_on_separate_opt_or_var_stops_before_mutation(self):
        (self.root / 'opt').mkdir()
        (self.root / 'var/lib').mkdir(parents=True)
        for shortage in ({'FREE_OPT_GIB': '5'}, {'FREE_VAR_GIB': '5'}):
            self.run_installer(success=False, **shortage)
            self.assertFalse(self.commands('apt-get'))
            self.assertFalse((self.root / 'etc/robopark').exists())
            self.assertFalse((self.root / 'var/lib/robopark').exists())

    def test_journal_flush_failure_stops_before_package_mutation(self):
        self.run_installer(success=False, SYNC_FAIL='1')
        self.assertFalse(self.commands('apt-get'))

    def test_journal_syncs_file_before_rename_and_parent_after(self):
        self.run_installer()
        calls = self.commands('sync')
        self.assertTrue(calls, 'journal writes require durable flushes')
        self.assertEqual(len(calls) % 2, 0)
        state_dir = self.root / 'var/lib/robopark/ops/state'
        for before, after in zip(calls[::2], calls[1::2]):
            self.assertEqual(before['args'][0], '-f')
            self.assertEqual(Path(before['args'][1]).parent, state_dir)
            self.assertTrue(Path(before['args'][1]).name.startswith('.install.'))
            self.assertEqual(after['args'], ['-f', str(state_dir)])

    def test_atomic_link_flushes_parent_after_each_publication(self):
        helper = runpy.run_path(str(REPO / 'deploy/installer/lib/install-release.py'))
        link = self.base / 'current'
        parent_inode = self.base.stat().st_ino
        actual_fsync = os.fsync
        observed = []
        def record_fsync(descriptor):
            info = os.fstat(descriptor)
            observed.append((stat.S_ISDIR(info.st_mode), info.st_ino, os.readlink(link)))
            return actual_fsync(descriptor)
        with mock.patch.object(os, 'fsync', side_effect=record_fsync):
            helper['atomic_link'](link, self.base / 'first')
            helper['atomic_link'](link, self.base / 'second')
        self.assertEqual(observed, [(True, parent_inode, str(self.base / 'first')), (True, parent_inode, str(self.base / 'second'))])


    def test_release_payload_is_durable_before_current_link_is_published(self):
        helper = runpy.run_path(str(REPO / 'deploy/installer/lib/install-release.py'))
        (self.root / 'etc/robopark').mkdir()
        native_fsync, native_replace = os.fsync, os.replace
        flushed, at_cutover = set(), []
        def record_fsync(descriptor):
            info = os.fstat(descriptor)
            flushed.add((info.st_dev, info.st_ino))
            return native_fsync(descriptor)
        def record_replace(source, destination):
            if Path(destination).name == 'current':
                at_cutover.append(flushed.copy())
            return native_replace(source, destination)
        with mock.patch.object(os, 'fsync', side_effect=record_fsync), mock.patch.object(os, 'replace', side_effect=record_replace):
            helper['install'](str(self.root), str(self.bundle))
        release = self.root / 'opt/robopark/releases/1.0.0'
        required = { (path.stat().st_dev, path.stat().st_ino) for path in [release.parent, release, *release.rglob('*')] }
        self.assertEqual(len(at_cutover), 1)
        self.assertTrue(required <= at_cutover[0], 'release files and directory entries must be flushed before cutover')

    def test_container_exchange_directories_are_private_and_root_state_is_closed(self):
        self.run_installer()
        ops = self.root / 'var/lib/robopark/ops'
        for name in ('inbox', 'artifacts'):
            path = ops / name
            self.assertTrue(path.is_dir(), f'{name} must exist for container uid 10001')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            # Privileged ownership is an external host boundary faked on macOS.
            self.assertTrue(any(call['args'] == ['10001:10001', str(path)] for call in self.commands('chown')))
        for path in (ops / 'state', ops / 'compose', ops / 'rollbacks'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            self.assertTrue(any(call['args'] == ['0:0', str(path)] for call in self.commands('chown')))
        self.assertEqual(stat.S_IMODE(ops.stat().st_mode), 0o750)
        self.assertEqual(stat.S_IMODE((ops / 'state/install.json').stat().st_mode), 0o600)

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

    def test_shared_host_lock_blocks_install_before_mutation(self):
        ops = self.root / 'var/lib/robopark/ops'
        ops.mkdir(parents=True)
        with (ops / 'host.lock').open('w') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.run_installer(success=False)
        self.assertFalse(self.commands('apt-get'))

    def test_installer_releases_host_owner_before_synchronous_consumer_start(self):
        self.run_installer(CHECK_HOST_LOCK_HANDOFF='1')

    def test_large_profile_requires_sufficient_memory(self):
        (self.root / 'proc').mkdir()
        (self.root / 'proc/meminfo').write_text('MemTotal:        8388608 kB\n')
        with self.config.open('a') as stream:
            stream.write('UVICORN_WORKERS=4\n')
        self.run_installer(success=False)
        (self.root / 'proc/meminfo').write_text('MemTotal:        33554432 kB\n')
        self.run_installer()

    def test_interactive_wizard_never_echoes_secrets(self):
        answers = [SECRETS[0], 'park', SECRETS[1], '']
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

    def test_large_host_selects_four_workers_automatically(self):
        (self.root / 'proc').mkdir()
        (self.root / 'proc/meminfo').write_text('MemTotal:       33554432 kB\n')
        self.run_installer()
        host = (self.root / 'etc/robopark/host.env').read_text()
        self.assertIn("UVICORN_WORKERS='4'", host)


if __name__ == '__main__':
    unittest.main(verbosity=2)
