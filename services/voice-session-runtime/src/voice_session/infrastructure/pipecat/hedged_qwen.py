from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from pipecat.services.qwen.llm import QwenLLMService


class HedgedQwenLLMService(QwenLLMService):
    """复用 Pipecat 千问服务，并用延迟副请求削平首响应长尾。"""

    def __init__(self, *, hedge_delay_seconds: float = 0.28, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._hedge_delay_seconds = hedge_delay_seconds

    async def get_chat_completions(self, context):
        primary = asyncio.create_task(super().get_chat_completions(context))
        done, _ = await asyncio.wait({primary}, timeout=self._hedge_delay_seconds)
        if done:
            return primary.result()

        secondary = asyncio.create_task(super().get_chat_completions(context))
        done, pending = await asyncio.wait(
            {primary, secondary}, return_when=asyncio.FIRST_COMPLETED
        )
        winner = primary if primary in done else secondary
        try:
            return winner.result()
        finally:
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task
