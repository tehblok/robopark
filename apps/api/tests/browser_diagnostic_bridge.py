"""JSON-lines transport for browser tests of the real diagnostic API.

One process owns one temporary database and real authenticated TestClients.
Only the external Emergency payload is replaced; no diagnostic DTO is fabricated.
Run via e2e/support/diagnosticApi.ts, never as an application server.
"""

import json
import os
import secrets
import sys
from base64 import b64decode, b64encode
from collections import Counter
from contextlib import ExitStack
from tempfile import TemporaryDirectory
from unittest.mock import patch


def run():
    with TemporaryDirectory(prefix="robopark-diagnostic-e2e-") as directory, ExitStack() as stack:
        os.environ["DATABASE_URL"] = f"sqlite:///{directory}/browser.db"
        os.environ["SECRET_KEY"] = os.environ["ROBOPARK_BROWSER_SECRET_KEY"]
        os.environ["ROBOPARK_LIVE_MERGE"] = "0"
        browser_password = os.environ["ROBOPARK_BROWSER_PASSWORD"]

        from robopark_api.config import Settings, get_settings, reset_settings_cache

        Settings.model_config["env_file"] = None
        reset_settings_cache()

        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session, sessionmaker

        from robopark_api import main
        from robopark_api.collaboration_models import TrackerClaim
        from robopark_api.db import get_db
        from robopark_api.models import AuditLog, Base, Park, Permission, Role, User, UserPark
        from robopark_api.routers import emergency
        from robopark_api.security import hash_password
        from robopark_api.services import (
            platform_settings,
            rbac,
            tracker_cache,
            tracker_client,
            tracker_outbox,
        )
        from robopark_api.services.emergency_cache import EmergencyPayloadResult
        from robopark_api.services.emergency_config import ensure_default_section_roles
        from robopark_api.services.rbac_seed import ensure_rbac_catalog
        from robopark_api.task_workflow_models import ReliableAction, TaskMessage

        engine = create_engine(
            os.environ["DATABASE_URL"], connect_args={"check_same_thread": False}
        )
        stack.callback(engine.dispose)
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            ensure_rbac_catalog(db)
            ensure_default_section_roles(db)
            custom = Role(slug="custom-admin", name="Custom administrator", is_system=False)
            custom.permissions = list(db.scalars(select(Permission)))
            db.add(custom)
            db.add(Park(id=7, name="Северный парк", tag="north", tracker_queue="ROBOPARK"))
            db.flush()
            for slug in ["admin", "royal", "operator", "mechanic", "mechanic-next", "custom-admin"]:
                role_slug = "mechanic" if slug == "mechanic-next" else slug
                role = custom if slug == "custom-admin" else rbac.get_role_by_slug(db, role_slug)
                user = User(
                    username=f"{slug}-browser",
                    password_hash=hash_password(browser_password),
                    role_id=role.id,
                    access_status="approved",
                    is_active=True,
                )
                db.add(user)
                db.flush()
                db.add(UserPark(user_id=user.id, park_id=7))
            platform_settings.set_setting(
                db, platform_settings.TRACKER_TOKEN_KEY, secrets.token_urlsafe(24)
            )
            db.commit()

        tracker = {
            "available": True,
            "status": "В очереди",
            "status_key": "queued",
            "comments": [],
            "field_values": {},
            "counts": Counter(),
            "assignee": None,
            "tags": ["north"],
            "components": ["Колёса"],
            "component_ids": ["wheels"],
            "defect_code": None,
            "solution_method": None,
            "transitions": [
                {"id": "start", "display": "В работу"},
                {"id": "review", "display": "На проверку"},
                {"id": "return", "display": "Вернуть в работу"},
                {"id": "close", "display": "Закрыть"},
            ],
        }

        def tracker_issue(**_kwargs):
            require_tracker()
            return {
                "key": "ROBOPARK-42",
                "summary": "Проверить колесо робота [447]",
                "status": tracker["status"],
                "status_key": tracker["status_key"],
                "queue": "ROBOPARK",
                "tags": list(tracker["tags"]),
                "created": "2026-09-02T08:00:00Z",
                "updated": "2026-09-02T08:00:00Z",
                "queued_at": "2026-09-02T08:00:00Z",
                "sla_deadline": "2026-09-02T13:00:00Z",
                "sla_source": "status_history",
                "hours_created": "1",
                "robot": "447",
                "description": "Проверить крепление колеса.",
                "assignee": tracker["assignee"],
                "reporter": {"display": "Оператор", "login": "operator-browser"},
                "resolution": None,
                "priority": "normal",
                "type": "repair",
                "type_key": "repair",
                "components": list(tracker["components"]),
                "component_ids": list(tracker["component_ids"]),
                "defect_code": tracker["defect_code"],
                "solution_method": tracker["solution_method"],
                "attachments": [],
            }

        def require_tracker():
            if not tracker["available"]:
                raise tracker_client.TrackerError("network unavailable")

        def list_comments(**_kwargs):
            tracker["counts"]["list_comments"] += 1
            return list(tracker["comments"])

        def add_comment(**kwargs):
            require_tracker()
            text = kwargs["text"]
            marker = next(
                (line for line in text.splitlines() if line.startswith("surp-action:")), text
            )
            tracker["counts"][f"comment:{marker}"] += 1
            external_id = f"comment-{len(tracker['comments']) + 1}"
            tracker["comments"].append(
                {
                    "id": external_id,
                    "text": text,
                    "author": "Бот СУРП",
                    "created_at": "2026-09-02T09:00:00Z",
                    "attachments": [],
                }
            )
            return {"id": external_id}

        def upload_temp_attachment(**_kwargs):
            require_tracker()
            tracker["counts"]["upload"] += 1
            return "temporary-photo-1"

        def transition_issue(**kwargs):
            require_tracker()
            transition = kwargs["transition"]
            tracker["counts"][f"transition:{transition}"] += 1
            tracker["status"], tracker["status_key"] = {
                "start": ("В работе", "in_progress"),
                "review": ("Проверка", "verification"),
                "return": ("В работе", "in_progress"),
                "close": ("Закрыто", "closed"),
            }[transition]

        def set_issue_field(*, field_id, value, **_kwargs):
            require_tracker()
            tracker["counts"][f"field:{field_id}"] += 1
            tracker["field_values"][field_id] = value

        def assign_issue(*, assignee, **_kwargs):
            require_tracker()
            tracker["counts"]["assign"] += 1
            tracker["assignee"] = {"display": assignee, "login": assignee}

        def set_issue_tags(*, tags, **_kwargs):
            require_tracker()
            tracker["counts"]["tags"] += 1
            tracker["tags"] = list(tags)

        def set_issue_components(*, components, **_kwargs):
            require_tracker()
            tracker["counts"]["components"] += 1
            tracker["components"] = list(components)

        def list_queue_components(**_kwargs):
            require_tracker()
            tracker["counts"]["component_catalog"] += 1
            return [
                {"id": "wheels", "label": "Колёса"},
                {"id": "lidar", "label": "Лидар"},
            ]

        def set_repair_fields(*, component_ids, defect_code, solution_method, **_kwargs):
            require_tracker()
            tracker["counts"]["repair_fields"] += 1
            tracker["counts"]["repair_fields:components"] += 1
            tracker["counts"]["repair_fields:defect_code"] += 1
            tracker["counts"]["repair_fields:solution_method"] += 1
            tracker["component_ids"] = list(component_ids)
            labels = {"wheels": "Колёса", "lidar": "Лидар"}
            tracker["components"] = [labels.get(value, value) for value in component_ids]
            tracker["defect_code"] = defect_code
            tracker["solution_method"] = solution_method
            tracker["field_values"]["components"] = list(component_ids)
            tracker["field_values"]["60df26695151a36df681d67b--theDefectCode"] = defect_code
            tracker["field_values"]["solutionMethod"] = solution_method

        for name, replacement in {
            "get_issue": tracker_issue,
            "search_issues": lambda **_kwargs: [tracker_issue()],
            "get_issue_status_history": lambda **_kwargs: [],
            "list_comments": list_comments,
            "list_transitions": lambda **_kwargs: (require_tracker(), list(tracker["transitions"]))[
                1
            ],
            "add_comment": add_comment,
            "upload_temp_attachment": upload_temp_attachment,
            "transition_issue": transition_issue,
            "assign_issue": assign_issue,
            "set_issue_tags": set_issue_tags,
            "set_issue_components": set_issue_components,
            "list_queue_components": list_queue_components,
            "set_repair_fields": set_repair_fields,
        }.items():
            stack.enter_context(patch.object(tracker_client, name, replacement))
        stack.enter_context(
            patch.object(
                tracker_client,
                "_client",
                side_effect=AssertionError("unmocked Tracker client call in diagnostic bridge"),
            )
        )
        stack.enter_context(patch.object(tracker_outbox, "_set_issue_field", set_issue_field))

        settings = Settings(
            _env_file=None,
            ops_dir=f"{directory}/ops",
            cors_origins=os.environ.get("ROBOPARK_BROWSER_ORIGIN", "http://127.0.0.1:4173"),
        )
        stack.enter_context(patch.object(main, "get_settings", return_value=settings))
        payload = {
            "isOnline": True,
            "batteriesStatus": {"chargePercents": 84},
            "wheelsBroken": [0],
            "errors": ["LIDAR_OFFLINE", "UNMAPPED_SENSOR_42"],
        }
        stack.enter_context(patch.object(emergency, "_get_robot_payload", return_value=payload))
        stack.enter_context(
            patch.object(
                emergency,
                "_get_robot_payload_result",
                return_value=EmergencyPayloadResult(payload, False, 0),
            )
        )
        # Explicit mutation proves the browser catches a broken snapshot integration.
        if os.environ.get("DIAGNOSTIC_E2E_MUTATION") == "drop-events":
            stack.enter_context(
                patch(
                    "robopark_api.services.emergency_snapshot.match_diagnostic_events",
                    return_value=[],
                )
            )
        app = main.create_app()

        def database():
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_settings] = lambda: settings
        clients = {}
        for slug in ["admin", "royal", "operator", "mechanic", "mechanic-next", "custom-admin"]:
            # No lifespan: workers are outside this contract and must not contact upstreams.
            client = TestClient(app)
            stack.callback(client.close)
            response = client.post(
                "/auth/login",
                json={
                    "username": f"{slug}-browser",
                    "password": browser_password,
                },
            )
            assert response.status_code == 204, response.text
            clients[slug] = client
        print(json.dumps({"ready": True}), flush=True)
        for line in sys.stdin:
            request = json.loads(line)
            if request.get("control"):
                if (
                    os.environ.get("ROBOPARK_BROWSER_BRIDGE") != "1"
                    and os.environ.get("DIAGNOSTIC_E2E_MUTATION") != "task-lifecycle"
                ):
                    response_data = {"status": 403, "json": {"detail": "test_control_disabled"}}
                elif request["control"] == "tracker":
                    tracker["available"] = bool(request.get("available"))
                    if "transitions" in request:
                        tracker["transitions"] = request["transitions"]
                    if tracker["available"]:
                        tracker_cache.clear_all_for_tests()
                    response_data = {"status": 200, "json": {"available": tracker["available"]}}
                elif request["control"] == "drain":
                    with Session(engine) as db:
                        for row in db.query(ReliableAction).filter_by(state="retry_wait"):
                            row.next_attempt_at = 0
                        db.commit()
                    processed = 0
                    factory = sessionmaker(bind=engine, future=True)
                    for _ in range(20):
                        count = tracker_outbox._process_batch(factory)
                        processed += count
                        if count == 0:
                            break
                    response_data = {"status": 200, "json": {"processed": processed}}
                elif request["control"] == "snapshot":
                    with Session(engine) as db:
                        actions = [
                            {
                                "id": row.id,
                                "action": row.action,
                                "state": row.state,
                                "error_code": row.error_code,
                            }
                            for row in db.query(ReliableAction).order_by(
                                ReliableAction.created_at, ReliableAction.id
                            )
                        ]
                        timeline = [
                            row.text
                            for row in db.query(TaskMessage).order_by(
                                TaskMessage.created_at, TaskMessage.id
                            )
                        ]
                        claims = [
                            {
                                "issue_key": row.issue_key,
                                "owner_user_id": row.owner_user_id,
                                "state": row.state,
                                "start_action_id": row.start_action_id,
                            }
                            for row in db.query(TrackerClaim).order_by(TrackerClaim.issue_key)
                        ]
                    response_data = {
                        "status": 200,
                        "json": {
                            "counts": dict(tracker["counts"]),
                            "field_values": tracker["field_values"],
                            "actions": actions,
                            "claims": claims,
                            "timeline": timeline,
                        },
                    }
                else:
                    response_data = {"status": 400, "json": {"detail": "unknown_test_control"}}
            elif request.get("audit"):
                with Session(engine) as db:
                    result = [
                        {"action": row.action, "target_id": row.target_id, "detail": row.detail}
                        for row in db.scalars(
                            select(AuditLog)
                            .where(AuditLog.target_type == "diagnostic_rule")
                            .order_by(AuditLog.id)
                        )
                    ]
                response_data = {"status": 200, "json": result}
            else:
                content = (
                    b64decode(request["body_base64"])
                    if request.get("body_base64")
                    else request.get("body")
                )
                response = clients[request.get("actor", "admin")].request(
                    request["method"],
                    request["path"],
                    content=content,
                    headers=request.get("headers", {}),
                )
                response_data = {
                    "status": response.status_code,
                    "body": response.text,
                    "body_base64": b64encode(response.content).decode("ascii"),
                    "headers": dict(response.headers),
                }
            print(json.dumps({"id": request["id"], **response_data}), flush=True)


if __name__ == "__main__":
    run()
