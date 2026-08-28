from __future__ import annotations


class ConnectionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}

    def bind(self, connection_id: str, session_id: str) -> None:
        if connection_id in self._sessions:
            raise ValueError("连接已绑定会话")
        self._sessions[connection_id] = session_id

    def session_for(self, connection_id: str) -> str | None:
        return self._sessions.get(connection_id)

    def unbind(self, connection_id: str) -> str | None:
        return self._sessions.pop(connection_id, None)
