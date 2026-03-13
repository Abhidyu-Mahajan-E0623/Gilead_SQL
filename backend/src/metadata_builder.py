"""Metadata builder — auto-discovers table schemas and relationships."""

import json
import itertools

from .database import Database
from .config import METADATA_CATALOG_PATH


class MetadataBuilder:
    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    def build(self) -> dict:
        schemas = self.db.get_all_schemas()
        metadata = {
            "database": "GileadCombinedDB",
            "rag_type": "semantic_layer",
            "tables": [],
            "canonical_relationships": [],
        }
        for table_name, columns in schemas.items():
            entry = {
                "table_name": table_name,
                "business_purpose": f"Auto-discovered table {table_name}",
                "grain": "inferred",
                "columns": [{"name": c["name"], "description": f"Column {c['name']}", "type": c["type"]} for c in columns],
                "joins": [],
            }
            metadata["tables"].append(entry)
        metadata["canonical_relationships"] = self._infer_relationships(schemas)

        METADATA_CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        METADATA_CATALOG_PATH.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return metadata

    def _infer_relationships(self, schemas: dict) -> list[dict]:
        rels = []
        tables = list(schemas.keys())
        for t1, t2 in itertools.combinations(tables, 2):
            for c1 in schemas[t1]:
                for c2 in schemas[t2]:
                    if c1["name"].lower() == c2["name"].lower():
                        score = self._overlap(t1, c1["name"], t2, c2["name"])
                        if score > 0.6:
                            rels.append({
                                "from": f"{t1}.{c1['name']}",
                                "to": f"{t2}.{c2['name']}",
                                "confidence": round(score, 2),
                                "type": "value_overlap",
                            })
        return rels

    def _overlap(self, t1: str, c1: str, t2: str, c2: str) -> float:
        df1 = self.db.execute(f'SELECT DISTINCT "{c1}" FROM "{t1}" LIMIT 500')
        df2 = self.db.execute(f'SELECT DISTINCT "{c2}" FROM "{t2}" LIMIT 500')
        v1 = set(df1[c1].dropna().tolist())
        v2 = set(df2[c2].dropna().tolist())
        if not v1 or not v2:
            return 0
        return len(v1 & v2) / len(v1 | v2)
