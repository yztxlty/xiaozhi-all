from realtime_server.ota_server import build_ota_response, verify_device_token


def test_ota_response_contains_only_device_connection_configuration():
    response = build_ota_response(
        device_id="AA:BB:CC:DD:EE:FF",
        client_id="client-1",
        user_agent="xiaozhi/1.0",
        language="zh-CN",
    )

    assert response["websocket"]["url"].endswith("/xiaozhi/v1/ws")
    assert response["websocket"]["version"] == 1
    assert response["websocket"]["token"]
    assert "api_key" not in str(response).lower()


def test_ota_response_rejects_missing_identity():
    try:
        build_ota_response(device_id="", client_id="client-1", user_agent="", language="zh-CN")
    except ValueError as exc:
        assert "设备" in str(exc)
    else:
        raise AssertionError("missing device identity must fail")


def test_ota_token_can_be_verified_without_exposing_secret():
    response = build_ota_response(device_id="AA:BB", client_id="c1", user_agent="", language="")
    token = response["websocket"]["token"]
    assert verify_device_token("AA:BB", "c1", token)
    assert not verify_device_token("AA:BC", "c1", token)
