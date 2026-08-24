"""README.md's tool table must not drift from the live tool registry.

PR #73 review round 4, item 6: ``scripts/generate_tool_doc.py``'s
``--check`` flag was silently ignored (the script always printed the
table and exited 0 regardless), so nothing ever caught the README
table going stale -- ``list_plugin_actions``/``plugin_execute_action``'s
descriptions drifted across review round 3 without a regenerate, and
no test or CI check noticed. This test performs the same comparison
``--check`` now does, as a permanent guard against a repeat.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load_generate_tool_doc():
    """Import scripts/generate_tool_doc.py as a module. Not on
    sys.path by default (only the Server Plugin dir is, via
    conftest.py), so add it just for this import."""
    added = str(SCRIPTS_DIR) not in sys.path
    if added:
        sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import generate_tool_doc
        return generate_tool_doc
    finally:
        if added:
            sys.path.remove(str(SCRIPTS_DIR))


def test_readme_tool_table_matches_live_registry():
    mod = _load_generate_tool_doc()
    generated = mod.generate_table()
    committed = mod._readme_committed_block()
    assert generated.rstrip("\n") == committed.rstrip("\n"), (
        "README.md's tool table is out of date with the live tool "
        "registry -- run `python3 scripts/generate_tool_doc.py` and "
        "paste the output between the <!-- BEGIN TOOL TABLE --> / "
        "<!-- END TOOL TABLE --> markers in README.md."
    )


def test_check_flag_exits_nonzero_on_drift(tmp_path, monkeypatch):
    """The --check flag itself (not just the underlying comparison)
    must actually exit nonzero on drift -- pin the CLI wiring, not
    just the comparison function, since the whole point of item 6 is
    that --check was silently a no-op before this fix."""
    mod = _load_generate_tool_doc()
    # A README with a deliberately wrong committed block.
    fake_readme = tmp_path / "README.md"
    fake_readme.write_text(
        "before\n<!-- BEGIN TOOL TABLE -->\nSTALE CONTENT\n"
        "<!-- END TOOL TABLE -->\nafter\n"
    )
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generate_tool_doc.py", "--check"])
    try:
        mod.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("main() must sys.exit(1) on a stale README")


def test_check_flag_exits_zero_when_up_to_date(monkeypatch):
    """The inverse: --check must NOT exit nonzero when the README is
    genuinely current (the case test_readme_tool_table_matches_live_
    registry above is already pinning) -- proves --check isn't just
    unconditionally failing."""
    mod = _load_generate_tool_doc()
    monkeypatch.setattr(sys, "argv", ["generate_tool_doc.py", "--check"])
    try:
        mod.main()
    except SystemExit as exc:
        raise AssertionError(
            f"main() must not exit nonzero when README is current, got {exc.code}"
        )
