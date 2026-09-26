"""Paths, tenant defaults, and the two Presidio policies (ingest vs gateway)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
FIXTURES_DIR = Path(os.environ.get("FIXTURES_DIR", ROOT / "tests" / "fixtures"))
CAPTURE_DIR = Path(os.environ.get("PHI_CAPTURE_DIR", DATA_DIR / "gateway-captures"))
LEXICON_PATH = Path(os.environ.get("MEDICATION_LEXICON", ROOT / "app" / "lexicon" / "medications.txt"))
KEYS_PATH = DATA_DIR / "keys.json"

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:4000").rstrip("/")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "sk-phi-demo-master")
CHAT_MODEL = os.environ.get("CHAT_MODEL_NAME", "demo-chat")
EMBED_MODEL = os.environ.get("EMBED_MODEL_NAME", "demo-embed")

# Trailing slash: LiteLLM joins this string directly to "analyze" / "anonymize".
PRESIDIO_ANALYZER_API_BASE = os.environ.get("PRESIDIO_ANALYZER_API_BASE", "http://127.0.0.1:5002").rstrip("/") + "/"
PRESIDIO_ANONYMIZER_API_BASE = os.environ.get("PRESIDIO_ANONYMIZER_API_BASE", "http://127.0.0.1:5002").rstrip("/") + "/"

# Ingest entity list. Aligned with the gateway list so nothing the gateway would
# mask can persist in storage. MRN, MEDICATION, and US_STREET_ADDRESS are custom.
INGEST_ENTITIES = [
    "PERSON",
    "DATE_TIME",
    "US_SSN",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "LOCATION",
    "MRN",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "US_PASSPORT",
    "US_DRIVER_LICENSE",
    "US_BANK_NUMBER",
    "IBAN_CODE",
    "URL",
    "MEDICAL_LICENSE",
    "US_STREET_ADDRESS",
]

# Gateway entity list. Same as ingest, so both layers catch the same identifiers.
# MEDICATION stays an ingest-only policy: a clinic can keep drug names in the prompt.
# The gateway demonstrates defense-in-depth by catching identifiers in user questions
# or text that bypasses the ingest API entirely.
GATEWAY_ENTITIES = [
    "PERSON",
    "DATE_TIME",
    "US_SSN",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "LOCATION",
    "MRN",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "US_PASSPORT",
    "US_DRIVER_LICENSE",
    "US_BANK_NUMBER",
    "IBAN_CODE",
    "URL",
    "MEDICAL_LICENSE",
    "US_STREET_ADDRESS",
]

TENANTS: dict[str, dict] = {
    "clinic-a": {
        "label": "Clinic A",
        "alias": "clinic-a",
        "mask_medications": True,
        "max_budget": 5.0,
    },
    "clinic-b": {
        "label": "Clinic B",
        "alias": "clinic-b",
        "mask_medications": False,
        "max_budget": 5.0,
    },
}

DEFAULT_QUESTION = (
    "What problem was this visit for, and what treatment was continued? "
    "Answer in two sentences. Keep any angle-bracket placeholders as written."
)

SCORE_THRESHOLD = 0.4
MAX_NOTE_CHARS = 8000
