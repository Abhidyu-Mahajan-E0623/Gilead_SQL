"""Session memory — SQLite-backed context persistence for cross-query entity resolution."""

import os
import json
import re
import sqlite3
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone

from .config import SESSION_STORE_DIR

DB_PATH = os.path.join(str(SESSION_STORE_DIR), "session_store.sqlite")
MAX_HISTORY = 20

REFERENCE_PATTERNS = [
    r'\b(their|them|those|these|the same)\b',
    # Keep "last" only for explicit record/result references, not time phrases like "last quarter".
    r'\b(the (?:above|previous|mentioned|selected))\b',
    r'\b(the last (?:result|query|output|record|provider|hcp|account|territory|one|same one|list|row))\b',
    r'\b((?:same|corresponding|related|associated)\s+\w+)\b',
    r'\b(previous output|previous result|last result|above list|same list)\b',
    r'\b(that (?:record|provider|hcp|account|territory|one|same one))\b',
    r'\b(its (?:volume|credit|territory|status|details|alignment|mapping))\b',
]
REFERENCE_RE = re.compile('|'.join(REFERENCE_PATTERNS), re.IGNORECASE)
EXPLICIT_ID_RE = re.compile(
    r"\bNPI\b\s*[:#-]?\s*\d{10}\b|\b\d{10}\b|\bHCO[-_][A-Z0-9_-]+\b|\bHCP[-_][A-Z0-9_-]+\b|"
    r"\b[A-Z]{2,5}-\d{2,4}\b",
    re.IGNORECASE,
)

ENTITY_KEYWORDS = {
    'hcp': ['hcp', 'hcps', 'doctor', 'doctors', 'physician', 'physicians', 'provider', 'providers'],
    'account': ['account', 'accounts', 'organization', 'organizations', 'hco', 'hcos'],
    'territory': ['territory', 'territories', 'territory code', 'territory id'],
    'region': ['region', 'regions', 'region code', 'region id'],
    'rep': ['rep', 'reps', 'representative', 'representatives'],
}


def _init_db():
    os.makedirs(str(SESSION_STORE_DIR), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute('''
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            user_query TEXT NOT NULL,
            generated_sql TEXT,
            result_meta TEXT,
            entities TEXT,
            summary TEXT,
            UNIQUE(session_id, timestamp, user_query)
        )
    ''')
    con.execute('CREATE INDEX IF NOT EXISTS idx_session_ts ON interactions(session_id, timestamp DESC)')
    con.commit()
    con.close()


def _get_con():
    if not os.path.exists(DB_PATH):
        _init_db()
    return sqlite3.connect(DB_PATH)


def save_interaction(session_id, user_query, generated_sql=None, result_meta=None, key_entities=None, summary=None):
    _init_db()
    con = _get_con()
    ts = datetime.now(timezone.utc).isoformat()
    try:
        con.execute(
            'INSERT OR REPLACE INTO interactions (session_id, timestamp, user_query, generated_sql, result_meta, entities, summary) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (session_id, ts, user_query, generated_sql or '', json.dumps(result_meta or {}), json.dumps(key_entities or {}), summary or ''),
        )
        con.commit()
        con.execute(
            'DELETE FROM interactions WHERE session_id = ? AND id NOT IN (SELECT id FROM interactions WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?)',
            (session_id, session_id, MAX_HISTORY),
        )
        con.commit()
    finally:
        con.close()


def get_history(session_id: str, n: int = 10) -> List[Dict[str, Any]]:
    _init_db()
    con = _get_con()
    try:
        rows = con.execute(
            'SELECT user_query, generated_sql, result_meta, entities, summary, timestamp FROM interactions WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?',
            (session_id, n),
        ).fetchall()
        return [
            {
                'user_query': r[0], 'generated_sql': r[1],
                'result_meta': json.loads(r[2]) if r[2] else {},
                'entities': json.loads(r[3]) if r[3] else {},
                'summary': r[4], 'timestamp': r[5],
            }
            for r in reversed(rows)
        ]
    finally:
        con.close()


def get_last_interaction(session_id: str) -> Optional[Dict[str, Any]]:
    h = get_history(session_id, n=1)
    return h[0] if h else None


def _has_resolvable_entities(entities: Dict) -> bool:
    if not isinstance(entities, dict):
        return False
    return any(isinstance(v, list) and len(v) > 0 for v in entities.values())


def get_last_interaction_with_entities(session_id: str, n: int = MAX_HISTORY) -> Optional[Dict[str, Any]]:
    for item in reversed(get_history(session_id, n=n)):
        if _has_resolvable_entities(item.get('entities', {})):
            return item
    return None


def clear_session(session_id: str):
    _init_db()
    con = _get_con()
    try:
        con.execute('DELETE FROM interactions WHERE session_id = ?', (session_id,))
        con.commit()
    finally:
        con.close()


def resolve_references(session_id: str, user_query: str) -> Dict[str, Any]:
    result = {'has_references': False, 'resolved_entities': {}, 'context_hint': '', 'ambiguous': False, 'clarification_question': None}
    # If the user supplies explicit identifiers, skip reference resolution.
    if EXPLICIT_ID_RE.search(user_query or ""):
        return result
    if not REFERENCE_RE.search(user_query):
        return result
    result['has_references'] = True
    last = get_last_interaction_with_entities(session_id)
    if not last:
        result['ambiguous'] = True
        result['clarification_question'] = "I noticed you're referring to a previous result, but I don't have saved IDs. Could you specify which records?"
        return result
    entities = last['entities']
    query_lower = user_query.lower()
    matched_types = []
    for etype, keywords in ENTITY_KEYWORDS.items():
        if any(kw in query_lower for kw in keywords):
            matched_types.append(etype)
    entity_key_map = {
        'hcp': ['hcp_ids', 'hcp_id'], 'account': ['account_ids', 'account_id', 'hco_ids', 'hco_id'],
        'territory': ['territory_codes', 'territory_ids'], 'region': ['region_codes', 'region_ids'],
        'rep': ['rep_ids', 'rep_id'],
    }
    if matched_types:
        for mt in matched_types:
            for key in entity_key_map.get(mt, []):
                if key in entities and entities[key]:
                    result['resolved_entities'][key] = entities[key]
    else:
        result['resolved_entities'] = entities
    if not result['resolved_entities']:
        result['resolved_entities'] = entities
    if _has_resolvable_entities(result['resolved_entities']):
        parts = [f"{k}: {v}" for k, v in result['resolved_entities'].items() if isinstance(v, list)]
        result['context_hint'] = (
            f"\n\n[CONTEXT MEMORY]: Use these IDs to filter SQL: {'; '.join(parts)}\n"
            f"Previous query: \"{last.get('user_query', '')}\"\n"
            f"Previous SQL: \"{last.get('generated_sql', '')}\"\n"
            f"Previous Columns: {last.get('result_meta', {}).get('columns', [])}"
        )
    elif result['has_references']:
        result['ambiguous'] = True
        result['clarification_question'] = "I noticed you're referring to a previous query, but I don't have enough context. Could you specify which records?"
    return result


def extract_entities_from_result(result_df, sql: str = None) -> Dict[str, list]:
    import pandas as pd
    entities: dict[str, list] = {}
    if result_df is None or not isinstance(result_df, pd.DataFrame) or result_df.empty:
        return entities
    id_columns = {
        'hcp_ids': [r'hcp.?id', r'hcp_id'], 'account_ids': [r'account.?id', r'hco.?id'],
        'territory_ids': [r'territory.?id'], 'region_ids': [r'region.?id'],
        'rep_ids': [r'rep.?id'], 'zip_codes': [r'^zip$'],
    }
    for ekey, patterns in id_columns.items():
        for col in result_df.columns:
            for pat in patterns:
                if re.search(pat, col, re.IGNORECASE):
                    entities[ekey] = [str(v) for v in result_df[col].dropna().unique()[:100]]
                    break
    return entities
