from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[2]


def _imports_under(directory: Path) -> set[str]:
    imported: set[str] = set()
    for source_file in directory.rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    return imported


def test_required_architecture_packages_are_importable() -> None:
    import audio_codec
    import provider_contracts
    import realtime_protocol
    import xiaozhi_observability

    assert realtime_protocol.PROTOCOL_VERSION == 1
    assert audio_codec.DEVICE_AUDIO.sample_rate_hz == 16000
    assert provider_contracts.ProviderCapability
    assert xiaozhi_observability.TraceContext


def test_stable_packages_do_not_depend_on_apps_gateways_or_services() -> None:
    forbidden_prefixes = ("realtime_server", "xiaozhi_gateway", "voice_session", "model_router", "speech_router")

    for package_dir in (ROOT / "packages").glob("*/src"):
        violations = {
            imported
            for imported in _imports_under(package_dir)
            if imported.startswith(forbidden_prefixes)
        }
        assert not violations, f"{package_dir} 存在反向依赖：{sorted(violations)}"


def test_voice_session_core_has_no_framework_or_provider_dependency() -> None:
    core = ROOT / "services/voice-session-runtime/src/voice_session/core"
    imports = _imports_under(core)
    forbidden = {
        name
        for name in imports
        if name.startswith(("pipecat", "httpx", "model_router.providers", "speech_router.providers"))
    }
    assert not forbidden, f"会话核心依赖了基础设施：{sorted(forbidden)}"


def test_gateway_has_no_concrete_provider_dependency() -> None:
    gateway = ROOT / "gateways/xiaozhi-websocket/src/xiaozhi_gateway"
    imports = _imports_under(gateway)
    forbidden = {
        name
        for name in imports
        if name.startswith(("model_router.providers", "speech_router.providers", "pipecat"))
    }
    assert not forbidden, f"协议网关依赖了具体实现：{sorted(forbidden)}"
