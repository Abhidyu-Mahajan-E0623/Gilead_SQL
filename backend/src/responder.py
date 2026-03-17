"""SQL-first responder using only user query + SQL results for answer synthesis."""

from __future__ import annotations

import ast
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from .azure_client import AzureOpenAIClient
from .query_engine import process_query as sql_process_query
from .utils import STOPWORDS, TOKEN_PATTERN, normalize_text, text_preview

LOGGER = logging.getLogger(__name__)

IDENTIFIER_PATTERNS = {
    "NPI": re.compile(r"\bNPI\b\s*[:#-]?\s*(\d{10})", re.IGNORECASE),
    "HCO ID": re.compile(r"\bHCO\s+ID\b\s*[:#-]?\s*([A-Z0-9_-]+)", re.IGNORECASE),
}
HCO_CODE_PATTERN = re.compile(r"\bHCO[-_][A-Z0-9_-]+\b", re.IGNORECASE)
HCP_CODE_PATTERN = re.compile(r"\bHCP[-_][A-Z0-9_-]+\b", re.IGNORECASE)
TERRITORY_ID_PATTERN = re.compile(r"\b(?:[A-Z]{2,5}-\d{2,4}|[A-Z]{1,3}\d{4,8})\b", re.IGNORECASE)
TERRITORY_CONTEXT_PATTERN = re.compile(
    r"\b(?:territory(?:\s+id)?(?:\s+code)?)\b[^A-Z0-9]{0,6}([A-Z]{1,5}-?\d{2,8})\b",
    re.IGNORECASE,
)
NON_TERRITORY_PREFIXES = {"DCR", "HCO", "HCP", "NPI", "SP", "ZIP", "IC"}
PROVIDER_ID_VALUE_PATTERN = re.compile(r"\b(\d{10})\b")
PROVIDER_REF_PATTERN = re.compile(r"\bdr\.?\s+[A-Za-z'`-]+\b", re.IGNORECASE)
PROVIDER_NAME_PATTERN = re.compile(r"\bDr\.?\s+([A-Z][A-Za-z'`-]+(?:\s+[A-Z][A-Za-z'`-]+)+)")
ACCOUNT_NAME_PATTERN = re.compile(
    r"\b(?:[A-Z][a-z&']+(?:\s+[A-Z][a-z&']+){0,3}\s+(?:Center|Hospital|Clinic|Pharmacy|Account|Facility|Practice|Site|Institute|University|Care|Medical|Group))\b"
)
VAGUE_QUERY_PREFIXES = (
    "what happened",
    "whats happening",
    "what is happening",
    "can you explain",
    "explain this",
    "why is this happening",
    "why did this happen",
)
DATA_QUERY_KEYWORDS = re.compile(
    r"\b(top\s+\d+|bottom\s+\d+|how\s+many|count|total|sum|average|min|max|"
    r"rank|list|show\s+me|sales|trx|nrx|volume|revenue|quota|attainment|"
    r"q[1-4]|quarter|year|ytd|mtd|2024|2025|2026|percentage|percent|%|"
    r"compare|comparison|growth|decline|trend)\b",
    re.IGNORECASE,
)
AGGREGATE_QUERY_HINTS = re.compile(
    r"\b(top|bottom|all|every|each|count|how\s+many|rank|compare|across|trend|growth|summarize|summary|breakdown|distribution)\b",
    re.IGNORECASE,
)
FOLLOW_UP_PREFIXES = (
    "what about",
    "how about",
    "and ",
    "also ",
    "then ",
    "for that",
    "for those",
    "for them",
    "same ",
    "that ",
    "those ",
    "these ",
    "it ",
    "they ",
    "them ",
    "can you also",
    "could you also",
    "what else",
)
FOLLOW_UP_REFERENCE_PATTERN = re.compile(
    r"\b(it|they|them|those|these|that|same|there|above|previous|earlier|follow[\s-]?up|also)\b",
    re.IGNORECASE,
)
LOW_CONTEXT_QUESTION_PATTERN = re.compile(
    r"^(why|how|when|where|which|who|what|did|does|is|are|was|were|can)\b",
    re.IGNORECASE,
)
DCR_TOPIC_PATTERN = re.compile(
    r"\b(dcr|merge|merged|credit|alignment|realignment|duplicate|retroactive|exception|dashboard)\b",
    re.IGNORECASE,
)
TIME_PERIOD_PATTERN = re.compile(
    r"\b(q[1-4]|quarter|month|ytd|mtd|2024|2025|2026)\b",
    re.IGNORECASE,
)
RETAIL_TOPIC_PATTERN = re.compile(
    r"\b(retail|retail status|retail flag|trx|nrx|prescrib|volume|dashboard|ddd)\b",
    re.IGNORECASE,
)
NON_RETAIL_TOPIC_PATTERN = re.compile(
    r"\b(non[-\s]?retail|ship[-\s]?to|867|outlet|shipment|account|hco)\b",
    re.IGNORECASE,
)
STATUS_TOPIC_PATTERN = re.compile(
    r"\b(status|active|inactive|retired|merged|duplicate|credit|flag)\b",
    re.IGNORECASE,
)
SIMPLIFY_REQUEST_PATTERN = re.compile(
    r"\b(explain|simplify|summarize|summary|in\s+(?:simple|plain|easy)\s+language|"
    r"easy\s+language|plain\s+english|layman(?:'s)?\s+terms|"
    r"break\s+it\s+down|can\s+you\s+explain)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class AssistantResult:
    content: str
    matched_inquiry_id: str | None
    matched_title: str | None
    confidence: float | None


@dataclass(slots=True)
class ReasoningPreview:
    summary: str
    details: list[str]


class ChatResponder:
    def __init__(self, azure_client: AzureOpenAIClient) -> None:
        self.azure_client = azure_client

    @staticmethod
    def _content_tokens(value: str) -> list[str]:
        return [t for t in TOKEN_PATTERN.findall(value.lower()) if t not in STOPWORDS]

    def _is_data_query(self, query: str) -> bool:
        return bool(DATA_QUERY_KEYWORDS.search(query))

    @classmethod
    def _extract_territory_id(cls, text: str) -> str | None:
        for match in TERRITORY_CONTEXT_PATTERN.finditer(text):
            candidate = match.group(1).upper()
            if cls._is_valid_territory_id(candidate):
                return candidate
        for match in TERRITORY_ID_PATTERN.finditer(text):
            candidate = match.group(0).upper()
            if cls._is_valid_territory_id(candidate):
                return candidate
        return None

    @staticmethod
    def _is_valid_territory_id(candidate: str) -> bool:
        value = re.sub(r"[^A-Z0-9-]", "", candidate.upper())
        if not value or not re.search(r"\d", value):
            return False
        prefix_match = re.match(r"^([A-Z]+)", value)
        if not prefix_match:
            return False
        return prefix_match.group(1) not in NON_TERRITORY_PREFIXES

    def _query_has_identifier(self, query: str) -> bool:
        return bool(
            any(pattern.search(query) for pattern in IDENTIFIER_PATTERNS.values())
            or HCO_CODE_PATTERN.search(query)
            or HCP_CODE_PATTERN.search(query)
            or self._extract_territory_id(query)
            or PROVIDER_ID_VALUE_PATTERN.search(query)
        )

    @staticmethod
    def _query_mentions_territory(query: str) -> bool:
        normalized = normalize_text(query)
        return "territory" in normalized or bool(ChatResponder._extract_territory_id(query))

    @staticmethod
    def _query_mentions_specific_provider(query: str) -> bool:
        if PROVIDER_REF_PATTERN.search(query) or PROVIDER_NAME_PATTERN.search(query):
            return True
        lowered = query.lower()
        if any(word in lowered for word in ("doctor", "physician", "provider", "hcp")):
            return not bool(AGGREGATE_QUERY_HINTS.search(query))
        return False

    @staticmethod
    def _query_mentions_specific_account(query: str) -> bool:
        if ACCOUNT_NAME_PATTERN.search(query):
            return True
        lowered = query.lower()
        if any(word in lowered for word in ("account", "hco", "facility", "hospital", "clinic", "pharmacy", "center", "practice", "site")):
            return not bool(AGGREGATE_QUERY_HINTS.search(query))
        return False

    @staticmethod
    def _format_identifier_follow_up(required: list[str]) -> str:
        if len(required) == 1:
            noun = "records" if required[0].endswith("s") else "record"
            return f"Please provide the {required[0]} so the correct {noun} can be confirmed before proceeding."
        if len(required) == 2:
            joined = " and ".join(required)
        else:
            joined = ", ".join(required[:-1]) + f", and {required[-1]}"
        return f"Please provide the {joined} so the correct records can be confirmed before proceeding."

    def _required_identifiers(self, query: str) -> list[str]:
        required: list[str] = []
        if self._query_mentions_specific_provider(query) and not (
            IDENTIFIER_PATTERNS["NPI"].search(query) or HCP_CODE_PATTERN.search(query)
        ):
            required.append("NPI ID")
        if self._query_mentions_specific_account(query) and not (
            IDENTIFIER_PATTERNS["HCO ID"].search(query) or HCO_CODE_PATTERN.search(query)
        ):
            required.append("HCO ID")
        if self._query_mentions_territory(query) and not self._extract_territory_id(query):
            required.append("territory ID")
        return required

    def _is_too_vague(self, query: str) -> bool:
        normalized = normalize_text(query)
        token_count = len(TOKEN_PATTERN.findall(query))
        if token_count <= 4 and not self._query_has_identifier(query) and not self._is_data_query(query):
            return True
        return any(normalized.startswith(prefix) for prefix in VAGUE_QUERY_PREFIXES) and not self._query_has_identifier(query)

    @staticmethod
    def _message_preview(value: str, max_length: int = 140) -> str:
        compact = text_preview(value, length=max_length)
        compact = re.sub(r"`{3}.*?`{3}", "", compact, flags=re.DOTALL)
        return compact.strip()

    @staticmethod
    def _last_message_for_role(
        conversation_history: list[dict[str, Any]] | None,
        role: str,
    ) -> str | None:
        if not conversation_history:
            return None
        for message in reversed(conversation_history):
            if message.get("role") == role:
                content = str(message.get("content", "")).strip()
                if content:
                    return content
        return None

    @staticmethod
    def _extract_provider_label(text: str) -> str | None:
        match = PROVIDER_NAME_PATTERN.search(text)
        if match:
            return f"Dr. {match.group(1).strip()}"
        generic = PROVIDER_REF_PATTERN.search(text)
        if generic:
            return generic.group(0).strip().replace("dr ", "Dr. ").replace("dr.", "Dr.")
        return None

    def _looks_like_follow_up(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> bool:
        if not conversation_history:
            return False
        lowered = user_message.strip().lower()
        token_count = len(TOKEN_PATTERN.findall(user_message))
        if any(lowered.startswith(prefix) for prefix in FOLLOW_UP_PREFIXES):
            return True
        if FOLLOW_UP_REFERENCE_PATTERN.search(user_message):
            return True
        return token_count <= 8 and bool(LOW_CONTEXT_QUESTION_PATTERN.search(lowered))

    def _rewrite_follow_up_with_context(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> str | None:
        if not self._looks_like_follow_up(user_message, conversation_history):
            return None

        previous_user = self._last_message_for_role(conversation_history, "user")
        previous_assistant = self._last_message_for_role(conversation_history, "assistant")

        if self.azure_client.is_ready and (previous_user or previous_assistant):
            context_parts: list[str] = []
            if previous_user:
                context_parts.append(f"Previous user question:\n{previous_user.strip()}")
            if previous_assistant:
                context_parts.append(
                    f"Previous assistant answer summary:\n{self._message_preview(previous_assistant, max_length=220)}"
                )
            context_parts.append(f"Latest follow-up question:\n{user_message.strip()}")
            rewritten = self.azure_client.chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Rewrite the user's latest follow-up into a standalone analytics or CRM support question. "
                            "Use the recent conversation only to restore missing context. "
                            "Preserve IDs, providers, territories, products, DCR wording, and time periods. "
                            "Do not answer the question. Return only the rewritten question."
                        ),
                    },
                    {"role": "user", "content": "\n\n".join(context_parts)},
                ],
                temperature=0.1,
                max_tokens=140,
            )
            if rewritten:
                return rewritten.strip()

        context_lines: list[str] = []
        if previous_user:
            context_lines.append(f"Previous question: {self._message_preview(previous_user)}")
        if previous_assistant:
            context_lines.append(f"Previous answer summary: {self._message_preview(previous_assistant)}")
        if not context_lines:
            return None
        return (
            "Use this recent chat context when answering the latest follow-up.\n"
            + "\n".join(context_lines)
            + f"\nLatest follow-up question: {user_message.strip()}"
        )

    @staticmethod
    def _is_simplification_request(user_message: str) -> bool:
        return bool(SIMPLIFY_REQUEST_PATTERN.search(user_message))

    def _simplify_previous_answer(self, previous_answer: str) -> str:
        if not previous_answer.strip():
            return ""
        if self.azure_client.is_ready:
            simplified = self.azure_client.chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Rewrite the response in simple, plain language for a non-technical reader. "
                            "Use 4-7 short sentences. Do not use headings or bullet points. "
                            "Keep facts and numbers unchanged."
                        ),
                    },
                    {"role": "user", "content": previous_answer.strip()},
                ],
                temperature=0.2,
                max_tokens=220,
            )
            if simplified:
                return simplified.strip()

        # Fallback: strip headings/bullets and keep a compact preview.
        lines = [line.strip() for line in previous_answer.splitlines() if line.strip()]
        cleaned: list[str] = []
        for line in lines:
            if line.startswith("**") and line.endswith("**"):
                continue
            if line.startswith("-") or re.match(r"^\d+\.", line):
                line = re.sub(r"^(-|\d+\.)\s*", "", line)
            cleaned.append(line)
        compact = " ".join(cleaned)
        return text_preview(compact, length=420) if compact else previous_answer.strip()

    def _describe_query_focus(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> str:
        provider = self._extract_provider_label(user_message)
        territory = self._extract_territory_id(user_message)
        hco_id_match = HCO_CODE_PATTERN.search(user_message)
        npi_match = PROVIDER_ID_VALUE_PATTERN.search(user_message)

        focus_parts: list[str] = []
        if provider:
            focus_parts.append(provider)
        if territory:
            focus_parts.append(f"territory {territory}")
        if hco_id_match:
            focus_parts.append(f"HCO {hco_id_match.group(0).upper()}")
        if npi_match:
            focus_parts.append(f"NPI {npi_match.group(1)}")
        if DCR_TOPIC_PATTERN.search(user_message):
            focus_parts.append("the DCR / merge context")
        if TIME_PERIOD_PATTERN.search(user_message):
            focus_parts.append("the requested time period")

        if focus_parts:
            return ", ".join(focus_parts)

        previous_user = self._last_message_for_role(conversation_history, "user")
        if previous_user and self._looks_like_follow_up(user_message, conversation_history):
            return f"the earlier thread about {self._message_preview(previous_user, max_length=90)}"

        keyword_tokens = [token for token in TOKEN_PATTERN.findall(user_message) if token.lower() not in STOPWORDS]
        if keyword_tokens:
            return " ".join(keyword_tokens[:8])
        return "the current request"

    def _build_hardcoded_reasoning_preview(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> ReasoningPreview:
        """Original regex-based reasoning preview — used as fallback when LLM is unavailable."""
        focus = self._describe_query_focus(user_message, conversation_history)
        is_follow_up = self._looks_like_follow_up(user_message, conversation_history)
        is_dcr_related = bool(DCR_TOPIC_PATTERN.search(user_message))
        territory = self._extract_territory_id(user_message)
        provider = self._extract_provider_label(user_message)
        hco_id_match = HCO_CODE_PATTERN.search(user_message)
        npi_match = PROVIDER_ID_VALUE_PATTERN.search(user_message)
        time_period = TIME_PERIOD_PATTERN.search(user_message)

        details = [f"I am understanding the user query and the key request around {focus}."]

        if is_follow_up:
            details.append(
                "I am applying recent context from this chat so the follow-up stays aligned with the earlier request."
            )

        if npi_match:
            details.append(f"I am preparing SQL to verify NPI {npi_match.group(1)} across provider, alignment, and credit-related records.")
        elif provider:
            details.append(f"I am preparing SQL checks around {provider} to verify identity, attribution, and related records.")
        elif hco_id_match:
            details.append(f"I am preparing SQL to verify account and affiliation data for {hco_id_match.group(0).upper()}.")
        elif territory:
            details.append(f"I am preparing SQL to verify territory mapping and effective dates for {territory}.")

        if is_dcr_related:
            details.append(
                "I am checking DCR, merge, duplicate-record, alignment, and credit-impact signals before deciding what the data supports."
            )
        if RETAIL_TOPIC_PATTERN.search(user_message):
            details.append(
                "I am performing SQL checks for retail status, retail flags, and recent retail volume where that data is relevant."
            )
        if NON_RETAIL_TOPIC_PATTERN.search(user_message):
            details.append(
                "I am gathering non-retail, ship-to, or account-level signals if they are needed to explain the result."
            )
        if STATUS_TOPIC_PATTERN.search(user_message) and not is_dcr_related:
            details.append("I am checking active or inactive status flags, credit indicators, and any record-level changes tied to this request.")
        if self._is_data_query(user_message) and not RETAIL_TOPIC_PATTERN.search(user_message):
            details.append("I am mapping the request to the right filters, metrics, and joins before pulling the final data slice.")
        if time_period:
            details.append(f"I am keeping the requested time frame in scope while I compare the returned data with {time_period.group(0).upper()}.")

        details.append("I am gathering the returned rows, validating the context, and preparing the final response.")

        summary = f"Tracing {focus} through the relevant data checks."
        return ReasoningPreview(summary=summary, details=details)

    def build_reasoning_preview(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> ReasoningPreview:
        """Try LLM-generated reasoning steps first; fall back to hardcoded regex logic."""
        focus = self._describe_query_focus(user_message, conversation_history)

        # Attempt LLM-powered reasoning
        if self.azure_client.is_ready:
            try:
                llm_steps = self.azure_client.generate_reasoning_steps(
                    user_message=user_message,
                    focus_description=focus,
                )
                if llm_steps and len(llm_steps) >= 2:
                    summary = f"Tracing {focus} through the relevant data checks."
                    return ReasoningPreview(summary=summary, details=llm_steps)
            except Exception:
                LOGGER.warning("LLM reasoning preview failed, falling back to hardcoded logic")

        # Fallback: deterministic regex-based preview
        return self._build_hardcoded_reasoning_preview(user_message, conversation_history)

    def _prepare_query_message(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> str:
        prepared_message = user_message
        used_identifier_context = False
        if conversation_history:
            for index in reversed(range(len(conversation_history))):
                message = conversation_history[index]
                if message.get("role") != "assistant":
                    continue
                assistant_content = str(message.get("content", "")).lower()
                if "please provide the" in assistant_content and "before proceeding" in assistant_content:
                    if index > 0 and conversation_history[index - 1].get("role") == "user":
                        original_user = str(conversation_history[index - 1].get("content", "")).strip()
                        if original_user:
                            prepared_message = f"{original_user}\n{user_message}"
                            used_identifier_context = True
                    break

        if used_identifier_context:
            return prepared_message

        rewritten_follow_up = self._rewrite_follow_up_with_context(prepared_message, conversation_history)
        return rewritten_follow_up or prepared_message

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
        digest = hashlib.md5(seed.encode()).hexdigest()
        return f"DCR-2025-{int(digest[:4], 16) % 9000 + 1000}"

    @classmethod
    def _build_dcr_confirmation(cls, last_content: str, seed: str) -> str:
        dcr_number = cls._generate_dcr_number(seed)
        lowered = last_content.lower()
        if "territory" in lowered and ("exception" in lowered or "alignment" in lowered):
            dcr_type = "Territory Alignment Exception Request"
        elif "merge" in lowered or "duplicate" in lowered:
            dcr_type = "Record Merge Request"
        elif "retroactive" in lowered and "credit" in lowered:
            dcr_type = "Retroactive Credit Correction Request"
        elif "onboarding" in lowered or "missing from crm" in lowered:
            dcr_type = "HCP Onboarding Request"
        elif "deactivat" in lowered or "retired" in lowered:
            dcr_type = "Deactivation and Reallocation Request"
        elif "340b" in lowered:
            dcr_type = "340B Exclusion Flag Activation Request"
        elif "mapping" in lowered and ("ship" in lowered or "867" in lowered or "pharmacy" in lowered):
            dcr_type = "Distributor Feed Mapping Correction Request"
        else:
            dcr_type = "Data Correction Request (DCR)"

        lines = [
            f"A {dcr_type} #{dcr_number} has been submitted successfully.",
            "",
            "**Submission Details**",
            f"- Request Type: {dcr_type}",
            f"- Reference ID: {dcr_number}",
            "- Status: Submitted - Pending Review",
            "- Assigned To: Data Governance Team",
            "",
            "The Data Governance team will review and process this request. Typical turnaround: 3-5 business days.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _extract_sql_queries(sql_queries_raw: Any) -> list[str]:
        parsed = sql_queries_raw
        queries: list[str] = []
        if isinstance(parsed, str):
            try:
                literal = ast.literal_eval(parsed)
                if isinstance(literal, list):
                    parsed = literal
                elif isinstance(literal, dict) and "query" in literal:
                    parsed = [literal]
                else:
                    parsed = [parsed] if parsed.strip() else []
            except Exception:
                parsed = [parsed] if parsed.strip() else []

        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and "query" in item:
                    queries.append(str(item["query"]).strip())
                elif isinstance(item, str) and item.strip():
                    match = re.search(r"['\"]query['\"]:\s*['\"](.*?)['\"]\s*}", item, re.IGNORECASE | re.DOTALL)
                    queries.append(match.group(1).strip() if match else item.strip())
        return queries

    _SECTION_HEADERS = {
        "key findings",
        "detailed analysis",
        "root cause",
        "root cause / issue analysis",
        "issue analysis",
    }
    _SKIP_SECTIONS = {"business impact", "recommended action", "recommended actions"}

    def _normalize_structured_output(self, answer: str) -> str:
        raw_lines = [line.strip() for line in answer.splitlines() if line.strip()]
        if not raw_lines:
            return ""
        output: list[str] = []
        skip = False
        for line in raw_lines:
            stripped = re.sub(r"^#+\s*", "", line).rstrip(":")
            lowered = stripped.lower()
            if lowered in self._SKIP_SECTIONS:
                skip = True
                continue
            if line.startswith("**") and line.endswith("**"):
                inner = line.strip("*").strip().rstrip(":")
                lowered = inner.lower()
                if lowered in self._SKIP_SECTIONS:
                    skip = True
                    continue
                if lowered == "summary":
                    skip = False
                    continue
                if lowered in self._SECTION_HEADERS:
                    skip = False
                    output.append(f"**{inner}**")
                    continue
            if lowered == "summary":
                skip = False
                continue
            if lowered in self._SECTION_HEADERS:
                skip = False
                output.append(f"**{stripped}**")
                continue
            if skip:
                continue
            if line.startswith("-") or re.match(r"^\d+\.", line):
                clean = re.sub(r"^(-|\d+\.)\s*", "", line)
                output.append(f"- {clean}")
            else:
                output.append(line)
        return "\n\n".join(output)

    @classmethod
    def _detect_dcr_action(cls, user_text: str, answer_text: str) -> str | None:
        lowered = f"{user_text}\n{answer_text}".lower()
        if "territory" in lowered and any(term in lowered for term in ("exception", "alignment", "realignment", "reassign")):
            return "Would you like me to submit a territory alignment exception request for this case?"
        if any(term in lowered for term in ("merge", "duplicate record", "duplicate hcp")):
            return "Would you like me to submit a record merge request to resolve this?"
        if "retroactive" in lowered and "credit" in lowered:
            return "Would you like me to submit a retroactive credit correction request?"
        if any(term in lowered for term in ("onboarding", "missing from crm", "missing provider", "new prescriber")):
            return "Would you like me to submit an HCP onboarding request for this provider?"
        if any(term in lowered for term in ("retired", "deactivated", "inactive physician")):
            return "Would you like me to submit a deactivation and reallocation request?"
        if "340b" in lowered:
            return "Would you like me to submit a 340B exclusion flag activation request?"
        if "mapping" in lowered and any(term in lowered for term in ("ship", "867", "pharmacy")):
            return "Would you like me to submit a distributor feed mapping correction request?"
        if any(term in lowered for term in ("missing credit", "wrong territory", "volume disappeared", "credit not appearing")):
            return "Would you like me to submit a data correction request (DCR) for this issue?"
        return None

    def _build_answer_from_sql(self, user_message: str, sql_response: str) -> str | None:
        if not self.azure_client.is_ready:
            return None

        system_prompt = (
            "You are an enterprise CRM support analyst. "
            "Use only the user question and SQL data results provided. "
            "Do not rely on any unstated background context. "
            "If the SQL results are partial or inconclusive, state exactly what they do and do not prove based on the Data Investigation Framework logic. "
            "Your goal is to 'connect the dots' for the user: explain *why* something happened (e.g., 'A merge occurred in OneKey, retiring the old record and moving volume to a new territory'). "
            "Use the exact terminology from the guide (Merge, Move, Retail vs Non-Retail, Reporting Lag). "
            "Provide a direct response without a 'Summary' heading. "
            "Use exactly these sections: Key Findings, Detailed Analysis. "
            "Use bullet points for list items. "
            "Do not invent facts. Do not apologize. Professional tone. "
            "Never state or imply that a Data Correction Request (DCR) or similar request has already been submitted."
        )
        user_prompt = (
            f"User question:\n{user_message}\n\n"
            f"SQL Data Results:\n{sql_response}\n\n"
            "OUTPUT FORMAT:\n"
            "1. Start with a direct 1-2 sentence paragraph answering the question.\n"
            "2. Add a 'Key Findings' section with bullet points.\n"
            "3. Add a 'Detailed Analysis' section with bullet points.\n"
            "4. Do not include Business Impact or Recommended Action sections.\n"
            "5. If evidence is incomplete, say that clearly in Detailed Analysis.\n"
        )
        return self.azure_client.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=900,
        )

    @staticmethod
    def _append_sql_queries(answer: str, sql_queries: list[str]) -> str:
        return answer if answer else ""

    @staticmethod
    def _is_control_response(response_text: str) -> bool:
        lowered = response_text.lower()
        return lowered.startswith("please provide the") or lowered.startswith("i noticed you're referring")

    def generate_answer(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
        chat_id: str | None = None,
        session_id: str | None = None,
    ) -> AssistantResult:
        if conversation_history and self._is_simplification_request(user_message):
            previous_answer = self._last_message_for_role(conversation_history, "assistant")
            if previous_answer:
                simplified = self._simplify_previous_answer(previous_answer)
                if simplified:
                    return AssistantResult(
                        content=simplified,
                        matched_inquiry_id=None,
                        matched_title=None,
                        confidence=None,
                    )

        if conversation_history:
            last_assistant = next((msg for msg in reversed(conversation_history) if msg.get("role") == "assistant"), None)
            if last_assistant and self._is_dcr_confirmation(user_message, str(last_assistant.get("content", ""))):
                dcr = self._build_dcr_confirmation(str(last_assistant.get("content", "")), chat_id or session_id or "default")
                return AssistantResult(content=dcr, matched_inquiry_id=None, matched_title=None, confidence=None)

        user_message_combined = self._prepare_query_message(user_message, conversation_history)

        required = self._required_identifiers(user_message_combined)
        if required:
            return AssistantResult(
                content=self._format_identifier_follow_up(required),
                matched_inquiry_id=None,
                matched_title=None,
                confidence=None,
            )

        if self._is_too_vague(user_message_combined):
            return AssistantResult(
                content="Please provide the provider NPI ID, HCO ID, or territory ID, plus the exact issue.",
                matched_inquiry_id=None,
                matched_title=None,
                confidence=None,
            )

        sid = session_id or chat_id or "default"
        sql_result = sql_process_query(user_message_combined, sid)
        response_text = str(sql_result.get("response", "No data found.")).strip()
        sql_queries = self._extract_sql_queries(sql_result.get("sql", []))

        if self._is_control_response(response_text):
            return AssistantResult(content=response_text, matched_inquiry_id=None, matched_title=None, confidence=None)
        if not sql_result.get("success", True):
            return AssistantResult(content=response_text, matched_inquiry_id=None, matched_title=None, confidence=None)

        answer = self._build_answer_from_sql(user_message, response_text)
        if not answer:
            fallback = response_text or "No data found."
            return AssistantResult(content=fallback, matched_inquiry_id=None, matched_title=None, confidence=None)

        final = self._normalize_structured_output(answer.strip())
        final_text = final or response_text or "No data found."

        dcr_prompt = self._detect_dcr_action(user_message_combined, final_text)
        if dcr_prompt:
            final_text = f"{final_text}\n\n{dcr_prompt}"

        return AssistantResult(content=final_text, matched_inquiry_id=None, matched_title=None, confidence=None)
