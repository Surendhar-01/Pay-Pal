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


async def check_ai_health(
    settings: Optional[Settings] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> ServiceStatus:
    """Probe Ollama and report model availability without exposing credentials."""
    settings = settings or get_settings()
    provider = (settings.AI_PROVIDER or "").strip().lower()
    model = (settings.OLLAMA_MODEL or "").strip()
    base_url = (settings.OLLAMA_BASE_URL or "").strip()
    safe_base = safe_ollama_base_url(base_url)

    if provider != "ollama":
        return ServiceStatus(
            status="unconfigured",
            message=f"Unsupported AI provider configuration: '{provider or 'unset'}'.",
        )
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
