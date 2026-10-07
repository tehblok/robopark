from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.models import (
    NativeBotJob,
    NativeBotMigration,
    NativeBotMigrationSource,
    Park,
    User,
)
from robopark_api.native_telegram_schemas import BotJobCreate
from robopark_api.services import audit, bot_shared_settings
from robopark_api.services.native_telegram import utcnow

_SECTIONS = ("locations", "schedules", "broadcasts", "campaigns")
_ALL_DAYS = list(range(7))


def _source_fingerprint(
    sources: list[tuple[str, dict[str, bot_shared_settings.Section]]],
) -> str:
    value = json.dumps(
        [
            {
                "source": label,
                "revisions": {name: sections[name].revision for name in _SECTIONS},
            }
            for label, sections in sources
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()


def _target_fingerprint(
    source_fingerprint: str,
    parks: dict[str, Park],
    seen_sources: set[str],
) -> str:
    target = {
        "source": source_fingerprint,
        "parks": [
            {
                "id": park.id,
                "tag": park.tag,
                "chat_id": park.chat_id,
                "thread_id": park.thread_id,
                "revision": park.bot_revision,
            }
            for park in sorted(parks.values(), key=lambda item: item.id)
        ],
        "seen_sources": sorted(seen_sources),
    }
    raw = json.dumps(target, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _conflict(source: str, source_id: str | None, reason: str, park_tag=None) -> dict:
    return {
        "source": source,
        "source_id": source_id,
        "park_tag": park_tag,
        "reason": reason,
    }


def _source_ref(source: str, source_id: str, park_id: int) -> str:
    return f"legacy:{source}:{source_id}:{park_id}"


def _target_locations(row: dict, locations: dict[str, dict]) -> list[str] | None:
    mode = row.get("location_mode")
    if mode == "all":
        return sorted(locations)
    if mode == "hourly_png":
        return sorted(
            key
            for key, location in locations.items()
            if location.get("participation", {}).get("hourly_png") is True
        )
    if mode == "keys" or mode is None:
        keys = row.get("location_keys")
        return list(keys) if isinstance(keys, list) else None
    return None


def _job_preview(
    *,
    source: str,
    source_id: str,
    park: Park,
    title: str,
    kind: str,
    schedule: str,
    timezone: str | None,
    time: str | None,
    run_at: datetime | None,
    weekdays: list[int],
    text: str | None,
    url: str | None,
    tracker_tag: str | None,
    alternate: str = "all",
    anchor_date: date | None = None,
    start_hour: int | None = None,
    end_hour: int | None = None,
    source_ref_id: str | None = None,
) -> dict:
    return {
        "source_ref": _source_ref(source, source_ref_id or source_id, park.id),
        "source": source,
        "source_id": source_id,
        "park_id": park.id,
        "park_tag": park.tag,
        "title": title,
        "kind": kind,
        "schedule": schedule,
        "timezone": timezone,
        "time": time,
        "run_at": run_at,
        "start_hour": start_hour,
        "end_hour": end_hour,
        "weekdays": weekdays,
        "text": text,
        "url": url,
        "tracker_tag": tracker_tag,
        "alternate": alternate,
        "anchor_date": anchor_date,
    }


def _once_run_at(row: dict, timezone_name: str) -> datetime | None:
    try:
        zone = ZoneInfo(timezone_name)
        created_at = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            return None
        hour, minute = (int(value) for value in str(row["fire_at"]).split(":", 1))
        local_created = created_at.astimezone(zone)
        candidate = local_created.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate < local_created:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        return None


def preview(db: Session, host_data_path: str) -> dict:
    try:
        sources = bot_shared_settings.read_legacy_sources(host_data_path)
    except bot_shared_settings.BotConfigError as exc:
        raise HTTPException(status_code=409, detail="legacy_bot_config_invalid") from exc
    if not sources:
        sections = bot_shared_settings.read_all(host_data_path)
        sources = [("defaults", sections)]
    else:
        sections = sources[0][1]
    signatures = [
        tuple(
            json.dumps(
                source[name].value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            for name in _SECTIONS
        )
        for _, source in sources
    ]
    if any(signature != signatures[0] for signature in signatures[1:]):
        raise HTTPException(status_code=409, detail="legacy_bot_sources_conflict")
    source_fingerprint = _source_fingerprint(sources)
    receipt = db.scalar(
        select(NativeBotMigration).where(
            NativeBotMigration.source_fingerprint == source_fingerprint
        )
    )
    if receipt is not None:
        stored = json.loads(receipt.result_json)
        stored["already_applied"] = True
        return stored

    location_rows = sections["locations"].value["locations"]
    locations = {row["key"]: row for row in location_rows}
    parks = {
        park.tag: park
        for park in db.scalars(select(Park).execution_options(populate_existing=True)).all()
    }
    seen_sources = set(db.scalars(select(NativeBotMigrationSource.source_ref)).all())
    fingerprint = _target_fingerprint(source_fingerprint, parks, seen_sources)
    park_update_by_id: dict[int, dict] = {}
    destination_conflict_parks: set[int] = set()
    jobs: list[dict] = []
    conflicts: list[dict] = []
    deleted_location_keys = set(
        sections["locations"].value.get("metadata", {}).get("deleted_location_keys", [])
    )
    deleted_job_ids = set(sections["schedules"].value.get("deleted_job_ids", []))
    deleted_broadcast_ids = set(sections["broadcasts"].value.get("removed_ids", []))
    tombstones = [
        *(f"legacy:locations:{key}:*" for key in sorted(deleted_location_keys)),
        *(f"legacy:schedules:{job_id}:*" for job_id in sorted(deleted_job_ids)),
        *(f"legacy:broadcasts:{item_id}:*" for item_id in sorted(deleted_broadcast_ids)),
    ]
    tombstones = [item for item in tombstones if item not in seen_sources]
    current_tombstones = set(tombstones)
    blocked_tombstones = seen_sources | current_tombstones
    skipped = len(tombstones)

    def tombstone_blocks(marker: str) -> bool:
        nonlocal skipped
        if marker not in blocked_tombstones:
            return False
        if marker not in current_tombstones:
            skipped += 1
        return True

    for location in location_rows:
        if tombstone_blocks(f"legacy:locations:{location['key']}:*"):
            continue
        tag = location["tracker_tag"]
        park = parks.get(tag)
        if park is None:
            conflicts.append(_conflict("locations", location["key"], "park_not_found", tag))
            continue
        destination = location.get("chats", {}).get("prod")
        if destination is None:
            continue
        chat_id = destination.get("chat_id")
        thread_id = destination.get("thread_id")
        if (
            not isinstance(chat_id, int)
            or chat_id == 0
            or abs(chat_id) > 2**52 - 1
            or (thread_id is not None and (not isinstance(thread_id, int) or thread_id <= 0))
        ):
            conflicts.append(_conflict("locations", location["key"], "unsupported_shape", tag))
        elif park.id in destination_conflict_parks:
            continue
        elif park.chat_id is None:
            update = {
                "park_id": park.id,
                "park_tag": park.tag,
                "location_key": location["key"],
                "chat_id": chat_id,
                "thread_id": thread_id,
            }
            existing_update = park_update_by_id.get(park.id)
            if existing_update is None:
                park_update_by_id[park.id] = update
            elif (existing_update["chat_id"], existing_update["thread_id"]) != (
                chat_id,
                thread_id,
            ):
                park_update_by_id.pop(park.id, None)
                destination_conflict_parks.add(park.id)
                conflicts.append(
                    _conflict("locations", location["key"], "destination_conflict", tag)
                )
        elif (park.chat_id, park.thread_id) != (
            chat_id,
            thread_id,
        ) and park.id not in destination_conflict_parks:
            destination_conflict_parks.add(park.id)
            conflicts.append(_conflict("locations", location["key"], "destination_conflict", tag))

    park_updates = [park_update_by_id[park_id] for park_id in sorted(park_update_by_id)]
    planned_source_refs: set[str] = set()

    def add_job(job: dict, location_key: str) -> None:
        nonlocal skipped
        if tombstone_blocks(f"legacy:locations:{location_key}:*"):
            return
        location = locations.get(location_key)
        if location is None:
            conflicts.append(_conflict(job["source"], job["source_id"], "location_not_found"))
            return
        park = parks.get(location["tracker_tag"])
        if park is None:
            conflicts.append(
                _conflict(
                    job["source"], job["source_id"], "park_not_found", location["tracker_tag"]
                )
            )
            return
        row = _job_preview(park=park, **job)
        if row["source_ref"] in seen_sources:
            skipped += 1
            return
        if row["source_ref"] in planned_source_refs:
            skipped += 1
            return
        try:
            BotJobCreate.model_validate(
                {
                    "park_id": row["park_id"],
                    "kind": row["kind"],
                    "title": row["title"],
                    "enabled": False,
                    "schedule": row["schedule"],
                    "timezone": row["timezone"],
                    "time": row["time"],
                    "run_at": row["run_at"],
                    "weekdays": row["weekdays"],
                    "start_hour": row["start_hour"],
                    "end_hour": row["end_hour"],
                    "text": row["text"],
                    "url": row["url"],
                    "tracker_tag": row["tracker_tag"],
                    "alternate": row["alternate"],
                    "anchor_date": row["anchor_date"],
                }
            )
        except ValidationError:
            conflicts.append(
                _conflict(job["source"], job["source_id"], "unsupported_shape", park.tag)
            )
            return
        planned_source_refs.add(row["source_ref"])
        jobs.append(row)

    schedules = sections["schedules"].value
    source_timezone = str(schedules.get("timezone") or "Europe/Moscow")
    try:
        anchor = date.fromisoformat(schedules["planner_anchor"])
    except (KeyError, TypeError, ValueError):
        anchor = None
    for row in schedules.get("jobs", []):
        source_id = str(row.get("id") or "")
        if tombstone_blocks(f"legacy:schedules:{source_id}:*"):
            continue
        if row.get("kind") == "planner_ab":
            groups = row.get("groups")
            if (
                not isinstance(groups, dict)
                or not isinstance(row.get("fire_at"), str)
                or not isinstance(row.get("label"), str)
                or len(row["label"]) > 128
                or anchor is None
            ):
                conflicts.append(_conflict("schedules", source_id or None, "unsupported_shape"))
                continue
            raw_weekdays = row.get("weekdays")
            weekdays = _ALL_DAYS if raw_weekdays is None else list(raw_weekdays)
            valid_group = False
            for side, alternate in (("A", "even"), ("B", "odd")):
                group = groups.get(side)
                if not isinstance(group, dict) or not isinstance(group.get("locations"), list):
                    continue
                link = str(group.get("link") or "").strip() or None
                if len(link or "") > 2048:
                    continue
                valid_group = True
                for key in group["locations"]:
                    add_job(
                        {
                            "source": "schedules",
                            "source_id": source_id,
                            "source_ref_id": f"{source_id}:{side}",
                            "title": row["label"],
                            "kind": "zoom" if link else "text",
                            "schedule": "daily",
                            "timezone": source_timezone,
                            "time": row["fire_at"],
                            "run_at": None,
                            "weekdays": weekdays,
                            "text": str(row.get("text") or "").strip() or None,
                            "url": link,
                            "tracker_tag": None,
                            "alternate": alternate,
                            "anchor_date": anchor,
                        },
                        str(key),
                    )
            if not valid_group:
                conflicts.append(_conflict("schedules", source_id or None, "unsupported_shape"))
            continue
        locations_for_job = row.get("locations")
        if (
            row.get("kind") not in {None, "simple"}
            or not isinstance(locations_for_job, list)
            or not locations_for_job
            or not isinstance(row.get("fire_at"), str)
            or not isinstance(row.get("label"), str)
            or len(row["label"]) > 128
            or len(str(row.get("link") or "")) > 2048
        ):
            conflicts.append(_conflict("schedules", source_id or None, "unsupported_shape"))
            continue
        alternate = {"A": "even", "B": "odd", None: "all"}.get(row.get("alternate"))
        if alternate is None or (alternate != "all" and anchor is None):
            conflicts.append(_conflict("schedules", source_id, "unsupported_shape"))
            continue
        link = str(row.get("link") or "").strip() or None
        raw_weekdays = row.get("weekdays")
        weekdays = _ALL_DAYS if raw_weekdays is None else list(raw_weekdays)
        for key in locations_for_job:
            add_job(
                {
                    "source": "schedules",
                    "source_id": source_id,
                    "title": row["label"],
                    "kind": "zoom" if link else "text",
                    "schedule": "daily",
                    "timezone": source_timezone,
                    "time": row["fire_at"],
                    "run_at": None,
                    "weekdays": weekdays,
                    "text": str(row.get("text") or "").strip() or None,
                    "url": link,
                    "tracker_tag": None,
                    "alternate": alternate,
                    "anchor_date": anchor if alternate != "all" else None,
                },
                str(key),
            )

    for source in ("broadcasts", "campaigns"):
        for row in sections[source].value.get("campaigns", []):
            source_id = str(row.get("id") or "")
            if tombstone_blocks(f"legacy:{source}:{source_id}:*"):
                continue
            keys = _target_locations(row, locations)
            title = row.get("label") if source == "broadcasts" else f"SK {row.get('tag', '')}"
            tracker_tag = None if source == "broadcasts" else row.get("tag")
            if (
                keys is None
                or not isinstance(title, str)
                or not title.strip()
                or len(title) > 128
                or (tracker_tag is not None and len(str(tracker_tag)) > 128)
            ):
                conflicts.append(_conflict(source, source_id, "unsupported_shape"))
                continue
            weekdays = list(range(5)) if row.get("repeat") == "weekdays" else _ALL_DAYS
            schedule = "once" if row.get("repeat") == "once" else "daily"
            run_at = _once_run_at(row, source_timezone) if schedule == "once" else None
            if schedule == "once" and run_at is None:
                conflicts.append(_conflict(source, source_id, "unsupported_shape"))
                continue
            for key in keys:
                add_job(
                    {
                        "source": source,
                        "source_id": source_id,
                        "title": title.strip(),
                        "kind": "text" if source == "broadcasts" else "campaign",
                        "schedule": schedule,
                        "timezone": source_timezone,
                        "time": None if schedule == "once" else row["fire_at"],
                        "run_at": run_at,
                        "weekdays": [] if schedule == "once" else weekdays,
                        "text": row.get("text") if source == "broadcasts" else None,
                        "url": None,
                        "tracker_tag": tracker_tag,
                    },
                    str(key),
                )

    global_window = schedules.get("send_window", {})
    for location in location_rows:
        if not location.get("participation", {}).get("hourly_png"):
            continue
        source_id = f"hourly_report:{location['key']}"
        if f"legacy:locations:{location['key']}:*" in blocked_tombstones:
            continue
        window = location.get("report_window") or global_window
        if (
            not isinstance(window, dict)
            or type(window.get("start_hour")) is not int
            or type(window.get("end_hour")) is not int
        ):
            conflicts.append(
                _conflict("schedules", source_id, "unsupported_shape", location["tracker_tag"])
            )
            continue
        add_job(
            {
                "source": "schedules",
                "source_id": source_id,
                "title": f"Report · {location['display_name']}",
                "kind": "report",
                "schedule": "hourly",
                "timezone": source_timezone,
                "time": None,
                "run_at": None,
                "weekdays": _ALL_DAYS,
                "text": None,
                "url": None,
                "tracker_tag": None,
                "start_hour": window["start_hour"],
                "end_hour": window["end_hour"],
            },
            location["key"],
        )

    result = {
        "fingerprint": fingerprint,
        "already_applied": False,
        "park_updates": park_updates,
        "jobs": jobs,
        "conflicts": conflicts,
        "counts": {
            "park_updates": len(park_updates),
            "jobs": len(jobs),
            "conflicts": len(conflicts),
            "skipped": skipped,
        },
        "_tombstones": tombstones,
        "_source_fingerprint": source_fingerprint,
    }
    return result


def apply(db: Session, user: User, host_data_path: str, fingerprint: str) -> dict:
    plan = preview(db, host_data_path)
    if plan["fingerprint"] != fingerprint:
        raise HTTPException(status_code=409, detail="migration_fingerprint_changed")
    existing = db.get(NativeBotMigration, fingerprint)
    if existing is not None:
        stored = json.loads(existing.result_json)
        return {**stored, "already_applied": True, "applied": True}

    applied_at = utcnow()
    stored = {**plan, "already_applied": False, "applied_at": applied_at.isoformat()}
    receipt = NativeBotMigration(
        fingerprint=fingerprint,
        source_fingerprint=plan["_source_fingerprint"],
        actor_user_id=user.id,
        result_json=json.dumps(stored, ensure_ascii=False, separators=(",", ":"), default=str),
        created_at=applied_at,
    )
    try:
        db.add(receipt)
        for source_ref in plan.get("_tombstones", []):
            db.add(
                NativeBotMigrationSource(
                    source_ref=source_ref,
                    fingerprint=fingerprint,
                    disposition="deleted",
                )
            )
        for update in plan["park_updates"]:
            park = db.scalar(
                select(Park)
                .where(Park.id == update["park_id"])
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if park is None or park.chat_id is not None:
                raise HTTPException(status_code=409, detail="migration_state_changed")
            park.chat_id = update["chat_id"]
            park.thread_id = update["thread_id"]
            park.bot_revision += 1
        for item in plan["jobs"]:
            row = NativeBotJob(
                source_ref=item["source_ref"],
                park_id=item["park_id"],
                kind=item["kind"],
                title=item["title"],
                enabled=False,
                schedule=item["schedule"],
                timezone=item["timezone"],
                time=item["time"],
                run_at=item["run_at"],
                start_hour=item["start_hour"],
                end_hour=item["end_hour"],
                weekdays=",".join(str(day) for day in item["weekdays"]),
                text=item["text"],
                url=item["url"],
                tracker_tag=item["tracker_tag"],
                alternate=item["alternate"],
                anchor_date=item["anchor_date"],
            )
            db.add(row)
            db.flush()
            db.add(
                NativeBotMigrationSource(
                    source_ref=item["source_ref"],
                    fingerprint=fingerprint,
                    disposition="imported",
                    native_job_id=row.id,
                )
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="migration_state_changed") from None
    except Exception:
        db.rollback()
        raise
    audit.record(
        db,
        action=audit.ACTION_NATIVE_BOT_MIGRATION_APPLIED,
        actor=user,
        target_type="native_bot_migration",
        target_id=fingerprint,
        detail=json.dumps(plan["counts"], separators=(",", ":")),
    )
    return {**stored, "applied": True}
