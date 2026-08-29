import struct

import pytest

import audio_codec.device_media as device_media
from audio_codec.opus import OpusEncoder
from audio_codec.device_media import DeviceOpusInput, DeviceOpusOutput
from audio_codec.formats import AudioFormat


def test_device_input_decodes_one_firmware_opus_frame():
    encoded = OpusEncoder().encode(struct.pack("<960h", *([0] * 960)))
    assert len(DeviceOpusInput().decode(encoded)) == 1920


@pytest.mark.asyncio
async def test_device_output_buffers_pcm_until_one_firmware_frame():
    encoded = []
    output = DeviceOpusOutput(encoded.append)
    await output.write(b"\x00\x00" * 400)
    assert encoded == []
    await output.write(b"\x00\x00" * 560)
    assert len(encoded) == 1
    assert encoded[0]


@pytest.mark.asyncio
async def test_device_output_paces_every_firmware_frame(monkeypatch):
    clock = [0.0]
    sent_at = []

    async def fake_sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(device_media.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(device_media.asyncio, "sleep", fake_sleep)
    output = DeviceOpusOutput(lambda frame: sent_at.append(clock[0]))

    await output.write(b"\x00\x00" * (960 * 6))

    assert len(sent_at) == 6
    assert sent_at == pytest.approx([0.0, 0.06, 0.12, 0.18, 0.24, 0.30])


@pytest.mark.asyncio
async def test_device_output_resamples_tts_to_24khz_and_flushes_complete_frames(monkeypatch):
    sent = []

    async def fake_sleep(_):
        return None

    monkeypatch.setattr(device_media.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(device_media.asyncio, "sleep", fake_sleep)
    output = DeviceOpusOutput(sent.append, AudioFormat("opus", 24000, 1, 60))

    await output.write(b"\x00\x00" * 16000)
    await output.finish()

    assert len(sent) >= 1
    assert all(frame for frame in sent)


@pytest.mark.asyncio
async def test_device_output_can_start_a_new_tts_turn_after_finish():
    sent = []
    output = DeviceOpusOutput(sent.append, AudioFormat("opus", 24000, 1, 60))

    await output.write(b"\x00\x00" * 960)
    await output.finish()
    first_turn_frames = len(sent)
    await output.write(b"\x00\x00" * 960)
    await output.finish()

    assert len(sent) > first_turn_frames
