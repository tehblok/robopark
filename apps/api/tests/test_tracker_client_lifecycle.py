from __future__ import annotations

import gc
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from yandex_tracker_client import TrackerClient

from robopark_api.services import tracker_client


@pytest.fixture(autouse=True)
def isolated_clients(monkeypatch):
    monkeypatch.setattr(tracker_client, "_CLIENTS", {})
    yield
    tracker_client.clear_tracker_clients()
    gc.collect()


def test_token_rotations_release_retired_sdk_clients_without_local_admin_reset():
    retired = []
    for index in range(100):
        client = tracker_client._client(f"synthetic-token-{index}")
        retired.append(weakref.ref(client))
    gc.collect()
    assert all(reference() is None for reference in retired[:-1])
    assert retired[-1]() is client
    assert tracker_client._client("synthetic-token-99") is client


def test_retired_session_closes_only_after_live_sdk_resources_are_released(monkeypatch):
    closed = []
    original_close = requests.Session.close

    def close(session):
        closed.append(id(session))
        original_close(session)

    monkeypatch.setattr(requests.Session, "close", close)
    client = tracker_client._client("synthetic-before")
    session_id = id(client._connection.session)
    collection = client.issues
    del client
    tracker_client._client("synthetic-after")
    gc.collect()
    assert session_id not in closed
    assert collection._connection.session.headers["Authorization"] == "OAuth synthetic-before"
    del collection
    gc.collect()
    assert closed.count(session_id) == 1


def test_explicit_clear_retires_but_does_not_close_an_in_use_session(monkeypatch):
    closed = []
    original_close = requests.Session.close

    def close(session):
        closed.append(id(session))
        original_close(session)

    monkeypatch.setattr(requests.Session, "close", close)
    client = tracker_client._client("synthetic-active")
    session_id = id(client._connection.session)
    tracker_client.clear_tracker_clients()
    gc.collect()
    assert session_id not in closed
    del client
    gc.collect()
    assert closed.count(session_id) == 1


def test_simultaneous_requests_reuse_a_single_sdk_client(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def construct(**kwargs):
        calls.append(kwargs)
        started.set()
        assert release.wait(3)
        return TrackerClient(**kwargs)

    monkeypatch.setattr(tracker_client, "_import_startrek", lambda: construct)
    with ThreadPoolExecutor(max_workers=8) as executor:
        first = executor.submit(tracker_client._client, "synthetic-shared")
        assert started.wait(3)
        remaining = [executor.submit(tracker_client._client, "synthetic-shared") for _ in range(7)]
        release.set()
        values = [first.result(timeout=3), *(future.result(timeout=3) for future in remaining)]
    assert len({id(value) for value in values}) == 1
    assert len(calls) == 1
