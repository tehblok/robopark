"""Finish a verified install's optional signing bridge after local readiness."""

import sys
from pathlib import Path


def main():
    root = Path(sys.argv[1])
    trusted = (root / "opt/robopark/host-tools").resolve(strict=True)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(trusted))
    from robopark_host.paths import HostPaths
    from robopark_host.trust import bootstrap
    from robopark_host.updater import SystemRunner

    bootstrap(HostPaths.from_root(root), SystemRunner())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Signing trust initialization failed", file=sys.stderr)
        raise SystemExit(1) from None
