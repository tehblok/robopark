"""Short-lived caches over the Tracker (Startrek) client.

Multiple mechanics and operators typically open the same task board or
robot page within seconds of each other. Each worker reuses short-lived
results; JSON-safe lookup results may additionally be shared across workers.
Issue objects retain live SDK resources and must stay in process memory.

Each wrapper preserves the signature of the underlying ``tracker_client``
function so router code stays a one-line swap. Cache keys never include
the OAuth token because Robopark uses a single platform token.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from robopark_api.services import tracker_client
from robopark_api.services.live_merge import LiveMergeTimeout, LiveMergeUpstreamError
from robopark_api.services.response_cache import ResponseCache


class _TrackerCache[T](ResponseCache[T]):
    """Keep shared cache failures within the Tracker facade's error contract."""

    def get_or_load(self, key: str, loader: Callable[[], T]) -> T:
        try:
            return super().get_or_load(key, loader)
        except (LiveMergeTimeout, LiveMergeUpstreamError) as exc:
            raise tracker_client.TrackerError("Tracker shared request failed") from exc


_TTL_ISSUES = 20.0
_TTL_ISSUE = 30.0
_TTL_COMMENTS = 15.0
_TTL_TRANSITIONS = 60.0
_TTL_BLOCKERS = 20.0
_TTL_ROBOT_TICKETS = 30.0
_TTL_COUNTS = 20.0


def _shared_issue_payload(
    value: list[dict[str, Any]] | dict[str, Any] | None,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """Project live SDK resources out of the cross-process JSON cache."""
    if value is None:
        return None
    if isinstance(value, list):
        return [
            {key: item for key, item in issue.items() if key != "_tracker_resource"}
            for issue in value
        ]
    return {key: item for key, item in value.items() if key != "_tracker_resource"}


_issues_cache: ResponseCache[list[dict[str, Any]]] = _TrackerCache(
    _TTL_ISSUES, name="tracker.issues", shared_payload=_shared_issue_payload
)
_issue_cache: ResponseCache[dict[str, Any] | None] = _TrackerCache(
    _TTL_ISSUE, name="tracker.issue", shared_payload=_shared_issue_payload
)
_comments_cache: ResponseCache[list[dict[str, Any]]] = _TrackerCache(
    _TTL_COMMENTS, name="tracker.comments"
)
_transitions_cache: ResponseCache[list[dict[str, Any]]] = _TrackerCache(
    _TTL_TRANSITIONS, name="tracker.transitions"
)
_blockers_cache: ResponseCache[list[dict[str, Any]]] = _TrackerCache(
    _TTL_BLOCKERS, name="tracker.blockers", shared_payload=_shared_issue_payload
)
_robot_tickets_cache: ResponseCache[list[dict[str, Any]]] = _TrackerCache(
    _TTL_ROBOT_TICKETS, name="tracker.robot_tickets", shared_payload=_shared_issue_payload
)
_count_cache: ResponseCache[int] = _TrackerCache(_TTL_COUNTS, name="tracker.counts")
_metrics_cache: ResponseCache[dict[str, int]] = _TrackerCache(_TTL_COUNTS, name="tracker.metrics")

_ALL_CACHES: tuple[ResponseCache[Any], ...] = (
    _issues_cache,
    _issue_cache,
    _comments_cache,
    _transitions_cache,
    _blockers_cache,
    _robot_tickets_cache,
    _count_cache,
    _metrics_cache,
)


def search_issues(
    *,
    token: str,
    query: str,
    filter_open: bool = True,
    order: list[str] | None = None,
) -> list[dict[str, Any]]:
    key = f"{filter_open}|{order or []}|{query}"
    return _issues_cache.get_or_load(
        key,
        lambda: tracker_client.search_issues(
            token=token, query=query, filter_open=filter_open, order=order
        ),
    )


def get_issue(*, token: str, key: str) -> dict[str, Any] | None:
    return _issue_cache.get_or_load(
        key,
        lambda: tracker_client.get_issue(token=token, key=key),
    )


def list_comments(*, token: str, key: str) -> list[dict[str, Any]]:
    return _comments_cache.get_or_load(
        key,
        lambda: tracker_client.list_comments(token=token, key=key),
    )


def list_transitions(*, token: str, key: str) -> list[dict[str, Any]]:
    return _transitions_cache.get_or_load(
        key,
        lambda: tracker_client.list_transitions(token=token, key=key),
    )


def fetch_park_blockers(
    *,
    token: str,
    queue: str,
    park_tag: str,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> list[dict[str, Any]]:
    ck = f"{queue}|{park_tag}|{priority}|{issue_type or ''}"
    return _blockers_cache.get_or_load(
        ck,
        lambda: tracker_client.fetch_park_blockers(
            token=token,
            queue=queue,
            park_tag=park_tag,
            priority=priority,
            issue_type=issue_type,
        ),
    )


def search_robot_tickets(*, token: str, queue: str, query: str) -> list[dict[str, Any]]:
    ck = f"{queue}|{query}"
    return _robot_tickets_cache.get_or_load(
        ck,
        lambda: tracker_client.search_robot_tickets(token=token, queue=queue, query=query),
    )


def count_issues(*, token: str, query: str) -> int:
    """Merge identical Startrek count queries across cabinets (not per user)."""
    return _count_cache.get_or_load(
        query,
        lambda: tracker_client.count_issues(token=token, query=query),
    )


def collect_park_metrics(
    *,
    token: str,
    queue: str,
    tag: str,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> dict[str, int]:
    """Park KPI bundle keyed by park query shape, never ``user_id``."""
    from robopark_api.services import tracker_metrics as metrics_svc

    key = f"{queue}|{tag}|{priority}|{issue_type or ''}"
    return _metrics_cache.get_or_load(
        key,
        lambda: metrics_svc.collect_park_metrics(
            token=token,
            queue=queue,
            tag=tag,
            priority=priority,
            issue_type=issue_type,
        ),
    )


def invalidate_issue(key: str) -> None:
    """Drop cached artefacts around a single issue and any list that might contain it."""
    _issue_cache.invalidate(key)
    tracker_client.invalidate_issue_status_history(key)
    _comments_cache.invalidate(key)
    _transitions_cache.invalidate(key)
    _issues_cache.clear()
    _blockers_cache.clear()
    _robot_tickets_cache.clear()
    _count_cache.clear()
    _metrics_cache.clear()


def invalidate_all_lists() -> None:
    """Force refetch of every list-style Tracker response (e.g. after token change)."""
    _issues_cache.clear()
    _blockers_cache.clear()
    _robot_tickets_cache.clear()
    _count_cache.clear()
    _metrics_cache.clear()


def clear_all() -> None:
    """Drop every cached Tracker response (e.g. after a token rotation)."""
    for cache in _ALL_CACHES:
        cache.clear()
    tracker_client.clear_issue_status_history_cache()


def clear_all_for_tests() -> None:
    """Alias of :func:`clear_all` used in test setup for readability."""
    clear_all()
