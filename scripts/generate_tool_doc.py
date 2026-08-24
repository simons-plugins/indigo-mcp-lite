"""Generate the README's tool-list table from the live registry.

Run from the repo root:

    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
        scripts/generate_tool_doc.py

Output is written to stdout as Markdown. The README keeps a copy in
the body between ``<!-- BEGIN TOOL TABLE -->`` and
``<!-- END TOOL TABLE -->`` markers; refresh by piping through
``sed`` or by running this script and pasting.

Single source of truth is ``tool_registry.register_all`` — the
script imports the same registration path the live plugin uses, so
new tools added to any ``tools/*.py`` module automatically show up
in the table on the next regenerate.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock


REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PLUGIN = (
    REPO_ROOT / "Indigo MCP Lite.indigoPlugin" / "Contents" / "Server Plugin"
)
sys.path.insert(0, str(SERVER_PLUGIN))


def _build_handler():
    """Construct an MCPHandler with all tools registered.

    Uses MagicMock for the indigo runtime + a no-op Indexer so we
    can call ``tool_registry.register_all`` exactly the way the live
    plugin does, without needing a running Indigo server. The
    handler ends up with the full real registration record for
    every tool (name, description, schema).
    """
    sys.modules.setdefault("indigo", MagicMock(name="indigo"))
    from indexer import Indexer
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    indigo_mock = MagicMock(name="indigo")
    indigo_mock.devices = []
    indigo_mock.variables = []
    indigo_mock.actionGroups = []

    indexer = Indexer(indigo_module=indigo_mock)
    indexer.build()

    handler = MCPHandler(server_name="indigo-mcp-lite", server_version="docgen")
    register_all(handler, indigo_module=indigo_mock, indexer=indexer)
    return handler


# System tool names are checked first so plugin-related tools don't
# leak into the generic Lookup bucket via the ``list_*``/``get_*``
# prefix rules.
_SYSTEM_TOOLS = frozenset({
    "query_event_log", "list_plugins", "get_plugin_by_id",
    "get_plugin_status", "restart_plugin",
    # Issue #71: plugin action discovery/dispatch. Grouped with the
    # other plugin-lifecycle tools (same reasoning as restart_plugin
    # being System rather than Control) instead of Lookup/Control.
    "list_plugin_actions", "plugin_execute_action",
})


# Automation-contents tools (.indiDb reader) get their own group;
# without this, find_automation_references would match no predicate
# and silently vanish from the table.
_AUTOMATION_CONTENT_TOOLS = frozenset({
    "get_automation_contents", "find_automation_references",
    "list_automation_scripts",
})


# Auto Lights config tools (issue #66) get their own group for the
# same reason as Automation contents above: auto_lights_list_zones
# would otherwise match the generic list_* Lookup rule while its
# three write siblings (set_level/set_zone_enabled/reset_locks) would
# match no predicate at all and silently vanish from the table.
_AUTO_LIGHTS_TOOLS = frozenset({
    "auto_lights_list_zones", "auto_lights_set_level",
    "auto_lights_set_zone_enabled", "auto_lights_reset_locks",
})


def _is_lookup(n):
    if n in _SYSTEM_TOOLS or n in _AUTOMATION_CONTENT_TOOLS or n in _AUTO_LIGHTS_TOOLS:
        return False
    return n.startswith("list_") or n.startswith("get_")


_HISTORY_TOOLS = frozenset({"query_sql_logger", "list_sql_logger_columns"})


def _is_lookup_final(n):
    return _is_lookup(n) and n not in _HISTORY_TOOLS


def _is_control(n):
    return (
        n.startswith("device_") or n.startswith("thermostat_")
        or n.startswith("variable_") or n == "action_execute_group"
        or n.startswith("trigger_") or n.startswith("schedule_")
        or n.startswith("sprinkler_") or n.startswith("speedcontrol_")
    )


_GROUPS = (
    ("Lookup", _is_lookup_final),
    ("Automation contents", lambda n: n in _AUTOMATION_CONTENT_TOOLS),
    ("Control", _is_control),
    ("History", lambda n: n in _HISTORY_TOOLS),
    ("System", lambda n: n in _SYSTEM_TOOLS),
    ("Search", lambda n: n == "find_devices"),
    ("Auto Lights config", lambda n: n in _AUTO_LIGHTS_TOOLS),
)


def _row(name, description):
    """Render one Markdown table row, single-line and pipe-escaped."""
    one_line = " ".join(description.split())
    safe = one_line.replace("|", "\\|")
    return f"| `{name}` | {safe} |"


def generate_table():
    """Build the full Markdown block (leading marker comment plus
    every group table) as a single string. Shared by the default
    print path and ``--check`` so there is exactly one place that
    defines what "up to date" means.

    Raises via ``sys.exit(1)`` (after printing to stderr) if any tool
    doesn't land in EXACTLY one group -- a tool matching none silently
    vanished from the README for five releases (trigger/schedule/
    sprinkler/speedcontrol/query_sql_logger) before this check existed.
    """
    handler = _build_handler()
    # _tools is {name: {description, inputSchema, handler}} — flatten
    # to (name, description) pairs sorted alphabetically for stable diffs.
    tools = sorted(
        (name, info["description"])
        for name, info in handler._tools.items()  # type: ignore[attr-defined]
    )

    placements = {name: [l for l, p in _GROUPS if p(name)] for name, _ in tools}
    bad = {n: ls for n, ls in placements.items() if len(ls) != 1}
    if bad:
        print(f"ERROR: tools not in exactly one group: {bad}", file=sys.stderr)
        sys.exit(1)

    lines = [
        f"<!-- AUTO-GENERATED by scripts/generate_tool_doc.py — "
        f"{len(tools)} tools -->",
        "",
    ]
    for label, predicate in _GROUPS:
        group = [(name, desc) for name, desc in tools if predicate(name)]
        if not group:
            continue
        lines.append(f"### {label} tools ({len(group)})")
        lines.append("")
        lines.append("| Tool | Description |")
        lines.append("|------|-------------|")
        lines.extend(_row(name, desc) for name, desc in group)
        lines.append("")
    return "\n".join(lines)


def _readme_committed_block():
    """Return the README's current tool-table content, exactly as it
    sits between the BEGIN/END TOOL TABLE markers (exclusive of the
    markers themselves, with no trailing blank line)."""
    readme_path = REPO_ROOT / "README.md"
    text = readme_path.read_text()
    begin = "<!-- BEGIN TOOL TABLE -->\n"
    end = "\n<!-- END TOOL TABLE -->"
    try:
        start_idx = text.index(begin) + len(begin)
        end_idx = text.index(end, start_idx)
    except ValueError as exc:
        raise ValueError(
            f"{readme_path} is missing the {begin.strip()!r} / "
            f"{end.strip()!r} markers -- cannot locate the tool table to "
            "compare against."
        ) from exc
    return text[start_idx:end_idx]


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Generate (or check) the README's tool-list table."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Compare the live registry against README.md's committed "
            "table and exit nonzero on drift, without writing anything. "
            "NOT wired into CI or a pre-commit hook today -- "
            "tests/test_generate_tool_doc.py is the actual enforcement "
            "point (see module docstring). Uses argparse so a typo'd "
            "flag (e.g. --chek) is rejected with a usage error instead "
            "of silently falling through to the default print-and-exit-0 "
            "behaviour -- the exact failure mode --check itself was "
            "added to catch, one layer up (PR #73 review round 4, item "
            "6, then found unfixed by the post-#73 review)."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    """``--check`` verifies the README's committed block matches the
    live registry and exits nonzero on drift, WITHOUT writing anything.
    Default (no flag) behaviour is unchanged: print the table to
    stdout for pasting by hand.

    Nothing in this repo currently RUNS ``--check`` automatically --
    grepping ``.github/``, ``.pre-commit-config.yaml``, and
    ``Makefile`` for ``generate_tool_doc`` finds no matches.
    ``tests/test_generate_tool_doc.py`` (which calls
    ``generate_table()``/``_readme_committed_block()`` directly, not
    this CLI) is the actual enforcement point; treat this flag as a
    convenience for a human running it by hand, not a guard anything
    depends on.
    """
    args = _parse_args(argv)
    table = generate_table()
    if args.check:
        committed = _readme_committed_block()
        if table.rstrip("\n") != committed.rstrip("\n"):
            print(
                "README.md's tool table is out of date with the live "
                "tool registry. Run `python3 scripts/generate_tool_doc.py` "
                "and paste the output between the "
                "<!-- BEGIN TOOL TABLE --> / <!-- END TOOL TABLE --> "
                "markers in README.md.",
                file=sys.stderr,
            )
            sys.exit(1)
        print("README.md tool table matches the live registry.")
        return
    print(table)


if __name__ == "__main__":
    main()
