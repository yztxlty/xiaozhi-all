from __future__ import annotations

import asyncio
import os
from pathlib import Path
from aiohttp import web

from .demo import _H5_ASSETS, _H5_ROOT, handler as h5_handler
from .device_server import handle_device_connection
from .ota_server import build_ota_response


class _WebSocketAdapter:
    def __init__(self, websocket: web.WebSocketResponse, request: web.Request) -> None:
        self._websocket = websocket
        self.request = request
        self.remote_address = request.remote

    async def recv(self) -> str | bytes:
        message = await self._websocket.receive()
        if message.type is web.WSMsgType.TEXT:
            return message.data
        if message.type is web.WSMsgType.BINARY:
            return message.data
        raise ConnectionError("WebSocket 已关闭")

    async def send(self, value: str | bytes) -> None:
        if isinstance(value, bytes):
            await self._websocket.send_bytes(value)
        else:
            await self._websocket.send_str(value)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self._websocket.close(code=code, message=reason.encode())

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.recv()
        except ConnectionError as exc:
            raise StopAsyncIteration from exc


class _PrefetchedWebSocketAdapter(_WebSocketAdapter):
    """把路由选择时读取的首帧还给具体协议处理器。"""

    def __init__(self, websocket: web.WebSocketResponse, request: web.Request, first: str | bytes) -> None:
        super().__init__(websocket, request)
        self._first = first

    async def recv(self) -> str | bytes:
        if self._first is not None:
            first, self._first = self._first, None
            return first
        return await super().recv()


async def _websocket_route(request: web.Request) -> web.StreamResponse:
    websocket = web.WebSocketResponse(max_msg_size=2 * 1024 * 1024)
    await websocket.prepare(request)
    first_message = await websocket.receive()
    if first_message.type is web.WSMsgType.TEXT:
        first: str | bytes = first_message.data
    elif first_message.type is web.WSMsgType.BINARY:
        first = first_message.data
    else:
        await websocket.close(code=1002, message="首帧缺失".encode())
        return websocket
    adapter = _PrefetchedWebSocketAdapter(websocket, request, first)
    if request.path == "/xiaozhi/v1/ws":
        try:
            hello = json.loads(first) if isinstance(first, str) else {}
        except json.JSONDecodeError:
            hello = {}
        if (hello.get("audio_params") or {}).get("format") == "pcm":
            await h5_handler(adapter)
        else:
            await handle_device_connection(adapter)
    else:
        await h5_handler(adapter)
    return websocket


async def _ota_route(request: web.Request) -> web.Response:
    try:
        payload = build_ota_response(
            device_id=request.headers.get("Device-Id", ""),
            client_id=request.headers.get("Client-Id", ""),
            user_agent=request.headers.get("User-Agent", ""),
            language=request.headers.get("Accept-Language", ""),
        )
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response(payload)


async def _static_route(request: web.Request) -> web.StreamResponse:
    path = request.path.split("?", 1)[0]
    asset = _H5_ASSETS.get(path)
    if asset is None:
        raise web.HTTPNotFound()
    filename, content_type = asset
    target = Path(_H5_ROOT) / filename
    if not target.is_file():
        raise web.HTTPInternalServerError(text="h5 asset not found")
    use_gzip = (
        "gzip" in request.headers.get("Accept-Encoding", "").lower()
        and path in {"/vendor/voice/ort-wasm-simd-threaded.wasm", "/vendor/voice/silero_vad_v5.onnx"}
        and target.with_name(target.name + ".gz").is_file()
    )
    if use_gzip:
        return web.Response(
            body=target.with_name(target.name + ".gz").read_bytes(),
            content_type=content_type.split(";", 1)[0],
            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding", "Cache-Control": "public, max-age=86400"},
        )
    return web.FileResponse(target, headers={"Content-Type": content_type, "Cache-Control": "no-store"})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/xiaozhi/ota/", _ota_route)
    app.router.add_get("/xiaozhi/v1/ws", _websocket_route)
    app.router.add_get("/", _static_route)
    app.router.add_get("/{path:.*}", _static_route)
    return app


def main() -> None:
    web.run_app(
        create_app(),
        host=os.getenv("XIAOZHI_DEVICE_HOST", "0.0.0.0"),
        port=int(os.getenv("XIAOZHI_DEVICE_PORT", "18765")),
    )


if __name__ == "__main__":
    main()
