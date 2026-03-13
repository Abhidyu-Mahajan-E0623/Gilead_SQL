"""LangChain SQL agent — wraps DuckDB with guardrails."""

from __future__ import annotations

import os

from langchain_openai import AzureChatOpenAI
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent

from .config import (
    AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_CHAT_DEPLOYMENT, AZURE_OPENAI_API_VERSION,
    WARNING_FILE_PATH, METADATA_CATALOG_PATH,
)
from .database import Database
from .sql_validator import validate_sql, build_whitelist_from_metadata, load_guardrails_config, get_guardrail_system_prompt


def _load_metadata() -> dict | None:
    if not METADATA_CATALOG_PATH.exists():
        return None
    import json
    return json.loads(METADATA_CATALOG_PATH.read_text(encoding="utf-8"))


def _load_warning_notes() -> str:
    if not WARNING_FILE_PATH.exists():
        return ""
    try:
        return WARNING_FILE_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


class DuckDBSQLDatabase(SQLDatabase):
    """Custom SQLDatabase wrapper for DuckDB with guardrails."""

    def __init__(self, con, allowed_identifiers=None, guardrails_config=None):
        self._con = con
        self._schema = None
        self._ignore_tables = []
        self._include_tables = []
        self._sample_rows_in_table_info = 3
        self._custom_table_info = None
        self._view_support = True
        self._max_string_length = 300
        self._allowed_identifiers = allowed_identifiers or set()
        self._guardrails_config = guardrails_config or load_guardrails_config()
        self.last_result_df = None

    def run(self, command: str, fetch: str = "all", parameters=None, **kwargs):
        try:
            cmd_upper = command.strip().upper()
            is_internal = (
                cmd_upper.startswith('DESCRIBE') or cmd_upper.startswith('SHOW') or
                cmd_upper.startswith('PRAGMA') or 'information_schema' in command.lower()
            )
            if not is_internal:
                is_valid, error_msg = validate_sql(
                    command,
                    allowed_identifiers=self._allowed_identifiers or None,
                    config=self._guardrails_config,
                )
                if not is_valid:
                    return f"QUERY BLOCKED: {error_msg}\nRewrite with explicit columns, LIMIT, and allowed tables."
            result = self._con.execute(command, parameters) if parameters else self._con.execute(command)
            if fetch == "all":
                df = result.fetchdf()
                try:
                    from .normalizer import normalize_resultset, load_normalization_config
                    df = normalize_resultset(df, load_normalization_config())
                except Exception:
                    pass
                self.last_result_df = df
                return df.to_string() if not df.empty else "No results found"
            elif fetch == "one":
                return result.fetchone()
            else:
                rows = result.fetchmany(fetch)
                return str(rows) if rows else "No results found"
        except Exception as e:
            return f"Error executing query: {str(e)}"

    def get_usable_table_names(self):
        try:
            return [r[0] for r in self._con.execute("SHOW TABLES").fetchall()]
        except Exception:
            return []

    def get_table_info(self, table_names=None):
        tables = table_names or self.get_usable_table_names()
        info = []
        for t in tables:
            try:
                schema = self._con.execute(f'DESCRIBE "{t}"').fetchall()
                info.append(f"\nTable: {t}")
                info.append("Columns: " + ", ".join(f"{r[0]} ({r[1]})" for r in schema))
                sample = self._con.execute(f'SELECT * FROM "{t}" LIMIT 2').fetchdf()
                if not sample.empty:
                    info.append(f"\nSample data:\n{sample.to_string()}")
            except Exception as e:
                info.append(f"\nTable: {t} (Error: {e})")
        return "\n".join(info)

    @property
    def dialect(self):
        return "duckdb"

    def get_table_info_no_throw(self, table_names=None):
        try:
            return self.get_table_info(table_names)
        except Exception as e:
            return f"Error: {e}"


class LangChainSQLAgent:
    def __init__(self, database: Database):
        self.db = database
        self.metadata = _load_metadata() or {}
        self.guardrails_config = load_guardrails_config()
        self.allowed_identifiers = build_whitelist_from_metadata()

        self.llm = AzureChatOpenAI(
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
            deployment_name=AZURE_OPENAI_CHAT_DEPLOYMENT,
            temperature=0,
        )
        self.sql_db = DuckDBSQLDatabase(
            self.db.con,
            allowed_identifiers=self.allowed_identifiers,
            guardrails_config=self.guardrails_config,
        )
        context = self._build_context()
        self.agent = create_sql_agent(
            llm=self.llm, db=self.sql_db, agent_type="openai-tools",
            verbose=True, prefix=context, max_iterations=10,
            agent_executor_kwargs={"return_intermediate_steps": True},
        )

    def _build_context(self) -> str:
        ctx = get_guardrail_system_prompt()
        ctx += f"\n# Database: {self.metadata.get('database', 'CombinedDB')}\n\n## Business Context\n\n"
        for table in self.metadata.get("tables", []):
            ctx += f"### Table: `{table['table_name']}`\n"
            ctx += f"**Purpose**: {table.get('business_purpose', 'N/A')}\n"
            ctx += f"**Grain**: {table.get('grain', 'N/A')}\n\n**Columns**:\n"
            for col in table.get("columns", []):
                ctx += f"- `{col['name']}`: {col.get('description', 'N/A')}\n"
            if table.get("joins"):
                ctx += "\n**Joins**:\n"
                for j in table["joins"]:
                    ctx += f"- {j['condition']}\n"
            ctx += "\n"
        if self.metadata.get("canonical_relationships"):
            ctx += "## Canonical Relationships\n"
            for rel in self.metadata["canonical_relationships"]:
                ctx += f"- `{rel['from']}` → `{rel['to']}`\n"
            ctx += "\n"
        warnings = _load_warning_notes()
        if warnings:
            ctx += "## Critical Data Warnings (MANDATORY)\n" + warnings + "\n\n"
        ctx += """## Query Guidelines

1. Use EXACT column names (case-sensitive)
2. Wrap column names with spaces in double quotes
3. Always add LIMIT clause
4. Handle NULL values
5. Select ID columns alongside names for context memory

## Context Memory

If the user query includes `[CONTEXT MEMORY]`, use the provided IDs to filter SQL.

## Output Format

- Show markdown table of results
- If empty, state "No results found"

Generate accurate DuckDB SQL queries following these guidelines.
"""
        return ctx

    def query(self, question: str) -> dict:
        try:
            result = self.agent.invoke({"input": question})
            generated_sql = ""
            if isinstance(result, dict):
                output = result.get("output", str(result))
                for step in result.get("intermediate_steps", []):
                    if hasattr(step[0], "tool") and step[0].tool == "sql_db_query":
                        generated_sql = step[0].tool_input
            else:
                output = str(result)
            if generated_sql:
                generated_sql = str(generated_sql).strip()
            return {"success": True, "response": output, "sql": generated_sql}
        except Exception as e:
            return {"success": False, "error": str(e), "response": f"Error: {str(e)}"}

    @property
    def last_result_df(self):
        return self.sql_db.last_result_df


_agent_instance = None


def get_sql_agent(database: Database | None = None) -> LangChainSQLAgent:
    global _agent_instance
    if _agent_instance is None:
        if database is None:
            raise ValueError("Database required for first initialization")
        _agent_instance = LangChainSQLAgent(database)
    return _agent_instance
