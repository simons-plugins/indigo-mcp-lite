"""Wires every tool module's tools onto the MCPHandler.

Pure dispatcher — no business logic. Each ``tools/*.py`` module
exposes a ``register(handler, *, indigo_module, **_)`` function;
this file imports them all and calls each in turn.

The keyword-only ``**_`` on each module's register lets future
phases (e.g. Phase 6) thread additional collaborators like an
indexer through without breaking the existing call sites.
"""

from tools import control, lookup


def register_all(handler, *, indigo_module, **_):
    """Register every tool onto ``handler``.

    Extra kwargs are accepted (and ignored here) so future phases
    can add collaborators (``indexer=...`` etc.) without touching
    every call site at once.
    """
    lookup.register(handler, indigo_module=indigo_module)
    control.register(handler, indigo_module=indigo_module)
