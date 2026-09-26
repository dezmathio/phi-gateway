"""Ingest scrubber: Presidio, plus recognizers for Synthea MRNs, fictional 555 numbers, and a medication list.

The entity allowlist is the clinical identifier set. CREDIT_CARD is not on it.
Payment-card numbers are left for the LiteLLM Presidio guardrail, which runs a
wider entity list. Medication masking is a per-request (per-tenant) policy.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

# Presidio's email recognizer uses tldextract, which otherwise tries to write under ~/.cache.
os.environ.setdefault(
    "TLDEXTRACT_CACHE",
    str(Path(__file__).resolve().parents[1] / "data" / "tldextract"),
)

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from app.settings import INGEST_ENTITIES, LEXICON_PATH, SCORE_THRESHOLD


@dataclass(frozen=True)
class Finding:
    entity_type: str
    start: int
    end: int
    score: float
    recognizer: str

    def public(self) -> dict:
        """Metadata safe to persist. The matched text is not included."""
        return {
            "entity_type": self.entity_type,
            "start": self.start,
            "end": self.end,
            "score": round(float(self.score), 4),
            "recognizer": self.recognizer,
        }


def load_medication_names(path=LEXICON_PATH) -> list[str]:
    names: list[str] = []
    if not path.exists():
        return names
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(line)
    return names


def _mrn_recognizer() -> PatternRecognizer:
    return PatternRecognizer(
        supported_entity="MRN",
        name="SyntheaMrnRecognizer",
        supported_language="en",
        patterns=[
            Pattern(
                name="synthea_uuid",
                regex=r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
                score=0.85,
            )
        ],
    )


def _phone_555_recognizer() -> PatternRecognizer:
    # Presidio's phone recognizer uses the phonenumbers library and often skips
    # the fictional 555 prefix, especially when the separators vary.
    return PatternRecognizer(
        supported_entity="PHONE_NUMBER",
        name="Fictional555Recognizer",
        supported_language="en",
        patterns=[
            Pattern(name="555-separated", regex=r"\b555[-.\s]\d{3}[-.\s]\d{4}\b", score=0.91),
            Pattern(name="555-paren", regex=r"\(555\)\s*\d{3}[-.\s]\d{4}", score=0.91),
        ],
    )


def _medication_recognizer(names: list[str]) -> PatternRecognizer | None:
    cleaned = sorted({name.strip() for name in names if len(name.strip()) >= 3}, key=len, reverse=True)
    if not cleaned:
        return None
    pattern = r"\b(?:%s)\b" % "|".join(re.escape(name) for name in cleaned)
    return PatternRecognizer(
        supported_entity="MEDICATION",
        name="SyntheaMedicationRecognizer",
        supported_language="en",
        patterns=[Pattern(name="synthea_med", regex=pattern, score=0.85)],
        global_regex_flags=re.IGNORECASE,
    )


def _recognizer_name(result) -> str:
    metadata = getattr(result, "recognition_metadata", None) or {}
    if metadata.get("recognizer_name"):
        return str(metadata["recognizer_name"])
    explanation = getattr(result, "analysis_explanation", None)
    if explanation is not None and getattr(explanation, "recognizer", None):
        return str(explanation.recognizer)
    return "unknown"


def _resolve(results: list) -> list:
    """Keep the higher-scoring span when two detections overlap, so highlights match replacements."""
    ordered = sorted(results, key=lambda item: (item.score, item.end - item.start), reverse=True)
    kept: list = []
    for result in ordered:
        if any(result.start < other.end and other.start < result.end for other in kept):
            continue
        kept.append(result)
    kept.sort(key=lambda item: item.start)
    return kept


class Scrubber:
    def __init__(self, lexicon_path=LEXICON_PATH):
        self.lexicon_path = lexicon_path
        self._lock = threading.Lock()
        self._analyzer: AnalyzerEngine | None = None
        self._anonymizer = AnonymizerEngine()

    def load(self) -> None:
        if self._analyzer is not None:
            return
        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_md"}],
            }
        )
        nlp_engine = provider.create_engine()
        registry = RecognizerRegistry()
        registry.load_predefined_recognizers(nlp_engine=nlp_engine, languages=["en"])
        registry.add_recognizer(_mrn_recognizer())
        registry.add_recognizer(_phone_555_recognizer())
        medication = _medication_recognizer(load_medication_names(self.lexicon_path))
        if medication is not None:
            registry.add_recognizer(medication)
        self._analyzer = AnalyzerEngine(registry=registry, nlp_engine=nlp_engine, supported_languages=["en"])

    def scrub(self, text: str, *, mask_medications: bool) -> tuple[str, list[Finding]]:
        self.load()
        assert self._analyzer is not None
        entities = list(INGEST_ENTITIES)
        if mask_medications:
            entities.append("MEDICATION")
        with self._lock:
            results = self._analyzer.analyze(
                text=text,
                language="en",
                entities=entities,
                score_threshold=SCORE_THRESHOLD,
            )
        kept = _resolve(list(results))
        findings = [
            Finding(
                entity_type=result.entity_type,
                start=result.start,
                end=result.end,
                score=result.score,
                recognizer=_recognizer_name(result),
            )
            for result in kept
        ]
        if not kept:
            return text, findings
        operators = {
            result.entity_type: OperatorConfig("replace", {"new_value": f"<{result.entity_type}>"})
            for result in kept
        }
        anonymized = self._anonymizer.anonymize(text=text, analyzer_results=kept, operators=operators)
        return anonymized.text, findings


_default: Scrubber | None = None
_default_lock = threading.Lock()


def get_scrubber() -> Scrubber:
    global _default
    with _default_lock:
        if _default is None:
            _default = Scrubber()
        return _default
