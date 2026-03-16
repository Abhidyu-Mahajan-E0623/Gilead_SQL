"""Shared utility functions — merged from both source projects."""

from __future__ import annotations

import re
from datetime import datetime, timezone

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "this", "that",
    "with", "for", "from", "into", "your", "you", "are", "was", "were",
    "have", "has", "had", "how", "what", "when", "where", "why", "who",
    "about", "after", "before", "still", "does", "did", "can", "not",
    "my", "our", "rep", "doctor", "dr",
}

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9']+")
TITLE_CLEANUP_PATTERN = re.compile(r"^[\"'`]+|[\"'`]+$")


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def text_preview(value: str, length: int = 120) -> str:
    compact = " ".join(value.strip().split())
    return compact if len(compact) <= length else compact[: length - 3] + "..."


def normalize_text(value: str) -> str:
    return " ".join(TOKEN_PATTERN.findall(value.lower()))


def sanitize_chat_title(value: str, fallback: str = "New Chat") -> str:
    compact = " ".join(value.strip().split())
    compact = TITLE_CLEANUP_PATTERN.sub("", compact).strip(" .,:;|-")
    if not compact:
        return fallback

    if len(compact) <= 60:
        return compact

    shortened = compact[:60].rsplit(" ", 1)[0].strip()
    return shortened or compact[:60].strip() or fallback


def auto_title_from_message(message: str) -> str:
    original_tokens = TOKEN_PATTERN.findall(message)
    normalized_tokens = [token.lower() for token in original_tokens]
    keywords = [
        token
        for token, normalized in zip(original_tokens, normalized_tokens, strict=False)
        if normalized not in STOPWORDS and len(token) > 2
    ]
    chosen = keywords[:6] if keywords else original_tokens[:6]
    if not chosen:
        return "New Chat"
    formatted: list[str] = []
    for token in chosen:
        if token.isupper() or token.isdigit():
            formatted.append(token)
        elif len(token) <= 4 and token.upper() in {"TRX", "NRX", "HCP", "HCO", "ZIP", "Q1", "Q2", "Q3", "Q4"}:
            formatted.append(token.upper())
        else:
            formatted.append(token.capitalize())
    return sanitize_chat_title(" ".join(formatted))
