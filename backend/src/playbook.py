"""Playbook index — loads JSON playbook and provides semantic + lexical search."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from .azure_client import AzureOpenAIClient
from .utils import normalize_text

try:
    from rapidfuzz import fuzz
except Exception:
    fuzz = None

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class InquiryRecord:
    inquiry_id: str
    category: str
    title: str
    field_rep_says: str
    what_happened: str
    steward_steps: list[str]
    datasets_used: list[str]
    resolution_and_response_to_rep: str


@dataclass(slots=True)
class SearchResult:
    inquiry: InquiryRecord
    score: float
    lexical_score: float
    semantic_score: float | None


class PlaybookIndex:
    def __init__(
        self,
        playbook_path: Path,
        azure_client: AzureOpenAIClient,
        embedding_cache_path: Path,
    ) -> None:
        self.playbook_path = playbook_path
        self.azure_client = azure_client
        self.embedding_cache_path = embedding_cache_path
        self.embedding_cache_path.parent.mkdir(parents=True, exist_ok=True)

        self.inquiries = self._load_inquiries()
        self._search_text = {
            inq.inquiry_id: self._build_search_text(inq) for inq in self.inquiries
        }
        self._token_sets = {
            inq.inquiry_id: set(normalize_text(self._search_text[inq.inquiry_id]).split())
            for inq in self.inquiries
        }
        self._embeddings: dict[str, list[float]] = {}
        self._semantic_enabled = False
        self._init_embeddings()

    def _load_inquiries(self) -> list[InquiryRecord]:
        if not self.playbook_path.exists():
            raise FileNotFoundError(f"Playbook not found: {self.playbook_path}")
        data = json.loads(self.playbook_path.read_text(encoding="utf-8"))
        inquiries = []
        for item in data.get("inquiries", []):
            inquiries.append(InquiryRecord(
                inquiry_id=str(item.get("inquiry_id", "")),
                category=item.get("category", ""),
                title=item.get("title", ""),
                field_rep_says=item.get("field_rep_says", ""),
                what_happened=item.get("what_happened", ""),
                steward_steps=item.get("steward_steps", []),
                datasets_used=item.get("datasets_used", []),
                resolution_and_response_to_rep=item.get("resolution_and_response_to_rep", ""),
            ))
        if not inquiries:
            raise ValueError("No inquiries in playbook")
        return inquiries

    @staticmethod
    def _build_search_text(inq: InquiryRecord) -> str:
        return (
            f"Category: {inq.category}. Title: {inq.title}. "
            f"Field rep says: {inq.field_rep_says}. What happened: {inq.what_happened}."
        )

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    @staticmethod
    def _keyword_overlap_score(qt: set[str], tt: set[str]) -> float:
        if not qt or not tt:
            return 0.0
        return len(qt & tt) / len(qt | tt)

    @staticmethod
    def _fuzzy_score(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        if fuzz is not None:
            return fuzz.token_set_ratio(a, b) / 100.0
        at = set(normalize_text(a).split())
        bt = set(normalize_text(b).split())
        return PlaybookIndex._keyword_overlap_score(at, bt)

    # ── embeddings ────────────────────────────────────────────────────────────
    def _init_embeddings(self) -> None:
        if not self.azure_client.is_ready:
            return
        if self._load_embeddings_from_cache():
            self._semantic_enabled = True
            LOGGER.info("Loaded embeddings from cache")
            return
        vectors: dict[str, list[float]] = {}
        batch_size = 6
        ids = [inq.inquiry_id for inq in self.inquiries]
        for start in range(0, len(ids), batch_size):
            batch_ids = ids[start : start + batch_size]
            batch_text = [self._search_text[i] for i in batch_ids]
            vecs = self.azure_client.embed_texts(batch_text)
            if len(vecs) != len(batch_ids):
                self._embeddings = {}
                self._semantic_enabled = False
                return
            for i, v in zip(batch_ids, vecs, strict=False):
                vectors[i] = v
        if len(vectors) != len(ids):
            return
        self._embeddings = vectors
        self._semantic_enabled = True
        self._save_embeddings_to_cache()

    def _load_embeddings_from_cache(self) -> bool:
        if not self.embedding_cache_path.exists():
            return False
        try:
            payload = json.loads(self.embedding_cache_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if payload.get("embedding_deployment") != self.azure_client.settings.embedding_deployment:
            return False
        vectors = payload.get("vectors", {})
        ids = {inq.inquiry_id for inq in self.inquiries}
        if set(vectors.keys()) != ids or not vectors:
            return False
        sample = next(iter(vectors.values()))
        if not isinstance(sample, list) or not sample:
            return False
        self._embeddings = vectors
        return True

    def _save_embeddings_to_cache(self) -> None:
        payload = {
            "embedding_deployment": self.azure_client.settings.embedding_deployment,
            "vectors": self._embeddings,
        }
        self.embedding_cache_path.write_text(json.dumps(payload), encoding="utf-8")

    # ── search ────────────────────────────────────────────────────────────────
    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        nq = normalize_text(query)
        qt = set(nq.split())
        semantic_by_id: dict[str, float] = {}
        if self._semantic_enabled:
            qv = self.azure_client.embed_texts([query])
            if qv and qv[0]:
                for inq in self.inquiries:
                    v = self._embeddings.get(inq.inquiry_id, [])
                    c = self._cosine_similarity(qv[0], v)
                    semantic_by_id[inq.inquiry_id] = max(0.0, (c + 1.0) / 2.0)
            else:
                self._semantic_enabled = False

        scored: list[SearchResult] = []
        for inq in self.inquiries:
            ff = self._fuzzy_score(query, inq.field_rep_says)
            ft = self._fuzzy_score(query, inq.title)
            fc = self._fuzzy_score(query, inq.what_happened)
            ol = self._keyword_overlap_score(qt, self._token_sets[inq.inquiry_id])
            lex = 0.44 * ff + 0.24 * ft + 0.20 * fc + 0.12 * ol
            sem = semantic_by_id.get(inq.inquiry_id)
            combined = (0.65 * sem + 0.35 * lex) if sem is not None else lex
            scored.append(SearchResult(inquiry=inq, score=combined, lexical_score=lex, semantic_score=sem))

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[: max(1, top_k)]

    @staticmethod
    def confidence(results: list[SearchResult]) -> float:
        if not results:
            return 0.0
        top = results[0].score
        second = results[1].score if len(results) > 1 else 0.0
        margin = max(0.0, top - second)
        return round(min(1.0, max(0.0, top * 0.83 + margin * 0.42)), 4)
