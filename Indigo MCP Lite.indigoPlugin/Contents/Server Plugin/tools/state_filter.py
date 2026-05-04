"""Spec-based device state filtering for get_devices_by_state.

Spec format mirrors mlamoure's MCP server for compatibility:
- ``{"onState": true}``         — equals (default for non-string values)
- ``{"batteryLevel": "<20"}``    — less-than
- ``{"brightness": ">=50"}``     — greater-or-equal
- ``{"someKey": ">", "other": "<="}`` — multi-key spec, all must match (AND)

Comparators are str-prefix encoded so the JSON-RPC call surface stays
plain JSON (no nested objects). Supported prefixes: ``>=``, ``<=``,
``>``, ``<``. Any other string OR a non-string value compares as ``==``.
"""

_OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
}


def _parse_spec(value):
    """Return (op, target). String values starting with a known
    operator prefix decode into (op, parsed-numeric-or-string-tail);
    everything else returns ('==', value)."""
    if isinstance(value, str):
        for prefix in (">=", "<=", ">", "<"):
            if value.startswith(prefix):
                tail = value[len(prefix):]
                try:
                    return prefix, float(tail)
                except ValueError:
                    return prefix, tail
    return "==", value


def _device_state(device, state_name):
    """Lookup state on the device — first via the .states dict (where
    plugin-managed state lives), then via direct attribute (where Indigo
    puts core attrs like onState, brightness)."""
    states = getattr(device, "states", None)
    if states is not None:
        try:
            value = states.get(state_name)
            if value is not None:
                return value
        except (AttributeError, TypeError):
            pass  # states wasn't a dict
    return getattr(device, state_name, None)


def matches(device, spec):
    """Return True if device matches every key in spec.

    A key whose state is missing on this device is treated as a non-match
    (the device is excluded from the result). No exception is raised —
    state queries against partial fleets are normal."""
    for state_name, raw in spec.items():
        op, target = _parse_spec(raw)
        current = _device_state(device, state_name)
        if current is None:
            return False
        if not _OPS[op](current, target):
            return False
    return True
