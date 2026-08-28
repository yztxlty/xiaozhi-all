from __future__ import annotations


class DifyClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


def error_from_status(status_code: int) -> DifyClientError:
    if status_code == 429:
        return DifyClientError(
            "DIFY_RATE_LIMITED",
            "Dify 请求受到限流",
            retryable=True,
            status_code=status_code,
        )
    if status_code in {502, 503, 504}:
        return DifyClientError(
            "DIFY_UPSTREAM_FAILED",
            "Dify 上游服务暂时不可用",
            retryable=True,
            status_code=status_code,
        )
    if status_code in {401, 403}:
        return DifyClientError(
            "DIFY_AUTH_FAILED",
            "Dify 鉴权失败",
            retryable=False,
            status_code=status_code,
        )
    if status_code in {400, 404, 422}:
        return DifyClientError(
            "DIFY_BAD_REQUEST",
            "Dify 请求与工作流契约不匹配",
            retryable=False,
            status_code=status_code,
        )
    return DifyClientError(
        "DIFY_HTTP_ERROR",
        "Dify 返回异常状态",
        retryable=False,
        status_code=status_code,
    )
