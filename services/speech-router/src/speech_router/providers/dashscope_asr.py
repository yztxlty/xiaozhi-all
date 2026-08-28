"""
阿里云 DashScope 实时语音识别 ASR Provider

使用 DashScope 的 paraformer-realtime-v2 WebSocket 流式 ASR。
输入：PCM 16kHz 单声道 int16 音频帧（bytes）
输出：ASRPartial / ASRFinal 事件
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterable, AsyncIterator

import websockets

from speech_router.core.asr_contracts import ASRFinal, ASRPartial, ASRProvider

logger = logging.getLogger("asr.dashscope")

_DASHSCOPE_ASR_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference/"


class DashScopeASRProvider:
    """阿里云 DashScope 实时 ASR，符合 ASRProvider Protocol。"""

    provider_id = "dashscope-paraformer"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")

    async def recognize(
        self,
        audio: AsyncIterable[bytes],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[ASRPartial | ASRFinal]:
        task_id = uuid.uuid4().hex
        headers = {
            "Authorization": f"bearer {self._api_key}",
            "X-DashScope-DataInspection": "enable",
        }
        logger.info("[ASR] recognize 开始 task_id=%s", task_id[:8])

        import inspect
        header_kwarg = (
            "additional_headers"
            if "additional_headers" in inspect.signature(websockets.connect).parameters
            else "extra_headers"
        )
        connect_kwargs = {header_kwarg: headers}

        async with websockets.connect(_DASHSCOPE_ASR_URL, **connect_kwargs) as ws:
            logger.info("[ASR] WebSocket 已连接")

            # 1. run-task
            await ws.send(json.dumps({
                "header": {
                    "action": "run-task",
                    "task_id": task_id,
                    "streaming": "duplex",
                },
                "payload": {
                    "task_group": "audio",
                    "task": "asr",
                    "function": "recognition",
                    "model": "paraformer-realtime-v2",
                    "parameters": {
                        "format": "pcm",
                        "sample_rate": 16000,
                        "language_hints": ["zh", "en"],
                    },
                    "input": {},
                },
            }, ensure_ascii=False))
            logger.info("[ASR] run-task 已发送，等待 task-started")

            # 等待 task-started
            msg = json.loads(await ws.recv())
            event = msg.get("header", {}).get("event")
            logger.info("[ASR] 收到事件: %s", event)
            if event != "task-started":
                raise RuntimeError(f"DashScope ASR task-started 失败: {msg}")

            # 2. 并行：发送音频帧 + 接收结果
            send_done = asyncio.Event()
            audio_chunks_sent = 0
            audio_bytes_sent = 0

            async def _send_audio() -> None:
                nonlocal audio_chunks_sent, audio_bytes_sent
                async for chunk in audio:
                    if cancel_event.is_set():
                        logger.info("[ASR] cancel_event 已设置，停止发送音频")
                        break
                    audio_chunks_sent += 1
                    audio_bytes_sent += len(chunk)
                    await ws.send(chunk)
                logger.info("[ASR] 音频发送完毕: %d 帧 共 %d bytes，发送 finish-task",
                            audio_chunks_sent, audio_bytes_sent)
                await ws.send(json.dumps({
                    "header": {
                        "action": "finish-task",
                        "task_id": task_id,
                        "streaming": "duplex",
                    },
                    "payload": {"input": {}},
                }, ensure_ascii=False))
                send_done.set()

            send_task = asyncio.create_task(_send_audio())

            # 3. 接收识别结果
            result_queue: asyncio.Queue[ASRPartial | ASRFinal | None] = asyncio.Queue()

            async def _recv_loop() -> None:
                partial_count = 0
                final_count = 0
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    event = msg.get("header", {}).get("event", "")
                    if event == "result-generated":
                        output = msg.get("payload", {}).get("output", {})
                        sentence = output.get("sentence", {})
                        text = sentence.get("text", "")
                        sentence_end = sentence.get("sentence_end", False)
                        if not text:
                            logger.debug("[ASR] result-generated 空文本，跳过")
                            continue
                        if sentence_end is True:
                            final_count += 1
                            logger.info("[ASR] ASRFinal #%d text=%r", final_count, text)
                            await result_queue.put(ASRFinal(text=text))
                        else:
                            partial_count += 1
                            logger.debug("[ASR] ASRPartial #%d text=%r", partial_count, text)
                            await result_queue.put(ASRPartial(text=text))
                    elif event in ("task-finished", "task-failed"):
                        logger.info("[ASR] 收到 %s，识别结束", event)
                        await result_queue.put(None)
                        break
                    else:
                        logger.debug("[ASR] 忽略事件: %s", event)

            recv_task = asyncio.create_task(_recv_loop())

            try:
                while True:
                    item = await result_queue.get()
                    if item is None:
                        logger.info("[ASR] recognize 完成")
                        break
                    if cancel_event.is_set():
                        logger.info("[ASR] cancel_event 已设置，停止输出")
                        break
                    yield item
            finally:
                send_task.cancel()
                recv_task.cancel()
                for t in (send_task, recv_task):
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
