import httpx
import pytest

from app.core.config import Settings
from app.services.ai.health import check_ai_health


def _settings(**overrides) -> Settings:
    values = {
        "AI_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://ollama.test",
        "OLLAMA_MODEL": "qwen3.5:4b",
        "ENVIRONMENT": "production",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.anyio
async def test_ai_health_is_operational_when_model_is_present():
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b"}]})

    status = await check_ai_health(
        _settings(), transport=httpx.MockTransport(handle_request)
    )

    assert status.status == "operational"
    assert "qwen3.5:4b" in status.message
    assert "ollama.test" in status.message
    assert status.latency_ms is not None


@pytest.mark.anyio
async def test_ai_health_is_degraded_when_model_is_missing():
    def handle_request(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "another-model:7b"}]})

    status = await check_ai_health(
        _settings(), transport=httpx.MockTransport(handle_request)
    )

    assert status.status == "degraded"
    assert "qwen3.5:4b" in status.message


@pytest.mark.anyio
async def test_ai_health_is_unavailable_on_http_error():
    def handle_request(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal detail"})

    status = await check_ai_health(
        _settings(), transport=httpx.MockTransport(handle_request)
    )

    assert status.status == "unavailable"
    assert "HTTP 500" in status.message
    assert "internal detail" not in status.message


@pytest.mark.anyio
async def test_ai_health_is_unavailable_on_connection_error():
    def handle_request(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    status = await check_ai_health(
        _settings(OLLAMA_API_KEY="super-secret-token"),
        transport=httpx.MockTransport(handle_request),
    )

    assert status.status == "unavailable"
    assert "ollama.test" in status.message
    assert "ConnectError" in status.message
    assert "super-secret-token" not in (status.message or "")


@pytest.mark.anyio
async def test_ai_health_is_unavailable_on_timeout():
    def handle_request(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    status = await check_ai_health(
        _settings(OLLAMA_HEALTH_TIMEOUT_SECONDS=1.5),
        transport=httpx.MockTransport(handle_request),
    )

    assert status.status == "unavailable"
    assert "1.5s" in status.message


@pytest.mark.anyio
async def test_ai_health_flags_loopback_in_deployed_environment_without_request():
    def handle_request(_: httpx.Request) -> httpx.Response:
        raise AssertionError("loopback configuration must not be probed")

    status = await check_ai_health(
        _settings(OLLAMA_BASE_URL="http://localhost:11434"),
        transport=httpx.MockTransport(handle_request),
    )

    assert status.status == "unavailable"
    assert "loopback" in status.message
    assert "cannot reach" in status.message


@pytest.mark.anyio
async def test_ai_health_sends_bearer_token_when_configured():
    seen = {}

    def handle_request(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b"}]})

    status = await check_ai_health(
        _settings(OLLAMA_API_KEY="endpoint-token"),
        transport=httpx.MockTransport(handle_request),
    )

    assert status.status == "operational"
    assert seen["authorization"] == "Bearer endpoint-token"


@pytest.mark.anyio
async def test_ai_health_reports_unsupported_provider_as_unconfigured():
    status = await check_ai_health(
        _settings(AI_PROVIDER="unknown-provider"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"models": []})
        ),
    )

    assert status.status == "unconfigured"
    assert "unknown-provider" in status.message
