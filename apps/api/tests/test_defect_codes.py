import pytest
from fastapi import HTTPException

from conftest import login_as

EXPECTED_CODES = [
    "BD-01",
    "BD-02",
    "BD-03",
    "BD-04",
    "BD-05",
    "BD-06",
    "BD-07",
    "BD-08",
    "BD-09",
    "BD-10",
    "EL-01",
    "EL-02",
    "EL-03",
    "EL-04",
    "EL-05",
    "EL-06",
    "EL-07",
    "EL-08",
    "EL-09",
    "EL-10",
    "EL-11",
    "WH-01",
    "WH-02",
    "WH-03",
    "WH-04",
    "WH-05",
    "WH-06",
    "CH-01",
    "CH-02",
    "CH-03",
    "CH-04",
    "CH-05",
    "PP-01",
    "PP-02",
    "PP-03",
]


def test_defect_catalog_exposes_exact_approved_codes_and_russian_explanations(client, seed_royal):
    login_as(client, "royal", "secret")

    response = client.get("/tracker/defect-codes")

    assert response.status_code == 200
    catalog = response.json()
    assert [item["code"] for item in catalog] == EXPECTED_CODES
    assert len(set(EXPECTED_CODES)) == 35
    assert catalog[2] == {
        "code": "BD-03",
        "label": "Зазор",
        "description": "Детали находятся в одном уровне, но расстояние между ними отличается от допустимого по сравнению с другими роботами.",
    }
    assert catalog[-1] == {
        "code": "PP-03",
        "label": "Не соответствует серийный номер",
        "description": None,
    }


def test_defect_code_validation_returns_only_exact_known_code():
    from robopark_api.services.defect_codes import validate_defect_code

    assert validate_defect_code("BD-01") == "BD-01"
    for unknown in ("bd-01", " BD-01 ", "XX-99"):
        with pytest.raises(HTTPException) as caught:
            validate_defect_code(unknown)
        assert caught.value.status_code == 422
