from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class DifyChatflowProtocolError(ValueError):
    """Dify Chatflow 流式事件不符合约定。"""


@dataclass(frozen=True, slots=True)
class DifyChatflowEvent:
    event: str
    task_id: str | None
    message_id: str | None
    conversation_id: str | None
    workflow_run_id: str | None
    answer: str | None
    data: dict[str, Any]
    metadata: dict[str, Any]
    status: int | str | None = None
    code: str | None = None
    message: str | None = None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _object_field(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DifyChatflowProtocolError(f"Dify Chatflow 事件 {name} 必须是对象")
    return value


def parse_sse_line(line: str) -> DifyChatflowEvent | None:
    if not line or not line.startswith("data:"):
        return None

    serialized = line[5:].lstrip()
    if not serialized:
        raise DifyChatflowProtocolError("Dify Chatflow 事件缺少数据")
    try:
        payload = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise DifyChatflowProtocolError("Dify Chatflow 事件不是合法 JSON") from exc

    if not isinstance(payload, dict):
        raise DifyChatflowProtocolError("Dify Chatflow 事件必须是对象")
    event_name = payload.get("event")
    if not isinstance(event_name, str) or not event_name:
        raise DifyChatflowProtocolError("Dify Chatflow 事件缺少事件名称")

    status = payload.get("status")
    if not isinstance(status, (int, str)):
        status = None

    return DifyChatflowEvent(
        event=event_name,
        task_id=_optional_string(payload.get("task_id")),
        message_id=_optional_string(payload.get("message_id")),
        conversation_id=_optional_string(payload.get("conversation_id")),
        workflow_run_id=_optional_string(payload.get("workflow_run_id")),
        answer=_optional_string(payload.get("answer")),
        data=_object_field(payload, "data"),
        metadata=_object_field(payload, "metadata"),
        status=status,
        code=_optional_string(payload.get("code")),
        message=_optional_string(payload.get("message")),
    )
