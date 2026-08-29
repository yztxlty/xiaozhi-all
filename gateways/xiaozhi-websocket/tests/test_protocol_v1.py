import json

from xiaozhi_gateway.protocol_v1 import DeviceProtocolV1


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, value):
        self.sent.append(value)


async def test_protocol_v1_parses_device_controls_and_audio():
    protocol = DeviceProtocolV1(FakeWebSocket(), "s_1")

    assert await protocol.parse(json.dumps({"type": "listen", "state": "start", "mode": "auto"})) == {
        "type": "listen", "state": "start", "mode": "auto"
    }
    assert await protocol.parse(b"opus") == {"type": "audio", "payload": b"opus"}


async def test_protocol_v1_emits_firmware_compatible_messages():
    ws = FakeWebSocket()
    protocol = DeviceProtocolV1(ws, "s_1")

    await protocol.send_hello()
    await protocol.send_stt("你好")
    await protocol.send_tts_start()
    await protocol.send_tts_sentence("你好呀")
    await protocol.send_llm_emotion("happy")
    await protocol.send_audio(b"opus")
    await protocol.send_tts_stop()

    messages = [json.loads(item) for item in ws.sent if isinstance(item, str)]
    assert messages[0]["type"] == "hello"
    assert messages[0]["audio_params"]["format"] == "opus"
    assert messages[0]["audio_params"]["sample_rate"] == 24000
    assert messages[0]["audio_params"]["frame_duration"] == 60
    assert messages[1] == {"type": "stt", "text": "你好", "session_id": "s_1"}
    assert messages[2] == {"type": "tts", "state": "start", "session_id": "s_1"}
    assert messages[3]["state"] == "sentence_start"
    assert messages[4] == {"type": "llm", "emotion": "happy", "session_id": "s_1"}
    assert ws.sent[5] == b"opus"
    assert messages[5]["state"] == "stop"
