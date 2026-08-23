# Phase 4 Operator Working Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship operator cabinet hub with three read-only Tracker tools (assigned-park blockers, multi-queue robot search, «Сейчас по Tracker» metrics) and move park/request management to `/operator/parks`.

**Architecture:** Dedicated operator routers mirroring Phase 3 mechanic pattern; reuse `tracker_client` + `tracker_filters`; add `tracker_metrics.py` for count-based now-report queries; operator ACL via `get_operator_parks` / `require_operator_park`. No DB migration.

**Tech Stack:** FastAPI + SQLAlchemy + httpx (Phase 3); pytest with `unittest.mock` patches; React + Vite pages under `/operator/*`; Russian UI (`PageShell`, `ru.ts`).

**Origin:** Approved design `docs/superpowers/specs/2026-08-22-robopark-phase4-operator-tools-design.md`.

**Base branch:** `origin/main` (Phase 3 merged). Create worktree `.worktrees/phase4-operator` on branch `feature/phase4-operator-tools`. If PR #4 (Russian UI) is merged, base on that; otherwise implement API first then layer Russian copy from `feature/web-ru-polish` patterns.

## Global Constraints

- Operator tools: `role=operator` + `access_status=approved` only.
- Park scope: **only** parks in `user_parks` for that operator; never trust client `park_id` without ACL check.
- Read-only: no ticket edit, comments, assignee changes.
- Reuse platform Tracker token from Phase 3; never expose raw token to operator.
- Blockers require: assigned park, `feature_blockers=true`, non-empty `tracker_queue`, token configured.
- Now-report requires: assigned park(s) with `feature_reports=true` + `tracker_queue`.
- Do **not** import code from `robopark_tehblokdan`; port query semantics only.
- Out of scope: SLA ≥12ч, xlsx, untagged blockers, Emergency, Telegram, Postgres, Alembic.
- UI language: Russian.
- Follow Phase 3 test patterns: hermetic `conftest`, `login_as`, mock `_search` / `count_issues`.

## File map

```
apps/api/src/robopark_api/
  deps.py                         # + get_operator_parks, require_operator_park
  schemas.py                      # OperatorBlockersOut, NowReportOut, …
  services/
    tracker_client.py             # + count_issues (Tracker /issues/_count)
    tracker_metrics.py            # NEW: query builders + collect_park_metrics + cache
  routers/
    operator_blockers.py          # NEW GET /operator/blockers
    operator_robots.py            # NEW GET /operator/robots/{query}/tickets
    operator_report.py            # NEW GET /operator/now-report
  main.py                         # include three routers
apps/api/tests/
  test_operator_deps.py
  test_tracker_metrics.py
  test_operator_blockers.py
  test_operator_robots.py
  test_operator_report.py
apps/web/src/
  api.ts                          # operator tool client methods + types
  App.tsx                         # RequireApprovedOperator + new routes
  i18n/ru.ts                      # hub copy + error details
  i18n/errors.ts                  # map new detail codes
  pages/Operator.tsx              # REPLACE with hub (tool grid)
  pages/OperatorParks.tsx         # MOVE current Operator.tsx content
  pages/OperatorBlockers.tsx      # NEW
  pages/OperatorRobotSearch.tsx   # NEW
  pages/OperatorNowReport.tsx     # NEW
README.md                         # brief Phase 4 operator tools note
```

---

### Task 1: Operator park deps

**Files:**
- Modify: `apps/api/src/robopark_api/deps.py`
- Create: `apps/api/tests/test_operator_deps.py`

**Interfaces:**
- Consumes: `User`, `Park`, `UserPark`, `require_approved_operator` (existing)
- Produces:
  - `get_operator_parks(db: Session, user: User) -> list[Park]` — join `user_parks`, order by `Park.id`
  - `require_operator_park(park_id: int, db: Session, user: User) -> Park` — 404 if missing, 403 if not assigned

- [ ] **Step 1: Write failing tests**

```python
# apps/api/tests/test_operator_deps.py
from fastapi import HTTPException
import pytest

from robopark_api.deps import get_operator_parks, require_operator_park
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password


def _approved_operator(db, username: str) -> User:
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_get_operator_parks_ordered(db_session):
    op = _approved_operator(db_session, "op-deps-1")
    p_b = Park(name="B", tag="B", is_active=True)
    p_a = Park(name="A", tag="A", is_active=True)
    db_session.add_all([p_b, p_a])
    db_session.flush()
    # assign in reverse id order; helper must return by Park.id ascending
    db_session.add_all(
        [
            UserPark(user_id=op.id, park_id=p_b.id),
            UserPark(user_id=op.id, park_id=p_a.id),
        ]
    )
    db_session.commit()

    parks = get_operator_parks(db_session, op)
    assert [p.id for p in parks] == sorted(p.id for p in parks)
    assert {p.tag for p in parks} == {"A", "B"}


def test_require_operator_park_forbidden_when_not_assigned(db_session):
    op = _approved_operator(db_session, "op-deps-2")
    other = Park(name="X", tag="X", is_active=True)
    db_session.add(other)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        require_operator_park(other.id, db_session, op)
    assert exc.value.status_code == 403


def test_require_operator_park_not_found(db_session):
    op = _approved_operator(db_session, "op-deps-3")
    with pytest.raises(HTTPException) as exc:
        require_operator_park(99999, db_session, op)
    assert exc.value.status_code == 404


def test_require_operator_park_ok(db_session):
    op = _approved_operator(db_session, "op-deps-4")
    park = Park(name="Y", tag="Y", is_active=True)
    db_session.add(park)
    db_session.flush()
    db_session.add(UserPark(user_id=op.id, park_id=park.id))
    db_session.commit()
    assert require_operator_park(park.id, db_session, op).id == park.id
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd apps/api && source .venv/bin/activate && pytest tests/test_operator_deps.py -v
```

Expected: FAIL (import/name error for new helpers)

- [ ] **Step 3: Implement**

Append to `apps/api/src/robopark_api/deps.py`:

```python
def get_operator_parks(db: Session, user: User) -> list[Park]:
    return list(
        db.scalars(
            select(Park)
            .join(UserPark)
            .where(UserPark.user_id == user.id)
            .order_by(Park.id)
        ).all()
    )


def require_operator_park(park_id: int, db: Session, user: User) -> Park:
    park = db.get(Park, park_id)
    if park is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    assigned = db.scalar(
        select(UserPark).where(
            UserPark.user_id == user.id,
            UserPark.park_id == park_id,
        )
    )
    if assigned is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return park
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd apps/api && source .venv/bin/activate && pytest tests/test_operator_deps.py -v
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/robopark_api/deps.py apps/api/tests/test_operator_deps.py
git commit -m "$(cat <<'EOF'
feat(api): add operator park ACL helpers

EOF
)"
```

---

### Task 2: Tracker count + metrics service

**Files:**
- Modify: `apps/api/src/robopark_api/services/tracker_client.py`
- Create: `apps/api/src/robopark_api/services/tracker_metrics.py`
- Create: `apps/api/tests/test_tracker_metrics.py`

**Interfaces:**
- Consumes: existing query helpers / httpx / `TrackerError`
- Produces:
  - `tracker_client.count_issues(*, token: str, query: str) -> int`
  - Export or duplicate `_join_query` / `_ql_quote` for metrics builders (prefer exporting public aliases `join_query` / `ql_quote` from `tracker_client` if currently private)
  - `tracker_metrics.DEFAULT_STATUS_KEYS`
  - `tracker_metrics.build_backlog_query(queue, tag, donor_tag="") -> str`
  - `tracker_metrics.build_arrived_today_query(queue, tag) -> str`
  - `tracker_metrics.build_done_today_query(queue, tag) -> str`
  - `tracker_metrics.build_status_query(queue, tag, statuses) -> str | None`
  - `tracker_metrics.collect_park_metrics(*, token, queue, tag) -> dict[str, int]`
    - Keys: `open_blockers`, `backlog`, `in_transit`, `queued`, `waiting_team`, `waiting_parts`, `arrived`, `done`
    - Call order for counts must be fixed as listed above (tests use `side_effect`)
  - `get_cached_now_report(cache_key) -> dict | None`
  - `set_cached_now_report(cache_key, payload, ttl_sec=60) -> None`
  - `clear_metrics_cache() -> None`

**Default status keys** (port semantics, no old-bot import):

```python
DEFAULT_STATUS_KEYS: dict[str, list[str]] = {
    "waiting_parts": ["delieveryWaiting", "Ожидание поставки"],
    "in_transit": ["moving", "Перемещение"],
    "queued": ["queued", "В очереди"],
    "waiting_team": ["waitingForAnotherTeam", "Ждём смежников"],
}
```

- [ ] **Step 1: Write failing tests**

```python
# apps/api/tests/test_tracker_metrics.py
from unittest.mock import patch

from robopark_api.services import tracker_metrics


def test_build_backlog_query_no_donor_exclude_when_empty():
    q = tracker_metrics.build_backlog_query("ROBOPARK", "Alpha", "")
    assert "Queue: ROBOPARK" in q
    assert 'Tags: "Alpha"' in q
    assert 'Tags: !' not in q


def test_collect_park_metrics_fixed_order():
    with patch(
        "robopark_api.services.tracker_metrics.count_issues",
        side_effect=[3, 1, 0, 2, 0, 1, 4, 5],
    ) as mocked:
        result = tracker_metrics.collect_park_metrics(
            token="t", queue="ROBOPARK", tag="Alpha"
        )
    assert mocked.call_count == 8
    assert result == {
        "open_blockers": 3,
        "backlog": 1,
        "in_transit": 0,
        "queued": 2,
        "waiting_team": 0,
        "waiting_parts": 1,
        "arrived": 4,
        "done": 5,
    }


def test_metrics_cache_roundtrip():
    tracker_metrics.clear_metrics_cache()
    tracker_metrics.set_cached_now_report("k", {"totals": {"blocker": 1}}, ttl_sec=60)
    assert tracker_metrics.get_cached_now_report("k") == {"totals": {"blocker": 1}}
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd apps/api && source .venv/bin/activate && pytest tests/test_tracker_metrics.py -v
```

- [ ] **Step 3: Implement `count_issues`**

Add to `tracker_client.py`:

```python
TRACKER_COUNT_URL = "https://api.tracker.yandex.net/v2/issues/_count"


def count_issues(*, token: str, query: str) -> int:
    headers = {"Authorization": f"OAuth {token}"}
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                TRACKER_COUNT_URL,
                headers=headers,
                json={"query": query},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise TrackerError(str(exc)) from exc
    if isinstance(payload, (int, float)):
        return int(payload)
    if isinstance(payload, dict):
        for key in ("count", "total", "value"):
            if key in payload:
                return int(payload[key])
    raise TrackerError("unexpected tracker count response")
```

Also export thin public wrappers if needed:

```python
def join_query(*parts: str) -> str:
    return _join_query(*parts)


def ql_quote(value: str) -> str:
    return _ql_quote(value)
```

- [ ] **Step 4: Implement `tracker_metrics.py`**

```python
# apps/api/src/robopark_api/services/tracker_metrics.py
from __future__ import annotations

import time
from typing import Any

from robopark_api.services.tracker_client import (
    build_open_blockers_query,
    count_issues,
    join_query,
    ql_quote,
)

DEFAULT_STATUS_KEYS: dict[str, list[str]] = {
    "waiting_parts": ["delieveryWaiting", "Ожидание поставки"],
    "in_transit": ["moving", "Перемещение"],
    "queued": ["queued", "В очереди"],
    "waiting_team": ["waitingForAnotherTeam", "Ждём смежников"],
}

_METRICS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
METRICS_CACHE_TTL_SEC = 60


def clear_metrics_cache() -> None:
    _METRICS_CACHE.clear()


def get_cached_now_report(cache_key: str) -> dict[str, Any] | None:
    entry = _METRICS_CACHE.get(cache_key)
    if entry is None:
        return None
    expires_at, payload = entry
    if expires_at <= time.monotonic():
        _METRICS_CACHE.pop(cache_key, None)
        return None
    return payload


def set_cached_now_report(
    cache_key: str,
    payload: dict[str, Any],
    *,
    ttl_sec: int = METRICS_CACHE_TTL_SEC,
) -> None:
    _METRICS_CACHE[cache_key] = (time.monotonic() + ttl_sec, payload)


def _status_or_clause(statuses: list[str]) -> str:
    parts: list[str] = []
    for raw in statuses:
        text = raw.strip()
        if not text:
            continue
        if " " in text or not text.isascii():
            parts.append(f'Status: "{text}"')
        else:
            parts.append(f"Status: {text}")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "(" + " OR ".join(parts) + ")"


def build_backlog_query(queue: str, tag: str, donor_tag: str = "") -> str:
    parts = [
        f"Queue: {queue}",
        "Priority: blocker",
        "Resolution: empty()",
        f"Tags: {ql_quote(tag)}",
    ]
    donor = (donor_tag or "").strip()
    if donor:
        parts.append(f"Tags: !{ql_quote(donor)}")
    return join_query(*parts)


def build_arrived_today_query(queue: str, tag: str) -> str:
    return join_query(
        f"Queue: {queue}",
        "Priority: blocker",
        "Resolution: empty(), fixed",
        "Created: today()",
        f"Tags: {ql_quote(tag)}",
    )


def build_done_today_query(queue: str, tag: str) -> str:
    return join_query(
        f"Queue: {queue}",
        "Priority: blocker",
        "Resolution: fixed",
        "Updated: today()",
        f"Tags: {ql_quote(tag)}",
    )


def build_status_query(queue: str, tag: str, statuses: list[str]) -> str | None:
    status_part = _status_or_clause(statuses)
    if not status_part:
        return None
    return join_query(
        f"Queue: {queue}",
        "Priority: blocker",
        "Resolution: empty()",
        f"Tags: {ql_quote(tag)}",
        status_part,
    )


def collect_park_metrics(*, token: str, queue: str, tag: str) -> dict[str, int]:
    keys = DEFAULT_STATUS_KEYS
    query_map: list[tuple[str, str | None]] = [
        ("open_blockers", build_open_blockers_query(queue, tag)),
        ("backlog", build_backlog_query(queue, tag, "")),
        ("in_transit", build_status_query(queue, tag, keys["in_transit"])),
        ("queued", build_status_query(queue, tag, keys["queued"])),
        ("waiting_team", build_status_query(queue, tag, keys["waiting_team"])),
        ("waiting_parts", build_status_query(queue, tag, keys["waiting_parts"])),
        ("arrived", build_arrived_today_query(queue, tag)),
        ("done", build_done_today_query(queue, tag)),
    ]
    metrics: dict[str, int] = {}
    for key, query in query_map:
        metrics[key] = count_issues(token=token, query=query) if query else 0
    return metrics
```

Note: `build_open_blockers_query` in Phase 3 already includes Priority: blocker + open clause + tag. Reuse it for `open_blockers`.

- [ ] **Step 5: Run — expect PASS**

```bash
cd apps/api && source .venv/bin/activate && pytest tests/test_tracker_metrics.py -v
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/robopark_api/services/tracker_client.py \
  apps/api/src/robopark_api/services/tracker_metrics.py \
  apps/api/tests/test_tracker_metrics.py
git commit -m "$(cat <<'EOF'
feat(api): add Tracker count client and park metrics service

EOF
)"
```

---

### Task 3: Schemas + operator blockers route

**Files:**
- Modify: `apps/api/src/robopark_api/schemas.py`
- Create: `apps/api/src/robopark_api/routers/operator_blockers.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Create: `apps/api/tests/test_operator_blockers.py`

**Interfaces:**
- Consumes: `require_approved_operator`, `require_operator_park`, `fetch_park_blockers`, `tracker_filters`, `get_tracker_token`, `BlockerOut`
- Produces:
  - `OperatorBlockersOut(park_id: int, park_tag: str, status: str, counts: dict[str, int], items: list[BlockerOut])`
  - `GET /operator/blockers?park_id=&status=all`
- Errors: `400` bad status; `403` not assigned; `404` park missing; `409` `blockers_disabled_for_park`; `503` `tracker_token_not_configured`; `502` upstream

- [ ] **Step 1: Write failing tests**

```python
# apps/api/tests/test_operator_blockers.py
import json
from pathlib import Path
from unittest.mock import patch

from conftest import login_as
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services.tracker_client import issue_to_dict

FIXTURES = Path(__file__).parent / "fixtures"


def seed_op_with_park(db_session, *, feature_blockers=True, queue="ROBOPARK"):
    park = Park(
        name="Alpha",
        tag="Alpha",
        is_active=True,
        tracker_queue=queue,
        feature_blockers=feature_blockers,
        feature_reports=True,
    )
    db_session.add(park)
    db_session.flush()
    op = User(
        username="op-block",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db_session.add(op)
    db_session.flush()
    db_session.add(UserPark(user_id=op.id, park_id=park.id))
    db_session.commit()
    db_session.refresh(op)
    db_session.refresh(park)
    return op, park


def test_blockers_requires_token(client, db_session, seed_royal):
    _, park = seed_op_with_park(db_session)
    login_as(client, "op-block", "secret")
    r = client.get(f"/operator/blockers?park_id={park.id}")
    assert r.status_code == 503
    assert r.json()["detail"] == "tracker_token_not_configured"


def test_blockers_forbidden_other_park(client, db_session, seed_royal):
    _, _park = seed_op_with_park(db_session)
    other = Park(name="Other", tag="Other", is_active=True, tracker_queue="ROBOPARK")
    db_session.add(other)
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-block", "secret")
    r = client.get(f"/operator/blockers?park_id={other.id}")
    assert r.status_code == 403


def test_blockers_mocked_ok(client, db_session, seed_royal):
    _, park = seed_op_with_park(db_session)
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-block", "secret")
    issues = [
        issue_to_dict(i)
        for i in json.loads((FIXTURES / "tracker_issues.json").read_text())
    ]
    with patch("robopark_api.services.tracker_client._search", return_value=issues):
        r = client.get(f"/operator/blockers?park_id={park.id}&status=all")
    assert r.status_code == 200
    body = r.json()
    assert body["park_id"] == park.id
    assert body["park_tag"] == "Alpha"
    assert body["counts"]["all"] == 2
    assert len(body["items"]) == 2


def test_blockers_disabled(client, db_session, seed_royal):
    _, park = seed_op_with_park(db_session, feature_blockers=False)
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-block", "secret")
    r = client.get(f"/operator/blockers?park_id={park.id}")
    assert r.status_code == 409
    assert r.json()["detail"] == "blockers_disabled_for_park"
```

- [ ] **Step 2: Run — expect FAIL** (route missing)

```bash
cd apps/api && source .venv/bin/activate && pytest tests/test_operator_blockers.py -v
```

- [ ] **Step 3: Add schema**

```python
# schemas.py
class OperatorBlockersOut(BaseModel):
    park_id: int
    park_tag: str
    status: str
    counts: dict[str, int]
    items: list[BlockerOut]
```

- [ ] **Step 4: Implement router**

Create `apps/api/src/robopark_api/routers/operator_blockers.py` mirroring `mechanic_tasks.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_approved_operator, require_operator_park
from robopark_api.models import User
from robopark_api.schemas import BlockerOut, OperatorBlockersOut
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_client, tracker_filters

router = APIRouter(prefix="/operator", tags=["operator-blockers"])


def _blocker_out(item: dict) -> BlockerOut:
    return BlockerOut(
        key=item["key"],
        summary=item["summary"],
        status=item["status"],
        robot=item.get("robot"),
        created_at=item.get("created"),
        hours_created=item.get("hours_created"),
        url=tracker_client.build_issue_url(item["key"]),
        bucket=tracker_filters.issue_status_bucket(item),
    )


@router.get("/blockers", response_model=OperatorBlockersOut)
def operator_blockers(
    park_id: int = Query(...),
    status_filter: str = Query(default="all", alias="status"),
    user: User = Depends(require_approved_operator),
    db: Session = Depends(get_db),
) -> OperatorBlockersOut:
    if status_filter not in tracker_filters.VALID_STATUS_FILTERS:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST)

    park = require_operator_park(park_id, db, user)

    if not park.feature_blockers or not park.tracker_queue:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="blockers_disabled_for_park",
        )

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    try:
        issues = tracker_client.fetch_park_blockers(
            token=token,
            queue=park.tracker_queue,
            park_tag=park.tag,
        )
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    sorted_issues = tracker_filters.sort_issues_oldest_first(issues)
    filtered = tracker_filters.filter_issues_by_status(sorted_issues, status_filter)
    counts = tracker_filters.count_status_buckets(sorted_issues)
    return OperatorBlockersOut(
        park_id=park.id,
        park_tag=park.tag,
        status=status_filter,
        counts=counts,
        items=[_blocker_out(item) for item in filtered[:200]],
    )
```

Register in `main.py`:

```python
from robopark_api.routers import operator_blockers
# ...
app.include_router(operator_blockers.router)
```

- [ ] **Step 5: Run — expect PASS**

```bash
cd apps/api && source .venv/bin/activate && pytest tests/test_operator_blockers.py -v
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/robopark_api/schemas.py \
  apps/api/src/robopark_api/routers/operator_blockers.py \
  apps/api/src/robopark_api/main.py \
  apps/api/tests/test_operator_blockers.py
git commit -m "$(cat <<'EOF'
feat(api): add GET /operator/blockers for assigned parks

EOF
)"
```

---

### Task 4: Operator robot search route

**Files:**
- Create: `apps/api/src/robopark_api/routers/operator_robots.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Create: `apps/api/tests/test_operator_robots.py`

**Interfaces:**
- Consumes: `get_operator_parks`, `search_robot_tickets`, `RobotTicketsOut`, `sort_issues_oldest_first`
- Produces: `GET /operator/robots/{query}/tickets`
- Behavior:
  1. Distinct non-empty `tracker_queue` from assigned parks
  2. None → `409` `no_tracker_parks`
  3. No token → `503` `tracker_token_not_configured`
  4. For each queue call `search_robot_tickets`; merge; dedupe by `key`; oldest-first sort

- [ ] **Step 1: Write failing tests**

```python
# apps/api/tests/test_operator_robots.py
from unittest.mock import patch

from conftest import login_as
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password


def seed_op_two_queues(db_session):
    p1 = Park(name="A", tag="A", is_active=True, tracker_queue="Q1")
    p2 = Park(name="B", tag="B", is_active=True, tracker_queue="Q2")
    p3 = Park(name="C", tag="C", is_active=True, tracker_queue=None)
    db_session.add_all([p1, p2, p3])
    db_session.flush()
    op = User(
        username="op-robot",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db_session.add(op)
    db_session.flush()
    for p in (p1, p2, p3):
        db_session.add(UserPark(user_id=op.id, park_id=p.id))
    db_session.commit()
    return op


def test_robot_search_no_queues(client, db_session, seed_royal):
    op = User(
        username="op-empty",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db_session.add(op)
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-empty", "secret")
    r = client.get("/operator/robots/a447/tickets")
    assert r.status_code == 409
    assert r.json()["detail"] == "no_tracker_parks"


def test_robot_search_merges_and_dedupes(client, db_session, seed_royal):
    seed_op_two_queues(db_session)
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-robot", "secret")

    def fake_search(*, token, queue, query):
        base = {
            "status": "queued",
            "created": "2026-01-01T10:00:00+00:00",
            "hours_created": "100.0",
            "robot": "a447",
            "in_relocation": "0",
            "status_key": "queued",
            "resolution": "",
        }
        if queue == "Q1":
            return [{**base, "key": "R-1", "summary": "[a447] one"}]
        return [
            {**base, "key": "R-1", "summary": "[a447] one"},
            {
                **base,
                "key": "R-2",
                "summary": "[a447] two",
                "status": "moving",
                "hours_created": "50.0",
                "in_relocation": "1",
                "status_key": "moving",
                "created": "2026-01-02T10:00:00+00:00",
            },
        ]

    with patch(
        "robopark_api.services.tracker_client.search_robot_tickets",
        side_effect=fake_search,
    ):
        r = client.get("/operator/robots/a447/tickets")
    assert r.status_code == 200
    keys = [i["key"] for i in r.json()["items"]]
    assert len(keys) == 2
    assert set(keys) == {"R-1", "R-2"}
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement router**

```python
# apps/api/src/robopark_api/routers/operator_robots.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import get_operator_parks, require_approved_operator
from robopark_api.models import User
from robopark_api.schemas import BlockerOut, RobotTicketsOut
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_client, tracker_filters

router = APIRouter(prefix="/operator", tags=["operator-robots"])


def _blocker_out(item: dict) -> BlockerOut:
    return BlockerOut(
        key=item["key"],
        summary=item["summary"],
        status=item["status"],
        robot=item.get("robot"),
        created_at=item.get("created"),
        hours_created=item.get("hours_created"),
        url=tracker_client.build_issue_url(item["key"]),
        bucket=tracker_filters.issue_status_bucket(item),
    )


@router.get("/robots/{query}/tickets", response_model=RobotTicketsOut)
def operator_robot_tickets(
    query: str,
    user: User = Depends(require_approved_operator),
    db: Session = Depends(get_db),
) -> RobotTicketsOut:
    parks = get_operator_parks(db, user)
    queues: list[str] = []
    seen: set[str] = set()
    for park in parks:
        q = (park.tracker_queue or "").strip()
        if q and q not in seen:
            seen.add(q)
            queues.append(q)
    if not queues:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no_tracker_parks",
        )

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    merged: list[dict] = []
    keys: set[str] = set()
    try:
        for queue in queues:
            for item in tracker_client.search_robot_tickets(
                token=token, queue=queue, query=query
            ):
                if item["key"] in keys:
                    continue
                keys.add(item["key"])
                merged.append(item)
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    sorted_items = tracker_filters.sort_issues_oldest_first(merged)
    return RobotTicketsOut(
        query=query,
        items=[_blocker_out(item) for item in sorted_items],
    )
```

Include router in `main.py`.

- [ ] **Step 4: Run — expect PASS**

```bash
cd apps/api && source .venv/bin/activate && pytest tests/test_operator_robots.py -v
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/robopark_api/routers/operator_robots.py \
  apps/api/src/robopark_api/main.py \
  apps/api/tests/test_operator_robots.py
git commit -m "$(cat <<'EOF'
feat(api): add multi-queue operator robot search

EOF
)"
```

---

### Task 5: Operator now-report route

**Files:**
- Modify: `apps/api/src/robopark_api/schemas.py`
- Create: `apps/api/src/robopark_api/routers/operator_report.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Create: `apps/api/tests/test_operator_report.py`

**Interfaces:**
- Consumes: `get_operator_parks`, `require_operator_park`, `collect_park_metrics`, cache helpers, `get_tracker_token`
- Produces:
  - `ParkMetricsOut(park_id, park_name, park_tag, metrics: dict[str, int])`
  - `SkippedParkOut(park_id, park_name, reason: str)`
  - `NowReportOut(generated_at: str, scope: str, totals: dict[str, int], parks: list[ParkMetricsOut], skipped_parks: list[SkippedParkOut])`
  - `GET /operator/now-report?park_id=` (optional)
- Totals keys: `blocker`, `backlog`, `in_transit`, `queued`, `waiting_team`, `waiting_parts`, `arrived`, `done`  
  Map park `open_blockers` → totals `blocker`
- Skip reasons: `reports_disabled`, `no_tracker_queue`
- Cache key: `now:{operator_id}:{park_id|all}`
- `generated_at`: ISO8601 with `Europe/Moscow` tz
- Zero assigned parks → `409` `no_report_parks`
- All skipped but operator has parks → `200` with empty `parks` + `skipped_parks` + zero totals
- Dedupe totals by `(queue, tag)` so the same tag is not double-counted

- [ ] **Step 1: Write failing tests**

```python
# apps/api/tests/test_operator_report.py
from unittest.mock import patch

from conftest import login_as
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import tracker_metrics


def seed_op_report(db_session):
    tracker_metrics.clear_metrics_cache()
    good = Park(
        name="Alpha",
        tag="Alpha",
        is_active=True,
        tracker_queue="ROBOPARK",
        feature_reports=True,
    )
    disabled = Park(
        name="Beta",
        tag="Beta",
        is_active=True,
        tracker_queue="ROBOPARK",
        feature_reports=False,
    )
    db_session.add_all([good, disabled])
    db_session.flush()
    op = User(
        username="op-report",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db_session.add(op)
    db_session.flush()
    db_session.add_all(
        [
            UserPark(user_id=op.id, park_id=good.id),
            UserPark(user_id=op.id, park_id=disabled.id),
        ]
    )
    db_session.commit()
    db_session.refresh(good)
    return op, good, disabled


def test_now_report_skips_disabled_and_aggregates(client, db_session, seed_royal):
    seed_op_report(db_session)
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-report", "secret")

    fake_metrics = {
        "open_blockers": 3,
        "backlog": 1,
        "in_transit": 0,
        "queued": 2,
        "waiting_team": 0,
        "waiting_parts": 1,
        "arrived": 4,
        "done": 5,
    }
    with patch(
        "robopark_api.services.tracker_metrics.collect_park_metrics",
        return_value=fake_metrics,
    ):
        r = client.get("/operator/now-report")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "all"
    assert body["totals"]["blocker"] == 3
    assert body["totals"]["done"] == 5
    assert len(body["parks"]) == 1
    assert body["parks"][0]["park_tag"] == "Alpha"
    assert any(s["reason"] == "reports_disabled" for s in body["skipped_parks"])


def test_now_report_forbidden_foreign_park(client, db_session, seed_royal):
    seed_op_report(db_session)
    foreign = Park(name="Z", tag="Z", is_active=True, tracker_queue="ROBOPARK")
    db_session.add(foreign)
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-report", "secret")
    r = client.get(f"/operator/now-report?park_id={foreign.id}")
    assert r.status_code == 403


def test_now_report_uses_cache(client, db_session, seed_royal):
    seed_op_report(db_session)
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-report", "secret")
    with patch(
        "robopark_api.services.tracker_metrics.collect_park_metrics",
        return_value={
            "open_blockers": 1,
            "backlog": 0,
            "in_transit": 0,
            "queued": 0,
            "waiting_team": 0,
            "waiting_parts": 0,
            "arrived": 0,
            "done": 0,
        },
    ) as mocked:
        assert client.get("/operator/now-report").status_code == 200
        assert client.get("/operator/now-report").status_code == 200
        assert mocked.call_count == 1


def test_now_report_no_parks(client, db_session, seed_royal):
    op = User(
        username="op-none",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db_session.add(op)
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-none", "secret")
    r = client.get("/operator/now-report")
    assert r.status_code == 409
    assert r.json()["detail"] == "no_report_parks"
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add schemas**

```python
class ParkMetricsOut(BaseModel):
    park_id: int
    park_name: str
    park_tag: str
    metrics: dict[str, int]


class SkippedParkOut(BaseModel):
    park_id: int
    park_name: str
    reason: str


class NowReportOut(BaseModel):
    generated_at: str
    scope: str
    totals: dict[str, int]
    parks: list[ParkMetricsOut]
    skipped_parks: list[SkippedParkOut]
```

- [ ] **Step 4: Implement `operator_report.py`**

```python
# apps/api/src/robopark_api/routers/operator_report.py
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import (
    get_operator_parks,
    require_approved_operator,
    require_operator_park,
)
from robopark_api.models import Park, User
from robopark_api.schemas import NowReportOut, ParkMetricsOut, SkippedParkOut
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_client, tracker_metrics

router = APIRouter(prefix="/operator", tags=["operator-report"])

EMPTY_TOTALS = {
    "blocker": 0,
    "backlog": 0,
    "in_transit": 0,
    "queued": 0,
    "waiting_team": 0,
    "waiting_parts": 0,
    "arrived": 0,
    "done": 0,
}

TOTAL_FROM_PARK = {
    "blocker": "open_blockers",
    "backlog": "backlog",
    "in_transit": "in_transit",
    "queued": "queued",
    "waiting_team": "waiting_team",
    "waiting_parts": "waiting_parts",
    "arrived": "arrived",
    "done": "done",
}


@router.get("/now-report", response_model=NowReportOut)
def operator_now_report(
    park_id: int | None = Query(default=None),
    user: User = Depends(require_approved_operator),
    db: Session = Depends(get_db),
) -> NowReportOut:
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    if park_id is not None:
        parks = [require_operator_park(park_id, db, user)]
        scope = "park"
        cache_key = f"now:{user.id}:{park_id}"
    else:
        parks = get_operator_parks(db, user)
        scope = "all"
        cache_key = f"now:{user.id}:all"

    if not parks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no_report_parks",
        )

    cached = tracker_metrics.get_cached_now_report(cache_key)
    if cached is not None:
        return NowReportOut(**cached)

    totals = dict(EMPTY_TOTALS)
    park_rows: list[ParkMetricsOut] = []
    skipped: list[SkippedParkOut] = []
    counted_keys: set[tuple[str, str]] = set()

    for park in parks:
        if not park.feature_reports:
            skipped.append(
                SkippedParkOut(
                    park_id=park.id, park_name=park.name, reason="reports_disabled"
                )
            )
            continue
        queue = (park.tracker_queue or "").strip()
        if not queue:
            skipped.append(
                SkippedParkOut(
                    park_id=park.id, park_name=park.name, reason="no_tracker_queue"
                )
            )
            continue
        try:
            metrics = tracker_metrics.collect_park_metrics(
                token=token, queue=queue, tag=park.tag
            )
        except tracker_client.TrackerError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc

        park_rows.append(
            ParkMetricsOut(
                park_id=park.id,
                park_name=park.name,
                park_tag=park.tag,
                metrics=metrics,
            )
        )
        dedupe = (queue, park.tag)
        if dedupe in counted_keys:
            continue
        counted_keys.add(dedupe)
        for total_key, metric_key in TOTAL_FROM_PARK.items():
            totals[total_key] += int(metrics.get(metric_key) or 0)

    payload = {
        "generated_at": datetime.now(ZoneInfo("Europe/Moscow")).isoformat(),
        "scope": scope,
        "totals": totals,
        "parks": [p.model_dump() for p in park_rows],
        "skipped_parks": [s.model_dump() for s in skipped],
    }
    tracker_metrics.set_cached_now_report(cache_key, payload)
    return NowReportOut(**payload)
```

Include router in `main.py`.

- [ ] **Step 5: Run — expect PASS**

```bash
cd apps/api && source .venv/bin/activate && pytest tests/test_operator_report.py tests/test_operator_blockers.py tests/test_operator_robots.py -v
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/robopark_api/schemas.py \
  apps/api/src/robopark_api/routers/operator_report.py \
  apps/api/src/robopark_api/main.py \
  apps/api/tests/test_operator_report.py
git commit -m "$(cat <<'EOF'
feat(api): add GET /operator/now-report with metrics cache

EOF
)"
```

---

### Task 6: Web API client + i18n error strings

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/i18n/ru.ts`
- Modify: `apps/web/src/i18n/errors.ts` (only if new status mapping needed; prefer `details` map)

**Interfaces:**
- Produces:
  - Types `OperatorBlockers`, `NowReport`
  - `api.operatorBlockers(parkId, status?)`
  - `api.operatorRobotTickets(query)`
  - `api.operatorNowReport(parkId?)`
- `ru.errors.details` keys: `blockers_disabled_for_park`, `no_tracker_parks`, `no_report_parks`, `tracker_token_not_configured`

- [ ] **Step 1: Add types + methods to `api.ts`**

```typescript
export type OperatorBlockers = {
  park_id: number
  park_tag: string
  status: string
  counts: Record<string, number>
  items: Blocker[]
}

export type NowReport = {
  generated_at: string
  scope: string
  totals: Record<string, number>
  parks: Array<{
    park_id: number
    park_name: string
    park_tag: string
    metrics: Record<string, number>
  }>
  skipped_parks: Array<{ park_id: number; park_name: string; reason: string }>
}

// inside api object:
operatorBlockers: (parkId: number, status = 'all') =>
  request<OperatorBlockers>(
    `/operator/blockers?park_id=${parkId}&status=${encodeURIComponent(status)}`,
  ),
operatorRobotTickets: (query: string) =>
  request<{ query: string; items: Blocker[] }>(
    `/operator/robots/${encodeURIComponent(query)}/tickets`,
  ),
operatorNowReport: (parkId?: number) =>
  request<NowReport>(
    parkId == null
      ? '/operator/now-report'
      : `/operator/now-report?park_id=${parkId}`,
  ),
```

- [ ] **Step 2: Extend `ru.ts` `errors.details`**

```typescript
blockers_disabled_for_park:
  'Блокеры отключены для этого парка (очередь или feature_blockers).',
no_tracker_parks: 'Нет назначенных парков с очередью Tracker.',
no_report_parks:
  'Нет парков для отчёта — назначьте парк или включите feature_reports.',
tracker_token_not_configured:
  'Tracker не настроен — попросите администратора указать OAuth-токен.',
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/api.ts apps/web/src/i18n/ru.ts apps/web/src/i18n/errors.ts
git commit -m "$(cat <<'EOF'
feat(web): add operator Tracker API client and error strings

EOF
)"
```

---

### Task 7: Operator hub + parks page + routing

**Files:**
- Create: `apps/web/src/pages/OperatorParks.tsx` (move content from current `Operator.tsx`)
- Rewrite: `apps/web/src/pages/Operator.tsx` as hub
- Modify: `apps/web/src/App.tsx`

**Critical routing fix:** `RequirePath` requires `pathForUser(user) === path`, so `/operator/parks` redirects away. Add:

```tsx
function RequireApprovedOperator({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'operator' || user.access_status !== 'approved') {
    return <Navigate to={pathForUser(user)} replace />
  }
  return children
}
```

Keep `RequirePath` only for `/operator/pending` and `/operator/rejected`.

- [ ] **Step 1: Move parks UI into `OperatorParks.tsx`**

Copy current `Operator.tsx` content. Add `backTo="/operator"` on `PageShell`. Keep park request forms unchanged.

- [ ] **Step 2: Rewrite `Operator.tsx` hub** (pattern: `Mechanic.tsx`)

```tsx
const tools = [
  {
    href: '/operator/blockers',
    title: 'Блокеры',
    text: 'Открытые blocker по выбранному парку с фильтрами статуса.',
  },
  {
    href: '/operator/robot-search',
    title: 'Поиск робота',
    text: 'Тикеты по номеру робота или ключу задачи по вашим паркам.',
  },
  {
    href: '/operator/now-report',
    title: 'Сейчас по Tracker',
    text: 'Сводка: блокеры, бэклог, статусы, сегодня пришло / сделано.',
  },
  {
    href: '/operator/parks',
    title: 'Мои парки',
    text: 'Заявки на доступ и история запросов.',
  },
] as const
```

- [ ] **Step 3: Wire routes in `App.tsx`**

```tsx
<Route path="/operator" element={<RequireApprovedOperator><Operator /></RequireApprovedOperator>} />
<Route path="/operator/parks" element={<RequireApprovedOperator><OperatorParks /></RequireApprovedOperator>} />
<Route path="/operator/blockers" element={<RequireApprovedOperator><OperatorBlockers /></RequireApprovedOperator>} />
<Route path="/operator/robot-search" element={<RequireApprovedOperator><OperatorRobotSearch /></RequireApprovedOperator>} />
<Route path="/operator/now-report" element={<RequireApprovedOperator><OperatorNowReport /></RequireApprovedOperator>} />
```

If Task 8/9 pages are not created yet, create thin placeholders in this task that export the named components (title-only shells), then fill them in Tasks 8–9.

- [ ] **Step 4: Build**

```bash
cd apps/web && npm run build
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/pages/Operator.tsx \
  apps/web/src/pages/OperatorParks.tsx \
  apps/web/src/App.tsx
git commit -m "$(cat <<'EOF'
feat(web): operator hub and parks route split

EOF
)"
```

---

### Task 8: Operator blockers + robot search pages

**Files:**
- Create/fill: `apps/web/src/pages/OperatorBlockers.tsx`
- Create/fill: `apps/web/src/pages/OperatorRobotSearch.tsx`

**OperatorBlockers:**
- Load `api.operatorParks()` for park `<select>`
- Default first assigned park
- Status filter buttons identical to `MechanicTasks` (`FILTERS` + `taskFilterLabel`)
- `api.operatorBlockers(parkId, status)` on park/status change
- Card list with Tracker URL links; `backTo="/operator"`
- Errors via `mapApiError`

**OperatorRobotSearch:**
- Mirror `MechanicRobotSearch.tsx`; call `api.operatorRobotTickets`

- [ ] **Step 1: Implement both pages**

Park selector sketch:

```tsx
<select
  aria-label="Парк"
  value={parkId}
  onChange={(e) => setParkId(Number(e.target.value))}
>
  {parks.map((p) => (
    <option key={p.id} value={p.id}>{p.name} ({p.tag})</option>
  ))}
</select>
```

- [ ] **Step 2: Build**

```bash
cd apps/web && npm run build
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/pages/OperatorBlockers.tsx \
  apps/web/src/pages/OperatorRobotSearch.tsx \
  apps/web/src/App.tsx
git commit -m "$(cat <<'EOF'
feat(web): operator blockers and robot search pages

EOF
)"
```

---

### Task 9: Now-report page + README + full verification

**Files:**
- Create/fill: `apps/web/src/pages/OperatorNowReport.tsx`
- Modify: `README.md`

**OperatorNowReport:**
- Select: «Все парки» + assigned parks
- Button «Обновить» → `api.operatorNowReport(parkId | undefined)`
- Panels: totals, per-park metrics, skipped parks
- Reason labels: `reports_disabled` → «отчёты выключены»; `no_tracker_queue` → «нет очереди Tracker»

- [ ] **Step 1: Implement page**

```tsx
<PageShell
  backTo="/operator"
  title="Сейчас по Tracker"
  subtitle="Живой срез открытых blocker по вашим паркам."
>
  {/* filter + refresh */}
  <Panel title="Итого">{/* totals */}</Panel>
  <Panel title="По паркам">{/* parks */}</Panel>
  {/* skipped panel if any */}
</PageShell>
```

- [ ] **Step 2: README** — short note: operator hub tools, need Tracker token + park `tracker_queue` / feature flags

- [ ] **Step 3: Full verification**

```bash
cd apps/api && source .venv/bin/activate && pytest -v
cd apps/web && npm run build
```

Expected: all API tests green; web build green.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/pages/OperatorNowReport.tsx README.md
git commit -m "$(cat <<'EOF'
feat(web): operator now-report page and Phase 4 docs note

EOF
)"
```

---

## Self-review (spec coverage)

| Spec requirement | Task |
|------------------|------|
| `get_operator_parks` / `require_operator_park` | 1 |
| `tracker_metrics` + count client + 60s cache | 2, 5 |
| `GET /operator/blockers` + filters + ACL + flags | 3 |
| `GET /operator/robots/{query}/tickets` merge/dedupe | 4 |
| `GET /operator/now-report` totals + skipped + cache | 5 |
| Hub `/operator` + parks at `/operator/parks` | 7 |
| Blockers / robot-search / now-report pages | 8, 9 |
| Russian UI + error mapping | 6–9 |
| No Alembic | none needed |
| No old-bot import | Global + Task 2 |
| Tests: ACL, flags, token, mocked upstream | 3–5 |
| Success criteria 1–8 | Tasks 1–9 |

## Out of plan (follow-on)

- SLA ≥12ч, untagged blockers, mechanic report inbox
- Shared `BlockerList` extract
- Operator Emergency

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-22-robopark-phase4-operator-tools.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks
2. **Inline Execution** — execute tasks in this session with checkpoints

Which approach?
