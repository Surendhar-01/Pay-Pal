import asyncio
import json
import time
from http import HTTPStatus
from typing import Dict, List, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.ai import AIConversationRequest, AIConversationResponse
from app.services.ai.ai_factory import create_ai_provider
from app.services.ai.base import (
    AIProviderConfigurationError,
    AIProviderInvalidResponse,
    AIProviderResourceError,
    AIProviderTimeout,
    AIProviderUnavailable,
)
from app.services.ai.conversation_service import ConversationService
from app.services.ai.ollama_provider import OllamaProvider

router = APIRouter()

_MAX_REQUEST_BYTES = 32 * 1024
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMIT_REQUESTS = 10
_MAX_TRACKED_CLIENTS = 10_000


class RequirementTranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=2000)
    language: Literal["ta-IN", "hi-IN"]


class RequirementTranslationResponse(BaseModel):
    translated_text: str


class _ConversationRateLimiter:
    """Small per-process limiter for the anonymous conversation endpoint."""

    def __init__(self) -> None:
        self._requests: Dict[str, List[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, client_key: str) -> bool:
        now = time.monotonic()
        cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
        async with self._lock:
            for key, timestamps in list(self._requests.items()):
                active = [timestamp for timestamp in timestamps if timestamp > cutoff]
                if active:
                    self._requests[key] = active
                else:
                    del self._requests[key]

            timestamps = self._requests.get(client_key)
            if timestamps is None:
                if len(self._requests) >= _MAX_TRACKED_CLIENTS:
                    return False
                timestamps = []
                self._requests[client_key] = timestamps
            if len(timestamps) >= _RATE_LIMIT_REQUESTS:
                return False
            timestamps.append(now)
            return True


_rate_limiter = _ConversationRateLimiter()


async def _parse_conversation_request(request: Request) -> AIConversationRequest:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_REQUEST_BYTES:
                raise HTTPException(
                    status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    detail="Conversation request exceeds the maximum size.",
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length header.",
            ) from exc

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_REQUEST_BYTES:
            raise HTTPException(
                status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                detail="Conversation request exceeds the maximum size.",
            )

    try:
        return AIConversationRequest.model_validate_json(bytes(body))
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="Invalid conversation request.",
        ) from exc


@router.post(
    "/conversation",
    response_model=AIConversationResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": AIConversationRequest.model_json_schema(),
                }
            },
        }
    },
)
async def continue_conversation(request: Request) -> AIConversationResponse:
    payload = await _parse_conversation_request(request)
    client_key = request.client.host if request.client else "unknown"
    if not await _rate_limiter.allow(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many conversation requests. Please try again shortly.",
        )

    try:
        provider = create_ai_provider()
        return await ConversationService(provider).respond(payload)
    except AIProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI conversation is not configured.",
        ) from exc
    except AIProviderTimeout as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The AI provider took too long to respond. Please try again.",
        ) from exc
    except AIProviderResourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except AIProviderUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The AI provider is unavailable. Please try again later.",
        ) from exc
    except AIProviderInvalidResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.public_message
            or "The AI provider returned an invalid response. Please try again.",
        ) from exc


@router.post(
    "/translate-requirement",
    response_model=RequirementTranslationResponse,
)
async def translate_requirement(
    payload: RequirementTranslationRequest,
    request: Request,
) -> RequirementTranslationResponse:
    client_key = request.client.host if request.client else "unknown"
    if not await _rate_limiter.allow(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many AI requests. Please try again shortly.",
        )

    try:
        provider = create_ai_provider()
        if not isinstance(provider, OllamaProvider):
            raise AIProviderConfigurationError(
                "Requirement translation requires the configured Ollama provider."
            )
        translated_text = await provider.translate_requirement(
            payload.text,
            payload.language,
        )
        return RequirementTranslationResponse(translated_text=translated_text)
    except AIProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI translation is not configured.",
        ) from exc
    except AIProviderTimeout as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Translation took too long. Please retry.",
        ) from exc
    except AIProviderResourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except AIProviderUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Translation is temporarily unavailable. Please retry.",
        ) from exc
    except AIProviderInvalidResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.public_message
            or "The requirement could not be translated. Please retry.",
        ) from exc
