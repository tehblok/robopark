from types import SimpleNamespace

import pytest
from robopark_host import ai_acceptance


def response():
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Ошибка осталась."},
            }
        ],
        "usage": {"completion_tokens": 4},
    }


def test_load_probe_counts_failures_and_never_treats_truncation_as_success():
    value = response()
    value["choices"][0]["finish_reason"] = "length"
    result = ai_acceptance.run_stage(lambda: value, 5)
    assert result["completed"] == 0
    assert result["failed_or_truncated"] == 5
    result = ai_acceptance.run_stage(response, 5)
    assert result["completed"] == 5 and result["failed_or_truncated"] == 0
    assert result["latency_p95_seconds"] >= result["latency_p50_seconds"]


def test_probe_rejects_excessive_load():
    with pytest.raises(ValueError):
        ai_acceptance.run_stage(response, 16)
    with pytest.raises(ValueError):
        ai_acceptance.run(None, concurrency=(1, 5, 10, 15))


def test_khadas_never_reaches_private_model_or_credentials(monkeypatch):
    monkeypatch.setattr(
        ai_acceptance.ai_runtime,
        "probe_support",
        lambda paths: (False, "p3701_required"),
    )
    monkeypatch.setattr(
        ai_acceptance.ai_runtime,
        "read_api_key",
        lambda paths: pytest.fail("read key on Khadas"),
    )
    assert ai_acceptance.run(SimpleNamespace())["state"] == "unsupported"


def test_probe_output_does_not_retain_answers_or_private_errors():
    def fail():
        raise OSError("sensitive server body")

    assert ai_acceptance.sample(fail)["ok"] is False
    result = ai_acceptance.sample(response)
    assert set(result) == {"ok", "seconds", "completion_tokens"}
    assert ai_acceptance.percentile([5, 1, 3], 0.95) == 5
