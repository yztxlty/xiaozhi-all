from realtime_server.device_server import is_device_path
import realtime_server.device_server as device_server


def test_device_paths_are_separate_from_h5_paths():
    assert is_device_path("/xiaozhi/v1/ws")
    assert not is_device_path("/")
    assert not is_device_path("/h5/ws")


def test_module_exposes_a_real_server_entrypoint():
    assert callable(device_server.main)
    assert callable(device_server.run_device_server)


def test_device_speech_boundary_closes_after_voice_and_silence():
    boundary = device_server.DeviceSpeechBoundary(silence_seconds=0.72)
    voice = (b"\x00\x04" * 960)
    silence = b"\x00\x00" * 960
    assert not boundary.feed(voice, now=1.0)
    assert not boundary.feed(silence, now=1.70)
    assert boundary.feed(silence, now=1.72)


def test_device_speech_boundary_accepts_quiet_voice():
    boundary = device_server.DeviceSpeechBoundary(rms_threshold=35.0)
    quiet_voice = (b"\x28\x00" * 960)
    assert not boundary.feed(quiet_voice, now=1.0)
