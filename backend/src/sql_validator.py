"""SQL Validator — pre-execution guardrails for generated SQL queries."""

import re
import json
import os
from typing import Tuple, Set, Optional

from .config import GUARDRAILS_CONFIG_PATH, METADATA_CATALOG_PATH

DISALLOWED_KEYWORDS = (
    'ALTER', 'UPDATE', 'DELETE', 'DROP', 'TRUNCATE', 'INSERT', 'CREATE',
    'GRANT', 'REVOKE', 'MERGE', 'CALL', 'EXEC', 'COMMIT', 'ROLLBACK',
    'USE', 'SHOW'
)
DISALLOWED_RE = re.compile(r'\b(' + '|'.join(DISALLOWED_KEYWORDS) + r')\b', re.IGNORECASE)
SELECT_STAR_RE = re.compile(r'\bSELECT\s+\*', re.IGNORECASE)
LINE_COMMENT_RE = re.compile(r'--')
BLOCK_COMMENT_RE = re.compile(r'/\*')
LIMIT_RE = re.compile(r'\bLIMIT\s+\d+', re.IGNORECASE)
TOP_RE = re.compile(r'\bTOP\s+\d+', re.IGNORECASE)
READ_ONLY_START_RE = re.compile(r'^\s*(SELECT\b|WITH\b)', re.IGNORECASE | re.DOTALL)

SQL_FUNCTIONS = {
    'count', 'sum', 'avg', 'min', 'max', 'coalesce', 'cast', 'round',
    'lower', 'upper', 'trim', 'length', 'substring', 'replace', 'concat',
    'iif', 'case', 'when', 'then', 'else', 'end', 'as', 'on', 'and',
    'or', 'not', 'in', 'is', 'null', 'between', 'like', 'exists',
    'distinct', 'asc', 'desc', 'having', 'union', 'all', 'any',
    'inner', 'outer', 'left', 'right', 'cross', 'full', 'natural',
    'true', 'false', 'ifnull', 'nullif', 'strftime', 'date_trunc',
    'extract', 'year', 'month', 'day', 'hour', 'minute', 'second',
    'date_part', 'epoch', 'interval', 'abs', 'sqrt', 'power', 'mod',
    'ceiling', 'floor', 'sign', 'string_agg', 'list', 'array_agg',
    'row_number', 'rank', 'dense_rank', 'ntile', 'lag', 'lead',
    'first_value', 'last_value', 'over', 'partition', 'rows', 'range',
    'unbounded', 'preceding', 'following', 'current', 'row',
}
SQL_RESERVED = {
    'select', 'from', 'where', 'group', 'by', 'order', 'limit', 'top',
    'join', 'inner', 'outer', 'left', 'right', 'cross', 'full', 'on',
    'and', 'or', 'not', 'in', 'is', 'null', 'between', 'like', 'exists',
    'having', 'union', 'all', 'as', 'distinct', 'case', 'when', 'then',
    'else', 'end', 'asc', 'desc', 'offset', 'fetch', 'next', 'with',
    'recursive', 'values', 'set', 'into', 'table', 'true', 'false', 'using',
}


def build_whitelist_from_metadata(metadata_path: str | None = None) -> Set[str]:
    path = metadata_path or str(METADATA_CATALOG_PATH)
    if not os.path.exists(path):
        return set()
    with open(path, 'r') as f:
        metadata = json.load(f)
    allowed: set[str] = set()
    for table in metadata.get('tables', []):
        tname = table['table_name']
        if tname == 'temp_df':
            continue
        allowed.add(tname.lower())
        for col in table.get('columns', []):
            cname = col['name']
            allowed.add(cname.lower())
            allowed.add(f"{tname.lower()}.{cname.lower()}")
    return allowed


def load_guardrails_config(config_path: str | None = None) -> dict:
    path = config_path or str(GUARDRAILS_CONFIG_PATH)
    if not os.path.exists(path):
        return {'max_rows': 100, 'require_limit': True, 'allow_select_star': False, 'extra_disallowed': []}
    with open(path, 'r') as f:
        return json.load(f)


def _extract_identifiers(sql: str) -> list:
    identifiers = list(re.findall(r'"([^"]+)"', sql))
    cleaned = re.sub(r'"[^"]*"', ' __QUOTED__ ', sql)
    cleaned = re.sub(r"'[^']*'", ' __STRING__ ', cleaned)
    identifiers.extend(re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\b', cleaned))
    return identifiers


def _extract_aliases(sql: str) -> Set[str]:
    aliases: set[str] = set()
    for name in re.findall(r'\bWITH\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(', sql, re.IGNORECASE):
        aliases.add(name.lower())
    for name in re.findall(r',\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(', sql, re.IGNORECASE):
        aliases.add(name.lower())
    for alias in re.findall(
        r'\b(?:FROM|JOIN)\s+(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)\b',
        sql, re.IGNORECASE,
    ):
        aliases.add(alias.lower())
    for alias in re.findall(r'\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b', sql, re.IGNORECASE):
        aliases.add(alias.lower())
    return aliases


def validate_sql(sql: str, allowed_identifiers: Optional[Set[str]] = None, config: Optional[dict] = None) -> Tuple[bool, str]:
    if config is None:
        config = load_guardrails_config()
    s = sql.strip()
    if not s:
        return False, "Empty query is not allowed."
    stripped = s.rstrip(';').strip()
    if ';' in stripped:
        return False, "Multiple statements are not allowed."
    if not READ_ONLY_START_RE.search(stripped):
        return False, "Only SELECT statements are allowed."
    if not config.get('allow_select_star', False) and SELECT_STAR_RE.search(stripped):
        return False, "SELECT * is not allowed. Specify explicit column names."
    match = DISALLOWED_RE.search(stripped)
    if match:
        return False, f"Disallowed keyword '{match.group(1).upper()}' detected."
    if LINE_COMMENT_RE.search(stripped):
        return False, "SQL comments (--) are not allowed."
    if BLOCK_COMMENT_RE.search(stripped):
        return False, "SQL block comments (/* */) are not allowed."
    if config.get('require_limit', True):
        if not LIMIT_RE.search(stripped) and not TOP_RE.search(stripped):
            return False, f"A LIMIT clause is required (max {config.get('max_rows', 100)})."
        limit_match = re.search(r'\bLIMIT\s+(\d+)', stripped, re.IGNORECASE)
        if limit_match and int(limit_match.group(1)) > config.get('max_rows', 100):
            return False, f"LIMIT exceeds max ({config.get('max_rows', 100)})."
    if allowed_identifiers:
        identifiers = _extract_identifiers(stripped)
        aliases = _extract_aliases(stripped)
        skip = SQL_FUNCTIONS | SQL_RESERVED | {'__quoted__', '__string__'} | aliases
        for ident in identifiers:
            li = ident.lower()
            if li in skip or li.isdigit():
                continue
            if li not in allowed_identifiers:
                return False, f"Identifier '{ident}' is not in the allowed schema."
    return True, "OK"


def get_guardrail_system_prompt() -> str:
    return """## STRICT SQL GENERATION RULES (MANDATORY)

You are a SQL generation assistant for a READ-ONLY analytics system.

### Mandatory Rules:
1. ONLY produce single, read-only SELECT statements with explicit column names.
2. NEVER use SELECT * — always list specific columns.
3. NEVER include: ALTER, UPDATE, DELETE, DROP, TRUNCATE, INSERT, CREATE, etc.
4. NEVER output multiple statements or SQL comments.
5. Use ONLY the allowed tables and columns from the schema below.
6. ALWAYS include LIMIT (default 100). Never exceed LIMIT 100.
7. Wrap column names with spaces in double quotes.
8. Use DuckDB SQL dialect.

"""
