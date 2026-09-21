# Versioning, LTS Support and Technical-Debt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ввести единую SemVer-модель, каналы релизов, 24-месячную LTS-поддержку, проверяемый граф обновлений и управляемый жизненный цикл технического долга без принудительных обновлений.

**Architecture:** Существующие `VERSION`, release manifest, host updater, Alembic и source-bound evidence остаются основой. Новые машинные контракты добавляются в отдельные небольшие модули и JSON-политики; API и web только отображают вычисленное состояние. Каждый этап совместим с текущим релизным форматом до явного повышения format version и имеет отдельный review-gate.

**Tech Stack:** Python 3.12/FastAPI/SQLAlchemy/Alembic, host Python 3.10+, PostgreSQL 17, React/TypeScript/Vite/PWA, GitHub Actions, POSIX shell.

**Spec:** `docs/superpowers/specs/2026-09-21-versioning-support-design.md`

## Global Constraints

- Каноническая пользовательская версия — SemVer из корневого `VERSION`.
- Целевая последовательность — `0.2.0-rc.1`, затем неизменяемый проверенный состав `1.0.0 LTS`.
- `1.0.0` поддерживается 24 месяца; обычный stable — 6 месяцев.
- Старая установка продолжает работать после окончания поддержки и не обновляется принудительно.
- Каналы: `stable`, `rc`, `manual`; смена канала сама не запускает установку.
- Подпись OTA и её текущая семантика не изменяются этой работой.
- `fast` не запускает Docker, PostgreSQL, load, soak, installer/VM или OTA.
- Любой release evidence привязывается к точному Git SHA и digest артефакта.
- Исторический PASS не считается PASS текущего дерева.
- Тяжёлые проверки выполняются только после отдельного разрешения пользователя.

## Review Focus

- Очень старая версия должна получить bridge-path или безопасный отказ до изменения данных — Task 4 проверяет оба исхода.
- Просроченная LTS должна продолжать работать и только показывать предупреждение — Task 5 фиксирует контракт API/UI.
- Prerelease не должен попасть на stable-канал, а stable не должен пересобираться при продвижении — Tasks 3 и 9 проверяют канал и digest.
- Прерванное обновление с необратимой миграцией должно восстановить snapshot и прежний release — Task 4 добавляет recovery-контракт.
- Cleanup не должен удалить current/previous/recovery или активный journal — Task 6 проверяет защищённый набор при давлении на диск.

---

### Task 1: Каноническая версия и build identity

**Files:**
- Create: `scripts/robopark_version.py`
- Create: `scripts/set-release-version.py`
- Modify: `scripts/check-release-version.py`
- Modify: `apps/api/src/robopark_api/services/ops/context.py`
- Test: `tests/host/test_release_version.py`
- Test: `tests/host/test_packaging.py`

**Interfaces:**
- Produces: `ReleaseVersion.parse(value: str) -> ReleaseVersion`, `ReleaseVersion.stage -> Literal["stable", "rc"]`.
- Produces: `BuildIdentity(version: str, git_sha: str, migration_head: str, build_id: str)`.
- Produces CLI: `python scripts/set-release-version.py 0.2.0-rc.1 --check|--write`.

- [ ] **Step 1: Write failing SemVer and synchronization tests**

```python
def test_rc_channel_and_stable_channel():
    assert ReleaseVersion.parse("0.2.0-rc.1").stage == "rc"
    assert ReleaseVersion.parse("1.0.0").stage == "stable"

def test_set_version_updates_every_shipped_source(tmp_path, repository_copy):
    run_set_version(repository_copy, "0.2.0-rc.1")
    assert check(repository_copy) == "0.2.0-rc.1"
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_version.py tests/host/test_packaging.py -k 'version or channel'`

Expected: FAIL because `robopark_version.py` and the synchronization CLI do not exist.

- [ ] **Step 3: Implement immutable version/build types and the synchronization CLI**

```python
@dataclass(frozen=True)
class ReleaseVersion:
    raw: str
    precedence: tuple
    stage: Literal["stable", "rc"]

    @classmethod
    def parse(cls, value: str) -> "ReleaseVersion":
        precedence = parse_semver(value)
        return cls(value, precedence, "rc" if "-" in value else "stable")
```

The write command must update `VERSION`, API/web manifests and locks, plus
`APP_VERSION`, using structured TOML/JSON/AST-safe replacements. `--check` performs
no writes and reuses `check-release-version.py`.

- [ ] **Step 4: Run focused tests and static checks**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_version.py tests/host/test_packaging.py -k 'version or channel'`

Run: `uv run --project apps/api --frozen --extra dev ruff check scripts/robopark_version.py scripts/set-release-version.py scripts/check-release-version.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/robopark_version.py scripts/set-release-version.py scripts/check-release-version.py apps/api/src/robopark_api/services/ops/context.py tests/host/test_release_version.py tests/host/test_packaging.py
git commit -m "feat(release): centralize version identity"
```

### Task 2: Support policy and manifest schema v3

**Files:**
- Create: `deploy/support-policy.json`
- Create: `scripts/release_policy.py`
- Modify: `deploy/release-metadata.json`
- Modify: `scripts/release_pack.py`
- Modify: `scripts/verify-artifact.py`
- Modify: `apps/api/src/robopark_api/services/ops/archives.py`
- Modify: `deploy/host/robopark_host/release.py`
- Test: `tests/host/test_release_metadata.py`
- Test: `tests/host/test_packaging.py`

**Interfaces:**
- Consumes: `ReleaseVersion` from Task 1.
- Produces: `SupportPolicy.from_file(path) -> SupportPolicy`.
- Produces manifest v3 fields: `eligible_channels`, `built_at`, `support_class`, `support_months`, `build_id`, `content_digest`, `upgrade_policy`.
- Backward read support remains for manifest v2; new artifacts are v3 only.

- [ ] **Step 1: Add failing policy and manifest tests**

```python
def test_lts_policy_is_24_months(policy):
    release = policy.release("1.0.0", lts=True)
    assert release.support_class == "lts"
    assert release.support_months == 24

def test_stable_manifest_rejects_prerelease_channel(manifest_v3):
    manifest_v3.update(app_version="1.0.0-rc.1", eligible_channels=["stable"])
    with pytest.raises(ValueError, match="invalid_release_policy"):
        validate_policy_metadata(manifest_v3)
```

- [ ] **Step 2: Confirm tests fail on the current v2 contract**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_metadata.py tests/host/test_packaging.py -k 'policy or manifest_v3 or support_months'`

- [ ] **Step 3: Implement the policy schema and v2/v3 readers**

`deploy/support-policy.json` must contain exact defaults:

```json
{
  "schema": 1,
  "stable_support_months": 6,
  "lts_support_months": 24,
  "channels": ["stable", "rc", "manual"],
  "lts_lines": ["1.0"]
}
```

`content_digest` is computed from the canonical ordered source-file entries, excluding
the generated outer manifest. The final archive SHA-256 is written only to detached
metadata/evidence after the archive exists. `build_id` is deterministic from
`version + git_sha + migration_head`. A prerelease may only declare `rc`; a stable
SemVer may declare `rc`, `stable`, or both. The active publication channel is external
catalog state and is not rewritten inside the artifact.

- [ ] **Step 4: Verify manifests, old-reader compatibility and tamper rejection**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_metadata.py tests/host/test_packaging.py`

Run: `uv run --project apps/api --frozen --extra dev ruff check scripts/release_policy.py scripts/release_pack.py scripts/verify-artifact.py deploy/host/robopark_host/release.py`

- [ ] **Step 5: Commit**

```bash
git add deploy/support-policy.json deploy/release-metadata.json scripts/release_policy.py scripts/release_pack.py scripts/verify-artifact.py apps/api/src/robopark_api/services/ops/archives.py deploy/host/robopark_host/release.py tests/host/test_release_metadata.py tests/host/test_packaging.py
git commit -m "feat(release): add support-aware manifest v3"
```

### Task 3: Release channels and immutable promotion

**Files:**
- Modify: `deploy/host/robopark_host/github_releases.py`
- Modify: `deploy/host/robopark_host/commands.py`
- Modify: `deploy/host.env.example`
- Modify: `deploy/installer/lib/configure.py`
- Create: `tests/host/test_release_channels.py`
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes manifest v3 from Task 2.
- Produces host setting `ROBOPARK_UPDATE_CHANNEL=stable|rc|manual`.
- Produces `promote_release(source_digest: str, target_channel: str)`, which changes external catalog state and rejects a target absent from `eligible_channels`.

- [ ] **Step 1: Write failing channel-selection and promotion tests**

```python
def test_stable_channel_ignores_rc(releases, config):
    config.channel = "stable"
    assert select_release(releases.rc("1.1.0-rc.1"), config) is None

def test_promotion_requires_same_digest(candidate):
    with pytest.raises(ReleaseError, match="promotion_digest_mismatch"):
        promote_release(candidate, stable_digest="0" * 64)
```

- [ ] **Step 2: Run and observe RED**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_channels.py`

- [ ] **Step 3: Implement channel parsing, discovery and digest-preserving promotion**

`manual` disables GitHub discovery but keeps local upload. `rc` sees stable candidates
and prereleases; `stable` sees only stable SemVer explicitly promoted in the external
catalog. Promotion never mutates the signed manifest and never calls the packer.

- [ ] **Step 4: Test workflow ordering and host behavior**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_channels.py tests/host/test_github_releases.py tests/host/test_github_review.py`

Run: `python3 -c 'import yaml; yaml.safe_load(open(".github/workflows/release.yml"))'`

- [ ] **Step 5: Commit**

```bash
git add deploy/host/robopark_host/github_releases.py deploy/host/robopark_host/commands.py deploy/host.env.example deploy/installer/lib/configure.py tests/host/test_release_channels.py .github/workflows/release.yml
git commit -m "feat(updater): add stable rc and manual channels"
```

### Task 4: Compatibility graph and bridge releases

**Files:**
- Create: `deploy/migration-policy.json`
- Create: `scripts/migration_graph.py`
- Modify: `scripts/release_pack.py`
- Modify: `deploy/host/robopark_host/release.py`
- Modify: `deploy/host/robopark_host/updater.py`
- Test: `tests/host/test_migration_graph.py`
- Modify: `tests/host/test_updater.py`
- Modify: `tests/host/test_end_to_end_update.py`

**Interfaces:**
- Produces: `UpgradePlan(releases: tuple[str, ...], reversible: bool, recovery: Literal["rollback", "snapshot"])`.
- Produces: `plan_upgrade(current_version, current_head, target_manifest, policy) -> UpgradePlan`.
- Replaces newly written manual `migration_compatibility.from_heads`; v2 manifests remain readable.

- [ ] **Step 1: Write failing direct, bridge and rejection tests**

```python
def test_old_release_requires_bridge(policy):
    plan = plan_upgrade("0.1.18", "0026_global_inventory_workflows", target, policy)
    assert plan.releases == ("0.1.45", "0.2.0-rc.1")

def test_unknown_schema_is_rejected_before_snapshot(policy):
    with pytest.raises(ReleaseError, match="migration_incompatible"):
        plan_upgrade("0.1.9", "unknown", target, policy)
```

- [ ] **Step 2: Confirm RED**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_migration_graph.py tests/host/test_updater.py -k 'bridge or unknown_schema'`

- [ ] **Step 3: Implement deterministic graph validation and update planning**

`deploy/migration-policy.json` records edges with `from_head`, `to_head`,
`min_version`, `bridge_version`, `reversible` and `recovery`. Cycles, missing
revisions, duplicate edges and a bridge not newer than the source are rejected by
the packer before artifact creation.

- [ ] **Step 4: Add interruption recovery for irreversible plans**

The updater journal persists the entire `UpgradePlan` before mutation. Recovery
must restore the PostgreSQL snapshot and previous release when `recovery=snapshot`.

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_migration_graph.py tests/host/test_updater.py tests/host/test_end_to_end_update.py -k 'bridge or migration or snapshot or interruption'`

- [ ] **Step 5: Commit**

```bash
git add deploy/migration-policy.json scripts/migration_graph.py scripts/release_pack.py deploy/host/robopark_host/release.py deploy/host/robopark_host/updater.py tests/host/test_migration_graph.py tests/host/test_updater.py tests/host/test_end_to_end_update.py
git commit -m "feat(updater): plan compatible bridge upgrades"
```

### Task 5: Support status API and administration UI

**Files:**
- Create: `apps/api/src/robopark_api/services/release_status.py`
- Modify: `apps/api/src/robopark_api/ops_schemas.py`
- Modify: `apps/api/src/robopark_api/routers/admin_ops.py`
- Modify: `apps/api/src/robopark_api/services/ops/host_bridge.py`
- Test: `apps/api/tests/test_admin_ops.py`
- Create: `apps/web/src/components/admin/SystemVersionPanel.tsx`
- Create: `apps/web/src/components/admin/SystemVersionPanel.test.tsx`
- Modify: `apps/web/src/components/admin/AdminOpsPanel.tsx`
- Modify: `apps/web/src/api.ts`

**Interfaces:**
- Produces API `GET /api/admin/ops/release-status` for admin/royal.
- Produces `ReleaseStatusOut` with version, build identity, channel, catalog `released_at`/`supported_until`, support state, DB head, installer version, available update, bridges, disk and cleanup status.

- [ ] **Step 1: Write failing API support-state tests**

```python
def test_expired_lts_is_warning_not_block(client, royal_headers):
    response = client.get("/api/admin/ops/release-status", headers=royal_headers)
    assert response.status_code == 200
    assert response.json()["support_status"] == "expired"
    assert response.json()["operations_blocked"] is False
```

- [ ] **Step 2: Write failing UI state tests**

```tsx
it('shows an expired support warning without an update action lock', async () => {
  render(<SystemVersionPanel value={expiredFixture} />)
  expect(screen.getByText('Поддержка завершена')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Проверить обновления' })).toBeEnabled()
})
```

- [ ] **Step 3: Implement service, schema, endpoint and compact panel**

Support state is one of `supported|ending|expired|unknown`; `ending` starts 30 days
before `supported_until`. Missing host metadata yields `unknown`, not a fabricated
date. Ordinary users receive 403.

- [ ] **Step 4: Run focused API/web tests and type checks**

Run: `uv run --project apps/api --frozen --extra dev pytest -q apps/api/tests/test_admin_ops.py`

Run: `npm --prefix apps/web test -- SystemVersionPanel.test.tsx AdminOpsPanel.test.tsx`

Run: `npm --prefix apps/web exec tsc -b --pretty false`

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/robopark_api/services/release_status.py apps/api/src/robopark_api/ops_schemas.py apps/api/src/robopark_api/routers/admin_ops.py apps/api/src/robopark_api/services/ops/host_bridge.py apps/api/tests/test_admin_ops.py apps/web/src/components/admin/SystemVersionPanel.tsx apps/web/src/components/admin/SystemVersionPanel.test.tsx apps/web/src/components/admin/AdminOpsPanel.tsx apps/web/src/api.ts
git commit -m "feat(admin): show release and support status"
```

### Task 6: Safe release retention and cleanup evidence

**Files:**
- Modify: `deploy/host/robopark_host/image_retention.py`
- Modify: `deploy/host/robopark_host/retention.py`
- Modify: `deploy/host/robopark_host/operational_state.py`
- Modify: `tests/host/test_image_retention.py`
- Modify: `tests/host/test_update_recovery.py`
- Create: `tests/host/test_release_retention.py`

**Interfaces:**
- Produces `RetentionReport(kept, removed, reclaimed_bytes, completed_at, errors)`.
- Protected roots derive from current, previous, recovery and every active journal.

- [ ] **Step 1: Add failing protected-set and bounded-cleanup tests**

```python
def test_cleanup_preserves_every_referenced_release(host):
    protected = seed_current_previous_recovery_and_active_journal(host)
    report = cleanup_releases(host.paths, max_items=20)
    assert protected <= existing_release_names(host.paths)
    assert report.removed
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_retention.py tests/host/test_image_retention.py`

- [ ] **Step 3: Implement one protected-set calculation for archives, trees and images**

Cleanup is bounded by both item count and elapsed seconds. It writes the report
atomically to `ops/public/retention-status.json`. A cleanup error is recorded and
does not change current/previous links or trigger rollback.

- [ ] **Step 4: Test disk-pressure and interrupted-journal cases**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_retention.py tests/host/test_image_retention.py tests/host/test_update_recovery.py -k 'retention or cleanup or preserve'`

- [ ] **Step 5: Commit**

```bash
git add deploy/host/robopark_host/image_retention.py deploy/host/robopark_host/retention.py deploy/host/robopark_host/operational_state.py tests/host/test_image_retention.py tests/host/test_update_recovery.py tests/host/test_release_retention.py
git commit -m "fix(host): bound release retention safely"
```

### Task 7: Technical-debt registry and architecture contracts

**Files:**
- Create: `docs/technical-debt/registry.yaml`
- Create: `docs/architecture/module-contracts.md`
- Create: `scripts/check-tech-debt.py`
- Create: `scripts/check-module-boundaries.py`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/host/test_repository_governance.py`

**Interfaces:**
- Registry entry fields: `id`, `module`, `impact`, `risk`, `owner`, `target_version`, `acceptance`, `status`, `evidence`.
- Boundary checker consumes an explicit allow-list from `module-contracts.md` fenced YAML block.

- [ ] **Step 1: Add failing governance tests**

```python
def test_debt_entries_have_owner_target_and_acceptance():
    entries = load_registry(ROOT / "docs/technical-debt/registry.yaml")
    assert all(item.owner and item.target_version and item.acceptance for item in entries)

def test_api_does_not_import_host_implementation():
    assert boundary_violations(ROOT) == []
```

- [ ] **Step 2: Run tests and confirm missing registry/checkers**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_repository_governance.py`

- [ ] **Step 3: Implement schema validation and initial evidence-backed registry**

Initial entries must only describe confirmed open debt from current documentation;
each `acceptance` is a concrete command or observable result. Closed entries require
an evidence path and exact closing commit.

- [ ] **Step 4: Add both checkers to fast CI without heavy work**

Run: `python3 scripts/check-tech-debt.py && python3 scripts/check-module-boundaries.py`

Run: `python3 -c 'import yaml; yaml.safe_load(open(".github/workflows/ci.yml"))'`

- [ ] **Step 5: Commit**

```bash
git add docs/technical-debt/registry.yaml docs/architecture/module-contracts.md scripts/check-tech-debt.py scripts/check-module-boundaries.py .github/workflows/ci.yml tests/host/test_repository_governance.py
git commit -m "chore(architecture): track debt and module contracts"
```

### Task 8: Release notes, compatibility matrix and support documentation

**Files:**
- Create: `CHANGELOG.md`
- Create: `docs/releases/0.2.0-rc.1.md`
- Create: `docs/releases/compatibility.json`
- Create: `scripts/generate-release-notes.py`
- Modify: `deploy/README.md`
- Modify: `deploy/INSTALL-ARMBIAN-RU.md`
- Test: `tests/host/test_release_documentation.py`

**Interfaces:**
- Produces `compatibility.json` from support policy, migration graph and release metadata.
- Release note schema includes changes, upgrade path, recovery, known issues, support dates and evidence SHA.

- [ ] **Step 1: Add failing source-bound documentation tests**

```python
def test_release_note_matches_version_and_manifest():
    note = load_release_note("0.2.0-rc.1")
    assert note.version == read_version(ROOT)
    assert note.migration_head == load_metadata(ROOT)["migration_head"]
```

- [ ] **Step 2: Confirm RED before files and generator exist**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_documentation.py`

- [ ] **Step 3: Implement deterministic documentation generation**

The generator reads repository metadata only and refuses dirty/mismatched version
sources in `--check` mode. Human prose stays in the release note; generated fields
are delimited and checked without overwriting prose.

- [ ] **Step 4: Run documentation and link checks**

Run: `python3 scripts/generate-release-notes.py --check`

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_documentation.py`

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md docs/releases/0.2.0-rc.1.md docs/releases/compatibility.json scripts/generate-release-notes.py deploy/README.md deploy/INSTALL-ARMBIAN-RU.md tests/host/test_release_documentation.py
git commit -m "docs(release): add source-bound support documentation"
```

### Task 9: Source-bound release gates and immutable promotion evidence

**Files:**
- Modify: `scripts/release_acceptance.py`
- Modify: `scripts/verify.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`
- Create: `.github/workflows/promote-release.yml`
- Modify: `docs/product-completion/acceptance-matrix.md`
- Test: `tests/host/test_release_acceptance.py`
- Test: `tests/host/test_github_review.py`

**Interfaces:**
- Acceptance schema v2 binds `source_sha`, `artifact_sha256`, `manifest_digest`, command, started/completed timestamps and expiry.
- Promotion workflow accepts an existing release asset digest and target channel; it never invokes `release_pack.py`.

- [ ] **Step 1: Add failing expiry, digest and no-rebuild tests**

```python
def test_expired_or_other_artifact_evidence_is_rejected(evidence):
    evidence["artifact_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="release_acceptance_failed"):
        validate_acceptance(ROOT, evidence, expected_artifact_sha256="1" * 64)

def test_promotion_workflow_never_runs_packer(workflow):
    assert "release_pack.py" not in workflow_text(workflow)
```

- [ ] **Step 2: Confirm RED**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_acceptance.py tests/host/test_github_review.py -k 'expiry or artifact or promotion'`

- [ ] **Step 3: Implement evidence v2 and promotion workflow**

`release.yml` builds and publishes `rc`; `promote-release.yml` verifies the existing
asset and evidence, then updates channel metadata. Missing load/soak/platform evidence
for stable or LTS fails before signing or publication.

- [ ] **Step 4: Verify workflow order and shell syntax**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_acceptance.py tests/host/test_github_review.py`

Run: `sh -n scripts/verify.sh && python3 -c 'import yaml; [yaml.safe_load(open(p)) for p in [".github/workflows/ci.yml", ".github/workflows/release.yml", ".github/workflows/promote-release.yml"]]'`

- [ ] **Step 5: Commit**

```bash
git add scripts/release_acceptance.py scripts/verify.sh .github/workflows/ci.yml .github/workflows/release.yml .github/workflows/promote-release.yml docs/product-completion/acceptance-matrix.md tests/host/test_release_acceptance.py tests/host/test_github_review.py
git commit -m "ci(release): bind gates to immutable artifacts"
```

### Task 10: Prepare `0.2.0-rc.1` without publishing it

**Files:**
- Modify through CLI: `VERSION`, `apps/api/pyproject.toml`, `apps/api/uv.lock`, `apps/web/package.json`, `apps/web/package-lock.json`, `apps/api/src/robopark_api/services/ops/context.py`
- Modify: `deploy/release-metadata.json`
- Modify: `docs/releases/0.2.0-rc.1.md`
- Modify: `docs/product-completion/PROGRESS.md`
- Test: existing version, packaging, migration and release-contract tests.

**Interfaces:**
- Consumes every contract from Tasks 1–9.
- Produces a source tree ready for permission-gated full release verification; it does not create, sign, install or publish an artifact.

- [ ] **Step 1: Set the version using the canonical CLI**

Run: `python3 scripts/set-release-version.py 0.2.0-rc.1 --write`

Expected: only the six declared version sources change.

- [ ] **Step 2: Regenerate checked-in release documentation**

Run: `python3 scripts/generate-release-notes.py --write`

Expected: generated compatibility/support blocks match manifest and migration policy.

- [ ] **Step 3: Run the permitted short gate**

Run: `./scripts/verify.sh fast`

Run: `python3 scripts/check-release-version.py`

Run: `python3 scripts/generate-release-notes.py --check`

Expected: PASS without Docker, load, soak, installer, OTA or signing.

- [ ] **Step 4: Confirm full release gates remain explicitly pending**

Run: `python3 scripts/release_acceptance.py --validate docs/product-completion/release-evidence.json`

Expected: fail-closed until full API/web, PostgreSQL, Docker, platform install,
upgrade/recovery, 200-user load and soak evidence are produced for this exact SHA and
artifact digest.

- [ ] **Step 5: Commit the release-candidate source state**

```bash
git add VERSION apps/api/pyproject.toml apps/api/uv.lock apps/web/package.json apps/web/package-lock.json apps/api/src/robopark_api/services/ops/context.py deploy/release-metadata.json docs/releases/0.2.0-rc.1.md docs/product-completion/PROGRESS.md
git commit -m "chore(release): prepare 0.2.0-rc.1 sources"
```

### Task 11: Final short verification and independent review

**Files:**
- Modify only defects confirmed by review.
- Create: `docs/reviews/2026-09-21-versioning-support-review.md`

**Interfaces:**
- Produces a review verdict for the exact final SHA.
- Does not mark long release gates PASS and does not create OTA artifacts.

- [ ] **Step 1: Run the combined targeted contract set**

Run: `uv run --project apps/api --frozen --extra dev pytest -q tests/host/test_release_version.py tests/host/test_release_metadata.py tests/host/test_release_channels.py tests/host/test_migration_graph.py tests/host/test_release_retention.py tests/host/test_repository_governance.py tests/host/test_release_documentation.py tests/host/test_release_acceptance.py`

Run: `npm --prefix apps/web test -- SystemVersionPanel.test.tsx AdminOpsPanel.test.tsx`

- [ ] **Step 2: Run static checks only for changed areas**

Run: `uv run --project apps/api --frozen --extra dev ruff check scripts deploy/host/robopark_host apps/api/src/robopark_api/services/release_status.py apps/api/src/robopark_api/routers/admin_ops.py`

Run: `npm --prefix apps/web run lint && npm --prefix apps/web run check-nav`

Run: `git diff --check 93432fb..HEAD`

- [ ] **Step 3: Obtain an independent whole-branch review**

The reviewer checks the spec, every commit after `93432fb`, upgrade/recovery failure
paths, support-state behavior, release workflow order, artifact immutability and the
explicit exclusion of signature semantics.

- [ ] **Step 4: Fix only confirmed findings and repeat affected short tests**

Each correction receives its own commit and reviewer re-check. Do not broaden into a
full gate without user permission.

- [ ] **Step 5: Record the final truthful status**

The review document lists short checks as PASS and lists PostgreSQL/Docker/full
suites/platform installs/load/soak/OTA/signing/publication as UNVERIFIED. No archive
is created by this plan.
