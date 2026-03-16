"""Pydantic schemas for API requests / responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateChatRequest(BaseModel):
    title: str | None = None


class UpdateChatRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=6000)


class ChatSummary(BaseModel):
    id: str
    title: str
    pinned: bool
    archived: bool
    created_at: str
    updated_at: str
    last_message_preview: str | None = None


class MessageRecord(BaseModel):
    id: str
    chat_id: str
    role: str
    content: str
    created_at: str
    metadata: dict[str, Any] | None = None


class SendMessageResponse(BaseModel):
    chat: ChatSummary
    user_message: MessageRecord
    assistant_message: MessageRecord
    matched_inquiry_id: str | None = None
    matched_title: str | None = None
    confidence: float | None = None


class ReasoningPreviewResponse(BaseModel):
    summary: str
    details: list[str]


class QueryRequest(BaseModel):
    question: str
    session_id: str = "default"


class ClearSessionRequest(BaseModel):
    session_id: str
