#!/usr/bin/env python3
"""Presidio analyzer + anonymizer HTTP API, for local runs without the Presidio containers.

LiteLLM's guardrail calls POST /analyze and POST /anonymize. The same process serves both.
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from pydantic import BaseModel

app = FastAPI()
anonymizer = AnonymizerEngine()
provider = NlpEngineProvider(
    nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_md"}],
    }
)
NLP = provider.create_engine()


class AnalyzeBody(BaseModel):
    text: str
    language: str = "en"
    entities: list[str] | None = None
    score_threshold: float | None = None
    ad_hoc_recognizers: list[dict] | None = None


class AnonymizeBody(BaseModel):
    text: str
    analyzer_results: list[dict]


def _recognizer(spec: dict) -> PatternRecognizer:
    patterns = [
        Pattern(name=item.get("name") or "pattern", regex=item["regex"], score=float(item.get("score") or 0.5))
        for item in spec.get("patterns") or []
    ]
    return PatternRecognizer(
        supported_entity=spec["supported_entity"],
        name=spec.get("name"),
        supported_language=spec.get("supported_language") or "en",
        patterns=patterns,
        context=spec.get("context"),
    )


def _engine(ad_hoc: list[dict] | None) -> AnalyzerEngine:
    registry = RecognizerRegistry()
    registry.load_predefined_recognizers(nlp_engine=NLP, languages=["en"])
    for spec in ad_hoc or []:
        registry.add_recognizer(_recognizer(spec))
    return AnalyzerEngine(registry=registry, nlp_engine=NLP, supported_languages=["en"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyze")
def analyze(body: AnalyzeBody):
    results = _engine(body.ad_hoc_recognizers).analyze(
        text=body.text,
        language=body.language,
        entities=body.entities,
        score_threshold=body.score_threshold or 0,
    )
    payload = []
    for result in results:
        explanation = getattr(result, "analysis_explanation", None)
        payload.append(
            {
                "entity_type": result.entity_type,
                "start": result.start,
                "end": result.end,
                "score": result.score,
                "analysis_explanation": {
                    "recognizer": getattr(explanation, "recognizer", None),
                },
            }
        )
    return payload


@app.post("/anonymize")
def anonymize(body: AnonymizeBody):
    results = [
        RecognizerResult(
            entity_type=item["entity_type"],
            start=int(item["start"]),
            end=int(item["end"]),
            score=float(item.get("score") or 0.5),
        )
        for item in body.analyzer_results
    ]
    out = anonymizer.anonymize(text=body.text, analyzer_results=results)
    return {
        "text": out.text,
        "items": [
            {
                "start": item.start,
                "end": item.end,
                "entity_type": item.entity_type,
                "text": item.text,
                "operator": item.operator,
            }
            for item in out.items
        ],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5002)
