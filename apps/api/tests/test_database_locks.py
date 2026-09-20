from types import SimpleNamespace

from robopark_api.services.database_locks import database_idempotency_lock


def test_postgresql_idempotency_lock_keeps_dedicated_transaction_through_request_rebind():
    """The advisory lock must not live on a RequestSession connection."""
    events: list[str] = []

    class Transaction:
        def __enter__(self):
            events.append("transaction-open")
            return self

        def __exit__(self, *_args):
            events.append("transaction-closed")

    class Connection:
        def __enter__(self):
            events.append("connection-open")
            return self

        def __exit__(self, *_args):
            events.append("connection-closed")

        def begin(self):
            return Transaction()

        def scalar(self, _statement, _parameters):
            events.append("try-lock")
            return True

    class Engine:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            return Connection()

    class RequestSession:
        def __init__(self):
            self.releases = 0

        def get_bind(self):
            return Engine()

        def release_connection(self):
            self.releases += 1
            events.append("request-rebound")

    request = RequestSession()
    with database_idempotency_lock(request, "receipt-key"):
        request.release_connection()
        assert events == ["connection-open", "transaction-open", "try-lock", "request-rebound"]

    assert request.releases == 1
    assert events[-2:] == ["transaction-closed", "connection-closed"]
