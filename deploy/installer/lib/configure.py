"""Read wizard answers without executing shell syntax or exporting credentials."""
import getpass
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

HOST_DEFAULTS = {
    'ROBOPARK_ROLE': 'host', 'COOKIE_SECURE': 'true', 'COOKIE_SAMESITE': 'lax',
    'SESSION_COOKIE_NAME': 'robopark_session', 'PASSWORD_MIN_LENGTH': '12',
    'PASSWORD_REQUIRE_COMPLEXITY': 'true', 'LOGIN_MAX_ATTEMPTS': '5',
    'LOGIN_ATTEMPT_WINDOW_SECONDS': '300', 'LOGIN_LOCKOUT_SECONDS': '900',
    'REGISTER_MAX_ATTEMPTS': '10', 'SEED_USERNAME': 'royal', 'SEED_ROLE': 'royal',
    'DEV_SEED': 'false', 'UVICORN_WORKERS': '', 'OPERATOR_SHARED_PASSWORD': '',
    'ROBOPARK_DATABASE_PROFILE': 'postgresql-17', 'ROBOPARK_HOST_PROFILE': '',
    'SECRET_KEY': '', 'SEED_PASSWORD': '', 'CORS_ORIGINS': '',
}
TUNA_DEFAULTS = {'TUNA_TOKEN': '', 'TUNA_LOCATION': 'ru', 'TUNA_SUBDOMAIN': '', 'TUNA_DOMAIN': '', 'TUNA_BIND': '127.0.0.1:8080'}
UPDATER_DEFAULTS = {'GITHUB_REPOSITORY': 'tehblok/robopark', 'GITHUB_TOKEN': '', 'GITHUB_CHANNEL': 'stable', 'GITHUB_ENABLED': 'true'}
ALLOWED = set(HOST_DEFAULTS) | set(TUNA_DEFAULTS) | set(UPDATER_DEFAULTS)


def probed_cpu_count(root):
    path = Path(root) / 'proc/cpuinfo'
    if path.exists():
        return len(re.findall(r'^processor\s*:', path.read_text(), re.M))
    return os.cpu_count() or 0 if Path(root) == Path('/') else 0


def validate_clean_data_root(target, *, trusted_base):
    """Return the exact named Robopark root; reject aliases and broad targets."""
    target = Path(target)
    trusted_base = Path(trusted_base).absolute()
    expected = trusted_base / "var/lib/robopark"
    broad = {Path("/"), Path("/var"), Path("/var/lib"), Path.cwd()}
    if target in broad or target.absolute() != expected or target.name != "robopark":
        raise ValueError("unsafe_data_root")
    try:
        relative = expected.relative_to(trusted_base)
        current = trusted_base
        candidates = [current]
        for part in relative.parts:
            current /= part
            candidates.append(current)
        for candidate in candidates:
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("unsafe_data_root")
        resolved = target.resolve(strict=True)
        named = expected.resolve(strict=True)
    except OSError as exc:
        raise ValueError("unsafe_data_root") from exc
    if resolved != named:
        raise ValueError("unsafe_data_root")
    return resolved


def clean_reinstall_data_root(target, *, trusted_base, confirmation):
    resolved = validate_clean_data_root(target, trusted_base=trusted_base)
    if confirmation != 'DELETE ROBOPARK DATA':
        raise ValueError('confirmation_required')
    shutil.rmtree(resolved)


def read_env(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('invalid_config_file')
    info = path.stat()
    if stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.geteuid():
        raise ValueError('config_permissions')
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or key not in ALLOWED:
            raise ValueError('invalid_config_key')
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[key] = value
    return values


def atomic_env(path, values):
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            for key, value in values.items():
                stream.write(f"{key}='{value}'\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_secret(path, value):
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError('invalid_secret_file')
        return path.read_text().strip()
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(value + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return value
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def validate(values):
    # Single-quoted env files work in both systemd and Compose, and keep dollars
    # literal. Reject representations that those two parsers interpret differently.
    if any(any(ord(c) < 32 or c in "'\\" for c in value) for value in values.values()):
        raise ValueError('unsupported_config_character')
    password = values['SEED_PASSWORD']
    classes = (any(c.islower() for c in password), any(c.isupper() for c in password), any(c.isdigit() for c in password), any(not c.isalnum() for c in password))
    if len(password) < 12 or sum(classes) < 3 or values['SEED_USERNAME'].lower() in password.lower():
        raise ValueError('royal_password_policy')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', values['SEED_USERNAME']):
        raise ValueError('invalid_royal_username')
    if not values['TUNA_TOKEN']:
        raise ValueError('tuna_token_required')
    subdomain, domain = values['TUNA_SUBDOMAIN'], values['TUNA_DOMAIN']
    if bool(subdomain) == bool(domain):
        raise ValueError('stable_tuna_address_required')
    if subdomain and not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', subdomain):
        raise ValueError('invalid_tuna_subdomain')
    if domain and ('.' not in domain or not re.fullmatch(r'[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?', domain)):
        raise ValueError('invalid_tuna_domain')
    location = values['TUNA_LOCATION']
    if location != 'ru':
        help_text = subprocess.run(['tuna', 'http', '--help'], check=True, capture_output=True, text=True, timeout=15).stdout
        sections = re.findall(r'--(?:location|region)\b[^\n]*(?:\n[ \t]+(?!-)[^\n]*)*', help_text)
        regions = set(re.findall(r'\b[a-z]{2}(?:-[a-z0-9]+)?\b', '\n'.join(sections)))
        if location not in regions:
            raise ValueError('unsupported_tuna_region')
    if not values['CORS_ORIGINS']:
        values['CORS_ORIGINS'] = 'https://' + (domain or f'{subdomain}.{location}.tuna.am')
    origin = urlsplit(values['CORS_ORIGINS'])
    if origin.scheme != 'https' or not origin.hostname or origin.username or origin.password or origin.path not in ('', '/') or origin.query or origin.fragment:
        raise ValueError('invalid_public_origin')
    repo = values['GITHUB_REPOSITORY']
    if repo and not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('invalid_github_repository')
    if values['GITHUB_TOKEN'] and not repo:
        raise ValueError('github_repository_required')
    if values['GITHUB_CHANNEL'] not in ('stable', 'prerelease'):
        raise ValueError('invalid_github_channel')
    values['GITHUB_ENABLED'] = 'true' if repo else 'false'
    if values['UVICORN_WORKERS'] not in ('2', '4'):
        raise ValueError('invalid_host_profile')
    expected_profile = 'orin' if values['UVICORN_WORKERS'] == '4' else 'vim4-safe'
    if values['ROBOPARK_HOST_PROFILE'] != expected_profile:
        raise ValueError('invalid_host_profile')
    if values['ROBOPARK_DATABASE_PROFILE'] != 'postgresql-17':
        raise ValueError('invalid_database_profile')
    for key in ('ROBOPARK_ROLE', 'COOKIE_SECURE', 'PASSWORD_REQUIRE_COMPLEXITY', 'SEED_ROLE', 'DEV_SEED'):
        if values[key] != HOST_DEFAULTS[key]:
            raise ValueError('unsafe_host_setting')
    if values['TUNA_BIND'] != '127.0.0.1:8080':
        raise ValueError('unsafe_tuna_bind')
    if not values['SECRET_KEY']:
        values['SECRET_KEY'] = secrets.token_urlsafe(48)


def configure(root, mode, filename, resume):
    etc = Path(root) / 'etc/robopark'
    existing = {}
    for name in ('host.env', 'tuna.env', 'updater.env'):
        path = etc / name
        if path.exists() or path.is_symlink():
            existing.update(read_env(path))
    values = {**HOST_DEFAULTS, **TUNA_DEFAULTS, **UPDATER_DEFAULTS}
    if filename:
        values.update(read_env(Path(filename)))
    # Reinstall and crash recovery never rotate non-empty host-owned values.
    values.update({key: value for key, value in existing.items() if value})
    if mode == 'interactive' and not (existing.get('TUNA_TOKEN') and existing.get('SEED_PASSWORD')):
        required = []
        if not values['TUNA_TOKEN']:
            required.append(('TUNA_TOKEN', 'Токен Tuna', True))
        if not values['TUNA_SUBDOMAIN'] and not values['TUNA_DOMAIN']:
            required.extend((
                ('TUNA_SUBDOMAIN', 'Зарезервированный поддомен Tuna (пусто для своего домена)', False),
                ('TUNA_DOMAIN', 'Свой домен (пусто при использовании поддомена)', False),
            ))
        if not values['SEED_PASSWORD']:
            required.append(('SEED_PASSWORD', 'Пароль Royal (12+ символов, 3 класса; без одинарной кавычки и обратной косой черты)', True))
        if required and not sys.stdin.isatty():
            raise ValueError('terminal_required_use_non_interactive')
        for key, label, secret in required:
            if key == 'TUNA_DOMAIN' and values['TUNA_SUBDOMAIN']:
                continue
            value = getpass.getpass(label + ': ') if secret else input(f'{label} [{values[key]}]: ')
            if value:
                values[key] = value
        if sys.stdin.isatty() and not existing.get('OPERATOR_SHARED_PASSWORD'):
            value = getpass.getpass('Пароль регистрации операторов (необязательно): ')
            if value:
                values['OPERATOR_SHARED_PASSWORD'] = value
    if not values['UVICORN_WORKERS']:
        memory_path = Path(root) / 'proc/meminfo'
        memory = memory_path.read_text() if memory_path.exists() else ''
        total = re.search(r'^MemTotal:\s+(\d+) kB', memory, re.M)
        values['UVICORN_WORKERS'] = (
            '4'
            if total
            and int(total.group(1)) >= 24 * 1024 * 1024
            and probed_cpu_count(root) >= 8
            else '2'
        )
    if not values['ROBOPARK_HOST_PROFILE']:
        values['ROBOPARK_HOST_PROFILE'] = (
            'orin' if values['UVICORN_WORKERS'] == '4' else 'vim4-safe'
        )
    validate(values)
    if values['UVICORN_WORKERS'] == '4':
        memory = (Path(root) / 'proc/meminfo').read_text()
        total = re.search(r'^MemTotal:\s+(\d+) kB', memory, re.M)
        if (
            not total
            or int(total.group(1)) < 24 * 1024 * 1024
            or probed_cpu_count(root) < 8
        ):
            raise ValueError('large_profile_requires_24_gib_and_8_cpu')
    etc.mkdir(parents=True, exist_ok=True, mode=0o700)
    if etc.is_symlink():
        raise ValueError('invalid_config_directory')
    os.chmod(etc, 0o700)
    for filename, defaults in (('host.env', HOST_DEFAULTS), ('tuna.env', TUNA_DEFAULTS), ('updater.env', UPDATER_DEFAULTS)):
        atomic_env(etc / filename, {key: values[key] for key in defaults})
    password = atomic_secret(etc / 'postgres-password', secrets.token_urlsafe(48))
    pgpass = etc / 'pgpass'
    atomic_secret(pgpass, f'db:5432:robopark:robopark:{password}')
    if os.geteuid() == 0:
        os.chown(pgpass, 10001, 10001)


if __name__ == '__main__':
    try:
        if sys.argv[1:2] == ['--validate-clean-data-root'] and len(sys.argv) == 4:
            validate_clean_data_root(Path(sys.argv[2]), trusted_base=Path(sys.argv[3]))
        elif sys.argv[1:2] == ['--clean-data-root'] and len(sys.argv) == 5:
            clean_reinstall_data_root(
                Path(sys.argv[2]), trusted_base=Path(sys.argv[3]), confirmation=sys.argv[4]
            )
        else:
            configure(*sys.argv[1:])
    except (ValueError, OSError, EOFError, subprocess.SubprocessError):
        # Parser/OS exceptions may contain input, paths, or child output.
        print('Некорректная конфигурация или права файла. Проверьте ответы и режим 0600.', file=sys.stderr)
        sys.exit(1)
