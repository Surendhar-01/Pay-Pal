import json
import logging
from typing import Sequence
from urllib.parse import urlsplit

from groq import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.schemas.ai import AIProviderResponse, ConversationMessage, RequirementDraft
from app.services.ai.base import (
    AIProvider,
    AIProviderConfigurationError,
    AIProviderInvalidResponse,
    AIProviderResourceError,
    AIProviderTimeout,
    AIProviderUnavailable,
)
from app.services.ai.ollama_provider import CONVERSATION_SYSTEM_PROMPT
from app.services.groq_service import GroqService, groq_service

logger = logging.getLogger("markettwin.ai.groq")
_MAX_RESPONSE_CHARACTERS = 64 * 1024


class GroqProvider(AIProvider):
    """Groq adapter for the structured requirement-conversation interface."""

    def __init__(
        self,
        settings: Settings | None = None,
        service: GroqService | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        if not self._settings.GROQ_API_KEY.strip():
            raise AIProviderConfigurationError("Groq API key is not configured.")

        model = self._settings.GROQ_MODEL.strip()
        if not model:
            raise AIProviderConfigurationError("No Groq model is configured.")
        self._model = model

        base_url = self._settings.GROQ_BASE_URL.strip()
        if base_url:
            try:
                parsed_url = urlsplit(base_url)
                _ = parsed_url.port
            except ValueError as exc:
                raise AIProviderConfigurationError(
                    "The configured Groq base URL is invalid."
                ) from exc
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                raise AIProviderConfigurationError("The configured Groq base URL is invalid.")

        if service is not None:
            self._service = service
        elif self._settings is get_settings():
            self._service = groq_service
        else:
            self._service = GroqService(self._settings)

    async def converse(
        self,
        message: str,
        history: Sequence[ConversationMessage],
        requirement_draft: RequirementDraft,
    ) -> AIProviderResponse:
        messages = [
            {
                "role": "system",
                "content": (
                    f"{CONVERSATION_SYSTEM_PROMPT}\n\nCurrent structured draft:\n"
                    f"{json.dumps(requirement_draft.model_dump(), ensure_ascii=False)}\n\n"
                    "Extract all customer-stated requirements into the response draft. "
                    "Do not return empty fields when the user messages or current draft "
                    "already provide their values.\n\nResponse JSON schema:\n"
                    f"{json.dumps(AIProviderResponse.model_json_schema(), ensure_ascii=False)}"
                ),
            },
            *(
                {"role": item.role, "content": item.content}
                for item in history
            ),
            {"role": "user", "content": message},
        ]

        try:
            content = await self._service.chat_completion_async(
                messages,
                model=self._model,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
        except APITimeoutError as exc:
            raise AIProviderTimeout("The Groq provider timed out.") from exc
        except APIConnectionError as exc:
            raise AIProviderUnavailable("The Groq provider is unavailable.") from exc
        except APIStatusError as exc:
            if exc.status_code in {408, 504}:
                raise AIProviderTimeout("The Groq provider timed out.") from exc
            if exc.status_code in {401, 403, 400, 404, 422}:
                raise AIProviderConfigurationError(
                    "The configured Groq model or API settings are invalid."
                ) from exc
            if exc.status_code == 429:
                raise AIProviderResourceError(
                    "The Groq provider is temporarily rate limited or out of capacity."
                ) from exc
            if exc.status_code >= 500:
                raise AIProviderUnavailable("The Groq provider is unavailable.") from exc
            raise AIProviderUnavailable("The Groq provider rejected the request.") from exc

        if not isinstance(content, str) or not content.strip() or len(content) > _MAX_RESPONSE_CHARACTERS:
            logger.warning("Groq returned an empty or oversized structured response.")
            raise AIProviderInvalidResponse(
                "The Groq provider returned an invalid structured response.",
                public_message="The AI provider returned an invalid response. Please try again.",
            )

        try:
            return AIProviderResponse.model_validate(json.loads(content))
        except (json.JSONDecodeError, TypeError, ValidationError, ValueError) as exc:
            logger.warning("Groq returned a malformed or schema-invalid structured response.")
            raise AIProviderInvalidResponse(
                "The Groq provider returned an invalid structured response.",
                public_message="The AI provider returned an invalid response. Please try again.",
            ) from exc
