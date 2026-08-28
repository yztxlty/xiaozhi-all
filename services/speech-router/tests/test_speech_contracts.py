import asyncio

from speech_router.core.asr_contracts import ASRFinal, ASRPartial
from speech_router.core.router import SpeechRouter
from speech_router.core.tts_contracts import TTSAudioChunk


class FakeASR:
    provider_id = "fake-asr"

    async def recognize(self, audio, cancel_event):
        yield ASRPartial("你好")
        yield ASRFinal("你好")


class FakeTTS:
    provider_id = "fake-tts"

    async def synthesize(self, text, cancel_event):
        yield TTSAudioChunk(1, text.encode())


async def test_router_exposes_streaming_asr_and_tts() -> None:
    router = SpeechRouter(FakeASR(), FakeTTS())

    asr_events = [event async for event in router.recognize(_empty_audio(), asyncio.Event())]
    tts_events = [event async for event in router.synthesize("你好", asyncio.Event())]

    assert [type(event) for event in asr_events] == [ASRPartial, ASRFinal]
    assert tts_events[0].payload == "你好".encode()


async def _empty_audio():
    if False:
        yield b""
