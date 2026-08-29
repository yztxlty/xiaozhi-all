import struct

from audio_codec.opus import OpusDecoder, OpusEncoder


def test_opus_round_trip_preserves_one_60ms_frame_shape():
    pcm = struct.pack("<960h", *([0] * 960))
    encoded = OpusEncoder().encode(pcm)
    decoded = OpusDecoder().decode(encoded)

    assert encoded
    assert len(decoded) == 960 * 2


def test_opus_rejects_non_60ms_pcm_frame():
    try:
        OpusEncoder().encode(b"\x00\x00")
    except ValueError as exc:
        assert "960" in str(exc)
    else:
        raise AssertionError("invalid PCM frame must fail")
