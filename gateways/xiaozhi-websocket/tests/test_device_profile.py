import pytest

from xiaozhi_gateway.device_profile import ESP32_PROFILE, T5_PROFILE, profile_from_hello
from realtime_protocol import ControlMessage, ControlType


def _hello(**overrides):
    value = {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "features": {"mcp": True},
        "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
    }
    value.update(overrides)
    return value


def test_esp32_hello_creates_profile():
    profile = profile_from_hello(_hello())
    assert profile == ESP32_PROFILE


@pytest.mark.parametrize("field,value", [("transport", "mqtt"), ("version", 2)])
def test_unsupported_hello_is_rejected(field, value):
    with pytest.raises(ValueError):
        profile_from_hello(_hello(**{field: value}))


def test_mcp_is_a_supported_control_type():
    assert ControlMessage.from_dict({"type": "mcp", "payload": {}}).type is ControlType.MCP


def test_profile_supports_t5_pcm_without_changing_esp32_profile():
    profile = profile_from_hello({
        "type": "hello", "version": 1, "transport": "websocket", "device_type": "t5",
        "audio_params": {"format": "pcm_s16le", "sample_rate": 16000, "channels": 1, "frame_duration": 20},
    })
    assert profile == T5_PROFILE
