from __future__ import annotations

import json

from realtime_protocol import ControlMessage, ProtocolError


def decode_control_message(serialized: str) -> ControlMessage:
    try:
        payload = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise ProtocolError("控制消息不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("控制消息必须是对象")
    return ControlMessage.from_dict(payload)


def encode_control_message(message: ControlMessage) -> str:
    return json.dumps(message.to_dict(), ensure_ascii=False, separators=(",", ":"))
