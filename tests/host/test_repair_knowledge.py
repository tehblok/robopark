from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from scripts.repair_knowledge import compile_export
from scripts.repair_knowledge.normalize import extract_error_codes

DEFECT_FIELD = "60df26695151a36df681d67b--theDefectCode"


def _record(
    key: str,
    *,
    robot: str = "a101",
    created: str = "2026-01-01T10:00:00+0000",
    comment: str = "Заменил переднюю камеру. Проверка пройдена, камера работает.",
    comment_id: str = "1",
    method: str = "CHANGE",
    defect: object = "EL-10",
    changelog: list[dict[str, object]] | None = None,
    links: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    issue = {
        "key": key,
        "summary": f"[{robot}] ERROR недоступна передняя камера",
        "description": "Камера не отвечает, код camera_timeout. Нужно проверить кабель.",
        "createdAt": created,
        "resolvedAt": created,
        "components": [{"id": "1", "display": "ROBOT_CAMERA_FRONT"}],
        "solutionMethod": method,
        DEFECT_FIELD: defect,
        "resolution": {"key": "fixed", "display": "Решен"},
        "statusType": {"key": "done", "display": "Завершен"},
    }
    return {
        "key": key,
        "sections": {
            "issue": {"ok": True, "value": issue},
            "comments": {
                "ok": True,
                "value": [
                    {
                        "id": comment_id,
                        "text": comment,
                        "createdAt": created,
                    }
                ],
            },
            "changelog": {"ok": True, "value": changelog or []},
            "links": {"ok": True, "value": links or []},
        },
    }


def _write(source: Path, name: str, payload: object) -> None:
    records = source / "records"
    records.mkdir(parents=True, exist_ok=True)
    (records / name).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_compile_distills_evidence_and_never_treats_proposals_as_actions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "export"
    output = tmp_path / "knowledge"
    first = _record(
        "FLEET-1",
        comment="Заменил переднюю камеру. Заменил крепление. Проверка пройдена, камера работает.",
        comment_id="101",
    )
    first["sections"]["issue"]["value"]["description"] += "\nMessage: LinkDown"
    _write(source, "one.json", first)
    _write(
        source,
        "two.json",
        _record(
            "FLEET-2",
            robot="a102",
            comment="> [В ответ на сообщение]\n> Заменил камеру\nНужно заменить камеру и проверить.",
            comment_id="102",
        ),
    )
    _write(
        source,
        "three.json",
        _record(
            "FLEET-3",
            robot="a103",
            comment="Перезапустил модуль камеры. Ошибка вернулась, результата нет.",
            comment_id="103",
            method="RESTART",
            changelog=[
                {
                    "id": "change-1",
                    "updatedAt": "2026-01-01T11:00:00+0000",
                    "fields": [
                        {
                            "field": {"id": "status", "display": "Статус"},
                            "from": {"key": "closed"},
                            "to": {"key": "reopened"},
                        }
                    ],
                }
            ],
        ),
    )
    _write(
        source,
        "four.json",
        _record(
            "FLEET-4",
            robot="a104",
            comment="Камера работает. Затем заменил крепление.",
            comment_id="104",
        ),
    )
    _write(
        source,
        "duplicate.json",
        _record(
            "FLEET-4",
            robot="a104",
            comment="Камера работает. Затем заменил крепление.",
            comment_id="104",
        ),
    )
    _write(
        source,
        "linked-duplicate.json",
        _record(
            "FLEET-5",
            robot="a104",
            comment_id="105",
            links=[
                {
                    "id": "link-1",
                    "type": {"id": "duplicates", "display": "Дубликат"},
                    "object": {"key": "FLEET-4", "display": "duplicate"},
                }
            ],
        ),
    )

    report = compile_export(source, output)

    cases = _jsonl(output / "cases.jsonl")
    cards = _jsonl(output / "cards.jsonl")
    rag = _jsonl(output / "rag-seed.jsonl")
    by_key = {case["issue_key"]: case for case in cases}
    assert report["records_duplicate"] == 1
    assert len(cases) == 5
    assert by_key["FLEET-1"]["components"] == [
        {"code": "ROBOT_CAMERA_FRONT", "label": "Передняя камера"}
    ]
    assert by_key["FLEET-1"]["defect_code"] == "EL-10"
    assert by_key["FLEET-1"]["error_codes"] == ["CAMERA_TIMEOUT", "LINKDOWN"]
    assert by_key["FLEET-1"]["declared_method"] == "CHANGE"
    assert by_key["FLEET-1"]["observed_actions"][0]["method"] == "CHANGE"
    assert by_key["FLEET-1"]["observed_actions"][0]["evidence_ref"] == "comment:101"
    assert by_key["FLEET-2"]["observed_actions"] == []
    assert "missing_observed_action" in by_key["FLEET-2"]["quality_flags"]
    assert by_key["FLEET-3"]["outcome"]["negative"] is True
    assert by_key["FLEET-3"]["outcome"]["reopened"] is True
    assert by_key["FLEET-3"]["training_eligible"] is False
    assert by_key["FLEET-3"]["review_required"] is True
    assert by_key["FLEET-4"]["outcome"]["explicitly_verified"] is False
    assert by_key["FLEET-4"]["incident_id"] == by_key["FLEET-5"]["incident_id"]
    assert len(cards) == 1
    assert cards[0]["supporting_incidents"] == 3
    assert cards[0]["method_counts"] == {"CHANGE": 2, "RESTART": 1}
    assert cards[0]["error_codes"] == ["CAMERA_TIMEOUT"]
    assert cards[0]["error_code_incident_counts"] == {
        "CAMERA_TIMEOUT": 3,
        "LINKDOWN": 1,
    }
    assert cards[0]["outcome_counts"]["negative"] == 1
    assert cards[0]["limitations"]
    assert len(rag) == 1
    assert set(rag[0]) == {"title", "content", "kind", "source_ref"}
    assert rag[0]["kind"] == "note"
    assert rag[0]["source_ref"].startswith("repair-card:v1:")
    assert "FLEET-" not in rag[0]["content"]
    assert "repair-case:v1:" not in rag[0]["content"]
    assert "CAMERA_TIMEOUT" in rag[0]["content"]
    assert "LINKDOWN" not in rag[0]["content"]
    assert "a101" not in rag[0]["content"]


def test_compile_keeps_partial_cases_and_marks_possible_repeat_conservatively(
    tmp_path: Path,
) -> None:
    source = tmp_path / "export"
    first = _record("FLEET-10", created="2026-02-01T10:00:00+0000", defect="n/a")
    second = _record("FLEET-11", created="2026-02-20T10:00:00+0000", defect="0")
    second["sections"]["comments"]["value"][0]["text"] = (
        "Камеру не заменил. Камера работает нестабильно. Ошибка осталась."
    )
    _write(source, "first.json", first)
    _write(source, "second.json", second)
    _write(
        source,
        "partial.json",
        {
            "key": "FLEET-12",
            "sections": {
                "issue": {
                    "ok": True,
                    "value": {"key": "FLEET-12", "summary": "[a999] повреждение"},
                },
                "comments": {"ok": False, "error": "timeout"},
            },
        },
    )
    output = tmp_path / "knowledge"

    compile_export(source, output)

    cases = {case["issue_key"]: case for case in _jsonl(output / "cases.jsonl")}
    assert cases["FLEET-10"]["defect_code"] is None
    assert cases["FLEET-10"]["defect_raw"] == "n/a"
    assert cases["FLEET-11"]["possible_repeat_30d"] is True
    assert cases["FLEET-11"]["observed_actions"] == []
    assert cases["FLEET-11"]["outcome"]["explicitly_verified"] is False
    assert cases["FLEET-12"]["quality_flags"] == [
        "missing_changelog_section",
        "missing_comments_section",
        "missing_component",
        "missing_defect_code",
        "missing_links_section",
        "missing_observed_action",
    ]
    taxonomy = json.loads((output / "taxonomy.json").read_text(encoding="utf-8"))
    assert taxonomy["defect_codes"]["unknown"] == 3


def test_compile_is_stable_private_and_rejects_symlinked_records(
    tmp_path: Path,
) -> None:
    source = tmp_path / "export"
    _write(source, "record.json", _record("FLEET-20"))
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"

    compile_export(source, first_output)
    compile_export(source, second_output)

    first_case = _jsonl(first_output / "cases.jsonl")[0]
    second_case = _jsonl(second_output / "cases.jsonl")[0]
    assert first_case["case_id"] == second_case["case_id"]
    assert stat.S_IMODE(first_output.stat().st_mode) == 0o700
    for name in (
        "cases.jsonl",
        "cards.jsonl",
        "rag-seed.jsonl",
        "taxonomy.json",
        "report.json",
    ):
        assert stat.S_IMODE((first_output / name).stat().st_mode) == 0o600

    symlink_source = tmp_path / "symlink-export"
    records = symlink_source / "records"
    records.mkdir(parents=True)
    (records / "linked.json").symlink_to(source / "records" / "record.json")
    with pytest.raises(ValueError, match="symlink"):
        compile_export(symlink_source, tmp_path / "rejected")

    linked_source = tmp_path / "linked-source"
    linked_source.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        compile_export(linked_source, tmp_path / "rejected-source")


def test_generic_tracker_templates_do_not_become_repair_cards(tmp_path: Path) -> None:
    source = tmp_path / "export"
    for number in range(3):
        record = _record(f"FLEET-{30 + number}", robot=f"a20{number}")
        issue = record["sections"]["issue"]["value"]
        issue["summary"] = f"[a20{number}] Other by SUF"
        issue["description"] = (
            "Робот находится под управлением системы SUF. Classificator: Other"
        )
        _write(source, f"generic-{number}.json", record)

    compile_export(source, tmp_path / "knowledge")

    cases = _jsonl(tmp_path / "knowledge" / "cases.jsonl")
    assert all("generic_symptom" in case["quality_flags"] for case in cases)
    assert _jsonl(tmp_path / "knowledge" / "cards.jsonl") == []


def test_template_comment_becomes_symptom_but_foreign_vehicle_domain_stays_audit_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "export"
    for number in range(3):
        record = _record(f"FLEET-{40 + number}", robot=f"a30{number}")
        issue = record["sections"]["issue"]["value"]
        issue["summary"] = f"[a30{number}] Camera by operator"
        issue["description"] = (
            "**Classificator:** Camera\n**Comment:** Передняя камера не отвечает"
        )
        _write(source, f"robot-{number}.json", record)
    foreign = _record("FLEET-50", robot="a500")
    foreign_issue = foreign["sections"]["issue"]["value"]
    foreign_issue["components"] = [{"id": "9", "display": "TRUCK_ENGINE_AGGREGATES"}]
    _write(source, "foreign.json", foreign)

    compile_export(source, tmp_path / "knowledge")

    cases = {
        case["issue_key"]: case
        for case in _jsonl(tmp_path / "knowledge" / "cases.jsonl")
    }
    cards = _jsonl(tmp_path / "knowledge" / "cards.jsonl")
    assert cases["FLEET-40"]["symptom_category"] == "camera_unavailable"
    assert cases["FLEET-40"]["symptom_excerpt"] == "Передняя камера не отвечает"
    assert "generic_symptom" not in cases["FLEET-40"]["quality_flags"]
    assert cases["FLEET-50"]["non_robot_domain_components"] == [
        "TRUCK_ENGINE_AGGREGATES"
    ]
    assert "non_robot_domain_component" in cases["FLEET-50"]["quality_flags"]
    assert len(cards) == 1


def test_administrative_pause_is_not_install_and_post_action_test_can_verify(
    tmp_path: Path,
) -> None:
    source = tmp_path / "export"
    administrative = _record(
        "FLEET-60",
        comment="Робота поставил на паузу. Блокер поставил в очередь.",
        comment_id="601",
    )
    administrative["sections"]["issue"]["value"]["description"] = "Message: LinkDown"
    verified = _record(
        "FLEET-61",
        comment="Починил разъём камеры. Тестовый проезд выполнен, ошибок не выявлено.",
        comment_id="602",
    )
    _write(source, "administrative.json", administrative)
    _write(source, "verified.json", verified)

    compile_export(source, tmp_path / "knowledge")

    cases = {
        case["issue_key"]: case
        for case in _jsonl(tmp_path / "knowledge" / "cases.jsonl")
    }
    assert cases["FLEET-60"]["observed_actions"] == []
    assert cases["FLEET-60"]["error_codes"] == ["LINKDOWN"]
    assert cases["FLEET-61"]["observed_actions"][0]["method"] == "REPAIR"
    assert cases["FLEET-61"]["outcome"]["explicitly_verified"] is True
    assert cases["FLEET-61"]["outcome"]["evidence_refs"]["explicitly_verified"] == [
        "comment:602"
    ]


def test_hud_state_codes_are_complete_and_overlong_tokens_are_rejected() -> None:
    complete = "CONFIGMISMATCHWITHSENSORPROFILE1234567890"
    overlong = "X" * 129

    assert extract_error_codes(f"CRIT: /System/Camera/Configuration: {complete}") == [
        f"HUD:/SYSTEM/CAMERA/CONFIGURATION:{complete}"
    ]
    assert extract_error_codes(f"CRIT: /System/Camera/Configuration: {overlong}") == []
