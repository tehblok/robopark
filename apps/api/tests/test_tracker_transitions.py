import pytest


@pytest.mark.parametrize(
    ("purpose", "transitions", "expected"),
    [
        ("start", [{"id": "inProgress", "display": "В работе"}], "inProgress"),
        ("start", [{"id": "start", "display": "In Progress"}], "start"),
        ("review", [{"id": "verify", "display": "Проверка"}], "verify"),
        ("review", [{"id": "verification", "display": "Verification"}], "verification"),
        ("return", [{"id": "return-to-work", "display": "Вернуть в работу"}], "return-to-work"),
        ("return", [{"id": "reopen", "display": "Return to work"}], "reopen"),
        ("close", [{"id": "resolve", "display": "Закрыть"}], "resolve"),
        ("close", [{"id": "close", "display": "Closed"}], "close"),
    ],
)
def test_resolve_transition_matches_russian_and_english_workflow_names(
    purpose, transitions, expected
):
    from robopark_api.services.tracker_transitions import resolve_transition

    assert resolve_transition(transitions, purpose) == expected


def test_resolve_transition_normalizes_case_yo_whitespace_and_punctuation():
    from robopark_api.services.tracker_transitions import resolve_transition

    transitions = [{"id": "DONE", "display": "  ЗАВЕРШЁНО!!!  "}]

    assert resolve_transition(transitions, "close") == "DONE"


def test_resolve_transition_prefers_exact_name_over_partial_token_match():
    from robopark_api.services.tracker_transitions import resolve_transition

    transitions = [
        {"id": "review-later", "display": "Review requested later"},
        {"id": "verification", "display": "Verification"},
    ]

    assert resolve_transition(transitions, "review") == "verification"


@pytest.mark.parametrize(
    "transitions",
    [
        [],
        [{"id": "pause", "display": "Pause"}],
        [
            {"id": "close-one", "display": "Close"},
            {"id": "close-two", "display": "Close"},
        ],
    ],
)
def test_resolve_transition_never_chooses_an_absent_or_ambiguous_match(transitions):
    from robopark_api.services.tracker_transitions import resolve_transition

    assert resolve_transition(transitions, "close") is None
