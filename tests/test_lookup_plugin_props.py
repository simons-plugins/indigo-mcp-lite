"""Tests for ``plugin_props`` on the device-detail serializer (#56).

``states`` describe what a device IS; a plugin device's *behaviour*
lives in its configuration — the IOM's ``globalProps``, a dict of
dicts keyed by plugin id (the live view of the database's MetaProps),
including the server's own block under ``com.indigodomo.indigoserver``.
Without it a meta relay, an occupancy zone, or a device carrying
``defaultDimmerLevel`` reads as a black box.
"""

from types import SimpleNamespace

from tools.lookup import _serialize_device_detail


def _fake_device(global_props):
    return SimpleNamespace(
        id=42, name="Meta Relay", deviceTypeId="metaRelay", model="",
        address="", description="", folderId=0,
        pluginId="com.berkinet.metadevice", onState=False,
        brightness=None, protocol="", states={"onOffState": False},
        globalProps=global_props,
    )


def test_plugin_props_included_keyed_by_plugin_id(mock_indigo):
    dev = _fake_device({
        "com.berkinet.metadevice": {
            "metaDevOnAction": "1532438874",
            "metaDevOffAction": "1248008121",
            "metaDevOnMode": "2",
        },
        "com.indigodomo.indigoserver": {"defaultDimmerLevel": 100},
    })
    out = _serialize_device_detail(dev)
    assert out["plugin_props"]["com.berkinet.metadevice"][
        "metaDevOnAction"] == "1532438874"
    # Server-owned props share the dict — defaultDimmerLevel is what
    # every dimToDefaultLevel action resolves to.
    assert out["plugin_props"]["com.indigodomo.indigoserver"] == {
        "defaultDimmerLevel": 100,
    }


def test_plugin_props_present_but_empty_when_device_has_none(mock_indigo):
    out = _serialize_device_detail(_fake_device({}))
    # Empty, not absent: an unconfigured device is a different answer
    # from a tool that doesn't report configuration at all.
    assert out["plugin_props"] == {}


def test_plugin_props_omitted_when_iom_exposes_no_such_attribute(
        mock_indigo):
    dev = _fake_device({})
    del dev.globalProps
    assert "plugin_props" not in _serialize_device_detail(dev)


def test_plugin_props_values_are_json_safe(mock_indigo):
    class _Enum:
        def __str__(self):
            return "kSomeEnum.Value"

    dev = _fake_device({
        "com.flyingdiver.indigoplugin.occupatum": {
            "offDelayValue": "240",
            "onAnyAll": "any",
            "sensorDeviceList": ["1321941000"],
            "mode": _Enum(),
        },
    })
    props = _serialize_device_detail(dev)["plugin_props"]
    occupatum = props["com.flyingdiver.indigoplugin.occupatum"]
    assert occupatum["sensorDeviceList"] == ["1321941000"]
    assert occupatum["mode"] == "kSomeEnum.Value"


def test_states_and_plugin_props_are_both_present(mock_indigo):
    out = _serialize_device_detail(_fake_device({"x": {"y": 1}}))
    assert out["states"] == {"onOffState": False}
    assert out["plugin_props"] == {"x": {"y": 1}}
