import math

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from robopark_api import models, schemas

VALID_RULE = {
    "source_path": "errors.navigation",
    "match_kind": "exact",
    "pattern": "WHEEL_BLOCKED",
    "example": "WHEEL_BLOCKED",
    "title": "Wheel is blocked",
    "description": "Remove the obstacle and inspect the wheel.",
    "severity": "critical",
    "part": "front_left_wheel",
    "preferred_view": "front",
    "x": 0.25,
    "y": 0.75,
    "indicator": "point",
}


def _insert_rule(db_session, **overrides):
    values = {
        **VALID_RULE,
        "is_enabled": True,
        "sort_order": 0,
        **overrides,
    }
    rule = models.DiagnosticRule(**values)
    db_session.add(rule)
    return rule


def test_create_schema_accepts_the_complete_persisted_contract():
    payload = schemas.DiagnosticRuleCreate.model_validate(VALID_RULE)

    assert payload.model_dump() == {
        **VALID_RULE,
        "is_enabled": True,
        "sort_order": 0,
    }


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("match_kind", "glob"),
        ("severity", "error"),
        ("preferred_view", "bottom"),
        ("indicator", "blink"),
        ("x", -0.001),
        ("x", 1.001),
        ("y", -0.001),
        ("y", 1.001),
        ("x", math.nan),
        ("y", math.inf),
    ],
)
def test_create_schema_rejects_invalid_enum_values_and_coordinates(field, invalid):
    with pytest.raises(ValidationError):
        schemas.DiagnosticRuleCreate.model_validate({**VALID_RULE, field: invalid})


@pytest.mark.parametrize("field", tuple(VALID_RULE))
def test_create_schema_rejects_omitted_required_fields(field):
    payload = VALID_RULE.copy()
    payload.pop(field)

    with pytest.raises(ValidationError):
        schemas.DiagnosticRuleCreate.model_validate(payload)


@pytest.mark.parametrize("field", (*tuple(VALID_RULE), "is_enabled", "sort_order"))
def test_create_and_update_schemas_reject_explicit_null_for_persisted_fields(field):
    with pytest.raises(ValidationError):
        schemas.DiagnosticRuleCreate.model_validate({**VALID_RULE, field: None})

    with pytest.raises(ValidationError):
        schemas.DiagnosticRuleUpdate.model_validate({field: None})


def test_update_schema_is_partial_and_preserves_coordinate_validation():
    assert schemas.DiagnosticRuleUpdate.model_validate({"title": "Updated"}).model_dump(
        exclude_unset=True
    ) == {"title": "Updated"}

    with pytest.raises(ValidationError):
        schemas.DiagnosticRuleUpdate.model_validate({"x": 2})


def test_output_schema_reads_the_model(db_session):
    rule = _insert_rule(db_session, sort_order=7)
    db_session.flush()

    output = schemas.DiagnosticRuleOut.model_validate(rule)

    assert output.model_dump() == {"id": rule.id, **VALID_RULE, "is_enabled": True, "sort_order": 7}


def test_database_enforces_unique_rule_identity_but_allows_pattern_reuse(db_session):
    _insert_rule(db_session)
    db_session.commit()

    _insert_rule(db_session, source_path="errors.power")
    _insert_rule(db_session, match_kind="regex")
    db_session.commit()

    with pytest.raises(IntegrityError):
        _insert_rule(db_session)
        db_session.flush()


@pytest.mark.parametrize(
    ("column", "invalid"),
    [
        ("match_kind", "glob"),
        ("severity", "error"),
        ("preferred_view", "bottom"),
        ("indicator", "blink"),
        ("x", -0.01),
        ("x", 1.01),
        ("y", -0.01),
        ("y", 1.01),
        ("x", math.inf),
    ],
)
def test_database_rejects_invalid_enum_values_and_coordinates(db_session, column, invalid):
    with pytest.raises(IntegrityError):
        _insert_rule(db_session, **{column: invalid})
        db_session.flush()


def test_database_columns_are_required_and_defaults_are_deterministic(db_engine):
    columns = {
        column["name"]: column for column in inspect(db_engine).get_columns("diagnostic_rules")
    }
    assert all(not column["nullable"] for column in columns.values())

    required_without_defaults = set(VALID_RULE)
    assert all(columns[name]["default"] is None for name in required_without_defaults)
    assert columns["is_enabled"]["default"] is not None
    assert columns["sort_order"]["default"] is not None

    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO diagnostic_rules (
                    source_path, match_kind, pattern, example, title, description,
                    severity, part, preferred_view, x, y, indicator
                ) VALUES (
                    :source_path, :match_kind, :pattern, :example, :title, :description,
                    :severity, :part, :preferred_view, :x, :y, :indicator
                )
                """
            ),
            VALID_RULE,
        )
        row = connection.execute(text("SELECT is_enabled, sort_order FROM diagnostic_rules")).one()

    assert tuple(row) == (1, 0)
