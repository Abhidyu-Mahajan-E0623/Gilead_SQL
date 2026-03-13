"""Unified responder — combines playbook Q&A with SQL data retrieval.

Flow:
1. Check DCR confirmation / follow-up state (from Gilead_Demo)
2. Classify query: playbook-only, data-only, or hybrid
3. Playbook search → get scenario context
4. SQL data retrieval → get numbers from DuckDB
5. Final LLM call → structured answer using both contexts
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from .azure_client import AzureOpenAIClient
from .playbook import PlaybookIndex
from .query_engine import process_query as sql_process_query
from .utils import STOPWORDS, TOKEN_PATTERN, normalize_text

LOGGER = logging.getLogger(__name__)

# ── Regex patterns from Gilead_Demo ──────────────────────────────────────────
IDENTIFIER_PATTERNS = {
    "NPI": re.compile(r"\bNPI\b\s*[:#-]?\s*(\d{10})", re.IGNORECASE),
    "HCO ID": re.compile(r"\bHCO\s+ID\b\s*[:#-]?\s*([A-Z0-9-]+)", re.IGNORECASE),
}
TERRITORY_ID_PATTERN = re.compile(r"\b(?:[A-Z]{2,5}-\d{2,4}|[A-Z]{1,3}\d{4,8})\b", re.IGNORECASE)
TERRITORY_CONTEXT_PATTERN = re.compile(
    r"\b(?:territory(?:\s+id)?(?:\s+code)?)\b[^A-Z0-9]{0,6}([A-Z]{1,5}-?\d{2,8})\b", re.IGNORECASE,
)
NON_TERRITORY_PREFIXES = {"DCR", "HCO", "HCP", "NPI", "SP", "ZIP", "IC"}
PROVIDER_ID_VALUE_PATTERN = re.compile(r"\b(\d{10})\b")
PROVIDER_REF_PATTERN = re.compile(r"\bdr\.?\s+[A-Za-z'`-]+\b", re.IGNORECASE)
PROVIDER_NAME_PATTERN = re.compile(r"\bDr\.?\s+([A-Z][A-Za-z'`-]+(?:\s+[A-Z][A-Za-z'`-]+)+)")
PROVIDER_NAME_WITH_NPI_PATTERN = re.compile(
    r"\bDr\.?\s+([A-Z][A-Za-z'`-]+(?:\s+[A-Z][A-Za-z'`-]+)*)\s*\(NPI\b[^0-9]{0,8}(\d{10})", re.IGNORECASE,
)
ACCOUNT_NAME_PATTERN = re.compile(r"\b([A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){1,5})\s+\(HCO\s+ID\b")
VAGUE_QUERY_PREFIXES = (
    "what happened", "whats happening", "what is happening", "can you explain",
    "explain this", "why is this happening", "why did this happen",
)

# Data-oriented keywords that indicate a SQL query is needed
DATA_QUERY_KEYWORDS = re.compile(
    r"\b(top\s+\d+|bottom\s+\d+|how\s+many|count|total|sum|average|min|max|"
    r"rank|list|show\s+me|sales|trx|nrx|volume|revenue|quota|attainment|"
    r"q[1-4]|quarter|year|ytd|mtd|2024|2025|percentage|percent|%|"
    r"compare|comparison|growth|decline|trend)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class AssistantResult:
    content: str
    matched_inquiry_id: str | None
    matched_title: str | None
    confidence: float | None


@dataclass(slots=True)
class ResponseDecision:
    mode: str  # "full", "partial", "vague", "follow_up", "data_only", "hybrid"
    follow_up: str | None = None


class ChatResponder:
    def __init__(self, index: PlaybookIndex, azure_client: AzureOpenAIClient) -> None:
        self.index = index
        self.azure_client = azure_client
        self.inquiries_by_id = {inq.inquiry_id: inq for inq in self.index.inquiries}

    # ── Query classification ─────────────────────────────────────────────────
    def _is_data_query(self, query: str) -> bool:
        """Return True if the query is asking for data/numbers that need SQL."""
        return bool(DATA_QUERY_KEYWORDS.search(query))

    def _classify_query_type(self, query: str, playbook_confidence: float) -> str:
        """Classify as 'playbook', 'data', or 'hybrid'."""
        is_data = self._is_data_query(query)
        is_playbook = playbook_confidence >= 0.35

        if is_data and is_playbook:
            return "hybrid"
        elif is_data:
            return "data"
        else:
            return "playbook"

    # ── Playbook context helpers ─────────────────────────────────────────────
    def _build_context_block(self, query: str) -> tuple[list[dict[str, Any]], float]:
        results = self.index.search(query, top_k=3)
        confidence = self.index.confidence(results)
        context = []
        for rank, r in enumerate(results, 1):
            inq = r.inquiry
            context.append({
                "rank": rank, "inquiry_id": inq.inquiry_id, "title": inq.title,
                "category": inq.category, "field_rep_says": inq.field_rep_says,
                "what_happened": inq.what_happened, "datasets_used": inq.datasets_used,
                "resolution_and_response_to_rep": inq.resolution_and_response_to_rep,
                "steward_steps": inq.steward_steps,
                "score": round(r.score, 4),
            })
        return context, confidence

    def _context_from_inquiry_id(self, inquiry_id: str, score: float = 1.0) -> dict[str, Any] | None:
        inq = self.inquiries_by_id.get(str(inquiry_id))
        if inq is None:
            return None
        return {
            "rank": 1, "inquiry_id": inq.inquiry_id, "title": inq.title,
            "category": inq.category, "field_rep_says": inq.field_rep_says,
            "what_happened": inq.what_happened, "datasets_used": inq.datasets_used,
            "resolution_and_response_to_rep": inq.resolution_and_response_to_rep,
            "steward_steps": inq.steward_steps,
            "score": round(score, 4),
        }

    # ── Identifier extraction (from Gilead_Demo) ─────────────────────────────
    @staticmethod
    def _content_tokens(value: str) -> list[str]:
        return [t for t in TOKEN_PATTERN.findall(value.lower()) if t not in STOPWORDS]

    @staticmethod
    def _extract_identifiers(text: str) -> list[str]:
        ids = []
        for label, pat in IDENTIFIER_PATTERNS.items():
            if pat.search(text):
                ids.append(label)
        return ids

    @staticmethod
    def _identifier_source_text(context: dict[str, Any]) -> str:
        return " ".join(filter(None, [
            str(context.get("field_rep_says", "")).strip(),
            str(context.get("what_happened", "")).strip(),
            str(context.get("resolution_and_response_to_rep", "")).strip(),
        ]))

    @classmethod
    def _extract_territory_id(cls, text: str) -> str | None:
        for m in TERRITORY_CONTEXT_PATTERN.finditer(text):
            c = m.group(1).upper()
            if cls._is_valid_territory_id(c):
                return c
        for m in TERRITORY_ID_PATTERN.finditer(text):
            c = m.group(0).upper()
            if cls._is_valid_territory_id(c):
                return c
        return None

    @staticmethod
    def _is_valid_territory_id(candidate: str) -> bool:
        v = re.sub(r"[^A-Z0-9-]", "", candidate.upper())
        if not v or not re.search(r"\d", v):
            return False
        pm = re.match(r"^([A-Z]+)", v)
        if not pm:
            return False
        return pm.group(1) not in NON_TERRITORY_PREFIXES

    def _query_has_identifier(self, query: str) -> bool:
        return (
            any(p.search(query) for p in IDENTIFIER_PATTERNS.values())
            or bool(self._extract_territory_id(query))
            or bool(PROVIDER_ID_VALUE_PATTERN.search(query))
        )

    # ── Follow-up / identifier request ───────────────────────────────────────
    def _required_identifiers(self, query: str, context: dict[str, Any]) -> list[str]:
        required = []
        src = self._identifier_source_text(context)
        available = self._extract_identifiers(src)
        if "NPI" in available and not re.search(r"\b(?:NPI|NCP)\b\s*[:#-]?\s*\d{10}\b", query, re.IGNORECASE):
            if PROVIDER_REF_PATTERN.search(query) or any(w in query.lower() for w in ("doctor", "physician", "hcp")):
                required.append("NPI ID")
        if "HCO ID" in available and not IDENTIFIER_PATTERNS["HCO ID"].search(query):
            required.append("HCO ID")
        if self._query_mentions_territory(query) and not self._extract_territory_id(query):
            required.append("territory ID")
        return required

    @staticmethod
    def _query_mentions_territory(query: str) -> bool:
        n = normalize_text(query)
        return "territory" in n or bool(ChatResponder._extract_territory_id(query))

    @staticmethod
    def _format_identifier_follow_up(required: list[str], *, correct: bool = False) -> str:
        q = "correct " if correct else ""
        if len(required) == 1:
            noun = "records" if required[0].endswith("s") else "record"
            return f"Please provide the {q}{required[0]} so the correct {noun} can be confirmed before proceeding."
        joined = " and ".join(required) if len(required) == 2 else ", ".join(required[:-1]) + f", and {required[-1]}"
        return f"Please provide the {q}{joined} so the correct records can be confirmed before proceeding."

    # ── DCR flow (from Gilead_Demo) ──────────────────────────────────────────
    _DCR_PATTERNS = re.compile(
        r"(?:DCR|correction request|exception request|onboarding request|mapping DCR|merge DCR|flag|submitted)"
        r".*?(?:has been submitted|was submitted|submitted to|has been logged|will be)",
        re.IGNORECASE | re.DOTALL,
    )
    _DCR_PROMPT_PATTERN = re.compile(r"Would you like me to (?:submit|raise|file|create|log)", re.IGNORECASE)
    _AFFIRMATIVE_PATTERN = re.compile(
        r"\b(?:yes|yeah|yep|yup|sure|go ahead|please|do it|submit|raise|generate|create|file|ok|okay|absolutely|definitely|please do|yes please|ya|y)\b",
        re.IGNORECASE,
    )
    _NEGATIVE_PATTERN = re.compile(r"\b(?:no|nope|nah|don't|do not|stop|cancel|nevermind|ignore)\b", re.IGNORECASE)

    @classmethod
    def _is_dcr_confirmation(cls, user_msg: str, last_assistant: str) -> bool:
        if not cls._DCR_PROMPT_PATTERN.search(last_assistant):
            return False
        if cls._NEGATIVE_PATTERN.search(user_msg.lower()):
            return False
        return bool(cls._AFFIRMATIVE_PATTERN.search(user_msg.lower()))

    @classmethod
    def _generate_dcr_number(cls, seed: str) -> str:
        d = hashlib.md5(seed.encode()).hexdigest()
        return f"DCR-2025-{int(d[:4], 16) % 9000 + 1000}"

    @classmethod
    def _build_dcr_confirmation(cls, last_content: str, inquiry_ctx: dict[str, Any] | None, seed: str) -> str:
        dcr_number = cls._generate_dcr_number(seed)
        lower = last_content.lower()
        if "territory" in lower and ("exception" in lower or "alignment" in lower):
            dcr_type = "Territory Alignment Exception Request"
        elif "merge" in lower or "consolidat" in lower:
            dcr_type = "Record Merge Request"
        elif "retroactive" in lower and "credit" in lower:
            dcr_type = "Retroactive Credit Correction Request"
        elif "onboarding" in lower:
            dcr_type = "HCP Onboarding Request"
        elif "deactivat" in lower:
            dcr_type = "Deactivation and Reallocation Request"
        elif "340b" in lower:
            dcr_type = "340B Exclusion Flag Activation Request"
        elif "mapping" in lower and ("ship" in lower or "867" in lower or "pharmacy" in lower):
            dcr_type = "Distributor Feed Mapping Correction Request"
        else:
            dcr_type = "Data Correction Request (DCR)"

        title = str(inquiry_ctx.get("title", "")) if inquiry_ctx else ""
        lines = [
            f"A {dcr_type} #{dcr_number} has been submitted successfully.", "",
            "**Submission Details**",
        ]
        if title:
            lines.append(f"• Issue: {title}")
        lines += [
            f"• Request Type: {dcr_type}", f"• Reference ID: {dcr_number}",
            "• Status: Submitted - Pending Review", "• Assigned To: Data Governance Team", "",
            "The Data Governance team will review and process this request. Typical turnaround: 3-5 business days.",
        ]
        return "\n".join(lines)

    @classmethod
    def _detect_dcr_action(cls, resolution_text: str) -> str | None:
        if not resolution_text or not cls._DCR_PATTERNS.search(resolution_text):
            return None
        lower = resolution_text.lower()
        if "territory exception" in lower or "alignment exception" in lower:
            return "Would you like me to submit a territory alignment exception request for this case?"
        if "merge dcr" in lower or "consolidat" in lower:
            return "Would you like me to submit a record merge request to resolve this?"
        if "retroactive" in lower and "credit" in lower:
            return "Would you like me to submit a retroactive credit correction request?"
        if "onboarding" in lower:
            return "Would you like me to submit an HCP onboarding request for this provider?"
        if "deactivat" in lower:
            return "Would you like me to submit a deactivation and reallocation request?"
        if "340b" in lower:
            return "Would you like me to submit a 340B exclusion flag activation request?"
        if "mapping" in lower and ("ship" in lower or "867" in lower or "pharmacy" in lower):
            return "Would you like me to submit a distributor feed mapping correction request?"
        return "Would you like me to submit a data correction request (DCR) for this issue?"

    # ── Response formatting ──────────────────────────────────────────────────
    _SECTION_HEADERS = {"key findings", "root cause", "root cause / issue analysis", "issue analysis", "data sources", "next steps"}
    _SKIP_SECTIONS = {"business impact", "recommended action", "recommended actions"}

    def _normalize_structured_output(self, answer: str) -> str:
        raw = [l.strip() for l in answer.splitlines() if l.strip()]
        if not raw:
            return ""
        out = []
        skip = False
        for line in raw:
            stripped = re.sub(r"^#+\s*", "", line).rstrip(":")
            sl = stripped.lower()
            if sl in self._SKIP_SECTIONS:
                skip = True
                continue
            if line.startswith("**") and line.endswith("**") and line.strip("*").strip().rstrip(":").lower() in self._SKIP_SECTIONS:
                skip = True
                continue
            if sl in self._SECTION_HEADERS:
                skip = False
                out.append(f"**{stripped}**")
                continue
            if sl == "summary":
                skip = False
                continue
            if line.startswith("**") and line.endswith("**"):
                inner = line.strip("*").strip().rstrip(":")
                if inner.lower() == "summary":
                    skip = False
                    continue
                if inner.lower() in self._SECTION_HEADERS:
                    skip = False
                    out.append(f"**{inner}**")
                    continue
            if skip:
                continue
            if line.startswith("•"):
                out.append(line)
            elif line.startswith("-") or re.match(r"^\d+\.", line):
                out.append(f"• {re.sub(r'^(-|\\d+\\.)\\s*', '', line)}")
            else:
                out.append(line)
        return "\n\n".join(out)

    @staticmethod
    def _dataset_reference_line(context: dict[str, Any]) -> str:
        ds = context.get("datasets_used", [])
        if not isinstance(ds, list):
            return ""
        names = list(dict.fromkeys(str(d).strip() for d in ds if str(d).strip()))
        return f"*Reference from {', '.join(names)}.*" if names else ""

    def _append_dataset_reference(self, answer: str, context: dict[str, Any]) -> str:
        ref = self._dataset_reference_line(context)
        clean = answer.strip()
        if not ref:
            return clean
        return f"{clean}\n\n{ref}" if clean else ref

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        protected = text.strip()
        for tok in ("Dr.", "Mr.", "Ms.", "Mrs.", "St.", "Sr.", "Jr."):
            protected = protected.replace(tok, tok.replace(".", "<dot>"))
        chunks = re.split(r"(?<=[.!?])\s+|\n+", protected)
        return [c.replace("<dot>", ".").strip().strip('"') for c in chunks if c.strip()]

    def _select_partial_sentences(self, query: str, text: str, limit: int = 3) -> list[str]:
        sentences = self._split_sentences(text)
        if not sentences:
            return []
        qt = set(self._content_tokens(query))
        scored = []
        for i, s in enumerate(sentences):
            st = set(self._content_tokens(s))
            overlap = len(qt & st)
            fz = self.index._fuzzy_score(query, s)
            score = overlap + 0.2 * fz
            if score > 0:
                scored.append((score, i, s))
        if not scored:
            return sentences[:2]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [x[2] for x in sorted(scored[:limit], key=lambda x: x[1])]

    @staticmethod
    def _generalize_text(text: str) -> str:
        replacements = [
            (re.compile(r"\bDr\.?\s+[A-Z][A-Za-z'`-]+(?:\s+[A-Z][A-Za-z'`-]+)+"), "the provider"),
            (re.compile(r"\bNPI\b\s*[:#-]?\s*\d{10}", re.IGNORECASE), "the provider's NPI"),
            (re.compile(r"\bHCO\s+ID\b\s*[:#-]?\s*[A-Z0-9-]+", re.IGNORECASE), "the account's HCO ID"),
            (re.compile(r"\b\d{5}\b"), "the relevant ZIP code"),
            (re.compile(r"\b[A-Z]{2,5}-\d{2,4}\b"), "the affected territory"),
        ]
        g = text
        for pat, rep in replacements:
            g = pat.sub(rep, g)
        return g.strip()

    def _fallback_bullets(self, query: str, context: dict[str, Any], mode: str) -> str:
        source = str(context.get("what_happened", "")).strip()
        if not source:
            return "• No matched playbook explanation is available."
        if mode == "full":
            items = self._split_sentences(source)
        elif mode == "vague":
            items = self._split_sentences(self._generalize_text(source))[:3]
        else:
            items = self._select_partial_sentences(query, source)
        return "\n\n".join(f"• {re.sub(r'^[-*]\\s*', '', re.sub(r'^\\d+\\.\\s*', '', ' '.join(i.replace('•', ' ').split())))}" for i in items if i.strip())

    def _classify_question_mode(self, query: str, context: dict[str, Any]) -> str:
        nq = normalize_text(query)
        qt = set(self._content_tokens(query))
        ql = len(TOKEN_PATTERN.findall(query))
        fs = self.index._fuzzy_score(query, str(context.get("field_rep_says", "")))
        if ql <= 5 and not self._query_has_identifier(query):
            return "vague"
        if any(nq.startswith(p) for p in VAGUE_QUERY_PREFIXES):
            return "vague"
        if fs >= 0.78 or (fs >= 0.7 and ql >= 10):
            return "full"
        return "partial"

    # ── Main entry point ─────────────────────────────────────────────────────
    def generate_answer(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
        chat_id: str | None = None,
        session_id: str | None = None,
    ) -> AssistantResult:
        # ── 1. DCR confirmation flow ─────────────────────────────────────────
        if conversation_history:
            last_assistant = None
            last_meta = None
            for msg in reversed(conversation_history):
                if msg.get("role") == "assistant":
                    last_assistant = msg
                    last_meta = msg.get("metadata") or {}
                    break
            if last_assistant and self._is_dcr_confirmation(user_message, str(last_assistant.get("content", ""))):
                iid = str(last_meta.get("matched_inquiry_id", "")) if last_meta else ""
                ictx = self._context_from_inquiry_id(iid) if iid else None
                dcr = self._build_dcr_confirmation(
                    str(last_assistant.get("content", "")), ictx, chat_id or iid or "default",
                )
                return AssistantResult(
                    content=dcr, matched_inquiry_id=iid or None,
                    matched_title=str(ictx.get("title", "")) if ictx else None,
                    confidence=float(last_meta.get("confidence") or 1.0) if last_meta else 1.0,
                )

        # ── 1b. Identifier follow-up flow ────────────────────────────────────
        is_follow_up = False
        user_message_combined = user_message
        if conversation_history:
            last_assistant = None
            last_meta = None
            orig_user = None
            for i in reversed(range(len(conversation_history))):
                msg = conversation_history[i]
                if msg.get("role") == "assistant":
                    last_assistant = msg
                    last_meta = msg.get("metadata") or {}
                    if i > 0 and conversation_history[i-1].get("role") == "user":
                        orig_user = conversation_history[i-1].get("content", "")
                    break
            
            if last_assistant:
                content_lower = str(last_assistant.get("content", "")).lower()
                if "please provide the" in content_lower and "before proceeding" in content_lower:
                    iid = str(last_meta.get("matched_inquiry_id", "")) if last_meta else ""
                    if iid:
                        ictx = self._context_from_inquiry_id(iid)
                        if ictx:
                            is_follow_up = True
                            context = [ictx]
                            pb_confidence = float(last_meta.get("confidence") or 1.0) if last_meta else 1.0
                            best = ictx
                            if orig_user:
                                user_message_combined = f"{orig_user}\n{user_message}"

        # ── 2. Build playbook context ────────────────────────────────────────
        if not is_follow_up:
            context, pb_confidence = self._build_context_block(user_message)
            best = context[0] if context else None

        # ── 3. Classify query type ───────────────────────────────────────────
        query_type = self._classify_query_type(user_message_combined, pb_confidence)

        # ── 4. Check identifier follow-ups (playbook and hybrid queries) ───────────
        if query_type in ("playbook", "hybrid") and best and not is_follow_up:
            required = self._required_identifiers(user_message, best)
            if required:
                fu = self._format_identifier_follow_up(required)
                return AssistantResult(
                    content=fu,
                    matched_inquiry_id=str(best["inquiry_id"]),
                    matched_title=str(best["title"]),
                    confidence=pb_confidence,
                )

        # ── 5. Data-only query → delegate to SQL engine ──────────────────────
        if query_type == "data":
            sid = session_id or chat_id or "default"
            sql_result = sql_process_query(user_message_combined, sid, best.get("steward_steps") if best else None)
            response_text = str(sql_result.get("response", "No data found."))
            return AssistantResult(
                content=response_text,
                matched_inquiry_id=str(best["inquiry_id"]) if best else None,
                matched_title=str(best["title"]) if best else None,
                confidence=pb_confidence,
            )

        # ── 6. Hybrid query → get SQL data + playbook context → LLM ─────────
        if query_type == "hybrid" and best:
            sid = session_id or chat_id or "default"
            sql_result = sql_process_query(user_message_combined, sid, best.get("steward_steps") if best else None)
            sql_response = str(sql_result.get("response", ""))
            
            sql_queries_raw = sql_result.get("sql", [])
            sql_queries = []
            if isinstance(sql_queries_raw, str):
                try:
                    import ast
                    parsed = ast.literal_eval(sql_queries_raw)
                    if isinstance(parsed, list):
                        sql_queries_raw = parsed
                    elif isinstance(parsed, dict) and "query" in parsed:
                        sql_queries_raw = [parsed]
                    else:
                        sql_queries_raw = [sql_queries_raw] if sql_queries_raw.strip() else []
                except Exception:
                    sql_queries_raw = [sql_queries_raw] if sql_queries_raw.strip() else []
            
            if isinstance(sql_queries_raw, list):
                for q in sql_queries_raw:
                    if isinstance(q, dict) and "query" in q:
                        sql_queries.append(str(q["query"]).strip())
                    elif isinstance(q, str) and q.strip():
                        # sometimes stringified dicts slip through AST if improperly escaped
                        import re
                        m = re.search(r"['\"]query['\"]:\s*['\"](.*?)['\"]\s*}", q, re.IGNORECASE | re.DOTALL)
                        if m:
                            sql_queries.append(m.group(1).strip())
                        else:
                            sql_queries.append(q.strip())
            
            if self.azure_client.is_ready:
                system_prompt = (
                    "You are an enterprise CRM support analyst. "
                    "Use the playbook context for background understanding and the SQL data results for actual numbers. "
                    "Provide a direct response without a 'Summary' heading. "
                    "Use explicitly exactly these exact sections: Key Findings, Root Cause / Issue Analysis. "
                    "Use bullet points (•) under those sections. Highlight important numbers and entities. "
                    "Do not invent facts. Do not apologize. Professional tone."
                )
                user_prompt = (
                    f"Rep question:\n{user_message}\n\n"
                    f"Playbook context (what_happened):\n{best['what_happened']}\n\n"
                    f"Playbook context (resolution):\n{best.get('resolution_and_response_to_rep', '')}\n\n"
                    f"SQL Data Results:\n{sql_response}\n\n"
                    "Combine the playbook narrative with the actual data numbers to give a comprehensive answer. "
                    "If the data shows specific numbers, use those. If the playbook provides context, include that. "
                    "OUTPUT FORMAT:\n"
                    "1. Start with a direct 1-2 sentence paragraph answering what happened (do NOT use a 'Summary' heading).\n"
                    "2. Add a 'Key Findings' section with bullet points.\n"
                    "3. Add a 'Root Cause / Issue Analysis' section with bullet points.\n"
                    "4. Bullet points must start with `• `.\n"
                    "5. Do NOT include Business Impact or Recommended Action sections.\n"
                    "6. CRITICAL: Never state or imply that a Data Correction Request (DCR) or similar request has already been submitted. We will ask the user to submit it instead."
                )
                answer = self.azure_client.chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.1, max_tokens=900,
                )
                if answer:
                    final = self._normalize_structured_output(answer.strip())
                    if final:
                        # Add DCR prompt if applicable
                        resolution = str(best.get("resolution_and_response_to_rep", ""))
                        dcr_prompt = self._detect_dcr_action(resolution)
                        parts = [final]
                        if dcr_prompt:
                            parts.append(f"**Recommended Action**\n{dcr_prompt}")
                        final_text = "\n\n".join(parts)

                        if sql_queries:
                            queries_text = "\n\n".join(f"```sql\n{q}\n```" for q in sql_queries)
                            final_text += f"\n\n**SQL Queries Used:**\n\n{queries_text}"

                        final_text = self._append_dataset_reference(final_text, best)
                        return AssistantResult(
                            content=final_text,
                            matched_inquiry_id=str(best["inquiry_id"]),
                            matched_title=str(best["title"]),
                            confidence=pb_confidence,
                        )

            # Fallback: just return SQL result
            fallback_res = sql_response or "No data found."
            if sql_queries:
                queries_text = "\n\n".join(f"```sql\n{q}\n```" for q in sql_queries)
                fallback_res += f"\n\n**SQL Queries Used:**\n\n{queries_text}"

            return AssistantResult(
                content=fallback_res,
                matched_inquiry_id=str(best["inquiry_id"]) if best else None,
                matched_title=str(best["title"]) if best else None,
                confidence=pb_confidence,
            )

        # ── 7. Playbook-only query → LLM with playbook context ──────────────
        if not best:
            return AssistantResult(
                content="Please provide the provider NPI ID, HCO ID, or territory ID, plus the exact issue.",
                matched_inquiry_id=None, matched_title=None, confidence=0.0,
            )

        decision_mode = self._classify_question_mode(user_message, best)
        fallback_content = self._append_dataset_reference(
            self._fallback_bullets(user_message, best, decision_mode), best,
        )

        if not self.azure_client.is_ready:
            return AssistantResult(
                content=fallback_content, matched_inquiry_id=str(best["inquiry_id"]),
                matched_title=str(best["title"]), confidence=pb_confidence,
            )

        system_prompt = (
            "You are an enterprise CRM support analyst. "
            "Use only the provided playbook context. "
            "Treat `what_happened` as primary evidence and `resolution_and_response_to_rep` as supporting context only. "
            "Do not mention internal field names. Do not invent facts. "
            "Do not apologize or use conversational filler. Professional tone.\n"
            "Provide a direct response without a 'Summary' heading. "
            "Use exactly these exact sections: Key Findings, Root Cause / Issue Analysis."
        )
        user_prompt = (
            f"Question mode: {decision_mode}\n\n"
            f"Rep question:\n{user_message}\n\n"
            f"Primary (`what_happened`):\n{best['what_happened']}\n\n"
            f"Supporting (`resolution_and_response_to_rep`):\n{best.get('resolution_and_response_to_rep', '')}\n\n"
            "OUTPUT FORMAT:\n"
            "1. Start with a direct 1-2 sentence paragraph answering what happened (do NOT use a 'Summary' heading).\n"
            "2. Add a 'Key Findings' section with bullet points.\n"
            "3. Add a 'Root Cause / Issue Analysis' section with bullet points.\n"
            "4. Bullet points must start with `• `.\n"
            "5. Omit empty sections.\n"
            "6. Do NOT include Business Impact or Recommended Action sections.\n"
            "7. CRITICAL: Never state or imply that a Data Correction Request (DCR) or similar request has already been submitted. We will ask the user to submit it instead.\n"
        )

        answer = self.azure_client.chat_completion(
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.1, max_tokens=900,
        )
        if not answer:
            return AssistantResult(
                content=fallback_content, matched_inquiry_id=str(best["inquiry_id"]),
                matched_title=str(best["title"]), confidence=pb_confidence,
            )

        final = self._normalize_structured_output(answer.strip())
        if not final:
            return AssistantResult(
                content=fallback_content, matched_inquiry_id=str(best["inquiry_id"]),
                matched_title=str(best["title"]), confidence=pb_confidence,
            )

        sql_queries = []
        try:
            sid = session_id or chat_id or "default"
            sql_result = sql_process_query(user_message_combined, sid, best.get("steward_steps") if best else None)
            sql_queries_raw = sql_result.get("sql", [])
            if isinstance(sql_queries_raw, str):
                try:
                    import ast
                    parsed = ast.literal_eval(sql_queries_raw)
                    if isinstance(parsed, list):
                        sql_queries_raw = parsed
                    elif isinstance(parsed, dict) and "query" in parsed:
                        sql_queries_raw = [parsed]
                    else:
                        sql_queries_raw = [sql_queries_raw] if sql_queries_raw.strip() else []
                except Exception:
                    sql_queries_raw = [sql_queries_raw] if sql_queries_raw.strip() else []
            
            if isinstance(sql_queries_raw, list):
                for q in sql_queries_raw:
                    if isinstance(q, dict) and "query" in q:
                        sql_queries.append(str(q["query"]).strip())
                    elif isinstance(q, str) and q.strip():
                        import re
                        m = re.search(r"['\"]query['\"]:\s*['\"](.*?)['\"]\s*}", q, re.IGNORECASE | re.DOTALL)
                        if m:
                            sql_queries.append(m.group(1).strip())
                        else:
                            sql_queries.append(q.strip())
        except Exception:
            pass

        resolution = str(best.get("resolution_and_response_to_rep", ""))
        dcr_prompt = self._detect_dcr_action(resolution)
        parts = [final]
        if dcr_prompt:
            parts.append(f"**Recommended Action**\n{dcr_prompt}")
        
        final_text = "\n\n".join(parts)
        if sql_queries:
            queries_text = "\n\n".join(f"```sql\n{q}\n```" for q in sql_queries)
            final_text += f"\n\n**SQL Queries Used:**\n\n{queries_text}"

        final_text = self._append_dataset_reference(final_text, best)

        return AssistantResult(
            content=final_text, matched_inquiry_id=str(best["inquiry_id"]),
            matched_title=str(best["title"]), confidence=pb_confidence,
        )
