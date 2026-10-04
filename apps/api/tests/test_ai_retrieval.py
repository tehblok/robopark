"""Synthetic relevance regressions: private repair texts never enter the repo."""

from robopark_api.ai_schemas import DocumentIn
from robopark_api.services.ai import knowledge


def put(db, user, title, text, kind="manual", **extra):
    row, _ = knowledge.add(
        db, user, DocumentIn(title=title, content=text, kind=kind, state="active", **extra)
    )
    db.flush()
    return row


def test_procedure_beats_repeated_chat_and_catalog(db_session, seed_admin):
    guide = put(
        db_session,
        seed_admin,
        "Замена камеры",
        "Отключите питание. Снимите камеру, разъединив разъём.",
    )
    for i in range(45):
        put(db_session, seed_admin, f"Переписка {i}", "Камера камеры камеру " * 15 + str(i), "chat")
    put(db_session, seed_admin, "Справочник деталей", "Камера кронштейн камеры " * 20, "note")
    found = knowledge.search(db_session, seed_admin, "Как заменить камеру?")
    assert found[0]["id"] == guide.id


def test_specific_component_and_code_cannot_be_replaced_by_partial_match(db_session, seed_admin):
    put(db_session, seed_admin, "Камера EL-02", "EL-02 камера нет связи " * 20, "ticket")
    put(
        db_session, seed_admin, "MotorControl EL-10", "MotorControl EL-10 нет изображения", "ticket"
    )
    correct = put(
        db_session,
        seed_admin,
        "MotorControl EL-02",
        "MotorControl: EL-02, проверить разъём.",
        "ticket",
    )
    found = knowledge.search(db_session, seed_admin, "Ошибка Motor Control EL02")
    assert found and all(s["id"] == correct.id for s in found)


def test_wrong_position_does_not_win_from_repetition(db_session, seed_admin):
    put(db_session, seed_admin, "Заднее правое колесо", "Замена заднего правого колеса. " * 20)
    correct = put(
        db_session,
        seed_admin,
        "Переднее левое колесо",
        "Снимите переднее левое колесо после фиксации робота.",
    )
    found = knowledge.search(db_session, seed_admin, "Как снять переднее левое колесо?")
    assert found and all(s["id"] == correct.id for s in found)


def test_unknown_subject_does_not_match_only_generic_repair_words(db_session, seed_admin):
    put(
        db_session,
        seed_admin,
        "Замена камеры робота",
        "Как заменить камеру робота. Проверка после ремонта.",
    )
    assert knowledge.search(db_session, seed_admin, "Как заменить квантовый реактор робота?") == []


def test_incomplete_manual_remains_unverified_evidence(db_session, seed_admin):
    put(
        db_session,
        seed_admin,
        "Неполная инструкция: замена камеры",
        "Перед работой отключите питание. Страница со схемой отсутствует: нужен оригинал.",
        "note",
    )
    found = knowledge.search(db_session, seed_admin, "Как заменить камеру?")
    assert found and found[0]["trust"] == "unverified"
    assert "нужен оригинал" in found[0]["excerpt"]


def test_excerpt_keeps_whole_short_procedure_and_safety(db_session, seed_admin):
    content = (
        "Перед работой отключите питание.\n\n"
        + "Подготовка рабочего места. " * 35
        + "\n\n1. Отсоедините разъём камеры.\n2. Установите камеру.\n3. Проверьте фиксацию."
    )
    guide = put(db_session, seed_admin, "Замена камеры", content)
    found = knowledge.search(db_session, seed_admin, "Как заменить камеру?")
    assert found[0]["id"] == guide.id
    assert "Перед работой отключите питание" in found[0]["excerpt"]
    assert "3. Проверьте фиксацию." in found[0]["excerpt"]


def test_long_manual_excerpt_keeps_initial_conditions_and_marks_gap():
    from robopark_api.services.ai import retrieval

    beginning = "Для модели R7. Перед работой отключите питание и закрепите корпус.\n\n"
    middle = "Подготовка деталей.\n\n" * 100
    step = "Снятие камеры: нажмите фиксатор и отсоедините разъём.\n\n"
    document = beginning + middle + step + "Сборка и проверка.\n\n" * 100
    excerpt = retrieval.excerpt(document, step, include_start=True)
    assert beginning.strip() in excerpt
    assert step.strip() in excerpt
    assert "пропущен" in excerpt
    assert len(excerpt) <= 2400


def test_ranking_is_independent_of_inaccessible_corpus(
    db_session, seed_admin, seed_mechanic, seed_park_with_tracker
):
    from robopark_api.models import Park

    own = put(
        db_session,
        seed_admin,
        "Камера",
        "Проверка камеры и разъёма.",
        park_id=seed_park_with_tracker.id,
    )
    before = knowledge.search(db_session, seed_mechanic, "камера")
    foreign = Park(name="Other", tag="Other", is_active=True)
    db_session.add(foreign)
    db_session.flush()
    for i in range(8):
        put(
            db_session,
            seed_admin,
            f"Foreign {i}",
            "Камера разъём " * 20 + str(i),
            park_id=foreign.id,
        )
    after = knowledge.search(db_session, seed_mechanic, "камера")
    assert before == after and after[0]["id"] == own.id


def test_document_context_is_only_used_when_question_has_no_subject(db_session, seed_admin):
    camera = put(db_session, seed_admin, "Замена камеры", "Камера: отсоедините разъём.")
    lidar = put(db_session, seed_admin, "Снятие лидара", "Лидар: отсоедините питание.")
    assert (
        knowledge.search(db_session, seed_admin, "Как снять лидар?", context="Камера не работает")[
            0
        ]["id"]
        == lidar.id
    )
    for question in ("Что проверить?", "Что с ним делать?", "Как это исправить?"):
        assert (
            knowledge.search(db_session, seed_admin, question, context="Камера не работает")[0][
                "id"
            ]
            == camera.id
        )


def test_exact_code_survives_candidate_limit(db_session, seed_admin):
    words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    for i in range(161):
        put(db_session, seed_admin, f"Noise {i}", (words + " ") * 3 + str(i), "note")
    correct = put(
        db_session, seed_admin, "EL-02", "EL-02: alpha bravo charlie delta echo", "ticket"
    )
    found = knowledge.search(db_session, seed_admin, words + " EL02")
    assert found and found[0]["id"] == correct.id


def test_entity_survives_candidate_limit(db_session, seed_admin):
    words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    for i in range(161):
        put(db_session, seed_admin, f"Noise {i}", words + str(i), "note")
    correct = put(db_session, seed_admin, "АКБ", "Проверка АКБ", "ticket")
    found = knowledge.search(db_session, seed_admin, words + " АКБ")
    assert found and found[0]["id"] == correct.id


def test_one_digit_code_and_separator_variants(db_session, seed_admin):
    put(db_session, seed_admin, "АКБ WH-02", "АКБ: WH-02", "ticket")
    put(db_session, seed_admin, "АКБ CH-40", "АКБ: CH-40", "ticket")
    correct = put(db_session, seed_admin, "АКБ CH-4", "АКБ: CH-4", "ticket")
    for code in ("CH4", "CH-4", "CH 4", "CH–4"):
        found = knowledge.search(db_session, seed_admin, "АКБ ошибка " + code)
        assert found and all(s["id"] == correct.id for s in found)


def test_long_query_keeps_component_and_code_before_optional_words():
    from robopark_api.services.ai import retrieval

    query = retrieval.prepare(
        " ".join("a" + str(i) for i in range(120)) + " рессора WH-05", knowledge.tokens
    )
    assert query.entities == {"spring"}
    assert query.exact_codes == {"wh05"}
    assert {"wh05", "wh-05"} <= query.terms
    assert "spring" in query.keys


def test_code_mapping_reference_survives_many_historical_repairs(db_session, seed_admin):
    for i in range(170):
        put(
            db_session,
            seed_admin,
            f"Код для камеры {i}",
            f"Выберите код. Камера — нет изображения. Заменена камера {i}. Выбрали код для камеры: запись оператора.",
            "ticket",
        )
    correct = put(
        db_session,
        seed_admin,
        "Справочник кодов: EL-10",
        "Код: EL-10. Дефект: Нет изображения с камеры.",
        "note",
    )
    found = knowledge.search(
        db_session, seed_admin, "Какой код выбрать, если с камеры нет изображения?"
    )
    assert found and found[0]["id"] == correct.id


def test_position_inflections_match_inner_camera_and_exclude_outer(db_session, seed_admin):
    for i in range(5):
        put(
            db_session,
            seed_admin,
            f"Крышка камеры {i}",
            "Крышка грузового отсека: отсоедините внешнюю камеру крышки. " * 3,
        )
    inner = put(
        db_session,
        seed_admin,
        "Отключение внутренних камер",
        "Откройте крышку грузового отсека. Нажмите фиксатор внутренней камеры и отсоедините разъём.",
    )
    found = knowledge.search(
        db_session, seed_admin, "Как отключить внутреннюю камеру крышки грузового отсека?", limit=3
    )
    assert found and found[0]["id"] == inner.id
    assert len(found) == 1
