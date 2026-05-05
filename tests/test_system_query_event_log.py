"""TDD tests for query_event_log.

Wraps ``indigo.server.getEventLogList(returnAsList=True,
showTimeStamp=True, lineCount=N)``. Each Indigo entry is a
dict-like with ``TimeStamp`` / ``TypeStr`` / ``Message`` keys
(per Home Intelligence's event_log_reader.py).
"""
from datetime import datetime, timedelta

import pytest


def _entry(ts, source, message):
    """Build a fake Indigo log entry as a plain dict (matches
    ``getEventLogList(returnAsList=True)`` shape)."""
    return {"TimeStamp": ts, "TypeStr": source, "Message": message}


def test_query_event_log_returns_serialised_entries(mock_indigo):
    from tools.system import _query_event_log_handler

    mock_indigo.server.getEventLogList.return_value = [
        _entry("2026-05-05 10:00:00.000", "Server", "Started"),
        _entry("2026-05-05 10:00:01.000", "Z-Wave", "Device on"),
    ]
    result = _query_event_log_handler({"limit": 10}, mock_indigo)

    mock_indigo.server.getEventLogList.assert_called_once_with(
        returnAsList=True, showTimeStamp=True, lineCount=10
    )
    assert result["results"] == [
        {"timestamp": "2026-05-05 10:00:00.000",
         "source": "Server", "message": "Started"},
        {"timestamp": "2026-05-05 10:00:01.000",
         "source": "Z-Wave", "message": "Device on"},
    ]
    assert result["total_count"] == 2
    assert result["limit"] == 10


def test_query_event_log_default_limit_50(mock_indigo):
    from tools.system import _query_event_log_handler

    mock_indigo.server.getEventLogList.return_value = []
    _query_event_log_handler({}, mock_indigo)

    # Default limit is 50; we request that many lines.
    call = mock_indigo.server.getEventLogList.call_args
    assert call.kwargs["lineCount"] == 50


def test_query_event_log_clamps_limit_to_500(mock_indigo):
    from tools.system import _query_event_log_handler

    mock_indigo.server.getEventLogList.return_value = []
    _query_event_log_handler({"limit": 9999}, mock_indigo)

    call = mock_indigo.server.getEventLogList.call_args
    assert call.kwargs["lineCount"] == 500


def test_query_event_log_floors_limit_to_1(mock_indigo):
    from tools.system import _query_event_log_handler

    mock_indigo.server.getEventLogList.return_value = []
    _query_event_log_handler({"limit": 0}, mock_indigo)

    call = mock_indigo.server.getEventLogList.call_args
    assert call.kwargs["lineCount"] == 1


def test_query_event_log_filters_by_since(mock_indigo):
    from tools.system import _query_event_log_handler

    mock_indigo.server.getEventLogList.return_value = [
        _entry("2026-05-05 09:00:00.000", "Server", "Old"),
        _entry("2026-05-05 11:00:00.000", "Server", "Recent"),
    ]
    result = _query_event_log_handler(
        {"limit": 10, "since": "2026-05-05T10:00:00"}, mock_indigo
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["message"] == "Recent"


def test_query_event_log_invalid_since_raises(mock_indigo):
    from tools.system import _query_event_log_handler

    with pytest.raises(ValueError, match="since"):
        _query_event_log_handler({"since": "not-a-date"}, mock_indigo)


def test_query_event_log_unparseable_timestamps_dropped(mock_indigo):
    from tools.system import _query_event_log_handler

    # Entries without a parseable timestamp are dropped when ``since``
    # filtering is active — we can't compare them safely. Without
    # ``since`` they pass through unchanged.
    mock_indigo.server.getEventLogList.return_value = [
        _entry("garbage", "Server", "?"),
        _entry("2026-05-05 11:00:00.000", "Server", "Good"),
    ]

    # Without since filter: both pass through.
    result = _query_event_log_handler({"limit": 10}, mock_indigo)
    assert len(result["results"]) == 2

    # With since filter: only the parseable one passes.
    result = _query_event_log_handler(
        {"limit": 10, "since": "2026-05-05T10:00:00"}, mock_indigo
    )
    assert len(result["results"]) == 1


def test_query_event_log_filters_by_level_error(mock_indigo):
    from tools.system import _query_event_log_handler

    mock_indigo.server.getEventLogList.return_value = [
        _entry("2026-05-05 10:00:00.000", "Server", "Started"),
        _entry("2026-05-05 10:00:01.000", "Server", "Error: thing broke"),
        _entry("2026-05-05 10:00:02.000", "Plugin", "Exception in handler"),
        _entry("2026-05-05 10:00:03.000", "Server", "Warning: low battery"),
    ]
    result = _query_event_log_handler(
        {"limit": 10, "level": "ERROR"}, mock_indigo
    )
    messages = [r["message"] for r in result["results"]]
    assert "Error: thing broke" in messages
    assert "Exception in handler" in messages
    assert "Started" not in messages
    assert "Warning: low battery" not in messages


def test_query_event_log_filters_by_level_warning(mock_indigo):
    from tools.system import _query_event_log_handler

    mock_indigo.server.getEventLogList.return_value = [
        _entry("2026-05-05 10:00:00.000", "Server", "Started"),
        _entry("2026-05-05 10:00:01.000", "Server", "Warning: low battery"),
    ]
    result = _query_event_log_handler(
        {"limit": 10, "level": "WARNING"}, mock_indigo
    )
    messages = [r["message"] for r in result["results"]]
    assert messages == ["Warning: low battery"]


def test_query_event_log_invalid_level_raises(mock_indigo):
    from tools.system import _query_event_log_handler

    with pytest.raises(ValueError, match="level"):
        _query_event_log_handler({"level": "TRACE"}, mock_indigo)


def test_query_event_log_handles_empty_log(mock_indigo):
    from tools.system import _query_event_log_handler

    mock_indigo.server.getEventLogList.return_value = []
    result = _query_event_log_handler({}, mock_indigo)

    assert result["results"] == []
    assert result["total_count"] == 0
