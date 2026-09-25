from __future__ import annotations

import hashlib
from io import StringIO

import pytest
from robopark_ota import cli
from robopark_ota.local_update import _stage_upload
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


def test_update_menu_dispatches_verified_local_ota(monkeypatch, tmp_path):
    bundle = tmp_path / "robopark.ota"
    bundle.write_bytes(b"ota")
    calls = []
    monkeypatch.setattr("builtins.input", lambda _prompt: "ОБНОВИТЬ")
    monkeypatch.setattr(
        cli,
        "apply_local_update",
        lambda path: calls.append(path)
        or {"version": "0.2.0-rc.8", "sha256": "a" * 64},
    )

    cli._update(bundle)

    assert calls == [bundle]


def test_local_update_stages_private_hash_checked_copy(tmp_path):
    source = tmp_path / "source.ota"
    source.write_bytes(b"verified ota")
    target = tmp_path / "incoming" / "upload.ota"
    _stage_upload(source, target, hashlib.sha256(source.read_bytes()).hexdigest())

    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_mode & 0o077 == 0
