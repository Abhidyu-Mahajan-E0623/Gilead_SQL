"""SQL planning and execution layer with multi-query support."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_openai import AzureChatOpenAI
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent
from langchain_core.messages import HumanMessage, SystemMessage

from .config import (
    AZURE_OPENAI_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_CHAT_DEPLOYMENT,
    AZURE_OPENAI_API_VERSION,
    WARNING_FILE_PATH,
    METADATA_CATALOG_PATH,
    LLM_GUIDE_PATH,
)
from .database import Database
from .sql_validator import (
    validate_sql,
    build_whitelist_from_metadata,
    load_guardrails_config,
    get_guardrail_system_prompt,
)
from .utils import STOPWORDS, TOKEN_PATTERN


def _load_metadata() -> dict | None:
    if not METADATA_CATALOG_PATH.exists():
        return None
    return json.loads(METADATA_CATALOG_PATH.read_text(encoding="utf-8"))


def _load_warning_notes() -> str:
    if not WARNING_FILE_PATH.exists():
        return ""
    try:
        return WARNING_FILE_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _load_llm_guide() -> str:
    if not LLM_GUIDE_PATH.exists():
        return ""
    try:
        return LLM_GUIDE_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _escape_prompt_braces(text: str) -> str:
    """Escape braces so langchain .format() doesn't treat them as placeholders."""
    return text.replace("{", "{{").replace("}", "}}")


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
                cmd_upper.startswith("DESCRIBE")
                or cmd_upper.startswith("SHOW")
                or cmd_upper.startswith("PRAGMA")
                or "information_schema" in command.lower()
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
                return df.to_string(index=False) if not df.empty else "No results found"
            if fetch == "one":
                return result.fetchone()
            rows = result.fetchmany(fetch)
            return str(rows) if rows else "No results found"
        except Exception as exc:
            return f"Error executing query: {exc}"

    def get_usable_table_names(self):
        try:
            return [row[0] for row in self._con.execute("SHOW TABLES").fetchall()]
        except Exception:
            return []

    def get_table_info(self, table_names=None):
        tables = table_names or self.get_usable_table_names()
        info = []
        for table_name in tables:
            try:
                schema = self._con.execute(f'DESCRIBE "{table_name}"').fetchall()
                info.append(f"\nTable: {table_name}")
                info.append("Columns: " + ", ".join(f"{row[0]} ({row[1]})" for row in schema))
                sample = self._con.execute(f'SELECT * FROM "{table_name}" LIMIT 2').fetchdf()
                if not sample.empty:
                    info.append(f"\nSample data:\n{sample.to_string(index=False)}")
            except Exception as exc:
                info.append(f"\nTable: {table_name} (Error: {exc})")
        return "\n".join(info)

    @property
    def dialect(self):
        return "duckdb"

    def get_table_info_no_throw(self, table_names=None):
        try:
            return self.get_table_info(table_names)
        except Exception as exc:
            return f"Error: {exc}"


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
        self.context = self._build_context()
        self.agent = None
        self._account_reference_rows: list[dict[str, Any]] | None = None
        self._product_values: list[str] | None = None

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
                for join in table["joins"]:
                    ctx += f"- {join['condition']}\n"
            ctx += "\n"
        if self.metadata.get("canonical_relationships"):
            ctx += "## Canonical Relationships\n"
            for rel in self.metadata["canonical_relationships"]:
                ctx += f"- `{rel['from']}` -> `{rel['to']}`\n"
            ctx += "\n"
        warnings = _load_warning_notes()
        if warnings:
            ctx += "## Critical Data Warnings (MANDATORY)\n" + _escape_prompt_braces(warnings) + "\n\n"
        guide = _load_llm_guide()
        if guide:
            ctx += "## Sample Data Guide (MANDATORY)\n"
            ctx += "Use the following business guidance when choosing joins, filters, and root-cause logic.\n\n"
            ctx += _escape_prompt_braces(guide) + "\n\n"
        ctx += """## Query Guidelines

1. Use EXACT column names (case-sensitive)
2. Wrap column names with spaces in double quotes
3. Always add LIMIT clause
4. Handle NULL values
5. Select ID columns alongside names for context memory
6. Prefer multiple focused queries over one oversized query when root cause requires checking multiple tables

## Context Memory

If the user query includes `[CONTEXT MEMORY]`, use the provided IDs to filter SQL.

## Output Format

- Show text tables of results
- If empty, state `No results found`

Generate accurate DuckDB SQL queries following these guidelines.
"""
        return ctx

    def _get_agent(self):
        if self.agent is None:
            self.agent = create_sql_agent(
                llm=self.llm,
                db=self.sql_db,
                agent_type="openai-tools",
                verbose=True,
                prefix=self.context,
                max_iterations=10,
                agent_executor_kwargs={"return_intermediate_steps": True},
            )
        return self.agent

    @staticmethod
    def _extract_json_block(text: str) -> dict | None:
        if not text:
            return None
        text = text.strip()
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
        if fence_match:
            text = fence_match.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start : end + 1]
        try:
            return json.loads(text)
        except Exception:
            return None

    @staticmethod
    def _normalize_plan(plan: dict | None) -> dict | None:
        if not isinstance(plan, dict):
            return None
        queries = plan.get("queries")
        if not isinstance(queries, list):
            return None
        normalized_queries = []
        for idx, query in enumerate(queries[:4], start=1):
            if not isinstance(query, dict):
                continue
            sql = str(query.get("sql", "")).strip().strip("`")
            if not sql:
                continue
            normalized_queries.append(
                {
                    "name": str(query.get("name", f"query_{idx}")).strip() or f"query_{idx}",
                    "purpose": str(query.get("purpose", "")).strip() or "Supporting analysis query",
                    "sql": sql,
                }
            )
        if not normalized_queries:
            return None
        return {
            "analysis": str(plan.get("analysis", "")).strip(),
            "queries": normalized_queries,
        }

    @staticmethod
    def _extract_first(pattern: str, text: str) -> str | None:
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1) if match else None

    @staticmethod
    def _tokenize_text(value: str) -> set[str]:
        return {
            token.lower()
            for token in TOKEN_PATTERN.findall(value or "")
            if len(token) > 2 and token.lower() not in STOPWORDS
        }

    @staticmethod
    def _sql_escape(value: str) -> str:
        return value.replace("'", "''")

    @staticmethod
    def _extract_territory_id(text: str) -> str | None:
        match = re.search(r"\b([A-Z]{2,5}-\d{2,4})\b", text.upper())
        return match.group(1) if match else None

    @staticmethod
    def _extract_address_fragment(text: str) -> str | None:
        match = re.search(
            r"\b(\d+(?:st|nd|rd|th)?\s+(?:street|st|avenue|ave|boulevard|blvd|road|rd|drive|dr))\b",
            text,
            re.IGNORECASE,
        )
        return match.group(1) if match else None

    def _get_account_reference_rows(self) -> list[dict[str, Any]]:
        if self._account_reference_rows is None:
            try:
                df = self.db.con.execute(
                    "SELECT DISTINCT HCO_ID, HCO_Name, HCO_Address, HCO_City, HCO_State, Retail "
                    "FROM iqvia_onekey"
                ).fetchdf()
                self._account_reference_rows = df.to_dict("records")
            except Exception:
                self._account_reference_rows = []
        return self._account_reference_rows

    def _get_product_values(self) -> list[str]:
        if self._product_values is None:
            try:
                rows = self.db.con.execute(
                    "SELECT DISTINCT Product FROM ("
                    "SELECT Product FROM iqvia_ddd "
                    "UNION SELECT Product FROM exponent "
                    "UNION SELECT Product FROM \"867_shipment\""
                    ") ORDER BY Product"
                ).fetchall()
                self._product_values = [str(row[0]) for row in rows if row and row[0]]
            except Exception:
                self._product_values = []
        return self._product_values

    def _resolve_product_from_question(self, question: str) -> str | None:
        lowered = question.lower()
        for product in self._get_product_values():
            if product.lower() in lowered:
                return product
        return None

    def _resolve_account_from_question(self, question: str) -> dict[str, Any] | None:
        lowered = question.lower()
        question_tokens = self._tokenize_text(question)
        best_match: dict[str, Any] | None = None
        best_score = 0.0

        for row in self._get_account_reference_rows():
            hco_name = str(row.get("HCO_Name", "") or "")
            address = str(row.get("HCO_Address", "") or "")
            city = str(row.get("HCO_City", "") or "")
            state = str(row.get("HCO_State", "") or "")

            score = 0.0
            if hco_name and hco_name.lower() in lowered:
                score += 100
            if address and address.lower() in lowered:
                score += 75

            score += len(question_tokens & self._tokenize_text(hco_name)) * 8
            score += len(question_tokens & self._tokenize_text(address)) * 3
            score += len(question_tokens & self._tokenize_text(city)) * 2
            score += len(question_tokens & self._tokenize_text(state))

            if score > best_score:
                best_score = score
                best_match = row

        return best_match if best_score >= 8 else None

    @staticmethod
    def _render_sql_template(sql: str, context: dict[str, Any]) -> tuple[str, list[str]]:
        missing: list[str] = []

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            value = context.get(key)
            if value is None or value == "":
                missing.append(key)
                return match.group(0)
            return str(value)

        rendered = re.sub(r"\{\{([A-Z0-9_]+)\}\}", replace, sql)
        return rendered, missing

    @staticmethod
    def _update_context_from_df(df, context: dict[str, Any]) -> None:
        if df is None or df.empty:
            return
        mappings = {
            "HCO_ID": "HCO_ID",
            "HCO_Name": "ACCOUNT_NAME",
            "HCO_NAME": "ACCOUNT_NAME",
            "NPI": "NPI",
            "Territory": "TERRITORY_ID",
            "TERRITORY_ID": "TERRITORY_ID",
            "Product": "PRODUCT",
            "PRODUCT": "PRODUCT",
            "HCO_Address": "ACCOUNT_ADDRESS",
            "ADDRESS": "ACCOUNT_ADDRESS",
        }
        for column, key in mappings.items():
            if column not in df.columns:
                continue
            values = [str(v) for v in df[column].dropna().unique().tolist() if str(v).strip()]
            if len(values) == 1:
                context[key] = values[0]

    def _build_execution_context(self, question: str) -> dict[str, Any]:
        context: dict[str, Any] = {}
        npi = self._extract_first(r"\b(\d{10})\b", question)
        hco_id = self._extract_first(r"\b(HCO[_-][A-Z0-9_-]+)\b", question)
        territory_id = self._extract_territory_id(question)
        product = self._resolve_product_from_question(question)
        account = self._resolve_account_from_question(question)
        address_fragment = self._extract_address_fragment(question)

        if npi:
            context["NPI"] = npi
        if hco_id:
            context["HCO_ID"] = hco_id
        if territory_id:
            context["TERRITORY_ID"] = territory_id.upper()
        if product:
            context["PRODUCT"] = self._sql_escape(product)
        if address_fragment:
            context["ADDRESS_FRAGMENT"] = self._sql_escape(address_fragment)
        if account:
            context.setdefault("HCO_ID", str(account.get("HCO_ID", "")))
            context["ACCOUNT_NAME"] = self._sql_escape(str(account.get("HCO_Name", "")))
            context["ACCOUNT_ADDRESS"] = self._sql_escape(str(account.get("HCO_Address", "")))

        return context

    def _heuristic_plan(self, question: str) -> dict | None:
        lowered = question.lower()
        npi = self._extract_first(r"\b(\d{10})\b", question)
        hco_id = self._extract_first(r"\b(HCO-[A-Z0-9-]+)\b", question)
        territory_id = self._extract_territory_id(question)
        product = self._resolve_product_from_question(question)
        resolved_account = self._resolve_account_from_question(question)
        diagnostic_terms = (
            "disappear",
            "disappeared",
            "missing",
            "merge",
            "merged",
            "move",
            "moved",
            "territory",
            "credit",
            "dashboard",
            "what happened",
            "why",
            "explain",
            "realignment",
            "alignment",
        )
        retail_terms = (
            "retail",
            "non-retail",
            "non retail",
            "account",
            "shipment",
            "ship-to",
            "ship to",
            "867",
            "outlet",
            "volume",
        )

        if npi and any(term in lowered for term in diagnostic_terms):
            return {
                "analysis": "Use multiple focused queries to inspect provider identity, alignment, governance events, and prescribing volume before answering.",
                "queries": [
                    {
                        "name": "provider_records",
                        "purpose": "Inspect all OneKey records tied to the NPI, including active and retired duplicates.",
                        "sql": (
                            "SELECT HCP_ID, HCP_Name, NPI, Master_ID, Specialty, Status, "
                            "HCP_Address, HCP_City, HCP_State, HCP_ZIP, "
                            "HCO_ID, HCO_Name, Affiliation_Type, Retail, "
                            "Digital_DNC_Flag, Inperson_DNC_Flag "
                            f"FROM iqvia_onekey WHERE NPI = {npi} "
                            "ORDER BY CASE WHEN Status = 'Active' THEN 0 ELSE 1 END LIMIT 100"
                        ),
                    },
                    {
                        "name": "territory_from_zip",
                        "purpose": "Check territory assignments for the provider's ZIP codes.",
                        "sql": (
                            "SELECT a.ZIP, a.Territory, a.Region, a.Area "
                            "FROM alignment a "
                            f"WHERE a.ZIP IN (SELECT HCP_ZIP FROM iqvia_onekey WHERE NPI = {npi}) "
                            "LIMIT 100"
                        ),
                    },
                    {
                        "name": "dcr_events",
                        "purpose": "Look for merge, DNC update, or other governance events tied to the provider.",
                        "sql": (
                            "SELECT d.Change_Date, d.Entity, d.Entity_ID, d.Change_Type, d.Details "
                            "FROM dcr d "
                            f"WHERE d.Entity_ID IN (SELECT HCP_ID FROM iqvia_onekey WHERE NPI = {npi}) "
                            "ORDER BY d.Change_Date DESC LIMIT 100"
                        ),
                    },
                    {
                        "name": "prescribing_volume",
                        "purpose": "Confirm whether prescribing volume continued and how it changed over time.",
                        "sql": (
                            "SELECT NPI, HCP_ID, Product, Month, Units, Territory "
                            f"FROM iqvia_ddd WHERE NPI = {npi} "
                            "ORDER BY Month LIMIT 100"
                        ),
                    },
                ],
            }

        resolved_hco_id = hco_id or (str(resolved_account.get("HCO_ID")) if resolved_account else None)
        if resolved_hco_id and any(term in lowered for term in retail_terms):
            safe_hco_id = self._sql_escape(resolved_hco_id)
            product_filter_e = f" AND e.Product = '{self._sql_escape(product)}'" if product else ""
            product_filter_d = f" AND d.Product = '{self._sql_escape(product)}'" if product else ""
            return {
                "analysis": (
                    "Resolve the account to a stable HCO_ID, then inspect affiliated providers, "
                    "non-retail volume, and any retail DDD activity tied to affiliated NPIs."
                ),
                "queries": [
                    {
                        "name": "account_identity",
                        "purpose": "Confirm the resolved account identifier and current HCP records.",
                        "sql": (
                            "SELECT DISTINCT HCP_ID, HCP_Name, NPI, Specialty, Status, "
                            "HCO_ID, HCO_Name, Affiliation_Type, Retail "
                            f"FROM iqvia_onekey WHERE HCO_ID = '{safe_hco_id}' LIMIT 100"
                        ),
                    },
                    {
                        "name": "nonretail_volume",
                        "purpose": "Check non-retail outlet-level volume at HCO grain.",
                        "sql": (
                            "SELECT e.HCO_ID, e.HCO_Name, e.Product, e.Period, e.Units, e.Territory "
                            "FROM exponent e "
                            f"WHERE e.HCO_ID = '{safe_hco_id}'"
                            f"{product_filter_e} "
                            "LIMIT 100"
                        ),
                    },
                    {
                        "name": "shipment_data",
                        "purpose": "Check shipment records to ship-to locations matching this account.",
                        "sql": (
                            "SELECT s.Shipment_Date, s.Product, s.Units, s.Distributor, "
                            "s.Ship_To_ID, s.Ship_To_Name, s.ZIP "
                            "FROM \"867_shipment\" s "
                            f"WHERE UPPER(s.Ship_To_Name) LIKE UPPER('%{safe_hco_id}%') "
                            f"OR s.ZIP IN (SELECT HCO_ZIP FROM iqvia_onekey WHERE HCO_ID = '{safe_hco_id}') "
                            "LIMIT 100"
                        ),
                    },
                    {
                        "name": "retail_volume_for_affiliates",
                        "purpose": "Check whether affiliated providers have retail DDD activity for the same product.",
                        "sql": (
                            "SELECT d.NPI, d.HCP_ID, o.HCP_Name, o.HCO_ID, o.HCO_Name, d.Product, d.Month, d.Units, d.Territory "
                            "FROM iqvia_onekey o "
                            "JOIN iqvia_ddd d ON o.HCP_ID = d.HCP_ID "
                            f"WHERE o.HCO_ID = '{safe_hco_id}'"
                            f"{product_filter_d} "
                            "ORDER BY d.Month, o.HCP_Name LIMIT 100"
                        ),
                    },
                ],
            }

        return None

    def _build_planner_messages(self, question: str) -> list[Any]:
        planner_system = (
            self.context
            + "\n## Planning Task\n"
            + "You are planning DuckDB SQL for an analytics question.\n"
            + "Return JSON only.\n"
            + "Use 1 to 4 focused SQL queries.\n"
            + "When different evidence must come from different grains or tables, prefer multiple queries over one giant query.\n"
            + "Typical root-cause questions should separate identity/master-data, alignment, DCR/event history, and volume queries.\n"
            + "Every SQL statement must be a single read-only DuckDB query with explicit columns and LIMIT.\n"
            + "JSON schema:\n"
            + "{\n"
            + '  "analysis": "short planning summary",\n'
            + '  "queries": [\n'
            + '    {"name": "short_name", "purpose": "why this query is needed", "sql": "SELECT ... LIMIT 100"}\n'
            + "  ]\n"
            + "}\n"
        )
        return [
            SystemMessage(content=planner_system),
            HumanMessage(content=f"User question:\n{question}\n\nReturn JSON only."),
        ]

    @staticmethod
    def _llm_to_text(response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return "\n".join(parts).strip()
        return str(content).strip()

    def _llm_plan(self, question: str) -> dict | None:
        try:
            raw = self._llm_to_text(self.llm.invoke(self._build_planner_messages(question)))
        except Exception:
            return None
        return self._normalize_plan(self._extract_json_block(raw))

    def _execute_sql(self, sql: str):
        result = self.db.con.execute(sql).fetchdf()
        try:
            from .normalizer import normalize_resultset, load_normalization_config

            result = normalize_resultset(result, load_normalization_config())
        except Exception:
            pass
        self.sql_db.last_result_df = result
        return result

    @staticmethod
    def _preview_df(df, max_rows: int = 20) -> str:
        if df is None or df.empty:
            return "No results found"
        try:
            return df.head(max_rows).to_string(index=False)
        except Exception:
            return str(df.head(max_rows))

    def _format_plan_response(self, plan: dict, results: list[dict[str, Any]]) -> str:
        lines = []
        if plan.get("analysis"):
            lines.append(f"Plan Analysis: {plan['analysis']}")
            lines.append("")
        for index, item in enumerate(results, start=1):
            lines.append(f"Query {index}: {item['name']}")
            lines.append(f"Purpose: {item['purpose']}")
            lines.append("SQL:")
            lines.append(f"```sql\n{item['sql']}\n```")
            if item.get("error"):
                lines.append(f"Execution Status: Failed")
                lines.append(f"Error: {item['error']}")
            else:
                lines.append(f"Execution Status: Succeeded")
                lines.append(f"Row Count: {item['row_count']}")
                lines.append("Results:")
                lines.append(item["preview"])
            lines.append("")
        return "\n".join(lines).strip()

    def _execute_plan(self, plan: dict, question: str) -> dict:
        results: list[dict[str, Any]] = []
        last_df = None
        executed_any = False
        sql_statements: list[str] = []
        context = self._build_execution_context(question)

        for item in plan.get("queries", [])[:4]:
            sql_template = item["sql"].strip().rstrip(";") + ";"
            sql, missing = self._render_sql_template(sql_template, context)
            if missing:
                results.append(
                    {
                        "name": item["name"],
                        "purpose": item["purpose"],
                        "sql": sql_template.rstrip(";"),
                        "error": f"Unresolved SQL placeholders: {', '.join(sorted(set(missing)))}",
                    }
                )
                continue
            sql_statements.append(sql.rstrip(";"))
            is_valid, error_msg = validate_sql(
                sql,
                allowed_identifiers=self.allowed_identifiers or None,
                config=self.guardrails_config,
            )
            if not is_valid:
                results.append(
                    {
                        "name": item["name"],
                        "purpose": item["purpose"],
                        "sql": sql.rstrip(";"),
                        "error": error_msg,
                    }
                )
                continue
            try:
                df = self._execute_sql(sql)
                last_df = df
                executed_any = True
                self._update_context_from_df(df, context)
                results.append(
                    {
                        "name": item["name"],
                        "purpose": item["purpose"],
                        "sql": sql.rstrip(";"),
                        "row_count": int(len(df)),
                        "columns": list(df.columns),
                        "preview": self._preview_df(df),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "name": item["name"],
                        "purpose": item["purpose"],
                        "sql": sql.rstrip(";"),
                        "error": str(exc),
                    }
                )

        self.sql_db.last_result_df = last_df
        response = self._format_plan_response(plan, results)
        return {
            "success": executed_any,
            "response": response,
            "sql": sql_statements,
            "plan": plan,
            "result_sets": results,
        }

    def _fallback_agent_query(self, question: str) -> dict:
        try:
            result = self._get_agent().invoke({"input": question})
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
            return {"success": True, "response": output, "sql": [generated_sql] if generated_sql else []}
        except Exception as exc:
            return {"success": False, "error": str(exc), "response": f"Error: {exc}"}

    def query(self, question: str) -> dict:
        for planner in (self._heuristic_plan, self._llm_plan):
            plan = planner(question)
            if not plan:
                continue
            executed = self._execute_plan(plan, question)
            if executed.get("success"):
                return executed
        return self._fallback_agent_query(question)

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


def reset_sql_agent() -> None:
    """Clear the cached SQL agent so it can be rebuilt with new metadata."""
    global _agent_instance
    _agent_instance = None
