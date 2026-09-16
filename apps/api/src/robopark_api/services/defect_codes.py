"""Immutable catalog of approved Tracker defect codes."""

from dataclasses import dataclass

from fastapi import HTTPException


@dataclass(frozen=True)
class DefectCode:
    code: str
    label: str
    description: str | None = None


DEFECT_CODES = (
    DefectCode("BD-01", "Вмятина"),
    DefectCode("BD-02", "Трещина"),
    DefectCode(
        "BD-03",
        "Зазор",
        "Детали находятся в одном уровне, но расстояние между ними отличается от допустимого по сравнению с другими роботами.",
    ),
    DefectCode("BD-04", "Плохая адгезия"),
    DefectCode(
        "BD-05",
        "Неправильная установка",
        "Элемент установлен в неправильном положении, например перевёрнут датчик парктроника.",
    ),
    DefectCode("BD-06", "Не прикручено / не затянуто"),
    DefectCode(
        "BD-07",
        "Перепад",
        "Между деталями есть перепад по уровню; его можно проверить линейкой по плоскости двух деталей.",
    ),
    DefectCode("BD-08", "Скол"),
    DefectCode(
        "BD-09",
        "Дефект литья пластика",
        "Например, отверстие, отсутствие крепёжного элемента или деформация.",
    ),
    DefectCode("BD-10", "Потёртость / царапины"),
    DefectCode("EL-01", "Не калибруется", "Камеры, лидар."),
    DefectCode("EL-02", "Нет сигнала / нет связи"),
    DefectCode(
        "EL-03",
        "Ошибка прошивки",
        "Например, не разворачивается ветка PL или не обновляется rootfs.",
    ),
    DefectCode("EL-04", "Диагностика сообщает об ошибке", "HUD-сообщение об ошибке."),
    DefectCode("EL-05", "Посторонний звук при функционировании"),
    DefectCode("EL-06", "Не откалибровано", "Например, не откалибрована камера кропа."),
    DefectCode("EL-07", "Низкий уровень сигнала", "Например, недостаточно лидарных точек."),
    DefectCode("EL-08", "Ложное срабатывание", "Например, парктроники."),
    DefectCode("EL-09", "Не горит", "Для освещения."),
    DefectCode("EL-10", "Нет изображения с камеры"),
    DefectCode("EL-11", "Не открывается / не закрывается", "Для замков."),
    DefectCode("WH-01", "Плохой контакт в коннекторе"),
    DefectCode("WH-02", "Короткое замыкание"),
    DefectCode("WH-03", "Не подключён коннектор"),
    DefectCode("WH-04", "Повреждение изоляции"),
    DefectCode("WH-05", "Повреждение провода"),
    DefectCode(
        "WH-06",
        "Попадание воды, жидкости или грязи",
        "Например, вода в электронном блоке или лампе.",
    ),
    DefectCode("CH-01", "Не прикручено / не затянуто"),
    DefectCode("CH-02", "Трещина"),
    DefectCode("CH-03", "Сломано", "Например, отлетело колесо."),
    DefectCode("CH-04", "Стук"),
    DefectCode(
        "CH-05",
        "Скрежет",
        "Например, сломана и скрипит рессора либо крышка дребезжит при движении.",
    ),
    DefectCode("PP-01", "Установлена не та деталь"),
    DefectCode("PP-02", "Отсутствует деталь"),
    DefectCode("PP-03", "Не соответствует серийный номер"),
)

_BY_CODE = {item.code: item for item in DEFECT_CODES}


def validate_defect_code(value: str) -> str:
    if value not in _BY_CODE:
        raise HTTPException(status_code=422, detail="defect_code_invalid")
    return value
