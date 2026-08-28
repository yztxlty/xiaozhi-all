import pytest

from model_router.providers.dify_workflow.event_parser import (
    DifyProtocolError,
    parse_sse_line,
)


def test_parse_text_chunk() -> None:
    event = parse_sse_line(
        'data: {"event":"text_chunk","task_id":"task_1",'
        '"workflow_run_id":"run_1","data":{"text":"你好"}}'
    )

    assert event is not None
    assert event.event == "text_chunk"
    assert event.task_id == "task_1"
    assert event.workflow_run_id == "run_1"
    assert event.data["text"] == "你好"


@pytest.mark.parametrize("line", ["", "event: ping", ": keep-alive"])
def test_ignore_non_data_lines(line: str) -> None:
    assert parse_sse_line(line) is None


def test_parse_error_fields_without_exposing_raw_payload() -> None:
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
        'data: {"event":"text_chunk","data":[]}',
    ],
)
def test_reject_invalid_protocol(line: str) -> None:
    with pytest.raises(DifyProtocolError):
        parse_sse_line(line)
