"""Central configuration — loads .env and defines all paths + Azure settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# ── directory layout ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
PROJECT_DIR = BASE_DIR.parent                               # Gilead_Data_POC/
INPUT_DIR = PROJECT_DIR / "Input"                           # shared input folder
DATA_DIR = BASE_DIR / "data"                                # runtime data (chat db, cache)
STATE_DIR = BASE_DIR / "state"                              # session store, excel shadow

PLAYBOOK_PATH = INPUT_DIR / "GILEAD_Field_Inquiry_Playbook.json"
DATA_FILE_DIR = INPUT_DIR                                    # Sample_Data 2.xlsx lives here
DB_PATH = DATA_DIR / "chat_history.db"
EMBED_CACHE_PATH = DATA_DIR / "embedding_cache.json"
SESSION_STORE_DIR = STATE_DIR

GUARDRAILS_CONFIG_PATH = Path(__file__).parent / "guardrails_config.json"
NORMALIZATION_CONFIG_PATH = Path(__file__).parent / "normalization_config.json"
METADATA_CATALOG_PATH = BASE_DIR / "metadata_catalog.json"

# Warning notes (optional)
WARNING_FILE_PATH = BASE_DIR / "warning.txt"


# ── environment variables ────────────────────────────────────────────────────
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name) or os.getenv(name.upper()) or default


AZURE_OPENAI_KEY = _env("AZURE_OPENAI_KEY")
AZURE_OPENAI_ENDPOINT = _env("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_EMBED_DEPLOYMENT = _env("AZURE_OPENAI_EMBED_DEPLOYMENT")
AZURE_OPENAI_CHAT_DEPLOYMENT = _env("AZURE_OPENAI_CHAT_DEPLOYMENT")
AZURE_OPENAI_API_VERSION = _env("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

MAX_ROWS = 100


@dataclass
class AzureSettings:
    azure_openai_key: str
    azure_openai_endpoint: str
    embedding_deployment: str
    chat_deployment: str
    api_version: str

    @property
    def can_use_azure(self) -> bool:
        return all([
            self.azure_openai_key,
            self.azure_openai_endpoint,
            self.embedding_deployment,
            self.chat_deployment,
            self.api_version,
        ]) and self.azure_openai_key.lower() != "x"


def load_settings() -> AzureSettings:
    return AzureSettings(
        azure_openai_key=AZURE_OPENAI_KEY,
        azure_openai_endpoint=AZURE_OPENAI_ENDPOINT,
        embedding_deployment=AZURE_OPENAI_EMBED_DEPLOYMENT,
        chat_deployment=AZURE_OPENAI_CHAT_DEPLOYMENT,
        api_version=AZURE_OPENAI_API_VERSION,
    )
