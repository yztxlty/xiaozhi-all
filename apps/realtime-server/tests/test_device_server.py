import inspect
import realtime_server.device_server as device_server

from realtime_server.device_server import (
    _new_device_audio_queue,
    is_device_path,
    should_interrupt_device_turn,
    should_accept_device_audio,
    should_start_device_asr,
)


def test_device_audio_queue_does_not_backpressure_websocket_reader():
    assert _new_device_audio_queue().maxsize == 0


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


def test_device_audio_interrupt_only_comes_from_detected_voice():
    source = inspect.getsource(device_server.handle_device_connection)
    assert "is_voice = speech_boundary.is_voice(pcm)" in source
    assert "should_interrupt_device_turn(" in source
    assert "await interrupt_current_turn()" in source


def test_new_voice_interrupts_finished_asr_while_tts_is_busy():
    assert should_interrupt_device_turn(
        input_closed=False, is_voice=True, asr_done=True, runtime_busy=True,
        voice_streak=5,
    )
    assert not should_interrupt_device_turn(
        input_closed=False, is_voice=True, asr_done=False, runtime_busy=True,
        voice_streak=5,
    )


def test_tts_ignores_short_echo_burst_but_allows_sustained_barge_in():
    assert not should_interrupt_device_turn(
        input_closed=True, is_voice=True, asr_done=True, runtime_busy=True,
        voice_streak=4,
    )
    assert should_interrupt_device_turn(
        input_closed=True, is_voice=True, asr_done=True, runtime_busy=True,
        voice_streak=5,
    )


def test_tts_completion_grace_drops_playback_tail_before_new_asr():
    assert not should_accept_device_audio(now=10.9, accept_after=11.0)
    assert should_accept_device_audio(now=11.0, accept_after=11.0)


def test_tts_does_not_start_asr_for_continuous_non_voice_audio():
    assert not should_start_device_asr(runtime_busy=True, is_voice=False, asr_active=False)
    assert not should_start_device_asr(
        runtime_busy=True, is_voice=True, asr_active=False, voice_streak=4
    )
    assert should_start_device_asr(
        runtime_busy=True, is_voice=True, asr_active=False, voice_streak=5
    )
    assert should_start_device_asr(runtime_busy=True, is_voice=False, asr_active=True)
