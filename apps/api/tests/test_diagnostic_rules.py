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


def _events(db_session, payload):
    from robopark_api.services.diagnostic_rules import match_diagnostic_events

    db_session.flush()
    return match_diagnostic_events(db_session, payload)


def test_exact_match_returns_display_ready_event_and_preserves_raw_value(db_session):
    rule = _insert_rule(db_session, sort_order=7)

    events = _events(db_session, {"errors": {"navigation": "WHEEL_BLOCKED"}})

    assert len(events) == 1
    event = events[0]
    assert event.id
    assert event.model_dump(exclude={"id"}) == {
        "rule_id": rule.id,
        "source_path": "errors.navigation",
        "raw_value": "WHEEL_BLOCKED",
        "title": "Wheel is blocked",
        "description": "Remove the obstacle and inspect the wheel.",
        "severity": "critical",
        "part": "front_left_wheel",
        "view": "front",
        "x": 0.25,
        "y": 0.75,
        "indicator": "point",
        "sort_order": 7,
    }


@pytest.mark.parametrize("raw", ["wheel_blocked", " WHEEL_BLOCKED", "WHEEL_BLOCKED "])
def test_exact_matching_is_case_and_whitespace_sensitive(db_session, raw):
    _insert_rule(db_session)

    events = _events(db_session, {"errors": {"navigation": raw}})

    assert [(event.rule_id, event.raw_value) for event in events] == [(None, raw)]


@pytest.mark.parametrize(("raw", "pattern"), [(123, "123"), (True, "true"), (1.5, "1.5")])
def test_exact_matching_uses_json_string_semantics_for_non_strings(db_session, raw, pattern):
    rule = _insert_rule(db_session, pattern=pattern)

    events = _events(db_session, {"errors": {"navigation": raw}})

    assert [(event.rule_id, event.raw_value) for event in events] == [(rule.id, raw)]


def test_regex_uses_search_and_deduplicates_repeated_matches_and_raw_values(db_session):
    rule = _insert_rule(db_session, source_path="errors", match_kind="regex", pattern=r"ERR_\d+")

    events = _events(db_session, {"errors": ["prefix ERR_12 ERR_34 suffix"] * 3})

    assert [(event.rule_id, event.raw_value) for event in events] == [
        (rule.id, "prefix ERR_12 ERR_34 suffix")
    ]


def test_structured_collections_preserve_each_error_and_canonical_json_matching(db_session):
    rule = _insert_rule(
        db_session, source_path="errors", pattern='{"code":"KNOWN","message":"Blocked"}'
    )
    known = {"message": "Blocked", "code": "KNOWN"}
    unknown = {"code": "NEW", "message": "Unknown failure", "details": {"count": 2}}

    events = _events(db_session, {"errors": {"navigation": [known, unknown], "power": "LOW"}})

    assert [(event.rule_id, event.raw_value) for event in events] == [
        (rule.id, known),
        (None, unknown),
        (None, "LOW"),
    ]


def test_indexed_path_reads_json_lists_and_does_not_duplicate_matched_structured_error(db_session):
    rule = _insert_rule(db_session, source_path="errors.0.code", pattern="KNOWN")

    events = _events(db_session, {"errors": [{"code": "KNOWN", "message": "Blocked"}]})

    assert [(event.rule_id, event.raw_value) for event in events] == [(rule.id, "KNOWN")]


def test_severity_then_rule_order_then_id_and_raw_value_define_stable_order(db_session):
    info = _insert_rule(
        db_session, source_path="errors", pattern="I", severity="info", sort_order=-9
    )
    warning = _insert_rule(
        db_session, source_path="errors", pattern="W", severity="warning", sort_order=-9
    )
    later = _insert_rule(db_session, source_path="errors", pattern="L", sort_order=4)
    first = _insert_rule(db_session, source_path="errors", pattern="C", sort_order=1)
    tied = _insert_rule(db_session, source_path="errors", pattern="T", sort_order=1)
    values = ["T", "W", "L", "C", "I", "UNKNOWN_Z", "UNKNOWN_A"]

    events = _events(db_session, {"errors": values})
    reversed_events = _events(db_session, {"errors": list(reversed(values))})

    assert [event.rule_id for event in events] == [
        first.id,
        tied.id,
        later.id,
        warning.id,
        None,
        None,
        info.id,
    ]
    assert [event.raw_value for event in events] == [
        "C",
        "T",
        "L",
        "W",
        "UNKNOWN_A",
        "UNKNOWN_Z",
        "I",
    ]
    assert [event.model_dump() for event in events] == [
        event.model_dump() for event in reversed_events
    ]
    assert len({event.id for event in events}) == len(events)


def test_disabled_rule_leaves_the_raw_error_visible_without_a_marker(db_session):
    _insert_rule(db_session, is_enabled=False)

    events = _events(db_session, {"errors": {"navigation": "WHEEL_BLOCKED"}})

    assert len(events) == 1
    event = events[0]
    assert event.raw_value == "WHEEL_BLOCKED"
    assert event.rule_id is None
    assert (event.part, event.view, event.x, event.y, event.indicator) == (None,) * 5


def test_disabling_a_rule_for_a_custom_source_keeps_its_raw_error_visible(db_session):
    _insert_rule(db_session, source_path="telemetry.fault", is_enabled=False)

    events = _events(db_session, {"telemetry": {"fault": "WHEEL_BLOCKED"}})

    assert [(event.rule_id, event.source_path, event.raw_value) for event in events] == [
        (None, "telemetry.fault", "WHEEL_BLOCKED")
    ]


@pytest.mark.parametrize("pattern", ["[", "(?P<", "x{9999999999999999999999999999}"])
def test_malformed_persisted_regex_does_not_hide_raw_errors_or_break_matching(db_session, pattern):
    _insert_rule(db_session, match_kind="regex", pattern=pattern)
    valid = _insert_rule(db_session, pattern="KNOWN")
    db_session.commit()  # Bypass API validation, as a legacy/imported row would.

    events = _events(db_session, {"errors": {"navigation": ["KNOWN", "NEW"]}})

    assert [(event.rule_id, event.raw_value) for event in events] == [
        (valid.id, "KNOWN"),
        (None, "NEW"),
    ]


@pytest.mark.parametrize(
    "source_path",
    [
        "missing.code",
        "errors..code",
        "errors[-1]",
        "errors.-1.code",
        "errors.99.code",
        "errors.__class__",
        "errors.0.__dict__",
        "errors.0.code.upper()",
    ],
)
def test_invalid_missing_or_non_json_paths_do_not_crash_or_match(db_session, source_path):
    _insert_rule(db_session, source_path=source_path, match_kind="regex", pattern=".*")

    events = _events(db_session, {"errors": [{"code": "NEW"}]})

    assert [(event.rule_id, event.raw_value) for event in events] == [(None, {"code": "NEW"})]


def test_path_lookup_never_reads_attributes_or_stringifies_non_json_objects(db_session):
    class Trap:
        @property
        def code(self):
            pytest.fail("Matcher accessed a Python attribute")

        def __str__(self):
            pytest.fail("Matcher stringified a Python object")

    _insert_rule(db_session, source_path="telemetry.code", match_kind="regex", pattern=".*")

    assert _events(db_session, {"telemetry": Trap()}) == []


def test_unknown_errors_from_all_legacy_sources_remain_visible_without_rules(db_session):
    events = _events(
        db_session,
        {
            "lastCritNotification": "CRIT",
            "lastErrorNotification": "ERROR",
            "errors": ["B", "A", "B"],
            "panics": ["PANIC"],
            "notifications": [{"text": "NOTICE"}],
            "telemetry": {"secret": "not an error source"},
        },
    )

    assert [event.raw_value for event in events] == [
        "A",
        "B",
        "CRIT",
        "ERROR",
        {"text": "NOTICE"},
        "PANIC",
    ]
    assert all(event.title and event.description and event.rule_id is None for event in events)
    assert all(
        (event.part, event.view, event.x, event.y, event.indicator) == (None,) * 5
        for event in events
    )


def test_missing_and_empty_error_sources_do_not_create_events(db_session):
    assert _events(db_session, {"errors": [], "panics": {}, "notifications": None}) == []


def test_unknown_structured_list_items_keep_their_complete_raw_shape(db_session):
    raw = {"subsystem": "power", "details": ["LOW", "HOT"]}

    events = _events(db_session, {"errors": [raw]})

    assert [event.raw_value for event in events] == [raw]


def test_non_json_nested_values_cannot_break_event_serialization(db_session):
    _insert_rule(db_session, source_path="errors", match_kind="regex", pattern=".*")

    events = _events(db_session, {"errors": [{"code": ("not", "json")}, "VALID"]})

    assert [event.raw_value for event in events] == ["VALID"]


def test_multiple_rules_and_raw_values_keep_distinct_stable_event_identities(db_session):
    broad = _insert_rule(db_session, source_path="errors", match_kind="regex", pattern="ERR")
    exact = _insert_rule(db_session, source_path="errors", pattern="ERR_A")

    first = _events(db_session, {"errors": ["ERR_B", "ERR_A", "ERR_A"]})
    second = _events(db_session, {"errors": ["ERR_A", "ERR_B"]})

    assert [(event.rule_id, event.raw_value) for event in first] == [
        (broad.id, "ERR_A"),
        (broad.id, "ERR_B"),
        (exact.id, "ERR_A"),
    ]
    assert len({event.id for event in first}) == 3
    assert [event.id for event in first] == [event.id for event in second]
