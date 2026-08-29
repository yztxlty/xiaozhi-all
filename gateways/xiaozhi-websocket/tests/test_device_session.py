from xiaozhi_gateway.device_session import DeviceOutputAdapter


class Protocol:
    def __init__(self):
        self.calls = []

    async def send_stt(self, text): self.calls.append(("stt", text))
    async def send_tts_start(self): self.calls.append(("start",))
    async def send_tts_sentence(self, text): self.calls.append(("sentence", text))
    async def send_llm_emotion(self, value): self.calls.append(("emotion", value))
    async def send_audio(self, value): self.calls.append(("audio", value))
    async def send_tts_stop(self): self.calls.append(("stop",))
    async def send_standby(self): self.calls.append(("standby",))


async def test_device_output_maps_runtime_events_to_firmware_events():
    protocol = Protocol()
    output = DeviceOutputAdapter(protocol)

    await output.json({"type": "asr.final", "text": "你好"})
    await output.json({"type": "assistant.emotion", "emotion": "happy"})
    await output.json({"type": "llm.text.delta", "text": "你好呀"})
    await output.pcm(b"\x00\x00" * 960)
    await output.json({"type": "tts.done"})

    assert protocol.calls[:4] == [
        ("stt", "你好"),
        ("emotion", "happy"),
        ("sentence", "你好呀"),
        ("start",),
    ]
    assert protocol.calls[-1] == ("stop",)


async def test_device_output_aggregates_llm_deltas_into_sentences():
    protocol = Protocol()
    output = DeviceOutputAdapter(protocol)

    await output.json({"type": "llm.text.delta", "text": "你好"})
    await output.json({"type": "llm.text.delta", "text": "呀。第二句"})
    await output.pcm(b"\x00\x00" * 960)

    assert [call for call in protocol.calls if call[0] == "sentence"] == [
        ("sentence", "你好呀。"),
        ("sentence", "第二句"),
    ]


async def test_device_output_forwards_standby_command():
    protocol = Protocol()
    await DeviceOutputAdapter(protocol).json({"type": "device.standby"})
    assert protocol.calls == [("standby",)]
