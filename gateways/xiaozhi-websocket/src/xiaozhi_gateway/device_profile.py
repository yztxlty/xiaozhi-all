from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from audio_codec import AudioFormat


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    device_type: str
    protocol_version: int
    input_audio: AudioFormat
    output_audio: AudioFormat
    supports_mcp: bool


ESP32_PROFILE = DeviceProfile(
    device_type="esp32",
    protocol_version=1,
    input_audio=AudioFormat("opus", 16000, 1, 60),
    output_audio=AudioFormat("opus", 24000, 1, 60),
    supports_mcp=True,
)

T5_PROFILE = DeviceProfile(
    device_type="t5",
    protocol_version=1,
    input_audio=AudioFormat("pcm_s16le", 16000, 1, 20),
    output_audio=AudioFormat("pcm_s16le", 16000, 1, 20),
    supports_mcp=False,
)


def profile_from_hello(payload: dict[str, Any]) -> DeviceProfile:
    if payload.get("transport") != "websocket":
        raise ValueError("只支持 websocket 传输")
    device_type = str(payload.get("device_type", "esp32")).lower()
    expected = {"esp32": ESP32_PROFILE, "t5": T5_PROFILE}.get(device_type)
    if expected is None:
        raise ValueError("不支持的设备型号")
    if payload.get("version", 1) != expected.protocol_version:
        raise ValueError("不支持的设备协议版本")
    audio = payload.get("audio_params")
    if not isinstance(audio, dict):
        raise ValueError("缺少音频参数")
    actual = AudioFormat(
        codec=audio.get("format"),
        sample_rate_hz=audio.get("sample_rate", 0),
        channels=audio.get("channels", 0),
        frame_duration_ms=audio.get("frame_duration", 0),
    )
    if actual != expected.input_audio:
        raise ValueError(f"{device_type} 音频参数不兼容")
    return DeviceProfile(
        device_type=device_type,
        protocol_version=expected.protocol_version,
        input_audio=actual,
        output_audio=expected.output_audio,
        supports_mcp=expected.supports_mcp and bool((payload.get("features") or {}).get("mcp", False)),
    )
