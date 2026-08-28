from prometheus_client import Counter, Histogram


DIFY_TTFT_SECONDS = Histogram(
    "xiaozhi_dify_ttft_seconds",
    "Dify 首个文本增量延迟",
    ["provider"],
)
DIFY_TOTAL_SECONDS = Histogram(
    "xiaozhi_dify_total_seconds",
    "Dify 工作流总耗时",
    ["provider", "status"],
)
DIFY_REQUESTS = Counter(
    "xiaozhi_dify_requests_total",
    "Dify 工作流请求数",
    ["provider", "result"],
)
DIFY_STOP_REQUESTS = Counter(
    "xiaozhi_dify_stop_requests_total",
    "Dify 停止任务请求数",
    ["provider", "result"],
)


def safe_trace_attributes(
    *,
    provider: str,
    session_id: str,
    turn_id: str,
    generation_id: str,
    task_id: str | None,
) -> dict[str, str]:
    attributes = {
        "llm.provider": provider,
        "xiaozhi.session_id": session_id,
        "xiaozhi.turn_id": turn_id,
        "xiaozhi.generation_id": generation_id,
    }
    if task_id:
        attributes["dify.task_id"] = task_id
    return attributes
