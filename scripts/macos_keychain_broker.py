"""Verify the immutable public TEST Keychain broker; never rebuild it in a release."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "deploy/macos-keychain-broker/v1/arm64"
IDENTIFIER = "ru.robopark.keychain-broker.v1"
INSTALL_DIR = "/Library/Application Support/RoboparkKeychainBroker/v1"
EXECUTABLE = "RoboparkKeychainBroker"


def source_digest(root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    for folder in ("KeychainBrokerIPC", "RoboparkKeychainBroker"):
        base = root / "apps/macos/Sources" / folder
        files = sorted(p for p in base.rglob("*") if p.is_file())
        if not files or any(p.is_symlink() for p in files):
            raise ValueError("broker_source_missing_or_symlink")
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def signature_metadata(binary: Path) -> tuple[str, str]:
    subprocess.run(["codesign", "--verify", "--strict", str(binary)], check=True,
                   capture_output=True)
    details = subprocess.run(["codesign", "-d", "--verbose=4", str(binary)], check=True,
                             capture_output=True, text=True).stderr
    cdhash = re.search(r"^CDHash=([0-9a-f]{40})$", details, re.MULTILINE)
    if not cdhash or f"Identifier={IDENTIFIER}\n" not in details or "runtime" not in details:
        raise ValueError("broker_signature_invalid")
    with tempfile.TemporaryDirectory(prefix="broker-public-cert-") as tmp:
        prefix = str(Path(tmp) / "cert")
        subprocess.run(["codesign", "-d", f"--extract-certificates={prefix}", str(binary)],
                       check=True, capture_output=True)
        certificate = hashlib.sha256(Path(prefix + "0").read_bytes()).hexdigest()
    return cdhash.group(1), certificate


def verify_frozen(directory: Path = FROZEN, *, root: Path = ROOT) -> dict:
    binary = directory / EXECUTABLE
    manifest_path = directory / "manifest.json"
    for path in (binary, manifest_path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("broker_frozen_file_not_regular")
    manifest = json.loads(manifest_path.read_text())
    pin = json.loads((root / "deploy/macos-test-signing.json").read_text())["certificate_sha256"]
    if (manifest.get("schema") != 1 or manifest.get("protocol") != 1
            or manifest.get("identifier") != IDENTIFIER or manifest.get("architecture") != "arm64"
            or manifest.get("certificate_sha256") != pin
            or manifest.get("sha256") != hashlib.sha256(binary.read_bytes()).hexdigest()
            or manifest.get("source_sha256") != source_digest(root)):
        raise ValueError("immutable_broker_mismatch_requires_explicit_migration")
    if subprocess.check_output(["lipo", "-archs", str(binary)], text=True).strip() != "arm64":
        raise ValueError("broker_architecture_invalid")
    cdhash, certificate = signature_metadata(binary)
    if cdhash != manifest.get("cdhash") or certificate != pin:
        raise ValueError("broker_identity_mismatch")
    return manifest


def stage_frozen(payload: Path, *, directory: Path = FROZEN) -> dict:
    manifest = verify_frozen(directory)
    destination = payload / INSTALL_DIR.lstrip("/")
    destination.mkdir(parents=True)
    for name in (EXECUTABLE, "manifest.json"):
        shutil.copyfile(directory / name, destination / name)
    (destination / EXECUTABLE).chmod(0o755)
    (destination / "manifest.json").chmod(0o644)
    return manifest


def launch_agent() -> dict:
    return {"Label": IDENTIFIER, "ProgramArguments": [f"{INSTALL_DIR}/{EXECUTABLE}"],
            "MachServices": {IDENTIFIER: True}, "LimitLoadToSessionType": "Aqua",
            "ProcessType": "Background", "ThrottleInterval": 10}


def preinstall_script(sha256: str) -> str:
    if not re.fullmatch("[0-9a-f]{64}", sha256):
        raise ValueError("broker_hash_invalid")
    return f"""# Broker v1 must remain byte-identical across normal updates.
for item in '/Library/Application Support/RoboparkKeychainBroker' '{INSTALL_DIR}'; do
  if [ -e "$item" ] || [ -L "$item" ]; then
    if [ ! -d "$item" ] || [ -L "$item" ] || [ "$(/usr/bin/stat -f %u "$item")" != 0 ] || [ $((8#$(/usr/bin/stat -f %Lp "$item") & 8#022)) -ne 0 ]; then
      echo 'Unsafe Robopark Keychain broker directory.' >&2; exit 1
    fi
  fi
done
broker='{INSTALL_DIR}/{EXECUTABLE}'
if [ -e "$broker" ] || [ -L "$broker" ]; then
  if [ ! -f "$broker" ] || [ -L "$broker" ] || [ "$(/usr/bin/stat -f %u "$broker")" != 0 ] || [ "$(/usr/bin/shasum -a 256 "$broker" | /usr/bin/awk '{{print $1}}')" != '{sha256}' ]; then
    echo 'Keychain broker differs; explicit migration is required.' >&2; exit 1
  fi
fi
"""


def postinstall_permissions() -> str:
    return f"""chown -R root:wheel '/Library/Application Support/RoboparkKeychainBroker'
chmod 755 '/Library/Application Support/RoboparkKeychainBroker' '{INSTALL_DIR}' '{INSTALL_DIR}/{EXECUTABLE}'
chmod 644 '{INSTALL_DIR}/manifest.json'
chown root:wheel /Library/LaunchAgents/{IDENTIFIER}.plist
chmod 644 /Library/LaunchAgents/{IDENTIFIER}.plist
"""


def register_script() -> str:
    return f"""if ! /bin/launchctl print "gui/$session_uid/{IDENTIFIER}" >/dev/null 2>&1; then
  /bin/launchctl bootstrap "gui/$session_uid" /Library/LaunchAgents/{IDENTIFIER}.plist
fi
"""
