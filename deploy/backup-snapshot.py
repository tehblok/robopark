"""Run inside the API container; stdout is only the validated artifact path."""

import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

from robopark_api.config import get_settings
from robopark_api.services.ops.archives import inspect_archive
from robopark_api.services.ops.context import build_ops_context
from robopark_api.services.ops.runner import artifact_path, start_and_run
from robopark_api.services.ops.snapshot import SNAPSHOT_DB_REL


def main():
    ctx = build_ops_context(get_settings())
    if len(sys.argv) == 3 and sys.argv[1] == "--ack":
        # Only remove our own previous scheduled artifact, after the host has
        # verified its durable copy. Manual royal snapshots are never rotated.
        name = sys.argv[2]
        if Path(name).name != name or not name.endswith(".zip"):
            raise RuntimeError("invalid_artifact")
        root = ctx.ops_dir / "artifacts"
        if not (root / name).is_file():
            raise RuntimeError("artifact_missing")
        marker = ctx.ops_dir / "scheduled-last.txt"
        previous = marker.read_text().strip() if marker.exists() else ""
        pending = marker.with_suffix(".tmp")
        pending.write_text(name)
        pending.replace(marker)
        if previous and previous != name and Path(previous).name == previous:
            (root / previous).unlink(missing_ok=True)
        return
    job = start_and_run(ctx, "snapshot", exempt_token_hash="scheduled-backup")
    path = artifact_path(ctx.ops_dir, job)
    if job.state != "succeeded" or path is None:
        raise RuntimeError("snapshot_failed")
    inspect_archive(path.read_bytes())
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as folder:
        database = Path(folder) / "check.db"
        database.write_bytes(archive.read(str(SNAPSHOT_DB_REL)))
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise RuntimeError("snapshot_database_invalid")
    print(path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("scheduled_snapshot_failed", file=sys.stderr)
        sys.exit(1)
