import pytest

from xiaozhi_gateway.handshake import HandshakeError, parse_hello


def test_parse_supported_xiaozhi_hello() -> None:
    hello = parse_hello(
        {
            "version": 1,
            "type": "hello",
            "transport": "websocket",
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60,
            },
        }
    )

    assert hello.transport == "websocket"
    assert hello.audio_format.codec == "opus"
    assert hello.audio_format.sample_rate_hz == 16000


@pytest.mark.parametrize(
    "patch",
    [
        {"type": "listen"},
        {"transport": "udp"},
        {"audio_params": {"format": "mp3"}},
    ],
)
def test_reject_incompatible_hello(patch: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "version": 1,
        "type": "hello",
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        },
    }
    payload.update(patch)

    with pytest.raises(HandshakeError):
        parse_hello(payload)
