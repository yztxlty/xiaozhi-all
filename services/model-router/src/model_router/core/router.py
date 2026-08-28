from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence

from .contracts import LLMFailed, LLMProvider, LLMRequest, LLMStreamEvent


class LLMRouter:
    def __init__(self, providers: Sequence[LLMProvider]) -> None:
        if not providers:
            raise ValueError("至少需要一个大语言模型提供方")
        self.providers = tuple(providers)

    async def stream(
        self,
        request: LLMRequest,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[LLMStreamEvent]:
        current_request = request

        for index, provider in enumerate(self.providers):
            can_fallback = False
            async for event in provider.stream(current_request, cancel_event):
                yield event
                if isinstance(event, LLMFailed):
                    can_fallback = event.retryable and not event.delta_emitted

            if (
                cancel_event.is_set()
                or not can_fallback
                or index == len(self.providers) - 1
            ):
                return

            current_request = current_request.model_copy(
                update={"generation_id": f"g_{uuid.uuid4().hex}"}
            )
