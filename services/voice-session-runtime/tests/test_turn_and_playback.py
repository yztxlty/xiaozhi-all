from voice_session.core.turn import PlaybackLedger, Turn


def test_new_generation_cancels_previous_generation() -> None:
    turn = Turn.create("turn_1")
    first = turn.current_generation

    second = turn.new_generation()

    assert first.cancel_scope.cancelled
    assert second.generation_id != first.generation_id
    assert not second.cancel_scope.cancelled


def test_playback_ledger_only_commits_confirmed_segments() -> None:
    ledger = PlaybackLedger()
    ledger.enqueue(1, "你好，")
    ledger.enqueue(2, "我是幽光。")

    ledger.confirm_played(1)

    assert ledger.heard_text == "你好，"
    assert ledger.pending_text == "我是幽光。"
