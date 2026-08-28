import pytest

from model_router.providers.dify_chatflow.event_parser import (
    DifyChatflowProtocolError,
    parse_sse_line,
)


def test_parse_message_delta() -> None:
    event = parse_sse_line(
        'data: {"event":"message","task_id":"task_1",'
        '"message_id":"message_1","conversation_id":"conversation_1",'
        '"answer":"你好","created_at":1720000000}'
    )

    assert event is not None
    assert event.event == "message"
    assert event.answer == "你好"
    assert event.task_id == "task_1"
    assert event.message_id == "message_1"
    assert event.conversation_id == "conversation_1"


def test_parse_message_end_metadata() -> None:
    event = parse_sse_line(
        'data:{"event":"message_end","task_id":"task_1",'
        '"message_id":"message_1","metadata":{"usage":{"total_tokens":12}}}'
    )

    assert event is not None
    assert event.event == "message_end"
    assert event.metadata["usage"]["total_tokens"] == 12


@pytest.mark.parametrize("line", ["", "event: ping", ": keep-alive"])
def test_ignore_non_data_lines(line: str) -> None:
    assert parse_sse_line(line) is None


def test_parse_error_without_retaining_raw_payload() -> None:
    event = parse_sse_line(
        'data: {"event":"error","status":503,"code":"upstream_error",'
        '"message":"provider unavailable"}'
    )

    assert event is not None
    assert event.status == 503
    assert event.code == "upstream_error"
    assert event.message == "provider unavailable"
    assert not hasattr(event, "raw")


@pytest.mark.parametrize(
    "line",
    [
        "data: {broken",
        'data: {"event":""}',
        'data: {"event":"message","metadata":[]}',
    ],
)
def test_reject_invalid_protocol(line: str) -> None:
    with pytest.raises(DifyChatflowProtocolError):
        parse_sse_line(line)
