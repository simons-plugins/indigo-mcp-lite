"""Lookup tools — read-only primitives over Indigo's object model.

Each tool wraps a single Indigo iter / lookup; no business logic.
Real implementations land per-task: this module starts with a
``list_devices`` stub (Task 3.1) that returns an empty paginated
result, and Task 3.2 fills the body in.
"""


def register(handler, *, indigo_module):
    """Register every lookup tool onto the given MCPHandler.

    ``indigo_module`` is injected (not imported here) so tests can
    pass a MagicMock without monkey-patching ``sys.modules`` from
    inside this file.
    """
    handler.register_tool(
        name="list_devices",
        description="List Indigo devices with optional pagination and filters.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        handler=lambda args: _list_devices_handler(args, indigo_module),
    )


def _list_devices_handler(args, indigo_module):
    """Stub — real implementation lands in Task 3.2.

    Shape matches the paginated envelope every list-style tool will
    return, so callers and tests can already key off it.
    """
    return {
        "results": [],
        "total_count": 0,
        "offset": 0,
        "limit": 50,
        "has_more": False,
    }
