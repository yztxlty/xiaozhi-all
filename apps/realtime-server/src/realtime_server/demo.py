"""
幽光 AI 全链路语音 Demo
全链路：H5 麦克风 PCM → WebSocket → ASR(DashScope) → Dify Chatflow → TTS(火山) → PCM → H5 扬声器

客户端 → 服务端消息：
  { type:"hello", version:1, transport:"websocket",
    audio_params:{format:"pcm",sample_rate:16000,channels:1,frame_duration:60} }
  binary：PCM 16kHz int16 音频帧（说话期间持续发送）
  { type:"audio_commit" }   — 本轮语音结束，触发 ASR→LLM→TTS
  { type:"text", text:"..." } — 文字直接输入（跳过 ASR）
  { type:"interrupt" }      — 打断当前 TTS 播放

服务端 → 客户端消息：
  { type:"hello", session_id:"..." }
  { type:"asr.partial", text:"..." }
  { type:"asr.final",   text:"..." }
  { type:"llm.text.delta", text:"..." }
  { type:"tts.audio", seq:N }  — 后随一帧二进制 PCM
  { type:"tts.done" }
  { type:"error", code:"..." }
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import ssl
import struct
import uuid
import wave
from http import HTTPStatus

import httpx
from dotenv import load_dotenv

# 加载同仓库根目录的 .env（相对于本文件向上 4 级）
_env_path = os.path.join(os.path.dirname(__file__), "../../../../.env")
load_dotenv(_env_path)

# 清除代理环境变量，避免 WebSocket 连接被代理拦截
for _proxy_var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_proxy_var, None)

from model_router.core.contracts import LLMRequest
from model_router.providers.dify_chatflow.client import DifyChatflowClient
from model_router.providers.dify_chatflow.config import DifyChatflowSettings
from model_router.providers.dify_chatflow.provider import DifyChatflowProvider
from speech_router.providers.dashscope_asr import DashScopeASRProvider
from speech_router.providers.volcengine_tts import VolcengineTTSProvider

logger = logging.getLogger("xiaozhi_demo")


def _dify_settings() -> DifyChatflowSettings | None:
    base_url = os.getenv("DIFY_CHATFLOW_BASE_URL")
    api_key = os.getenv("DIFY_CHATFLOW_API_KEY")
    if not base_url or not api_key:
        return None
    return DifyChatflowSettings(
        base_url=base_url,
        api_key=api_key,
        allow_insecure_http=os.getenv("DIFY_CHATFLOW_ALLOW_INSECURE_HTTP") == "1",
    )


_SENTENCE_SPLIT = re.compile(r"([。！？.!?，,；;：:\n]+)")


async def _pipeline(
    session_id: str,
    text: str,
    send_json,
    send_bytes,
    cancel: asyncio.Event,
) -> None:
    """流式 LLM → 按句拆分 → 逐句 TTS → 推送音频帧（首包低延迟）。"""
    logger.info("[pipeline] 开始 session=%s text=%r", session_id[:8], text[:40])
    try:
        settings = _dify_settings()
        tts = VolcengineTTSProvider()
        seq = 0
        buf = ""
        sentence_idx = 0

        async def _synthesize_sentence(sentence: str) -> None:
            nonlocal seq, sentence_idx
            sentence = sentence.strip()
            if not sentence:
                return
            sentence_idx += 1
            logger.info("[pipeline] TTS 合成第 %d 句: %r", sentence_idx, sentence[:40])
            chunk_count = 0
            chunk_bytes = 0
            async for chunk in tts.synthesize(sentence, cancel):
                if cancel.is_set():
                    logger.info("[pipeline] cancel_event，中止 TTS 推送")
                    return
                seq += 1
                chunk_count += 1
                chunk_bytes += len(chunk.payload)
                logger.debug("[pipeline] tts.audio seq=%d size=%d hex[:8]=%s",
                             seq, len(chunk.payload), chunk.payload[:8].hex())
                await send_json({"type": "tts.audio", "seq": seq})
                await send_bytes(chunk.payload)
            logger.info("[pipeline] 第 %d 句 TTS 完成: %d 帧 %d bytes", sentence_idx, chunk_count, chunk_bytes)

        if not settings:
            reply = "服务端尚未配置 Dify，收到：" + text
            logger.warning("[pipeline] Dify 未配置，使用 fallback")
            await send_json({"type": "llm.text.delta", "text": reply})
            await _synthesize_sentence(reply)
        else:
            request = LLMRequest(
                session_id=session_id,
                turn_id=f"t_{uuid.uuid4().hex}",
                generation_id=f"g_{uuid.uuid4().hex}",
                user_id="h5-demo",
                user_text=text,
                role_profile={"name": "幽光", "persona": "温柔可爱的 AI 陪伴"},
            )
            llm_tokens = 0
            async with httpx.AsyncClient(trust_env=False) as http:
                provider = DifyChatflowProvider(settings, DifyChatflowClient(settings, http))
                async for event in provider.stream(request, asyncio.Event()):
                    if cancel.is_set():
                        logger.info("[pipeline] cancel_event，中止 LLM 流")
                        return
                    if event.type == "llm.text.delta":
                        llm_tokens += 1
                        logger.debug("[pipeline] LLM delta #%d: %r", llm_tokens, event.text)
                        await send_json({"type": "llm.text.delta", "text": event.text})
                        buf += event.text
                        parts = _SENTENCE_SPLIT.split(buf)
                        while len(parts) >= 3:
                            sentence = parts[0] + parts[1]
                            buf = "".join(parts[2:])
                            parts = _SENTENCE_SPLIT.split(buf)
                            if sentence.strip():
                                await _synthesize_sentence(sentence)
                                if cancel.is_set():
                                    return
                    elif event.type == "llm.failed":
                        logger.error("[pipeline] LLM 失败: %s", event.code)
                        await send_json({"type": "error", "code": f"LLM_FAILED: {event.code}"})
                        return
            logger.info("[pipeline] LLM 流完毕，共 %d tokens，buf 剩余: %r", llm_tokens, buf[:30])

            # 剩余尾巴
            if buf.strip() and not cancel.is_set():
                await _synthesize_sentence(buf)

        if not cancel.is_set():
            logger.info("[pipeline] tts.done seq=%d", seq)
            await send_json({"type": "tts.done"})
    except asyncio.CancelledError:
        logger.info("[pipeline] CancelledError session=%s", session_id[:8])
    except Exception as exc:
        logger.exception("[pipeline] 异常 session=%s", session_id[:8])
        await send_json({"type": "error", "code": f"PIPELINE_ERROR: {exc}"})


async def _asr_then_pipeline(
    session_id: str,
    audio_queue: asyncio.Queue,
    send_json,
    send_bytes,
    cancel: asyncio.Event,
) -> None:
    logger.info("[asr_pipeline] 开始 session=%s", session_id[:8])
    dump_mic = os.getenv("DEBUG_DUMP_MIC") == "1"
    mic_bytes = bytearray()

    async def _audio_gen():
        chunk_count = 0
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                logger.info("[asr_pipeline] 音频队列结束，共 %d 帧", chunk_count)
                return
            chunk_count += 1
            if dump_mic:
                mic_bytes.extend(chunk)
            yield chunk

    def _report_mic() -> None:
        if not mic_bytes:
            logger.warning("[asr_pipeline] 麦克风数据为空")
            return
        n = len(mic_bytes) // 2
        samples = struct.unpack(f"{n}h", bytes(mic_bytes[: n * 2]))
        peak = max(abs(s) for s in samples)
        rms = (sum(s * s for s in samples) / n) ** 0.5
        zc = sum(1 for i in range(1, n) if (samples[i - 1] < 0) != (samples[i] < 0))
        logger.info(
            "[asr_pipeline] 麦克风 %d bytes = %.0fms peak=%d rms=%.0f zcr=%.3f",
            len(mic_bytes), n / 16000 * 1000, peak, rms, zc / n,
        )
        if dump_mic:
            with wave.open("/tmp/mic_dump.wav", "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(bytes(mic_bytes))
            logger.info("[asr_pipeline] 麦克风音频已写入 /tmp/mic_dump.wav")

    try:
        asr = DashScopeASRProvider()
        final_text = ""
        async for event in asr.recognize(_audio_gen(), cancel):
            if cancel.is_set():
                logger.info("[asr_pipeline] cancel_event，中止")
                return
            if type(event).__name__ == "ASRFinal":
                final_text = event.text
                logger.info("[asr_pipeline] ASRFinal: %r", final_text)
                await send_json({"type": "asr.final", "text": final_text})
            else:
                logger.debug("[asr_pipeline] ASRPartial: %r", event.text)
                await send_json({"type": "asr.partial", "text": event.text})

        _report_mic()

        if final_text and not cancel.is_set():
            logger.info("[asr_pipeline] 触发 pipeline text=%r", final_text)
            await _pipeline(session_id, final_text, send_json, send_bytes, cancel)
        elif not cancel.is_set():
            # 没识别到内容也要通知客户端，否则 UI 卡在“识别中...”
            logger.info("[asr_pipeline] 无识别结果，回发 asr.empty")
            await send_json({"type": "asr.empty"})
    except asyncio.CancelledError:
        logger.info("[asr_pipeline] CancelledError")
    except Exception as exc:
        logger.exception("[asr_pipeline] 异常 session=%s", session_id[:8])
        await send_json({"type": "error", "code": f"ASR_FAILED: {exc}"})


async def handler(websocket) -> None:
    session_id = f"s_{uuid.uuid4().hex}"
    audio_queue: asyncio.Queue = asyncio.Queue()
    cancel = asyncio.Event()
    pipeline_task: asyncio.Task | None = None
    asr_stream_open = False  # 本轮音频流是否已开启（与 pipeline_task 是否结束无关）
    logger.info("[handler] 新连接 session=%s remote=%s", session_id[:8], getattr(websocket, 'remote_address', '?'))

    async def send_json(obj: dict) -> None:
        try:
            await websocket.send(json.dumps(obj, ensure_ascii=False))
        except Exception as e:
            logger.debug("[handler] send_json 失败: %s", e)

    async def send_bytes(data: bytes) -> None:
        try:
            logger.debug("[handler] send_bytes %d bytes hex[:8]=%s", len(data), data[:8].hex())
            await websocket.send(data)
        except Exception as e:
            logger.debug("[handler] send_bytes 失败: %s", e)

    # ── 握手 ─────────────────────────────────────────────────
    first = await websocket.recv()
    if not isinstance(first, str):
        await websocket.close(code=1002, reason="首帧必须是 hello JSON")
        return
    try:
        hello = json.loads(first)
        if hello.get("type") != "hello":
            await websocket.close(code=1002, reason="首帧不是 hello")
            return
    except json.JSONDecodeError:
        await websocket.close(code=1002, reason="首帧不是 JSON")
        return

    await send_json({
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "session_id": session_id,
        "audio_params": {"format": "pcm", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
    })
    logger.info("客户端握手完成 session_id=%s", session_id)

    # ── 消息循环 ──────────────────────────────────────────────
    binary_frames = 0
    async for message in websocket:
        if isinstance(message, bytes):
            # 本轮第一帧：立即建立 ASR 会话，边说边传（真流式）
            if not asr_stream_open:
                # 用户开口即打断上一轮还在播的回复
                if pipeline_task and not pipeline_task.done():
                    logger.info("[handler] 新一轮说话，打断上一轮 pipeline")
                    cancel.set()
                    pipeline_task.cancel()
                    try:
                        await pipeline_task
                    except (asyncio.CancelledError, Exception):
                        pass
                cancel.clear()
                audio_queue = asyncio.Queue()
                asr_stream_open = True
                binary_frames = 0
                logger.info("[handler] 首帧音频到达，启动流式 ASR session=%s", session_id[:8])
                pipeline_task = asyncio.create_task(
                    _asr_then_pipeline(session_id, audio_queue, send_json, send_bytes, cancel)
                )
            binary_frames += 1
            if binary_frames % 50 == 1:
                logger.debug("[handler] 收到音频帧 #%d size=%d", binary_frames, len(message))
            await audio_queue.put(message)
            continue

        try:
            ctrl = json.loads(message)
        except json.JSONDecodeError:
            await websocket.close(code=1007, reason="控制消息不是 JSON")
            return

        msg_type = ctrl.get("type")
        logger.info("[handler] 收到控制消息 type=%s session=%s", msg_type, session_id[:8])

        if msg_type == "ping":
            await send_json({"type": "pong"})

        elif msg_type == "audio_commit":
            logger.info("[handler] audio_commit：本轮 %d 帧音频结束，关闭 ASR 输入流", binary_frames)
            binary_frames = 0
            if asr_stream_open:
                # 只关闭输入流，让已在流式识别的任务收尾；不要另起 ASR 会话
                await audio_queue.put(None)
                asr_stream_open = False
            else:
                logger.info("[handler] 本轮没有收到音频帧，回发 asr.empty")
                await send_json({"type": "asr.empty"})

        elif msg_type == "text":
            text = str(ctrl.get("text", "")).strip()
            if not text:
                continue
            logger.info("[handler] text 输入: %r", text[:40])
            cancel.clear()
            if pipeline_task and not pipeline_task.done():
                cancel.set()
                pipeline_task.cancel()
                try:
                    await pipeline_task
                except (asyncio.CancelledError, Exception):
                    pass
                cancel.clear()
            pipeline_task = asyncio.create_task(
                _pipeline(session_id, text, send_json, send_bytes, cancel)
            )

        elif msg_type == "interrupt":
            logger.info("[handler] interrupt 收到，取消 pipeline")
            cancel.set()
            asr_stream_open = False
            if pipeline_task and not pipeline_task.done():
                pipeline_task.cancel()
            await send_json({"type": "tts.done"})


_H5_INDEX = os.path.join(os.path.dirname(__file__), "../../../h5-demo/index.html")
_CERT_DIR = os.path.join(os.path.dirname(__file__), "../../../../certs")


async def _serve_static(connection, request):
    """非 WebSocket 的普通 GET 直接返回 H5 页面，让页面与 WSS 同源同端口。"""
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None  # 交给 WebSocket 处理

    path = request.path.split("?")[0]
    if path not in ("/", "/index.html"):
        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")

    try:
        with open(os.path.abspath(_H5_INDEX), encoding="utf-8") as f:
            html = f.read()
    except OSError as exc:
        logger.error("读取 H5 页面失败: %s", exc)
        return connection.respond(HTTPStatus.INTERNAL_SERVER_ERROR, "h5 not found\n")

    # 必须把正文交给 respond()，它据此计算 Content-Length
    response = connection.respond(HTTPStatus.OK, html)
    response.headers.pop("Content-Type", None)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Cache-Control"] = "no-store"
    return response


def _build_ssl_context() -> "ssl.SSLContext | None":
    crt = os.path.join(_CERT_DIR, "dev.crt")
    key = os.path.join(_CERT_DIR, "dev.key")
    if not (os.path.exists(crt) and os.path.exists(key)):
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    return ctx


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


async def run(host: str = "0.0.0.0", port: int = 18765) -> None:
    from websockets.asyncio.server import serve

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    ssl_ctx = _build_ssl_context()
    scheme = "https" if ssl_ctx else "http"
    ip = _lan_ip()

    kwargs = {"max_size": 2 * 1024 * 1024, "process_request": _serve_static}
    if ssl_ctx:
        kwargs["ssl"] = ssl_ctx

    async with serve(handler, host, port, **kwargs):
        print(f"幽光 AI 全链路服务已启动（页面与 WebSocket 同端口）", flush=True)
        print(f"  本机  : {scheme}://127.0.0.1:{port}", flush=True)
        print(f"  手机  : {scheme}://{ip}:{port}", flush=True)
        if not ssl_ctx:
            print("  ⚠️  未启用 TLS，手机端无法使用麦克风（navigator.mediaDevices 不可用）", flush=True)
        print("  ASR : DashScope paraformer-realtime-v2", flush=True)
        print("  LLM : Dify Chatflow", flush=True)
        print("  TTS : 火山引擎 BigTTS v3 双向流 (PCM 16kHz)", flush=True)
        await asyncio.Future()


def main() -> None:
    asyncio.run(run())
