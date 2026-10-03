from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol


@contextmanager
def clean_install_lock(root: Path) -> Iterator[None]:
    path = Path(root) / "run/lock/robopark-install.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("clean_install_in_progress") from error
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


class CleanInstallRuntime(Protocol):
    def preflight(self) -> None: ...

    def ensure_empty_host(self) -> None: ...

    def extract_release(self) -> None: ...

    def configure(self) -> None: ...

    def start_database(self) -> None: ...

    def migrate(self) -> None: ...

    def seed_royal(self, credential_file: Path) -> None: ...

    def start_application(self) -> None: ...

    def wait_ready(self) -> None: ...

    def smoke_check(self) -> None: ...

    def publish(self) -> None: ...

    def collect_diagnostics(self) -> Path: ...


class CleanInstallCoordinator:
    def __init__(self, runtime: CleanInstallRuntime) -> None:
        self.runtime = runtime

    def run(self, credential_path: Path) -> None:
        self.runtime.preflight()
        self.runtime.ensure_empty_host()
        try:
            self.runtime.extract_release()
            self.runtime.configure()
            self.runtime.start_database()
            self.runtime.migrate()
            self.runtime.seed_royal(credential_path)
            self.runtime.start_application()
            self.runtime.wait_ready()
            self.runtime.smoke_check()
            self.runtime.publish()
        except Exception as error:
            diagnostics = self.runtime.collect_diagnostics()
            raise RuntimeError(f"clean_install_failed: {diagnostics}") from error
