"""Semantic mapper — injects dimension hints into user questions."""

import re
import json
import os

from .config import METADATA_CATALOG_PATH


def load_metadata() -> dict | None:
    if not METADATA_CATALOG_PATH.exists():
        return None
    with open(METADATA_CATALOG_PATH, "r") as f:
        return json.load(f)


def build_dimension_map(metadata: dict) -> dict[str, str]:
    dm: dict[str, str] = {}
    for table in metadata["tables"]:
        tn = table["table_name"]
        for col in table["columns"]:
            cn = col["name"].lower()
            if "territory name" in cn:
                dm["territory"] = f'{tn}."Territory Name"'
            if "region name" in cn:
                dm["region"] = f'{tn}."Region Name"'
            if "area id" in cn:
                dm["area"] = f'{tn}."Area ID"'
            if "hcp id" in cn:
                dm["hcp"] = f'{tn}."HCP ID"'
            if "account id" in cn:
                dm["account"] = f'{tn}."Account ID"'
    return dm


def normalize_question(question: str) -> str:
    metadata = load_metadata()
    if not metadata:
        return question
    dm = build_dimension_map(metadata)
    normalized = question
    normalized = re.sub(r"(Territory|Region|Area)(\d+)", r"\1 \2", normalized)
    for key, col in dm.items():
        if key in normalized.lower():
            normalized += f"\n\nImportant: Use column {col} for {key}."
    return normalized
