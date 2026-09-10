# Technical Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound Robopark-owned OTA, diagnostics, staging, temporary, release, and Docker build residue without deleting business data or delaying service availability.

**Architecture:** Extend the existing fail-closed host retention subsystem instead of adding another cleaner. Exact-name ownership rules protect file and image deletion; release retention records three known-good releases, while a pressure-aware pass bounds unused build cache on the dedicated Robopark appliance after terminal operations or scheduled doctor runs.

**Tech Stack:** Python 3 standard library, systemd host tooling, Docker CLI boundary, pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-inventory-usability-and-technical-cleanup-design.md`

## Global Constraints

- Never traverse or delete API database data, reports, inventory photos, tickets, audit, settings, or arbitrary Docker images, containers, networks, or volumes.
- Protect the active command, updater journal, current release, and rollback material.
- Keep the current release plus two most recent successful releases.
- Cleanup failure never rolls back a healthy update and remains retryable.
- Each cleanup pass has a fixed file/command/time budget.

---

### Task 1: Keep three successful releases and their rollback material

**Files:**
- Modify: `deploy/host/robopark_host/updater.py`
- Test: `tests/host/test_update_recovery.py`
- Test: `tests/host/test_updater.py`

**Interfaces:**
- Consumes: root-owned `state/successful-releases/<release>.json` receipts and `current`/`previous` symlinks.
- Produces: `_retained_successful_releases(paths, limit=3): set[str]` used by `_retention` and image retention.

- [ ] **Step 1: Write a failing four-release retention test**

Create four receipt-backed release directories ordered by receipt modification time, set current and previous, execute terminal retention, and assert current plus the two newest other successful releases remain while the oldest directory, compose file, receipt, and rollback directory are removed. The break caught is deleting the second rollback candidate or retaining every historical release.

```python
assert {path.name for path in host.paths.releases.iterdir()} == {current, prior_one, prior_two}
assert not (host.paths.releases / oldest).exists()
```

- [ ] **Step 2: Run the test and verify RED**

Run: `pytest -q tests/host/test_update_recovery.py -k retention`.

Expected: FAIL because `_retention` currently keeps only current and previous.

- [ ] **Step 3: Implement receipt-based three-release retention**

Read only regular, root-owned, single-link receipt files whose stem matches the existing release-name regex. Sort by `st_mtime_ns` descending, seed the set with current and previous, and add newest successful releases until the set size is three. Use the resulting set for release directories, compose configs, rollbacks, and image ownership protection.

```python
def _retained_successful_releases(paths, limit=3):
    keep = {_release_target(paths, paths.current).name}
    if paths.previous.is_symlink():
        keep.add(_release_target(paths, paths.previous).name)
    # Admit only validated successful-release receipts until len(keep) == limit.
    return keep
```

- [ ] **Step 4: Run related host tests and verify GREEN**

Run: `pytest -q tests/host/test_update_recovery.py tests/host/test_updater.py -k 'retention or prune'`.

Expected: all selected tests PASS.

- [ ] **Step 5: Commit release retention**

```bash
git add deploy/host/robopark_host/updater.py tests/host/test_update_recovery.py tests/host/test_updater.py
git commit -m "fix(ota): retain three healthy releases"
```

### Task 2: Remove exact-name temporary operation residue

**Files:**
- Modify: `deploy/host/robopark_host/retention.py`
- Test: `tests/host/test_retention.py`
- Test: `tests/host/test_repair.py`

**Interfaces:**
- Consumes: protected command identities from `_protected(paths)`.
- Produces: `_cleanup_operation_residue(paths, identities, now): dict[str, int]` with bounded counts for staging, interrupted atomic files, and expired diagnostic work.

- [ ] **Step 1: Write failing ownership-boundary tests**

Create expired owned names under known Robopark directories, fresh equivalents, active-job equivalents, symlinks, foreign names, and representative business directories. Assert only expired exact-name technical entries are removed and the result reports counts. The break caught is either leaked technical residue or overly broad deletion.

```python
result = retain_artifacts(host_paths, now=clock)
assert result['temporary_deleted'] == 2
assert not expired_owned.exists()
assert active_owned.exists()
assert foreign_name.exists()
assert business_photo.exists()
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `pytest -q tests/host/test_retention.py tests/host/test_repair.py -k 'cleanup or staging or temporary'`.

Expected: FAIL because interrupted exact-name temporary artifacts are not counted and reclaimed as one retention phase.

- [ ] **Step 3: Implement bounded residue cleanup**

Extend `_locations` or a dedicated exact-directory scanner only for documented Robopark-owned temporary name patterns. Reuse descriptor-pinned deletion, `MAX_ENTRIES`, UID/link/type checks, `STAGING_TTL`, and protected identities. Add `temporary_deleted` to the persisted `retention.json`; never call `glob` recursively and never enter application data directories.

- [ ] **Step 4: Run retention tests and verify GREEN**

Run: `pytest -q tests/host/test_retention.py tests/host/test_repair.py`.

Expected: all tests PASS.

- [ ] **Step 5: Commit residue cleanup**

```bash
git add deploy/host/robopark_host/retention.py tests/host/test_retention.py tests/host/test_repair.py
git commit -m "feat(ops): clean technical operation residue"
```

### Task 3: Bound unused Docker builder cache cleanup

**Files:**
- Modify: `deploy/host/robopark_host/image_retention.py`
- Modify: `deploy/host/robopark_host/cli.py`
- Test: `tests/host/test_image_retention.py`
- Test: `tests/host/test_retention.py`

**Interfaces:**
- Consumes: existing image ownership receipts and `SystemRunner.run(argv, timeout=...)`.
- Produces: `cleanup_builder_cache(paths, runner): {blocked: bool, attempted: bool}` issuing one age/storage-bounded Docker builder prune command only under disk pressure, called after image cleanup in scheduled maintenance and terminal update cleanup.

- [ ] **Step 1: Write failing command-boundary tests**

Assert a healthy-space pass issues no builder command. Under the existing disk-pressure signal, assert the cleaner issues at most one build-cache command, includes `until=168h` and `--keep-storage=2GB`, uses a short timeout, treats timeout/failure as blocked, and never issues `docker system prune`, image prune, container prune, network prune, or volume prune. The break caught is routine expensive cleanup, unsafe resource deletion, or unbounded maintenance.

```python
assert command == [
    'docker', 'builder', 'prune', '-f',
    '--filter', 'until=168h',
    '--keep-storage', '2GB',
]
assert timeout <= 30
```

- [ ] **Step 2: Run the test and verify RED**

Run: `pytest -q tests/host/test_image_retention.py -k builder`.

Expected: FAIL because no builder-cache cleanup contract exists.

- [ ] **Step 3: Add ownership labels and bounded cleanup**

Use `shutil.disk_usage(paths.var)` and the existing storage-pressure threshold to skip ordinary passes. Add a single age- and storage-bounded prune call with a 30-second timeout and persist only booleans, never command output. This host is a dedicated Robopark appliance, but the command still targets unused builder cache only and never images, containers, networks, or volumes.

- [ ] **Step 4: Wire scheduled and terminal execution**

Invoke builder cleanup after existing exact image cleanup in `_doctor_handler` and `_retain_after_terminal_update`. Keep update success independent: exceptions are suppressed only at this post-terminal boundary and recorded for the next cycle.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `pytest -q tests/host/test_image_retention.py tests/host/test_retention.py`.

Expected: all tests PASS and command count remains within the existing cleanup budget.

- [ ] **Step 6: Commit Docker cache cleanup**

```bash
git add deploy/host/robopark_host/image_retention.py deploy/host/robopark_host/cli.py tests/host/test_image_retention.py tests/host/test_retention.py
git commit -m "feat(ops): bound robopark build cache cleanup"
```

### Task 4: Cleanup regression verification

**Files:**
- Verify only; modify failures only within files listed by Tasks 1–3.

**Interfaces:**
- Consumes: completed host retention behavior.
- Produces: fresh verification evidence.

- [ ] **Step 1: Run the host retention suite**

Run: `pytest -q tests/host/test_retention.py tests/host/test_restore_retention.py tests/host/test_image_retention.py tests/host/test_update_recovery.py tests/host/test_updater.py tests/host/test_repair.py`.

Expected: all tests PASS.

- [ ] **Step 2: Run packaging boundary tests**

Run: `pytest -q tests/host/test_packaging.py tests/host/test_end_to_end_update.py`.

Expected: all tests PASS.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff --check && git status --short`.

Expected: no whitespace errors and only planned files are modified.
