from robopark_api.services import tracker_client


def test_list_comments_parses_payload(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "id": "1",
                    "text": "ok",
                    "createdBy": {"display": "admin"},
                    "createdAt": "2026-01-01T00:00:00Z",
                }
            ]

    class DummyClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(tracker_client.httpx, "Client", DummyClient)
    comments = tracker_client.list_comments(token="t", key="ROBOPARK-1")
    assert comments[0]["author"] == "admin"
    assert comments[0]["text"] == "ok"
