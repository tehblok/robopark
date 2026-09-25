from __future__ import annotations

import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

SYSTEMD_UNITS = (
    "robopark-commands.service",
    "robopark-commands.path",
    "robopark.service",
    "robopark-tuna.service",
    "robopark-updater.service",
    "robopark-doctor.service",
    "robopark-doctor.timer",
    "robopark-watchdog.service",
    "robopark-watchdog.timer",
)


@dataclass(frozen=True)
class RemovalPlan:
    root: Path
    paths: tuple[Path, ...]
    services: tuple[str, ...]
    compose_projects: tuple[str, ...]
    volumes: tuple[str, ...]
    image_prefixes: tuple[str, ...]

    @classmethod
    def for_root(cls, root: Path) -> RemovalPlan:
        root = Path(root).resolve()
        paths = (
            root / "opt/robopark",
            root / "etc/robopark",
            root / "var/lib/robopark",
            root / "var/log/robopark",
            root / "run/lock/robopark",
            *(root / "etc/systemd/system" / unit for unit in SYSTEMD_UNITS),
            root / "etc/tmpfiles.d/robopark.conf",
        )
        return cls(
            root=root,
            paths=tuple(paths),
            services=SYSTEMD_UNITS,
            compose_projects=("robopark",),
            volumes=("robopark_robopark_postgres", "robopark_robopark_data"),
            image_prefixes=("robopark-api:", "robopark-web:"),
        )


@dataclass(frozen=True)
class DockerTargets:
    containers: tuple[str, ...]
    volumes: tuple[str, ...]
    networks: tuple[str, ...]
    images: tuple[str, ...]

    @classmethod
    def empty(cls) -> DockerTargets:
        return cls((), (), (), ())


class DockerRemoval(Protocol):
    def remove_containers(self, names: tuple[str, ...]) -> None: ...

    def remove_volumes(self, names: tuple[str, ...]) -> None: ...

    def remove_networks(self, names: tuple[str, ...]) -> None: ...

    def remove_images(self, names: tuple[str, ...]) -> None: ...


def _validate_path(plan: RemovalPlan, target: Path) -> None:
    try:
        relative = target.relative_to(plan.root)
    except ValueError as error:
        raise ValueError("unsafe_removal_path") from error
    if not relative.parts or relative.parts[0] not in {"opt", "etc", "var", "run"}:
        raise ValueError("unsafe_removal_path")
    current = plan.root
    for part in relative.parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("unsafe_removal_path")


def _remove_path(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def remove_owned_installation(
    plan: RemovalPlan, docker: DockerRemoval, targets: DockerTargets
) -> None:
    for target in plan.paths:
        _validate_path(plan, target)
    docker.remove_containers(targets.containers)
    docker.remove_volumes(targets.volumes)
    docker.remove_networks(targets.networks)
    docker.remove_images(targets.images)
    for target in sorted(plan.paths, key=lambda item: len(item.parts), reverse=True):
        _remove_path(target)


class DockerCli:
    @staticmethod
    def _lines(command: list[str]) -> tuple[str, ...]:
        completed = subprocess.run(
            command,
            check=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())

    def discover_owned(self) -> DockerTargets:
        label = "label=com.docker.compose.project=robopark"
        containers = self._lines(["docker", "ps", "-aq", "--filter", label])
        volumes = set(self._lines(["docker", "volume", "ls", "-q", "--filter", label]))
        volumes.update({"robopark_robopark_postgres", "robopark_robopark_data"})
        networks = self._lines(["docker", "network", "ls", "-q", "--filter", label])
        images = set(self._lines(["docker", "image", "ls", "-q", "robopark-api:*"]))
        images.update(self._lines(["docker", "image", "ls", "-q", "robopark-web:*"]))
        return DockerTargets(
            containers=containers,
            volumes=tuple(sorted(volumes)),
            networks=networks,
            images=tuple(sorted(images)),
        )

    def _remove(self, object_name: str, names: tuple[str, ...]) -> None:
        if not names:
            return
        subprocess.run(
            ["docker", object_name, "rm", "--force", *names],
            check=True,
            stdin=subprocess.DEVNULL,
        )

    def remove_containers(self, names: tuple[str, ...]) -> None:
        if names:
            subprocess.run(
                ["docker", "rm", "--force", *names],
                check=True,
                stdin=subprocess.DEVNULL,
            )

    def remove_volumes(self, names: tuple[str, ...]) -> None:
        self._remove("volume", names)

    def remove_networks(self, names: tuple[str, ...]) -> None:
        self._remove("network", names)

    def remove_images(self, names: tuple[str, ...]) -> None:
        self._remove("image", names)
