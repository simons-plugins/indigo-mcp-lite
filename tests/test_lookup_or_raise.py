"""Adversarial tests for ``_lookup_or_raise`` (issue #74).

Probed live against Indigo 2025.2: a genuinely missing id raises
``KeyError``; a bad key TYPE raises ``TypeError``/``OverflowError``
instead — never ``IndexError``/``ValueError``. So the only confirmed
"not found" signal is ``KeyError``; everything else the subscript
raises means the LOOKUP itself failed, and must not be relabelled as
"this id doesn't exist" nor routed through mcp_handler's self-correct-
and-retry bucket (which triggers on ``ValueError``/``TypeError``).

These tests exercise ``_lookup_or_raise`` directly rather than one of
its callers, plus one end-to-end check that a fault reaches
mcp_handler's back-off bucket (JSON-RPC error), not its isError:true
tool-result bucket — the whole point of distinguishing the two shapes.
"""

from unittest.mock import MagicMock

import pytest


def _collection_raising(exc):
    """A collection double whose ``__getitem__`` always raises ``exc``."""
    coll = MagicMock()
    coll.__getitem__.side_effect = exc
    return coll


# ----- KeyError: genuinely missing, unchanged friendly behaviour --------


def test_key_error_is_friendly_value_error_with_cause():
    from tools.lookup import _lookup_or_raise

    cause = KeyError("key id 42 not found in database")
    coll = _collection_raising(cause)

    with pytest.raises(ValueError, match="no device with id 42") as excinfo:
        _lookup_or_raise(coll, 42, "device")

    assert excinfo.value.__cause__ is cause


# ----- fault-shaped exceptions: NOT "not found" --------------------------


@pytest.mark.parametrize(
    "cause",
    [
        OverflowError("can't convert negative value to unsigned int"),
        TypeError("elem or key type must be either an elem integer ID, "
                   "elem string name, or elem instance"),
        IndexError("boom"),
        ValueError("boom"),
        RuntimeError("IOM busy"),
    ],
)
def test_fault_shaped_exception_is_not_value_or_type_error(cause):
    from tools.lookup import _LookupFault, _lookup_or_raise

    coll = _collection_raising(cause)

    with pytest.raises(_LookupFault) as excinfo:
        _lookup_or_raise(coll, 42, "device")

    # The whole point: mcp_handler routes ValueError/TypeError to its
    # self-correct-and-retry bucket. A fault must NOT be catchable
    # there, or a transient Indigo-side problem reads as "adjust your
    # arguments" instead of "back off".
    assert not isinstance(excinfo.value, ValueError)
    assert not isinstance(excinfo.value, TypeError)
    assert "no device with id" not in str(excinfo.value)
    assert excinfo.value.__cause__ is cause


def test_fault_message_names_the_entity_without_claiming_absence():
    from tools.lookup import _lookup_or_raise

    coll = _collection_raising(RuntimeError("IOM busy"))

    with pytest.raises(Exception) as excinfo:
        _lookup_or_raise(coll, 51886070, "device")

    msg = str(excinfo.value)
    assert "51886070" in msg
    assert "device" in msg
    assert "no device" not in msg


# ----- end-to-end: a fault reaches the back-off bucket, not self-correct -


def test_fault_from_lookup_reaches_mcp_handler_back_off_bucket(mock_indigo):
    """A get_device_by_id call whose lookup FAULTS (not "not found")
    must come back as an MCP protocol-level error (mcp_handler's
    ``except Exception`` -> back off), never as an isError:true
    tool-result (the self-correct-and-retry bucket reserved for
    ValueError/TypeError)."""
    import json

    from mcp_handler import MCPHandler
    from tool_registry import register_all

    mock_indigo.devices.__getitem__.side_effect = RuntimeError("IOM busy")

    handler = MCPHandler(server_name="test", server_version="0")
    register_all(handler, indigo_module=mock_indigo)

    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_device_by_id", "arguments": {"id": 42}},
        }),
    )

    body = json.loads(response["content"])
    assert "error" in body, f"expected a JSON-RPC protocol error, got: {body}"
    assert body["error"]["code"] == -32603
