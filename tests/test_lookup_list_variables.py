"""TDD tests for tools.lookup._list_variables_handler.

Mirrors test_lookup_list_devices.py — same pagination contract,
same wire-shape expectations. ``_fake_variable`` lives here (not
in conftest) so each test file stays self-contained, matching the
established pattern.
"""

from unittest.mock import MagicMock


def _fake_variable(id_, name, value="", folder_id=0, description=""):
    v = MagicMock()
    v.id = id_
    v.name = name
    v.value = value
    v.folderId = folder_id
    v.description = description
    return v


def test_list_variables_returns_all_under_limit(mock_indigo):
    from tools.lookup import _list_variables_handler

    mock_indigo.variables = [_fake_variable(1, "A"), _fake_variable(2, "B")]
    result = _list_variables_handler({}, mock_indigo)
    assert result["total_count"] == 2
    assert len(result["results"]) == 2
    assert result["has_more"] is False


def test_list_variables_paginates(mock_indigo):
    from tools.lookup import _list_variables_handler

    mock_indigo.variables = [_fake_variable(i, f"V{i}") for i in range(120)]
    result = _list_variables_handler({"limit": 50, "offset": 0}, mock_indigo)
    assert result["total_count"] == 120
    assert len(result["results"]) == 50
    assert result["has_more"] is True
    assert result["offset"] == 0


def test_list_variables_offset_works(mock_indigo):
    from tools.lookup import _list_variables_handler

    mock_indigo.variables = [_fake_variable(i, f"V{i}") for i in range(10)]
    result = _list_variables_handler({"limit": 5, "offset": 5}, mock_indigo)
    assert result["total_count"] == 10
    assert len(result["results"]) == 5
    assert result["results"][0]["id"] == 5
    assert result["has_more"] is False


def test_list_variables_default_limit_is_50(mock_indigo):
    from tools.lookup import _list_variables_handler

    mock_indigo.variables = [_fake_variable(i, f"V{i}") for i in range(80)]
    result = _list_variables_handler({}, mock_indigo)
    assert len(result["results"]) == 50


def test_list_variables_caps_limit_at_500(mock_indigo):
    from tools.lookup import _list_variables_handler

    mock_indigo.variables = [_fake_variable(i, f"V{i}") for i in range(1000)]
    result = _list_variables_handler({"limit": 9999}, mock_indigo)
    assert len(result["results"]) == 500


def test_list_variables_serializes_expected_fields(mock_indigo):
    from tools.lookup import _list_variables_handler

    var = _fake_variable(7, "doorOpen", value="true", folder_id=4, description="front door state")
    mock_indigo.variables = [var]

    result = _list_variables_handler({}, mock_indigo)
    [serialized] = result["results"]
    assert serialized["id"] == 7
    assert serialized["name"] == "doorOpen"
    assert serialized["value"] == "true"
    assert serialized["folder_id"] == 4
    assert serialized["description"] == "front door state"
