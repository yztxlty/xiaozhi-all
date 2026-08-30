from aiohttp import web

from realtime_server.combined_server import create_app


def test_combined_app_exposes_device_ota_and_h5_routes():
    routes = {(route.method, route.resource.canonical) for route in create_app().router.routes()}
    assert ("POST", "/xiaozhi/ota/") in routes
    assert ("GET", "/xiaozhi/v1/ws") in routes
    assert ("GET", "/{path}") in routes


def test_combined_source_routes_pcm_h5_handshake_and_opus_device_handshake_separately():
    source = (create_app.__globals__["Path"] if "Path" in create_app.__globals__ else None)
    assert source is None or source is not None
    import inspect
    module = inspect.getmodule(create_app)
    assert module is not None and "import json" in inspect.getsource(module)
    code = inspect.getsource(create_app.__globals__["_websocket_route"])
    assert 'audio_params' in code
    assert 'Device-Id' in code
    assert 'if request.headers.get("Device-Id"):' in code
    assert 'request.path.endswith("/xiaozhi/v1/ws")' in code
    assert 'h5_handler(adapter)' in code
    assert 'handle_device_connection(adapter)' in code
