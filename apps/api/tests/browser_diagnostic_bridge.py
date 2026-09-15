"""JSON-lines transport for browser tests of the real diagnostic API.

One process owns one temporary database and real authenticated TestClients.
Only the external Emergency payload is replaced; no diagnostic DTO is fabricated.
Run via e2e/support/diagnosticApi.ts, never as an application server.
"""

import json
import os
import sys
from contextlib import ExitStack
from tempfile import TemporaryDirectory
from unittest.mock import patch


def run():
    with TemporaryDirectory(prefix="robopark-diagnostic-e2e-") as directory, ExitStack() as stack:
        os.environ["DATABASE_URL"] = f"sqlite:///{directory}/browser.db"
        os.environ["SECRET_KEY"] = "public-browser-fixture-key"
        os.environ["ROBOPARK_LIVE_MERGE"] = "0"

        from robopark_api.config import Settings, get_settings, reset_settings_cache

        Settings.model_config["env_file"] = None
        reset_settings_cache()

        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session

        from robopark_api import main
        from robopark_api.db import get_db
        from robopark_api.models import AuditLog, Base, Park, Permission, Role, User, UserPark
        from robopark_api.routers import emergency
        from robopark_api.security import hash_password
        from robopark_api.services import rbac
        from robopark_api.services.emergency_cache import EmergencyPayloadResult
        from robopark_api.services.emergency_config import ensure_default_section_roles
        from robopark_api.services.rbac_seed import ensure_rbac_catalog

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
            for slug in ["admin", "royal", "operator", "custom-admin"]:
                role = custom if slug == "custom-admin" else rbac.get_role_by_slug(db, slug)
                user = User(
                    username=f"{slug}-browser",
                    password_hash=hash_password("public-fixture-password"),
                    role_id=role.id,
                    access_status="approved",
                    is_active=True,
                )
                db.add(user)
                db.flush()
                db.add(UserPark(user_id=user.id, park_id=7))
            db.commit()

        settings = Settings(_env_file=None, ops_dir=f"{directory}/ops")
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
        for slug in ["admin", "royal", "operator", "custom-admin"]:
            # No lifespan: workers are outside this contract and must not contact upstreams.
            client = TestClient(app)
            stack.callback(client.close)
            response = client.post(
                "/auth/login",
                json={
                    "username": f"{slug}-browser",
                    "password": "public-fixture-password",
                },
            )
            assert response.status_code == 204, response.text
            clients[slug] = client
        print(json.dumps({"ready": True}), flush=True)
        for line in sys.stdin:
            request = json.loads(line)
            if request.get("audit"):
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
                response = clients[request.get("actor", "admin")].request(
                    request["method"],
                    request["path"],
                    content=request.get("body"),
                    headers=request.get("headers", {}),
                )
                response_data = {
                    "status": response.status_code,
                    "body": response.text,
                    "headers": dict(response.headers),
                }
            print(json.dumps({"id": request["id"], **response_data}), flush=True)


if __name__ == "__main__":
    run()
