"""Post-commit revocation hints; bounded failures fall back to the 20s host lease."""

import asyncio
import threading

from sqlalchemy import event
from sqlalchemy.orm import Session

from robopark_api.config import get_settings

from .broker import BrokerClient

_slots = threading.BoundedSemaphore(2)


@event.listens_for(Session, "after_commit")
def committed(db):
    ids = db.info.pop("terminal_revocations", set())
    settings = get_settings()
    if not ids or not settings.terminal_broker_socket or not _slots.acquire(blocking=False):
        return

    async def stop():
        broker = BrokerClient(settings)
        await asyncio.wait_for(
            asyncio.gather(
                *(broker.terminate(sid, "revoked") for sid in list(ids)[:16]),
                return_exceptions=True,
            ),
            10,
        )

    def run():
        try:
            asyncio.run(stop())
        except Exception:
            pass
        finally:
            _slots.release()

    threading.Thread(target=run, name="terminal-revoke", daemon=True).start()


@event.listens_for(Session, "after_rollback")
def rolled_back(db):
    db.info.pop("terminal_revocations", None)
