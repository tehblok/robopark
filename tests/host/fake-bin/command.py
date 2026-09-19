"""External host commands only; filesystem/config/archive work stays real."""
import fcntl
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[1]).name
args = sys.argv[2:]
root = Path(os.environ['ROBOPARK_ROOT'])
with (root / 'commands.jsonl').open('a') as stream:
    stream.write(json.dumps({'name': name, 'args': args, 'env': dict(os.environ)}) + '\n')
if os.environ.get('CHECK_HOST_LOCK_HANDOFF') == '1' and (
    name == 'apt-get' or name == 'systemctl' and args[:2] == ['start', 'robopark-updater.service']
):
    with (root / 'var/lib/robopark/ops/host.lock').open('a') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            owned = False
        except BlockingIOError:
            owned = True
        if owned != (name == 'apt-get'):
            sys.exit('host lock was not held during mutation or retained during handoff')
if name == 'uname':
    print(os.environ.get('ARCH', 'aarch64'))
elif name == 'id':
    print('0')
elif name == 'df':
    print('Filesystem 1024-blocks Used Available Capacity Mounted on')
    top = Path(args[-1]).relative_to(root).parts
    mount = top[0].upper() if top else 'ROOT'
    free = os.environ.get('FREE_' + mount + '_GIB', os.environ.get('FREE_GIB', '12'))
    print('fake 20000000 0 %s 0%% /' % (int(free) * 1024**2))
elif name == 'dpkg':
    if args == ['--audit'] and os.environ.get('DPKG_INTERRUPTED') == '1':
        print('package is unpacked but not configured')
    elif args == ['--print-architecture']:
        print('arm64' if os.environ.get('ARCH', 'aarch64') in ('arm64', 'aarch64') else 'amd64')
elif name == 'apt-get':
    if os.environ.get('APT_FAIL') == '1':
        sys.exit(1)
    if 'docker-ce' in args:
        (root / 'docker-installed').touch()
    if 'docker-compose-plugin' in args:
        (root / 'compose-installed').touch()
    if 'docker-buildx-plugin' in args:
        (root / 'buildx-installed').touch()
elif name == 'curl':
    if '--output' in args:
        Path(args[args.index('--output') + 1]).write_text('fake signed repository key')
    elif args[-1] == 'http://127.0.0.1:8080/api/health/ready':
        sys.exit(int(os.environ.get('API_UNREADY', '0')))
    elif args[-1].startswith('https://'):
        unavailable = int(os.environ.get('PUBLIC_HTTPS_UNREADY', '0'))
        if not unavailable and '--write-out' in args:
            sys.stdout.write('{"status":"ready"}\n200')
        sys.exit(unavailable)
    else:
        sys.exit('curl must save repository key explicitly')
elif name == 'gpg':
    if '--output' in args:
        Path(args[args.index('--output') + 1]).write_text('fake binary keyring')
elif name == 'tuna':
    if args == ['http', '--help']:
        print('--location string Region (ru, eu)')
    elif args not in (['help'], ['--version']):
        sys.exit(2)
elif name == 'flock':
    try:
        fcntl.flock(int(args[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(1)
elif name == 'sync':
    if os.environ.get('SYNC_FAIL') == '1':
        sys.exit(1)
    descriptor = os.open(args[-1], os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
elif name == 'chown':
    if os.geteuid() == 0:
        owner, group = map(int, args[0].split(':'))
        for path in args[1:]:
            os.chown(path, owner, group)
elif name == 'docker':
    installed = os.environ.get('DOCKER_PREINSTALLED') == '1' or (root / 'docker-installed').exists()
    if args == ['--version'] and not installed:
        sys.exit(1)
    if args == ['compose', 'version'] and not (root / 'compose-installed').exists() and not (installed and os.environ.get('COMPOSE_MISSING') != '1'):
        sys.exit(1)
    if args == ['buildx', 'version'] and not (root / 'buildx-installed').exists() and not (installed and os.environ.get('BUILDX_MISSING') != '1'):
        sys.exit(1)
    if args == ['info'] or 'build' in args or args[:2] == ['image', 'inspect']:
        if os.environ.get('DOCKER_STOPPED') == '1' and not (root / 'docker-running').exists():
            sys.exit(1)
    if args[:2] == ['image', 'inspect']:
        print('sha256:' + ('1' if 'api' in args[-1] else '2') * 64)
    elif 'pg_dump' in args:
        output = next(arg for arg in args if arg.startswith('--file='))
        relative = output.removeprefix('--file=/host-rollbacks/')
        target = root / 'var/lib/robopark/ops/rollbacks' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'PGDMP fixture')
    elif any('SELECT version_num FROM alembic_version' in arg for arg in args):
        print('["initial"]')
    elif 'config' in args:
        print(json.dumps({'services': {'db': {'image': 'postgres:17.6-alpine', 'environment': {}, 'volumes': []}, 'api': {'build': {'context': 'api'}, 'environment': {}}, 'web': {'build': {'context': 'web'}}, 'ops-agent': {}}}))
    elif 'build' in args and os.environ.get('BUILD_FAIL') == '1':
        sys.exit(1)
elif name == 'systemctl':
    if args == ['start', 'docker.service']:
        if os.environ.get('DOCKER_START_FAIL') == '1':
            sys.exit(1)
        (root / 'docker-running').touch()
elif name == 'sleep':
    pass
else:
    sys.exit('unexpected fake command: ' + name)
