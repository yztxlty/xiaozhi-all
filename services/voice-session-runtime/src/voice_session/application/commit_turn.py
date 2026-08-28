from __future__ import annotations

from voice_session.core.turn import PlaybackLedger


def committed_assistant_text(ledger: PlaybackLedger) -> str:
    """返回终端确认播放过的文本，未播放尾部不得写入历史。"""

    return ledger.heard_text
