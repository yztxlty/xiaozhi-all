from __future__ import annotations

import asyncio


class CancellationScope:
    """可分层传播的本地取消作用域。"""

    def __init__(self, name: str, parent: CancellationScope | None = None) -> None:
        if not name.strip():
            raise ValueError("取消作用域名称不能为空")
        self.name = name
        self.parent = parent
        self._event = asyncio.Event()
        self._children: list[CancellationScope] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def child(self, name: str) -> CancellationScope:
        if self.cancelled:
            raise RuntimeError("已取消的作用域不能创建子作用域")
        child = CancellationScope(name, parent=self)
        self._children.append(child)
        return child

    def cancel(self) -> None:
        if self.cancelled:
            return
        self._event.set()
        for child in tuple(self._children):
            child.cancel()

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError(f"取消作用域已终止：{self.name}")
