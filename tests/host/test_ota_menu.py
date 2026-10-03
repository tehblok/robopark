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


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ValueError("invalid_tuna_address"), "Домен Tuna"),
        (RuntimeError("docker_install_unsupported_release"), "/etc/os-release"),
        (RuntimeError("clean_install_requires_empty_host: /opt/robopark"), "/opt/robopark"),
        (RuntimeError("confirmation_required"), "Данные не удалялись"),
    ],
)
def test_interactive_install_reports_expected_input_or_host_error_without_traceback(
    monkeypatch, tmp_path, capsys, error, message
):
    monkeypatch.setattr(cli, "_bundle_path", lambda: tmp_path / "bundle.ota")

    def fail(**_kwargs):
        raise error

    monkeypatch.setattr(cli, "run_menu", fail)

    assert cli.run_interactive() == 1
    output = capsys.readouterr()
    assert message in output.err
    assert "Traceback" not in output.err


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


def test_update_subcommand_never_enters_clean_install_menu(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle.ota"
    calls = []
    monkeypatch.setattr(cli, "_bundle_path", lambda: bundle)
    monkeypatch.setattr(cli, "_update", lambda path: calls.append(path))
    monkeypatch.setattr(cli, "run_interactive", lambda: pytest.fail("clean install menu"))

    assert cli.main(["update"]) == 0
    assert calls == [bundle]


def test_update_subcommand_cancellation_has_no_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_bundle_path", lambda: tmp_path / "bundle.ota")
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    assert cli.main(["update"]) == 1
    output = capsys.readouterr()
    assert "Операция отменена" in output.err
    assert "Traceback" not in output.err


def test_tuna_restored_only_when_enabled_without_reading_credentials():
    from robopark_ota import local_update

    class Runner:
        def __init__(self, enabled):
            self.enabled = enabled
            self.calls = []

        def run(self, argv, **kwargs):
            self.calls.append(argv)
            if argv[1] == "show":
                return self.enabled.encode()
            return b"active\n"

    assert hasattr(local_update, "restart_enabled_tuna")
    enabled = Runner("enabled\n")
    assert local_update.restart_enabled_tuna(enabled) == "active"
    assert enabled.calls == [
        ["systemctl", "show", "robopark-tuna.service", "--property=UnitFileState", "--value"],
        ["systemctl", "reset-failed", "robopark-tuna.service"],
        ["systemctl", "start", "robopark-tuna.service"],
        ["systemctl", "is-active", "robopark-tuna.service"],
    ]
    disabled = Runner("disabled\n")
    assert local_update.restart_enabled_tuna(disabled) == "disabled"
    assert len(disabled.calls) == 1


def test_local_update_restores_tuna_using_previous_updater(monkeypatch, host_paths):
    from types import SimpleNamespace
    from robopark_ota import local_update
    from robopark_host import ota_update, updater

    release = host_paths.releases / "installed"
    tools = release / "deploy/host"
    tools.mkdir(parents=True)
    (host_paths.opt / "host-tools").symlink_to(tools)
    bundle = host_paths.root / "test.ota"
    calls = []
    monkeypatch.setattr(local_update, "verify_ota", lambda _bundle: SimpleNamespace(
        path=bundle, sha256="a" * 64, manifest=SimpleNamespace(app_version="0.2.0-rc.11"),
    ))
    monkeypatch.setattr(local_update, "_stage_upload", lambda *_args: None)
    monkeypatch.setattr(updater, "SystemRunner", lambda: SimpleNamespace())
    # A previous updater has no tuna_state and never restores ingress.
    monkeypatch.setattr(ota_update, "SystemOtaUpdateRuntime", lambda *_args: SimpleNamespace())
    receipt = SimpleNamespace(phase="published", as_dict=lambda: {"version": "0.2.0-rc.11"})
    monkeypatch.setattr(ota_update, "OtaUpdateEngine", lambda *_args: SimpleNamespace(apply=lambda *_args: receipt))
    monkeypatch.setattr(local_update, "restart_enabled_tuna", lambda runner: calls.append(runner) or "active")

    assert local_update.apply_local_update(bundle, root=host_paths.root)["version"] == "0.2.0-rc.11"
    assert len(calls) == 1


def test_local_update_stages_private_hash_checked_copy(tmp_path):
    source = tmp_path / "source.ota"
    source.write_bytes(b"verified ota")
    target = tmp_path / "incoming" / "upload.ota"
    _stage_upload(source, target, hashlib.sha256(source.read_bytes()).hexdigest())

    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_mode & 0o077 == 0
