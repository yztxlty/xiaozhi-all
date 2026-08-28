from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx

from .config import DifyWorkflowSettings
from .errors import DifyClientError, error_from_status
from .event_parser import DifyEvent, DifyProtocolError, parse_sse_line


class DifyWorkflowClient:
    def __init__(
        self,
        settings: DifyWorkflowSettings,
        http: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.http = http

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key.get_secret_value()}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{str(self.settings.base_url).rstrip('/')}/{path.lstrip('/')}"

    async def _stream_once(self, payload: dict[str, object]) -> AsyncIterator[DifyEvent]:
        timeout = httpx.Timeout(
            connect=self.settings.connect_timeout_ms / 1000,
            read=self.settings.read_timeout_ms / 1000,
            write=2.0,
            pool=1.0,
        )
        try:
            async with self.http.stream(
                "POST",
                self._url("workflows/run"),
                headers=self.headers,
                json=payload,
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    raise error_from_status(response.status_code)
                content_type = response.headers.get("content-type", "").lower()
                if "text/event-stream" not in content_type:
                    raise DifyProtocolError("Dify 响应不是事件流")
                async for line in response.aiter_lines():
                    event = parse_sse_line(line)
                    if event is not None:
                        yield event
        except DifyClientError:
            raise
        except httpx.ConnectTimeout as exc:
            raise DifyClientError(
                "DIFY_CONNECT_TIMEOUT",
                "连接 Dify 超时",
                retryable=True,
            ) from exc
        except httpx.ConnectError as exc:
            raise DifyClientError(
                "DIFY_CONNECT_FAILED",
                "无法连接 Dify",
                retryable=True,
            ) from exc
        except httpx.ReadTimeout as exc:
            raise DifyClientError(
                "DIFY_READ_TIMEOUT",
                "读取 Dify 事件流超时",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise DifyClientError(
                "DIFY_STREAM_DISCONNECTED",
                "Dify 事件流连接中断",
                retryable=True,
            ) from exc

    async def stream(self, payload: dict[str, object]) -> AsyncIterator[DifyEvent]:
        retries = 0
        text_emitted = False
        task_id: str | None = None
        user = payload.get("user")

        try:
            async with asyncio.timeout(self.settings.total_timeout_ms / 1000):
                while True:
                    try:
                        async for event in self._stream_once(payload):
                            task_id = event.task_id or task_id
                            if event.event == "text_chunk":
                                text = event.data.get("text")
                                text_emitted = text_emitted or isinstance(text, str) and bool(text)
                            yield event
                        return
                    except DifyClientError as exc:
                        can_retry = (
                            exc.retryable
                            and not text_emitted
                            and retries < self.settings.max_retries_before_delta
                        )
                        if not can_retry:
                            raise
                        retries += 1
                        if task_id and isinstance(user, str) and user:
                            try:
                                await self.stop(task_id, user)
                            except Exception:
                                pass
                        task_id = None
        except TimeoutError as exc:
            raise DifyClientError(
                "DIFY_TOTAL_TIMEOUT",
                "Dify 单轮执行超过总时限",
                retryable=False,
            ) from exc

    async def stop(self, task_id: str, user: str) -> None:
        response = await self.http.post(
            self._url(f"workflows/tasks/{task_id}/stop"),
            headers=self.headers,
            json={"user": user},
            timeout=self.settings.stop_timeout_ms / 1000,
        )
        if response.status_code >= 400:
            raise error_from_status(response.status_code)
