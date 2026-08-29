from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from pipecat.frames.frames import CancelFrame, Frame, TTSAudioRawFrame, TTSStoppedFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TextAggregationMode, TTSService


class PipecatVolcengineTTSService(TTSService):
    """将既有火山双向流 Provider 接入 Pipecat 的 TTS 生命周期。"""

    def __init__(self, *, provider: Any, sample_rate: int = 16000) -> None:
        super().__init__(
            sample_rate=sample_rate,
            push_start_frame=True,
            push_stop_frames=False,
            text_aggregation_mode=TextAggregationMode.TOKEN,
            reuse_context_id_within_turn=True,
            settings=TTSSettings(
                model="BigTTS-v3",
                voice="volcengine-configured-voice",
                language="zh-CN",
            ),
        )
        self._provider = provider
        self._output_sample_rate = sample_rate
        self._provider_context_id: str | None = None

    async def warmup(self) -> None:
        await self._provider.warmup()

    async def on_turn_context_created(self, context_id: str):
        self._provider_context_id = context_id
        await self._provider.start_session(context_id)
        self._provider.set_audio_sink(self._on_audio)

    async def _on_audio(self, audio: bytes) -> None:
        context_id = self._provider_context_id
        if context_id is None:
            return
        await self.stop_ttfb_metrics()
        await self.append_to_audio_context(
            context_id,
            TTSAudioRawFrame(
                audio=audio,
                sample_rate=self._output_sample_rate,
                num_channels=1,
                context_id=context_id,
            ),
        )

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        await self._provider.send_text(context_id, text)
        yield None

    async def on_turn_context_completed(self):
        context_id = self._provider_context_id
        if context_id is not None:
            try:
                finished = await self._provider.finish_session(context_id)
                await self._provider.wait_session_finished(context_id, finished)
                if self.audio_context_available(context_id):
                    await self.append_to_audio_context(
                        context_id, TTSStoppedFrame(context_id=context_id)
                    )
                    await self.remove_audio_context(context_id)
            finally:
                self._provider.set_audio_sink(None)
                self._provider_context_id = None
        await super().on_turn_context_completed()

    async def on_audio_context_interrupted(self, context_id: str):
        if self._provider_context_id == context_id:
            self._provider.set_audio_sink(None)
            await self._provider.cancel_session(context_id)
            self._provider_context_id = None

    async def cancel(self, frame: CancelFrame):
        context_id = self._provider_context_id
        if context_id is not None:
            self._provider.set_audio_sink(None)
            await self._provider.cancel_session(context_id)
            self._provider_context_id = None
        await super().cancel(frame)

    async def cleanup(self):
        await self._provider.close()
        await super().cleanup()
