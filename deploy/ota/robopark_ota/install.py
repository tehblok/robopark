from __future__ import annotations

from pathlib import Path
from typing import Protocol


class CleanInstallRuntime(Protocol):
    def preflight(self) -> None: ...

    def stop_and_remove(self) -> None: ...

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
        try:
            self.runtime.preflight()
            self.runtime.stop_and_remove()
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
