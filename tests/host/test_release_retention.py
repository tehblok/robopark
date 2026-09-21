import json
import os
import time
from uuid import uuid4


def test_cleanup_publishes_bounded_sanitized_evidence(host_paths):
    from robopark_host.retention import retain_artifacts

    artifact = host_paths.ops / "artifacts" / f"update-{uuid4()}.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"old")
    os.utime(artifact, (time.time() - 10 * 86400,) * 2)

    result = retain_artifacts(host_paths, max_bytes=0)

    assert result["removed"] == 1
    evidence = json.loads((host_paths.ops / "public/retention-status.json").read_text())
    assert evidence["removed"] == 1
    assert evidence["reclaimed_bytes"] == 3
    assert evidence["errors"] == []
    assert (host_paths.ops / "public/retention-status.json").stat().st_mode & 0o777 == 0o644
