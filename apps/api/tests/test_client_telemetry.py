from conftest import login_as


def test_client_telemetry_accepts_only_bounded_aggregate_metrics(client, seed_mechanic):
    login_as(client, seed_mechanic.username, "secret")
    response = client.post(
        "/client-telemetry",
        json={
            "metrics": [
                {"name": "startup_ms", "value": 840.5},
                {"name": "queue_length", "value": 3},
            ]
        },
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": 2}

    protected = client.post(
        "/client-telemetry",
        json={
            "metrics": [
                {"name": "startup_ms", "value": 10, "task_text": "secret"},
            ]
        },
    )
    arbitrary = client.post(
        "/client-telemetry",
        json={
            "metrics": [
                {"name": "robot_photo", "value": 1},
            ]
        },
    )
    assert protected.status_code == 422
    assert arbitrary.status_code == 422


def test_client_telemetry_is_bounded_and_requires_authentication(client, seed_mechanic):
    assert client.post("/client-telemetry", json={"metrics": []}).status_code == 401
    login_as(client, seed_mechanic.username, "secret")
    response = client.post(
        "/client-telemetry",
        json={"metrics": [{"name": "retry_count", "value": index} for index in range(21)]},
    )
    assert response.status_code == 422
