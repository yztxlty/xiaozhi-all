import asyncio

import pytest

from speech_router import providers


def _event_frame(event: int, session_id: str, payload: bytes = b"{}", *, audio: bool = False) -> bytes:
    message_type = providers.AUDIO_ONLY_RESPONSE if audio else providers.FULL_SERVER_RESPONSE
    return b"".join(
        (
            providers._header(message_type, providers.MsgTypeFlagWithEvent),
            event.to_bytes(4, "big", signed=True),
            len(session_id.encode()).to_bytes(4, "big", signed=True),
            session_id.encode(),
            len(payload).to_bytes(4, "big", signed=True),
            payload,
        )
    )


def test_parser_keeps_session_id_for_stale_event_filtering() -> None:
    event, session_id, audio = providers._parse(
        _event_frame(providers.EVENT_TTSResponse, "current-session", b"\x01\x02", audio=True)
    )

    assert event == providers.EVENT_TTSResponse
    assert session_id == "current-session"
    assert audio == b"\x01\x02"


@pytest.mark.asyncio
async def test_old_session_event_cannot_finish_current_session() -> None:
    provider = providers.VolcengineTTSProvider()
    current = provider._wait_for_event(providers.EVENT_SessionFinished, "current-session")

    provider._resolve_event(providers.EVENT_SessionFinished, "old-session")

    assert current.done() is False
    provider._resolve_event(providers.EVENT_SessionFinished, "current-session")
    assert current.done() is True


@pytest.mark.asyncio
async def test_finish_session_is_non_blocking_and_keeps_long_lived_connection(monkeypatch) -> None:
    class FakeWebSocket:
        def __init__(self):
            self.sent = []
            self.closed = False

        async def send(self, frame):
            self.sent.append(frame)

        async def close(self):
            self.closed = True

    monkeypatch.setattr(providers, "_send", lambda *args, **kwargs: asyncio.sleep(0))

    provider = providers.VolcengineTTSProvider()
    websocket = FakeWebSocket()
    provider._ws = websocket
    provider._active_session_id = "session-1"

    finished = await provider.finish_session("session-1")

    assert finished is not None
    assert finished.done() is False
    assert provider._active_session_id == "session-1"
    assert provider._ws is websocket
    assert websocket.closed is False


@pytest.mark.asyncio
async def test_cancel_waits_for_matching_service_confirmation(monkeypatch) -> None:
    class FakeWebSocket:
        async def close(self):
            pass

    monkeypatch.setattr(providers, "_send", lambda *args, **kwargs: asyncio.sleep(0))
    provider = providers.VolcengineTTSProvider()
    provider._ws = FakeWebSocket()
    provider._active_session_id = "current-session"

    cancellation = asyncio.create_task(provider.cancel_session("current-session"))
    await asyncio.sleep(0)
    assert cancellation.done() is False

    provider._resolve_event(providers.EVENT_SessionCanceled, "old-session")
    await asyncio.sleep(0)
    assert cancellation.done() is False

    provider._resolve_event(providers.EVENT_SessionCanceled, "current-session")
    await cancellation
    assert provider._active_session_id is None
