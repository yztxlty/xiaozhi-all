import pytest

from model_router.cli import create_http_client, event_to_json, parse_json_object
from model_router.contracts import LLMRequest, LLMTextDelta


def test_cli_json_keeps_chinese_text() -> None:
    request = LLMRequest(
        session_id="s_1",
        turn_id="t_1",
        generation_id="g_1",
        user_id="usr_1",
        user_text="你好",
        role_profile={"name": "幽光"},
    )
    event = LLMTextDelta.from_request(request, "dify", 1, "我在")

    rendered = event_to_json(event)

    assert '"text":"我在"' in rendered
    assert "\\u6211" not in rendered


def test_cli_requires_json_object() -> None:
    assert parse_json_object('{"name":"幽光"}', "角色") == {"name": "幽光"}
    with pytest.raises(ValueError, match="角色必须是 JSON 对象"):
        parse_json_object("[]", "角色")


@pytest.mark.asyncio
async def test_cli_does_not_inherit_desktop_proxy(monkeypatch) -> None:
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")

    client = create_http_client()

    await client.aclose()
