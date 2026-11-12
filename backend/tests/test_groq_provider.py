import threading

import httpx
import pytest
from groq import APIStatusError, APITimeoutError
from fastapi.testclient import TestClient

from app.api.v1.endpoints import ai as ai_endpoint
from app.core.config import Settings
from app.main import app
from app.schemas.ai import AIProviderResponse, ConversationMessage, RequirementDraft
from app.services.ai.ai_factory import create_ai_provider
from app.services.ai.base import (
    AIProvider,
    AIProviderConfigurationError,
    AIProviderInvalidResponse,
    AIProviderResourceError,
    AIProviderTimeout,
    AIProviderUnavailable,
)
from app.services.ai.groq_provider import GroqProvider
from app.services.ai.ollama_provider import OllamaProvider
from app.services.ai.diagnostics import (
    loopback_in_deployed_environment,
    provider_context,
)
from app.services.groq_service import GroqService


def _settings(**values) -> Settings:
    return Settings(
        _env_file=None,
        GROQ_API_KEY="test-key",
        GROQ_MODEL="test-model",
        **values,
    )


def _response() -> AIProviderResponse:
    return AIProviderResponse(
        assistant_message="I captured the water tank requirements.",
        requirement_draft=RequirementDraft(
            product_name="Water Storage Tank",
            mandatory_requirements={"material": "SS 304"},
            commercial_constraints={"quantity": "10", "unit": "units"},
        ),
    )


def test_factory_selects_groq_and_ollama_providers():
    groq_provider = create_ai_provider(settings=_settings(AI_PROVIDER="groq"))
    ollama_provider = create_ai_provider(
        settings=Settings(_env_file=None, AI_PROVIDER="ollama")
    )

    assert isinstance(groq_provider, GroqProvider)
    assert isinstance(ollama_provider, OllamaProvider)


def test_factory_rejects_unsupported_provider():
    with pytest.raises(AIProviderConfigurationError, match="not supported"):
        create_ai_provider(settings=Settings(_env_file=None, AI_PROVIDER="other"))


def test_groq_provider_requires_api_key():
    with pytest.raises(AIProviderConfigurationError, match="API key"):
        GroqProvider(Settings(_env_file=None, GROQ_API_KEY=""))


def test_groq_provider_rejects_invalid_base_url():
    with pytest.raises(AIProviderConfigurationError, match="base URL"):
        GroqProvider(_settings(GROQ_BASE_URL="not-a-url"))


def test_groq_service_removes_sdk_api_prefix_from_configured_base_url():
    service = GroqService(
        _settings(GROQ_BASE_URL="https://api.groq.com/openai/v1/")
    )

    assert str(service.client.base_url) == "https://api.groq.com/"


def test_groq_diagnostics_use_groq_model_and_ignore_ollama_loopback():
    settings = _settings(
        AI_PROVIDER="groq",
        ENVIRONMENT="production",
        GROQ_BASE_URL="https://gateway.example/v1?token=redacted",
        OLLAMA_BASE_URL="http://localhost:11434",
    )

    assert provider_context(settings) == (
        "provider=groq, model=test-model, base=https://gateway.example"
    )
    assert not loopback_in_deployed_environment(settings)


@pytest.mark.anyio
async def test_groq_conversation_sends_history_draft_and_schema(monkeypatch):
    calls = {}

    async def fake_chat_completion(
        self,
        messages,
        *,
        model=None,
        temperature=0.7,
        max_tokens=None,
        response_format=None,
    ):
        calls.update(
            messages=messages,
            model=model,
            temperature=temperature,
            response_format=response_format,
        )
        return _response().model_dump_json()

    monkeypatch.setattr(GroqService, "chat_completion_async", fake_chat_completion)
    provider = GroqProvider(_settings())
    history = [
        ConversationMessage(role="user", content="I need storage tanks."),
        ConversationMessage(role="assistant", content="What material do you prefer?"),
    ]
    draft = RequirementDraft(
        product_name="Water Storage Tank",
        mandatory_requirements={"capacity": "1000 litres"},
    )

    result = await provider.converse("SS 304, ten units.", history, draft)

    assert AIProviderResponse.model_validate(result.model_dump()) == _response()
    assert calls["model"] == "test-model"
    assert calls["temperature"] == 0.1
    assert calls["response_format"] == {"type": "json_object"}
    assert calls["messages"][1:3] == [
        {"role": item.role, "content": item.content} for item in history
    ]
    assert calls["messages"][-1] == {
        "role": "user",
        "content": "SS 304, ten units.",
    }
    assert '"capacity": "1000 litres"' in calls["messages"][0]["content"]
    assert "Response JSON schema" in calls["messages"][0]["content"]


@pytest.mark.anyio
async def test_groq_timeout_maps_to_provider_timeout(monkeypatch):
    async def timeout(*args, **kwargs):
        request = httpx.Request("POST", "https://api.groq.com")
        raise APITimeoutError(request=request)

    monkeypatch.setattr(GroqService, "chat_completion_async", timeout)
    provider = GroqProvider(_settings())

    with pytest.raises(AIProviderTimeout):
        await provider.converse("Need a tank.", [], RequirementDraft())


@pytest.mark.anyio
async def test_groq_rate_limit_maps_to_resource_error(monkeypatch):
    async def rate_limited(*args, **kwargs):
        request = httpx.Request("POST", "https://api.groq.com")
        response = httpx.Response(429, request=request)
        raise APIStatusError("rate limited", response=response, body=None)

    monkeypatch.setattr(GroqService, "chat_completion_async", rate_limited)
    provider = GroqProvider(_settings())

    with pytest.raises(AIProviderResourceError):
        await provider.converse("Need a tank.", [], RequirementDraft())


@pytest.mark.anyio
async def test_groq_server_error_maps_to_provider_unavailable(monkeypatch):
    async def server_error(*args, **kwargs):
        request = httpx.Request("POST", "https://api.groq.com")
        response = httpx.Response(503, request=request)
        raise APIStatusError("server error", response=response, body=None)

    monkeypatch.setattr(GroqService, "chat_completion_async", server_error)
    provider = GroqProvider(_settings())

    with pytest.raises(AIProviderUnavailable):
        await provider.converse("Need a tank.", [], RequirementDraft())


@pytest.mark.anyio
async def test_groq_authentication_error_maps_to_configuration_error(monkeypatch):
    async def authentication_error(*args, **kwargs):
        request = httpx.Request("POST", "https://api.groq.com")
        response = httpx.Response(401, request=request)
        raise APIStatusError("unauthorized", response=response, body=None)

    monkeypatch.setattr(GroqService, "chat_completion_async", authentication_error)
    provider = GroqProvider(_settings())

    with pytest.raises(AIProviderConfigurationError):
        await provider.converse("Need a tank.", [], RequirementDraft())


@pytest.mark.anyio
async def test_groq_empty_response_is_rejected(monkeypatch):
    async def empty_response(*args, **kwargs):
        return " "

    monkeypatch.setattr(GroqService, "chat_completion_async", empty_response)
    provider = GroqProvider(_settings())

    with pytest.raises(AIProviderInvalidResponse):
        await provider.converse("Need a tank.", [], RequirementDraft())


@pytest.mark.anyio
async def test_groq_service_offloads_sync_sdk_call_to_worker_thread(monkeypatch):
    main_thread = threading.get_ident()
    worker_thread = []

    def fake_chat_completion(self, *args, **kwargs):
        worker_thread.append(threading.get_ident())
        return "ok"

    monkeypatch.setattr(GroqService, "chat_completion", fake_chat_completion)
    result = await GroqService(_settings()).chat_completion_async([])

    assert result == "ok"
    assert worker_thread
    assert worker_thread[0] != main_thread


def test_conversation_endpoint_logs_configured_groq_model(monkeypatch, caplog):
    class FixedProvider(AIProvider):
        async def converse(self, message, history, requirement_draft):
            return _response()

    settings = _settings(AI_PROVIDER="groq")
    settings.GROQ_MODEL = "configured-groq-model"
    monkeypatch.setattr(ai_endpoint, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_endpoint, "create_ai_provider", FixedProvider)
    ai_endpoint._rate_limiter._requests.clear()
    caplog.set_level("INFO", logger="markettwin.ai.conversation")

    result = TestClient(app).post(
        "/api/v1/ai/conversation",
        json={"message": "I need ten water tanks."},
    )

    assert result.status_code == 200
    assert "provider=groq model=configured-groq-model" in caplog.text
