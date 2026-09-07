"""Atomically publish verified release units without executing payload scripts."""
import os
from pathlib import Path
import sys
import tempfile

UNITS = (
    'robopark.service', 'robopark-tuna.service', 'robopark-updater.service',
    'robopark-update-check.service', 'robopark-update-check.timer',
    'robopark-doctor.service', 'robopark-doctor.timer',
    'robopark-watchdog.service', 'robopark-watchdog.timer',
)


def install_units(root):
    root = Path(root)
    release = (root / 'opt/robopark/current').resolve(strict=True)
    if not release.is_relative_to((root / 'opt/robopark/releases').resolve()):
        raise ValueError('invalid_release')
    target_dir = root / 'etc/systemd/system'
    if target_dir.is_symlink() or target_dir.parent.is_symlink():
        raise ValueError('invalid_unit_directory')
    # Validate every source before replacing the first installed unit.
    contents = {}
    for name in UNITS:
        source = release / 'deploy/systemd' / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('missing_unit')
        contents[name] = source.read_bytes()
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        descriptor, temporary = tempfile.mkstemp(prefix='.' + name + '.', dir=target_dir)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o644)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target_dir / name)
            directory = os.open(target_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


if __name__ == '__main__':
    try:
        install_units(sys.argv[1])
    except (ValueError, OSError):
        print('Service unit installation failed', file=sys.stderr)
        sys.exit(1)
