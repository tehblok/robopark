from robopark_api.services import tracker_client


def test_list_comments_parses_startrek_objects(monkeypatch):
    class Author:
        display = "admin"
        login = "admin"

    class Attachment:
        id = "att-1"
        name = "photo.jpg"
        content = "https://st-api.yandex-team.ru/v2/issues/ROBOPARK-1/attachments/att-1"
        mimetype = "image/jpeg"
        size = 100

    class Comment:
        id = "1"
        text = "ok"
        createdBy = Author()
        createdAt = "2026-01-01T00:00:00Z"
        attachments = [Attachment()]

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
    assert comments[0]["attachments"][0]["name"] == "photo.jpg"
    assert comments[0]["attachments"][0]["url"].endswith("/att-1")


def test_upload_temp_attachment_posts_multipart(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 201

        @staticmethod
        def json():
            return {
                "id": "temp-55",
                "name": "photo.jpg",
                "self": "https://st-api.yandex-team.ru/v2/attachments/temp-55",
                "mimetype": "image/jpeg",
                "size": 4,
            }

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, *, headers, files):
            captured["url"] = url
            captured["headers"] = headers
            captured["files"] = files
            return FakeResponse()

    monkeypatch.setattr(
        "robopark_api.services.tracker_client.httpx.Client",
        FakeClient,
    )
    monkeypatch.setattr(
        "robopark_api.services.tracker_client.call_with_retry",
        lambda fn, **_kwargs: fn(),
    )

    attachment_id = tracker_client.upload_temp_attachment(
        token="secret",
        filename="photo.jpg",
        content=b"data",
        content_type="image/jpeg",
    )

    assert attachment_id == "temp-55"
    assert captured["url"] == "https://st-api.yandex-team.ru/v2/attachments/"
    assert captured["headers"]["Authorization"] == "OAuth secret"
    assert captured["files"]["file"][0] == "photo.jpg"


def test_add_comment_with_attachment_ids(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 201

        @staticmethod
        def json():
            return {"id": "c-1", "text": "Фото\nAlpha / mech / op"}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(
        "robopark_api.services.tracker_client.httpx.Client",
        FakeClient,
    )
    monkeypatch.setattr(
        "robopark_api.services.tracker_client.call_with_retry",
        lambda fn, **_kwargs: fn(),
    )

    result = tracker_client.add_comment(
        token="secret",
        key="ROBOPARK-1",
        text="Фото\nAlpha / mech / op",
        attachment_ids=["temp-55"],
    )

    assert result["id"] == "c-1"
    assert captured["url"] == "https://st-api.yandex-team.ru/v2/issues/ROBOPARK-1/comments"
    assert captured["json"]["attachmentIds"] == ["temp-55"]


def test_add_attachment_posts_multipart(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 201

        @staticmethod
        def json():
            return {
                "id": "55",
                "name": "photo.jpg",
                "self": "https://st-api.yandex-team.ru/v2/issues/ROBOPARK-1/attachments/55",
                "mimetype": "image/jpeg",
                "size": 4,
            }

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, *, headers, files):
            captured["url"] = url
            captured["headers"] = headers
            captured["files"] = files
            return FakeResponse()

    monkeypatch.setattr(
        "robopark_api.services.tracker_client.httpx.Client",
        FakeClient,
    )
    monkeypatch.setattr(
        "robopark_api.services.tracker_client.call_with_retry",
        lambda fn, **_kwargs: fn(),
    )

    result = tracker_client.add_attachment(
        token="secret",
        key="ROBOPARK-1",
        filename="photo.jpg",
        content=b"data",
        content_type="image/jpeg",
    )

    assert result["name"] == "photo.jpg"
    assert captured["url"] == "https://st-api.yandex-team.ru/v2/issues/ROBOPARK-1/attachments"
    assert captured["headers"]["Authorization"] == "OAuth secret"
    assert captured["files"]["file"][0] == "photo.jpg"


def test_normalize_attachment_content_type_from_extension():
    content = b"\xff\xd8\xff\xe0"
    assert (
        tracker_client.normalize_attachment_content_type(
            filename="IMG_001.heic",
            content=content,
            content_type="application/octet-stream",
        )
        == "image/heic"
    )
    assert (
        tracker_client.normalize_attachment_content_type(
            filename="photo.jpg",
            content=content,
            content_type="",
        )
        == "image/jpeg"
    )
