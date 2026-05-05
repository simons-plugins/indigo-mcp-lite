"""Numeric coercion helpers reused across control tools.

Centralised because 7 of the 13 Phase 4 tools normalise the same
families of inputs (brightness 0-100 / 0-1, RGB bytes 0-255, RGB
percent 0-100, and hex colour strings). Keeping the helpers here means
the wire-input rules — clamping, rounding direction, alternate units —
live in exactly one place. Each helper raises ``ValueError`` on
unrecoverable input so the JSON-RPC layer can map it to ``isError``.
"""


def _clamp(value, low, high):
    """Bound ``value`` to ``[low, high]``."""
    return max(low, min(high, value))


def normalize_brightness(value):
    """Return an integer 0–100 brightness from a flexible numeric input.

    Accepts either a 0–100 integer/float (passed through, clamped) or a
    0–1 fractional float (scaled up by ×100). The 0–1 detection only
    fires for ``0 < v <= 1`` so a literal 0 stays at 0 in either unit.
    """
    v = float(value)
    if 0 < v <= 1:
        v = v * 100
    return int(round(_clamp(v, 0, 100)))


def normalize_rgb_byte(value):
    """Return an integer 0–255 RGB channel from a numeric input.

    Floats are rounded to the nearest integer, then clamped — same
    rule the CSS spec uses for sub-byte precision (e.g. 127.5 → 128).
    """
    return int(round(_clamp(float(value), 0, 255)))


def normalize_rgb_percent(value):
    """Return an integer 0–255 RGB channel from a 0–100 percent input.

    Used by ``device_set_rgb_percent`` so callers can pick the unit
    that matches their sensor or scene-builder UI without doing the
    arithmetic themselves.
    """
    return int(round(_clamp(float(value), 0, 100) * 255 / 100))


def parse_hex_color(value):
    """Parse a CSS hex colour string into an ``(r, g, b)`` byte tuple.

    Accepts ``#RRGGBB``, ``RRGGBB``, ``#RGB``, or ``RGB``. The 3-digit
    short form expands each nibble (``F80`` → ``FF8800``) per CSS.
    Raises ``ValueError`` on anything else, including 4-/8-digit forms
    with alpha — Indigo's ``setColorLevels`` doesn't carry alpha, so
    accepting it here would silently discard data.
    """
    s = value.lstrip("#") if isinstance(value, str) else ""
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"invalid hex color: {value!r}")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError as exc:
        raise ValueError(f"invalid hex color: {value!r}") from exc
