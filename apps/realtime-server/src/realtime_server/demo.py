"""
幽光 AI 全链路语音 Demo
全链路：H5 麦克风 PCM → WebSocket → ASR(DashScope) → 可替换 LLM Provider → TTS(火山) → PCM → H5 扬声器

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
  { type:"assistant.emotion", emotion:"happy" }
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
from websockets.datastructures import Headers
from websockets.http11 import Response

os.environ.setdefault(
    "NLTK_DATA",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.runtime/nltk_data")),
)

from pipecat.processors.frame_processor import FrameProcessor
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.deepseek.llm import DeepSeekLLMService
from pipecat.services.qwen.llm import QwenLLMService

# 加载同仓库根目录的 .env（相对于本文件向上 4 级）
_env_path = os.path.join(os.path.dirname(__file__), "../../../../.env")

# 清除进程代理环境变量，Dify、ASR、TTS 都按项目原约定直连。
for _proxy_var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_proxy_var, None)

from model_router.core.contracts import LLMProvider, LLMRequest
from model_router.providers.dify_chatflow.client import DifyChatflowClient
from model_router.providers.dify_chatflow.config import DifyChatflowSettings
from model_router.providers.dify_chatflow.provider import DifyChatflowProvider
from model_router.providers.openai_compatible.client import OpenAICompatibleClient
from model_router.providers.openai_compatible.config import OpenAICompatibleSettings
from model_router.providers.openai_compatible.provider import OpenAICompatibleProvider
from speech_router.providers.dashscope_asr import DashScopeASRProvider
from speech_router.providers.volcengine_tts import VolcengineTTSProvider
from voice_session.infrastructure.pipecat.runtime import PipecatVoiceRuntime
from voice_session.infrastructure.pipecat.dify_llm import DifyPipecatLLM
from voice_session.infrastructure.pipecat.volcengine_tts import PipecatVolcengineTTSService
from voice_session.application.preemptive_generation import PreemptiveGenerationCoordinator

logger = logging.getLogger("xiaozhi_demo")


class _ASRTurnTaskRegistry:
    """管理 ASR 轮次，避免已提交轮次被下一轮首帧误取消。

    `audio_commit` 只关闭输入流，不代表 ASR→LLM→TTS 任务已经结束。
    已提交任务必须继续完成；只有仍在接收音频的任务才允许被新轮次取消。
    """

    def __init__(self) -> None:
        self.current: tuple[asyncio.Task, asyncio.Event] | None = None
        self.retired: set[asyncio.Task] = set()

    async def prepare_new_stream(self, stream_open: bool) -> None:
        current = self.current
        if current is None:
            return
        task, cancel = current
        if task.done():
            self.current = None
            return
        if not stream_open:
            # 已经收到 audio_commit：任务可能仍在等待 ASR 收尾或 TTS，不能取消。
            self.retired.add(task)
            self.current = None
            return
        cancel.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        self.current = None

    def start(self, task: asyncio.Task, cancel: asyncio.Event) -> None:
        self.current = (task, cancel)

    async def cancel_all(self) -> None:
        tasks = [task for task, _cancel in ([self.current] if self.current else [])]
        tasks.extend(self.retired)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.current = None
        self.retired.clear()


def _log_level() -> int:
    return getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)


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


def _dify_http_client_options() -> dict[str, object]:
    return {"trust_env": False}


def _openai_compatible_settings() -> OpenAICompatibleSettings | None:
    api_key = os.getenv("OPENAI_COMPATIBLE_API_KEY")
    if not api_key:
        return None
    return OpenAICompatibleSettings(
        base_url=os.getenv("OPENAI_COMPATIBLE_BASE_URL", "https://api.deepseek.com"),
        api_key=api_key,
        model=os.getenv("OPENAI_COMPATIBLE_MODEL", "deepseek-v4-flash"),
        thinking=os.getenv("OPENAI_COMPATIBLE_THINKING", "disabled"),
    )


def _build_llm_provider(http: httpx.AsyncClient) -> LLMProvider | None:
    provider_name = os.getenv("LLM_PROVIDER", "dify").strip().lower()
    if provider_name == "dify":
        settings = _dify_settings()
        if settings is None:
            return None
        return DifyChatflowProvider(settings, DifyChatflowClient(settings, http))
    if provider_name in {"deepseek", "openai_compatible", "openai-compatible"}:
        settings = _openai_compatible_settings()
        if settings is None:
            return None
        return OpenAICompatibleProvider(settings, OpenAICompatibleClient(settings, http))
    raise ValueError(f"不支持的 LLM_PROVIDER: {provider_name}")


def _build_pipecat_processors(
    _http: httpx.AsyncClient | None, *, device_mode: bool = False
) -> list[FrameProcessor]:
    """构造实时主链路；模型服务直接复用 Pipecat 官方实现。"""
    provider_name = os.getenv("LLM_PROVIDER", "dify").strip().lower()
    if provider_name == "dify":
        settings = _dify_settings()
        if settings is None:
            raise RuntimeError("DIFY_CHATFLOW_BASE_URL 或 DIFY_CHATFLOW_API_KEY 未配置")
        if _http is None:
            raise RuntimeError("Dify Pipecat 适配器需要共享 httpx.AsyncClient")
        provider = DifyChatflowProvider(settings, DifyChatflowClient(settings, _http))
        llm = DifyPipecatLLM(
            provider,
            fallback_text="网络有点慢，我再听你说一次。" if device_mode else None,
        )
    elif provider_name == "qwen":
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置")
        llm = QwenLLMService(
            api_key=api_key,
            base_url=os.getenv(
                "QWEN_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            settings=QwenLLMService.Settings(
                model=os.getenv("QWEN_MODEL", "qwen3.7-flash"),
                system_instruction=None,
                temperature=0.3,
                max_tokens=160,
                extra={"extra_body": {"enable_thinking": False}},
            ),
        )
    elif provider_name in {"deepseek", "openai_compatible", "openai-compatible"}:
        settings = _openai_compatible_settings()
        if settings is None:
            raise RuntimeError("OPENAI_COMPATIBLE_API_KEY 未配置")
        base_url = str(settings.base_url).rstrip("/")
        if base_url == "https://api.deepseek.com":
            base_url += "/v1"
        llm_settings = DeepSeekLLMService.Settings(
            model=settings.model,
            system_instruction=None,
            extra={"extra_body": {"thinking": {"type": settings.thinking}}},
        )
        llm = DeepSeekLLMService(
            api_key=settings.api_key.get_secret_value(),
            base_url=base_url,
            settings=llm_settings,
        )
    else:
        raise RuntimeError(f"Pipecat 实时主链路暂不支持 LLM_PROVIDER={provider_name}")

    # 火山双流 TTS 的既有 PCM 契约保持不变；设备输出适配器负责协议要求的
    # 16kHz PCM -> 24kHz Opus，避免改变已验证的设备音色。H5 同样保持 16kHz。
    tts = PipecatVolcengineTTSService(provider=VolcengineTTSProvider())
    return [llm, tts]


async def _warmup_pipecat_processors(processors: list[FrameProcessor]) -> None:
    """使用 Pipecat Service 的公开接口预热模型连接池。"""
    llm = processors[0]
    tts = processors[1] if len(processors) > 1 else None

    async def warmup_llm() -> None:
        if not isinstance(llm, QwenLLMService):
            return
        try:
            await llm.run_inference(
                LLMContext([{"role": "user", "content": "你好"}]),
                max_tokens=1,
                system_instruction="只回答一个字。",
            )
            logger.info("[warmup] 阿里云通义千问连接池预热完成")
        except Exception:
            logger.warning("[warmup] 阿里云通义千问预热失败，首轮将正常重试", exc_info=True)

    async def warmup_tts() -> None:
        if tts is None or not hasattr(tts, "warmup"):
            return
        try:
            await tts.warmup()
            logger.info("[warmup] 火山引擎 BigTTS v3 连接预热完成")
        except Exception:
            logger.warning("[warmup] 火山引擎 BigTTS v3 预热失败，首轮将正常重试", exc_info=True)

    await asyncio.gather(warmup_llm(), warmup_tts())


_SENTENCE_SPLIT = re.compile(r"([。！？.!?，,；;：:\n]+)")
_EMOTION_RULES = (
    ("sad", ("难过", "伤心", "失落", "孤独", "委屈")),
    ("crying", ("大哭", "哭泣", "眼泪", "泪流", "放声哭")),
    ("happy", ("开心", "高兴", "太棒", "恭喜", "喜欢", "谢谢", "笑一个", "笑笑", "笑一下", "大笑")),
    ("shy", ("害羞", "不好意思", "脸红", "羞涩")),
    ("surprised", ("真的吗", "居然", "没想到", "惊讶", "震惊")),
)


def extract_emotion(text: str) -> str:
    """将本轮语义映射为跨端稳定枚举；异常输入永远回退 neutral。"""
    value = str(text or "")[:500]
    if any(char in value for char in "<>\x00"):
        return "neutral"
    for emotion, keywords in _EMOTION_RULES:
        if any(keyword in value for keyword in keywords):
            return emotion
    return "neutral"


async def _pipeline(
    session_id: str,
    text: str,
    send_json,
    send_bytes,
    cancel: asyncio.Event,
) -> None:
    """流式 LLM → 按句拆分 → 逐句 TTS → 推送音频帧（首包低延迟）。"""
    logger.info("[pipeline] 开始 session=%s text=%r", session_id[:8], text[:40])
    tts_task: asyncio.Task | None = None
    try:
        tts = VolcengineTTSProvider()
        sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
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

        async def _tts_worker() -> None:
            while True:
                sentence = await sentence_queue.get()
                if sentence is None:
                    return
                await _synthesize_sentence(sentence)
                if cancel.is_set():
                    return

        tts_task = asyncio.create_task(_tts_worker())

        request = LLMRequest(
            session_id=session_id,
            turn_id=f"t_{uuid.uuid4().hex}",
            generation_id=f"g_{uuid.uuid4().hex}",
            user_id="h5-demo",
            user_text=text,
            role_profile={"name": "幽光", "persona": "温柔可爱的 AI 陪伴"},
        )
        llm_tokens = 0
        await send_json({"type": "assistant.emotion", "emotion": extract_emotion(text)})
        async with httpx.AsyncClient(**_dify_http_client_options()) as http:
            provider = _build_llm_provider(http)
            if provider is None:
                logger.error("[pipeline] 选中的 LLM Provider 未配置")
                await send_json({"type": "error", "code": "LLM_PROVIDER_NOT_CONFIGURED"})
                return
            async for event in provider.stream(request, cancel):
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
                            await sentence_queue.put(sentence)
                elif event.type == "llm.failed":
                    logger.error("[pipeline] LLM 失败: %s", event.code)
                    await send_json({"type": "error", "code": f"LLM_FAILED: {event.code}"})
                    return
        logger.info("[pipeline] LLM 流完毕，共 %d tokens，buf 剩余: %r", llm_tokens, buf[:30])

        if buf.strip() and not cancel.is_set():
            await sentence_queue.put(buf)

        if not cancel.is_set():
            await sentence_queue.put(None)
            await tts_task
            logger.info("[pipeline] tts.done seq=%d", seq)
            await send_json({"type": "tts.done"})
    except asyncio.CancelledError:
        logger.info("[pipeline] CancelledError session=%s", session_id[:8])
    except Exception as exc:
        logger.exception("[pipeline] 异常 session=%s", session_id[:8])
        await send_json({"type": "error", "code": f"PIPELINE_ERROR: {exc}"})
    finally:
        if tts_task is not None and not tts_task.done():
            tts_task.cancel()
            try:
                await tts_task
            except asyncio.CancelledError:
                pass


async def _asr_then_pipeline(
    session_id: str,
    audio_queue: asyncio.Queue,
    send_json,
    runtime: PipecatVoiceRuntime,
    cancel: asyncio.Event,
    *,
    auto_commit_on_final: bool = False,
    preemptive_delay_seconds: float | None = None,
) -> None:
    logger.info("[asr_pipeline] 开始 session=%s", session_id[:8])
    dump_mic = os.getenv("DEBUG_DUMP_MIC") == "1"
    mic_bytes = bytearray()
    audio_committed = asyncio.Event()
    # VAD 已确认用户结束说话后，给云端最终识别一个很短的收尾窗口；
    # 若最终结果仍未到达，再复用 LiveKit Agents 的预生成/取消语义。
    preemptive = PreemptiveGenerationCoordinator(
        runtime,
        stability_delay_seconds=(
            float(os.getenv("ASR_FINAL_GRACE_SECONDS", "0.28"))
            if preemptive_delay_seconds is None
            else preemptive_delay_seconds
        ),
    )

    async def _audio_gen():
        chunk_count = 0
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                audio_committed.set()
                # 短部分结果先不抢跑，等待 ASRFinal；足够长的部分结果仍可立即生成。
                submitted = "" if cancel.is_set() else await preemptive.commit(force_short=False)
                if submitted:
                    logger.info("[asr_pipeline] 松手后使用最新识别文字提前生成: %r", submitted)
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
                if auto_commit_on_final and not audio_committed.is_set():
                    # ESP32 realtime mode can keep sending audio after the
                    # recognizer has emitted a sentence-final result. Close
                    # this turn so the provider sends finish-task; the next
                    # device turn gets a fresh queue in device_server.
                    await audio_queue.put(None)
                had_preemptive_result = preemptive.submitted
                await send_json({"type": "assistant.emotion", "emotion": extract_emotion(final_text)})
                await preemptive.finalize(final_text)
                if auto_commit_on_final and not preemptive.submitted:
                    # 设备端 ASR 可能先给出最终句子、再迟迟不结束云端流；
                    # 不能让设备等待 ASR 清理完成才进入 LLM/TTS。
                    await preemptive.commit()
                if had_preemptive_result:
                    logger.info("[asr_pipeline] 最终识别已校验提前生成")
                elif audio_committed.is_set():
                    logger.info("[asr_pipeline] 最终识别到达，立即触发 Pipecat")
            else:
                logger.debug("[asr_pipeline] ASRPartial: %r", event.text)
                await send_json({"type": "asr.partial", "text": event.text})
                await preemptive.update_partial(event.text)

        _report_mic()

        # 部分 ASR 服务会在消费到结束标记前先产出 ASRFinal；此时必须显式提交，
        # 否则 finalize() 会等待一个永远不会到达的 commit，表现为“识别到了但不回答”。
        if final_text and not cancel.is_set():
            await preemptive.finalize(final_text)
            if not preemptive.submitted:
                logger.info("[asr_pipeline] ASRFinal 后强制提交 pipeline text=%r", final_text)
                await preemptive.commit()
        elif not cancel.is_set():
            await preemptive.wait_preemptive()
        if not final_text and not preemptive.submitted and not cancel.is_set():
            # 没识别到内容也要通知客户端，否则 UI 卡在“识别中..."
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
    asr_cancel = asyncio.Event()
    asr_task: asyncio.Task | None = None
    asr_tasks = _ASRTurnTaskRegistry()
    asr_stream_open = False  # 本轮音频流是否已开启（与 pipeline_task 是否结束无关）
    logger.info("[handler] 新连接 session=%s remote=%s", session_id[:8], getattr(websocket, 'remote_address', '?'))

    async def send_json(obj: dict) -> None:
        try:
            await websocket.send(json.dumps(obj, ensure_ascii=False))
        except Exception as e:
            logger.debug("[handler] send_json 失败: %s", e)
            raise

    async def send_bytes(data: bytes) -> None:
        try:
            logger.debug("[handler] send_bytes %d bytes hex[:8]=%s", len(data), data[:8].hex())
            await websocket.send(data)
        except Exception as e:
            logger.debug("[handler] send_bytes 失败: %s", e)
            raise

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

    http = httpx.AsyncClient(**_dify_http_client_options())
    try:
        processors = _build_pipecat_processors(http)
        runtime = PipecatVoiceRuntime(
            processors,
            send_json,
            send_bytes,
        )
        await asyncio.gather(runtime.start(), _warmup_pipecat_processors(processors))
    except Exception:
        logger.exception("[handler] Pipecat 启动失败 session=%s", session_id[:8])
        await send_json({"type": "error", "code": "VOICE_RUNTIME_START_FAILED"})
        await http.aclose()
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
    try:
        async for message in websocket:
            if isinstance(message, bytes):
                # 本轮第一帧即启动云端 ASR；用户开口同时触发 Pipecat 打断。
                if not asr_stream_open:
                    if not runtime.turn_done.is_set():
                        logger.info("[handler] 新一轮说话，Pipecat 打断上一轮")
                        await runtime.interrupt()
                    await asr_tasks.prepare_new_stream(asr_stream_open)
                    asr_cancel = asyncio.Event()
                    audio_queue = asyncio.Queue()
                    asr_stream_open = True
                    binary_frames = 0
                    logger.info("[handler] 首帧音频到达，启动流式 ASR session=%s", session_id[:8])
                    asr_task = asyncio.create_task(
                        _asr_then_pipeline(session_id, audio_queue, send_json, runtime, asr_cancel)
                    )
                    asr_tasks.start(asr_task, asr_cancel)
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
            if msg_type != "ping":
                logger.info("[handler] 收到控制消息 type=%s session=%s", msg_type, session_id[:8])

            if msg_type == "ping":
                await send_json({"type": "pong"})

            elif msg_type == "audio_commit":
                logger.info("[handler] audio_commit：本轮 %d 帧音频结束，关闭 ASR 输入流", binary_frames)
                binary_frames = 0
                if asr_stream_open:
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
                if not runtime.turn_done.is_set():
                    await runtime.interrupt()
                await send_json({"type": "assistant.emotion", "emotion": extract_emotion(text)})
                await runtime.submit_text(text)

            elif msg_type == "interrupt":
                logger.info("[handler] interrupt 收到，交给 Pipecat 取消")
                asr_cancel.set()
                if asr_stream_open:
                    await audio_queue.put(None)
                    asr_stream_open = False
                if asr_task and not asr_task.done():
                    await asr_tasks.cancel_all()
                await runtime.interrupt()
    finally:
        asr_cancel.set()
        if asr_stream_open:
            await audio_queue.put(None)
        await asr_tasks.cancel_all()
        try:
            if not runtime.turn_done.is_set():
                await runtime.interrupt()
            await runtime.close()
        except Exception:
            logger.debug("[handler] 连接关闭阶段忽略发送失败", exc_info=True)
        await http.aclose()


_H5_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../h5-demo"))
_CERT_DIR = os.path.join(os.path.dirname(__file__), "../../../../certs")

_H5_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/call-core.js": ("call-core.js", "application/javascript; charset=utf-8"),
    "/mascot-layer-layout.js": ("mascot-layer-layout.js", "application/javascript; charset=utf-8"),
    "/mascot-assets.js": ("mascot-assets.js", "application/javascript; charset=utf-8"),
    "/mascot-controller.js": ("mascot-controller.js", "application/javascript; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/assets/mascot/youguang-base.png": ("assets/mascot/youguang-base.png", "image/png"),
    "/vendor/voice/bundle.min.js": ("vendor/voice/bundle.min.js", "application/javascript; charset=utf-8"),
    "/vendor/voice/vad.worklet.bundle.min.js": ("vendor/voice/vad.worklet.bundle.min.js", "application/javascript; charset=utf-8"),
    "/vendor/voice/ort.wasm.min.js": ("vendor/voice/ort.wasm.min.js", "application/javascript; charset=utf-8"),
    "/vendor/voice/ort-wasm-simd-threaded.mjs": ("vendor/voice/ort-wasm-simd-threaded.mjs", "application/javascript; charset=utf-8"),
    "/vendor/voice/ort-wasm-simd-threaded.wasm": ("vendor/voice/ort-wasm-simd-threaded.wasm", "application/wasm"),
    "/vendor/voice/silero_vad_v5.onnx": ("vendor/voice/silero_vad_v5.onnx", "application/octet-stream"),
}

for _mascot_state in (
    "idle", "listening", "hearing", "recognizing", "thinking", "speaking",
    "happy", "sad", "comforting", "surprised", "error", "shy", "laughing", "crying",
    *(f"{_name}-{_index}" for _name in (
        "neutral", "listening", "speaking", "laughing", "crying", "shy", "surprised", "sad"
    ) for _index in (1, 2, 3)),
):
    _H5_ASSETS[f"/assets/mascot/layers/{_mascot_state}.png"] = (
        f"assets/mascot/layers/{_mascot_state}.png", "image/png"
    )


async def _serve_static(connection, request):
    """非 WebSocket 的普通 GET 直接返回 H5 页面，让页面与 WSS 同源同端口。"""
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None  # 交给 WebSocket 处理

    path = request.path.split("?")[0]
    asset = _H5_ASSETS.get(path)
    if asset is None:
        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")

    filename, content_type = asset
    content_encoding = None
    if path in {
        "/vendor/voice/ort-wasm-simd-threaded.wasm",
        "/vendor/voice/silero_vad_v5.onnx",
    } and "gzip" in request.headers.get("Accept-Encoding", "").lower():
        filename = f"{filename}.gz"
        content_encoding = "gzip"
    try:
        with open(os.path.join(_H5_ROOT, filename), "rb") as f:
            body = f.read()
    except OSError as exc:
        logger.error("读取 H5 静态资源失败 path=%s: %s", path, exc)
        return connection.respond(HTTPStatus.INTERNAL_SERVER_ERROR, "h5 asset not found\n")

    headers = Headers()
    headers["Content-Type"] = content_type
    headers["Content-Length"] = str(len(body))
    # 表情轮播会反复切换同一批 PNG；缓存固定资源，避免每 180/360 ms 重新请求。
    # HTML/JS/CSS 仍禁止缓存，确保测试环境刷新即可获得最新逻辑。
    headers["Cache-Control"] = (
        "public, max-age=86400"
        if path.startswith("/assets/") or path.endswith((".wasm", ".onnx"))
        else "no-store"
    )
    if content_encoding:
        headers["Content-Encoding"] = content_encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(HTTPStatus.OK.value, HTTPStatus.OK.phrase, headers, body)


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

    load_dotenv(_env_path, override=False)

    logging.basicConfig(
        level=_log_level(),
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
        print(f"  LLM : {os.getenv('LLM_PROVIDER', 'dify')}", flush=True)
        print("  TTS : 火山引擎 BigTTS v3 双向流 (PCM 16kHz)", flush=True)
        await asyncio.Future()


def main() -> None:
    asyncio.run(run())
