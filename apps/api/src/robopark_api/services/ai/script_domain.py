"""Shared invariants for script mutations from HTTP and assistant tools."""

import time

from sqlalchemy import select

from robopark_api.ai_models import AIAutomation


def disable_dependencies(db, script_id: str) -> None:
    """Disable every rule whose reviewed script revision is being invalidated."""
    # JSON extraction differs across SQLite/Postgres; this catalog is bounded.
    for rule in db.scalars(select(AIAutomation)):
        if rule.action.get("script_id") == script_id:
            rule.enabled = False
            rule.enabled_at = None
            rule.revision += 1
            rule.updated_at = time.time()
