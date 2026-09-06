# Armbian Installer, Autostart, Diagnostics, and OTA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a self-installing Robopark archive for Armbian/Ubuntu with Tuna autostart, safe diagnostics and repair, signed Royal OTA, optional GitHub release discovery, and automatic rollback.

**Architecture:** `install.sh` prepares a root-owned host layout and immutable releases. A Python host utility outside the application containers owns diagnostics, update discovery, privileged cutover, service replacement, and rollback; the API exchanges sanitized state and approved commands through `/data/ops`. Release ZIPs are Ed25519-signed, tested in staging, and activated by an atomic `current` symlink switch.

**Tech Stack:** POSIX shell, Python 3 standard library plus `cryptography`, systemd, Docker Engine with Compose v2, FastAPI/Pydantic, React/TypeScript, pytest, Vitest, GitHub Actions, Tuna CLI.

**Spec:** `docs/superpowers/specs/2026-09-07-armbian-installer-ota-design.md`

## Global Constraints

- Supported first host: Armbian 26 ARM64 with 8 GiB RAM; also support Ubuntu ARM64/AMD64 with 32 GiB RAM.
- Require `systemd`, `apt`, and at least 6 GiB free storage before host mutation.
- Publish only through Tuna HTTPS to `127.0.0.1:8080`; never expose API, database, Docker, or port 8080 on WAN.
- Keep `/etc/robopark` and `/var/lib/robopark` outside immutable releases; never package production secrets.
- Store secret configuration as root-owned mode `0600`; never log secret values or command-line arguments containing them.
- Every OTA installation requires explicit Royal approval; GitHub checking never applies a release automatically.
- Keep the current and previous successful release; never remove either during cleanup.
- Repair operations may perform only the allowlisted actions in the specification and never modify application data or rotate secrets.
- Preserve the existing manual Compose deployment until the installer path passes its complete acceptance gate.
- Use the canonical `./scripts/verify.sh` checks and add a `host` target for installer/updater verification.

## File Map

- `deploy/installer/install.sh`: interactive and resumable root installer.
- `deploy/installer/lib/*.sh`: focused preflight, package, configuration, release, and service phases.
- `deploy/host/robopark_host/*.py`: host CLI, paths, atomic state, diagnostics, repair, GitHub discovery, signing, updater, and rollback.
- `deploy/systemd/*`: application, Tuna, updater, checker, doctor, and watchdog units/timers.
- `tests/host/*`: fake-root and fake-command host tests that do not mutate the development machine.
- `apps/api/src/robopark_api/services/ops/*`: signed archive validation and host command/status bridge.
- `apps/api/src/robopark_api/routers/admin_ops.py`: Royal-only health, diagnostic, repair, discovery, and approval endpoints.
- `apps/web/src/components/admin/AdminOpsPanel.tsx`: Royal operations and system-health UI.
- `scripts/pack-release.sh`, `scripts/pack-installer.sh`: reproducible signed artifacts.
- `.github/workflows/release.yml`: verified release artifact publication.

---

### Task 1: Versioned signed release contract

**Files:**
- Modify: `apps/api/src/robopark_api/services/ops/archives.py`
- Modify: `apps/api/tests/test_ops_archives.py`
- Create: `apps/api/src/robopark_api/services/ops/release_signing.py`
- Create: `apps/api/tests/test_release_signing.py`
- Create: `scripts/release_pack.py`
- Create: `scripts/generate-release-key.py`
- Create: `deploy/keys/release-public-key.pem`
- Modify: `scripts/pack-release.sh`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `canonical_manifest_bytes(manifest: dict) -> bytes`.
- Produces: `sign_manifest(manifest: dict, private_key: bytes) -> bytes`.
- Produces: `verify_manifest_signature(manifest: dict, signature: bytes, public_key: bytes) -> None` raising `ArchiveError("signature_invalid")`.
- Produces: release format 2 with `manifest.json` and `manifest.sig` reserved members.
- Consumes: existing archive path, count, size, compression, and SHA-256 protections.

- [ ] **Step 1: Add failing signature and compatibility tests**

```python
def test_signed_release_roundtrip(tmp_path, ed25519_keys):
    private_key, public_key = ed25519_keys
    archive = build_archive(
        kind=KIND_RELEASE,
        source_root=release_tree(tmp_path),
        app_version="1.2.3",
        release_meta={"git_sha": "a" * 40, "migration_head": "0017_driver_work_reports"},
        signing_key=private_key,
    )
    meta = inspect_archive(archive, expected_kind=KIND_RELEASE, public_key=public_key)
    assert meta.app_version == "1.2.3"
    assert meta.git_sha == "a" * 40

def test_tampered_signed_release_is_rejected(tmp_path, ed25519_keys):
    archive = tamper_manifest(build_signed_release(tmp_path, ed25519_keys), "app_version", "9.9.9")
    with pytest.raises(ArchiveError, match="signature_invalid"):
        inspect_archive(archive, expected_kind=KIND_RELEASE, public_key=ed25519_keys[1])
```

- [ ] **Step 2: Run the new tests and confirm the contract is missing**

Run: `cd apps/api && uv run --frozen --extra dev pytest tests/test_release_signing.py tests/test_ops_archives.py -q`

Expected: failure because signing helpers and format-2 arguments do not exist.

- [ ] **Step 3: Implement canonical signing and strict format-2 inspection**

```python
def canonical_manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

def verify_manifest_signature(manifest: dict, signature: bytes, public_key: bytes) -> None:
    try:
        serialization.load_pem_public_key(public_key).verify(
            signature, canonical_manifest_bytes(manifest)
        )
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise ArchiveError("signature_invalid") from exc
```

Reserve `manifest.sig`, reject duplicate member names, reject symlinks and non-regular ZIP entries, require exact metadata fields, and keep format-1 support only for snapshots. A release must be format 2 and signed.

- [ ] **Step 4: Add explicit release-key generation with private-file protection**

```bash
mkdir -p .release-secrets deploy/keys
python3 scripts/generate-release-key.py \
  --private .release-secrets/robopark-release-key.pem \
  --public deploy/keys/release-public-key.pem
test "$(stat -f '%Lp' .release-secrets/robopark-release-key.pem 2>/dev/null || stat -c '%a' .release-secrets/robopark-release-key.pem)" = 600
```

Add `.release-secrets/` to `.gitignore`. The generator refuses to overwrite an existing private key and writes the public key separately for tracking and installer packaging.

- [ ] **Step 5: Update release packing to require a protected signing-key path**

```sh
: "${ROBOPARK_SIGNING_KEY_FILE:?set ROBOPARK_SIGNING_KEY_FILE to the Ed25519 PEM key}"
test -r "$ROBOPARK_SIGNING_KEY_FILE"
python3 "$root/scripts/release_pack.py" \
  --root "$stage" --output "$out" --version "$version" \
  --git-sha "$git_sha" --signing-key "$ROBOPARK_SIGNING_KEY_FILE"
```

The key path is read from the environment, its contents are never printed, and packaging refuses an unsigned production release.

- [ ] **Step 6: Run focused and existing ops tests**

Run: `cd apps/api && uv run --frozen --extra dev pytest tests/test_release_signing.py tests/test_ops_archives.py tests/test_ops_runner.py tests/test_ops_http.py -q`

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/robopark_api/services/ops/archives.py apps/api/src/robopark_api/services/ops/release_signing.py apps/api/tests/test_ops_archives.py apps/api/tests/test_release_signing.py scripts/pack-release.sh scripts/release_pack.py scripts/generate-release-key.py deploy/keys/release-public-key.pem .gitignore
git commit -m "feat(ops): sign and verify release archives"
```

### Task 2: Host utility foundation and durable state

**Files:**
- Create: `deploy/host/robopark_host/__init__.py`
- Create: `deploy/host/robopark_host/paths.py`
- Create: `deploy/host/robopark_host/state.py`
- Create: `deploy/host/robopark_host/redaction.py`
- Create: `deploy/host/robopark_host/cli.py`
- Create: `deploy/host/robopark`
- Create: `tests/host/test_host_state.py`
- Create: `tests/host/conftest.py`

**Interfaces:**
- Produces: immutable `HostPaths.from_root(root: Path) -> HostPaths`.
- Produces: `atomic_write_json(path: Path, payload: dict, mode: int = 0o600) -> None`.
- Produces: `exclusive_lock(path: Path)` context manager.
- Produces: `redact(value: object) -> object` for structured logs and status.
- Produces: CLI commands `status`, `doctor`, `repair`, `update`, `check-update`, and `watchdog`.

- [ ] **Step 1: Write failing fake-root state and redaction tests**

```python
def test_atomic_state_is_mode_600_and_valid_json(host_paths):
    atomic_write_json(host_paths.state / "status.json", {"state": "ready"})
    target = host_paths.state / "status.json"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {"state": "ready"}

def test_redaction_removes_nested_secret_values():
    value = {"TUNA_TOKEN": "tt_secret", "nested": {"password": "hidden"}, "state": "ok"}
    assert redact(value) == {"TUNA_TOKEN": "[REDACTED]", "nested": {"password": "[REDACTED]"}, "state": "ok"}
```

- [ ] **Step 2: Verify the tests fail because the host package is absent**

Run: `PYTHONPATH=deploy/host pytest tests/host/test_host_state.py -q`

Expected: import failure for `robopark_host`.

- [ ] **Step 3: Implement paths, atomic writes, lock, redaction, and CLI dispatch**

```python
@dataclass(frozen=True)
class HostPaths:
    root: Path
    opt: Path
    etc: Path
    var: Path
    releases: Path
    current: Path
    previous: Path
    ops: Path
    state: Path

    @classmethod
    def from_root(cls, root: Path = Path("/")) -> "HostPaths":
        return cls(root, root / "opt/robopark", root / "etc/robopark", root / "var/lib/robopark", root / "opt/robopark/releases", root / "opt/robopark/current", root / "opt/robopark/previous", root / "var/lib/robopark/ops", root / "var/lib/robopark/ops/state")
```

The test-only root comes from `ROBOPARK_ROOT`; production defaults to `/`. Reject a non-root override unless `ROBOPARK_TESTING=1`.

- [ ] **Step 4: Run host foundation tests**

Run: `PYTHONPATH=deploy/host pytest tests/host/test_host_state.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/host tests/host/test_host_state.py tests/host/conftest.py
git commit -m "feat(host): add durable host utility foundation"
```

### Task 3: Resumable installer and secret-safe wizard

**Files:**
- Create: `deploy/installer/install.sh`
- Create: `deploy/installer/lib/common.sh`
- Create: `deploy/installer/lib/preflight.sh`
- Create: `deploy/installer/lib/packages.sh`
- Create: `deploy/installer/lib/configure.sh`
- Create: `deploy/installer/lib/install-release.sh`
- Create: `tests/host/test_installer.sh`
- Create: `tests/host/fake-bin/apt-get`
- Create: `tests/host/fake-bin/systemctl`
- Create: `tests/host/fake-bin/docker`
- Create: `tests/host/fake-bin/tuna`

**Interfaces:**
- Consumes: signed initial release and `HostPaths` layout.
- Produces: `sudo ./install.sh`, `--non-interactive CONFIG_FILE`, and `--resume` modes.
- Produces: `/etc/robopark/host.env`, `tuna.env`, and `updater.env` with atomic mode `0600` writes.
- Produces: phase journal `/var/lib/robopark/ops/state/install.json`.

- [ ] **Step 1: Write failing shell scenarios**

```sh
run_case clean-arm64 env \
  ROBOPARK_TESTING=1 ROBOPARK_ROOT="$case_root" \
  PATH="$fake_bin:$PATH" ARCH=aarch64 OS_ID=armbian FREE_GIB=12 \
  sh deploy/installer/install.sh --non-interactive "$fixtures/valid.env"
assert_file "$case_root/etc/robopark/host.env"
assert_mode 600 "$case_root/etc/robopark/host.env"
assert_contains "$case_root/var/lib/robopark/ops/state/install.json" '"phase":"complete"'
assert_not_contains "$case_root/install.log" 'tt_test_secret'
```

Add cases for unsupported architecture, less than 6 GiB, interrupted `dpkg`, repeated installation preserving `SECRET_KEY`, and resume after a failed package phase.

- [ ] **Step 2: Run the harness and confirm the installer is absent**

Run: `sh tests/host/test_installer.sh`

Expected: failure because `deploy/installer/install.sh` does not exist.

- [ ] **Step 3: Implement preflight and bounded package recovery**

```sh
require_supported_arch() {
  case "$ROBOPARK_ARCH" in aarch64|arm64|x86_64|amd64) ;; *) die unsupported_arch ;; esac
}

repair_dpkg_if_needed() {
  if ! dpkg --audit 2>/dev/null | grep -q .; then return 0; fi
  dpkg --configure -a || die dpkg_repair_failed
}
```

Install `ca-certificates curl gnupg jq rsync python3 python3-cryptography docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin` and Tuna from its signed APT repository. Retry network package operations three times with 2/5/10-second delays.

- [ ] **Step 4: Implement interactive prompts and atomic configuration**

```sh
prompt_secret TUNA_TOKEN "Tuna token"
prompt_secret SEED_PASSWORD "Пароль первого Royal"
SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
write_secret_env "$ROBOPARK_ETC/host.env" \
  "ROBOPARK_ROLE=host" "COOKIE_SECURE=true" "SECRET_KEY=$SECRET_KEY"
```

Never export wizard secrets globally. Validate Royal password length/complexity and accept only `ru` or a value returned by `tuna http --help` for a non-default region.

- [ ] **Step 5: Implement initial signed release installation and idempotent resume**

Verify the embedded release before creating its version directory. Write phase state before and after each mutation. On rerun, validate existing config and symlinks, preserve non-empty secrets, and continue from the first incomplete phase.

- [ ] **Step 6: Run all installer scenarios**

Run: `sh -n deploy/installer/install.sh deploy/installer/lib/*.sh && sh tests/host/test_installer.sh`

Expected: syntax and scenarios pass; logs contain no fixture secrets.

- [ ] **Step 7: Commit**

```bash
git add deploy/installer tests/host/test_installer.sh tests/host/fake-bin
git commit -m "feat(installer): bootstrap supported Armbian hosts"
```

### Task 4: Production Compose layout and systemd autostart

**Files:**
- Modify: `deploy/docker-compose.yml`
- Modify: `deploy/tuna-http.sh`
- Create: `deploy/systemd/robopark.service`
- Create: `deploy/systemd/robopark-tuna.service`
- Create: `deploy/systemd/robopark-updater.service`
- Create: `deploy/systemd/robopark-update-check.service`
- Create: `deploy/systemd/robopark-update-check.timer`
- Create: `deploy/systemd/robopark-doctor.service`
- Create: `deploy/systemd/robopark-doctor.timer`
- Create: `deploy/systemd/robopark-watchdog.service`
- Create: `deploy/systemd/robopark-watchdog.timer`
- Create: `tests/host/test_systemd_units.py`
- Modify: `apps/api/tests/test_ops_agent.py`

**Interfaces:**
- Consumes: `/opt/robopark/current`, `/etc/robopark/*.env`, `/var/lib/robopark/data`.
- Produces: ordered boot with application readiness before Tuna.
- Produces: bounded restart and watchdog behavior.

- [ ] **Step 1: Write failing unit dependency and security tests**

```python
def test_tuna_waits_for_application(units):
    tuna = units["robopark-tuna.service"]
    assert "Requires=robopark.service" in tuna
    assert "After=network-online.target robopark.service" in tuna

def test_timers_have_persistent_bounded_schedules(units):
    assert "OnUnitActiveSec=6h" in units["robopark-update-check.timer"]
    assert "OnCalendar=daily" in units["robopark-doctor.timer"]
    assert "OnUnitActiveSec=2min" in units["robopark-watchdog.timer"]
    assert all("Persistent=true" in value for name, value in units.items() if name.endswith(".timer"))
```

- [ ] **Step 2: Confirm the unit tests fail**

Run: `pytest tests/host/test_systemd_units.py -q`

Expected: missing unit files.

- [ ] **Step 3: Add units and host-bound Compose data path**

```ini
[Unit]
Description=Robopark application stack
Requires=docker.service
After=network-online.target docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=HOST_ENV_FILE=/etc/robopark/host.env
ExecStart=/usr/bin/docker compose -f /opt/robopark/current/deploy/docker-compose.yml up -d --build --wait --wait-timeout 180 api web
ExecStop=/usr/bin/docker compose -f /opt/robopark/current/deploy/docker-compose.yml stop api web
TimeoutStartSec=900
```

Set Compose data source to `${ROBOPARK_DATA_SOURCE:-robopark_data}` and installer config to `/var/lib/robopark/data`. Remove the Docker-socket `ops-agent` from the installer profile while keeping a legacy profile for existing manual installations.

- [ ] **Step 4: Add installer unit installation and enablement**

Install units atomically, run `systemctl daemon-reload`, enable Docker plus all Robopark units/timers, start `robopark.service`, then start Tuna only after local readiness.

- [ ] **Step 5: Validate units and Compose**

Run: `pytest tests/host/test_systemd_units.py -q && HOST_ENV_FILE=./host.env.example docker compose -f deploy/docker-compose.yml config --quiet`

Expected: tests and Compose validation pass.

- [ ] **Step 6: Commit**

```bash
git add deploy/docker-compose.yml deploy/tuna-http.sh deploy/systemd deploy/installer apps/api/tests/test_ops_agent.py tests/host/test_systemd_units.py
git commit -m "feat(deploy): add ordered systemd autostart"
```

### Task 5: Doctor, status, repair, and watchdog

**Files:**
- Create: `deploy/host/robopark_host/checks.py`
- Create: `deploy/host/robopark_host/doctor.py`
- Create: `deploy/host/robopark_host/repair.py`
- Create: `deploy/host/robopark_host/watchdog.py`
- Create: `deploy/host/robopark_host/bundle.py`
- Create: `tests/host/test_doctor.py`
- Create: `tests/host/test_repair.py`
- Create: `tests/host/test_diagnostic_bundle.py`
- Modify: `deploy/host/robopark_host/cli.py`

**Interfaces:**
- Produces: `CheckResult(code: str, status: Literal["ok", "warning", "failed"], message: str, repair: str | None)`.
- Produces: `run_doctor(paths, runner, http) -> DiagnosticReport`.
- Produces: `run_repairs(report, allowlist, runner) -> RepairReport`.
- Produces: secret-free diagnostic ZIP.
- Produces: consecutive-failure watchdog state and targeted service restart.

- [ ] **Step 1: Write failing behavior tests for checks and repair boundaries**

```python
def test_database_failure_is_reported_but_never_auto_repaired(host):
    report = run_doctor(host.paths, host.runner(db_ready=False), host.http)
    item = report.by_code("database_unavailable")
    assert item.status == "failed"
    assert item.repair is None

def test_repair_restarts_only_allowlisted_failed_service(host):
    report = DiagnosticReport([CheckResult("tuna_inactive", "failed", "Tuna остановлен", "restart_tuna")])
    result = run_repairs(report, DEFAULT_REPAIRS, host.runner())
    assert result.performed == ["restart_tuna"]
    assert host.commands == [["systemctl", "restart", "robopark-tuna.service"]]
```

- [ ] **Step 2: Verify the tests fail because diagnostics are absent**

Run: `PYTHONPATH=deploy/host pytest tests/host/test_doctor.py tests/host/test_repair.py tests/host/test_diagnostic_bundle.py -q`

Expected: import failures.

- [ ] **Step 3: Implement the typed check registry and read-only doctor**

Implement exact checks from specification section 8.2. Commands use argument arrays, timeouts, and capped output. Environment values are checked by key presence and consistency without returning values.

- [ ] **Step 4: Implement allowlisted repairs and mandatory post-check**

```python
DEFAULT_REPAIRS = {
    "restart_docker": ["systemctl", "restart", "docker.service"],
    "restart_app": ["systemctl", "restart", "robopark.service"],
    "restart_tuna": ["systemctl", "restart", "robopark-tuna.service"],
    "daemon_reload": ["systemctl", "daemon-reload"],
}
```

Implement cleanup with explicit paths and retention selectors. Reject volume pruning, database commands, secret mutation, firewall changes, and commands outside the registry.

- [ ] **Step 5: Implement bundle redaction and watchdog threshold**

The bundle contains the sanitized JSON report, bounded journal excerpts for named Robopark units, Compose service state, and release metadata. It excludes environment files, database files, attachments, raw command lines, and user payloads. Watchdog restarts after three consecutive local readiness failures and resets after success.

- [ ] **Step 6: Run host diagnostic tests**

Run: `PYTHONPATH=deploy/host pytest tests/host/test_doctor.py tests/host/test_repair.py tests/host/test_diagnostic_bundle.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add deploy/host/robopark_host deploy/host/robopark tests/host/test_doctor.py tests/host/test_repair.py tests/host/test_diagnostic_bundle.py
git commit -m "feat(host): add diagnostics repair and watchdog"
```

### Task 6: Atomic host updater, self-update, and rollback

**Files:**
- Create: `deploy/host/robopark_host/release.py`
- Create: `deploy/host/robopark_host/updater.py`
- Create: `deploy/host/robopark_host/rollback.py`
- Create: `deploy/host/robopark_host/launcher.py`
- Create: `tests/host/test_updater.py`
- Create: `tests/host/test_update_recovery.py`
- Modify: `deploy/host/robopark_host/cli.py`
- Modify: `apps/api/src/robopark_api/services/ops/runner.py`

**Interfaces:**
- Consumes: approved command JSON with `job_id`, `kind`, `artifact`, `actor_user_id`, `created_at`.
- Produces: durable phase journal and sanitized `host-status.json`/`rebuild.result`.
- Produces: `apply_release(request: UpdateRequest, paths: HostPaths, runner: Runner) -> UpdateResult`.
- Produces: `recover_interrupted_update(paths, runner) -> RecoveryResult`.

- [ ] **Step 1: Write failing update state-machine tests**

```python
@pytest.mark.parametrize("phase", ["unpacked", "built", "snapshotted", "switched", "migrated", "started"])
def test_power_loss_recovers_to_a_verified_release(host, signed_release, phase):
    host.interrupt_after(phase)
    with pytest.raises(SimulatedPowerLoss):
        apply_release(host.request(signed_release), host.paths, host.runner)
    result = recover_interrupted_update(host.paths, host.runner)
    assert result.state in {"current_healthy", "previous_restored"}
    assert host.current_target().exists()

def test_failed_health_restores_previous_code_units_and_snapshot(host, signed_release):
    result = apply_release(host.request(signed_release), host.paths, host.runner(health=False))
    assert result.error == "cutover_unhealthy"
    assert host.current_version() == "1.0.0"
    assert host.snapshot_restored is True
    assert host.previous_units_restored is True
```

- [ ] **Step 2: Confirm updater tests fail**

Run: `PYTHONPATH=deploy/host pytest tests/host/test_updater.py tests/host/test_update_recovery.py -q`

Expected: updater modules do not exist.

- [ ] **Step 3: Implement verify, stage, build, test, and pre-cutover smoke phases**

Each phase writes state atomically before mutation. Validate same-filesystem staging, available-space estimate, signature, compatibility, Compose config, candidate images, candidate-provided `scripts/verify.sh api` and `web`, and isolated readiness.

- [ ] **Step 4: Implement snapshot, atomic symlink switch, migrations, and health gate**

```python
previous_target = paths.current.resolve(strict=True)
atomic_symlink(previous_target, paths.previous)
atomic_symlink(candidate_release, paths.current)
runner.run(["systemctl", "restart", "robopark.service"], timeout=900)
if not health.wait_ready(timeout=180):
    rollback_release(paths, journal, runner)
    return UpdateResult.failed("cutover_unhealthy")
```

Writers remain under maintenance until the health gate completes. Restore the data snapshot only when production migration began and writes have not resumed.

- [ ] **Step 5: Implement host-tool self-update through stable launcher**

Stage new host modules and units, run `python3 staged/robopark --self-test`, atomically replace the installed host-tools symlink, and let `launcher.py` perform `daemon-reload`, updater restart, and final reconciliation after the old process exits.

- [ ] **Step 6: Implement deterministic interruption recovery and retention**

Use the journal to discard pre-cutover staging, finish verification for a running candidate, or restore previous release and snapshot. Retain exactly two successful release targets plus active rollback material.

- [ ] **Step 7: Run updater and existing ops regression tests**

Run: `PYTHONPATH=deploy/host pytest tests/host/test_updater.py tests/host/test_update_recovery.py -q && cd apps/api && uv run --frozen --extra dev pytest tests/test_ops_runner.py tests/test_ops_agent.py tests/test_ops_jobs_abort.py -q`

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add deploy/host/robopark_host apps/api/src/robopark_api/services/ops/runner.py tests/host/test_updater.py tests/host/test_update_recovery.py
git commit -m "feat(ota): add atomic host updater and rollback"
```

### Task 7: Royal host bridge and sanitized health API

**Files:**
- Create: `apps/api/src/robopark_api/services/ops/host_bridge.py`
- Create: `apps/api/src/robopark_api/ops_schemas.py`
- Modify: `apps/api/src/robopark_api/routers/admin_ops.py`
- Modify: `apps/api/src/robopark_api/services/ops/jobs.py`
- Modify: `apps/api/src/robopark_api/config.py`
- Create: `apps/api/tests/test_ops_host_bridge.py`
- Modify: `apps/api/tests/test_ops_http.py`
- Modify: `apps/api/tests/test_security_hardening.py`

**Interfaces:**
- Produces: `GET /admin/ops/system-health`.
- Produces: `POST /admin/ops/diagnostics`, `POST /admin/ops/repair`, and `GET /admin/ops/diagnostic-artifact`.
- Produces: `POST /admin/ops/update/inspect` and `POST /admin/ops/update/approve`.
- Produces: atomic command files consumed by the root updater; API never executes root commands.
- Consumes: sanitized host status files.

- [ ] **Step 1: Write failing Royal access and secret-boundary tests**

```python
def test_only_royal_can_request_repair(client, seed_admin, login_as, tmp_path):
    login_as(seed_admin)
    assert client.post("/admin/ops/repair").status_code == 403

def test_health_response_never_contains_host_secrets(client, seed_royal, test_settings):
    write_host_status(test_settings, {"state": "degraded", "TUNA_TOKEN": "tt_leak"})
    response = client.get("/admin/ops/system-health")
    assert response.status_code == 200
    assert "tt_leak" not in response.text
    assert set(response.json()) == {"version", "git_sha", "generated_at", "overall", "checks", "update", "last_backup"}
```

- [ ] **Step 2: Run focused HTTP tests and confirm endpoints are missing**

Run: `cd apps/api && uv run --frozen --extra dev pytest tests/test_ops_host_bridge.py tests/test_ops_http.py -q`

Expected: 404 for new endpoints.

- [ ] **Step 3: Implement strict schemas and atomic command writer**

```python
class HostCommand(BaseModel):
    id: UUID
    kind: Literal["diagnostics", "repair", "update"]
    actor_user_id: int
    artifact: str | None = None
    created_at: datetime

def enqueue_command(ops_dir: Path, command: HostCommand) -> Path:
    target = approved_inbox(ops_dir) / f"{command.id}.json"
    atomic_write_json(target, command.model_dump(mode="json"))
    return target
```

The API accepts only server-created artifact basenames and resolves them beneath the ops artifact directory. Host paths supplied by clients are rejected.

- [ ] **Step 4: Refactor manual update upload into inspect then approve**

Inspection stores and validates the signed archive and returns version, Git SHA, migration head, and notes. Approval requires `ОБНОВИТЬ`, creates an ops job, audits the Royal actor, and enqueues the host command.

- [ ] **Step 5: Add diagnostics, repair, sanitized health, and artifact endpoints**

Use response allowlists rather than recursive removal. Diagnostic ZIP download resolves only the artifact named by the completed diagnostic job.

- [ ] **Step 6: Run ops and security tests**

Run: `cd apps/api && uv run --frozen --extra dev pytest tests/test_ops_host_bridge.py tests/test_ops_http.py tests/test_security_hardening.py tests/test_ops_jobs_abort.py -q`

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/robopark_api/ops_schemas.py apps/api/src/robopark_api/config.py apps/api/src/robopark_api/routers/admin_ops.py apps/api/src/robopark_api/services/ops apps/api/tests/test_ops_host_bridge.py apps/api/tests/test_ops_http.py apps/api/tests/test_security_hardening.py
git commit -m "feat(api): bridge Royal operations to host services"
```

### Task 8: GitHub release discovery with Royal approval

**Files:**
- Create: `deploy/host/robopark_host/github_releases.py`
- Create: `tests/host/test_github_releases.py`
- Modify: `deploy/host/robopark_host/cli.py`
- Modify: `apps/api/src/robopark_api/ops_schemas.py`
- Modify: `apps/api/src/robopark_api/routers/admin_ops.py`
- Modify: `apps/api/tests/test_ops_host_bridge.py`

**Interfaces:**
- Produces: `check_latest_release(config, http) -> AvailableRelease | None`.
- Produces: `download_approved_release(release, paths, http) -> Path` with whole-ZIP signature verification.
- Produces: `GET /admin/ops/available-update` and `POST /admin/ops/github-update/approve`.
- Consumes: root-only GitHub token and exact repository slug from `updater.env`.

- [ ] **Step 1: Write failing public/private/stale discovery tests**

```python
def test_private_repository_token_never_enters_status(fake_github, host_paths):
    result = check_latest_release(config(token="github_pat_secret"), fake_github)
    stored = json.loads((host_paths.state / "available-update.json").read_text())
    assert result.version == "1.3.0"
    assert "github_pat_secret" not in json.dumps(stored)

def test_checker_requires_all_signed_assets(fake_github):
    fake_github.release.assets = ["robopark-release-1.3.0.zip"]
    assert check_latest_release(config(), fake_github) is None
```

- [ ] **Step 2: Confirm discovery tests fail**

Run: `PYTHONPATH=deploy/host pytest tests/host/test_github_releases.py -q`

Expected: missing discovery module.

- [ ] **Step 3: Implement exact-repository metadata checks and version comparison**

Use `urllib.request` with timeouts, `Accept: application/vnd.github+json`, bounded response size, optional bearer token, and stable/prerelease channel filtering. Require ZIP, ZIP signature, and JSON metadata assets whose names match the parsed version exactly.

- [ ] **Step 4: Implement approved download and signature verification**

Download to `.partial`, cap bytes, verify detached ZIP signature before rename, then allow the updater to verify the internal manifest signature and hashes. Network failure leaves current state unchanged and records `discovery_stale`.

- [ ] **Step 5: Add Royal availability and approval endpoints**

Approval creates a host command referencing the release ID already written by the root checker; the browser cannot provide an arbitrary download URL.

- [ ] **Step 6: Run host and API discovery tests**

Run: `PYTHONPATH=deploy/host pytest tests/host/test_github_releases.py -q && cd apps/api && uv run --frozen --extra dev pytest tests/test_ops_host_bridge.py -q`

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add deploy/host/robopark_host/github_releases.py deploy/host/robopark_host/cli.py tests/host/test_github_releases.py apps/api/src/robopark_api/ops_schemas.py apps/api/src/robopark_api/routers/admin_ops.py apps/api/tests/test_ops_host_bridge.py
git commit -m "feat(ota): discover GitHub releases for Royal approval"
```

### Task 9: Royal operations and system-health interface

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/components/admin/AdminOpsPanel.tsx`
- Modify: `apps/web/src/components/admin/AdminOpsPanel.test.tsx`
- Modify: `apps/web/src/i18n/ru.ts`
- Modify: `apps/web/src/i18n/errors.ts`
- Create: `apps/web/src/components/admin/SystemHealthPanel.tsx`
- Create: `apps/web/src/components/admin/SystemHealthPanel.test.tsx`

**Interfaces:**
- Consumes: system health, update inspection, available update, diagnostic, repair, and approval endpoints from Tasks 7-8.
- Produces: sanitized Royal health cards, GitHub update notice, ZIP preview, explicit confirmation, progress, rollback reason, and diagnostic download.

- [ ] **Step 1: Write failing health and approval interaction tests**

```tsx
it('shows degraded checks and requests repair only after Royal action', async () => {
  render(<SystemHealthPanel client={clientWithHealth('degraded')} />)
  expect(await screen.findByText('Tuna остановлен')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Исправить безопасные проблемы' }))
  expect(api.opsRepair).toHaveBeenCalledTimes(1)
})

it('previews a release before enabling approval', async () => {
  render(<AdminOpsPanel />)
  await chooseFile(screen.getByLabelText('Архив обновления'), signedZip)
  expect(await screen.findByText('Версия 1.3.0')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Установить обновление' })).toBeDisabled()
})
```

- [ ] **Step 2: Confirm the UI tests fail**

Run: `cd apps/web && npm test -- src/components/admin/AdminOpsPanel.test.tsx src/components/admin/SystemHealthPanel.test.tsx`

Expected: missing component and API methods.

- [ ] **Step 3: Add exact API types and methods**

```ts
export type HostCheck = { code: string; status: 'ok' | 'warning' | 'failed'; message: string }
export type SystemHealth = {
  version: string; git_sha: string; generated_at: string; overall: HostCheck['status']
  checks: HostCheck[]; update: AvailableUpdate | null; last_backup: string | null
}
```

Add methods for inspection, approval, GitHub approval, diagnostics, repair, health, available update, and diagnostic artifact.

- [ ] **Step 4: Implement system health and OTA presentation**

Use existing Panels and Alerts. Poll only while an operation is active; otherwise use the shared cached resource and focus refresh. Labels are Russian, status is text plus icon/color, and logs remain sanitized server output.

- [ ] **Step 5: Run focused UI tests, build, and navigation checks**

Run: `cd apps/web && npm test -- src/components/admin/AdminOpsPanel.test.tsx src/components/admin/SystemHealthPanel.test.tsx && npm run build && npm run check-nav`

Expected: tests, TypeScript build, and navigation checks pass.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/api.ts apps/web/src/components/admin/AdminOpsPanel.tsx apps/web/src/components/admin/AdminOpsPanel.test.tsx apps/web/src/components/admin/SystemHealthPanel.tsx apps/web/src/components/admin/SystemHealthPanel.test.tsx apps/web/src/i18n/ru.ts apps/web/src/i18n/errors.ts
git commit -m "feat(web): add Royal system health and OTA controls"
```

### Task 10: Installer packaging and GitHub release workflow

**Files:**
- Create: `scripts/pack-installer.sh`
- Create: `scripts/release_pack.py`
- Create: `scripts/verify-artifact.py`
- Create: `tests/host/test_packaging.py`
- Create: `.github/workflows/release.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `scripts/verify.sh`

**Interfaces:**
- Produces: installer `.tar.gz`, release `.zip`, internal manifest signature, whole-ZIP `.sig`, `.sha256`, and release metadata `.json`.
- Produces: `./scripts/verify.sh host` and artifact verification command.
- Consumes: `ROBOPARK_SIGNING_KEY_FILE` locally or protected CI signing secret.

- [ ] **Step 1: Write failing deterministic package-content tests**

```python
def test_installer_contains_initial_release_public_key_and_offline_readme(built_installer):
    names = tar_names(built_installer)
    assert {"install.sh", "payload/robopark-release.zip", "keys/release-public-key.pem", "README-RU.txt"} <= names

def test_release_artifacts_verify_without_repository_checkout(built_artifacts, public_key):
    completed = run_verify_artifact(built_artifacts.zip, built_artifacts.signature, public_key)
    assert completed.returncode == 0
```

- [ ] **Step 2: Confirm packaging tests fail**

Run: `pytest tests/host/test_packaging.py -q`

Expected: installer packer and verifier are missing.

- [ ] **Step 3: Implement reproducible packaging and checksums**

Normalize archive ordering, uid/gid, permissions, and timestamps from `SOURCE_DATE_EPOCH`. Exclude `.git`, caches, node modules, venvs, runtime data, every non-example env file, and private keys. Refuse output inside the payload tree.

- [ ] **Step 4: Add host verification target**

```sh
run_host() {
  sh -n deploy/installer/install.sh deploy/installer/lib/*.sh deploy/tuna-http.sh
  PYTHONPATH=deploy/host uv run --directory apps/api --frozen --extra dev pytest ../../tests/host -q
}
```

Wire `host` into usage and the full `all` gate without executing privileged host operations.

- [ ] **Step 5: Add release workflow with protected signing secret**

Trigger on version tags, run the full verify gate, decode the signing key into an ephemeral mode-`0600` file, build artifacts, verify them with the committed public key, upload all assets, and delete the ephemeral file in an `always()` cleanup step. Do not publish when the signing secret is absent.

- [ ] **Step 6: Run packaging and host gates**

Run: `pytest tests/host/test_packaging.py -q && ./scripts/verify.sh host`

Expected: deterministic package tests and all host tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/pack-installer.sh scripts/pack-release.sh scripts/release_pack.py scripts/verify-artifact.py scripts/verify.sh tests/host/test_packaging.py .github/workflows/release.yml .github/workflows/ci.yml .gitignore
git commit -m "build: package signed installer and OTA releases"
```

### Task 11: End-to-end failure exercises and operator documentation

**Files:**
- Create: `tests/host/test_end_to_end_update.py`
- Create: `tests/host/fixtures/releases/dependency-change.json`
- Create: `tests/host/fixtures/releases/host-tools-change.json`
- Create: `deploy/INSTALL-ARMBIAN-RU.md`
- Modify: `deploy/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: completed installer, diagnostics, updater, API, UI, and packagers.
- Produces: repeatable simulated acceptance suite and exact target-host checklist.

- [ ] **Step 1: Add end-to-end scenario covering dependency and host-tool replacement**

```python
def test_release_changes_dependencies_units_and_updater_then_survives_reboot(e2e_host):
    e2e_host.install_initial("1.0.0")
    e2e_host.approve_release("1.1.0", fixture="host-tools-change")
    assert e2e_host.wait_job().state == "succeeded"
    e2e_host.reboot()
    assert e2e_host.current_version() == "1.1.0"
    assert e2e_host.service_active("robopark.service")
    assert e2e_host.service_active("robopark-tuna.service")
    assert e2e_host.command("robopark", "doctor").returncode == 0
```

Add scenarios for tampering, failed candidate tests, failed migrations, failed post-cutover health, Tuna outage, GitHub outage, low disk, and simulated power loss at every journal phase.

- [ ] **Step 2: Run the completed end-to-end acceptance scenario**

Run: `PYTHONPATH=deploy/host pytest tests/host/test_end_to_end_update.py -q`

Expected: all scenarios pass using the production installer, updater, diagnostic, and rollback entry points with fake host adapters.

- [ ] **Step 3: Write exact Russian installation and recovery guide**

Document:

```text
tar -xzf robopark-installer-0.2.0.tar.gz
cd robopark-installer-0.2.0
sudo ./install.sh
```

Include wizard inputs, stable Tuna requirement for production, `status/doctor/repair`, reboot verification, Royal OTA, GitHub token scope, backups, rollback states, log locations, and recovery when both releases fail. Never include example values shaped like real secrets.

- [ ] **Step 4: Run focused E2E and full repository verification**

Run: `PYTHONPATH=deploy/host pytest tests/host/test_end_to_end_update.py -q && ./scripts/verify.sh`

Expected: all API, web, host, Compose, and image gates pass.

- [ ] **Step 5: Build and independently verify final artifacts**

Run:

```bash
ROBOPARK_SIGNING_KEY_FILE="$PWD/.release-secrets/robopark-release-key.pem" scripts/pack-release.sh artifacts/robopark-release-0.2.0.zip
ROBOPARK_SIGNING_KEY_FILE="$PWD/.release-secrets/robopark-release-key.pem" scripts/pack-installer.sh artifacts/robopark-installer-0.2.0.tar.gz
python3 scripts/verify-artifact.py --public-key deploy/keys/release-public-key.pem artifacts/robopark-release-0.2.0.zip
sha256sum artifacts/robopark-installer-0.2.0.tar.gz artifacts/robopark-release-0.2.0.zip
```

Expected: artifact verification succeeds and both checksums are printed. The actual protected key path is supplied by the release operator and is never committed.

- [ ] **Step 6: Execute the target Armbian acceptance checklist**

On the target device: clean installation, local readiness, Tuna HTTPS, reboot autostart, Royal login, successful signed OTA, deliberately failing OTA with rollback, `doctor`, allowlisted `repair`, and diagnostic ZIP inspection. Record host architecture, OS release, duration, installed version, and results without secrets.

- [ ] **Step 7: Commit**

```bash
git add tests/host/test_end_to_end_update.py tests/host/fixtures/releases deploy/INSTALL-ARMBIAN-RU.md deploy/README.md README.md
git commit -m "test(deploy): verify Armbian installation and OTA recovery"
```

## Final release gate

- [ ] `./scripts/verify.sh` exits 0 from a clean dependency install.
- [ ] `git diff --check` exits 0.
- [ ] The installer and release artifacts verify with the committed public key.
- [ ] A secret-pattern scan finds no configured Tuna, GitHub, Royal, Tracker, Emergency, or signing secrets in tracked files or artifacts.
- [ ] The target Armbian checklist records a healthy reboot, successful OTA, failed-OTA rollback, and clean diagnostic bundle.
- [ ] `git status --short` contains only intentionally untracked local caches or produced artifacts documented in the handoff.
