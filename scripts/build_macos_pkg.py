#!/usr/bin/env python3
"""Build the Mac application and one pkg; never install it on the build host."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

ROOT = Path(__file__).resolve().parents[1]


def package_name(version: str) -> str:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+.][A-Za-z0-9.]+)?", version):
        raise ValueError("invalid_version")
    return f"Robopark-{version}-arm64.pkg"


def _key(path: Path) -> Ed25519PrivateKey:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("update_key_requires_private_regular_file")
    return Ed25519PrivateKey.from_private_bytes(path.read_bytes())


def ensure_update_key(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(
                Ed25519PrivateKey.generate().private_bytes(
                    Encoding.Raw, PrivateFormat.Raw, NoEncryption()
                )
            )
    public = _key(path).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(public).decode("ascii")


def sign_catalog(
    package: Path, version: str, key: Path, *, generation: int, now: int
) -> dict[str, str]:
    if package.name != package_name(version) or generation <= 0:
        raise ValueError("invalid_package_identity")
    data = package.read_bytes()
    catalog = {
        "schema": 1,
        "generation": generation,
        "expires": now + 14 * 86400,
        "channels": {
            "rc": {
                "version": version,
                "url": f"/api/distribution/v1/macos/{package.name}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "length": len(data),
                "architecture": "arm64",
                "protocol": 1,
                "notes": "Офлайн-рабочее место, автоматическое подключение Mac, распределение Emergency и сжатие трафика.",
            }
        },
    }
    payload = json.dumps(
        catalog, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return {
        "payload": base64.b64encode(payload).decode("ascii"),
        "signature": base64.b64encode(_key(key).sign(payload)).decode("ascii"),
    }


def bundle_versions(version: str, now: int) -> dict[str, str]:
    package_name(version)
    date = datetime.fromtimestamp(now, timezone.utc)  # noqa: UP017 -- Python 3.9
    days = (
        date.date() - datetime(2020, 1, 1, tzinfo=timezone.utc).date()  # noqa: UP017 -- Python 3.9
    ).days + 1
    if not 0 < days <= 9999:
        raise ValueError("bundle_build_date_out_of_range")
    return {
        "CFBundleShortVersionString": version.split("-")[0].split("+")[0],
        "CFBundleVersion": f"{days}.{date.hour}.{date.minute}",
        "RoboparkReleaseVersion": version,
    }


def launch_agent() -> dict:
    return {
        "Label": "ru.robopark.agent",
        "ProgramArguments": [
            "/Applications/Robopark.app/Contents/MacOS/RoboparkAgent",
            "--background",
        ],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "LimitLoadToSessionType": "Aqua",
        "ProcessType": "Background",
    }


def update_daemon() -> dict:
    return {
        "Label": "ru.robopark.update-helper",
        "ProgramArguments": ["/Library/PrivilegedHelperTools/RoboparkUpdater"],
        "UserName": "root",
        "RunAtLoad": True,
        "StartInterval": 15 * 60,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }


def automatic_update_config(envelope: dict, public_key: str) -> dict:
    try:
        if set(envelope) != {"payload", "signature"}:
            raise ValueError
        payload = base64.b64decode(envelope["payload"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key, validate=True)
        ).verify(signature, payload)
        profile = json.loads(payload)
        if (
            profile.get("purpose") != "robopark-deployment-v1"
            or profile.get("origin") != "https://roboparktest.ru.tuna.am"
            or profile.get("instance_id") != "d6ed3b43-c41b-4614-833e-c5ec30936ef3"
            or profile.get("environment") != "test"
            or profile.get("channel") != "rc"
            or not re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                profile.get("instance_id", ""),
                re.IGNORECASE,
            )
            or type(profile.get("expires")) is not int
            or profile["expires"] <= int(time.time())
        ):
            raise ValueError
    except (
        ValueError,
        TypeError,
        KeyError,
        InvalidSignature,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("invalid_deployment_profile") from error
    return {
        "schema": 1,
        "origin": profile["origin"],
        "instance_id": profile["instance_id"],
        "environment": profile["environment"],
        "channel": profile["channel"],
    }


def postinstall_script() -> str:
    return """#!/bin/sh
set -eu
chown -R root:wheel /Applications/Robopark.app
chmod -R go-w /Applications/Robopark.app
chown root:wheel /Library/LaunchAgents/ru.robopark.agent.plist
chmod 644 /Library/LaunchAgents/ru.robopark.agent.plist
chown root:wheel /Library/LaunchDaemons/ru.robopark.update-helper.plist
chmod 644 /Library/LaunchDaemons/ru.robopark.update-helper.plist
chown root:wheel /Library/PrivilegedHelperTools/RoboparkUpdater
chmod 755 /Library/PrivilegedHelperTools/RoboparkUpdater
chown root:wheel '/Library/Application Support/RoboparkUpdater'
chmod 755 '/Library/Application Support/RoboparkUpdater'
chown root:wheel '/Library/Application Support/RoboparkUpdater/config.json' '/Library/Application Support/RoboparkUpdater/update-root.json'
chmod 644 '/Library/Application Support/RoboparkUpdater/config.json' '/Library/Application Support/RoboparkUpdater/update-root.json'
/bin/launchctl bootstrap system /Library/LaunchDaemons/ru.robopark.update-helper.plist 2>/dev/null || true
session_uid=$(/usr/bin/stat -f %u /dev/console)
case "$session_uid" in ''|*[!0-9]*) exit 0;; esac
if [ "$session_uid" -ge 501 ]; then
  disabled=$(/bin/launchctl print-disabled "gui/$session_uid" 2>/dev/null || true)
  if ! /usr/bin/grep -q '"ru.robopark.agent" => true' <<EOF
$disabled
EOF
  then
    /bin/launchctl bootstrap "gui/$session_uid" /Library/LaunchAgents/ru.robopark.agent.plist 2>/dev/null || true
  fi
fi
"""


def stage_workspace(dist: Path, target: Path, *, version: str, commit: str) -> dict:
    """Copy only the bounded public web build; private source/config never enters pkg."""
    if not dist.is_dir() or dist.is_symlink():
        raise ValueError("workspace_build_missing")
    files = sorted(dist.rglob("*"))
    if any(path.is_symlink() for path in files):
        raise ValueError("workspace_symlink")
    index = dist / "index.html"
    if not index.is_file():
        raise ValueError("workspace_index_missing")
    entries = re.findall(r'(?:src|href)=["\'](/assets/[^"\']+)["\']', index.read_text())
    for entry in entries:
        parts = Path(entry.removeprefix("/")).parts
        if ".." in parts or not (dist / entry.removeprefix("/")).is_file():
            raise ValueError("workspace_entry_asset_missing")
    allowed = {
        ".html",
        ".js",
        ".css",
        ".json",
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".ico",
        ".woff",
        ".woff2",
        ".webmanifest",
        ".wasm",
    }
    assets = [
        path
        for path in files
        if path.is_file()
        and path.suffix in allowed
        and not any(part.startswith(".") for part in path.relative_to(dist).parts)
    ]
    if (
        len(assets) > 4096
        or sum(path.stat().st_size for path in assets) > 128 * 1024 * 1024
    ):
        raise ValueError("workspace_build_too_large")
    manifest = {"version": version, "source_commit": commit, "files": {}}
    target.mkdir(parents=True)
    for path in assets:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("workspace_asset_too_large")
        relative = path.relative_to(dist)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        destination.write_bytes(data)
        manifest["files"][relative.as_posix()] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    (target / "workspace-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    )
    return manifest


def build(
    output: Path,
    *,
    key: Path,
    deployment_profile: Path,
    sign_application: str | None = None,
    sign_installer: str | None = None,
    notary_profile: str | None = None,
) -> dict:
    version = (ROOT / "VERSION").read_text().strip()
    filename = package_name(version)
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    source_clean = not subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=ROOT,
        text=True,
    ).strip()
    if bool(sign_application) != bool(sign_installer) or (
        notary_profile and not sign_installer
    ):
        raise ValueError("provide_both_developer_id_identities")
    output.mkdir(parents=True, exist_ok=True)
    public = ensure_update_key(key)
    try:
        profile_envelope = json.loads(deployment_profile.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("invalid_deployment_profile") from error
    updater_config = automatic_update_config(profile_envelope, public)
    web = ROOT / "apps/web"
    if json.loads((web / "package.json").read_text())["version"] != version:
        raise ValueError("workspace_version_mismatch")
    subprocess.run(["npm", "run", "build"], cwd=web, check=True)
    scratch = Path(tempfile.gettempdir()) / "robopark-macos-release-build"
    swift_options = [
        "--package-path",
        str(ROOT / "apps/macos"),
        "--scratch-path",
        str(scratch),
        "--cache-path",
        str(scratch / "cache"),
        "--config-path",
        str(scratch / "config"),
        "--security-path",
        str(scratch / "security"),
        "--disable-sandbox",
        "--configuration",
        "release",
        "--arch",
        "arm64",
    ]
    env = dict(
        os.environ,
        CLANG_MODULE_CACHE_PATH=str(scratch / "clang"),
        SWIFT_MODULECACHE_PATH=str(scratch / "swift"),
    )
    subprocess.run(
        ["swift", "build", *swift_options, "--product", "RoboparkAgent"],
        env=env,
        check=True,
    )
    subprocess.run(
        ["swift", "build", *swift_options, "--product", "RoboparkUpdater"],
        env=env,
        check=True,
    )
    bin_path = Path(
        subprocess.check_output(
            ["swift", "build", *swift_options, "--show-bin-path"], env=env, text=True
        ).strip()
    )
    with tempfile.TemporaryDirectory(prefix="robopark-pkg-") as temporary:
        work = Path(temporary)
        payload = work / "payload"
        app = payload / "Applications/Robopark.app"
        resources = app / "Contents/Resources"
        executable = app / "Contents/MacOS/RoboparkAgent"
        executable.parent.mkdir(parents=True)
        resources.mkdir()
        shutil.copy2(bin_path / "RoboparkAgent", executable)
        executable.chmod(0o755)
        architecture = subprocess.check_output(
            ["lipo", "-archs", str(executable)], text=True
        ).strip()
        if architecture != "arm64":
            raise ValueError("unexpected_binary_architecture")
        subprocess.run(["strip", "-S", str(executable)], check=True)
        shutil.copy2(ROOT / "apps/macos/Resources/panel.html", resources / "panel.html")
        workspace = stage_workspace(
            web / "dist", resources / "workspace", version=version, commit=source_commit
        )
        (resources / "update-root.json").write_text(
            json.dumps({"ed25519": public}) + "\n"
        )
        subprocess.run(
            [
                "openssl",
                "x509",
                "-in",
                str(
                    ROOT
                    / "apps/api/src/robopark_api/certificates/YandexInternalRootCA.crt"
                ),
                "-outform",
                "DER",
                "-out",
                str(resources / "YandexInternalRootCA.der"),
            ],
            check=True,
        )
        info = {
            "CFBundleIdentifier": "ru.robopark.agent",
            "RoboparkSourceCommit": source_commit,
            "RoboparkSourceClean": source_clean,
            "CFBundleName": "Robopark",
            "CFBundleDisplayName": "Robopark",
            "CFBundleExecutable": "RoboparkAgent",
            "CFBundlePackageType": "APPL",
            **bundle_versions(version, int(time.time())),
            "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            "NSCameraUsageDescription": "Сканирование QR-кодов роботов и добавление фотографий к рабочим задачам.",
            "NSMicrophoneUsageDescription": "Запись голосовых вложений по вашему запросу.",
            "RoboparkWorkspaceVersion": version,
        }
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        launch_dir = payload / "Library/LaunchAgents"
        launch_dir.mkdir(parents=True)
        (launch_dir / "ru.robopark.agent.plist").write_bytes(
            plistlib.dumps(launch_agent())
        )
        signing = [
            "codesign",
            "--force",
            "--sign",
            sign_application or "-",
            "--options",
            "runtime",
        ]
        if sign_application:
            signing.append("--timestamp")
        helper_dir = payload / "Library/PrivilegedHelperTools"
        helper_dir.mkdir(parents=True)
        helper = helper_dir / "RoboparkUpdater"
        shutil.copy2(bin_path / "RoboparkUpdater", helper)
        helper.chmod(0o755)
        subprocess.run(["strip", "-S", str(helper)], check=True)
        subprocess.run([*signing, str(helper)], check=True)
        subprocess.run(["codesign", "--verify", "--strict", str(helper)], check=True)
        daemon_dir = payload / "Library/LaunchDaemons"
        daemon_dir.mkdir(parents=True)
        (daemon_dir / "ru.robopark.update-helper.plist").write_bytes(
            plistlib.dumps(update_daemon())
        )
        helper_support = payload / "Library/Application Support/RoboparkUpdater"
        helper_support.mkdir(parents=True)
        (helper_support / "config.json").write_text(
            json.dumps(updater_config, sort_keys=True, separators=(",", ":")) + "\n"
        )
        (helper_support / "update-root.json").write_text(
            json.dumps({"ed25519": public}) + "\n"
        )
        scripts = work / "scripts"
        scripts.mkdir()
        (scripts / "preinstall").write_text("""#!/bin/sh
set -eu
if [ -L /Applications/Robopark.app ]; then
  echo 'Refusing a symbolic-link application destination.' >&2
  exit 1
fi
""")
        (scripts / "postinstall").write_text(postinstall_script())
        for script in scripts.iterdir():
            script.chmod(0o755)
            subprocess.run(["sh", "-n", str(script)], check=True)
        subprocess.run([*signing, str(app)], check=True)
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(app)], check=True
        )
        # .pkg version is numeric; the app and signed catalog retain full semver.
        numeric = info["CFBundleVersion"]
        package = output / filename
        command = [
            "pkgbuild",
            "--root",
            str(payload),
            "--identifier",
            "ru.robopark.agent",
            "--version",
            numeric,
            "--ownership",
            "recommended",
            "--scripts",
            str(scripts),
            "--install-location",
            "/",
        ]
        if sign_installer:
            command += ["--sign", sign_installer]
        subprocess.run([*command, str(package)], check=True)
        if notary_profile:
            subprocess.run(
                [
                    "xcrun",
                    "notarytool",
                    "submit",
                    str(package),
                    "--keychain-profile",
                    notary_profile,
                    "--wait",
                ],
                check=True,
            )
            subprocess.run(["xcrun", "stapler", "staple", str(package)], check=True)
        subprocess.run([str(executable), "--version"], check=True)
    now = int(time.time())
    catalog = sign_catalog(package, version, key, generation=time.time_ns(), now=now)
    (output / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")
    (output / "update-root.json").write_text(json.dumps({"ed25519": public}) + "\n")
    report = {
        "version": version,
        "package": package.name,
        "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "bytes": package.stat().st_size,
        "architecture": "arm64",
        "minimum_macos": "13.0",
        "developer_id_signed": bool(sign_application and sign_installer),
        "notarized": bool(notary_profile),
        "metadata_signed": True,
        "physical_install_tested": False,
        "source_commit": source_commit,
        "source_clean": source_clean,
        "workspace_assets": len(workspace["files"]),
        "workspace_bytes": sum(row["size"] for row in workspace["files"].values()),
    }
    final_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    final_clean = not subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=ROOT,
        text=True,
    ).strip()
    if final_commit != source_commit or (source_clean and not final_clean):
        package.unlink(missing_ok=True)
        (output / "catalog.json").unlink(missing_ok=True)
        raise ValueError("source_changed_during_macos_build")
    (output / "macos-build.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--update-key", type=Path, default=ROOT / ".release-secrets/macos-update.key"
    )
    parser.add_argument("--deployment-profile", type=Path, required=True)
    parser.add_argument("--sign-application")
    parser.add_argument("--sign-installer")
    parser.add_argument("--notary-profile")
    args = parser.parse_args()
    report = build(
        args.output.resolve(),
        key=args.update_key.resolve(),
        deployment_profile=args.deployment_profile.resolve(),
        sign_application=args.sign_application,
        sign_installer=args.sign_installer,
        notary_profile=args.notary_profile,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
