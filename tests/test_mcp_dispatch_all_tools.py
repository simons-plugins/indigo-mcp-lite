"""Issue #72's "worth considering" stretch idea: a single parameterised
test that every tool registered via ``tool_registry.register_all``
survives a real ``MCPHandler.handle_request`` round trip, so a
``lambda args:`` regression in *any* tool family is caught by this one
test rather than requiring a hand-written wire test per family.

What this test catches, and how it stays safe doing it
--------------------------------------------------------
Every tool call below passes ``arguments: {}`` — no per-tool fixtures,
no real argument values. That is enough to catch the repo's named
gotcha (``lambda args:`` instead of ``lambda **args:``) for every tool,
including ones that require arguments: ``mcp_handler`` dispatches as
``handler(**tool_args)``, and for a mis-registered ``lambda args:``
(no default), ``handler(**{})`` is ``handler()`` — a call with zero
positional/keyword arguments — which fails to bind the required
``args`` parameter and raises ``TypeError: <lambda>() missing 1
required positional argument: 'args'`` regardless of whether the real
call site would have passed any arguments at all. Spot-checked live
(see PR description) against both an ``auto_lights_*`` tool and
``device_turn_on`` in ``tools/control.py`` — flipping either
registration to ``lambda args:`` makes this test fail with exactly
that message.

``mcp_handler`` funnels both ``TypeError`` and ``ValueError`` into the
same ``isError: true`` tool-result bucket (see
``_handle_tools_call``), so a signature-mismatch ``TypeError`` is NOT
distinguishable from a legitimate business-logic ``ValueError`` by
error TYPE alone — only by message TEXT. Every tool in this repo
validates cheaply BEFORE touching ``indigo_module`` (documented
repeatedly across ``tools/*.py``, e.g. ``auto_lights._set_level_handler``'s
docstring), so calling with ``{}`` against every REQUIRED-argument
tool below reliably reaches that cheap validation and raises an
English "X is required"/"X must be a ..." message — never Python's own
arity-mismatch phrasing. This test asserts exactly that: whatever the
tool's own response is (success or a friendly refusal), the error text
must never contain the phrases Python's interpreter uses for a
missing/unexpected argument to a lambda.

What this test does NOT catch, on purpose
-------------------------------------------
A subtler mutation — giving the lambda's stray positional parameter a
default, e.g. ``lambda args={}: fn(args, indigo_module)`` instead of
``lambda **args:`` — behaves IDENTICALLY to the correct form when
called with zero arguments (``args`` is ``{}`` either way), so a
``{}``-only sweep like this one cannot distinguish it from correct
code for any tool that has required arguments. That mutation is only
caught by passing REAL argument values, which requires per-tool
knowledge of what's safe to pass (a write tool's fixture, mocked
collaborators, assertions on what got called) — exactly what the
family-scoped tests already do:
``test_mcp_dispatch_integration.py`` for the original list_devices/
control/query_event_log tools and the #71 (``list_plugin_actions``/
``plugin_execute_action``) and #72 (``auto_lights_list_zones``/
``auto_lights_set_level``) wire tests. This sweep complements those,
it does not replace them, and the two tools with no required
arguments at all (``auto_lights_list_zones``, and any future
zero-argument read tool) ARE fully covered here, since ``{}`` is their
entire real argument set.

``find_devices`` is intentionally excluded from the sweep — it only
registers when an ``indexer`` is supplied to ``register_all``, and
none is provided here (matching every other test in this file that
calls ``register_all`` without one). Its own wire test in
``test_mcp_dispatch_integration.py`` covers it directly.
"""

import json
import re

_SIGNATURE_MISMATCH = re.compile(
    r"positional argument|unexpected keyword argument"
)


def test_every_registered_tool_survives_a_wire_round_trip(mock_indigo):
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    handler = MCPHandler(server_name="test", server_version="0")
    register_all(handler, indigo_module=mock_indigo)

    tool_names = list(handler._tools.keys())
    # A generous floor, not an exact count -- pins that register_all
    # actually ran (an empty registry would trivially "pass" the loop
    # below) without this test needing to change every time a tool is
    # added.
    assert len(tool_names) >= 70, tool_names

    failures = []
    for name in tool_names:
        response = handler.handle_request(
            http_method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            }),
        )
        if response["status"] != 200:
            failures.append((name, f"HTTP {response['status']}: {response['content']}"))
            continue

        body = json.loads(response["content"])
        if "error" in body:
            message = body["error"].get("message", "")
        else:
            result = body["result"]
            message = (
                result["content"][0]["text"] if result.get("isError") else ""
            )

        if _SIGNATURE_MISMATCH.search(message):
            failures.append((name, message))

    assert not failures, (
        "the following tools dispatched a signature-mismatch error "
        "(a lambda args: / handler(**tool_args) mismatch, not a "
        f"business-logic refusal): {failures}"
    )


def test_signature_mismatch_regex_matches_this_interpreters_typeerror_wording():
    """Pins the detector itself, not just the tools it sweeps.

    The sweep above trusts ``_SIGNATURE_MISMATCH`` to recognise
    Python's own arity-mismatch wording. If a future interpreter
    changed that wording, the sweep would silently stop catching the
    ``lambda args:`` regression it exists to prevent -- exactly the
    "make it fatal" convention: reproduce the two real TypeErrors on
    THIS interpreter (whichever of 3.10/3.13 CI is running) and assert
    the regex still matches both, rather than trusting the wording
    never changes.
    """
    broken = lambda args: args  # noqa: E731 -- the exact mutation this guards against

    try:
        broken()
    except TypeError as exc:
        assert _SIGNATURE_MISMATCH.search(str(exc)), str(exc)
    else:
        raise AssertionError("expected TypeError for a missing positional arg")

    try:
        broken(some_kwarg=1)
    except TypeError as exc:
        assert _SIGNATURE_MISMATCH.search(str(exc)), str(exc)
    else:
        raise AssertionError("expected TypeError for an unexpected keyword arg")
