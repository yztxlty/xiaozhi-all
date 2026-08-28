import json

import pytest

from xiaozhi_gateway.server import handle_connection


class FakeWebSocket:
    def __init__(self, incoming):
        self.incoming = iter(incoming)
        self.sent: list[str] = []
        self.closed = None

    async def recv(self):
        return next(self.incoming)

    async def send(self, value):
        self.sent.append(value)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.incoming)
        except StopIteration:
            raise StopAsyncIteration

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


@pytest.mark.asyncio
async def test_server_accepts_real_firmware_hello() -> None:
    websocket = FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "hello",
                    "version": 1,
                    "transport": "websocket",
                    "audio_params": {
                        "format": "opus",
                        "sample_rate": 16000,
                        "channels": 1,
                        "frame_duration": 60,
                    },
                }
            )
        ]
    )

    await handle_connection(websocket)

    response = json.loads(websocket.sent[0])
    assert response["type"] == "hello"
    assert response["transport"] == "websocket"
    assert response["audio_params"]["sample_rate"] == 16000


@pytest.mark.asyncio
async def test_server_forwards_text_turn_to_reply_callback() -> None:
    websocket = FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "hello",
                    "version": 1,
                    "transport": "websocket",
                    "audio_params": {
                        "format": "opus",
                        "sample_rate": 16000,
                        "channels": 1,
                        "frame_duration": 60,
                    },
                }
            ),
            json.dumps({"type": "text", "text": "你好"}),
        ]
    )

    async def reply(_session_id: str, text: str) -> str:
        return f"收到：{text}"

    await handle_connection(websocket, on_text=reply)

    assert json.loads(websocket.sent[1]) == {
        "type": "llm.text.delta",
        "text": "收到：你好",
    }
