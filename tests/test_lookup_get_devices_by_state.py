"""TDD tests for tools.lookup._get_devices_by_state_handler.

Filters indigo.devices using state_filter.matches, then paginates
the matched subset using the standard envelope.
"""

import pytest


class _Device:
    """Same minimal device used in test_state_filter — keeps state
    attribute presence deterministic (unlike MagicMock auto-vivify)."""

    def __init__(self, id_, name, type_id="dimmer", **attrs):
        self.id = id_
        self.name = name
        self.deviceTypeId = type_id
        self.onState = False
        self.brightness = 0
        self.folderId = 0
        self.description = ""
        self.model = ""
        self.address = ""
        self.pluginId = ""
        for k, v in attrs.items():
            setattr(self, k, v)


def test_get_devices_by_state_filters(mock_indigo):
    from tools.lookup import _get_devices_by_state_handler

    mock_indigo.devices = [
        _Device(1, "On1", onState=True),
        _Device(2, "Off1", onState=False),
        _Device(3, "On2", onState=True),
    ]
    result = _get_devices_by_state_handler({"state_spec": {"onState": True}}, mock_indigo)
    assert result["total_count"] == 2
    assert {d["id"] for d in result["results"]} == {1, 3}


def test_get_devices_by_state_empty_spec_returns_all(mock_indigo):
    from tools.lookup import _get_devices_by_state_handler

    mock_indigo.devices = [_Device(i, f"D{i}") for i in range(5)]
    result = _get_devices_by_state_handler({"state_spec": {}}, mock_indigo)
    assert result["total_count"] == 5
    assert len(result["results"]) == 5


def test_get_devices_by_state_paginates_filtered(mock_indigo):
    from tools.lookup import _get_devices_by_state_handler

    # 6 on, 4 off — pagination runs on the filtered set.
    devices = []
    for i in range(6):
        devices.append(_Device(100 + i, f"On{i}", onState=True))
    for i in range(4):
        devices.append(_Device(200 + i, f"Off{i}", onState=False))
    mock_indigo.devices = devices

    result = _get_devices_by_state_handler(
        {"state_spec": {"onState": True}, "limit": 3, "offset": 0},
        mock_indigo,
    )
    assert result["total_count"] == 6
    assert len(result["results"]) == 3
    assert result["has_more"] is True
    assert [d["id"] for d in result["results"]] == [100, 101, 102]


def test_get_devices_by_state_default_spec_returns_all(mock_indigo):
    from tools.lookup import _get_devices_by_state_handler

    # No state_spec key at all — same behaviour as empty dict.
    mock_indigo.devices = [_Device(1, "A"), _Device(2, "B")]
    result = _get_devices_by_state_handler({}, mock_indigo)
    assert result["total_count"] == 2


def test_get_devices_by_state_non_dict_spec_raises(mock_indigo):
    from tools.lookup import _get_devices_by_state_handler

    with pytest.raises(ValueError, match="state_spec"):
        _get_devices_by_state_handler({"state_spec": "onState=true"}, mock_indigo)
