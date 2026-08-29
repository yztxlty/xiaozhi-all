from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """终端控制消息不符合稳定协议。"""


class ControlType(StrEnum):
    HELLO = "hello"
    LISTEN = "listen"
    ABORT = "abort"
    TTS = "tts"
    MCP = "mcp"
    PING = "ping"
    PONG = "pong"


@dataclass(frozen=True, slots=True)
class ControlMessage:
    type: ControlType
    payload: dict[str, Any]
    version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ControlMessage:
        version = value.get("version", PROTOCOL_VERSION)
        if version != PROTOCOL_VERSION:
            raise ProtocolError(f"不支持的协议版本：{version}")
        try:
            message_type = ControlType(value.get("type"))
        except (TypeError, ValueError) as exc:
            raise ProtocolError("未知控制消息类型") from exc
        payload = {key: item for key, item in value.items() if key not in {"version", "type"}}
        return cls(type=message_type, payload=payload, version=version)

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "type": self.type.value, **self.payload}
