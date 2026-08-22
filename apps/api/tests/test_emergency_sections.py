from robopark_api.services.emergency_sections import list_sections, render_section
from robopark_api.services.emergency_vin import normalize_robot_id


def test_normalize_robot_id():
    assert normalize_robot_id("447") == "YASADR00000000447"


def test_list_sections_non_empty():
    sections = list_sections()
    assert sections
    assert sections[0][0]


def test_render_status_section():
    payload = {"name": "Robot", "isOnline": True}
    rendered = render_section(payload, "status")
    assert rendered["id"] == "status"
    assert rendered["fields"]
