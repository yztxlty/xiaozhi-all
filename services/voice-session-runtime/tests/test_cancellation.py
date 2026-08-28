from voice_session.core.cancellation import CancellationScope


def test_parent_cancellation_propagates_to_all_descendants() -> None:
    session_scope = CancellationScope("session")
    turn_scope = session_scope.child("turn")
    generation_scope = turn_scope.child("generation")

    session_scope.cancel()

    assert session_scope.cancelled
    assert turn_scope.cancelled
    assert generation_scope.cancelled


def test_cancelled_scope_rejects_new_child() -> None:
    scope = CancellationScope("generation")
    scope.cancel()

    try:
        scope.child("provider")
    except RuntimeError as exc:
        assert "已取消" in str(exc)
    else:
        raise AssertionError("已取消的作用域不应创建子作用域")
