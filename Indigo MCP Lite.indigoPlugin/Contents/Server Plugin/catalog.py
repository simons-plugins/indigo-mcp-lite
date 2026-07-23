"""Lookup helper over the vendored device-catalog snapshot.

``profile_for(dev)`` matches a live Indigo device against the
community catalog by ``(pluginId, deviceTypeId)`` and returns the
vendored profile dict, or None when the device is uncataloged (or is
a built-in/interface device with no pluginId). Callers must treat
None as "no data" and proceed — the catalog is advisory, never a
gate on missing information.

The snapshot itself lives in ``catalog_snapshot.py`` (auto-generated,
see ``scripts/generate_catalog_snapshot.py``); this module is the
only place that should read it, so the key shape stays in one file.
"""

import catalog_snapshot


def profile_for(dev):
    """Return the catalog profile for ``dev``, or None when absent.

    Both keys must be genuine non-empty strings — mocks and built-in
    devices (empty pluginId) miss cleanly rather than raising.
    """
    plugin_id = getattr(dev, "pluginId", "")
    type_id = getattr(dev, "deviceTypeId", "")
    if not isinstance(plugin_id, str) or not isinstance(type_id, str):
        return None
    if not plugin_id or not type_id:
        return None
    return catalog_snapshot.PROFILES.get((plugin_id, type_id))


def snapshot_meta():
    """Return the snapshot provenance dict (commit, date, count)."""
    return dict(catalog_snapshot.SNAPSHOT_META)
