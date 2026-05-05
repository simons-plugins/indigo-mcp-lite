"""Wires every tool module's tools onto the MCPHandler.

Pure dispatcher — no business logic. Each ``tools/*.py`` module
exposes a ``register(handler, *, indigo_module, **_)`` function;
this file imports them all and calls each in turn.

The keyword-only ``**_`` on each module's register lets future
phases (e.g. Phase 6) thread additional collaborators like an
indexer through without breaking the existing call sites.
"""

from tools import control, find_devices, lookup, system


def register_all(handler, *, indigo_module, indexer=None, **_):
    """Register every tool onto ``handler``.

    ``indexer`` is required for ``find_devices``; if it isn't
    supplied (e.g. unit tests that don't exercise search) the
    find_devices tool is silently skipped rather than crashing the
    whole registration. Extra kwargs are accepted via ``**_`` so
    future phases can add more collaborators without touching every
    call site at once.
    """
    lookup.register(handler, indigo_module=indigo_module)
    control.register(handler, indigo_module=indigo_module)
    system.register(handler, indigo_module=indigo_module)
    if indexer is not None:
        find_devices.register(handler, indexer=indexer)
