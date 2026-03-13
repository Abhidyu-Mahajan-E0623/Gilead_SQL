"""FastAPI application — unified backend for the Gilead Field Rep Chatbot."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import (
    load_settings, DB_PATH, PLAYBOOK_PATH, EMBED_CACHE_PATH,
    METADATA_CATALOG_PATH, DATA_DIR,
)
from .azure_client import AzureOpenAIClient
from .chat_store import ChatStore
from .database import Database
from .metadata_builder import MetadataBuilder
from .playbook import PlaybookIndex
from .responder import ChatResponder
from .schemas import (
    CreateChatRequest, UpdateChatRequest, SendMessageRequest,
    ClearSessionRequest,
)
from .utils import auto_title_from_message
from .query_engine import process_query as sql_process_query, clear_session, get_session_history

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

# ── Global singletons ────────────────────────────────────────────────────────
settings: Any = None
azure_client: AzureOpenAIClient | None = None
chat_store: ChatStore | None = None
playbook_index: PlaybookIndex | None = None
responder: ChatResponder | None = None
database: Database | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings, azure_client, chat_store, playbook_index, responder, database

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

    # Init playbook index
    try:
        playbook_index = PlaybookIndex(
            playbook_path=PLAYBOOK_PATH,
            azure_client=azure_client,
            embedding_cache_path=EMBED_CACHE_PATH,
        )
        LOGGER.info("Playbook loaded: %d inquiries", len(playbook_index.inquiries))
    except FileNotFoundError:
        LOGGER.warning("Playbook file not found at %s", PLAYBOOK_PATH)

    # Init responder
    if playbook_index:
        responder = ChatResponder(index=playbook_index, azure_client=azure_client)

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
    description="Unified chatbot combining playbook Q&A with SQL data retrieval.",
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
        "playbook_loaded": playbook_index is not None,
        "playbook_inquiries": len(playbook_index.inquiries) if playbook_index else 0,
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

    # Try the unified responder first (playbook + SQL hybrid)
    if responder and chat_store:
        try:
            chat = chat_store.get_chat(session_id)
            if not chat:
                chat_store.create_chat(title=auto_title_from_message(question), chat_id=session_id)
            
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
        result = responder.generate_answer(
            user_message=body.content,
            conversation_history=history,
            chat_id=chat_id,
        )
        answer_text = result.content
        metadata = {
            "matched_inquiry_id": result.matched_inquiry_id,
            "matched_title": result.matched_title,
            "confidence": result.confidence,
        }

    assistant_msg = chat_store.add_message(chat_id, "assistant", answer_text, metadata)

    if c["title"] == "New Chat":
        chat_store.update_chat(chat_id, title=auto_title_from_message(body.content))

    return {
        "chat": chat_store.get_chat(chat_id),
        "user_message": user_msg,
        "assistant_message": assistant_msg,
        **metadata,
    }


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
    if not playbook_index:
        raise HTTPException(503, "Playbook not loaded")
    categories = {}
    for inq in playbook_index.inquiries:
        categories.setdefault(inq.category, 0)
        categories[inq.category] += 1
    return {
        "total_inquiries": len(playbook_index.inquiries),
        "categories": categories,
        "semantic_search_enabled": playbook_index._semantic_enabled,
    }
