def test_guidance_preserves_queue_identity_and_explains_known_parts():
    from robopark_api.services.repair_guidance import decorate_components

    source = [{"id": "42", "label": "ROBOT_SENSORS_CAMERA_WIRE"}]
    result = decorate_components(source)
    assert result[0]["id"] == "42"
    assert result[0]["label"] == "Кабель камеры"
    assert result[0]["tracker_name"] == "ROBOT_SENSORS_CAMERA_WIRE"
    assert "провод камеры" in result[0]["aliases"]
    assert result[0]["defect_codes"][:2] == ["WH-05", "EL-02"]
    assert source == [{"id": "42", "label": "ROBOT_SENSORS_CAMERA_WIRE"}]


def test_guidance_does_not_invent_meaning_or_a_completed_action():
    from robopark_api.services.repair_guidance import decorate_components

    result = decorate_components(
        [
            {"id": "x", "label": "CUSTOM_NEW_PART"},
            {"id": "y", "label": "ROBOT_MIM"},
            {"id": "z", "label": "ROBOT_CALIBRATION"},
        ]
    )
    assert result[0]["label"] == "CUSTOM_NEW_PART"
    assert result[0]["defect_codes"] == []
    assert result[1]["label"] == "Модуль MIM"
    assert result[2]["solution_methods"][0] == "CONFIG"
    assert "solution_method" not in result[2]


def test_conditioned_history_ranks_actions_without_claiming_the_work_was_done():
    from robopark_api.services.repair_guidance import decorate_components

    control, flag, unknown = decorate_components(
        [
            {"id": "a", "label": "ROBOT_BOARDS_MOTORCONTROL"},
            {"id": "b", "label": "ROBOT_BODY_FLAG"},
            {"id": "c", "label": "CUSTOM_PART"},
        ]
    )
    assert control["defect_method_suggestions"]["EL-02"][0] == "CHANGE"
    assert flag["defect_method_suggestions"]["BD-02"][0] == "CHANGE"
    assert unknown["defect_method_suggestions"] == {}
    assert "solution_method" not in control


def test_suggestions_only_reference_existing_defects_and_actions():
    from robopark_api.services.defect_codes import DEFECT_CODES
    from robopark_api.services.repair_fields import SOLUTION_METHODS
    from robopark_api.services.repair_guidance import (
        COMPONENT_LABELS,
        DEFECT_METHOD_SUGGESTIONS,
        decorate_components,
    )

    codes = {item.code for item in DEFECT_CODES}
    methods = dict(SOLUTION_METHODS)
    result = decorate_components([{"id": name, "label": name} for name in COMPONENT_LABELS])
    assert all(set(item["defect_codes"]) <= codes for item in result)
    assert all(set(item["solution_methods"]) <= methods.keys() for item in result)
    for item in result:
        assert set(item["defect_method_suggestions"]) <= codes
        assert all(
            set(values) <= methods.keys() for values in item["defect_method_suggestions"].values()
        )
    assert set(DEFECT_METHOD_SUGGESTIONS) <= codes
    assert all(set(values) <= methods.keys() for values in DEFECT_METHOD_SUGGESTIONS.values())
