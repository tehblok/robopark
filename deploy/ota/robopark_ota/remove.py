from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import storage

SYSTEMD_UNITS = (
    "robopark-commands.service",
    "robopark-commands.path",
    "robopark.service",
    "robopark-tuna.service",
    "robopark-updater.service",
    "robopark-doctor.service",
    "robopark-doctor.timer",
    "robopark-backup.service",
    "robopark-backup.timer",
    "robopark-watchdog.service",
    "robopark-watchdog.timer",
    "robopark-bot.service",
    "robopark-bot.path",
    "robopark-update-check.service",
    "robopark-update-check.timer",
)


def local_docker_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Pin host operations to the local daemon, regardless of shell context."""
    environment = dict(os.environ if base is None else base)
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        environment.pop(key, None)
    environment["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    return environment


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
            root / "var/backups/robopark",
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
            image_prefixes=("robopark-api:", "robopark-web:", "robopark-bot:"),
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


@dataclass(frozen=True)
class RemovalPreviewEntry:
    kind: str
    name: str
    path: Path | None
    bytes_used: int


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


def _allocated_bytes(path: Path) -> int:
    info = path.lstat()
    total = info.st_blocks * 512
    if stat.S_ISDIR(info.st_mode):
        for child in path.iterdir():
            total += _allocated_bytes(child)
    return total


def preview_owned_installation(
    plan: RemovalPlan, docker: DockerCli, targets: DockerTargets
) -> tuple[RemovalPreviewEntry, ...]:
    storage.require_storage(plan.root, check_space=False)
    storage.validate_removal_mounts(plan.root, plan.paths)
    entries = []
    for path in plan.paths:
        _validate_path(plan, path)
        if path.exists():
            entries.append(
                RemovalPreviewEntry("path", str(path), path, _allocated_bytes(path))
            )
    for name in targets.containers:
        entries.append(
            RemovalPreviewEntry("container", name, None, docker.container_size(name))
        )
    for name in targets.volumes:
        mountpoint = docker.volume_mountpoint(name)
        if mountpoint.is_symlink():
            raise ValueError("unsafe_volume_mountpoint")
        entries.append(
            RemovalPreviewEntry(
                "volume", name, mountpoint, _allocated_bytes(mountpoint)
            )
        )
    for name in targets.networks:
        entries.append(RemovalPreviewEntry("network", name, None, 0))
    for name in targets.images:
        entries.append(
            RemovalPreviewEntry("image", name, None, docker.image_size(name))
        )
    return tuple(entries)


def remove_owned_installation(
    plan: RemovalPlan, docker: DockerRemoval, targets: DockerTargets
) -> None:
    status = storage.require_storage(plan.root, check_space=False)
    storage.validate_removal_mounts(plan.root, plan.paths)
    for target in plan.paths:
        _validate_path(plan, target)
    docker.remove_containers(targets.containers)
    docker.remove_volumes(targets.volumes)
    docker.remove_networks(targets.networks)
    docker.remove_images(targets.images)
    for target in sorted(plan.paths, key=lambda item: len(item.parts), reverse=True):
        if (status.get("mode") == "emmc-nvme-data"
                and target in {plan.root / path.lstrip("/") for path in storage.TARGETS.values()}
                and target.is_dir()):
            for child in target.iterdir():
                _remove_path(child)
        else:
            _remove_path(target)


class DockerCli:
    _OWNED_PROJECT = re.compile(r"robopark(?:-candidate-[0-9a-f-]{8,64})?")
    _OWNED_IMAGE_TAG = re.compile(
        r"robopark-(?:api|web|bot):[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"
    )
    _RESERVED_NAME = re.compile(r"robopark(?:[-_].*)?")

    @staticmethod
    def _lines(command: list[str]) -> tuple[str, ...]:
        completed = subprocess.run(
            command,
            check=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=local_docker_environment(),
        )
        return tuple(
            line.strip() for line in completed.stdout.splitlines() if line.strip()
        )

    def discover_owned(self) -> DockerTargets:
        containers = self._discover_labelled(
            ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project"],
            "container",
            '{{ index .Config.Labels "com.docker.compose.project" }}',
        )
        self._reject_unlabelled_names(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            "container",
            '{{ index .Config.Labels "com.docker.compose.project" }}',
        )
        volumes = set(
            self._discover_labelled(
                [
                    "docker",
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    "label=com.docker.compose.project",
                ],
                "volume",
                '{{ index .Labels "com.docker.compose.project" }}',
            )
        )
        reserved_volumes = {"robopark_robopark_postgres", "robopark_robopark_data"}
        present_reserved = reserved_volumes.intersection(
            self._lines(["docker", "volume", "ls", "-q"])
        )
        if present_reserved - volumes:
            raise ValueError("unverified_robopark_volume")
        networks = self._discover_labelled(
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                "label=com.docker.compose.project",
            ],
            "network",
            '{{ index .Labels "com.docker.compose.project" }}',
        )
        self._reject_unlabelled_names(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            "network",
            '{{ index .Labels "com.docker.compose.project" }}',
        )
        images = {
            tag
            for repository in ("robopark-api", "robopark-web", "robopark-bot")
            for tag in self._lines(
                [
                    "docker",
                    "image",
                    "ls",
                    "--format",
                    "{{.Repository}}:{{.Tag}}",
                    f"{repository}:*",
                ]
            )
            if self._OWNED_IMAGE_TAG.fullmatch(tag)
        }
        return DockerTargets(
            containers=containers,
            volumes=tuple(sorted(volumes)),
            networks=networks,
            images=tuple(sorted(images)),
        )

    def _discover_labelled(
        self, list_command: list[str], object_name: str, format_template: str
    ) -> tuple[str, ...]:
        owned = []
        for identity in self._lines(list_command):
            project = self._lines(
                [
                    "docker",
                    object_name,
                    "inspect",
                    "--format",
                    format_template,
                    identity,
                ]
            )
            if len(project) == 1 and self._OWNED_PROJECT.fullmatch(project[0]):
                owned.append(identity)
        return tuple(owned)

    def _reject_unlabelled_names(
        self, list_command: list[str], object_name: str, format_template: str
    ) -> None:
        for identity in self._lines(list_command):
            if not self._RESERVED_NAME.fullmatch(identity):
                continue
            project = self._lines(
                [
                    "docker",
                    object_name,
                    "inspect",
                    "--format",
                    format_template,
                    identity,
                ]
            )
            if len(project) != 1 or not self._OWNED_PROJECT.fullmatch(project[0]):
                raise ValueError(f"unverified_robopark_{object_name}")

    def volume_mountpoint(self, name: str) -> Path:
        values = self._lines(
            ["docker", "volume", "inspect", "--format", "{{.Mountpoint}}", name]
        )
        if len(values) != 1 or not Path(values[0]).is_absolute():
            raise ValueError("invalid_volume_mountpoint")
        return Path(values[0])

    def container_size(self, name: str) -> int:
        return self._object_size(
            [
                "docker",
                "container",
                "inspect",
                "--size",
                "--format",
                "{{.SizeRw}}",
                name,
            ]
        )

    def image_size(self, name: str) -> int:
        return self._object_size(
            ["docker", "image", "inspect", "--format", "{{.Size}}", name]
        )

    def _object_size(self, command: list[str]) -> int:
        values = self._lines(command)
        if len(values) != 1 or not values[0].isdigit():
            raise ValueError("invalid_docker_object_size")
        return int(values[0])

    def _remove(self, object_name: str, names: tuple[str, ...]) -> None:
        if not names:
            return
        subprocess.run(
            ["docker", object_name, "rm", "--force", *names],
            check=True,
            stdin=subprocess.DEVNULL,
            env=local_docker_environment(),
        )

    def remove_containers(self, names: tuple[str, ...]) -> None:
        if names:
            subprocess.run(
                ["docker", "rm", "--force", *names],
                check=True,
                stdin=subprocess.DEVNULL,
                env=local_docker_environment(),
            )

    def remove_volumes(self, names: tuple[str, ...]) -> None:
        self._remove("volume", names)

    def remove_networks(self, names: tuple[str, ...]) -> None:
        self._remove("network", names)

    def remove_images(self, names: tuple[str, ...]) -> None:
        if any(not self._OWNED_IMAGE_TAG.fullmatch(name) for name in names):
            raise ValueError("unverified_robopark_image")
        if names:
            subprocess.run(
                ["docker", "image", "rm", *names],
                check=True,
                stdin=subprocess.DEVNULL,
                env=local_docker_environment(),
            )
