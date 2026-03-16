"""FastAPI application — unified backend for the Gilead Field Rep Chatbot."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import load_settings, DB_PATH, METADATA_CATALOG_PATH, DATA_DIR
from .azure_client import AzureOpenAIClient
from .chat_store import ChatStore
from .database import Database
from .metadata_builder import MetadataBuilder
from .responder import ChatResponder
from .schemas import (
    CreateChatRequest, UpdateChatRequest, SendMessageRequest,
    ClearSessionRequest, ReasoningPreviewResponse,
)
from .utils import auto_title_from_message
from .query_engine import process_query as sql_process_query, clear_session, get_session_history

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

# ── Global singletons ────────────────────────────────────────────────────────
settings: Any = None
azure_client: AzureOpenAIClient | None = None
chat_store: ChatStore | None = None
responder: ChatResponder | None = None
database: Database | None = None


def _refresh_chat_title(chat_id: str, user_message: str, assistant_message: str | None = None) -> dict[str, Any] | None:
    if not chat_store:
        return None

    chat = chat_store.get_chat(chat_id)
    if not chat:
        return None

    current_title = (chat.get("title") or "").strip()
    if current_title and current_title != "New Chat":
        return chat

    suggested_title = None
    if azure_client:
        suggested_title = azure_client.summarize_chat_title(
            user_message=user_message,
            assistant_message=assistant_message,
        )

    next_title = suggested_title or auto_title_from_message(user_message)
    return chat_store.update_chat(chat_id, title=next_title)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings, azure_client, chat_store, responder, database

    settings = load_settings()
    azure_client = AzureOpenAIClient(settings)
    chat_store = ChatStore(DB_PATH)

    # Init DuckDB + metadata
    database = Database()
    if not METADATA_CATALOG_PATH.exists():
        try:
            MetadataBuilder(database).build()
            LOGGER.info("Built metadata catalog")
        except Exception as e:
            LOGGER.warning("metadata build failed: %s", e)

    # Playbook-driven prompts are intentionally disabled.
    responder = ChatResponder(azure_client=azure_client)

    # Init SQL agent in background
    from .sql_agent import get_sql_agent
    try:
        get_sql_agent(database)
        LOGGER.info("SQL agent initialized")
    except Exception as e:
        LOGGER.warning("SQL agent init failed (will retry on first query): %s", e)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Backend startup complete")
    yield


app = FastAPI(
    title="Gilead Field Rep Chatbot",
    description="SQL-first chatbot using DuckDB data retrieval plus LLM answer synthesis.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "azure_configured": settings.can_use_azure if settings else False,
        "playbook_loaded": False,
        "playbook_inquiries": 0,
        "database_tables": database.list_tables() if database else [],
    }


# ── Main query endpoint (matches frontend POST /query) ───────────────────────
@app.post("/query")
async def query_endpoint(question: str, session_id: str = "default"):
    """Main chat endpoint called by the frontend.

    Returns ``{success: bool, response: str, error?: str}``
    """
    if not question or not question.strip():
        return {"success": False, "response": "", "error": "Empty question"}

    question = question.strip()

    # Try the SQL-first responder first
    if responder and chat_store:
        try:
            chat = chat_store.get_chat(session_id)
            if not chat:
                chat_store.create_chat(title="New Chat", chat_id=session_id)
            
            history = chat_store.list_messages(session_id)
            chat_store.add_message(session_id, "user", question)

            result = responder.generate_answer(
                user_message=question,
                conversation_history=history,
                chat_id=session_id,
                session_id=session_id,
            )

            chat_store.add_message(session_id, "assistant", result.content, metadata={
                "matched_inquiry_id": result.matched_inquiry_id,
                "matched_title": result.matched_title,
                "confidence": result.confidence,
            })
            _refresh_chat_title(session_id, question, result.content)

            return {"success": True, "response": result.content}
        except Exception as e:
            LOGGER.exception("Responder error")
            # Fall through to SQL-only
            pass

    # Fallback: SQL-only query
    try:
        sql_result = sql_process_query(question, session_id)
        return {
            "success": sql_result.get("success", True),
            "response": str(sql_result.get("response", "No data found.")),
            "error": sql_result.get("error"),
        }
    except Exception as e:
        LOGGER.exception("SQL query error")
        return {"success": False, "response": "", "error": str(e)}


# ── Chat CRUD ─────────────────────────────────────────────────────────────────
@app.post("/api/chats")
async def create_chat(body: CreateChatRequest = CreateChatRequest()):
    return chat_store.create_chat(body.title or "New Chat")


@app.get("/api/chats")
async def list_chats():
    return chat_store.list_chats()


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str):
    c = chat_store.get_chat(chat_id)
    if not c:
        raise HTTPException(404, "Chat not found")
    return c


@app.patch("/api/chats/{chat_id}")
async def update_chat(chat_id: str, body: UpdateChatRequest):
    c = chat_store.update_chat(chat_id, title=body.title, pinned=body.pinned, archived=body.archived)
    if not c:
        raise HTTPException(404, "Chat not found")
    return c


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    if not chat_store.delete_chat(chat_id):
        raise HTTPException(404, "Chat not found")
    return {"deleted": True}


# ── Messages ──────────────────────────────────────────────────────────────────
@app.get("/api/chats/{chat_id}/messages")
async def list_messages(chat_id: str):
    if not chat_store.get_chat(chat_id):
        raise HTTPException(404, "Chat not found")
    return chat_store.list_messages(chat_id)


@app.post("/api/chats/{chat_id}/messages")
async def send_message(chat_id: str, body: SendMessageRequest):
    c = chat_store.get_chat(chat_id)
    if not c:
        raise HTTPException(404, "Chat not found")

    history = chat_store.list_messages(chat_id)
    user_msg = chat_store.add_message(chat_id, "user", body.content)

    answer_text = "I'm unable to process your request at this time."
    metadata: dict[str, Any] = {}

    if responder:
        try:
            result = responder.generate_answer(
                user_message=body.content,
                conversation_history=history,
                chat_id=chat_id,
                session_id=chat_id,
            )
            answer_text = result.content
            metadata = {
                "matched_inquiry_id": result.matched_inquiry_id,
                "matched_title": result.matched_title,
                "confidence": result.confidence,
            }
        except Exception as e:
            import traceback
            LOGGER.exception("Responder error")
            with open("error_dump.txt", "w") as f:
                f.write(traceback.format_exc())
            answer_text = f"Error: {repr(e)}"
    assistant_msg = chat_store.add_message(chat_id, "assistant", answer_text, metadata=metadata)
    updated_chat = _refresh_chat_title(chat_id, body.content, answer_text) or chat_store.get_chat(chat_id)

    return {
        "chat": updated_chat,
        "user_message": user_msg,
        "assistant_message": assistant_msg,
        **metadata,
    }


@app.post("/api/chats/{chat_id}/reasoning-preview", response_model=ReasoningPreviewResponse)
async def reasoning_preview(chat_id: str, body: SendMessageRequest):
    c = chat_store.get_chat(chat_id)
    if not c:
        raise HTTPException(404, "Chat not found")

    history = chat_store.list_messages(chat_id)
    if responder:
        preview = responder.build_reasoning_preview(
            user_message=body.content,
            conversation_history=history,
        )
        return {
            "summary": preview.summary,
            "details": preview.details,
        }

    return {
        "summary": "Thinking through the request.",
        "details": [
            "I am reviewing the user question and the recent chat context.",
            "I am mapping the request to the relevant data checks before drafting the answer.",
        ],
    }


# ── SSE Reasoning Stream ─────────────────────────────────────────────────────
_reasoning_cooldowns: dict[str, float] = {}
_REASONING_COOLDOWN_SEC = 0.5


@app.get("/api/chats/{chat_id}/reasoning-stream")
async def reasoning_stream(
    chat_id: str,
    content: str = Query(..., min_length=1, max_length=6000),
):
    """Stream LLM-generated reasoning steps as Server-Sent Events.

    The frontend connects to this endpoint immediately after the user sends a
    message.  Each reasoning step is streamed as a separate SSE event so the
    "Thinking" panel can update in real time.
    """
    c = chat_store.get_chat(chat_id) if chat_store else None
    if not c:
        raise HTTPException(404, "Chat not found")

    # Per-chat cooldown to avoid double-fire / rate-limit abuse
    now = time.monotonic()
    last = _reasoning_cooldowns.get(chat_id, 0.0)
    if now - last < _REASONING_COOLDOWN_SEC:
        # Return an empty stream; frontend will keep its local fallback
        async def _empty():
            yield "event: done\ndata: {}\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")
    _reasoning_cooldowns[chat_id] = now

    history = chat_store.list_messages(chat_id) if chat_store else []

    async def _generate():
        try:
            if responder:
                preview = await asyncio.to_thread(
                    responder.build_reasoning_preview,
                    content,
                    history,
                )
                steps = preview.details
                total = len(steps)
                summary_payload = json.dumps({"summary": preview.summary, "total": total})
                yield f"event: summary\ndata: {summary_payload}\n\n"
                for idx, step in enumerate(steps):
                    payload = json.dumps({"step": step, "index": idx, "total": total})
                    yield f"data: {payload}\n\n"
                    await asyncio.sleep(0.12)  # 120ms gap for natural pacing
            else:
                fallback = json.dumps({"step": "Reviewing the request.", "index": 0, "total": 1})
                yield f"data: {fallback}\n\n"
        except Exception as exc:
            LOGGER.warning("SSE reasoning stream error: %s", exc)
        finally:
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Session management ───────────────────────────────────────────────────────
@app.post("/clear-session")
async def clear_session_endpoint(body: ClearSessionRequest):
    return clear_session(body.session_id)


@app.get("/session-history/{session_id}")
async def session_history(session_id: str):
    return get_session_history(session_id)


# ── Database info ─────────────────────────────────────────────────────────────
@app.get("/tables")
async def list_tables():
    if not database:
        return {"tables": []}
    tables = database.list_tables()
    return {"tables": tables, "schemas": {t: database.get_schema(t) for t in tables}}


# ── Playbook summary ─────────────────────────────────────────────────────────
@app.get("/api/playbook/summary")
async def playbook_summary():
    return {
        "enabled": False,
        "reason": "Playbook prompts are disabled. Answers are generated from user query plus SQL results only.",
        "total_inquiries": 0,
        "categories": {},
        "semantic_search_enabled": False,
    }
