from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class DifyProtocolError(ValueError):
    """Dify 流式事件不符合约定。"""


@dataclass(frozen=True, slots=True)
class DifyEvent:
    event: str
    task_id: str | None
    workflow_run_id: str | None
    data: dict[str, Any]
    status: int | str | None = None
    code: str | None = None
    message: str | None = None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_sse_line(line: str) -> DifyEvent | None:
    if not line or not line.startswith("data: "):
        return None

    try:
        payload = json.loads(line[6:])
    except json.JSONDecodeError as exc:
        raise DifyProtocolError("Dify 流式事件不是合法 JSON") from exc

    if not isinstance(payload, dict):
        raise DifyProtocolError("Dify 流式事件必须是对象")

    event_name = payload.get("event")
    if not isinstance(event_name, str) or not event_name:
        raise DifyProtocolError("Dify 流式事件缺少事件名称")

    data = payload.get("data")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise DifyProtocolError("Dify 流式事件 data 必须是对象")

    status = payload.get("status")
    if not isinstance(status, (int, str)):
        status = None

    return DifyEvent(
        event=event_name,
        task_id=_optional_string(payload.get("task_id")),
        workflow_run_id=_optional_string(payload.get("workflow_run_id")),
        data=data,
        status=status,
        code=_optional_string(payload.get("code")),
        message=_optional_string(payload.get("message")),
    )
