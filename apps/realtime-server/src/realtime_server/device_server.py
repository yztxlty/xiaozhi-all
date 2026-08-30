from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import time
import uuid
from typing import Any

import httpx
from dotenv import load_dotenv

from audio_codec import DeviceOpusInput
from xiaozhi_gateway.device_session import DeviceOutputAdapter
from xiaozhi_gateway.handshake import HandshakeError, parse_hello
from xiaozhi_gateway.protocol_v1 import DeviceProtocolV1

from .demo import _asr_then_pipeline, _build_pipecat_processors, _warmup_pipecat_processors, _dify_http_client_options
from .ota_server import verify_device_token
from voice_session.infrastructure.pipecat.runtime import PipecatVoiceRuntime

logger = logging.getLogger("xiaozhi_device_server")


class DeviceSpeechBoundary:
    """使用已解码 PCM 的能量识别设备端一轮语音结束。"""

    def __init__(self, silence_seconds: float = 0.72, rms_threshold: float = 35.0) -> None:
        self.silence_seconds = silence_seconds
        self.rms_threshold = rms_threshold
        self._last_voice_at: float | None = None

    def reset(self) -> None:
        self._last_voice_at = None

    def is_voice(self, pcm: bytes) -> bool:
        if not pcm or len(pcm) % 2:
            return False
        samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        rms = (sum(sample * sample for sample in samples) / len(samples)) ** 0.5
        return rms >= self.rms_threshold

    def feed(self, pcm: bytes, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if self.is_voice(pcm):
            self._last_voice_at = now
            return False
        return self._last_voice_at is not None and now - self._last_voice_at >= self.silence_seconds


def is_device_path(path: str) -> bool:
    return path.split("?", 1)[0] == "/xiaozhi/v1/ws"


async def handle_device_connection(websocket: Any) -> None:
    session_id = f"s_{uuid.uuid4().hex}"
    first = await websocket.recv()
    if not isinstance(first, str):
        await websocket.close(code=1002, reason="首帧必须是 hello JSON")
        return
    try:
        payload = json.loads(first)
        parse_hello(payload)
    except (json.JSONDecodeError, HandshakeError):
        await websocket.close(code=1002, reason="hello 不符合协议")
        return

    if os.getenv("XIAOZHI_DEVICE_REQUIRE_AUTH", "1") == "1":
        headers = getattr(getattr(websocket, "request", None), "headers", {})
        device_id = headers.get("Device-Id", "")
        client_id = headers.get("Client-Id", "")
        if not verify_device_token(device_id, client_id, headers.get("Authorization", "")):
            await websocket.close(code=1008, reason="设备认证失败")
            return

    handshake = parse_hello(payload)
    headers = getattr(getattr(websocket, "request", None), "headers", {})
    logger.info(
        "[device] hello session=%s input=%s output=%s device_header=%s",
        session_id[:8], handshake.device_profile.input_audio.codec,
        handshake.device_profile.output_audio.codec, bool(headers.get("Device-Id")),
    )
    protocol = DeviceProtocolV1(websocket, session_id, handshake.device_profile)
    await protocol.send_hello()
    http: httpx.AsyncClient | None = None
    runtime: PipecatVoiceRuntime | None = None
    warmup_task: asyncio.Task | None = None
    asr_task: asyncio.Task | None = None
    cancel = asyncio.Event()
    speech_boundary = DeviceSpeechBoundary()
    input_closed = False
    last_voice_at = time.monotonic()
    idle_prompt_task: asyncio.Task | None = None

    try:
        http = httpx.AsyncClient(**_dify_http_client_options())
        processors = _build_pipecat_processors(http, device_mode=True)
        output = DeviceOutputAdapter(protocol, handshake.device_profile)
        runtime = PipecatVoiceRuntime(processors, output.json, output.pcm)
        # Do not block the device receive loop on optional provider warmup.
        # A device can start sending audio immediately after hello; provider
        # connections are created lazily by the first real turn.
        await runtime.start()
        # 后台预热不阻塞设备首帧收音，并尽量让首轮复用已建立的 TTS 连接。
        warmup_task = asyncio.create_task(_warmup_pipecat_processors(processors))
        decoder = DeviceOpusInput() if handshake.device_profile.input_audio.codec == "opus" else None
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=32)
        binary_frames = 0

        async def start_asr() -> None:
            nonlocal asr_task, cancel, audio_queue, decoder, input_closed, last_voice_at
            if asr_task is not None and not asr_task.done():
                return
            cancel = asyncio.Event()
            audio_queue = asyncio.Queue(maxsize=32)
            decoder = DeviceOpusInput() if handshake.device_profile.input_audio.codec == "opus" else None
            speech_boundary.reset()
            input_closed = False
            last_voice_at = time.monotonic()
            asr_task = asyncio.create_task(
                _asr_then_pipeline(
                    session_id,
                    audio_queue,
                    output.json,
                    runtime,
                    cancel,
                    auto_commit_on_final=True,
                )
            )

        async def interrupt_current_turn() -> None:
            nonlocal asr_task, audio_queue, decoder, input_closed
            cancel.set()
            await runtime.interrupt()
            if asr_task is not None and not asr_task.done():
                asr_task.cancel()
                await asyncio.gather(asr_task, return_exceptions=True)
            asr_task = None
            audio_queue = asyncio.Queue(maxsize=32)
            decoder = DeviceOpusInput() if handshake.device_profile.input_audio.codec == "opus" else None
            speech_boundary.reset()
            input_closed = False

        async def idle_prompt_loop() -> None:
            nonlocal asr_task, audio_queue, decoder, input_closed, last_voice_at
            while True:
                await asyncio.sleep(1)
                if time.monotonic() - last_voice_at < 30:
                    continue
                if not runtime.turn_done.is_set():
                    continue
                if asr_task is not None and not asr_task.done():
                    asr_task.cancel()
                    await asyncio.gather(asr_task, return_exceptions=True)
                    asr_task = None
                cancel.set()
                audio_queue = asyncio.Queue(maxsize=32)
                decoder = DeviceOpusInput() if handshake.device_profile.input_audio.codec == "opus" else None
                speech_boundary.reset()
                input_closed = False
                last_voice_at = time.monotonic()
                await runtime.submit_text("没事我先退下了，随时等你召唤。")
                await runtime.turn_done.wait()
                await output.json({"type": "device.standby"})

        idle_prompt_task = asyncio.create_task(idle_prompt_loop())

        async for message in websocket:
            if isinstance(message, bytes):
                binary_frames += 1
                if binary_frames == 1:
                    logger.info("[device] first audio session=%s bytes=%d", session_id[:8], len(message))
                pcm = message if decoder is None else decoder.decode(message)
                is_voice = speech_boundary.is_voice(pcm)
                # 同一轮 ASR 仍在上传音频时，提前生成产生的“未完成”不能被误当成新轮次。
                # 只有上一轮已闭合后再次检测到语音，才执行播报打断并重开 ASR。
                if input_closed and is_voice:
                    await interrupt_current_turn()
                await start_asr()
                if is_voice:
                    last_voice_at = time.monotonic()
                if input_closed:
                    if not is_voice:
                        continue
                await audio_queue.put(pcm)
                if speech_boundary.feed(pcm):
                    input_closed = True
                    await audio_queue.put(None)
                continue
            control = await protocol.parse(message)
            message_type = control["type"]
            if message_type == "ping":
                await websocket.send(json.dumps({"type": "pong"}, separators=(",", ":")))
            elif message_type == "listen" and control.get("state") == "stop":
                if not input_closed:
                    input_closed = True
                    await audio_queue.put(None)
            elif message_type == "abort":
                await interrupt_current_turn()
    finally:
        if idle_prompt_task is not None:
            idle_prompt_task.cancel()
            await asyncio.gather(idle_prompt_task, return_exceptions=True)
        if warmup_task is not None:
            warmup_task.cancel()
            await asyncio.gather(warmup_task, return_exceptions=True)
        cancel.set()
        if asr_task is not None:
            if not asr_task.done():
                asr_task.cancel()
            await asyncio.gather(asr_task, return_exceptions=True)
        if runtime is not None:
            await runtime.close()
        if http is not None:
            await http.aclose()


async def handle_ota_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request_line = await reader.readline()
        if not request_line:
            return
        method, path, _version = request_line.decode().split()
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in {b"\r\n", b"\n", b""}:
                break
            name, value = line.decode().split(":", 1)
            headers[name.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        if length:
            await reader.readexactly(length)
        if method != "POST" or path.split("?", 1)[0] != "/xiaozhi/ota/":
            body = b"not found\n"
            status = "404 Not Found"
        else:
            from .ota_server import build_ota_response

            try:
                body = json.dumps(build_ota_response(
                    device_id=headers.get("device-id", ""),
                    client_id=headers.get("client-id", ""),
                    user_agent=headers.get("user-agent", ""),
                    language=headers.get("accept-language", ""),
                ), ensure_ascii=False).encode()
                status = "200 OK"
            except ValueError as exc:
                body = str(exc).encode()
                status = "400 Bad Request"
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
        )
        await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, ValueError):
        logger.debug("OTA HTTP 请求解析失败", exc_info=True)
    finally:
        writer.close()
        await writer.wait_closed()


async def run_ota_server(host: str = "0.0.0.0", port: int = 18766) -> None:
    server = await asyncio.start_server(handle_ota_http, host, port)
    async with server:
        await server.serve_forever()


async def run_device_server(host: str = "0.0.0.0", port: int = 18765) -> None:
    from websockets.asyncio.server import serve

    async with serve(handle_device_connection, host, port, max_size=2 * 1024 * 1024):
        await asyncio.Future()


def main() -> None:
    load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.env")), override=False)

    async def run_all() -> None:
        await asyncio.gather(
        run_device_server(
            os.getenv("XIAOZHI_DEVICE_HOST", "0.0.0.0"),
            int(os.getenv("XIAOZHI_DEVICE_PORT", "18765")),
        ),
        run_ota_server(
            os.getenv("XIAOZHI_OTA_HOST", "0.0.0.0"),
            int(os.getenv("XIAOZHI_OTA_PORT", "18766")),
        ),
        )

    asyncio.run(run_all())


if __name__ == "__main__":
    main()
