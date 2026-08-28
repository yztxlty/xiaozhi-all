import pytest

from voice_session.core.session import Session, SessionState, SessionTransitionError


def test_normal_session_state_flow() -> None:
    session = Session.create("session_1")

    session.ready()
    session.start_listening()
    session.commit_user_speech()
    session.start_speaking()
    session.finish_speaking()

    assert session.state is SessionState.READY


def test_session_rejects_illegal_transition() -> None:
    session = Session.create("session_1")

    with pytest.raises(SessionTransitionError, match="非法状态迁移"):
        session.start_speaking()


def test_interruption_moves_speaking_session_back_to_listening() -> None:
    session = Session.create("session_1")
    session.ready()
    session.start_listening()
    session.commit_user_speech()
    session.start_speaking()

    session.interrupt()

    assert session.state is SessionState.LISTENING
