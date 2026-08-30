"""
火山引擎双向流 TTS Provider（BigTTS v3 协议）
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import ssl
import struct
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import websockets

from speech_router.core.tts_contracts import TTSAudioChunk, TTSProvider

logger = logging.getLogger("tts.volcengine")

# ── 协议常量 ──────────────────────────────────────────────
PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001

FULL_CLIENT_REQUEST = 0b0001
AUDIO_ONLY_RESPONSE = 0b1011
FULL_SERVER_RESPONSE = 0b1001
ERROR_INFORMATION = 0b1111

MsgTypeFlagNoSeq = 0b0000
MsgTypeFlagPositiveSeq = 0b0001
MsgTypeFlagLastNoSeq = 0b0010
MsgTypeFlagNegativeSeq = 0b0011
MsgTypeFlagWithEvent = 0b0100

NO_SERIALIZATION = 0b0000
JSON_SERIAL = 0b0001
COMPRESSION_NO = 0b0000

EVENT_NONE = 0
EVENT_Start_Connection = 1
EVENT_FinishConnection = 2
EVENT_ConnectionStarted = 50
EVENT_ConnectionFailed = 51
EVENT_ConnectionFinished = 52
EVENT_StartSession = 100
EVENT_CancelSession = 101
EVENT_FinishSession = 102
EVENT_SessionStarted = 150
EVENT_SessionCanceled = 151
EVENT_SessionFinished = 152
EVENT_SessionFailed = 153
EVENT_TaskRequest = 200
EVENT_TTSSentenceStart = 350
EVENT_TTSSentenceEnd = 351
EVENT_TTSResponse = 352

_EVENT_NAMES = {
    1: "Start_Connection", 2: "FinishConnection", 50: "ConnectionStarted",
    51: "ConnectionFailed", 52: "ConnectionFinished", 100: "StartSession",
    101: "CancelSession", 102: "FinishSession", 150: "SessionStarted",
    151: "SessionCanceled", 152: "SessionFinished", 153: "SessionFailed",
    200: "TaskRequest", 350: "TTSSentenceStart", 351: "TTSSentenceEnd", 352: "TTSResponse",
}

_TTS_CONNECTION_MAX_AGE_SECONDS = 45.0


def _header(msg_type: int, flag: int, serial: int = NO_SERIALIZATION) -> bytes:
    return bytes([
        (PROTOCOL_VERSION << 4) | DEFAULT_HEADER_SIZE,
        (msg_type << 4) | flag,
        (serial << 4) | COMPRESSION_NO,
        0,
    ])


def _optional(event: int = EVENT_NONE, session_id: str | None = None) -> bytes:
    buf = bytearray()
    buf.extend(event.to_bytes(4, "big", signed=True))
    if session_id is not None:
        b = session_id.encode()
        buf.extend(len(b).to_bytes(4, "big", signed=True))
        buf.extend(b)
    return bytes(buf)


async def _send(ws, header: bytes, optional: bytes | None = None, payload: bytes | None = None) -> None:
    frame = bytearray(header)
    if optional:
        frame.extend(optional)
    if payload:
        frame.extend(len(payload).to_bytes(4, "big", signed=True))
        frame.extend(payload)
    await ws.send(bytes(frame))


def _parse(res: bytes) -> tuple[int, str | None, bytes | None]:
    """返回 (event, session_id, audio_payload)；保留会话标识以隔离残余下行。"""
    if len(res) < 4:
        logger.warning("[TTS _parse] 帧太短: %d bytes hex=%s", len(res), res.hex())
        return EVENT_NONE, None, None

    num = 0b00001111
    msg_type = (res[1] >> 4) & num
    flag = res[1] & 0x0F
    serial = (res[2] >> 4) & num
    offset = 4

    logger.debug("[TTS _parse] 原始帧 %d bytes | header=%s | msg_type=%d flag=%d serial=%d",
                 len(res), res[:4].hex(), msg_type, flag, serial)

    if msg_type in (FULL_SERVER_RESPONSE, AUDIO_ONLY_RESPONSE):
        if flag == MsgTypeFlagWithEvent:
            event = int.from_bytes(res[offset:offset + 4], "big")
            offset += 4
            event_name = _EVENT_NAMES.get(event, str(event))
            logger.debug("[TTS _parse] event=%d(%s) msg_type=%d remaining=%d bytes",
                         event, event_name, msg_type, len(res) - offset)

            if event == EVENT_ConnectionStarted:
                sz = int.from_bytes(res[offset:offset + 4], "big")
                offset += 4 + sz
                return event, None, None

            if event in (
                EVENT_SessionStarted,
                EVENT_SessionCanceled,
                EVENT_SessionFailed,
                EVENT_SessionFinished,
            ):
                sid_sz = int.from_bytes(res[offset:offset + 4], "big")
                offset += 4
                session_id = res[offset:offset + sid_sz].decode("utf-8", errors="replace")
                offset += sid_sz
                message_sz = int.from_bytes(res[offset:offset + 4], "big")
                offset += 4 + message_sz
                return event, session_id, None

            if event == EVENT_ConnectionFailed:
                sz = int.from_bytes(res[offset:offset + 4], "big")
                offset += 4 + sz
                return event, None, None

            if event in (EVENT_TTSResponse, EVENT_TTSSentenceStart, EVENT_TTSSentenceEnd):
                # 先跳过 sessionId 字段（4字节长度前缀 + 内容）
                sid_sz = int.from_bytes(res[offset:offset + 4], "big")
                offset += 4
                session_id = res[offset:offset + sid_sz].decode("utf-8", errors="replace")
                offset += sid_sz
                logger.debug("[TTS _parse] event=%s sessionId=%s", event_name, session_id[:8])

                sz = int.from_bytes(res[offset:offset + 4], "big")
                offset += 4
                payload = res[offset:offset + sz]
                logger.debug("[TTS _parse] event=%s payload_size=%d msg_type=%d is_audio=%s total_frame=%d",
                             event_name, sz, msg_type, msg_type == AUDIO_ONLY_RESPONSE, len(res))
                if event == EVENT_TTSResponse and msg_type == AUDIO_ONLY_RESPONSE:
                    # payload 已是裸 PCM 二进制，不能再做 base64 解码
                    if len(payload) >= 2:
                        logger.debug("[TTS _parse] PCM %d bytes int16[:4]=%s", len(payload),
                                     struct.unpack(f'{min(4,len(payload)//2)}h', payload[:min(8,len(payload))]))
                    return event, session_id, payload
                return event, session_id, None

            return event, None, None

        else:
            logger.warning("[TTS _parse] 未知 flag=%d msg_type=%d hex[:8]=%s", flag, msg_type, res[:8].hex())

    elif msg_type == ERROR_INFORMATION:
        code = int.from_bytes(res[offset:offset + 4], "big", signed=True)
        logger.error("[TTS _parse] 服务端错误 code=%d", code)
        raise RuntimeError(f"TTS 服务端错误 code={code}")
    else:
        logger.warning("[TTS _parse] 未知 msg_type=%d hex[:8]=%s", msg_type, res[:8].hex())

    return EVENT_NONE, None, None


def _payload_json(event: int, params: dict, text: str = "") -> bytes:
    return json.dumps({
        "user": {"uid": params.get("uid", "xiaozhi-h5")},
        "event": event,
        "namespace": "BidirectionalTTS",
        "req_params": {
            "text": text,
            "speaker": params.get("speaker", "zh_female_meilinvyou_emo_v2_mars_bigtts"),
            "audio_params": {
                "format": params.get("format", "pcm"),
                "sample_rate": params.get("sample_rate", 16000),
                "speech_rate": params.get("speech_rate", 0),
                "loudness_rate": params.get("loudness_rate", 0),
            },
        },
    }, ensure_ascii=False).encode()


class VolcengineTTSProvider:
    """火山引擎双向流 TTS，符合 TTSProvider Protocol。"""

    provider_id = "volcengine-bigtts"

    def __init__(self, params: dict | None = None) -> None:
        self._params = {
            "url": os.environ.get("VOLC_TTS_URL", "wss://openspeech.bytedance.com/api/v3/tts/bidirection"),
            "app_id": os.environ.get("VOLC_TTS_APP_ID", ""),
            "access_key": os.environ.get("VOLC_TTS_ACCESS_KEY", ""),
            "resource_id": os.environ.get("VOLC_TTS_RESOURCE_ID", "volc.service_type.10029"),
            "speaker": os.environ.get("VOLC_TTS_SPEAKER", "zh_female_meilinvyou_emo_v2_mars_bigtts"),
            "format": os.environ.get("VOLC_TTS_FORMAT", "pcm"),
            "sample_rate": int(os.environ.get("VOLC_TTS_SAMPLE_RATE", "16000")),
        }
        if params:
            self._params.update(params)
        self._ws = None
        self._receiver_task: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._event_waiters: dict[tuple[int, str | None], list[asyncio.Future]] = {}
        self._audio_queue: asyncio.Queue = asyncio.Queue()
        self._audio_sink: Callable[[bytes], Awaitable[None]] | None = None
        self._active_session_id: str | None = None
        self._connection_started_at: float | None = None
        self._audio_packets = 0

    def set_audio_sink(self, sink: Callable[[bytes], Awaitable[None]] | None) -> None:
        """设置实时音频下游；Pipecat 用它直接接收双流 TTS 音频。"""
        self._audio_sink = sink

    async def warmup(self) -> None:
        """提前建立持久连接，避免首轮语音承担握手耗时。"""
        await self._ensure_connection()

    def _connect_kwargs(self) -> dict:
        p = self._params
        ws_header = {
            "X-Api-App-Key": p["app_id"],
            "X-Api-Access-Key": p["access_key"],
            "X-Api-Resource-Id": p["resource_id"],
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        header_kwarg = (
            "additional_headers"
            if "additional_headers" in inspect.signature(websockets.connect).parameters
            else "extra_headers"
        )
        return {
            header_kwarg: ws_header,
            "max_size": 10_000_000,
            "ssl": ssl_ctx,
            "ping_interval": 20,
            "ping_timeout": 10,
        }

    async def _ensure_connection(self) -> None:
        async with self._connect_lock:
            if (
                self._ws is not None
                and self._receiver_task is not None
                and not self._receiver_task.done()
                and not self._connection_needs_rotation()
            ):
                return
            await self._close_socket()
            self._ws = await websockets.connect(self._params["url"], **self._connect_kwargs())
            await _send(
                self._ws,
                _header(FULL_CLIENT_REQUEST, MsgTypeFlagWithEvent),
                _optional(EVENT_Start_Connection),
                b"{}",
            )
            raw = await asyncio.wait_for(self._ws.recv(), timeout=3)
            event, _, _ = _parse(raw)
            if event != EVENT_ConnectionStarted:
                await self._close_socket()
                raise RuntimeError(f"TTS: 建连失败 event={event}")
            self._receiver_task = asyncio.create_task(self._receive_loop())
            self._connection_started_at = time.monotonic()
            logger.info("[TTS] 持久 WebSocket 已建立")

    def _connection_needs_rotation(self, now: float | None = None) -> bool:
        started_at = self._connection_started_at
        return started_at is not None and (time.monotonic() if now is None else now) - started_at >= _TTS_CONNECTION_MAX_AGE_SECONDS

    def _wait_for_event(self, event: int, session_id: str | None = None) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        self._event_waiters.setdefault((event, session_id), []).append(future)
        return future

    def _resolve_event(self, event: int, session_id: str | None = None) -> None:
        key = (event, session_id)
        waiters = self._event_waiters.get(key)
        if not waiters:
            return
        future = waiters.pop(0)
        if not waiters:
            self._event_waiters.pop(key, None)
        if not future.done():
            future.set_result(None)

    async def _receive_loop(self) -> None:
        try:
            while self._ws is not None:
                raw = await self._ws.recv()
                event, session_id, audio = _parse(raw)
                is_active = session_id is None or session_id == self._active_session_id
                if event == EVENT_TTSResponse and audio and is_active:
                    self._audio_packets += 1
                    if self._audio_packets == 1:
                        logger.info("[TTS] 首个音频包 session_id=%s bytes=%d", (session_id or "")[:8], len(audio))
                    if self._audio_sink is not None:
                        await self._audio_sink(audio)
                    else:
                        await self._audio_queue.put(audio)
                elif event == EVENT_TTSSentenceEnd and self._audio_sink is None and is_active:
                    await self._audio_queue.put(None)
                elif (
                    event in (EVENT_SessionCanceled, EVENT_SessionFinished, EVENT_SessionFailed)
                    and session_id == self._active_session_id
                ):
                    logger.info("[TTS] 会话结束 event=%s session_id=%s audio_packets=%d", event, (session_id or "")[:8], self._audio_packets)
                    self._active_session_id = None
                self._resolve_event(event, session_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[TTS] 持久连接接收失败: %s", exc)
            await self._audio_queue.put(exc)
            for waiters in self._event_waiters.values():
                for future in waiters:
                    if not future.done():
                        future.set_exception(exc)
            self._event_waiters.clear()
        finally:
            self._receiver_task = None

    async def start_session(self, session_id: str) -> None:
        if self._active_session_id == session_id:
            return
        if self._active_session_id is not None:
            await self.cancel_session(self._active_session_id)
        await self._ensure_connection()
        self._audio_queue = asyncio.Queue()
        self._active_session_id = session_id
        self._audio_packets = 0
        started = self._wait_for_event(EVENT_SessionStarted, session_id)
        async with self._send_lock:
            await _send(
                self._ws,
                _header(FULL_CLIENT_REQUEST, MsgTypeFlagWithEvent, JSON_SERIAL),
                _optional(EVENT_StartSession, session_id),
                _payload_json(EVENT_StartSession, self._params),
            )
        await asyncio.wait_for(started, timeout=3)
        logger.info("[TTS] 会话已预热 session_id=%s", session_id[:8])

    async def send_text(self, session_id: str, text: str) -> None:
        """向当前双流会话追加文本，不等待对应音频返回。"""
        if self._active_session_id != session_id:
            await self.start_session(session_id)
        async with self._send_lock:
            await _send(
                self._ws,
                _header(FULL_CLIENT_REQUEST, MsgTypeFlagWithEvent, JSON_SERIAL),
                _optional(EVENT_TaskRequest, session_id),
                _payload_json(EVENT_TaskRequest, self._params, text),
            )

    async def stream_text(
        self,
        session_id: str,
        text: str,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[TTSAudioChunk]:
        await self.send_text(session_id, text)
        sequence = 0
        while True:
            if cancel_event.is_set():
                return
            item = await self._audio_queue.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            sequence += 1
            yield TTSAudioChunk(sequence=sequence, payload=item)

    async def finish_session(self, session_id: str) -> asyncio.Future | None:
        if self._active_session_id != session_id or self._ws is None:
            return None
        finished = self._wait_for_event(EVENT_SessionFinished, session_id)
        async with self._send_lock:
            await _send(
                self._ws,
                _header(FULL_CLIENT_REQUEST, MsgTypeFlagWithEvent, JSON_SERIAL),
                _optional(EVENT_FinishSession, session_id),
                b"{}",
            )
        # 沿用 xiaozhi-esp32-server：结束事件交给长期接收任务处理，
        # 这里不阻塞 Pipecat 当前轮次，也不因厂商漏发确认而污染后续轮次。
        return finished

    async def wait_session_finished(
        self,
        session_id: str,
        finished: asyncio.Future | None,
        timeout: float = 5,
    ) -> None:
        if finished is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(finished), timeout=timeout)
        except TimeoutError:
            finished.cancel()
            logger.warning(
                "[TTS] SessionFinished 等待超时，关闭当前连接并让下一轮重连 session_id=%s",
                session_id[:8],
            )
            if self._active_session_id == session_id:
                self._active_session_id = None
            await self._close_socket()

    async def cancel_session(self, session_id: str) -> None:
        if self._active_session_id != session_id or self._ws is None:
            return
        cancelled = self._wait_for_event(EVENT_SessionCanceled, session_id)
        async with self._send_lock:
            await _send(
                self._ws,
                _header(FULL_CLIENT_REQUEST, MsgTypeFlagWithEvent, JSON_SERIAL),
                _optional(EVENT_CancelSession, session_id),
                b"{}",
            )
        # Pipecat 可能紧接着启动替代轮次；必须等当前会话真正释放，
        # 否则同一连接上的 StartSession 会被厂商服务忽略。
        try:
            await asyncio.wait_for(asyncio.shield(cancelled), timeout=2)
        except TimeoutError:
            cancelled.cancel()
            logger.warning("[TTS] 取消确认等待超时，关闭连接后重连 session_id=%s", session_id[:8])
            await self._close_socket()
        self._active_session_id = None

    async def _close_socket(self) -> None:
        task = self._receiver_task
        self._receiver_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        ws = self._ws
        self._ws = None
        self._connection_started_at = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def close(self) -> None:
        if self._active_session_id is not None:
            await self.cancel_session(self._active_session_id)
        if self._ws is not None:
            try:
                async with self._send_lock:
                    await _send(
                        self._ws,
                        _header(FULL_CLIENT_REQUEST, MsgTypeFlagWithEvent, JSON_SERIAL),
                        _optional(EVENT_FinishConnection),
                        b"{}",
                    )
            except Exception:
                pass
        await self._close_socket()

    async def synthesize(
        self,
        text: str,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[TTSAudioChunk]:
        session_id = uuid.uuid4().hex
        await self.start_session(session_id)
        try:
            async for chunk in self.stream_text(session_id, text, cancel_event):
                yield chunk
            if cancel_event.is_set():
                await self.cancel_session(session_id)
            else:
                finished = await self.finish_session(session_id)
                await self.wait_session_finished(session_id, finished)
        except Exception:
            await self.cancel_session(session_id)
            raise
