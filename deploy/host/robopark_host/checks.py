"""Typed, bounded primitives shared by host diagnostics and repair."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

CheckStatus = Literal["ok", "warning", "failed"]
MAX_OUTPUT = 16_384
DEFAULT_TIMEOUT = 10


@dataclass(frozen=True)
class CheckResult:
    """One stable, operator-facing diagnostic outcome."""

    code: str
    status: CheckStatus
    message: str
    repair: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"ok", "warning", "failed"}:
            raise ValueError("unsupported diagnostic status")
        if not self.code:
            raise ValueError("diagnostic code is required")

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "repair": self.repair,
        }


@dataclass(frozen=True)
class CommandResult:
    """The bounded, non-secret part of a command result."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class DiagnosticReport:
    """A serializable collection of check outcomes without command output."""

    checks: list[CheckResult]
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).replace(microsecond=0).isoformat()
    )

    def __init__(self, checks: Iterable[CheckResult], created_at: str | None = None) -> None:
        self.checks = list(checks)
        codes = [check.code for check in self.checks]
        if len(codes) != len(set(codes)):
            raise ValueError("diagnostic codes must be unique")
        self.created_at = created_at or datetime.now(UTC).replace(microsecond=0).isoformat()

    @property
    def results(self) -> list[CheckResult]:
        """Compatibility alias for callers that use the term results."""

        return list(self.checks)

    def by_code(self, code: str) -> CheckResult:
        for check in self.checks:
            if check.code == code:
                return check
        raise KeyError(code)

    @property
    def failed(self) -> list[CheckResult]:
        return [check for check in self.checks if check.status == "failed"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "checks": [check.as_dict() for check in self.checks],
        }


Runner = Callable[..., Any]


def execute(
    runner: Runner,
    command: Sequence[str],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = MAX_OUTPUT,
) -> CommandResult:
    """Run one argument-array command through an injectable bounded runner."""

    arguments = [str(item) for item in command]
    value = runner(arguments, timeout=timeout, max_output=max_output)
    return _bounded_result(value, max_output)


def _bounded_result(value: Any, max_output: int) -> CommandResult:
    if isinstance(value, CommandResult):
        result = value
    elif isinstance(value, bool):
        result = CommandResult(returncode=0 if value else 1)
    elif isinstance(value, Mapping):
        result = CommandResult(
            returncode=int(value.get("returncode", 0)),
            stdout=str(value.get("stdout", "")),
            stderr=str(value.get("stderr", "")),
        )
    else:
        result = CommandResult(
            returncode=int(getattr(value, "returncode", 0)),
            stdout=str(getattr(value, "stdout", "") or ""),
            stderr=str(getattr(value, "stderr", "") or ""),
        )
    return CommandResult(result.returncode, result.stdout[:max_output], result.stderr[:max_output])
