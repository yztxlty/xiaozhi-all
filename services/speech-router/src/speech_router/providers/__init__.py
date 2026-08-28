"""
火山引擎双向流 TTS Provider（BigTTS v3 协议）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import struct
import uuid
from collections.abc import AsyncIterator

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
EVENT_StartSession = 100
EVENT_FinishSession = 102
EVENT_SessionStarted = 150
EVENT_SessionFinished = 152
EVENT_SessionFailed = 153
EVENT_TaskRequest = 200
EVENT_TTSSentenceStart = 350
EVENT_TTSSentenceEnd = 351
EVENT_TTSResponse = 352

_EVENT_NAMES = {
    1: "Start_Connection", 2: "FinishConnection", 50: "ConnectionStarted",
    51: "ConnectionFailed", 100: "StartSession", 102: "FinishSession",
    150: "SessionStarted", 152: "SessionFinished", 153: "SessionFailed",
    200: "TaskRequest", 350: "TTSSentenceStart", 351: "TTSSentenceEnd", 352: "TTSResponse",
}


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


def _parse(res: bytes) -> tuple[int, bytes | None]:
    """返回 (event, audio_payload|None)；audio_payload=None 表示非音频帧。"""
    if len(res) < 4:
        logger.warning("[TTS _parse] 帧太短: %d bytes hex=%s", len(res), res.hex())
        return EVENT_NONE, None

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
                return event, None

            if event in (EVENT_SessionStarted, EVENT_SessionFailed, EVENT_SessionFinished):
                for _ in range(2):
                    sz = int.from_bytes(res[offset:offset + 4], "big")
                    offset += 4 + sz
                return event, None

            if event == EVENT_ConnectionFailed:
                sz = int.from_bytes(res[offset:offset + 4], "big")
                offset += 4 + sz
                return event, None

            if event in (EVENT_TTSResponse, EVENT_TTSSentenceStart, EVENT_TTSSentenceEnd):
                # 先跳过 sessionId 字段（4字节长度前缀 + 内容）
                sid_sz = int.from_bytes(res[offset:offset + 4], "big")
                offset += 4 + sid_sz
                logger.debug("[TTS _parse] event=%s skipped sessionId size=%d", event_name, sid_sz)

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
                    return event, payload
                return event, None

            return event, None

        else:
            logger.warning("[TTS _parse] 未知 flag=%d msg_type=%d hex[:8]=%s", flag, msg_type, res[:8].hex())

    elif msg_type == ERROR_INFORMATION:
        code = int.from_bytes(res[offset:offset + 4], "big", signed=True)
        logger.error("[TTS _parse] 服务端错误 code=%d", code)
        raise RuntimeError(f"TTS 服务端错误 code={code}")
    else:
        logger.warning("[TTS _parse] 未知 msg_type=%d hex[:8]=%s", msg_type, res[:8].hex())

    return EVENT_NONE, None


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

    async def synthesize(
        self,
        text: str,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[TTSAudioChunk]:
        p = self._params
        logger.info("[TTS] synthesize text=%r format=%s sample_rate=%s speaker=%s",
                    text[:30], p.get("format"), p.get("sample_rate"), p.get("speaker", "")[:20])

        ws_header = {
            "X-Api-App-Key": p["app_id"],
            "X-Api-Access-Key": p["access_key"],
            "X-Api-Resource-Id": p["resource_id"],
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        import inspect
        header_kwarg = (
            "additional_headers"
            if "additional_headers" in inspect.signature(websockets.connect).parameters
            else "extra_headers"
        )
        connect_kwargs = {header_kwarg: ws_header, "max_size": 10_000_000, "ssl": ssl_ctx}

        session_id = uuid.uuid4().hex
        sequence = 0
        total_bytes = 0

        async with websockets.connect(p["url"], **connect_kwargs) as ws:
            logger.info("[TTS] WebSocket 已连接 session_id=%s", session_id[:8])

            # 1. StartConnection
            await _send(ws, _header(FULL_CLIENT_REQUEST, MsgTypeFlagWithEvent),
                        _optional(EVENT_Start_Connection), b"{}")
            raw = await ws.recv()
            event, _ = _parse(raw)
            logger.info("[TTS] StartConnection → event=%s(%d)", _EVENT_NAMES.get(event, "?"), event)
            if event != EVENT_ConnectionStarted:
                raise RuntimeError(f"TTS: 建连失败 event={event}")

            # 2. StartSession
            await _send(ws, _header(FULL_CLIENT_REQUEST, MsgTypeFlagWithEvent, JSON_SERIAL),
                        _optional(EVENT_StartSession, session_id), _payload_json(EVENT_StartSession, p))
            raw = await ws.recv()
            event, _ = _parse(raw)
            logger.info("[TTS] StartSession → event=%s(%d)", _EVENT_NAMES.get(event, "?"), event)
            if event != EVENT_SessionStarted:
                raise RuntimeError(f"TTS: 会话启动失败 event={event}")

            # 3. TaskRequest
            await _send(ws, _header(FULL_CLIENT_REQUEST, MsgTypeFlagWithEvent, JSON_SERIAL),
                        _optional(EVENT_TaskRequest, session_id), _payload_json(EVENT_TaskRequest, p, text))
            logger.info("[TTS] TaskRequest 已发送 text=%r", text[:30])

            # 4. FinishSession
            await _send(ws, _header(FULL_CLIENT_REQUEST, MsgTypeFlagWithEvent, JSON_SERIAL),
                        _optional(EVENT_FinishSession, session_id), b"{}")
            logger.info("[TTS] FinishSession 已发送，等待音频帧...")

            # 5. 接收音频帧
            while True:
                if cancel_event.is_set():
                    logger.info("[TTS] cancel_event 已设置，中止接收")
                    break
                raw = await ws.recv()
                event, audio = _parse(raw)
                if event == EVENT_TTSResponse and audio:
                    sequence += 1
                    total_bytes += len(audio)
                    logger.debug("[TTS] 音频帧 seq=%d size=%d total_bytes=%d hex[:8]=%s",
                                 sequence, len(audio), total_bytes, audio[:8].hex())
                    yield TTSAudioChunk(sequence=sequence, payload=audio)
                elif event in (EVENT_TTSSentenceStart, EVENT_TTSSentenceEnd):
                    logger.debug("[TTS] event=%s，继续", _EVENT_NAMES.get(event))
                    continue
                else:
                    logger.info("[TTS] 结束 event=%s(%d) total_frames=%d total_bytes=%d",
                                _EVENT_NAMES.get(event, "?"), event, sequence, total_bytes)
                    break

            # 6. FinishConnection
            await _send(ws, _header(FULL_CLIENT_REQUEST, MsgTypeFlagWithEvent, JSON_SERIAL),
                        _optional(EVENT_FinishConnection), b"{}")
            logger.info("[TTS] FinishConnection 已发送，连接关闭")
