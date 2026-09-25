from __future__ import annotations

import sys
from collections.abc import Callable
from enum import Enum
from typing import TextIO


class Action(str, Enum):  # noqa: UP042 -- StrEnum is unavailable on Python 3.10
    CLEAN_INSTALL = "clean-install"
    UPDATE = "update"
    DIAGNOSE = "diagnose"
    REMOVE = "remove"
    EXIT = "exit"


_CHOICES = {
    "1": Action.CLEAN_INSTALL,
    "2": Action.UPDATE,
    "3": Action.DIAGNOSE,
    "4": Action.REMOVE,
    "5": Action.EXIT,
}
_MENU = """\
Robopark OTA
1. Чистая установка
2. Обычное обновление
3. Диагностика
4. Полное удаление
5. Выход
"""


def select_action(
    *,
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
) -> Action:
    while True:
        output.write(_MENU)
        output.flush()
        selected = _CHOICES.get(input_fn("Выберите действие: ").strip())
        if selected is not None:
            return selected
        output.write("Введите номер от 1 до 5.\n")


def run_menu(
    *,
    handlers: dict[Action, Callable[[], object]],
    preflight: Callable[[Action], None],
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
) -> Action:
    action = select_action(input_fn=input_fn, output=output)
    if action is Action.EXIT:
        return action
    if action in {Action.CLEAN_INSTALL, Action.UPDATE}:
        preflight(action)
    handler = handlers.get(action)
    if handler is None:
        raise RuntimeError("action_unavailable")
    handler()
    return action
