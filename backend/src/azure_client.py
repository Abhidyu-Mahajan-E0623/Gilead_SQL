"""Azure OpenAI client — wraps embeddings + chat completion."""

from __future__ import annotations

import logging
from typing import Sequence

from openai import AzureOpenAI

from .config import AzureSettings

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
