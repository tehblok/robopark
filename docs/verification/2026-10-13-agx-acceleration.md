# AGX acceleration companion — verification, 2026-10-13

Scope: prepare files only. No SSH, target installation, BSP modification, model
download, model training, production provider switch or publication was performed.

The companion adds a local systemd service and Unix socket API for TensorRT
ONNX inference and JPEG thumbnails, a model inventory, on-target numerical
GPU/DLA smoke, and a separately configured Edge-LLM experiment endpoint.
It is installed after the original RC43 with its own explicit installer.
Current RC43 application consumers, its llama.cpp broker and its retired RAG
flow are not silently connected to the new services.

## Verified contracts and corrections

- NVIDIA's October 8 forum update confirms DLA in a standalone TensorRT package
  and announces bundled support for JetPack 7.2.2. The old 10.x matrix does not
  justify a blanket version-based ban. Actual runtime detection and strict
  build/inference decide readiness.
- DLA counts from Python are trusted only if their TensorRT version matches
  the selected `trtexec`. A different Python runtime cannot veto standalone DLA.
- `trtexec --help` supplies its version banner; `--version` is unsupported in
  the inspected v10.16 source. Static ONNX receives no `--shapes` argument.
- TensorRT 11 receives no removed precision flags and may accept FP32 GPU
  graphs. The ONNX graph specifies its types; actual parsing/build decides
  operator and precision support.
- Strict DLA receives no GPU fallback; hybrid builds are explicitly labelled.
  The DLA core is also passed when loading the engine for inference.
- The FP16 smoke graph uses nonzero weights and depends on its input. CPU ONNX
  ReferenceEvaluator confirms output=4 for ones and output=7 for doubled input.
  This is a CPU validation of the test graph, not evidence of GPU/DLA execution.
- Independent review corrections include service conditions, UID0 account
  rejection, destination symlink guards, GPU permissions, bounded thumbnail
  dimensions/no upscaling, checkpoint placement and truthful readiness fields.

## Local verification

- Initial full host run: **1462 passed, 7 skipped**, 82.07 seconds. Skips were
  Pillow-dependent checks in the development venv.
- After refining the smoke graph to prevent constant folding: targeted AGX
  tests **91 passed, 8 skipped** in that venv; independent system-Python image
  tests **34 passed, 1 skipped**. The remaining added skip was ONNX.
- ONNX 1.19.0 and Pillow 11.3.0 were installed only in a temporary test venv.
  The real ONNX checker and CPU ReferenceEvaluator test passed.
- Final full host suite with those optional test dependencies: **1470 passed,
  no skips**, 95.24 seconds. Command:
  `PYTHONPATH=/tmp/robopark-agx-onnx-check-20261013/lib/python3.12/site-packages:deploy/host:deploy/ota:apps/api/src:. apps/api/.venv/bin/python -m pytest -q tests/host`.
- Ruff, source compilation and Git whitespace checks passed.
- Archive CRC, all per-file SHA256 entries and the embedded original RC43 OTA
  verifier passed. Off-target check/prepare/apply reject the macOS workstation
  before any package or service mutation. Status reports no models/no readiness.

Full target acceptance remains pending: service-user execution under systemd,
TensorRT/DLA numerical smoke, hardware GStreamer output, native package resolution,
model-specific preprocessing/quality, tool calling and context accounting,
shared-memory/thermal load and measured speedup.

## Rebuild

`python3.12 scripts/build_agx_preparation.py` builds the optional companion
archive from reviewed/indexed source and requires the exact original RC43 OTA
SHA256 `eabeba6cd12fe79b7202bd44fc26b31ad27ca6447f6fb10cea53032359d7714c`.
It does not rebuild or relabel RC43. The ZIP and its checksum sidecar are in
`artifacts/`; no model, engine, credential or backup files are packaged.
