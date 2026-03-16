"""Azure OpenAI client — wraps embeddings + chat completion."""

from __future__ import annotations

import logging
from typing import Sequence

from openai import AzureOpenAI

from .config import AzureSettings
from .utils import sanitize_chat_title

LOGGER = logging.getLogger(__name__)


class AzureOpenAIClient:
    def __init__(self, settings: AzureSettings) -> None:
        self.settings = settings
        self._client: AzureOpenAI | None = None

        if settings.can_use_azure:
            self._client = AzureOpenAI(
                api_key=settings.azure_openai_key,
                api_version=settings.api_version,
                azure_endpoint=settings.azure_openai_endpoint,
            )

    @property
    def is_ready(self) -> bool:
        return self._client is not None

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not self._client:
            return []
        try:
            response = self._client.embeddings.create(
                model=self.settings.embedding_deployment,
                input=list(texts),
            )
            return [record.embedding for record in response.data]
        except Exception as exc:
            LOGGER.warning("Embedding call failed: %s", exc)
            return []

    def chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 700,
    ) -> str | None:
        if not self._client:
            return None
        try:
            response = self._client.chat.completions.create(
                model=self.settings.chat_deployment,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content
            return content.strip() if content else None
        except Exception as exc:
            LOGGER.warning("Chat completion failed: %s", exc)
            return None

    def summarize_chat_title(
        self,
        *,
        user_message: str,
        assistant_message: str | None = None,
    ) -> str | None:
        if not self._client:
            return None

        assistant_preview = (assistant_message or "").strip()
        if len(assistant_preview) > 220:
            assistant_preview = assistant_preview[:220].rsplit(" ", 1)[0].strip()

        prompt = (
            "Create a concise title for this chat. "
            "Return only the title, without quotes. "
            "Use 2 to 6 words and keep it under 50 characters."
        )
        content = f"User message:\n{user_message.strip()}"
        if assistant_preview:
            content += f"\n\nAssistant response preview:\n{assistant_preview}"

        title = self.chat_completion(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": content},
            ],
            temperature=0.2,
            max_tokens=24,
        )
        return sanitize_chat_title(title) if title else None
