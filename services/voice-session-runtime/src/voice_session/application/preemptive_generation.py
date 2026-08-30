from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
from typing import Protocol


class GenerationRuntime(Protocol):
    async def submit_text(self, text: str) -> None: ...

    async def interrupt(self, *, notify_client: bool = True) -> None: ...


class PreemptiveGenerationCoordinator:
    """协调提前生成与最终识别校正。

    行为复用 LiveKit Agents 的 preemptive generation 设计：先隐藏识别收尾延迟，
    最终文字发生实质变化时取消旧生成并重启；不承担识别、模型或合成职责。
    """

    _PUNCTUATION = "，。！？；：,.!?;:、 \t\r\n"
    _MIN_PREEMPTIVE_CHARS = 3

    def __init__(
        self,
        runtime: GenerationRuntime,
        *,
        stability_delay_seconds: float = 0.18,
    ) -> None:
        self._runtime = runtime
        self._stability_delay_seconds = stability_delay_seconds
        self._latest_partial = ""
        self._final_text = ""
        self._submitted_text = ""
        self._committed = False
        self._partial_at_commit = ""
        self._submit_task: asyncio.Task | None = None

    @property
    def submitted(self) -> bool:
        return bool(self._submitted_text)

    async def update_partial(self, text: str) -> None:
        self._latest_partial = text
        if self._committed:
            if not self._submitted_text and self._is_stable_extension(
                self._partial_at_commit, text
            ):
                await self._cancel_pending_partial()
                await self._submit_partial_once()
                return
        # 在拾音尚未结束时也提前生成；commit 只负责把当前识别结果锁定为本轮。
        await self._schedule_partial()

    async def commit(self, *, force_short: bool = True) -> str:
        self._committed = True
        self._partial_at_commit = self._latest_partial
        if self._final_text and not self._submitted_text:
            await self._runtime.submit_text(self._final_text)
            self._submitted_text = self._final_text
        elif (
            self._latest_partial
            and force_short
            and len(self._normalize(self._latest_partial)) < self._MIN_PREEMPTIVE_CHARS
            and not self._submitted_text
        ):
            # 短句不允许在拾音中抢跑，但用户结束说话后仍立即提交。
            await self._runtime.submit_text(self._latest_partial)
            self._submitted_text = self._latest_partial
        else:
            await self._schedule_partial()
        return self._submitted_text

    async def finalize(self, text: str) -> str:
        self._final_text = text
        if not self._committed:
            return self._submitted_text
        await self._cancel_pending_partial()
        if self._submitted_text:
            if not self._same_utterance(self._submitted_text, text):
                # 最终识别校正属于同一用户轮次，不应向客户端伪造整轮结束事件。
                await self._runtime.interrupt(notify_client=False)
                await self._runtime.submit_text(text)
                self._submitted_text = text
        else:
            await self._runtime.submit_text(text)
            self._submitted_text = text
        return self._submitted_text

    async def wait_preemptive(self) -> None:
        task = self._submit_task
        if task is not None:
            await task

    async def _schedule_partial(self) -> None:
        if self._submitted_text or (self._submit_task and not self._submit_task.done()):
            return
        if self._stability_delay_seconds <= 0:
            await self._submit_partial_once()
            return
        self._submit_task = asyncio.create_task(self._submit_after_stability_window())

    async def _submit_after_stability_window(self) -> None:
        await asyncio.sleep(self._stability_delay_seconds)
        await self._submit_partial_once()

    async def _cancel_pending_partial(self) -> None:
        task = self._submit_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            if self._submit_task is task:
                self._submit_task = None

    async def _submit_partial_once(self) -> None:
        if (
            len(self._normalize(self._latest_partial)) >= self._MIN_PREEMPTIVE_CHARS
            and not self._submitted_text
        ):
            await self._runtime.submit_text(self._latest_partial)
            self._submitted_text = self._latest_partial

    @classmethod
    def _same_utterance(cls, left: str, right: str) -> bool:
        left_normalized = cls._normalize(left)
        right_normalized = cls._normalize(right)
        if left_normalized == right_normalized:
            return True
        # 中文实时识别经常在最终结果末尾补出唤醒名；这不会改变用户意图，
        # 不应因此取消已经开始的首句生成与播报。
        if right_normalized == f"{left_normalized}幽光":
            return True
        if left_normalized == f"{right_normalized}幽光":
            return True
        if not left_normalized or not right_normalized:
            return False
        if len(left_normalized) / len(right_normalized) < 0.65:
            return False
        final_prefix = right_normalized[: len(left_normalized)]
        return SequenceMatcher(None, left_normalized, final_prefix).ratio() >= 0.88

    @classmethod
    def _is_stable_extension(cls, before: str, after: str) -> bool:
        before_normalized = cls._normalize(before)
        after_normalized = cls._normalize(after)
        return len(after_normalized) >= len(before_normalized) + 2

    @classmethod
    def _normalize(cls, text: str) -> str:
        translation = str.maketrans("", "", cls._PUNCTUATION)
        return text.translate(translation).replace("忧光", "幽光")
