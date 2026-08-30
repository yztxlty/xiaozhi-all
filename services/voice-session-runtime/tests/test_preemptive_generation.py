import pytest

from voice_session.application.preemptive_generation import PreemptiveGenerationCoordinator


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls = []

    async def submit_text(self, text: str) -> None:
        self.calls.append(("submit", text))

    async def interrupt(self, *, notify_client: bool = True) -> None:
        self.calls.append(("interrupt", notify_client))


@pytest.mark.asyncio
async def test_partial_transcript_starts_generation_before_audio_commit() -> None:
    runtime = RecordingRuntime()
    coordinator = PreemptiveGenerationCoordinator(runtime, stability_delay_seconds=0)

    await coordinator.update_partial("今天天气怎么样")
    await coordinator.wait_preemptive()

    assert runtime.calls == [("submit", "今天天气怎么样")]


@pytest.mark.asyncio
async def test_short_partial_does_not_start_wrong_generation() -> None:
    runtime = RecordingRuntime()
    coordinator = PreemptiveGenerationCoordinator(runtime, stability_delay_seconds=0)

    await coordinator.update_partial("你要")
    await coordinator.wait_preemptive()

    assert runtime.calls == []


def test_default_preemptive_delay_can_be_overridden_for_device_path() -> None:
    coordinator = PreemptiveGenerationCoordinator(
        RecordingRuntime(), stability_delay_seconds=0.08
    )

    assert coordinator._stability_delay_seconds == 0.08


@pytest.mark.asyncio
async def test_final_transcript_before_commit_waits_for_user_endpoint() -> None:
    runtime = RecordingRuntime()
    coordinator = PreemptiveGenerationCoordinator(runtime, stability_delay_seconds=0)

    await coordinator.finalize("你好。")
    assert runtime.calls == []

    await coordinator.commit()
    assert runtime.calls == [("submit", "你好。")]


@pytest.mark.asyncio
async def test_material_final_transcript_change_cancels_preemptive_generation() -> None:
    runtime = RecordingRuntime()
    coordinator = PreemptiveGenerationCoordinator(runtime, stability_delay_seconds=0)

    await coordinator.update_partial("播放音乐")
    await coordinator.commit()
    await coordinator.finalize("不要播放音乐")

    assert runtime.calls == [
        ("submit", "播放音乐"),
        ("interrupt", False),
        ("submit", "不要播放音乐"),
    ]


@pytest.mark.asyncio
async def test_stability_window_uses_freshest_partial_transcript() -> None:
    runtime = RecordingRuntime()
    coordinator = PreemptiveGenerationCoordinator(runtime, stability_delay_seconds=0.01)

    await coordinator.update_partial("你好幽光给我讲")
    await coordinator.commit()
    assert runtime.calls == []
    await coordinator.update_partial("你好幽光给我讲一个很短")
    assert runtime.calls == [("submit", "你好幽光给我讲一个很短")]
    await coordinator.wait_preemptive()

    assert runtime.calls == [("submit", "你好幽光给我讲一个很短")]


@pytest.mark.asyncio
async def test_final_extension_keeps_matching_preemptive_generation() -> None:
    runtime = RecordingRuntime()
    coordinator = PreemptiveGenerationCoordinator(runtime, stability_delay_seconds=0)

    await coordinator.update_partial("你好幽光给我讲一个很短")
    await coordinator.commit()
    await coordinator.finalize("你好忧光给我讲一个很短的笑话")

    assert runtime.calls == [("submit", "你好幽光给我讲一个很短")]


@pytest.mark.asyncio
async def test_trailing_wake_word_does_not_restart_preemptive_generation() -> None:
    runtime = RecordingRuntime()
    coordinator = PreemptiveGenerationCoordinator(runtime, stability_delay_seconds=0)

    await coordinator.update_partial("你好")
    await coordinator.commit()
    await coordinator.finalize("你好忧光。")

    assert runtime.calls == [("submit", "你好")]
