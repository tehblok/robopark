"""A report handoff persists across users on a migrated PostgreSQL installation."""

from __future__ import annotations

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from robopark_api import main
from robopark_api.config import Settings, get_settings
from robopark_api.db import configure_engine, get_db
from robopark_api.models import AccessStatus, Park, Report, Role, User, UserPark
from robopark_api.notification_delivery_models import NotificationDelivery
from robopark_api.schedule_models import NotificationEvent
from robopark_api.security import hash_password
from robopark_api.services import report_attachments, reports
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.rbac_seed import ensure_rbac_catalog

pytestmark = pytest.mark.postgres

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def test_migrated_postgres_report_handoff_and_attachment_scope(
    postgres_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("DATABASE_URL", postgres_database_url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = configure_engine(postgres_database_url)
    monkeypatch.setattr(report_attachments, "attachments_root", lambda: tmp_path / "reports")
    try:
        logins: dict[int, tuple[str, str]] = {}
        with Session(engine) as db:
            ensure_rbac_catalog(db)
            park = Park(name="Северный тестовый парк", tag="north-test", is_active=True)
            other_park = Park(name="Южный тестовый парк", tag="south-test", is_active=True)
            db.add_all([park, other_park])
            db.flush()

            def add_user(slug: str, park_id: int) -> User:
                role_id = db.scalar(select(Role.id).where(Role.slug == slug))
                assert role_id is not None
                password = f"R0bo!{uuid4().hex}"
                user = User(
                    username=f"report-{uuid4().hex[:12]}",
                    password_hash=hash_password(password),
                    role_id=role_id,
                    access_status=AccessStatus.approved.value,
                    is_active=True,
                )
                db.add(user)
                db.flush()
                db.add(UserPark(user_id=user.id, park_id=park_id))
                logins[user.id] = (user.username, password)
                return user

            mechanic = add_user(RoleSlug.MECHANIC, park.id)
            operator = add_user(RoleSlug.OPERATOR, park.id)
            foreign_operator = add_user(RoleSlug.OPERATOR, other_park.id)
            db.commit()

            report = reports.create_manual_report(
                db,
                author=mechanic,
                park_id=park.id,
                kind=reports.KIND_MECHANIC_PROBLEM,
                title="Повторная проверка колеса",
                body="Требуется осмотр робота.",
                tracker_key="ROBO-42",
                tracker_url=None,
            )
            attachment = report_attachments.add_attachment(
                db,
                mechanic,
                report.id,
                kind=report_attachments.KIND_DEVICE_PHOTO,
                filename="wheel.png",
                content=TINY_PNG,
                content_type="image/png",
            )
            park_id = park.id
            report_id, attachment_id = report.id, attachment.id
            mechanic_id, operator_id, foreign_id = mechanic.id, operator.id, foreign_operator.id

        with Session(engine) as db:
            mechanic = db.get(User, mechanic_id)
            operator = db.get(User, operator_id)
            foreign_operator = db.get(User, foreign_id)
            assert mechanic and operator and foreign_operator
            assert [item.id for item in reports.list_inbox(db, operator, park_id=park_id)] == [
                report_id
            ]
            assert [item.id for item in reports.list_mine(db, mechanic)] == [report_id]
            with pytest.raises(PermissionError):
                report_attachments.get_attachment(db, foreign_operator, report_id, attachment_id)

            row, path = report_attachments.get_attachment(db, operator, report_id, attachment_id)
            assert row.filename == "wheel.png"
            assert path.read_bytes() == TINY_PNG

            returned = reports.return_report(db, operator, report_id, "Добавьте результат проверки")
            assert returned.status == reports.STATUS_RETURNED
            resubmitted = reports.resubmit_report(
                db,
                mechanic,
                report_id,
                title="Колесо проверено",
                body="Повторная диагностика выполнена.",
                tracker_key="ROBO-42",
                tracker_url=None,
            )
            assert resubmitted.status == reports.STATUS_OPEN
            assert reports.done_report(db, operator, report_id).status == reports.STATUS_DONE

        with Session(engine) as db:
            operator = db.get(User, operator_id)
            assert operator is not None
            persisted = reports.get_report(db, operator, report_id)
            assert persisted.status == reports.STATUS_DONE
            assert persisted.title == "Колесо проверено"
            assert (
                report_attachments.get_attachment(db, operator, report_id, attachment_id)[
                    1
                ].read_bytes()
                == TINY_PNG
            )

        settings = Settings(
            _env_file=None,
            database_url=postgres_database_url,
            seed_username=None,
            seed_password=None,
            report_attachments_dir=str(tmp_path / "reports"),
            ops_dir=str(tmp_path / "ops"),
            ops_apply_root=str(tmp_path / "apply"),
            ops_sync=True,
        )
        factory = sessionmaker(bind=engine, future=True)
        monkeypatch.setattr(main, "SessionLocal", factory)
        monkeypatch.setattr(main, "get_settings", lambda: settings)
        app = main.create_app()

        def database_override():
            with factory() as db:
                yield db

        app.dependency_overrides[get_db] = database_override
        app.dependency_overrides[get_settings] = lambda: settings

        with TestClient(app) as client:

            def sign_in(user_id: int) -> None:
                client.post("/auth/logout")
                username, password = logins[user_id]
                assert (
                    client.post(
                        "/auth/login", json={"username": username, "password": password}
                    ).status_code
                    == 204
                )

            sign_in(mechanic_id)
            created = client.post(
                "/reports",
                json={
                    "park_id": park_id,
                    "kind": reports.KIND_MECHANIC_PROBLEM,
                    "title": "HTTP проверка робота",
                    "body": "Проверить колесо после ремонта.",
                    "tracker_key": "ROBO-43",
                },
            )
            assert created.status_code == 201
            http_report_id = created.json()["id"]
            uploaded = client.post(
                f"/reports/{http_report_id}/attachments",
                data={"kind": report_attachments.KIND_DEVICE_PHOTO},
                files={"file": ("http-wheel.png", TINY_PNG, "image/png")},
            )
            assert uploaded.status_code == 201
            http_attachment_id = uploaded.json()["id"]

            sign_in(operator_id)
            inbox = client.get("/reports/inbox", params={"park_id": park_id})
            assert inbox.status_code == 200
            assert http_report_id in {item["id"] for item in inbox.json()}
            downloaded = client.get(f"/reports/{http_report_id}/attachments/{http_attachment_id}")
            assert downloaded.status_code == 200
            assert downloaded.content == TINY_PNG
            returned = client.post(
                f"/reports/{http_report_id}/return", json={"comment": "Добавьте проверку"}
            )
            assert returned.status_code == 200
            assert returned.json()["status"] == reports.STATUS_RETURNED

            sign_in(foreign_id)
            assert client.get(f"/reports/{http_report_id}").status_code == 403
            assert (
                client.get(
                    f"/reports/{http_report_id}/attachments/{http_attachment_id}"
                ).status_code
                == 403
            )

            sign_in(mechanic_id)
            resubmitted = client.post(
                f"/reports/{http_report_id}/resubmit",
                json={
                    "title": "HTTP колесо проверено",
                    "body": "Повторная диагностика выполнена.",
                    "tracker_key": "ROBO-43",
                },
            )
            assert resubmitted.status_code == 200
            assert resubmitted.json()["status"] == reports.STATUS_OPEN

            sign_in(operator_id)
            done = client.post(f"/reports/{http_report_id}/done")
            assert done.status_code == 200
            assert done.json()["status"] == reports.STATUS_DONE

        with Session(engine) as db:
            assert (
                db.scalar(select(Report.status).where(Report.id == http_report_id))
                == reports.STATUS_DONE
            )
            events = list(
                db.scalars(
                    select(NotificationEvent).where(
                        NotificationEvent.user_id == operator_id,
                        NotificationEvent.event_type == "report",
                        NotificationEvent.park_id == park_id,
                    )
                )
            )
            assert len(events) == 2
            assert set(
                db.scalars(
                    select(NotificationDelivery.event_id).where(
                        NotificationDelivery.event_id.in_([event.id for event in events]),
                        NotificationDelivery.channel == "in_app",
                        NotificationDelivery.state == "delivered",
                    )
                )
            ) == {event.id for event in events}
    finally:
        engine.dispose()
