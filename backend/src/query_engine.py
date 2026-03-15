"""Query engine — orchestrates SQL agent with session memory."""

from __future__ import annotations

import os
import re
import json

from .database import Database
from .sql_agent import get_sql_agent
from .semantic_mapper import normalize_question
from .session_memory import save_interaction, resolve_references, get_history, clear_session as clear_session_store, extract_entities_from_result
from .config import METADATA_CATALOG_PATH

db: Database | None = None
sql_agent = None
session_memory: dict = {}


def _get_db() -> Database:
    global db
    if db is None:
        db = Database()
    return db


def _coerce(result):
    if isinstance(result, dict):
        p = dict(result)
        p.setdefault("success", True)
        p.setdefault("response", str(result))
        return p
    return {"success": True, "response": str(result), "sql": ""}


def _get_last_df(agent):
    try:
        return agent.last_result_df if agent else None
    except Exception:
        return None


def _reset_df(agent):
    if agent is None:
        return
    try:
        if hasattr(agent, "sql_db") and hasattr(agent.sql_db, "last_result_df"):
            agent.sql_db.last_result_df = None
    except Exception:
        pass


def _extract_hcp_ids(df):
    try:
        import pandas as pd
    except Exception:
        return set()
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return set()
    for col in df.columns:
        if re.search(r'^hcp[\s_]*id$', str(col), re.IGNORECASE):
            return {str(v).strip() for v in df[col].dropna().astype(str).tolist() if str(v).strip()}
    return set()


def _check_conformance(df, expected):
    expected = {str(v).strip() for v in (expected or []) if str(v).strip()}
    if not expected:
        return True, [], []
    actual = _extract_hcp_ids(df)
    return len(expected - actual) == 0 and len(actual - expected) == 0, sorted(expected - actual), sorted(actual - expected)


def _metadata_needs_refresh() -> bool:
    if not METADATA_CATALOG_PATH.exists():
        return True
    try:
        metadata = json.loads(METADATA_CATALOG_PATH.read_text(encoding="utf-8"))
        catalog_tables = {t["table_name"] for t in metadata.get("tables", []) if t.get("table_name")}
    except Exception:
        return True
    current = set(_get_db().list_tables()) - {"temp_df"}
    return not current.issubset(catalog_tables)


def process_query(question: str, session_id: str, steward_steps: list[str] | None = None) -> dict:
    global sql_agent
    memory_applied = False
    memory_retry = False
    resolved_ents = {}

    if _metadata_needs_refresh():
        from .metadata_builder import MetadataBuilder
        try:
            MetadataBuilder(_get_db()).build()
        except Exception as e:
            return {"response": f"Metadata generation failed: {e}", "success": False}

    if sql_agent is None:
        try:
            sql_agent = get_sql_agent(_get_db())
        except Exception as e:
            return {"response": f"SQL Agent init failed: {e}", "success": False}

    if session_id not in session_memory:
        session_memory[session_id] = {"history": [], "context": {}}
    mem = session_memory[session_id]
    mem["history"].append({"role": "user", "content": question})

    enriched = question
    # Playbook steward step injection is intentionally disabled.
    # SQL generation should rely on the static guide + schema only.
    try:
        resolution = resolve_references(session_id, question)
        if resolution.get("ambiguous") and resolution.get("clarification_question"):
            cq = resolution["clarification_question"]
            mem["history"].append({"role": "assistant", "content": cq})
            return {"response": cq, "success": True, "memory_applied": False, "memory_retry_used": False}
        elif resolution.get("context_hint"):
            memory_applied = True
            resolved_ents = resolution.get("resolved_entities", {}) or {}
            enriched = question + resolution["context_hint"]
            sample_md = mem.get("context", {}).get("last_result_sample_md")
            if sample_md:
                enriched += f"\n\n[PREVIOUS RESULT SAMPLE]:\n{sample_md}\nPreserve these columns."
    except Exception:
        pass

    try:
        normalized = normalize_question(enriched)
    except Exception:
        normalized = enriched

    try:
        _reset_df(sql_agent)
        result = sql_agent.query(normalized)
    except Exception as e:
        return {"response": f"Error: {e}", "success": False}

    payload = _coerce(result)
    response_text = str(payload.get("response", ""))

    # Conformance check
    if memory_applied and isinstance(resolved_ents.get("hcp_ids"), list) and payload.get("success", True):
        current_df = _get_last_df(sql_agent)
        ok, missing, unexpected = _check_conformance(current_df, resolved_ents.get("hcp_ids"))
        if not ok:
            memory_retry = True
            # One retry
            try:
                _reset_df(sql_agent)
                retry = sql_agent.query(normalized + f"\n[RETRY]: Missing IDs: {missing[:30]}")
                rp = _coerce(retry)
                if rp.get("success", True):
                    payload = rp
                    response_text = str(payload.get("response", ""))
            except Exception:
                pass

    mem["history"].append({"role": "assistant", "content": response_text})

    current_df = _get_last_df(sql_agent)
    if current_df is not None:
        try:
            mem["context"]["last_result_sample_md"] = current_df.head(5).to_markdown(index=False)
        except Exception:
            pass

    entities = {}
    if current_df is not None:
        try:
            entities = extract_entities_from_result(current_df)
        except Exception:
            pass

    result_meta = {"response_length": len(response_text)}
    if current_df is not None:
        try:
            result_meta["columns"] = list(current_df.columns)
            result_meta["row_count"] = int(len(current_df))
        except Exception:
            pass

    generated_sql = str(payload.get("sql", "") or "") if isinstance(payload, dict) else ""
    try:
        save_interaction(
            session_id=session_id, user_query=question, generated_sql=generated_sql,
            result_meta=result_meta, key_entities=entities,
            summary=f"Query: '{question[:80]}' Response: {len(response_text)} chars.",
        )
    except Exception:
        pass

    payload["response"] = response_text
    payload["memory_applied"] = memory_applied
    payload["memory_retry_used"] = memory_retry
    return payload


def clear_session(session_id: str) -> dict:
    session_memory.pop(session_id, None)
    try:
        clear_session_store(session_id)
    except Exception:
        pass
    return {"success": True, "message": f"Session {session_id} cleared"}


def get_session_history(session_id: str) -> dict:
    in_mem = session_memory.get(session_id, {"history": [], "context": {}})
    try:
        in_mem["persistent_history"] = get_history(session_id, n=10)
    except Exception:
        pass
    return in_mem
