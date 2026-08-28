from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

import httpx

from .application import create_dify_chatflow_router
from .contracts import LLMCancelled, LLMFailed, LLMRequest, LLMStreamEvent
from .providers.dify_chatflow.config import DifyChatflowSettings


def parse_json_object(value: str, field_name: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name}必须是 JSON 对象")
    return parsed


def parse_json_list(value: str, field_name: str) -> list[dict[str, Any]]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ValueError(f"{field_name}必须是 JSON 对象数组")
    return parsed


def event_to_json(event: LLMStreamEvent) -> str:
    return json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def create_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="调用无状态 Dify Chatflow 并输出流式事件")
    parser.add_argument("文本", help="本轮用户文本")
    parser.add_argument("--用户标识", default="本地验证用户")
    parser.add_argument("--设备标识", default=None)
    parser.add_argument("--角色", default='{"name":"幽光","persona":"温暖、自然"}')
    parser.add_argument("--短期上下文", default="[]")
    parser.add_argument("--长期记忆", default="[]")
    parser.add_argument("--场景", choices=["companion_chat", "knowledge_qa", "tool_task"], default="companion_chat")
    return parser


async def run(args: argparse.Namespace) -> int:
    settings = DifyChatflowSettings()
    request = LLMRequest(
        session_id=f"s_{uuid.uuid4().hex}",
        turn_id=f"t_{uuid.uuid4().hex}",
        generation_id=f"g_{uuid.uuid4().hex}",
        user_id=args.用户标识,
        device_id=args.设备标识,
        user_text=args.文本,
        role_profile=parse_json_object(args.角色, "角色"),
        short_history=parse_json_list(args.短期上下文, "短期上下文"),
        long_memories=parse_json_list(args.长期记忆, "长期记忆"),
        scene=args.场景,
    )
    cancel_event = asyncio.Event()
    exit_code = 0
    async with create_http_client() as http:
        router = create_dify_chatflow_router(settings, http)
        async for event in router.stream(request, cancel_event):
            print(event_to_json(event), flush=True)
            if isinstance(event, LLMFailed):
                exit_code = 2
            elif isinstance(event, LLMCancelled):
                exit_code = 130
    return exit_code


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
