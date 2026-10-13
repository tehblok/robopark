"""Reproducible RC43 + optional acceleration companion, with per-file hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RC43_SHA256 = "eabeba6cd12fe79b7202bd44fc26b31ad27ca6447f6fb10cea53032359d7714c"
DOCS = (
    "agx-acceleration.md",
    "geacx1-rc43-migration.md",
    "geacx1-storage.md",
    "geacx1-performance.md",
    "local-ai.md",
    "ota-recovery.md",
)


def build(ota: Path, output: Path, *, root: Path = ROOT) -> dict:
    if ota.is_symlink() or not ota.is_file():
        raise ValueError("rc43_ota_missing")
    ota_bytes = ota.read_bytes()
    if hashlib.sha256(ota_bytes).hexdigest() != RC43_SHA256:
        raise ValueError("rc43_ota_sha256_mismatch")
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    ).stdout
    allowed = {name.decode() for name in tracked.split(b"\0") if name}
    # Include only reviewed source; no caches, tests, model weights or local state.
    files = {}
    for directory in (root / "deploy/accelerator", root / "deploy/host"):
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ValueError("source_symlink")
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and (path.suffix in (".py", ".service", ".txt") or path.name.startswith("robopark"))
            ):
                if relative not in allowed:
                    raise ValueError("source_not_reviewed_and_staged")
                files[relative] = path.read_bytes()
    files["robopark-agx.py"] = files["deploy/host/robopark_host/agx_preflight.py"]
    files["robopark-0.2.0-rc.43.ota"] = ota_bytes
    for name in DOCS:
        files[f"docs/runbooks/{name}"] = (root / "docs/runbooks" / name).read_bytes()
    files["README-RU.md"] = (
        "# Robopark RC43 + AGX acceleration companion\n\n"
        "Файлы подготовлены 13 октября 2026. На AGX не устанавливались.\n\n"
        "1. Проверить `sha256sum -c SHA256SUMS`.\n"
        "2. [Перенос RC43](docs/runbooks/geacx1-rc43-migration.md).\n"
        "3. [TensorRT/DLA/JPEG и модели](docs/runbooks/agx-acceleration.md).\n\n"
        "Оригинальный RC43 сохранён без изменений. Companion устанавливается отдельно.\n"
        "Весов, firmware и резервных копий в архиве нет. GPU/DLA acceptance выполняется на AGX.\n"
        "LLM endpoint экспериментальный; production broker и RAG автоматически не переключаются.\n"
    ).encode()
    files["provenance.json"] = (
        json.dumps(
            {
                "schema": 1,
                "kind": "rc43-with-optional-acceleration-companion",
                "base_release": "v0.2.0-rc.43",
                "base_ota_sha256": RC43_SHA256,
                "base_ota_url": "https://github.com/tehblok/robopark/releases/download/v0.2.0-rc.43/robopark-0.2.0-rc.43.ota",
                "recovery_commit": "7611b523e3311ba08d821449236736590ec23e35",
                "edge_llm_commit": "95515c2f87fba8982db5a519f9022277667b3cc9",
                "companion_source": "reviewed-worktree-not-published-release",
                "models_included": False,
                "target_acceptance_performed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode()
    files["SHA256SUMS"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items())
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("output_symlink")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("archive_crc_mismatch")
        for name, data in files.items():
            if archive.read(name) != data:
                raise ValueError("archive_payload_mismatch")
    sha = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{sha}  {output.name}\n")
    return {"path": str(output), "sha256": sha, "files": len(files), "bytes": output.stat().st_size}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ota", type=Path, default=ROOT / "artifacts/agx-rc43/robopark-0.2.0-rc.43.ota"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/robopark-agx-rc43-acceleration.zip"
    )
    args = parser.parse_args()
    print(json.dumps(build(args.ota, args.output), ensure_ascii=False))
