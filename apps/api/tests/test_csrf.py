import pytest

from conftest import login_as


@pytest.mark.parametrize(
    "origin",
    ["https://attacker.ru.tuna.am", "null", "http://testserver.attacker.example"],
)
def test_foreign_origin_cannot_log_out_cookie_session(client, seed_royal, origin):
    assert login_as(client, "royal", "secret").status_code == 204
    response = client.post("/auth/logout", headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json()["detail"] == "untrusted_origin"
    assert client.get("/auth/me").status_code == 200


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_browser_without_origin_cannot_mutate_from_another_site(client, seed_royal, site):
    assert login_as(client, "royal", "secret").status_code == 204
    response = client.post("/auth/logout", headers={"Sec-Fetch-Site": site})
    assert response.status_code == 403
    assert client.get("/auth/me").status_code == 200


def test_foreign_referrer_is_rejected_when_origin_is_absent(client, seed_royal):
    assert login_as(client, "royal", "secret").status_code == 204
    response = client.post("/auth/logout", headers={"Referer": "https://attacker.example/form"})
    assert response.status_code == 403
    assert client.get("/auth/me").status_code == 200


@pytest.mark.parametrize("origin", ["http://testserver", "http://localhost:5173"])
def test_same_origin_and_explicit_development_origin_can_mutate(client, seed_royal, origin):
    assert login_as(client, "royal", "secret").status_code == 204
    assert client.post("/auth/logout", headers={"Origin": origin}).status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_forged_forwarded_host_does_not_authorize_origin(client, seed_royal):
    assert login_as(client, "royal", "secret").status_code == 204
    response = client.post(
        "/auth/logout",
        headers={"Origin": "https://attacker.example", "X-Forwarded-Host": "attacker.example"},
    )
    assert response.status_code == 403


def test_login_itself_rejects_foreign_origin(client, seed_royal):
    response = client.post(
        "/auth/login",
        json={"username": "royal", "password": "secret"},
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403
    assert client.get("/auth/me").status_code == 401


def test_local_host_port_is_part_of_same_origin(client, seed_royal):
    headers = {"Host": "127.0.0.1:8080", "Origin": "http://127.0.0.1:8080"}
    response = client.post(
        "/auth/login", json={"username": "royal", "password": "secret"}, headers=headers
    )
    assert response.status_code == 204
