import pytest

from realtime_protocol import ControlType
from xiaozhi_gateway.audio_frames import AudioFrame, AudioFrameError
from xiaozhi_gateway.connection_registry import ConnectionRegistry
from xiaozhi_gateway.control_messages import decode_control_message, encode_control_message


def test_control_message_json_round_trip() -> None:
    message = decode_control_message('{"version":1,"type":"listen","state":"start"}')

    assert message.type is ControlType.LISTEN
    assert decode_control_message(encode_control_message(message)) == message


def test_audio_frame_rejects_empty_or_oversized_payload() -> None:
    with pytest.raises(AudioFrameError):
        AudioFrame.create(1, b"")
    with pytest.raises(AudioFrameError):
        AudioFrame.create(1, b"x" * 4097)


def test_connection_registry_enforces_one_session_per_connection() -> None:
    registry = ConnectionRegistry()
    registry.bind("connection_1", "session_1")

    assert registry.session_for("connection_1") == "session_1"
    with pytest.raises(ValueError, match="已绑定"):
        registry.bind("connection_1", "session_2")

    registry.unbind("connection_1")
    assert registry.session_for("connection_1") is None
