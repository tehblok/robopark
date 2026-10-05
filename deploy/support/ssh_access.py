#!/usr/bin/env python3
"""ROBOPARK_SSH_ACCESS_MANAGED: opt-in recovery SSH over a reserved Tuna TCP alias."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import platform
import re
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ACCOUNT = "robopark-support"
SSHD_UNIT = "robopark-support-sshd.service"
TUNA_UNIT = "robopark-support-tuna.service"
MANAGED = "# ROBOPARK_SSH_ACCESS_MANAGED\n"
TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]{8,2048}\Z")
NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?\Z")
ACTIVE_NOTICE = (
    "support SSH and Tuna processes are active; verify public SSH externally"
)


class SecurityError(RuntimeError):
    pass


def _ssh_field(raw: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(raw):
        raise ValueError("invalid ed25519 public key blob")
    length = struct.unpack(">I", raw[offset : offset + 4])[0]
    start = offset + 4
    end = start + length
    if end > len(raw):
        raise ValueError("invalid ed25519 public key blob")
    return raw[start:end], end


def validate_public_key(value: str) -> str:
    """Return one normalized, option-free OpenSSH ed25519 public key line."""
    if not isinstance(value, str) or any(
        ord(ch) < 32 or ord(ch) == 127 for ch in value.rstrip("\n")
    ):
        raise ValueError("invalid ed25519 public key: control character")
    if (
        "\r" in value
        or value.count("\n") > 1
        or ("\n" in value and not value.endswith("\n"))
    ):
        raise ValueError("invalid ed25519 public key: exactly one line is required")
    parts = value.strip().split(None, 2)
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise ValueError(
            "invalid ed25519 public key: key options and other algorithms are forbidden"
        )
    if len(parts) == 3 and any(
        ch.isspace() or ord(ch) < 33 or ord(ch) > 126 for ch in parts[2]
    ):
        raise ValueError("invalid ed25519 public key: unsafe comment")
    try:
        raw = base64.b64decode(parts[1], validate=True)
        kind, offset = _ssh_field(raw, 0)
        key, offset = _ssh_field(raw, offset)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid ed25519 public key blob") from exc
    if kind != b"ssh-ed25519" or len(key) != 32 or offset != len(raw):
        raise ValueError("invalid ed25519 public key blob")
    return " ".join(parts)


def extract_tuna_token(content: str) -> str:
    """Read only a literal TUNA_TOKEN assignment from an existing env file."""
    found = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("TUNA_TOKEN="):
            found.append(line[len("TUNA_TOKEN=") :])
    if len(found) != 1 or not TOKEN_RE.fullmatch(found[0]):
        raise ValueError("TUNA_TOKEN must be one nonempty literal token")
    return found[0]


def validate_managed_path(
    path: Path, *, root: Path = Path("/"), expected_uid: int = 0
) -> None:
    """Reject symlinks and untrusted existing ancestors below the trust root."""
    path = Path(path)
    root = Path(root).absolute()
    absolute = path.absolute()
    try:
        relative = absolute.relative_to(root)
    except ValueError as exc:
        raise SecurityError(f"managed path is outside root: {path}") from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise SecurityError(f"managed path contains symlink: {current}")
        if info.st_uid != expected_uid:
            raise SecurityError(f"managed path has unexpected owner: {current}")
        if info.st_mode & 0o022:
            raise SecurityError(
                f"managed path is writable by group or others: {current}"
            )


@dataclass(frozen=True)
class Layout:
    root: Path

    def path(self, absolute: str) -> Path:
        return self.root / absolute.lstrip("/")

    @property
    def etc(self) -> Path:
        return self.path("/etc/robopark-ssh")

    @property
    def state(self) -> Path:
        return self.path("/var/lib/robopark-ssh")

    @property
    def units(self) -> Path:
        return self.path("/etc/systemd/system")

    @property
    def installed_helper(self) -> Path:
        return self.path("/usr/local/libexec/robopark-ssh-access.py")


class FileTransaction:
    def __init__(self):
        self._before: list[
            tuple[Path, bytes | None, int | None, int | None, int | None]
        ] = []

    def write(self, path: Path, content: bytes, mode: int, uid: int, gid: int) -> None:
        if path.exists():
            info = path.stat()
            snapshot = (
                path,
                path.read_bytes(),
                stat.S_IMODE(info.st_mode),
                info.st_uid,
                info.st_gid,
            )
        else:
            snapshot = (path, None, None, None, None)
        self._before.append(snapshot)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.chown(temporary, uid, gid)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def rollback(self) -> None:
        for path, content, mode, uid, gid in reversed(self._before):
            if content is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            fd, temporary = tempfile.mkstemp(
                prefix=f".{path.name}.rollback.", dir=path.parent
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, mode)
                os.chown(temporary, uid, gid)
                os.replace(temporary, path)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass


Runner = Callable[..., subprocess.CompletedProcess]


class SshAccessManager:
    def __init__(
        self,
        *,
        root: Path = Path("/"),
        runner: Runner = subprocess.run,
        expected_uid: int = 0,
        expected_gid: int | None = None,
        sshd_binary: str | None = None,
        tuna_binary: str | None = None,
        manage_account: bool = True,
        generate_host_key: bool = True,
        listener_probe: Callable[[], bool] | None = None,
        stability_interval: float = 1.0,
        stability_checks: int = 3,
    ):
        self.layout = Layout(Path(root).absolute())
        self.runner = runner
        self.expected_uid = expected_uid
        self.expected_gid = (
            (0 if expected_uid == 0 else os.getgid())
            if expected_gid is None
            else expected_gid
        )
        self.sshd_binary = sshd_binary
        self.tuna_binary = tuna_binary
        self.manage_account = manage_account
        self.generate_host_key = generate_host_key
        self.listener_probe = listener_probe or self._listener_ready
        self.stability_interval = stability_interval
        self.stability_checks = stability_checks

    def _run(self, argv: Sequence[str], *, check: bool = True, capture: bool = False):
        return self.runner(list(argv), check=check, capture_output=capture, text=True)

    def _trusted_binary(
        self, configured: str | None, candidates: Sequence[str], package: str
    ) -> str:
        choices = [configured] if configured else list(candidates)
        for value in choices:
            if not value or not os.path.isabs(value):
                continue
            candidate = Path(value)
            if self.layout.root != Path("/") and configured:
                return value
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if (
                stat.S_ISREG(info.st_mode)
                and not stat.S_ISLNK(info.st_mode)
                and info.st_uid == 0
                and info.st_mode & 0o111
                and not info.st_mode & 0o022
            ):
                return value
        raise RuntimeError(f"{package} is required but no trusted executable was found")

    def _mkdir(
        self,
        path: Path,
        mode: int,
        uid: int = 0,
        gid: int = 0,
        *,
        enforce_existing: bool = True,
    ) -> None:
        validate_managed_path(
            path.parent,
            root=self.layout.root,
            expected_uid=self.expected_uid,
        )
        try:
            leaf = path.lstat()
        except FileNotFoundError:
            leaf = None
        if leaf is not None and (
            not stat.S_ISDIR(leaf.st_mode)
            or stat.S_ISLNK(leaf.st_mode)
            or leaf.st_uid != uid
            or leaf.st_mode & 0o022
        ):
            raise SecurityError(f"managed directory is unsafe: {path}")
        relative = path.relative_to(self.layout.root)
        current = self.layout.root
        created_leaf = False
        for part in relative.parts:
            current = current / part
            if current.exists():
                continue
            created_mode = mode if current == path else 0o755
            current.mkdir(mode=created_mode)
            os.chmod(current, created_mode)
            os.chown(current, uid, gid)
            if current == path:
                created_leaf = True
        if created_leaf or enforce_existing:
            os.chmod(path, mode)
            os.chown(path, uid, gid)

    def _private_source(self, path: Path) -> str:
        validate_managed_path(
            path, root=self.layout.root, expected_uid=self.expected_uid
        )
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise SecurityError(f"credential source must be a regular file: {path}")
        if info.st_uid != self.expected_uid or info.st_mode & 0o077:
            raise SecurityError(f"credential source must be owner-only: {path}")
        return path.read_text(encoding="utf-8")

    def _ensure_account(self) -> tuple[int, int, bool, bool]:
        created_group = False
        created_user = False
        try:
            passwd_result = self._run(
                ["getent", "passwd", ACCOUNT], check=False, capture=True
            )
            group_result = self._run(
                ["getent", "group", ACCOUNT], check=False, capture=True
            )
            if group_result.returncode != 0 or not group_result.stdout.strip():
                self._run(["groupadd", "--system", ACCOUNT])
                created_group = True
            if passwd_result.returncode != 0 or not passwd_result.stdout.strip():
                self._run(
                    [
                        "useradd",
                        "--system",
                        "--gid",
                        ACCOUNT,
                        "--home-dir",
                        "/var/lib/robopark-ssh/home",
                        "--shell",
                        "/bin/bash",
                        "--no-create-home",
                        ACCOUNT,
                    ]
                )
                created_user = True
                self._run(["passwd", "--lock", ACCOUNT])
            passwd_result = self._run(["getent", "passwd", ACCOUNT], capture=True)
            group_result = self._run(["getent", "group", ACCOUNT], capture=True)
            fields = passwd_result.stdout.strip().split(":")
            if (
                len(fields) != 7
                or fields[0] != ACCOUNT
                or fields[5:]
                != [
                    "/var/lib/robopark-ssh/home",
                    "/bin/bash",
                ]
            ):
                raise SecurityError("conflicting existing robopark-support account")
            group_fields = group_result.stdout.strip().split(":")
            if len(group_fields) != 4 or group_fields[0] != ACCOUNT:
                raise SecurityError("conflicting existing robopark-support group")
            uid = int(fields[2])
            gid = int(fields[3])
            if uid in (0, 10001) or gid != int(group_fields[2]):
                raise SecurityError("unsafe robopark-support uid or primary group")
            groups = self._run(["id", "-Gn", ACCOUNT], capture=True).stdout.split()
            if groups != [ACCOUNT]:
                raise SecurityError(
                    "robopark-support must belong only to its own group"
                )
            shadow = self._run(
                ["getent", "shadow", ACCOUNT], capture=True
            ).stdout.split(":")
            if (
                len(shadow) < 2
                or shadow[0] != ACCOUNT
                or not shadow[1].startswith(("!", "*"))
            ):
                raise SecurityError("robopark-support password must be locked")
            return uid, gid, created_user, created_group
        except Exception:
            if created_user:
                self._run(["userdel", ACCOUNT], check=False)
            if created_group:
                self._run(["groupdel", ACCOUNT], check=False)
            raise

    @staticmethod
    def _read_authorized_key(content: str) -> str:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if lines and lines[0] == MANAGED.strip():
            lines.pop(0)
        if len(lines) != 1:
            raise ValueError("managed authorized_keys must contain exactly one key")
        return validate_public_key(lines[0])

    @staticmethod
    def _legacy_tuna_env(content: str) -> bool:
        lines = [line for line in content.splitlines() if line]
        if len(lines) != 1 or not lines[0].startswith("TUNA_TOKEN="):
            return False
        try:
            extract_tuna_token(content)
        except ValueError:
            return False
        return True

    @staticmethod
    def _helper_managed_or_equal(path: Path, expected: bytes) -> None:
        if not path.exists():
            return
        current = path.read_bytes()
        if current == expected or b"ROBOPARK_SSH_ACCESS_MANAGED" in current[:256]:
            return
        raise SecurityError(f"unrelated managed file already exists: {path}")

    def _sshd_config(self) -> str:
        return (
            MANAGED
            + """Port 2222
ListenAddress 127.0.0.1
Protocol 2
HostKey /etc/robopark-ssh/ssh_host_ed25519_key
PidFile /run/robopark-support-sshd.pid
AuthorizedKeysFile /etc/robopark-ssh/authorized_keys
AllowUsers robopark-support
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
AuthenticationMethods publickey
PubkeyAuthentication yes
PubkeyAcceptedAlgorithms ssh-ed25519
PermitEmptyPasswords no
UsePAM yes
LoginGraceTime 30
MaxAuthTries 3
MaxStartups 3:30:6
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
PermitTunnel no
GatewayPorts no
PermitUserEnvironment no
StrictModes yes
Subsystem sftp internal-sftp
"""
        )

    def _sshd_unit(self, sshd: str) -> str:
        return (
            MANAGED
            + f"""[Unit]
Description=Robopark support loopback SSH daemon
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
RuntimeDirectory=sshd
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
ExecStartPre={sshd} -t -f /etc/robopark-ssh/sshd_config
ExecStart={sshd} -D -e -f /etc/robopark-ssh/sshd_config
ExecReload={sshd} -t -f /etc/robopark-ssh/sshd_config
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5s
PrivateTmp=yes
ProtectSystem=full
ProtectHome=yes

[Install]
WantedBy=multi-user.target
"""
        )

    def _tuna_unit(self, tuna: str, alias: str, location: str) -> str:
        return (
            MANAGED
            + f"""[Unit]
Description=Robopark support SSH Tuna tunnel
Wants=network-online.target
Requires={SSHD_UNIT}
After=network-online.target {SSHD_UNIT}
StartLimitIntervalSec=0

[Service]
Type=simple
DynamicUser=yes
StateDirectory=robopark-support-tuna
StateDirectoryMode=0700
Environment=HOME=/var/lib/robopark-support-tuna
EnvironmentFile=/etc/robopark-ssh/tuna.env
ExecStart={tuna} tcp 127.0.0.1:2222 --port={alias} --location={location}
Restart=on-failure
RestartSec=5s
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes

[Install]
WantedBy=multi-user.target
"""
        )

    def _validate_candidate(
        self, sshd: str, config: str, *, host_key: Path | None = None
    ) -> None:
        if host_key is not None:
            config = config.replace(
                "HostKey /etc/robopark-ssh/ssh_host_ed25519_key",
                f"HostKey {host_key}",
            )
        fd, name = tempfile.mkstemp(prefix="robopark-support-sshd-", suffix=".conf")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(config)
            self._run([sshd, "-t", "-f", name])
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    def _derive_host_public_key(self, private_key: Path) -> str:
        result = self._run(
            ["ssh-keygen", "-y", "-f", str(private_key)],
            capture=True,
        )
        return validate_public_key(result.stdout)

    def _verify_host_key_pair(self, private_key: Path, public_key: Path) -> str:
        derived = self._derive_host_public_key(private_key)
        installed = validate_public_key(public_key.read_text(encoding="utf-8"))
        if derived.split()[:2] != installed.split()[:2]:
            raise SecurityError("support SSH host key pair does not match")
        return derived

    def install(
        self,
        public_key_file: Path | None,
        *,
        tuna_env: Path = Path("/etc/robopark/tuna.env"),
        alias: str = "ssh",
        location: str = "ru",
        enable: bool = False,
    ) -> None:
        if not NAME_RE.fullmatch(alias) or not NAME_RE.fullmatch(location):
            raise ValueError("alias and location must be lowercase names")
        key_path = (
            Path(public_key_file)
            if public_key_file is not None
            else Path(__file__).with_name("robopark_support_ed25519.pub")
        )
        using_installed_key = False
        if not key_path.exists() and public_key_file is None:
            key_path = self.layout.etc / "authorized_keys"
            using_installed_key = True
            validate_managed_path(
                key_path,
                root=self.layout.root,
                expected_uid=self.expected_uid,
            )
        if not key_path.exists():
            raise ValueError(
                "no support public key: provide --public-key-file or install "
                "robopark_support_ed25519.pub beside this helper"
            )
        key_info = key_path.lstat()
        if not stat.S_ISREG(key_info.st_mode) or stat.S_ISLNK(key_info.st_mode):
            raise SecurityError("public key file must be a regular file")
        key_content = key_path.read_text(encoding="utf-8")
        public_key = (
            self._read_authorized_key(key_content)
            if using_installed_key
            else validate_public_key(key_content)
        ) + "\n"
        token = extract_tuna_token(self._private_source(Path(tuna_env)))
        sshd = self._trusted_binary(
            self.sshd_binary,
            ("/usr/sbin/sshd", "/usr/local/sbin/sshd"),
            "openssh-server",
        )
        tuna = self._trusted_binary(
            self.tuna_binary,
            ("/usr/local/bin/tuna", "/usr/bin/tuna", "/opt/tuna/bin/tuna"),
            "Tuna",
        )
        config = self._sshd_config()
        sshd_unit = self._sshd_unit(sshd).encode()
        tuna_unit = self._tuna_unit(tuna, alias, location).encode()
        authorized_path = self.layout.etc / "authorized_keys"
        tuna_env_path = self.layout.etc / "tuna.env"
        managed = {
            authorized_path: MANAGED.encode() + public_key.encode(),
            tuna_env_path: MANAGED.encode() + f"TUNA_TOKEN={token}\n".encode(),
            self.layout.etc / "sshd_config": config.encode(),
            self.layout.units / SSHD_UNIT: sshd_unit,
            self.layout.units / TUNA_UNIT: tuna_unit,
        }
        ownership_paths = (
            self.layout.etc / "sshd_config",
            self.layout.units / SSHD_UNIT,
            self.layout.units / TUNA_UNIT,
        )
        existing_install = all(
            path.is_file()
            and not path.is_symlink()
            and path.read_bytes().startswith(MANAGED.encode())
            for path in ownership_paths
        )
        changed_existing = False
        for path, expected in managed.items():
            validate_managed_path(
                path, root=self.layout.root, expected_uid=self.expected_uid
            )
            if not path.exists():
                continue
            current = path.read_bytes()
            accepted = current == expected or current.startswith(MANAGED.encode())
            if not accepted and existing_install and path == authorized_path:
                try:
                    self._read_authorized_key(current.decode("utf-8"))
                    accepted = True
                except (UnicodeDecodeError, ValueError):
                    pass
            if not accepted and existing_install and path == tuna_env_path:
                try:
                    accepted = self._legacy_tuna_env(current.decode("utf-8"))
                except UnicodeDecodeError:
                    pass
            if not accepted:
                raise SecurityError(f"unrelated managed file already exists: {path}")
            changed_existing = changed_existing or current != expected
        if changed_existing and any(
            self._unit_state("is-active", unit) for unit in (SSHD_UNIT, TUNA_UNIT)
        ):
            raise RuntimeError(
                "disable support SSH before changing its credentials or configuration"
            )
        validate_managed_path(
            self.layout.installed_helper,
            root=self.layout.root,
            expected_uid=self.expected_uid,
        )

        created_user = created_group = False
        support_uid = self.expected_uid
        support_gid = self.expected_gid
        transaction = FileTransaction()
        try:
            if self.manage_account:
                support_uid, support_gid, created_user, created_group = (
                    self._ensure_account()
                )
            self._mkdir(self.layout.etc, 0o755, self.expected_uid, self.expected_gid)
            self._mkdir(self.layout.state, 0o755, self.expected_uid, self.expected_gid)
            self._mkdir(self.layout.state / "home", 0o700, support_uid, support_gid)
            self._mkdir(
                self.layout.units,
                0o755,
                self.expected_uid,
                self.expected_gid,
                enforce_existing=False,
            )
            self._mkdir(
                self.layout.path("/run/sshd"),
                0o755,
                self.expected_uid,
                self.expected_gid,
                enforce_existing=False,
            )
            self._mkdir(
                self.layout.installed_helper.parent,
                0o755,
                self.expected_uid,
                self.expected_gid,
                enforce_existing=False,
            )
            host_key = self.layout.etc / "ssh_host_ed25519_key"
            host_public = Path(str(host_key) + ".pub")
            validate_managed_path(
                host_key, root=self.layout.root, expected_uid=self.expected_uid
            )
            validate_managed_path(
                host_public, root=self.layout.root, expected_uid=self.expected_uid
            )
            new_host_key: bytes | None = None
            new_host_public: bytes | None = None
            if host_key.exists():
                private_info = host_key.lstat()
                if (
                    not stat.S_ISREG(private_info.st_mode)
                    or stat.S_ISLNK(private_info.st_mode)
                    or private_info.st_uid != self.expected_uid
                    or private_info.st_mode & 0o077
                ):
                    raise SecurityError("existing support SSH host key is unsafe")
                if not host_public.exists():
                    raise SecurityError(
                        "existing support SSH host public key is missing"
                    )
                public_info = host_public.lstat()
                if (
                    not stat.S_ISREG(public_info.st_mode)
                    or stat.S_ISLNK(public_info.st_mode)
                    or public_info.st_uid != self.expected_uid
                    or public_info.st_mode & 0o022
                ):
                    raise SecurityError(
                        "existing support SSH host public key is unsafe"
                    )
                self._verify_host_key_pair(host_key, host_public)
                self._validate_candidate(sshd, config)
            elif self.generate_host_key:
                with tempfile.TemporaryDirectory(
                    prefix="robopark-support-hostkey-"
                ) as directory:
                    temporary_key = Path(directory) / "ssh_host_ed25519_key"
                    self._run(
                        [
                            "ssh-keygen",
                            "-q",
                            "-t",
                            "ed25519",
                            "-N",
                            "",
                            "-f",
                            str(temporary_key),
                        ]
                    )
                    new_host_key = temporary_key.read_bytes()
                    new_host_public = Path(str(temporary_key) + ".pub").read_bytes()
                    self._verify_host_key_pair(
                        temporary_key, Path(str(temporary_key) + ".pub")
                    )
                    self._validate_candidate(sshd, config, host_key=temporary_key)
            else:
                self._validate_candidate(sshd, config)
            source = Path(__file__).read_bytes()
            self._helper_managed_or_equal(self.layout.installed_helper, source)
            if new_host_key is not None and new_host_public is not None:
                transaction.write(
                    host_key,
                    new_host_key,
                    0o600,
                    self.expected_uid,
                    self.expected_gid,
                )
                transaction.write(
                    host_public,
                    new_host_public,
                    0o644,
                    self.expected_uid,
                    self.expected_gid,
                )
            transaction.write(
                authorized_path,
                MANAGED.encode() + public_key.encode(),
                0o644,
                self.expected_uid,
                self.expected_gid,
            )
            transaction.write(
                tuna_env_path,
                MANAGED.encode() + f"TUNA_TOKEN={token}\n".encode(),
                0o600,
                self.expected_uid,
                self.expected_gid,
            )
            transaction.write(
                self.layout.etc / "sshd_config",
                config.encode(),
                0o600,
                self.expected_uid,
                self.expected_gid,
            )
            transaction.write(
                self.layout.units / SSHD_UNIT,
                sshd_unit,
                0o644,
                self.expected_uid,
                self.expected_gid,
            )
            transaction.write(
                self.layout.units / TUNA_UNIT,
                tuna_unit,
                0o644,
                self.expected_uid,
                self.expected_gid,
            )
            transaction.write(
                self.layout.installed_helper,
                source,
                0o755,
                self.expected_uid,
                self.expected_gid,
            )
            self._run(["systemctl", "daemon-reload"])
            if enable:
                self.enable()
        except Exception:
            transaction.rollback()
            self._run(["systemctl", "daemon-reload"], check=False)
            if created_user:
                self._run(["userdel", ACCOUNT], check=False)
            if created_group:
                self._run(["groupdel", ACCOUNT], check=False)
            raise

    def _unit_state(self, action: str, unit: str) -> bool:
        return (
            self._run(["systemctl", action, "--quiet", unit], check=False).returncode
            == 0
        )

    def _unit_property(self, unit: str, name: str) -> str:
        result = self._run(
            ["systemctl", "show", f"--property={name}", "--value", unit],
            check=False,
            capture=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def _stable_active(self, unit: str) -> bool:
        if not self._unit_state("is-active", unit):
            return False
        main_pid = self._unit_property(unit, "MainPID")
        restarts = self._unit_property(unit, "NRestarts")
        if not main_pid.isdigit() or int(main_pid) <= 0 or not restarts.isdigit():
            return False
        for _ in range(self.stability_checks):
            time.sleep(self.stability_interval)
            if (
                not self._unit_state("is-active", unit)
                or self._unit_property(unit, "MainPID") != main_pid
                or self._unit_property(unit, "NRestarts") != restarts
            ):
                return False
        return True

    def _snapshot_units(self) -> dict[str, tuple[bool, bool]]:
        return {
            unit: (
                self._unit_state("is-enabled", unit),
                self._unit_state("is-active", unit),
            )
            for unit in (SSHD_UNIT, TUNA_UNIT)
        }

    def _restore_units(self, before: dict[str, tuple[bool, bool]]) -> None:
        for unit in (SSHD_UNIT, TUNA_UNIT):
            enabled, _ = before[unit]
            self._run(
                ["systemctl", "enable" if enabled else "disable", unit],
                check=False,
            )
        for unit in (SSHD_UNIT, TUNA_UNIT):
            _, active = before[unit]
            self._run(
                ["systemctl", "start" if active else "stop", unit],
                check=False,
            )

    @staticmethod
    def _listener_ready() -> bool:
        for _ in range(20):
            try:
                with socket.create_connection(
                    ("127.0.0.1", 2222), timeout=0.5
                ) as stream:
                    banner = stream.recv(64)
                    if banner.startswith(b"SSH-"):
                        return True
            except OSError:
                pass
            time.sleep(0.25)
        return False

    def enable(self) -> None:
        before = self._snapshot_units()
        try:
            self._run(["systemctl", "enable", "--now", SSHD_UNIT])
            if not self.listener_probe() or not self._stable_active(SSHD_UNIT):
                raise RuntimeError("support SSH did not become ready on 127.0.0.1:2222")
            self._run(["systemctl", "enable", "--now", TUNA_UNIT])
            if not self._stable_active(TUNA_UNIT):
                raise RuntimeError(
                    "support Tuna process did not remain stable; public reachability "
                    "requires an external SSH check"
                )
        except Exception:
            self._restore_units(before)
            raise

    def disable(self) -> None:
        self._run(["systemctl", "disable", "--now", TUNA_UNIT, SSHD_UNIT])

    def status(self) -> dict:
        result = {}
        for label, unit in (("sshd", SSHD_UNIT), ("tuna", TUNA_UNIT)):
            result[label] = {
                "enabled": self._unit_state("is-enabled", unit),
                "active": self._unit_state("is-active", unit),
            }
        private_host_key = self.layout.etc / "ssh_host_ed25519_key"
        result["host_fingerprint"] = None
        validate_managed_path(
            private_host_key,
            root=self.layout.root,
            expected_uid=self.expected_uid,
        )
        if private_host_key.is_file() and not private_host_key.is_symlink():
            info = private_host_key.lstat()
            if info.st_uid != self.expected_uid or info.st_mode & 0o077:
                raise SecurityError("existing support SSH host key is unsafe")
            derived = self._derive_host_public_key(private_host_key)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix="robopark-host-public-"
            ) as temporary:
                temporary.write(derived + "\n")
                temporary.flush()
                probe = self._run(
                    ["ssh-keygen", "-lf", temporary.name, "-E", "sha256"],
                    check=False,
                    capture=True,
                )
                if probe.returncode == 0:
                    fields = probe.stdout.split()
                    result["host_fingerprint"] = fields[1] if len(fields) >= 2 else None
        return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Prepare and control optional Robopark support SSH"
    )
    commands = result.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="prepare isolated SSH and Tuna units")
    install.add_argument(
        "--public-key-file",
        type=Path,
        help="ed25519 public key (default: robopark_support_ed25519.pub beside helper)",
    )
    install.add_argument(
        "--tuna-env", type=Path, default=Path("/etc/robopark/tuna.env")
    )
    install.add_argument("--alias", default="ssh")
    install.add_argument("--location", default="ru")
    install.add_argument(
        "--enable", action="store_true", help="activate only after preparation"
    )
    commands.add_parser("enable", help="enable SSH first, then the Tuna tunnel")
    commands.add_parser("disable", help="disable only the two support units")
    commands.add_parser("status", help="show unit state and host key fingerprint")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if platform.system() != "Linux":
        print(
            "error: mutations and status are supported only on Linux", file=sys.stderr
        )
        return 2
    if os.geteuid() != 0:
        print("error: this command must run as root", file=sys.stderr)
        return 2
    manager = SshAccessManager()
    try:
        if args.command == "install":
            manager.install(
                args.public_key_file,
                tuna_env=args.tuna_env,
                alias=args.alias,
                location=args.location,
                enable=args.enable,
            )
            if args.enable:
                print(ACTIVE_NOTICE)
        elif args.command == "enable":
            manager.enable()
            print(ACTIVE_NOTICE)
        elif args.command == "disable":
            manager.disable()
        else:
            print(json.dumps(manager.status(), sort_keys=True))
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
