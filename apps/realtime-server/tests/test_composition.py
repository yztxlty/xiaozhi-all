import httpx

from realtime_server.composition import compose_runtime
from realtime_server.health import health_snapshot
from realtime_server.settings import RealtimeSettings


class FakeASR:
    provider_id = "fake-asr"

    async def recognize(self, audio, cancel_event):
        if False:
            yield


class FakeTTS:
    provider_id = "fake-tts"

    async def synthesize(self, text, cancel_event):
        if False:
            yield


def test_composition_is_the_single_provider_assembly_root() -> None:
    settings = RealtimeSettings.from_values(
        dify_base_url="https://dify.example.test/v1",
        dify_api_key="not-a-real-key",
    )
    components = compose_runtime(settings, httpx.AsyncClient(trust_env=False), FakeASR(), FakeTTS())

    snapshot = health_snapshot(components)

    assert len(components.model_router.providers) == 1
    assert components.speech_router.asr.provider_id == "fake-asr"
    assert snapshot == {"status": "ready", "model_router": "ready", "speech_router": "ready"}
