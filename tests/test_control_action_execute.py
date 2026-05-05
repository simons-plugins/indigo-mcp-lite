"""TDD tests for action_execute_group."""
import pytest


def test_action_execute_group_calls_indigo(mock_indigo):
    from tools.control import _action_execute_group_handler

    result = _action_execute_group_handler(
        {"action_group_id": 99}, mock_indigo
    )
    mock_indigo.actionGroup.execute.assert_called_once_with(99)
    assert result["status"] == "ok"


def test_action_execute_group_missing_id_raises(mock_indigo):
    from tools.control import _action_execute_group_handler

    with pytest.raises(ValueError, match="action_group_id"):
        _action_execute_group_handler({}, mock_indigo)
    mock_indigo.actionGroup.execute.assert_not_called()


def test_action_execute_group_non_int_id_raises(mock_indigo):
    from tools.control import _action_execute_group_handler

    with pytest.raises(ValueError, match="action_group_id"):
        _action_execute_group_handler(
            {"action_group_id": "ninetynine"}, mock_indigo
        )
    mock_indigo.actionGroup.execute.assert_not_called()
