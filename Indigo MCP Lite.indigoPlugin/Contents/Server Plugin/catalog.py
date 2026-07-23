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

The snapshot import is guarded: a corrupt or missing generated file
degrades to an empty catalog (every lookup misses, meta is empty)
with a logged error. Advisory data must NEVER take down the tool
registry — every tool module imports through this file at startup,
so an unguarded import failure here would kill all 72 tools while
the /mcp endpoint kept serving an empty list.
"""

import logging


try:
    import catalog_snapshot as _snapshot
except Exception:  # noqa: BLE001 — a corrupt generated file can raise anything
    logging.getLogger("Plugin.catalog").exception(
        "device-catalog snapshot failed to load — catalog enrichment "
        "disabled (all profile lookups will miss). Regenerate with "
        "scripts/generate_catalog_snapshot.py"
    )
    _snapshot = None


def profile_for(dev):
    """Return the catalog profile for ``dev``, or None when absent.

    Both keys must be genuine non-empty strings — mocks and built-in
    devices (empty pluginId) miss cleanly rather than raising.
    """
    if _snapshot is None:
        return None
    plugin_id = getattr(dev, "pluginId", "")
    type_id = getattr(dev, "deviceTypeId", "")
    if not isinstance(plugin_id, str) or not isinstance(type_id, str):
        return None
    if not plugin_id or not type_id:
        return None
    return _snapshot.PROFILES.get((plugin_id, type_id))


def snapshot_meta():
    """Return the snapshot provenance dict (commit, date, count).

    Empty dict when the snapshot failed to load — callers surface it
    verbatim, so an empty meta is the diagnosable signal for "the
    vendored catalog itself is broken/absent".
    """
    if _snapshot is None:
        return {}
    return dict(_snapshot.SNAPSHOT_META)
