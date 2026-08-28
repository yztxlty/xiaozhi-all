from __future__ import annotations

import pytest

from audio_codec import AudioFormat, DEVICE_AUDIO
from provider_contracts import ProviderCapability, ProviderHealth
from realtime_protocol import ControlMessage, ControlType, ProtocolError
from xiaozhi_observability import TraceContext


def test_device_audio_contract_is_esp32_friendly() -> None:
    assert DEVICE_AUDIO == AudioFormat(
        codec="opus",
        sample_rate_hz=16000,
        channels=1,
        frame_duration_ms=60,
    )


def test_control_message_rejects_wrong_protocol_version() -> None:
    with pytest.raises(ProtocolError, match="协议版本"):
        ControlMessage.from_dict({"version": 99, "type": "listen", "state": "start"})


def test_control_message_parses_known_type() -> None:
    message = ControlMessage.from_dict({"version": 1, "type": "listen", "state": "start"})

    assert message.type is ControlType.LISTEN
    assert message.payload["state"] == "start"


def test_provider_models_and_trace_context_do_not_include_business_payload() -> None:
    capability = ProviderCapability("dify", streaming=True, cancellation=True)
    health = ProviderHealth("dify", healthy=True, detail="ready")
    trace = TraceContext(session_id="s1", turn_id="t1", generation_id="g1")

    assert capability.provider_id == health.provider_id == "dify"
    assert trace.as_log_fields() == {
        "session_id": "s1",
        "turn_id": "t1",
        "generation_id": "g1",
    }
