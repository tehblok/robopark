"""Presentation and reversible suggestions, never repair validity rules.

Queue IDs remain authoritative. Names translate known Tracker labels; suggestions
help navigate the form and never assert what was diagnosed or actually done.
Unknown labels stay visible, and all canonical defects/actions remain available.
"""

from typing import Any

COMPONENT_LABELS = {
    "ROBOT_BODY_COVER": "Крышки и панели корпуса",
    "ROBOT_BODY_DAMAGE": "Корпус — повреждения",
    "ROBOT_BODY_FLAG": "Флаг",
    "ROBOT_BODY_SKIN": "Наружная обшивка",
    "ROBOT_BODY_LOCK": "Замок крышки",
    "ROBOT_BODY_FRAME": "Рама корпуса",
    "ROBOT_BODY_BAG": "BAG",
    "ROBOT_SUSPENSION": "Подвеска",
    "ROBOT_SUSPENSION_WHEEL": "Мотор-колесо",
    "ROBOT_SUSPENSION_TIRE": "Шина",
    "ROBOT_SUSPENSION_WHEELS_FRONT_LEFT": "Переднее левое мотор-колесо",
    "ROBOT_SUSPENSION_WHEELS_FRONT_RIGHT": "Переднее правое мотор-колесо",
    "ROBOT_SUSPENSION_WHEELS_MIDDLE_LEFT": "Среднее левое мотор-колесо",
    "ROBOT_SUSPENSION_WHEELS_MIDDLE_RIGHT": "Среднее правое мотор-колесо",
    "ROBOT_SUSPENSION_WHEELS_REAR_LEFT": "Заднее левое мотор-колесо",
    "ROBOT_SUSPENSION_WHEELS_REAR_RIGHT": "Заднее правое мотор-колесо",
    "ROBOT_BOARDS_MOTORCONTROL": "Контроллер двигателя",
    "ROBOT_BOARDS_PCU": "Блок PCU",
    "ROBOT_BOARDS_IMU": "Инерциальный модуль IMU",
    "ROBOT_BOARDS_RBCM": "Блок RBCM",
    "ROBOT_MIM": "Модуль MIM",
    "ROBOT_BRAIN_SYNC": "Синхронизация BRAIN",
    "ROBOT_SENSORS_ULTRASONIC": "Парктроники",
    "ROBOT_ULTRASONIC_FRONT_LEFT": "Передний левый парктроник",
    "ROBOT_ULTRASONIC_FRONT_RIGHT": "Передний правый парктроник",
    "ROBOT_ULTRASONIC_REAR_LEFT": "Задний левый парктроник",
    "ROBOT_ULTRASONIC_REAR_RIGHT": "Задний правый парктроник",
    "ROBOT_SENSORS_CAMERA": "Камера",
    "ROBOT_CAMERA_FRONT": "Передняя камера",
    "ROBOT_CAMERA_REAR": "Задняя камера",
    "ROBOT_CAMERA_LEFT": "Левая камера",
    "ROBOT_CAMERA_RIGHT": "Правая камера",
    "ROBOT_CAMERA_BOOTLID": "Камера BOOTLID",
    "ROBOT_CAMERA_TOWER_FRONT": "Передняя камера TOWER",
    "ROBOT_SENSORS_CAMERA_WIRE": "Кабель камеры",
    "ROBOT_SENSORS_LIDAR": "Лидар",
    "ROBOT_SENSORS_LIDAR_WIRE": "Кабель лидара",
    "ROBOT_ELECTRIC_WIRING": "Электропроводка",
    "ROBOT_LIGHTING": "Освещение",
    "ROBOT_BATTERY": "Аккумулятор",
    "ROBOT_KILL_SWITCH": "Аварийный выключатель",
    "ROBOT_COOLING": "Охлаждение",
    "ROBOT_SIM": "SIM",
    "ROBOT_COMP_MODEM": "Модем",
    "ROBOT_COMP_NETWORK": "Сетевой модуль",
    "ROBOT_COMP_ORIN": "Вычислительный модуль Orin",
    "ROBOT_COMP_MOTHERBOARD": "Материнская плата",
    "ROBOT_CALIBRATION": "Калибровка",
    "ROBOT_CALIBRATION_CHECK": "Проверка калибровки",
    "SOFTWARE_LOGS": "Журналы ошибок",
    "SOFTWARE_BRANCH": "Ветка программного обеспечения",
    "SOFTWARE_INVENTORY": "Программный инвентарь",
}

_ALIASES = {
    "ROBOT_BODY_COVER": ["крышка", "панель корпуса"],
    "ROBOT_BODY_DAMAGE": ["корпус", "повреждение корпуса"],
    "ROBOT_BODY_FLAG": ["флажок"],
    "ROBOT_BODY_SKIN": ["обшивка", "боковина", "пластик"],
    "ROBOT_SUSPENSION": ["шасси"],
    "ROBOT_SUSPENSION_WHEEL": ["колесо", "моторколесо", "приводное колесо"],
    "ROBOT_SUSPENSION_TIRE": ["покрышка", "резина"],
    "ROBOT_BOARDS_MOTORCONTROL": ["мотор-контроллер", "motorcontrol", "MCU"],
    "ROBOT_SENSORS_ULTRASONIC": ["парктроник", "ультразвуковой датчик"],
    "ROBOT_SENSORS_CAMERA_WIRE": ["провод камеры", "жгут камеры"],
    "ROBOT_SENSORS_LIDAR_WIRE": ["провод лидара", "жгут лидара"],
    "ROBOT_ELECTRIC_WIRING": ["проводка", "жгут", "провод", "разъём"],
    "ROBOT_LIGHTING": ["фара", "фонарь", "подсветка"],
    "ROBOT_BATTERY": ["АКБ", "батарея"],
    "ROBOT_KILL_SWITCH": ["kill switch", "аварийная кнопка"],
    "ROBOT_COOLING": ["вентилятор", "радиатор"],
    "SOFTWARE_LOGS": ["логи", "журнал ошибок"],
    "SOFTWARE_BRANCH": ["прошивка", "branch"],
}

# Ordered navigation hints. A mechanic must still choose the performed action.
DEFECT_METHOD_SUGGESTIONS = {
    "BD-06": ["REPAIR", "MAINTENANCE"],
    "CH-01": ["MAINTENANCE", "REPAIR"],
    "CH-03": ["CHANGE", "REPAIR"],
    "EL-01": ["CONFIG", "DIAG"],
    "EL-02": ["DIAG", "REPAIR", "CHANGE"],
    "EL-03": ["CONFIG", "RESTART", "DIAG"],
    "EL-04": ["DIAG", "REPAIR"],
    "EL-06": ["CONFIG", "DIAG"],
    "EL-10": ["DIAG", "CHANGE", "REPAIR"],
    "WH-03": ["REPAIR", "INSTALL"],
    "WH-05": ["REPAIR", "CHANGE"],
    "PP-01": ["CHANGE", "INSTALL"],
    "PP-02": ["INSTALL", "CHANGE"],
    "PP-03": ["DIAG", "CONFIG"],
}


def _suggestions(name: str) -> tuple[list[str], list[str]]:
    if name not in COMPONENT_LABELS:
        return [], []
    if name in {"ROBOT_CALIBRATION", "ROBOT_CALIBRATION_CHECK"}:
        return ["EL-06", "EL-01"], ["CONFIG", "DIAG"]
    if name.endswith("_WIRE") or name == "ROBOT_ELECTRIC_WIRING":
        return ["WH-05", "EL-02", "WH-01", "WH-03", "WH-04", "WH-02", "WH-06"], [
            "REPAIR",
            "CHANGE",
            "DIAG",
        ]
    if name == "ROBOT_BOARDS_MOTORCONTROL":
        return ["EL-02", "EL-04"], ["CHANGE", "DIAG", "REPAIR"]
    if name == "ROBOT_BODY_FLAG":
        return ["BD-02", "EL-09", "BD-06"], ["CHANGE", "REPAIR"]
    if "CAMERA" in name:
        return ["EL-10", "EL-02", "EL-01", "EL-06", "BD-10"], ["CHANGE", "CONFIG", "DIAG"]
    if "ULTRASONIC" in name:
        return ["EL-02", "BD-06", "EL-08"], ["REPAIR", "CHANGE", "DIAG"]
    if "LIDAR" in name:
        return ["EL-02", "EL-07", "EL-01", "EL-06"], ["CONFIG", "CHANGE", "DIAG"]
    if name == "ROBOT_BODY_LOCK":
        return ["EL-11", "BD-06", "CH-03"], ["REPAIR", "CHANGE", "MAINTENANCE"]
    if name.startswith("ROBOT_BODY_"):
        return ["BD-06", "BD-02", "BD-01", "BD-09", "CH-03"], ["REPAIR", "CHANGE"]
    if name.startswith("ROBOT_SUSPENSION"):
        return ["CH-01", "CH-03", "CH-02", "CH-04", "CH-05"], ["MAINTENANCE", "CHANGE", "REPAIR"]
    if name == "ROBOT_LIGHTING":
        return ["EL-09", "EL-02", "WH-01"], ["CHANGE", "REPAIR", "DIAG"]
    return [], []


def decorate_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for component in components:
        name = str(component.get("tracker_name") or component["label"])
        defects, methods = _suggestions(name)
        result.append(
            {
                **component,
                "tracker_name": name,
                "label": COMPONENT_LABELS.get(name, component["label"]),
                "aliases": list(_ALIASES.get(name, [])),
                "defect_codes": defects,
                "solution_methods": methods,
            }
        )
    return result
