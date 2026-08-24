from robopark_api.services import tracker_client


def test_list_comments_parses_startrek_objects(monkeypatch):
    class Author:
        display = "admin"

    class Comment:
        id = "1"
        text = "ok"
        createdBy = Author()
        createdAt = "2026-01-01T00:00:00Z"

    class Comments:
        def get_all(self):
            return [Comment()]

    class Issue:
        comments = Comments()

    class Issues:
        def __getitem__(self, _key):
            return Issue()

    class FakeClient:
        issues = Issues()

    monkeypatch.setattr(
        "robopark_api.services.tracker_client._client",
        lambda _token: FakeClient(),
    )
    monkeypatch.setattr(
        "robopark_api.services.tracker_client.call_with_retry",
        lambda fn, **_kwargs: fn(),
    )

    comments = tracker_client.list_comments(token="t", key="ROBOPARK-1")
    assert comments[0]["author"] == "admin"
    assert comments[0]["text"] == "ok"
