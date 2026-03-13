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


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def text_preview(value: str, length: int = 120) -> str:
    compact = " ".join(value.strip().split())
    return compact if len(compact) <= length else compact[: length - 3] + "..."


def normalize_text(value: str) -> str:
    return " ".join(TOKEN_PATTERN.findall(value.lower()))


def auto_title_from_message(message: str) -> str:
    tokens = TOKEN_PATTERN.findall(message.lower())
    keywords = [t for t in tokens if t not in STOPWORDS and len(t) > 2]
    chosen = keywords[:6] if keywords else tokens[:6]
    if not chosen:
        return "New Chat"
    title = " ".join(chosen).title().strip()
    if len(title) > 52:
        title = title[:52].rstrip()
    return title or "New Chat"
