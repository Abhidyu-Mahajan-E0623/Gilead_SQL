"""Result set normalizer — display-only standardization."""

import re
import json
import os

import pandas as pd
from typing import Optional

from .config import NORMALIZATION_CONFIG_PATH


def load_normalization_config(config_path: str | None = None) -> dict:
    path = config_path or str(NORMALIZATION_CONFIG_PATH)
    if not os.path.exists(path):
        return {"default_rules": {"trim_whitespace": True}, "columns": {}}
    with open(path, "r") as f:
        return json.load(f)


def normalize_token(value, case=None, strip_chars=None, collapse_non_alnum=False, trim_whitespace=True) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return value
    t = str(value)
    if trim_whitespace:
        t = t.strip()
    if case == "upper":
        t = t.upper()
    elif case == "lower":
        t = t.lower()
    if strip_chars:
        for ch in strip_chars:
            t = t.replace(ch, "")
    if collapse_non_alnum:
        t = re.sub(r"[^A-Za-z0-9]+", "", t)
    return t


def normalize_resultset(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    result = df.copy()
    columns_config = config.get("columns", {})
    default_rules = config.get("default_rules", {})
    for col in result.columns:
        col_lower = col.lower()
        col_rules = None
        for cc, rules in columns_config.items():
            if cc.lower() == col_lower:
                col_rules = rules
                break
        if col_rules:
            result[col] = result[col].apply(
                lambda v: normalize_token(
                    v, case=col_rules.get("case"), strip_chars=col_rules.get("strip_chars"),
                    collapse_non_alnum=col_rules.get("collapse_non_alnum", False),
                    trim_whitespace=col_rules.get("trim_whitespace", True),
                )
            )
        elif default_rules.get("trim_whitespace", False) and result[col].dtype == "object":
            result[col] = result[col].apply(lambda v: normalize_token(v, trim_whitespace=True) if isinstance(v, str) else v)
    return result
