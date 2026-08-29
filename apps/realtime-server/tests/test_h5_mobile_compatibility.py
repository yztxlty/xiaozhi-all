from pathlib import Path

import pytest

from realtime_server import demo


H5 = Path(__file__).parents[2] / "h5-demo" / "index.html"
APP = Path(__file__).parents[2] / "h5-demo" / "app.js"
CSS = Path(__file__).parents[2] / "h5-demo" / "app.css"
MASCOT_ASSETS = Path(__file__).parents[2] / "h5-demo" / "mascot-assets.js"
MASCOT_CONTROLLER = Path(__file__).parents[2] / "h5-demo" / "mascot-controller.js"
MASCOT_BASE = Path(__file__).parents[2] / "h5-demo" / "assets" / "mascot" / "youguang-base.png"
VOICE_ASSETS = Path(__file__).parents[2] / "h5-demo" / "vendor" / "voice"


def test_h5_supports_standard_and_legacy_microphone_apis() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "async function requestMicrophone" in script
    assert "navigator.webkitGetUserMedia" in script
    assert "await requestMicrophone(" in script
    assert "await navigator.mediaDevices.getUserMedia(" not in script


def test_h5_drops_in_flight_audio_until_interruption_finishes() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "if (callState.dropTtsUntilDone) return" in script
    assert "type: 'interrupt.local'" in script
    assert "type: 'tts.done'" in script


def test_h5_has_companion_chat_call_and_debug_drawer() -> None:
    html = H5.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    for element_id in (
        "chat-screen",
        "messages",
        "text-form",
        "start-call",
        "call-screen",
        "call-status",
        "call-timer",
        "latency-value",
        "hangup",
        "debug-drawer",
        "debug-toggle",
        "debug-copy",
        "debug-clear",
    ):
        assert f'id="{element_id}"' in html

    assert 'aria-expanded="false"' in html
    assert 'aria-hidden="true"' in html
    assert '@media (max-width: 720px)' in css
    assert 'overflow-x: hidden' in css


def test_h5_uses_layered_mascot_state_renderer() -> None:
    html = H5.read_text(encoding="utf-8")
    assets = MASCOT_ASSETS.read_text(encoding="utf-8")
    controller = MASCOT_CONTROLLER.read_text(encoding="utf-8")
    layout = (H5.parent / "mascot-layer-layout.js").read_text(encoding="utf-8")

    assert '<div id="mascot" class="mascot"' in html
    assert "mascot-body" not in html
    assert "youguang-base.png" in assets
    assert "assets/mascot/layers/" in assets
    assert "applyFrame(asset.overlay)" in controller
    assert "mascot-layer-layout.js" in html
    assert "canvasWidth" in layout
    assert "asset.frames?.length > 1" in controller
    assert "setInterval" in controller
    assert "assistant.emotion" in controller
    assert "const speakingFrames" in assets
    assert "const listeningFrames" in assets
    assert "const laughingFrames" in assets
    assert "const cryingFrames" in assets
    assert "frames('speaking')" in assets
    assert "frames('listening')" in assets
    assert MASCOT_BASE.exists() and MASCOT_BASE.stat().st_size > 0
    assert "event.data instanceof Blob" in APP.read_text(encoding="utf-8")
    assert "handleBinaryAudio(data)" in APP.read_text(encoding="utf-8")
    assert "binaryFrameChain" in APP.read_text(encoding="utf-8")
    assert "document.addEventListener('visibilitychange'" in APP.read_text(encoding="utf-8")
    assert "async recover()" in APP.read_text(encoding="utf-8")


def test_mascot_base_has_real_transparent_background() -> None:
    from PIL import Image

    image = Image.open(MASCOT_BASE)
    assert image.mode == "RGBA"
    assert image.getchannel("A").getextrema()[0] == 0


def test_mascot_expression_layers_are_aligned_to_face_safe_box() -> None:
    from PIL import Image
    import json
    import re

    root = MASCOT_BASE.parent / "layers"
    layout_text = (root.parent.parent.parent / "mascot-layer-layout.js").read_text(encoding="utf-8")
    layout = json.loads(re.search(r"= (\{.*\});", layout_text).group(1))
    for name in ("neutral", "listening", "speaking", "laughing", "crying", "shy", "surprised", "sad"):
        for index in (1, 2, 3):
            filename = f"{name}-{index}.png"
            image = Image.open(root / filename)
            meta = layout[filename]
            assert image.mode == "RGBA"
            assert image.width < 466 and image.height < 466
            assert image.getchannel("A").getbbox() == (0, 0, image.width, image.height)
            assert abs(meta["x"] + image.width / 2 - 233) <= 0.5
            assert abs(meta["y"] + image.height / 2 - 180) <= 0.5


def test_h5_hides_stale_manual_fallback_after_handsfree_recovers() -> None:
    script = APP.read_text(encoding="utf-8")
    assert "await HandsFreeConversation.start();" in script
    assert "await HandsFreeConversation.start();\n      $('manual-fallback').hidden = true;" in script


def test_h5_uses_pinned_mature_vad_with_manual_fallback() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "vendor/voice/" in script
    assert "HandsFreeConversation" in script
    assert "startManual" in script
    assert "onSpeechStart" in script
    assert "onSpeechEnd" in script


def test_h5_local_interactions_do_not_wait_for_third_party_cdn_scripts() -> None:
    html = H5.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert "cdn.jsdelivr.net" not in html
    assert "cdn.jsdelivr.net" not in script
    assert "function loadVoiceLibraries" in script
    assert "await loadVoiceLibraries()" in script


def test_h5_packages_pinned_voice_runtime_assets_for_wechat() -> None:
    expected = {
        "bundle.min.js",
        "bundle.min.js.LICENSE.txt",
        "ort.wasm.min.js",
        "ort-wasm-simd-threaded.mjs",
        "ort-wasm-simd-threaded.wasm",
        "ort-wasm-simd-threaded.wasm.gz",
        "silero_vad_v5.onnx",
        "silero_vad_v5.onnx.gz",
        "vad.worklet.bundle.min.js",
        "第三方许可说明.md",
    }

    assert expected <= {path.name for path in VOICE_ASSETS.iterdir()}
    assert all((VOICE_ASSETS / name).stat().st_size > 0 for name in expected)


def test_h5_vad_reuses_the_user_activated_audio_context() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "audioContext: ensureAudioContext()" in script


def test_h5_resumes_audio_context_after_async_microphone_and_vad_setup() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "async function ensureAudioContextRunning" in script
    assert "await ensureAudioContextRunning(context)" in script
    assert "await ensureAudioContextRunning(ensureAudioContext())" in script


def test_h5_mac_chrome_vad_reads_the_same_live_microphone_stream() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "getStream: async () => sourceStream" in script
    assert "sourceStream.clone()" not in script
    assert "onFrameProcessed:" in script
    assert "track.getSettings()" in script
    assert "麦克风输入正常" in script
    assert "startVadWatchdog" in script
    assert "检测帧超过 5 秒未更新" in script


def test_h5_uses_official_script_processor_for_chrome_and_wechat_compatibility() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "processorType: 'ScriptProcessor'" in script


def test_h5_keeps_pcm_capture_alive_using_the_official_chrome_output_pattern() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "event.outputBuffer.getChannelData(0).fill(0)" in script
    assert "processor.connect(context.destination)" in script
    assert "muteNode.gain.value = 0" not in script


def test_h5_rejects_virtual_microphones_and_selects_a_physical_input() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "function isVirtualAudioInput" in script
    assert "EShareAudio" in script
    assert "navigator.mediaDevices.enumerateDevices()" in script
    assert "deviceId: { exact: preferred.deviceId }" in script
    assert "await requestPreferredMicrophone(" in script


def test_h5_only_reports_listening_after_vad_is_ready() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "type: 'microphone.preparing'" in script
    assert "await HandsFreeConversation.start()" in script
    assert script.index("type: 'microphone.preparing'") < script.index(
        "await HandsFreeConversation.start()"
    )


def test_h5_uses_combined_server_websocket_route_under_chat_subpath() -> None:
    core = (APP.parent / "call-core.js").read_text(encoding="utf-8")
    config = (APP.parent / "运行配置.js").read_text(encoding="utf-8")
    assert "websocketRoute" in core
    assert "websocketRoute: '/xiaozhi/v1/ws'" in config


def test_h5_prewarms_vad_assets_and_keeps_controls_inside_the_viewport() -> None:
    script = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert "function warmVoiceAssets" in script
    assert "silero_vad_v5.onnx" in script
    assert "ort-wasm-simd-threaded.wasm" in script
    assert "height:100dvh" in css
    assert ".screen{height:100%" in css
    assert ".aurora{pointer-events:none" in css


def test_h5_uses_non_overlay_debug_split_layout() -> None:
    html = H5.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'id="demo-layout"' in html
    assert "debug-backdrop" not in html
    assert ".demo-layout.debug-open" in css
    assert "grid-template-columns" in css
    assert "$('demo-layout').classList.add('debug-open')" in script
    assert "$('demo-layout').classList.remove('debug-open')" in script


def test_h5_keeps_composer_fixed_and_scrolls_streaming_replies_to_latest() -> None:
    css = CSS.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert ".chat-screen{display:none" in css
    assert ".chat-screen.is-active{display:grid" in css
    assert "grid-template-rows:auto minmax(0,1fr) auto" in css
    assert ".chat-composer{position:relative" in css
    assert ".messages{min-height:0;padding:26px 24px;overscroll-behavior:contain;scroll-behavior:auto" in css
    assert "function scrollMessagesToLatest" in script
    assert "assistantMessage.bubble.textContent +=" in script
    assert "scrollMessagesToLatest();" in script


def test_h5_archives_each_assistant_tts_for_play_pause_resume() -> None:
    script = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert "const AudioArchive" in script
    assert "CallCore.pcm16ToWav" in script
    assert "message-audio" in script
    assert ".message-audio" in css
    assert "audio.pause()" in script
    assert "audio.play()" in script


def test_h5_exposes_real_vad_driven_hearing_state() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "hearing: ['听到你了'" in script
    assert "setState({ type: 'speech.start' })" in script


def test_h5_uses_official_silero_thresholds_for_real_microphones() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "positiveSpeechThreshold: 0.3" in script
    assert "negativeSpeechThreshold: 0.25" in script
    assert "positiveSpeechThreshold: 0.72" not in script


def test_h5_unlocks_mobile_web_audio_from_the_call_gesture() -> None:
    script = APP.read_text(encoding="utf-8")
    start_call = script.split("async function startCall()", 1)[1].split(
        "async function endCall()", 1
    )[0]

    assert "function unlockAudioPlayback" in script
    assert "source.start(0)" in script
    assert "const audioUnlock = unlockAudioPlayback()" in start_call
    assert "await audioUnlock" in start_call
    assert start_call.index("const audioUnlock = unlockAudioPlayback()") < start_call.index(
        "await connect()"
    )
    assert "if (context.state !== 'running')" in script
    assert "context.resume().then(schedule)" in script
    assert "$('latency-value').textContent = '—'" in start_call


def test_h5_uses_an_inline_favicon_to_keep_the_demo_console_clean() -> None:
    html = H5.read_text(encoding="utf-8")

    assert 'rel="icon"' in html
    assert 'href="data:image/svg+xml,' in html


class _Response:
    def __init__(self, body: str) -> None:
        self.body = body
        self.headers = {"Content-Type": "text/plain"}


class _Connection:
    def respond(self, _status, body: str) -> _Response:
        return _Response(body)


class _Request:
    def __init__(self, path: str, headers: dict | None = None) -> None:
        self.path = path
        self.headers = headers or {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "content_type"),
    (("/", "text/html"), ("/app.css", "text/css"), ("/call-core.js", "javascript"), ("/app.js", "javascript")),
)
async def test_h5_static_assets_are_served_with_correct_mime(path: str, content_type: str) -> None:
    response = await demo._serve_static(_Connection(), _Request(path))

    assert response is not None
    assert content_type in response.headers["Content-Type"]
    assert response.body


@pytest.mark.asyncio
async def test_mascot_frames_are_cached_instead_of_refetched_on_every_animation_tick() -> None:
    response = await demo._serve_static(
        _Connection(),
        _Request("/assets/mascot/layers/neutral-1.png"),
    )

    assert response.headers["Cache-Control"] == "public, max-age=86400"


@pytest.mark.asyncio
async def test_h5_entry_document_remains_uncached_for_safe_updates() -> None:
    response = await demo._serve_static(_Connection(), _Request("/"))

    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "content_type"),
    (
        ("/vendor/voice/bundle.min.js", "javascript"),
        ("/vendor/voice/ort-wasm-simd-threaded.mjs", "javascript"),
        ("/vendor/voice/ort-wasm-simd-threaded.wasm", "application/wasm"),
        ("/vendor/voice/silero_vad_v5.onnx", "application/octet-stream"),
    ),
)
async def test_h5_voice_assets_are_served_with_correct_mime(path: str, content_type: str) -> None:
    response = await demo._serve_static(_Connection(), _Request(path))

    assert response is not None
    assert content_type in response.headers["Content-Type"]
    assert response.body


@pytest.mark.asyncio
async def test_h5_wasm_is_served_as_unchanged_binary() -> None:
    path = "/vendor/voice/ort-wasm-simd-threaded.wasm"
    response = await demo._serve_static(_Connection(), _Request(path))

    assert response.body == (VOICE_ASSETS / "ort-wasm-simd-threaded.wasm").read_bytes()
    assert response.body.startswith(b"\x00asm")
    assert response.headers["Content-Length"] == str(len(response.body))


@pytest.mark.asyncio
async def test_h5_wasm_uses_precompressed_transfer_when_browser_accepts_gzip() -> None:
    path = "/vendor/voice/ort-wasm-simd-threaded.wasm"
    request = _Request(path, {"Accept-Encoding": "gzip, deflate, br"})
    response = await demo._serve_static(_Connection(), request)
    compressed = (VOICE_ASSETS / "ort-wasm-simd-threaded.wasm.gz").read_bytes()

    assert response.body == compressed
    assert response.headers["Content-Encoding"] == "gzip"
    assert response.headers["Vary"] == "Accept-Encoding"
    assert int(response.headers["Content-Length"]) < 3_000_000


@pytest.mark.asyncio
async def test_h5_vad_uses_precompressed_transfer_when_browser_accepts_gzip() -> None:
    path = "/vendor/voice/silero_vad_v5.onnx"
    request = _Request(path, {"Accept-Encoding": "gzip, deflate, br"})
    response = await demo._serve_static(_Connection(), request)
    compressed = (VOICE_ASSETS / "silero_vad_v5.onnx.gz").read_bytes()

    assert response.body == compressed
    assert response.headers["Content-Encoding"] == "gzip"
    assert response.headers["Vary"] == "Accept-Encoding"
