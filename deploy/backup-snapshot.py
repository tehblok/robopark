"""Compatibility entry point for the scheduled snapshot inside the API image."""

from robopark_api.services.ops.scheduled_snapshot import main


if __name__ == "__main__":
    import sys

    try:
        main()
    except Exception:
        print("scheduled_snapshot_failed", file=sys.stderr)
        sys.exit(1)
