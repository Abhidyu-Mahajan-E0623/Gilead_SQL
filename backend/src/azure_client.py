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

    def generate_reasoning_steps(
        self,
        *,
        user_message: str,
        focus_description: str = "",
    ) -> list[str]:
        """Ask the LLM for 3-6 short reasoning steps describing its analysis approach.

        Returns an empty list on any failure so callers can fall back gracefully.
        """
        if not self._client:
            return []

        import time as _time
        seed_hint = f" (seed={int(_time.time()) % 9973})"
        focus_hint = f" The query focuses on: {focus_description}." if focus_description else ""
        system_prompt = (
            "You are an internal reasoning engine for a CRM support chatbot. "
            "Given the user query, output 3 to 6 short sentences (one per line) "
            "describing the analytical steps you would take to answer it. "
            "Each line should start with 'I am' and describe a concrete verification step "
            "(e.g. checking SQL data, verifying identifiers, cross-referencing records). "
            "Vary your phrasing and step order each time — never repeat the exact same wording. "
            "Do NOT answer the question itself. Do NOT use bullet points or numbering. "
            "Keep each line under 120 characters. Output only the lines, nothing else."
            + focus_hint + seed_hint
        )
        try:
            raw = self.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message.strip()},
                ],
                temperature=0.7,
                max_tokens=200,
            )
            if not raw:
                return []
            lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
            # Sanitise: drop any lines that look like the model answered the question
            return [l for l in lines if len(l) > 10 and not l.lower().startswith("sure")][:6]
        except Exception as exc:
            LOGGER.warning("Reasoning-step generation failed: %s", exc)
            return []

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
