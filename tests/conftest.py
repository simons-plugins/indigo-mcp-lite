"""Shared pytest fixtures for indigo-mcp-lite tests.

Mocks Indigo's runtime module so plugin code can be imported and
tested without the Indigo server being available. Mirrors the
``unittest.mock`` pattern used in ``netro/tests/conftest.py``.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Add the plugin's Server Plugin directory to sys.path so test modules
# can `from tool_registry import ...` etc. without packaging tricks.
SERVER_PLUGIN_DIR = (
    Path(__file__).parent.parent
    / "Indigo MCP Lite.indigoPlugin"
    / "Contents"
    / "Server Plugin"
)
sys.path.insert(0, str(SERVER_PLUGIN_DIR))


@pytest.fixture
def mock_indigo():
    """Inject a minimal mock for the ``indigo`` module.

    Plugin modules `import indigo` at module load; without this the
    import would fail under pytest. Cleans up after the test so
    fixtures stay isolated.
    """
    mock = MagicMock(name="indigo")
    sys.modules["indigo"] = mock
    yield mock
    del sys.modules["indigo"]
