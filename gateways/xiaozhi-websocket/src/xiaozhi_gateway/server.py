from __future__ import annotations

import json
import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .handshake import HandshakeError, parse_hello


def server_hello(session_id: str, version: int = 1) -> str:
    return json.dumps(
        {
            "type": "hello",
            "version": version,
            "transport": "websocket",
            "session_id": session_id,
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60,
            },
        },
        separators=(",", ":"),
    )


async def handle_connection(websocket: Any, on_audio: Callable[[str, bytes], Awaitable[None]] | None = None) -> None:
    session_id = f"s_{uuid.uuid4().hex}"
    first = await websocket.recv()
    if not isinstance(first, str):
        await websocket.close(code=1002, reason="首帧必须是 hello")
        return
    try:
        hello = json.loads(first)
        parsed = parse_hello(hello)
    except (json.JSONDecodeError, HandshakeError):
        await websocket.close(code=1002, reason="hello 不符合协议")
        return

    await websocket.send(server_hello(session_id, hello.get("version", 1)))
    async for message in websocket:
        if isinstance(message, bytes):
            if on_audio is not None:
                await on_audio(session_id, message)
            continue
        try:
            control = json.loads(message)
        except json.JSONDecodeError:
            await websocket.close(code=1007, reason="控制消息不是 JSON")
            return
        if control.get("type") == "ping":
            await websocket.send(json.dumps({"type": "pong"}, separators=(",", ":")))


async def serve(host: str = "0.0.0.0", port: int = 8765) -> None:
    import websockets

    async with websockets.serve(handle_connection, host, port, max_size=65536):
        await asyncio.Future()


def main() -> None:
    asyncio.run(serve())
