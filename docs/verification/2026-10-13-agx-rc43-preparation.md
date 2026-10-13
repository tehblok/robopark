# GEACX1 / RC43 preparation evidence — 2026-10-13

Scope: prepare files only, as requested by the owner. No SSH session, target
APT transaction, flashing, power tuning, database restore or model download.

Reference recovery repository commit:
`7611b523e3311ba08d821449236736590ec23e35`.
Robopark base: `cdc8ba8b` (main after stable v0.2.0-rc.43).

Changes: standalone stdlib AGX check/preparation companion, optional host CLI
`agx-check`, conservative GPU device discovery, and migration runbook. The
published RC43 installer is unchanged. CUDA toolkit selection is explicit and
requires verification against the target vendor stack; the companion rejects
APT plans changing protected BSP/driver/kernel/vendor packages or removing any
package. It checks exact versions after application, but no real application
was attempted in this task.

## Verification

- `PYTHONPATH=deploy/host:deploy/ota:apps/api/src:. apps/api/.venv/bin/python -m pytest -q tests/host`:
  **1408 passed in 78.70 s** on the local macOS environment.
- Ruff on all changed Python files: passed.
- `git diff --check` and `git diff --cached --check`: passed.
- Isolated host launcher `--self-test`: passed.
- Standalone companion from the ZIP: `check`, `prepare`, `prepare --apply`
  all exited 2 on the non-AGX workstation before any package operation.
- Original RC43 downloaded from GitHub Release: SHA-256
  `eabeba6cd12fe79b7202bd44fc26b31ad27ca6447f6fb10cea53032359d7714c`;
  embedded OTA `verify --json` passed before and after ZIP extraction.
- ZIP integrity and every `SHA256SUMS` entry: passed.

An earlier full run had 1379 passes and 19 packaging failures because the
repository intentionally rejects untracked runtime source. After review and
staging the exact new files, the complete run above passed. The guard itself
was preserved. No commit or remote publication was performed.

Independent review findings were fixed and regression-tested: missing carrier
marker admission, library-only false CUDA availability, configuration-only APT
plans silently ignored, and unparsed nvcc version allowing false readiness.

## Deliverable

`artifacts/robopark-agx-rc43-preparation.zip` — 3,915,117 bytes.
SHA-256: `982f3325afd3239ee31597e1ec8604ea7fbe25dd45c427fd4ef9c784158e86b8`.
The generated artifact is ignored by Git; source and runbook are staged.

## Outstanding hardware acceptance

Actual carrier/kernel/DTB and fan operation, configured APT repositories and
compatible toolkit version, CUDA service-user execution, NVMe managed layout,
ARM64 Docker builds, backup/SECRET_KEY restoration, restart/SSH/Tuna/Telegram
cutover, shared DRAM/thermal load, and performance benchmarks remain untested.
TensorRT/DLA and hardware JPEG/video consumers are not implemented by this
preparation and are not claimed as enabled in RC43.

See [the migration runbook](../runbooks/geacx1-rc43-migration.md).
