import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from groq import Groq

from app.core.config import Settings, get_settings


class GroqService:
    """Reusable Groq client wrapper for AI-powered features."""

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self.api_key = self._settings.GROQ_API_KEY.strip()
        self._client: Optional[Groq] = None

    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def client(self) -> Optional[Groq]:
        if self._client is None and self.api_key:
            base_url = self._settings.GROQ_BASE_URL.strip() or None
            if base_url:
                parsed_url = urlsplit(base_url)
                path = parsed_url.path.rstrip("/")
                api_prefix = "/openai/v1"
                while path.casefold().endswith(api_prefix):
                    path = path[:-len(api_prefix)]
                base_url = urlunsplit(
                    (
                        parsed_url.scheme,
                        parsed_url.netloc,
                        path or "/",
                        parsed_url.query,
                        parsed_url.fragment,
                    )
                )
            self._client = Groq(api_key=self.api_key, base_url=base_url)
        return self._client

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        client = self.client
        if client is None:
            raise RuntimeError("Groq API key is not configured.")
        request: Dict[str, Any] = {
            "model": model or self._settings.GROQ_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            request["response_format"] = response_format
        response = client.chat.completions.create(**request)
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""

    async def chat_completion_async(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        return await asyncio.to_thread(
            self.chat_completion,
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )


groq_service = GroqService()