"""Wires every tool module's tools onto the MCPHandler.

Pure dispatcher — no business logic. Each ``tools/*.py`` module
exposes a ``register(handler, *, indigo_module, **_)`` function;
this file imports them all and calls each in turn.

The keyword-only ``**_`` on each module's register lets future
phases (e.g. Phase 6) thread additional collaborators like an
indexer through without breaking the existing call sites.

On Indigo 2026.1+ the server ships its own MCP (``/v3/api/mcp``) that
covers lookup and control; there ``register_all`` drops the tools in
``BUILTIN_COVERED`` unless the "Legacy tools" pref is on (workspace
ADR-0009). The gate lives here so the ``tools/*.py`` modules stay
unaware of it.
"""

import re

from tools import (auto_lights, automation_contents, automations, control,
                   find_devices, history, introspection, irrigation_speed,
                   lamplighter, lookup, plugin_actions, system, zwave)


# Tools that Indigo 2026.1's built-in MCP server (/v3/api/mcp) already
# provides. Not registered on a 2026.1+ server unless the "Legacy tools"
# pref is on. Decision and tool-by-tool mapping: workspace ADR-0009
# (docs/adr/0009-builtin-mcp-is-general-surface-lite-is-extension.md)
# and docs/plans/2026-11-02-indigo-mcp-lite-builtin-overlap.md.
BUILTIN_COVERED = frozenset({
    # Lookup
    "list_devices", "list_variables", "list_action_groups", "list_triggers",
    "list_schedules", "list_device_folders", "list_variable_folders",
    "get_device_by_id", "get_variable_by_id", "get_action_group_by_id",
    "get_trigger_by_id", "get_schedule_by_id", "get_devices_by_type",
    "get_devices_by_state",
    # Device control
    "device_turn_on", "device_turn_off", "device_toggle",
    "device_set_brightness", "device_brighten", "device_dim",
    "device_set_rgb_color", "device_set_rgb_percent", "device_set_hex_color",
    "device_set_named_color", "device_set_white_levels",
    "thermostat_set_hvac_mode", "thermostat_set_fan_mode",
    "thermostat_set_heat_setpoint", "thermostat_set_cool_setpoint",
    "device_lock", "device_unlock",
    # Execute / variables
    "action_execute_group", "trigger_execute", "schedule_execute",
    "variable_create", "variable_update",
    # Sprinklers / speed control
    "sprinkler_run_zone", "sprinkler_run_schedule", "sprinkler_stop",
    "sprinkler_pause", "sprinkler_resume", "sprinkler_next_zone",
    "sprinkler_previous_zone", "speedcontrol_set_index",
    "speedcontrol_set_level", "speedcontrol_increase", "speedcontrol_decrease",
    # System / search
    "query_event_log", "list_plugins", "get_plugin_by_id",
    "get_plugin_status", "restart_plugin", "find_devices",
})

# First Indigo release with the built-in MCP server.
_BUILTIN_MCP_MIN_VERSION = (2026, 1)

_VERSION_RE = re.compile(r"\s*(\d+)\.(\d+)")


def server_has_builtin_mcp(indigo_module, logger=None):
    """True iff the connected Indigo server is 2026.1 or later.

    ``indigo.server.version`` is a string such as ``"2026.1.0"``.
    Anything else (attribute missing, a non-string such as the
    MagicMock the test suite and doc generator use, or an unparsable
    string) answers False, so lite falls back to registering every
    tool rather than registering too few.
    """
    try:
        version = indigo_module.server.version
    except Exception as exc:  # pylint: disable=broad-except
        if logger is not None:
            logger.debug(f"Indigo server version unreadable ({exc}); assuming no built-in MCP")
        return False
    if not isinstance(version, str):
        if logger is not None:
            logger.debug(f"Indigo server version is not a string ({version!r}); assuming no built-in MCP")
        return False
    match = _VERSION_RE.match(version)
    if match is None:
        if logger is not None:
            logger.debug(f"Indigo server version {version!r} unparsable; assuming no built-in MCP")
        return False
    return (int(match.group(1)), int(match.group(2))) >= _BUILTIN_MCP_MIN_VERSION


class _GatedRegistrar:
    """Handler proxy that drops (and counts) excluded tool names.

    ``register_tool`` skips names in ``excluded`` and forwards
    everything else, counting both; every other attribute,
    ``register_resource`` included, goes straight to the real handler.
    """

    def __init__(self, handler, excluded):
        self._handler = handler
        self._excluded = excluded
        self.registered = 0
        self.skipped = 0

    def register_tool(self, *args, **kwargs):
        """Forward to the real handler exactly as called, unless the
        tool name (``name=`` or first positional) is excluded."""
        name = kwargs["name"] if "name" in kwargs else args[0]
        if name in self._excluded:
            self.skipped += 1
            return None
        self.registered += 1
        return self._handler.register_tool(*args, **kwargs)

    def __getattr__(self, attr):
        return getattr(self._handler, attr)


def register_all(handler, *, indigo_module, indexer=None,
                 history_db_provider=None, logger=None,
                 legacy_tools=False, **_):
    """Register every tool onto ``handler``.

    ``indexer`` is required for ``find_devices``; if it isn't
    supplied (e.g. unit tests that don't exercise search) the
    find_devices tool is silently skipped rather than crashing the
    whole registration. Extra kwargs are accepted via ``**_`` so
    future phases can add more collaborators without touching every
    call site at once.

    On a server with the built-in MCP (2026.1+) and ``legacy_tools``
    off, the tools in ``BUILTIN_COVERED`` are not registered and
    ``find_devices`` is skipped even when an indexer is supplied.

    Returns ``{"slim": bool, "registered": int, "skipped": int}`` so
    the caller can log what this server ended up exposing.
    """
    slim = server_has_builtin_mcp(indigo_module, logger) and not legacy_tools
    handler = _GatedRegistrar(handler, BUILTIN_COVERED if slim else frozenset())
    lookup.register(handler, indigo_module=indigo_module)
    # Logger threads into the catalog capability pre-checks so skipped
    # checks (unreadable device) leave a debug trace.
    control.register(handler, indigo_module=indigo_module, logger=logger)
    system.register(handler, indigo_module=indigo_module)
    zwave.register(handler, indigo_module=indigo_module)
    automations.register(handler, indigo_module=indigo_module)
    # Always registered; the .indiDb reader object is constructed
    # eagerly (cheap) but does no I/O until the first tool call, and
    # raises a friendly error if the database file is unavailable
    # rather than blocking registration. The plugin's logger threads
    # through so degraded parses are visible in the event log.
    automation_contents.register(
        handler, indigo_module=indigo_module, logger=logger
    )
    irrigation_speed.register(handler, indigo_module=indigo_module)
    introspection.register(handler, indigo_module=indigo_module)
    # Scoped config-write tools for the Auto Lights fork (issue #66).
    # Reads/writes its own Preferences JSON file directly; no
    # dependency on this plugin's indexer/history collaborators.
    auto_lights.register(handler, indigo_module=indigo_module)
    # Scoped config + zone-lifecycle tools for the Lamplighter plugin
    # (its PRD 5.12). Reads/writes its own Preferences JSON file and
    # calls its own Actions.xml actions; no indexer/history dependency.
    lamplighter.register(handler, indigo_module=indigo_module)
    # Generic cross-plugin action discovery + dispatch (issue #71) —
    # the sanctioned write surface for every action-bearing plugin,
    # not just Auto Lights. No indexer/history dependency. Logger
    # threads through so a control-flow exception re-raised unchanged
    # from plugin_execute_action can still log its WAS-DISPATCHED
    # disclosure (post-merge review, item 1).
    plugin_actions.register(handler, indigo_module=indigo_module, logger=logger)
    if indexer is not None and not slim:
        find_devices.register(handler, indexer=indexer)
    elif slim:
        # Not registered and never built (plugin.py skips the indexer
        # on a slim server); counted so the log line's total is honest.
        handler.skipped += 1
    # History tools always register; an absent provider yields the
    # friendly "not configured" tool-result error rather than a missing
    # tool, so agents can discover the capability and tell the user
    # what to configure.
    history.register(
        handler, history_db_provider=history_db_provider or (lambda: None)
    )
    return {"slim": slim, "registered": handler.registered,
            "skipped": handler.skipped}
