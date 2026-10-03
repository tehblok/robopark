"""Exact owner-approved cleanup of Robopark's private BuildKit cache."""

import json
from uuid import uuid4

import pytest
from robopark_host.state import atomic_write_json

BUILDER = "robopark-buildkit-" + "a" * 32


def test_owned_cache_preview_accepts_203_records_without_pruning(host_paths):
    from robopark_host.builder_cleanup import preview_owned_builder_cache
    _receipt(host_paths)
    rows = "\n".join(json.dumps({"ID": f"cache{i}", "Size": "8.192kB", "Shared": False,
        "Reclaimable": True, "Mutable": False, "Type": "regular", "Description": "build context " + "a" * 500}) for i in range(203))
    runner = BuilderRunner(rows)
    result = preview_owned_builder_cache(host_paths, runner)
    assert result["blocked"] is False
    assert result["other_candidates"] == 202
    assert not any("prune" in argv for argv in runner.calls)


def _receipt(paths):
    atomic_write_json(paths.state / "buildkit-builder.json", {
        "schema": 1, "name": BUILDER, "driver": "docker-container",
    })


def _rows(*values):
    return "\n".join(json.dumps({
        "ID": identity, "Size": size, "Reclaimable": reclaimable,
        "Shared": shared, "Mutable": mutable, "Type": kind,
    }) for identity, size, reclaimable, shared, mutable, kind in values)


class BuilderRunner:
    def __init__(self, rows, *, change_after_preview=False, fail_prune=False):
        self.rows = rows
        self.calls = []
        self.change_after_preview = change_after_preview
        self.fail_prune = fail_prune
        self.pruned = False

    def run(self, argv, *, timeout, capture=False):
        self.calls.append(tuple(argv))
        assert 0 < timeout <= 30
        assert capture
        if argv == ["docker", "buildx", "inspect", BUILDER]:
            return f"Name: {BUILDER}\nDriver: docker-container\n"
        if argv == ["docker", "inspect", "--type", "container", "--format",
                    "{{json .Config.Env}}", "buildx_buildkit_" + BUILDER + "0"]:
            return json.dumps(["ROBOPARK_BUILDER_OWNER=" + BUILDER])
        if argv == ["docker", "buildx", "du", "--builder", BUILDER, "--format=json"]:
            if self.pruned:
                return ""
            if self.change_after_preview and len([call for call in self.calls if call[2] == "du"]) > 1:
                return self.rows.replace('"Size": 8192', '"Size": 8193')
            return self.rows
        if argv == ["docker", "buildx", "prune", "--builder", BUILDER, "--filter", "id=private", "--force"]:
            if self.fail_prune:
                raise RuntimeError("private Docker error text")
            self.pruned = True
            return ""
        raise AssertionError(argv)


def test_preview_requires_owned_receipt_and_selects_only_private_inactive_reclaimable_record(host_paths):
    from robopark_host.builder_cleanup import preview_owned_builder_cache

    runner = BuilderRunner(_rows(
        ("private", 8192, True, False, False, "regular"),
        ("shared", 16384, True, True, False, "regular"),
        ("active", 32768, True, False, True, "regular"),
        ("pinned", 65536, False, False, False, "regular"),
        ("internal", 131072, True, False, False, "internal"),
        ("empty", 0, True, False, False, "regular"),
    ))
    assert preview_owned_builder_cache(host_paths, runner)["blocked"]
    assert runner.calls == []
    _receipt(host_paths)

    preview = preview_owned_builder_cache(host_paths, runner)

    assert preview == {
        "blocked": False, "builder": BUILDER,
        "planned": [{"id": "private", "reported_bytes": 8192}],
        "total_reported_bytes": 8192, "other_candidates": 0,
    }
    assert all(call[:2] in (("docker", "buildx"), ("docker", "inspect")) for call in runner.calls)


def test_preview_blocks_invalid_or_ambiguous_builder_records(host_paths):
    from robopark_host.builder_cleanup import preview_owned_builder_cache

    _receipt(host_paths)
    for rows in (
        _rows(("private", 8192, True, False, False, "regular")) + "\n" + _rows(("private", 1, True, False, False, "regular")),
        '{"ID":"private","Size":8192,"Reclaimable":true,"Shared":false}',
        _rows(("private", 8192, True, False, False, "regular")) + "\n" + "x" * (64 * 1024),
    ):
        assert preview_owned_builder_cache(host_paths, BuilderRunner(rows))["blocked"]


def test_manual_preview_and_exact_cleanup_accept_human_sizes_from_buildx(host_paths):
    from robopark_host.builder_cleanup import (
        execute_owned_builder_cache_plan,
        preview_owned_builder_cache,
    )

    _receipt(host_paths)
    runner = BuilderRunner(_rows(("private", "8.192kB", True, False, False, "regular")))
    preview = preview_owned_builder_cache(host_paths, runner)
    assert preview["blocked"] is False
    assert preview["planned"] == [{"id": "private", "reported_bytes": 8192}]
    result = execute_owned_builder_cache_plan(host_paths, runner, preview)
    assert result["deleted"] == [{"id": "private", "reported_bytes": 8192}]
    assert [call for call in runner.calls if call[2] == "prune"] == [
        ("docker", "buildx", "prune", "--builder", BUILDER, "--filter", "id=private", "--force")
    ]


@pytest.mark.parametrize("size", [True, -1, "-1kB", "NaN", "8.192kB extra", "9999999999999999999GB"])
def test_manual_builder_preview_rejects_unsafe_sizes(host_paths, size):
    from robopark_host.builder_cleanup import preview_owned_builder_cache

    _receipt(host_paths)
    runner = BuilderRunner(_rows(("private", size, True, False, False, "regular")))
    assert preview_owned_builder_cache(host_paths, runner)["blocked"] is True
    assert not any(call[2] == "prune" for call in runner.calls)


def test_preview_rejects_a_replaced_builder_with_the_same_name(host_paths):
    from robopark_host.builder_cleanup import preview_owned_builder_cache

    _receipt(host_paths)

    class ReplacedBuilder(BuilderRunner):
        def run(self, argv, *, timeout, capture=False):
            if argv == ["docker", "buildx", "inspect", BUILDER]:
                self.calls.append(tuple(argv))
                return f"Name: {BUILDER}\nDriver: docker\n"
            return super().run(argv, timeout=timeout, capture=capture)

    runner = ReplacedBuilder(_rows(("private", 8192, True, False, False, "regular")))
    assert preview_owned_builder_cache(host_paths, runner)["blocked"]
    assert not any(call[2] == "du" for call in runner.calls)


def test_confirmed_cleanup_rechecks_plan_and_uses_only_exact_id_filter(host_paths):
    from robopark_host.builder_cleanup import (
        execute_owned_builder_cache_plan,
        preview_owned_builder_cache,
    )

    _receipt(host_paths)
    runner = BuilderRunner(_rows(("private", 8192, True, False, False, "regular")))
    preview = preview_owned_builder_cache(host_paths, runner)
    result = execute_owned_builder_cache_plan(host_paths, runner, preview)

    assert result == {"deleted": [{"id": "private", "reported_bytes": 8192}], "deleted_count": 1}
    assert [call for call in runner.calls if call[2] == "prune"] == [
        ("docker", "buildx", "prune", "--builder", BUILDER, "--filter", "id=private", "--force")
    ]
    assert not any("volume" in call or "system" in call or "--all" in call for call in runner.calls)


def test_changed_buildkit_plan_cannot_delete_anything(host_paths):
    from robopark_host.builder_cleanup import (
        execute_owned_builder_cache_plan,
        preview_owned_builder_cache,
    )

    _receipt(host_paths)
    runner = BuilderRunner(_rows(("private", 8192, True, False, False, "regular")), change_after_preview=True)
    preview = preview_owned_builder_cache(host_paths, runner)

    with pytest.raises(ValueError, match="builder_plan_changed"):
        execute_owned_builder_cache_plan(host_paths, runner, preview)
    assert not any(call[2] == "prune" for call in runner.calls)


def test_failed_buildkit_prune_reports_uncertain_record_without_leaking_docker_output(host_paths):
    from robopark_host.builder_cleanup import (
        BuilderCleanupPartialError,
        execute_owned_builder_cache_plan,
        preview_owned_builder_cache,
    )

    _receipt(host_paths)
    runner = BuilderRunner(_rows(("private", 8192, True, False, False, "regular")), fail_prune=True)
    preview = preview_owned_builder_cache(host_paths, runner)

    with pytest.raises(BuilderCleanupPartialError) as caught:
        execute_owned_builder_cache_plan(host_paths, runner, preview)
    assert caught.value.deleted == []
    assert caught.value.uncertain_target == {"id": "private", "reported_bytes": 8192}
    assert "private Docker error text" not in str(caught.value)


def test_host_builder_plan_is_one_use_and_expires_before_deletion(host_paths):
    from robopark_host.commands import SafeProductionTypedHostEffects
    from robopark_host.release import ReleaseError

    _receipt(host_paths)
    runner = BuilderRunner(_rows(("private", 8192, True, False, False, "regular")))
    effects = SafeProductionTypedHostEffects(host_paths, runner=runner)
    plan_id = str(uuid4())
    preview = effects.builder_cache_preview(plan_id)
    assert preview["plan_id"] == plan_id
    assert effects.builder_cache_execute(str(uuid4()), plan_id)["deleted_count"] == 1
    with pytest.raises(ReleaseError, match="builder_plan_changed"):
        effects.builder_cache_execute(str(uuid4()), plan_id)
    assert len([call for call in runner.calls if call[2] == "prune"]) == 1


def test_expired_host_builder_plan_never_calls_prune(host_paths):
    from robopark_host.commands import SafeProductionTypedHostEffects
    from robopark_host.release import ReleaseError

    _receipt(host_paths)
    runner = BuilderRunner(_rows(("private", 8192, True, False, False, "regular")))
    effects = SafeProductionTypedHostEffects(host_paths, runner=runner)
    plan_id = str(uuid4())
    effects.builder_cache_preview(plan_id)
    receipt = effects._read_private_json(None, "builder-cache-preview.json")
    effects._write_private_json(None, "builder-cache-preview.json", {
        **receipt, "created_at": receipt["created_at"] - 601,
    })

    with pytest.raises(ReleaseError, match="builder_plan_changed"):
        effects.builder_cache_execute(str(uuid4()), plan_id)
    assert not any(call[2] == "prune" for call in runner.calls)


def test_host_builder_plan_rechecks_record_before_any_prune(host_paths):
    from robopark_host.commands import SafeProductionTypedHostEffects
    from robopark_host.release import ReleaseError

    _receipt(host_paths)
    runner = BuilderRunner(
        _rows(("private", 8192, True, False, False, "regular")),
        change_after_preview=True,
    )
    effects = SafeProductionTypedHostEffects(host_paths, runner=runner)
    plan_id = str(uuid4())
    effects.builder_cache_preview(plan_id)

    with pytest.raises(ReleaseError, match="builder_plan_changed"):
        effects.builder_cache_execute(str(uuid4()), plan_id)
    assert not any(call[2] == "prune" for call in runner.calls)
