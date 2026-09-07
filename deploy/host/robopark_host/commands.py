"""Bounded, root-owned single-slot command consumer shared by boot and .path."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from uuid import UUID

from .bundle import create_diagnostic_bundle
from .doctor import run_doctor
from .release import UTC, ReleaseError, UpdateRequest, timestamp, unique_object
from .repair import DEFAULT_REPAIRS, run_repairs
from .state import atomic_write_json, exclusive_lock


def _read(path, limit=4096):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError()
            value = json.loads(stream.read(limit + 1), object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise ReleaseError("invalid_command") from exc


def _validate(value, *, fresh=True):
    if value.get("kind") == "update":
        try:
            stamp = timestamp(value.get("created_at"))
            if stamp.utcoffset().total_seconds() != 0:
                raise ValueError()
            # Preserve the exact request. Expired root claims must still reach
            # recovery; the worker never receives a refreshed approval.
            candidate = value if fresh else {**value, "created_at": datetime.now(UTC).isoformat()}
            UpdateRequest.from_dict(candidate)
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            raise ReleaseError("invalid_command") from exc
        return value
    try:
        expected = {"job_id", "kind", "actor_user_id", "created_at"}
        if value.get("kind") == "restore":
            expected.update({"artifact", "sha256"})
            if value.get("artifact") != "restore-" + str(value.get("job_id")) + ".zip":
                raise ValueError()
            if not isinstance(value.get("sha256"), str) or not re.fullmatch(
                r"[a-f0-9]{64}", value["sha256"]
            ):
                raise ValueError()
        if value.get("kind") == "github-update":
            expected.add("release_id")
            if type(value.get("release_id")) is not int or not 0 < value["release_id"] < 2**63:
                raise ValueError()
        if set(value) != expected:
            raise ValueError()
        if str(UUID(value["job_id"])) != value["job_id"] or value["kind"] not in {
            "diagnostics",
            "repair",
            "github-update",
            "restore",
        }:
            raise ValueError()
        if type(value["actor_user_id"]) is not int or not 0 < value["actor_user_id"] < 2**63:
            raise ValueError()
        stamp = timestamp(value["created_at"])
        if (
            stamp.utcoffset().total_seconds() != 0
            or fresh
            and not -300 <= (datetime.now(UTC) - stamp).total_seconds() <= 86400
        ):
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        raise ReleaseError("invalid_command") from exc


def _public(paths):
    root = paths.ops / "public"
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    root.chmod(0o755)
    return root


def publish_health(paths, report):
    """Only canonical diagnostic fields and bounded release identifiers leave root."""
    try:
        manifest = json.loads((paths.current / "manifest.json").read_text())
    except (OSError, ValueError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}

    def identifier(key, pattern):
        value = manifest.get(key)
        return value if isinstance(value, str) and re.fullmatch(pattern, value) else None

    value = {
        "version": identifier("app_version", r"\d{1,9}(?:\.\d{1,9}){0,2}"),
        "git_sha": identifier("git_sha", r"[a-fA-F0-9]{40}"),
        "generated_at": report.created_at,
        "overall": "degraded" if any(c.status != "ok" for c in report.checks) else "ok",
        "checks": report.as_dict()["checks"],
        "update": {"state": "unknown"},
        "last_backup": {"status": "unknown"},
    }
    try:
        backup = _read(paths.state / "last-backup.json")
        completed_at = timestamp(backup.get("completed_at")).astimezone(UTC).isoformat()
        if backup.get("status") in ("success", "failed"):
            value["last_backup"] = {
                "status": backup["status"],
                "completed_at": completed_at,
            }
    except (ReleaseError, ValueError, TypeError):
        pass
    atomic_write_json(_public(paths) / "system-health.json", value, mode=0o644)


def _claim_public(paths, request, active):
    atomic_write_json(
        _public(paths) / "command-claim.json",
        {
            "job_id": request["job_id"],
            "kind": request["kind"],
            "actor_user_id": request["actor_user_id"],
            "active": active,
        },
        mode=0o644,
    )


def _finish(paths, request, result):
    public = _public(paths)
    atomic_write_json(public / "command-result.json", result, mode=0o644)
    receipts = paths.state / "command-receipts"
    atomic_write_json(
        receipts / (request["job_id"] + ".json"), {"request": request, "result": result}
    )
    _claim_public(paths, request, False)
    (paths.state / "command-request.json").unlink(missing_ok=True)


def _allow_attempt(paths, request):
    counter = paths.state / "command-attempts.json"
    try:
        saved = _read(counter)
    except ReleaseError:
        saved = {}
    attempts = saved.get("attempts", 0) if saved.get("job_id") == request["job_id"] else 0
    if type(attempts) is not int or attempts < 0:
        attempts = 3
    if attempts >= 3:
        # No more automatic retries for this command. Keep interrupted updates
        # in maintenance until the root operator explicitly recovers them.
        if request["kind"] in {"update", "restore"}:
            from .updater import _maintenance, _publish_status

            _maintenance(paths, True)
            _publish_status(
                paths,
                {
                    "state": "maintenance",
                    "job_id": request["job_id"],
                    "error": "manual_recovery_required",
                },
            )
        else:
            _finish(
                paths,
                request,
                {
                    "job_id": request["job_id"],
                    "kind": request["kind"],
                    "actor_user_id": request["actor_user_id"],
                    "state": "failed",
                    "artifact": None,
                    "before": [],
                    "after": [],
                    "performed": [],
                    "failed": [],
                    "error": "command_interrupted",
                },
            )
        return False
    atomic_write_json(counter, {"job_id": request["job_id"], "attempts": attempts + 1})
    return True


def consume_commands(paths, runner, http, *, update_runner=None, github_http=None):
    """All privileged work is serialized; API never chooses argv or output paths."""
    if paths.root == Path("/") and os.geteuid() != 0:
        return 1
    # Separate outer lock prevents two launchers while worker owns host.lock.
    with exclusive_lock(paths.ops / "command-consumer.lock"):
        pending = paths.state / "command-request.json"
        inbox = paths.ops / "inbox/approved.json"
        with exclusive_lock(paths.ops / "host.lock"):
            resumed = pending.exists()
            if not resumed:
                if not inbox.exists() and not inbox.is_symlink():
                    return 0
                try:
                    request = _validate(_read(inbox), fresh=False)
                except ReleaseError:
                    inbox.unlink(missing_ok=True)
                    return 1
                # Persist a private copy before removing the untrusted slot.
                atomic_write_json(pending, request)
                _claim_public(paths, request, True)
                inbox.unlink(missing_ok=True)
            else:
                try:
                    request = _validate(_read(pending), fresh=False)
                except ReleaseError:
                    return 1
            if resumed and inbox.exists():
                try:
                    if _read(inbox) == request:
                        inbox.unlink()
                except ReleaseError:
                    inbox.unlink(missing_ok=True)
            fresh = (
                -300
                <= (datetime.now(UTC) - timestamp(request["created_at"])).total_seconds()
                <= 86400
            )
            receipt = paths.state / "command-receipts" / (request["job_id"] + ".json")
            if receipt.is_file():
                saved = _read(receipt, limit=65536)
                if saved.get("request") != request:
                    pending.unlink(missing_ok=True)
                    _claim_public(paths, request, False)
                    return 1
                if request["kind"] in {"update", "github-update"}:
                    atomic_write_json(
                        _public(paths) / "rebuild.result", saved["result"], mode=0o644
                    )
                    _claim_public(paths, request, False)
                    pending.unlink(missing_ok=True)
                else:
                    _finish(paths, request, saved["result"])
                return 0
            if request["kind"] == "github-update":
                from .github_releases import (
                    GithubHttp,
                    current_available,
                    download_approved_release,
                )
                from .updater import publish_result

                try:
                    if resumed or not fresh:
                        directory = paths.state / "github-artifacts"
                        for suffix in (".zip.partial", ".zip.sig.partial"):
                            (
                                directory
                                / ("github-release-" + str(request["release_id"]) + suffix)
                            ).unlink(missing_ok=True)
                        raise ReleaseError("github_approval_expired")
                    release = current_available(paths, request["release_id"])
                    artifact = download_approved_release(
                        release, paths, github_http or GithubHttp()
                    )
                    request = {key: value for key, value in request.items() if key != "release_id"}
                    request.update(kind="update", artifact=artifact.name)
                    atomic_write_json(artifact.with_suffix(".zip.approval.json"), request)
                    atomic_write_json(pending, request)
                    _claim_public(paths, request, True)
                except (OSError, ValueError, TypeError, KeyError, RecursionError):
                    result = {
                        "job_id": request["job_id"],
                        "ok": False,
                        "error": "github_download_failed",
                    }
                    publish_result(paths, result)
                    atomic_write_json(receipt, {"request": request, "result": result})
                    _claim_public(paths, request, False)
                    pending.unlink(missing_ok=True)
                    return 1
            if not _allow_attempt(paths, request):
                return 0
            if request["kind"] == "restore":
                from .restore import run_restore
                from .updater import SystemRunner, _maintenance

                if not fresh and not resumed:
                    result = {
                        **{key: request[key] for key in ("job_id", "kind", "actor_user_id")},
                        "state": "failed",
                        "error": "request_expired",
                    }
                else:
                    try:
                        result = run_restore(paths, request, update_runner or SystemRunner())
                    except Exception:
                        _maintenance(paths, True)
                        return 1
                if result["state"] == "maintenance":
                    atomic_write_json(_public(paths) / "command-result.json", result, mode=0o644)
                    return 1
                _finish(paths, request, result)
                return int(result["state"] != "succeeded")
            if request["kind"] != "update":
                result = {
                    "job_id": request["job_id"],
                    "kind": request["kind"],
                    "actor_user_id": request["actor_user_id"],
                    "state": "failed",
                    "artifact": None,
                    "before": [],
                    "after": [],
                    "performed": [],
                    "failed": [],
                    "error": "command_interrupted" if resumed else "request_expired",
                }
                try:
                    if not resumed and fresh:
                        before = run_doctor(paths, runner, http)
                        result["before"] = before.as_dict()["checks"]
                        if request["kind"] == "diagnostics":
                            directory = _public(paths) / "artifacts"
                            directory.mkdir(mode=0o755, exist_ok=True)
                            directory.chmod(0o755)
                            artifact = create_diagnostic_bundle(
                                paths,
                                before,
                                runner,
                                directory / (request["job_id"] + ".zip"),
                            )
                            artifact.chmod(0o644)
                            result["artifact"] = artifact.name
                            after = before
                        else:
                            repairs = run_repairs(before, DEFAULT_REPAIRS, runner)
                            after = run_doctor(paths, runner, http)
                            result.update(performed=repairs.performed, failed=repairs.failed)
                        result["after"] = after.as_dict()["checks"]
                        publish_health(paths, after)
                        result.update(
                            state="failed" if result["failed"] else "succeeded",
                            error="repair_failed" if result["failed"] else None,
                        )
                except Exception:
                    result.update(state="failed", error="host_operation_failed")
                _finish(paths, request, result)
                return int(result["state"] != "succeeded")
        # Worker/recovery acquire host.lock themselves. Keep outer consumer lock.
        from .launcher import launch_update
        from .updater import SystemRunner, recover_interrupted_update

        update_runner = update_runner or SystemRunner()
        if resumed or not fresh:
            recover_interrupted_update(paths, update_runner)
        try:
            finished = _read(paths.ops / "public/rebuild.result").get("job_id") == request["job_id"]
        except ReleaseError:
            finished = False
        worker_request = paths.state / "update-worker-request.json"
        code = 0
        if not finished and not fresh:
            from .updater import publish_result

            publish_result(
                paths,
                {"job_id": request["job_id"], "ok": False, "error": "request_expired"},
            )
            finished = True
            code = 1
        if not finished:
            atomic_write_json(worker_request, request)
            code = launch_update(paths, worker_request, update_runner)
        worker_request.unlink(missing_ok=True)
        with exclusive_lock(paths.ops / "host.lock"):
            try:
                result = _read(paths.ops / "public/rebuild.result")
            except ReleaseError:
                return 1
            if result.get("job_id") == request["job_id"]:
                atomic_write_json(
                    paths.state / "command-receipts" / (request["job_id"] + ".json"),
                    {"request": request, "result": result},
                )
                _claim_public(paths, request, False)
                pending.unlink(missing_ok=True)
        return code
