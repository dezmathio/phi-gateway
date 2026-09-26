#!/usr/bin/env python3
"""Train a tiny classifier on scrubbed notes. Labels come from Synthea, not from the identifiers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import make_pipeline

from app.notes import read_jsonl
from app.scrub import get_scrubber

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    notes = read_jsonl(ROOT / "tests" / "fixtures" / "notes.jsonl")
    scrubber = get_scrubber()
    texts = []
    labels = []
    for note in notes:
        scrubbed, _findings = scrubber.scrub(note["text"], mask_medications=True)
        texts.append(scrubbed)
        labels.append(note["encounter_class"])
    model = make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=1),
        LogisticRegression(max_iter=400),
    )
    folds = 2 if min(labels.count(label) for label in set(labels)) >= 2 else 0
    if folds:
        scores = cross_val_score(model, texts, labels, cv=folds)
        print(f"encounter_class, n={len(texts)}, stratified {folds}-fold accuracy {scores.mean():.2f} (folds {', '.join(f'{s:.2f}' for s in scores)})")
    model.fit(texts, labels)
    train = model.score(texts, labels)
    print(f"training-set accuracy {train:.2f} on {len(texts)} scrubbed notes")
    print("This is a toy model for the talking point. It is not a clinical result.")


if __name__ == "__main__":
    main()
