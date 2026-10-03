"""Two mechanics cannot take the same queued Tracker task concurrently."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.collaboration_models import TrackerClaim
from robopark_api.db import configure_engine
from robopark_api.models import AccessStatus, Park, Role, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import task_lifecycle
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.rbac_seed import ensure_rbac_catalog
from robopark_api.task_workflow_models import ReliableAction

pytestmark = pytest.mark.postgres


def test_only_one_mechanic_can_claim_a_queued_task(postgres_database_url: str, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", postgres_database_url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = configure_engine(postgres_database_url)
    issue_key = "ROBO-CONCURRENT-51"
    try:
        with Session(engine) as db:
            ensure_rbac_catalog(db)
            role_id = db.scalar(select(Role.id).where(Role.slug == RoleSlug.MECHANIC))
            assert role_id is not None
            park = Park(name="Парк параллельной проверки", tag="claim-test", is_active=True)
            db.add(park)
            db.flush()
            park_id = park.id
            user_ids = []
            for _ in range(2):
                user = User(
                    username=f"claim-{uuid4().hex[:12]}",
                    password_hash=hash_password(uuid4().hex),
                    role_id=role_id,
                    access_status=AccessStatus.approved.value,
                    is_active=True,
                )
                db.add(user)
                db.flush()
                db.add(UserPark(user_id=user.id, park_id=park_id))
                user_ids.append(user.id)
            db.commit()

        ready = Barrier(2)

        def take(user_id: int) -> int:
            with Session(engine) as db:
                actor = db.get(User, user_id)
                park = db.get(Park, park_id)
                assert actor is not None and park is not None
                ready.wait(timeout=5)
                try:
                    task_lifecycle.claim(
                        db,
                        actor=actor,
                        issue_key=issue_key,
                        park=park,
                        idempotency_key=f"claim-{user_id}-{uuid4().hex}",
                        issue={"key": issue_key, "status_key": "queued", "components": []},
                    )
                except HTTPException as exc:
                    return exc.status_code
                return 200

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(take, user_ids))

        assert sorted(outcomes) == [200, 409]
        with Session(engine) as db:
            claim = db.get(TrackerClaim, issue_key)
            assert claim is not None
            assert claim.owner_user_id in user_ids
            actions = list(
                db.scalars(
                    select(ReliableAction).where(
                        ReliableAction.resource_type == "tracker_issue",
                        ReliableAction.resource_id == issue_key,
                    )
                )
            )
            assert [action.action for action in actions].count("start") == 1
            assert len({action.actor_user_id for action in actions}) == 1
    finally:
        engine.dispose()
