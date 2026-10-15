import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_current_candidate_accepts_previous_documented_candidates():
    version = (ROOT / "VERSION").read_text().strip()
    match = re.fullmatch(r"(\d+\.\d+\.\d+)-rc\.(\d+)", version)
    if match is None:
        return
    metadata = json.loads((ROOT / "deploy/release-metadata.json").read_text())
    compatible = set(metadata["compatible_from_versions"])
    for note in (ROOT / "docs/releases").glob(f"{match[1]}-rc.*.md"):
        previous = re.fullmatch(re.escape(match[1]) + r"-rc\.(\d+)", note.stem)
        if previous and int(previous[1]) < int(match[2]):
            assert note.stem in compatible, f"OTA must accept documented predecessor {note.stem}"


def test_release_note_and_compatibility_match_release_metadata():
    spec = importlib.util.spec_from_file_location(
        "release_docs", ROOT / "scripts/generate-release-notes.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_note(ROOT)
    assert json.loads(
        (ROOT / "docs/releases/compatibility.json").read_text()
    ) == module.expected_compatibility(ROOT)


def test_release_notes_disclose_unverified_long_gates():
    note = (ROOT / "docs/releases/0.2.0-rc.1.md").read_text()
    assert "нагрузочный" in note and "soak" in note and "не опубликован" in note


def test_unified_ota_runbooks_cover_build_install_web_and_recovery():
    paths = [
        ROOT / "docs/runbooks/build-ota.md",
        ROOT / "docs/runbooks/usb-clean-install.md",
        ROOT / "docs/runbooks/web-ota-update.md",
        ROOT / "docs/runbooks/ota-recovery.md",
    ]
    text = "\n".join(path.read_text() for path in paths)
    required = [
        "./scripts/build-ota.sh /absolute/output/directory",
        "robopark-<версия>.ota",
        "sudo python3 robopark-<версия>.ota",
        "УДАЛИТЬ ВСЕ ДАННЫЕ",
        "только на хосте без файлов, служб и Docker-ресурсов Robopark",
        "точные Docker-объекты Robopark с измеренным",
        "Система → Обновление",
        "diagnose --output",
        "ota_hash_mismatch",
        "rollback",
        "Ubuntu 22.04+",
        "Python 3.10+",
        "не аутентифицирует",
    ]
    assert all(item in text for item in required)
