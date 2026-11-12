"""Dependency probe for the configured AI provider."""

import time
from typing import Optional

import httpx

from app.core.config import Settings, get_settings
from app.schemas.health import ServiceStatus
from app.services.ai.diagnostics import (
    loopback_in_deployed_environment,
    ollama_request_headers,
    safe_ollama_base_url,
)


def _model_is_installed(models: object, configured_model: str) -> bool:
    if not isinstance(models, list):
        return False
    target = configured_model.strip()
    names = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        for key in ("name", "model"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                names.add(value.strip())
    if target in names:
        return True
    if ":" not in target and f"{target}:latest" in names:
        return True
    return False


def _groq_base_url(settings: Settings) -> str:
    """Return the Groq base URL with a trailing slash stripped."""
    return (settings.GROQ_BASE_URL or "").strip().rstrip("/")


async def _check_groq_health(
    settings: Settings,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> ServiceStatus:
    """Probe the Groq API and report configuration validity without exposing credentials."""
    api_key = (settings.GROQ_API_KEY or "").strip()
    model = (settings.GROQ_MODEL or "").strip()
    base_url = _groq_base_url(settings)

    if not api_key:
        return ServiceStatus(
            status="unconfigured",
            message="No Groq API key is configured.",
        )
    if not model:
        return ServiceStatus(
            status="unconfigured",
            message="No Groq model is configured.",
        )
    if not base_url:
        return ServiceStatus(
            status="unconfigured",
            message="No Groq base URL is configured.",
        )

    timeout_seconds = settings.OLLAMA_HEALTH_TIMEOUT_SECONDS
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=timeout_seconds),
            transport=transport,
        ) as client:
            response = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.TimeoutException:
        return ServiceStatus(
            status="unavailable",
            message=(
                f"Groq did not respond within {timeout_seconds:g}s at {base_url}."
            ),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except httpx.RequestError as exc:
        return ServiceStatus(
            status="unavailable",
            message=f"Groq is unreachable at {base_url} ({type(exc).__name__}).",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    if response.status_code in {401, 403}:
        return ServiceStatus(
            status="unconfigured",
            message="Groq API key is invalid or unauthorized.",
            latency_ms=latency_ms,
        )
    if response.status_code != 200:
        return ServiceStatus(
            status="unavailable",
            message=(
                f"Groq returned HTTP {response.status_code} at {base_url} "
                "for the model listing."
            ),
            latency_ms=latency_ms,
        )

    try:
        payload = response.json()
    except ValueError:
        return ServiceStatus(
            status="unavailable",
            message=f"Groq returned an invalid model listing at {base_url}.",
            latency_ms=latency_ms,
        )

    if not isinstance(payload, dict):
        return ServiceStatus(
            status="unavailable",
            message=f"Groq returned an invalid model listing at {base_url}.",
            latency_ms=latency_ms,
        )

    models = payload.get("data")
    if not isinstance(models, list):
        return ServiceStatus(
            status="unavailable",
            message=f"Groq returned an invalid model listing at {base_url}.",
            latency_ms=latency_ms,
        )
    available = {
        item.get("id")
        for item in models
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if model and model not in available:
        return ServiceStatus(
            status="degraded",
            message=(
                f"Groq is reachable at {base_url}, but model '{model}' is not available."
            ),
            latency_ms=latency_ms,
        )

    return ServiceStatus(
        status="operational",
        message=f"Groq is reachable at {base_url}; model '{model}' is available.",
        latency_ms=latency_ms,
    )


async def check_ai_health(
    settings: Optional[Settings] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> ServiceStatus:
    """Probe the configured AI provider and report dependency health."""
    settings = settings or get_settings()
    provider = (settings.AI_PROVIDER or "").strip().lower()

    if provider == "ollama":
        model = (settings.OLLAMA_MODEL or "").strip()
        base_url = (settings.OLLAMA_BASE_URL or "").strip()
        safe_base = safe_ollama_base_url(base_url)

        if not model:
            return ServiceStatus(
                status="unconfigured",
                message="No Ollama model is configured.",
            )
        if not base_url:
            return ServiceStatus(
                status="unconfigured",
                message="No Ollama base URL is configured.",
            )
        if loopback_in_deployed_environment(settings):
            return ServiceStatus(
                status="unavailable",
                message=(
                    f"OLLAMA_BASE_URL targets loopback ({safe_base}), which a deployed "
                    "backend cannot reach. Configure a reachable Ollama endpoint."
                ),
            )

        timeout_seconds = settings.OLLAMA_HEALTH_TIMEOUT_SECONDS
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_seconds, connect=timeout_seconds),
                transport=transport,
            ) as client:
                response = await client.get(
                    f"{base_url.rstrip('/')}/api/tags",
                    headers=ollama_request_headers(settings),
                )
        except httpx.TimeoutException:
            return ServiceStatus(
                status="unavailable",
                message=(
                    f"Ollama did not respond within {timeout_seconds:g}s at {safe_base}."
                ),
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except httpx.RequestError as exc:
            return ServiceStatus(
                status="unavailable",
                message=(
                    f"Ollama is unreachable at {safe_base} ({type(exc).__name__})."
                ),
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if response.status_code != 200:
            return ServiceStatus(
                status="unavailable",
                message=(
                    f"Ollama returned HTTP {response.status_code} at {safe_base} "
                    "for the model listing."
                ),
                latency_ms=latency_ms,
            )

        try:
            payload = response.json()
        except ValueError:
            return ServiceStatus(
                status="unavailable",
                message=f"Ollama returned an invalid model listing at {safe_base}.",
                latency_ms=latency_ms,
            )

        if not isinstance(payload, dict):
            return ServiceStatus(
                status="unavailable",
                message=f"Ollama returned an invalid model listing at {safe_base}.",
                latency_ms=latency_ms,
            )

        models = payload.get("models")
        if not isinstance(models, list):
            return ServiceStatus(
                status="unavailable",
                message=f"Ollama returned an invalid model listing at {safe_base}.",
                latency_ms=latency_ms,
            )
        if not _model_is_installed(models, model):
            return ServiceStatus(
                status="degraded",
                message=(
                    f"Ollama is reachable at {safe_base}, but model '{model}' is not installed."
                ),
                latency_ms=latency_ms,
            )

        return ServiceStatus(
            status="operational",
            message=f"Ollama is reachable at {safe_base}; model '{model}' is available.",
            latency_ms=latency_ms,
        )

    if provider == "groq":
        return await _check_groq_health(settings, transport)

    return ServiceStatus(
        status="unconfigured",
        message=f"Unsupported AI provider configuration: '{provider or 'unset'}'.",
    )
