"""TDD tests for tools.lookup._list_action_groups_handler.

Mirrors test_lookup_list_devices.py — same pagination contract,
same wire-shape expectations.
"""

from unittest.mock import MagicMock


def _fake_action_group(id_, name, folder_id=0, description=""):
    a = MagicMock()
    a.id = id_
    a.name = name
    a.folderId = folder_id
    a.description = description
    return a


def test_list_action_groups_returns_all_under_limit(mock_indigo):
    from tools.lookup import _list_action_groups_handler

    mock_indigo.actionGroups = [_fake_action_group(1, "A"), _fake_action_group(2, "B")]
    result = _list_action_groups_handler({}, mock_indigo)
    assert result["total_count"] == 2
    assert len(result["results"]) == 2
    assert result["has_more"] is False


def test_list_action_groups_paginates(mock_indigo):
    from tools.lookup import _list_action_groups_handler

    mock_indigo.actionGroups = [_fake_action_group(i, f"AG{i}") for i in range(120)]
    result = _list_action_groups_handler({"limit": 50, "offset": 0}, mock_indigo)
    assert result["total_count"] == 120
    assert len(result["results"]) == 50
    assert result["has_more"] is True
    assert result["offset"] == 0


def test_list_action_groups_offset_works(mock_indigo):
    from tools.lookup import _list_action_groups_handler

    mock_indigo.actionGroups = [_fake_action_group(i, f"AG{i}") for i in range(10)]
    result = _list_action_groups_handler({"limit": 5, "offset": 5}, mock_indigo)
    assert result["total_count"] == 10
    assert len(result["results"]) == 5
    assert result["results"][0]["id"] == 5
    assert result["has_more"] is False


def test_list_action_groups_default_limit_is_50(mock_indigo):
    from tools.lookup import _list_action_groups_handler

    mock_indigo.actionGroups = [_fake_action_group(i, f"AG{i}") for i in range(80)]
    result = _list_action_groups_handler({}, mock_indigo)
    assert len(result["results"]) == 50


def test_list_action_groups_caps_limit_at_500(mock_indigo):
    from tools.lookup import _list_action_groups_handler

    mock_indigo.actionGroups = [_fake_action_group(i, f"AG{i}") for i in range(1000)]
    result = _list_action_groups_handler({"limit": 9999}, mock_indigo)
    assert len(result["results"]) == 500


def test_list_action_groups_serializes_expected_fields(mock_indigo):
    from tools.lookup import _list_action_groups_handler

    ag = _fake_action_group(11, "Goodnight", folder_id=2, description="lights + locks")
    mock_indigo.actionGroups = [ag]

    result = _list_action_groups_handler({}, mock_indigo)
    [serialized] = result["results"]
    assert serialized["id"] == 11
    assert serialized["name"] == "Goodnight"
    assert serialized["folder_id"] == 2
    assert serialized["description"] == "lights + locks"
