"""Wires every tool module's tools onto the MCPHandler.

Pure dispatcher — no business logic. Each ``tools/*.py`` module
exposes a ``register(handler, *, indigo_module, **_)`` function;
this file imports them all and calls each in turn.

The keyword-only ``**_`` on each module's register lets future
phases (e.g. Phase 6) thread additional collaborators like an
indexer through without breaking the existing call sites.
"""

from tools import (auto_lights, automation_contents, automations, control,
                   find_devices, history, introspection, irrigation_speed,
                   lookup, plugin_actions, system, zwave)


def register_all(handler, *, indigo_module, indexer=None,
                 history_db_provider=None, logger=None, **_):
    """Register every tool onto ``handler``.

    ``indexer`` is required for ``find_devices``; if it isn't
    supplied (e.g. unit tests that don't exercise search) the
    find_devices tool is silently skipped rather than crashing the
    whole registration. Extra kwargs are accepted via ``**_`` so
    future phases can add more collaborators without touching every
    call site at once.
    """
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
    # Generic cross-plugin action discovery + dispatch (issue #71) —
    # the sanctioned write surface for every action-bearing plugin,
    # not just Auto Lights. No indexer/history dependency.
    plugin_actions.register(handler, indigo_module=indigo_module)
    if indexer is not None:
        find_devices.register(handler, indexer=indexer)
    # History tools always register; an absent provider yields the
    # friendly "not configured" tool-result error rather than a missing
    # tool, so agents can discover the capability and tell the user
    # what to configure.
    history.register(
        handler, history_db_provider=history_db_provider or (lambda: None)
    )
