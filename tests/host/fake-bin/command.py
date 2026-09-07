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
if name == 'uname':
    print(os.environ.get('ARCH', 'aarch64'))
elif name == 'id':
    print('0')
elif name == 'df':
    print('Filesystem 1024-blocks Used Available Capacity Mounted on')
    print('fake 20000000 0 %s 0%% /' % (int(os.environ.get('FREE_GIB', '12')) * 1024**2))
elif name == 'dpkg':
    if args == ['--audit'] and os.environ.get('DPKG_INTERRUPTED') == '1':
        print('package is unpacked but not configured')
    elif args == ['--print-architecture']:
        print('arm64' if os.environ.get('ARCH', 'aarch64') in ('arm64', 'aarch64') else 'amd64')
elif name == 'apt-get':
    if os.environ.get('APT_FAIL') == '1':
        sys.exit(1)
elif name == 'curl':
    if '--output' in args:
        Path(args[args.index('--output') + 1]).write_text('fake signed repository key')
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
elif name in ('sleep', 'systemctl', 'docker'):
    pass
else:
    sys.exit('unexpected fake command: ' + name)
