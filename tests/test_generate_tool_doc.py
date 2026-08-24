"""README.md's tool table must not drift from the live tool registry.

PR #73 review round 4, item 6: ``scripts/generate_tool_doc.py``'s
``--check`` flag was silently ignored (the script always printed the
table and exited 0 regardless), so nothing ever caught the README
table going stale -- ``list_plugin_actions``/``plugin_execute_action``'s
descriptions drifted across review round 3 without a regenerate, and
no test or CI check noticed. This test performs the same comparison
``--check`` now does, as a permanent guard against a repeat.

Post-merge review, item 9: ``"--check" in sys.argv[1:]`` was itself
typo-tolerant -- ``--chek`` silently fell through to the default
print-and-exit-0 path, the exact failure mode ``--check`` was added to
catch, one layer up. Fixed with ``argparse``, which rejects an unknown
flag with a nonzero exit; ``test_typo_flag_is_rejected_not_silently_
ignored`` below pins that. Note that nothing in this repo actually
RUNS ``--check`` (no CI workflow, no pre-commit hook references
``generate_tool_doc``) -- this test file remains the real enforcement
point, not the CLI flag.
"""
import sys
from pathlib import Path

import pytest

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


def test_typo_flag_is_rejected_not_silently_ignored(monkeypatch):
    """Post-merge review, item 9: a typo'd flag (e.g. --chek) must be
    rejected with a usage error, not silently fall through to the
    default print-and-exit-0 behaviour -- the exact failure mode
    --check itself exists to catch, one layer up. Pre-argparse,
    "--check" in sys.argv[1:] made this a no-op for anything but the
    exact string "--check"."""
    mod = _load_generate_tool_doc()
    monkeypatch.setattr(sys, "argv", ["generate_tool_doc.py", "--chek"])
    with pytest.raises(SystemExit) as excinfo:
        mod.main()
    assert excinfo.value.code != 0


def test_readme_missing_markers_raises_named_error(tmp_path, monkeypatch):
    """Post-merge review, item 9 (minor): a README missing the BEGIN/
    END TOOL TABLE markers used to raise a raw
    "ValueError: substring not found" from text.index -- must name
    what's actually wrong instead."""
    mod = _load_generate_tool_doc()
    fake_readme = tmp_path / "README.md"
    fake_readme.write_text("no markers here at all\n")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

    with pytest.raises(ValueError, match="BEGIN TOOL TABLE"):
        mod._readme_committed_block()
