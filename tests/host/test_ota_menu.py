from __future__ import annotations

from io import StringIO

import pytest
from robopark_ota import cli
from robopark_ota.menu import Action, run_menu, select_action


def test_menu_is_rendered_before_action_preflight_failure():
    output = StringIO()

    def broken_preflight(_action: Action) -> None:
        assert "1. Чистая установка" in output.getvalue()
        raise RuntimeError("docker unavailable")

    with pytest.raises(RuntimeError, match="docker unavailable"):
        run_menu(
            handlers={Action.CLEAN_INSTALL: lambda: None},
            preflight=broken_preflight,
            input_fn=lambda _prompt: "1",
            output=output,
        )


@pytest.mark.parametrize(
    ("choice", "expected"),
    [("3", Action.DIAGNOSE), ("4", Action.REMOVE), ("5", Action.EXIT)],
)
def test_diagnostic_remove_and_exit_can_be_selected_without_install_discovery(
    choice: str, expected: Action
):
    output = StringIO()
    calls: list[Action] = []

    result = run_menu(
        handlers={expected: lambda: calls.append(expected)},
        preflight=lambda action: pytest.fail(f"unexpected preflight for {action}"),
        input_fn=lambda _prompt: choice,
        output=output,
    )

    assert result == expected
    assert calls == ([] if expected is Action.EXIT else [expected])
    assert "Диагностика" in output.getvalue()
    assert "Полное удаление" in output.getvalue()


def test_invalid_choice_is_reprompted_without_losing_menu():
    answers = iter(["bad", "5"])
    output = StringIO()

    action = select_action(input_fn=lambda _prompt: next(answers), output=output)

    assert action is Action.EXIT
    assert output.getvalue().count("1. Чистая установка") == 2
    assert "Введите номер от 1 до 5" in output.getvalue()


def test_cli_without_subcommand_dispatches_interactive_menu(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(cli, "run_interactive", lambda: calls.append("menu") or 0)

    assert cli.main([]) == 0
    assert calls == ["menu"]
