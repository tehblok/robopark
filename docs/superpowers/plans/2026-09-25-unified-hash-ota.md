# Unified Hash-Only OTA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace signed/GitHub-delivered releases with one deterministic, self-contained `robopark-<version>.ota` that installs from USB and updates from the royal system page using SHA-256 integrity checks only.

**Architecture:** A dependency-free Python 3.10 ZIP application owns the bundle format, verification, local menu, and clean-install lifecycle. The API accepts resumable bounded uploads and hands a verified staged package to the existing durable host-operation bridge; the host agent reuses its snapshot/cutover/rollback machinery but no longer admits packages through signing keys or GitHub metadata. The web client hashes locally in a worker, uploads chunks without page reloads, and follows the durable operation receipt through completion.

**Tech Stack:** Python 3.10+ standard library, FastAPI/Pydantic/SQLAlchemy, PostgreSQL, React/TypeScript/Vite, Web Workers, Docker Compose, pytest, Vitest/Testing Library.

**Spec:** [Unified hash-only OTA design](../specs/2026-09-25-unified-hash-ota-design.md)

## Global Constraints

- Work directly on `main` only after each task's focused tests pass; do not include unrelated local changes.
- Do not run load or soak tests without explicit user approval. The default build gate is short and bounded.
- The first transition from the signed release line is clean install only. Do not implement import, backup, or restore of the old installation.
- A normal future OTA preserves application data and must snapshot before migration and automatically roll back on migration, cutover, readiness, or smoke-check failure.
- SHA-256 proves byte integrity, not package authorship. Do not retain hidden signature or GitHub trust fallbacks.
- Only `royal` can upload, inspect, confirm, or start an OTA operation. Existing reauthorization and idempotent operation receipts remain mandatory.
- Never place a royal password in command arguments, environment variables, logs, journals, manifests, browser storage, or the artifact.
- Never buffer the whole OTA in API or browser UI memory. Hashing and upload are streaming/chunked and bounded.
- All archive extraction must reject traversal, absolute paths, duplicate names, symlinks, special files, oversized entries, excessive compression ratios, and manifest/file mismatches.
- The local action menu must be displayed before Docker, network, installed-version, or runtime checks.
- Clean removal is constrained to explicit Robopark roots, services, Docker labels/names, volumes, networks, and images. It must not prune unrelated Docker or host data.
- Python bundle/runtime code must remain compatible with Python 3.10 available on Ubuntu 22.04 and supported Armbian hosts.

## Review Focus

- Destructive clean-install and remove boundaries: prove exact targets and unrelated-data preservation.
- Password secrecy: inspect subprocess, journal, error, audit, and test output paths.
- Crash consistency: upload resume, idempotent finalize/start, durable host journal, and rollback after every cutover phase.
- Archive parser hardening and size limits before extraction.
- API authorization, per-user quotas, upload ownership, TTL cleanup, and concurrent-operation exclusion.
- Browser/PWA behavior after cutover: no reload during upload; exactly one reload when the server build id changes.
- Removal of every signing-key, `.sig`, GitHub Release, and trust-rotation dependency without removing ordinary Git/GitHub CI checks.

---

## Task 1: Establish the OTA manifest and verifier contract

**Files:**

- Create: `deploy/ota/robopark_ota/__init__.py`
- Create: `deploy/ota/robopark_ota/model.py`
- Create: `deploy/ota/robopark_ota/verify.py`
- Create: `tests/host/test_ota_verifier.py`

- [ ] **Step 1: Write failing tests for the canonical manifest and safe ZIP rules.**

  Cover a valid minimal bundle plus: changed byte, undeclared file, missing file, wrong size, wrong digest, duplicate member, absolute path, `..` traversal, backslash path, NUL path, symlink mode, non-regular mode, per-file limit, total expanded limit, member-count limit, and compression-ratio limit.

  ```python
  def test_changed_payload_is_rejected(tmp_path):
      bundle = build_test_ota(tmp_path, {"release/VERSION": b"0.3.0\n"})
      rewrite_member(bundle, "release/VERSION", b"0.3.1\n")
      with pytest.raises(OtaError, match="ota_hash_mismatch"):
          verify_ota(bundle)

  def test_duplicate_member_is_rejected(tmp_path):
      bundle = build_duplicate_ota(tmp_path, "release/VERSION")
      with pytest.raises(OtaError, match="ota_invalid_container"):
          verify_ota(bundle)
  ```

- [ ] **Step 2: Run the focused test and confirm it fails because the module does not exist.**

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=deploy/ota \
    uv run --project apps/api --frozen --extra dev \
    python -m pytest -p no:cacheprovider tests/host/test_ota_verifier.py -q
  ```

- [ ] **Step 3: Implement strict models and one public verification boundary.**

  Use explicit structures and stable public errors:

  ```python
  @dataclass(frozen=True)
  class OtaFile:
      path: str
      size: int
      sha256: str

  @dataclass(frozen=True)
  class OtaManifest:
      format_version: int
      app_version: str
      git_sha: str
      migration_head: str
      compatible_from: tuple[str, ...]
      required_free_bytes: int
      max_expanded_bytes: int
      changes: tuple[str, ...]
      files: tuple[OtaFile, ...]

  @dataclass(frozen=True)
  class VerifiedOta:
      path: Path
      sha256: str
      size: int
      manifest: OtaManifest

  def verify_ota(path: Path, *, expected_sha256: str | None = None) -> VerifiedOta: ...
  ```

  `manifest.json` is UTF-8 canonical JSON, schema `1`, and lists every regular member except `manifest.json`. The verifier reads members in chunks, never calls `extractall`, and maps all malformed-container cases to the spec's stable error codes.

- [ ] **Step 4: Add Python 3.10 syntax/import coverage and run the focused tests.**

  ```bash
  python3 -m compileall -q deploy/ota/robopark_ota
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=deploy/ota \
    uv run --project apps/api --frozen --extra dev \
    python -m pytest -p no:cacheprovider tests/host/test_ota_verifier.py -q
  ```

- [ ] **Step 5: Commit only the verifier contract.**

  ```bash
  git add deploy/ota/robopark_ota tests/host/test_ota_verifier.py
  git diff --cached --check
  git commit -m "feat(release): define hash-only OTA contract"
  ```

## Task 2: Build one deterministic self-executing `.ota`

**Files:**

- Create: `deploy/ota/__main__.py`
- Create: `deploy/ota/robopark_ota/cli.py`
- Create: `scripts/build_ota.py`
- Create: `scripts/build-ota.sh`
- Create: `tests/host/test_ota_builder.py`
- Modify: `scripts/verify.sh`
- Modify: `deploy/release-metadata.json`

- [ ] **Step 1: Write failing builder tests.**

  Assert two builds from the same tracked tree are byte-identical, executable with `python3 artifact.ota --help`, exclude `.git`, `.env`, runtime state, caches, logs, `output/`, old release artifacts, private/public release keys and `.sig`, and include the exact manifest inventory.

  ```python
  def test_two_builds_are_byte_identical(repo_copy, tmp_path):
      first = build(repo_copy, tmp_path / "one")
      second = build(repo_copy, tmp_path / "two")
      assert first.read_bytes() == second.read_bytes()
      assert sha256(first) == sha256(second)
  ```

- [ ] **Step 2: Run the builder tests and record the expected missing-command failure.**

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=deploy/ota \
    uv run --project apps/api --frozen --extra dev \
    python -m pytest -p no:cacheprovider tests/host/test_ota_builder.py -q
  ```

- [ ] **Step 3: Implement the deterministic builder.**

  `scripts/build-ota.sh /absolute/output/directory` must:

  1. reject a relative or source-tree output directory;
  2. reject modified tracked files but ignore untracked developer caches;
  3. validate `VERSION`, migration head and release metadata;
  4. run `./scripts/verify.sh fast` and focused OTA tests only;
  5. collect an allowlisted source tree;
  6. normalize ZIP timestamps, permissions and member ordering;
  7. write `manifest.json`, then run the independent verifier on the finished file;
  8. atomically publish only `robopark-<version>.ota` and print JSON containing `path`, `version`, `size`, and `sha256`.

  The CLI contract is:

  ```text
  python3 robopark-<version>.ota              # interactive menu
  python3 robopark-<version>.ota --help
  python3 robopark-<version>.ota verify --json
  python3 robopark-<version>.ota diagnose --output /path
  ```

- [ ] **Step 4: Add a bounded `ota` verification target, separate from load/soak.**

  `./scripts/verify.sh ota` runs verifier, builder, CLI, installer and update-unit tests. It must not start capacity or soak suites.

- [ ] **Step 5: Run the build twice and compare bytes.**

  ```bash
  ./scripts/verify.sh ota
  first_dir="$(mktemp -d)"
  second_dir="$(mktemp -d)"
  ./scripts/build-ota.sh "$first_dir"
  ./scripts/build-ota.sh "$second_dir"
  cmp "$first_dir"/*.ota "$second_dir"/*.ota
  shasum -a 256 "$first_dir"/*.ota "$second_dir"/*.ota
  ```

- [ ] **Step 6: Commit the builder and entry point.**

  ```bash
  git add deploy/ota scripts/build_ota.py scripts/build-ota.sh scripts/verify.sh \
    deploy/release-metadata.json tests/host/test_ota_builder.py
  git diff --cached --check
  git commit -m "feat(release): build deterministic unified OTA"
  ```

## Task 3: Implement menu-first USB lifecycle and clean installation

**Files:**

- Create: `deploy/ota/robopark_ota/menu.py`
- Create: `deploy/ota/robopark_ota/install.py`
- Create: `deploy/ota/robopark_ota/remove.py`
- Create: `deploy/ota/robopark_ota/diagnose.py`
- Create: `deploy/ota/robopark_ota/credentials.py`
- Modify: `deploy/installer/lib/configure.py`
- Modify: `deploy/installer/lib/install-services.py`
- Modify: `deploy/installer/lib/lifecycle.py`
- Modify: `deploy/installer/README-RU.txt`
- Create: `tests/host/test_ota_menu.py`
- Create: `tests/host/test_ota_clean_install.py`
- Create: `tests/host/test_ota_credentials.py`

- [ ] **Step 1: Write tests proving that the menu precedes preflight.**

  A broken Docker command, missing current release, or unsupported runtime must not prevent rendering and selecting `Диагностика`, `Полное удаление`, or `Выход`.

- [ ] **Step 2: Write destructive-boundary and password-leak tests.**

  Use a fake root and fake Docker runner. Seed both Robopark-owned and unrelated paths/containers/volumes/images. Assert clean install removes only the declared Robopark set. Capture argv, environment, stdout/stderr, journal and generated configuration and assert the chosen password is absent.

- [ ] **Step 3: Run the focused tests and confirm the missing lifecycle fails.**

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=deploy/ota:deploy/host \
    uv run --project apps/api --frozen --extra dev python -m pytest -p no:cacheprovider -q \
    tests/host/test_ota_menu.py tests/host/test_ota_clean_install.py \
    tests/host/test_ota_credentials.py
  ```

- [ ] **Step 4: Implement the action menu and explicit plan objects.**

  ```python
  class Action(StrEnum):
      CLEAN_INSTALL = "clean-install"
      UPDATE = "update"
      DIAGNOSE = "diagnose"
      REMOVE = "remove"
      EXIT = "exit"

  @dataclass(frozen=True)
  class RemovalPlan:
      paths: tuple[Path, ...]
      services: tuple[str, ...]
      compose_projects: tuple[str, ...]
      volumes: tuple[str, ...]
      image_prefixes: tuple[str, ...]
  ```

  Render `select_action()` before calling any action-specific discovery or preflight. Print the resolved deletion plan and require the exact phrase `УДАЛИТЬ ВСЕ ДАННЫЕ` before mutation.

- [ ] **Step 5: Implement secret-safe first-royal bootstrapping.**

  Prompt with `getpass.getpass()` twice; validate equality and product password policy. Pass credentials through a mode-`0600` root-owned temporary JSON file mounted read-only into a one-shot seed process, unlink it in `finally`, and make the seed process read and immediately discard it. Do not use `--password`, environment variables, or shell interpolation.

- [ ] **Step 6: Implement clean install, diagnose, update dispatch, remove, and smoke output.**

  Clean install has no backup branch. Use the verified bundle's extracted release tree, create empty PostgreSQL, run Alembic, seed roles/permissions/royal, wait for readiness, run a login-free health smoke-check, and publish version only after success. On failure print `clean_install_failed` and the diagnostic path without secrets.

- [ ] **Step 7: Run the focused host tests and shell syntax checks.**

  ```bash
  python3 -m compileall -q deploy/ota deploy/installer/lib
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=deploy/ota:deploy/host \
    uv run --project apps/api --frozen --extra dev python -m pytest -p no:cacheprovider -q \
    tests/host/test_ota_menu.py tests/host/test_ota_clean_install.py \
    tests/host/test_ota_credentials.py
  ```

- [ ] **Step 8: Commit the USB lifecycle.**

  ```bash
  git add deploy/ota/robopark_ota deploy/installer/lib deploy/installer/README-RU.txt \
    tests/host/test_ota_menu.py tests/host/test_ota_clean_install.py \
    tests/host/test_ota_credentials.py
  git diff --cached --check
  git commit -m "feat(installer): add menu-first clean OTA lifecycle"
  ```

## Task 4: Replace host signing/GitHub admission with verified local OTA staging

**Files:**

- Create: `deploy/host/robopark_host/ota_store.py`
- Create: `deploy/host/robopark_host/ota_update.py`
- Modify: `deploy/host/robopark_host/commands.py`
- Modify: `deploy/host/robopark_host/updater.py`
- Modify: `deploy/host/robopark_host/operation_capabilities.py`
- Modify: `deploy/host/robopark_host/retention.py`
- Modify: `deploy/host/robopark_host/doctor.py`
- Modify: `deploy/host/robopark_host/image_retention.py`
- Modify: `deploy/host/robopark_host/restore.py`
- Create: `tests/host/test_ota_store.py`
- Create: `tests/host/test_ota_update.py`
- Modify: `tests/host/test_update_recovery.py`
- Modify: `tests/host/test_end_to_end_update.py`

- [ ] **Step 1: Write host tests for admission, idempotency and crash recovery.**

  Cover: expected digest match, already-known digest reuse, incompatible/downgrade rejection, insufficient space, concurrent operation conflict, same operation replay, migration failure rollback, readiness failure rollback, crash after snapshot/staging/migration/cutover, retention of current plus previous, and TTL cleanup of abandoned staging.

- [ ] **Step 2: Run the host tests and confirm the old `release-update` contract fails them.**

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=deploy/ota:deploy/host \
    uv run --project apps/api --frozen --extra dev python -m pytest -p no:cacheprovider -q \
    tests/host/test_ota_store.py tests/host/test_ota_update.py \
    tests/host/test_update_recovery.py tests/host/test_end_to_end_update.py
  ```

- [ ] **Step 3: Introduce the `ota-update` typed operation.**

  Replace `release_id` with a server-created immutable package reference:

  ```python
  class OperationKind(StrEnum):
      OTA_UPDATE = "ota-update"

  @dataclass(frozen=True)
  class OtaUpdateRequest:
      operation_id: UUID
      upload_id: UUID
      sha256: str
      version: str
  ```

  The root host agent resolves `upload_id` only inside its fixed incoming directory, verifies file ownership/mode/regular-file status, then independently calls `verify_ota(..., expected_sha256=...)` before staging.

- [ ] **Step 4: Reuse the journaled update engine with explicit safe phases.**

  Durable phases are `accepted`, `verified`, `snapshot_done`, `staged`, `migration_started`, `migration_done`, `cutover_started`, `health_checked`, `published`, `rolled_back`, `failed`. Recovery must decide from journal plus filesystem links, never from process memory. The final receipt includes only version, package SHA-256, timestamps, safe phase and stable error code.

- [ ] **Step 5: Replace signature-aware retention and doctor checks.**

  Retain current and one previous successful release plus their digest receipts. Remove key/trust health checks; add incoming-upload disk use, abandoned staging, manifest consistency and rollback-readiness checks.

- [ ] **Step 6: Run focused host tests.**

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=deploy/ota:deploy/host \
    uv run --project apps/api --frozen --extra dev python -m pytest -p no:cacheprovider -q \
    tests/host/test_ota_store.py tests/host/test_ota_update.py \
    tests/host/test_update_recovery.py tests/host/test_end_to_end_update.py \
    tests/host/test_retention.py tests/host/test_doctor.py
  ```

- [ ] **Step 7: Commit the host OTA path.**

  ```bash
  git add deploy/host/robopark_host tests/host/test_ota_store.py \
    tests/host/test_ota_update.py tests/host/test_update_recovery.py \
    tests/host/test_end_to_end_update.py
  git diff --cached --check
  git commit -m "feat(host): stage and roll back hash-only OTA updates"
  ```

## Task 5: Add resumable, bounded, royal-only OTA uploads to the API

**Files:**

- Create: `apps/api/src/robopark_api/routers/admin_ota.py`
- Create: `apps/api/src/robopark_api/services/ops/ota_uploads.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Modify: `apps/api/src/robopark_api/ops_schemas.py`
- Modify: `apps/api/src/robopark_api/services/ops/host_bridge.py`
- Modify: `apps/api/src/robopark_api/services/ops/context.py`
- Modify: `apps/api/src/robopark_api/services/ops/operation_registry.py`
- Create: `apps/api/tests/test_admin_ota.py`
- Modify: `apps/api/tests/test_admin_ops.py`
- Modify: `apps/api/tests/test_ops_host_bridge.py`

- [ ] **Step 1: Write API tests for auth, streaming, resume and quotas.**

  Cover non-royal `403`, upload ownership, exact offset enforcement, duplicate-chunk idempotency, oversized chunk/package rejection, per-royal and global active-upload quotas, invalid client digest, server digest mismatch, interrupted resume, finalize replay, expired upload, unsafe file replacement, operation conflict and audit fields.

- [ ] **Step 2: Define exact HTTP contracts in tests.**

  ```text
  POST   /admin/ops/ota/uploads
         {filename, size, sha256} -> {upload_id, offset, chunk_size, expires_at, already_present}

  HEAD   /admin/ops/ota/uploads/{upload_id}
         -> Upload-Offset, Upload-Length, Upload-Expires

  PATCH  /admin/ops/ota/uploads/{upload_id}
         headers: Upload-Offset, Content-Type: application/offset+octet-stream
         body: one bounded chunk -> {offset}

  POST   /admin/ops/ota/uploads/{upload_id}/finalize
         {} -> {upload_id, sha256, size, version, changes, compatibility, state:"verified"}

  DELETE /admin/ops/ota/uploads/{upload_id}
         -> 204
  ```

- [ ] **Step 3: Run the focused API tests and confirm route failures.**

  ```bash
  cd apps/api
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen --extra dev \
    python -m pytest -p no:cacheprovider -q \
    tests/test_admin_ota.py tests/test_admin_ops.py tests/test_ops_host_bridge.py
  ```

- [ ] **Step 4: Implement streamed upload state and finalize.**

  Persist only bounded metadata in a mode-`0700` operations directory. Open partial files with no-follow/exclusive semantics, lock per upload, require a matching offset, write at most the advertised chunk size, `fsync`, and atomically update metadata. Finalize streams the whole-file SHA-256, verifies manifest and payload hashes, makes the file immutable to the API process, and returns sanitized inspection data.

- [ ] **Step 5: Connect finalized uploads to durable typed operations.**

  Add `HostOperationKind.OTA_UPDATE` and:

  ```python
  class HostOtaUpdateIn(_HostOperationBase):
      kind: Literal[HostOperationKind.OTA_UPDATE]
      upload_id: UUID
      sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
      version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,150}$")
  ```

  Before enqueue, resolve a finalized upload owned by the current royal, compare digest/version, re-check the privileged session, reserve the operation UUID, and audit actor/version/digest/result. Never forward the original filename or arbitrary path to root.

- [ ] **Step 6: Add TTL cleanup and disk-pressure behavior.**

  Cleanup expired partial/finalized-unclaimed uploads during startup and periodic maintenance. Refuse creation with `ota_insufficient_space` before accepting bytes when the configured reserve would be crossed.

- [ ] **Step 7: Run API lint and focused tests.**

  ```bash
  cd apps/api
  uv run --frozen --extra dev ruff check src/robopark_api/routers/admin_ota.py \
    src/robopark_api/services/ops/ota_uploads.py tests/test_admin_ota.py
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen --extra dev \
    python -m pytest -p no:cacheprovider -q \
    tests/test_admin_ota.py tests/test_admin_ops.py tests/test_ops_host_bridge.py
  ```

- [ ] **Step 8: Commit the upload API.**

  ```bash
  git add apps/api/src/robopark_api apps/api/tests/test_admin_ota.py \
    apps/api/tests/test_admin_ops.py apps/api/tests/test_ops_host_bridge.py
  git diff --cached --check
  git commit -m "feat(api): accept resumable royal OTA uploads"
  ```

## Task 6: Build the no-reload OTA experience for web and PWA

**Files:**

- Create: `apps/web/src/domains/system/ota/otaTypes.ts`
- Create: `apps/web/src/domains/system/ota/otaManifest.ts`
- Create: `apps/web/src/domains/system/ota/otaHash.worker.ts`
- Create: `apps/web/src/domains/system/ota/otaUpload.ts`
- Create: `apps/web/src/domains/system/ota/OtaUpdatePanel.tsx`
- Create: `apps/web/src/domains/system/ota/OtaUpdatePanel.css`
- Create: `apps/web/src/domains/system/ota/OtaUpdatePanel.test.tsx`
- Create: `apps/web/src/domains/system/ota/otaUpload.test.ts`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Modify: `apps/web/src/opsApi.ts`
- Modify: `apps/web/src/domains/system/SystemOperations.tsx`
- Modify: `apps/web/src/domains/system/SystemPage.tsx`
- Modify: `apps/web/src/sw.ts`

- [ ] **Step 1: Write component/client tests for the full user flow.**

  Test drag-and-drop/file input, `.ota` extension and ZIP preamble, local manifest preview, worker progress, hash mismatch, interrupted chunk retry/resume from server offset, already-present deduplication, install confirmation, durable receipt polling, mobile collapse, page revisit after reselecting the same file, and one PWA refresh only when build id changes.

- [ ] **Step 2: Run the focused web tests and confirm missing UI/client failures.**

  ```bash
  cd apps/web
  npm test -- src/domains/system/ota/OtaUpdatePanel.test.tsx \
    src/domains/system/ota/otaUpload.test.ts
  ```

- [ ] **Step 3: Implement worker-based hashing and minimal local inspection.**

  Add the pinned `@noble/hashes` dependency. The worker receives the `File`, streams `file.stream()`, incrementally updates `sha256.create()`, reports byte progress, and returns lowercase SHA-256. Test it against WebCrypto for bounded fixtures and include a fallback that slices the `File` into bounded chunks when `Blob.stream()` is unavailable. Read only the ZIP end record, bounded central directory and `manifest.json` for preview; reject ZIP64/encrypted/oversized previews in the browser and let the server remain authoritative.

- [ ] **Step 4: Implement resumable upload without page reload.**

  Store only `{upload_id, sha256, size, filename, expires_at}` in IndexedDB/local durable storage, never the file bytes. On retry/revisit require the user to reselect the file, re-hash it, then resume at the server's `HEAD` offset. Limit retries with jittered backoff and abort cleanly on logout or file change.

- [ ] **Step 5: Add a dedicated update panel and simplify operations.**

  Remove the generic `release-update` and `reinstall` buttons from `SystemOperations`. Render one royal-only “Обновление системы” panel with separate phases `Проверка файла`, `Загрузка`, `Готово к установке`, `Установка`, `Откат`, `Завершено`. Display version, changes, size, full SHA-256, compatibility and safe errors; provide diagnostic download on failure.

- [ ] **Step 6: Implement exactly-once build refresh.**

  Keep the pre-operation build id in session storage. After the operation succeeds and API becomes ready, compare `/admin/ops/release-status`; when changed, ask the service worker to activate/update and perform one `location.reload()`, guarded by `{operation_id, build_id}`. Do not reload during hash/upload/install polling.

- [ ] **Step 7: Run web lint, focused tests and production build.**

  ```bash
  cd apps/web
  npm run lint
  npm test -- src/domains/system/ota/OtaUpdatePanel.test.tsx \
    src/domains/system/ota/otaUpload.test.ts
  npm run build
  ```

- [ ] **Step 8: Commit the OTA UI.**

  ```bash
  git add apps/web/package.json apps/web/package-lock.json apps/web/src/opsApi.ts \
    apps/web/src/domains/system apps/web/src/sw.ts
  git diff --cached --check
  git commit -m "feat(web): add resumable royal OTA workflow"
  ```

## Task 7: Remove signing keys and GitHub Release machinery

**Files:**

- Delete: `scripts/release_pack.py`
- Delete: `scripts/pack-release.sh`
- Delete: `scripts/pack-installer.sh`
- Delete: `scripts/verify-artifact.py`
- Delete: `scripts/generate-release-key.py`
- Delete: `scripts/prepare-release-key.py`
- Delete: `deploy/keys/release-public-key.pem`
- Delete: `deploy/installer/lib/install-trust.py`
- Delete: `deploy/installer/lib/verify-bundle.py`
- Delete: `deploy/installer/lib/install-release.py`
- Delete: `deploy/installer/lib/install-release.sh`
- Delete: `deploy/host/robopark_host/trust.py`
- Delete: `deploy/host/robopark_host/github_releases.py`
- Delete: `apps/api/src/robopark_api/services/ops/release_signing.py`
- Delete: `.github/workflows/release.yml`
- Modify: `deploy/installer/install.sh`
- Modify: `deploy/installer/START.sh`
- Modify: `deploy/installer/lib/local-update.py`
- Modify: `apps/api/src/robopark_api/routers/admin_ops.py`
- Modify: `apps/api/src/robopark_api/services/ops/archives.py`
- Modify: `apps/api/src/robopark_api/services/release_status.py`
- Modify: `apps/web/src/components/admin/opsTestFixtures.ts`
- Delete or replace: `apps/api/tests/test_release_signing.py`
- Delete or replace: `tests/host/test_github_releases.py`
- Delete or replace: `tests/host/test_installer_trust.py`
- Delete or replace: `tests/host/test_key_rotation.py`

- [ ] **Step 1: Add a failing governance test that forbids retired release trust paths.**

  Extend `tests/host/test_repository_governance.py` to reject production references to `ROBOPARK_SIGNING_KEY`, `release-public-key.pem`, `.sig`, `github-update`, GitHub Release download code, `release_signing`, `install-trust`, and Ed25519 artifact verification. Permit normal Git remote metadata and CI checks unrelated to release delivery.

- [ ] **Step 2: Run the governance test and capture the expected legacy matches.**

  ```bash
  PYTHONDONTWRITEBYTECODE=1 \
    uv run --project apps/api --frozen --extra dev \
    python -m pytest -p no:cacheprovider tests/host/test_repository_governance.py -q
  ```

- [ ] **Step 3: Delete the retired files and remove all route/schema/UI branches.**

  Remove `/admin/ops/available-update`, `/admin/ops/github-update/approve`, old signed archive inspection, signing-state repair, key rotation, GitHub release discovery/download and obsolete installer entry points. Keep generic diagnostics, cleanup, rollback, reboot, backup, service and package operations that are still valid.

- [ ] **Step 4: Replace old tests with OTA equivalents rather than weakening assertions.**

  Update capability maps and web fixtures to include `ota-update` and exclude `release-update`. Update release status to report locally installed version/build/support metadata only; `available_update` becomes absent rather than a stale GitHub state.

- [ ] **Step 5: Prove no retired production references remain.**

  ```bash
  rg -n "ROBOPARK_SIGNING_KEY|release-public-key|github-update|release_signing|install-trust|\.sig\b" \
    apps deploy scripts .github --glob '!**/*.md'
  PYTHONDONTWRITEBYTECODE=1 \
    uv run --project apps/api --frozen --extra dev \
    python -m pytest -p no:cacheprovider tests/host/test_repository_governance.py -q
  ```

  The `rg` command must return no production matches. Documentation describing removal may remain.

- [ ] **Step 6: Commit the removal as one auditable change.**

  ```bash
  git add -A scripts deploy apps/api apps/web .github tests/host
  git diff --cached --check
  git commit -m "refactor(release): remove signing and GitHub delivery"
  ```

## Task 8: Update operator documentation and recovery runbooks

**Files:**

- Create: `docs/runbooks/build-ota.md`
- Create: `docs/runbooks/usb-clean-install.md`
- Create: `docs/runbooks/web-ota-update.md`
- Create: `docs/runbooks/ota-recovery.md`
- Modify: `README.md`
- Modify: `docs/operations.md`
- Modify: `docs/support.md`
- Modify: `docs/architecture/system-architecture.md`
- Modify: `tests/host/test_release_documentation.py`

- [ ] **Step 1: Write failing documentation-contract tests.**

  Require the canonical build command, one-file output, USB command, exact destructive phrase, no-backup warning, web upload path, diagnostics path, stable error codes, rollback explanation, supported OS/Python floor, and explicit statement that SHA-256 does not authenticate publisher identity.

- [ ] **Step 2: Run the documentation tests and confirm missing sections.**

  ```bash
  PYTHONDONTWRITEBYTECODE=1 \
    uv run --project apps/api --frozen --extra dev \
    python -m pytest -p no:cacheprovider tests/host/test_release_documentation.py -q
  ```

- [ ] **Step 3: Write concise role-specific runbooks.**

  Include exact commands and safe decision trees for:

  - developer: build and verify locally;
  - host operator: clean install/update/diagnose/remove from USB;
  - royal: inspect, upload, confirm, follow receipt, download diagnostics;
  - recovery: interrupted upload, failed update with successful rollback, rollback failure, and offline USB recovery.

- [ ] **Step 4: Run documentation and link checks.**

  ```bash
  PYTHONDONTWRITEBYTECODE=1 \
    uv run --project apps/api --frozen --extra dev \
    python -m pytest -p no:cacheprovider tests/host/test_release_documentation.py -q
  rg -n "pack-release|pack-installer|GitHub Release|release-public-key|ROBOPARK_SIGNING_KEY" \
    README.md docs deploy/installer/README-RU.txt
  ```

  Remaining historical/design mentions must explicitly say the mechanism is retired; operational instructions must use only `.ota`.

- [ ] **Step 5: Commit the runbooks.**

  ```bash
  git add README.md docs deploy/installer/README-RU.txt tests/host/test_release_documentation.py
  git diff --cached --check
  git commit -m "docs: document unified local OTA operations"
  ```

## Task 9: Run bounded acceptance and produce the first unified OTA

**Files:**

- Create: `tests/host/test_ota_acceptance.py`
- Modify: `scripts/verify.sh`
- Modify: `docs/product-completion/release-evidence.json`

- [ ] **Step 1: Add a single bounded acceptance test that composes the implemented boundaries.**

  The test builds a tiny package, verifies it, performs fake-root clean install, seeds one royal via secret file, performs a normal update preserving sample PostgreSQL/application data, injects migration and readiness failures to prove rollback, resumes an interrupted upload, and verifies the final operation receipt digest/version.

- [ ] **Step 2: Run all OTA-focused tests without load or soak.**

  ```bash
  ./scripts/verify.sh ota
  ./scripts/verify.sh fast
  ```

- [ ] **Step 3: Run the affected API and web regression tests.**

  ```bash
  cd apps/api
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen --extra dev \
    python -m pytest -p no:cacheprovider -q \
    tests/test_admin_ota.py tests/test_admin_ops.py tests/test_ops_http.py \
    tests/test_ops_host_bridge.py tests/test_ops_runner.py tests/test_ops_agent.py \
    tests/test_release_status.py
  cd ../web
  npm run lint
  npm test -- src/domains/system src/components/admin/AdminOpsPanel.test.tsx
  npm run build
  ```

- [ ] **Step 4: Perform a clean-tree deterministic build and independent inspection.**

  ```bash
  artifact_dir="$(mktemp -d)"
  ./scripts/build-ota.sh "$artifact_dir"
  python3 "$artifact_dir"/*.ota verify --json
  shasum -a 256 "$artifact_dir"/*.ota
  unzip -l "$artifact_dir"/*.ota
  ```

  Confirm there is exactly one `.ota`, no `.sig`, no key, no runtime/user data, and no nested prior artifact.

- [ ] **Step 5: Review the final diff for secrets, destructive scope and legacy paths.**

  ```bash
  git status --short
  git diff --check origin/main...HEAD
  rg -n "password=|--password|ROBOPARK_SIGNING_KEY|release-public-key|github-update|\.sig\b" \
    apps deploy scripts --glob '!**/*.md'
  ```

- [ ] **Step 6: Request one independent code review before final release commit.**

  The reviewer must prioritize archive parsing, deletion boundaries, credential secrecy, upload authorization/quotas, crash recovery and removal completeness. Resolve every critical/high finding and rerun the relevant focused tests.

- [ ] **Step 7: Commit acceptance evidence, then build the distributable outside the repository.**

  ```bash
  git add scripts/verify.sh tests/host/test_ota_acceptance.py \
    docs/product-completion/release-evidence.json
  git diff --cached --check
  git commit -m "test(release): verify unified OTA acceptance"
  ./scripts/build-ota.sh /absolute/user-selected/output
  ```

  Do not commit the `.ota`. Report its absolute path, version, size and SHA-256. Do not install it on a real host or erase real data until the user explicitly initiates that action.
