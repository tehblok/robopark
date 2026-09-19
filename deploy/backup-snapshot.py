"""Run inside the API container; stdout is only the validated artifact path."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from robopark_api.config import get_settings
from robopark_api.services.ops.archives import inspect_archive
from robopark_api.services.ops.context import build_ops_context
from robopark_api.services.ops.runner import artifact_path, start_and_run
from robopark_api.services.ops.snapshot import SNAPSHOT_DB_REL, SNAPSHOT_DUMP_REL


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
        receipt = ctx.ops_dir / "scheduled-copy.json"
        receipt_pending = receipt.with_suffix(".tmp")
        with receipt_pending.open("w") as stream:
            json.dump({"verified_at": time.time()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        receipt_pending.replace(receipt)
        if previous and previous != name and Path(previous).name == previous:
            (root / previous).unlink(missing_ok=True)
        return
    job = start_and_run(ctx, "snapshot", exempt_token_hash="scheduled-backup")
    path = artifact_path(ctx.ops_dir, job)
    if job.state != "succeeded" or path is None:
        raise RuntimeError("snapshot_failed")
    inspect_archive(path.read_bytes())
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as folder:
        if str(SNAPSHOT_DUMP_REL) in archive.namelist():
            database = Path(folder) / "check.dump"
            database.write_bytes(archive.read(str(SNAPSHOT_DUMP_REL)))
            result = subprocess.run(
                ["pg_restore", "--list", str(database)],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode or "alembic_version" not in result.stdout:
                raise RuntimeError("snapshot_database_invalid")
        else:
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
