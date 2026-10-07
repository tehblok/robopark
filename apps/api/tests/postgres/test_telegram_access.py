"""PostgreSQL concurrency contracts for Telegram access decisions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

from robopark_api.models import AuditLog, Park, ParkRequest, User, UserPark
from robopark_api.services import access_requests
from robopark_api.services.rbac import get_role_by_slug
from robopark_api.services.rbac_seed import ensure_rbac_catalog

from .test_schema_and_workflows import migrated_engine  # noqa: F401

pytestmark = pytest.mark.postgres


def test_multiple_park_admins_resolve_one_request_once(migrated_engine):  # noqa: F811
    unique = uuid4().hex
    with Session(migrated_engine) as db:
        ensure_rbac_catalog(db)
        admin_role = get_role_by_slug(db, "admin")
        mechanic_role = get_role_by_slug(db, "mechanic")
        assert admin_role is not None and mechanic_role is not None
        park = Park(name=unique, tag=unique, timezone="UTC")
        admins = [
            User(
                username=f"access-admin-{index}-{unique}",
                password_hash="unused",
                role_id=admin_role.id,
                access_status="approved",
                is_active=True,
            )
            for index in range(2)
        ]
        applicant = User(
            username=f"access-applicant-{unique}",
            password_hash="unused",
            role_id=mechanic_role.id,
            access_status="pending",
            is_active=True,
        )
        db.add_all([park, *admins, applicant])
        db.flush()
        db.add_all([UserPark(user_id=admin.id, park_id=park.id) for admin in admins])
        request = ParkRequest(user_id=applicant.id, park_id=park.id, status="pending")
        db.add(request)
        db.commit()
        ids = [admin.id for admin in admins], applicant.id, park.id, request.id

    ready = Barrier(2)

    def decide(args: tuple[int, bool]) -> tuple[bool, int]:
        admin_id, approve = args
        with Session(migrated_engine) as db:
            actor = db.get(User, admin_id)
            assert actor is not None
            ready.wait(timeout=5)
            try:
                access_requests.decide(
                    db,
                    actor,
                    ids[3],
                    approve=approve,
                    revision=1,
                )
                return approve, 200
            except HTTPException as error:
                return approve, error.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(decide, zip(ids[0], [True, False], strict=True)))
    assert sorted(status_code for _, status_code in results) == [200, 409]

    winning_approval = next(approve for approve, code in results if code == 200)
    with Session(migrated_engine) as db:
        request = db.get(ParkRequest, ids[3])
        applicant = db.get(User, ids[1])
        assert request is not None and applicant is not None
        assert request.revision == 2
        assert request.status == ("approved" if winning_approval else "rejected")
        assert applicant.role == "mechanic"
        assert applicant.access_status == ("approved" if winning_approval else "rejected")
        membership = db.get(UserPark, (ids[1], ids[2]))
        assert (membership is not None) is winning_approval

        db.execute(delete(AuditLog).where(AuditLog.actor_user_id.in_(ids[0])))
        db.execute(delete(UserPark).where(UserPark.park_id == ids[2]))
        db.execute(delete(ParkRequest).where(ParkRequest.id == ids[3]))
        db.execute(delete(User).where(User.id.in_([*ids[0], ids[1]])))
        db.execute(delete(Park).where(Park.id == ids[2]))
        db.commit()
