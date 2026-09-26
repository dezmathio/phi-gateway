"""Typed span scoring against the generator's ground truth.

A detection matches a planted span when the entity types are equal and the
overlap coefficient is at least 0.5 (intersection divided by the shorter span).
That credits a slightly wider NER span and rejects a one-token nick of a long value.
"Value still present" is separate: the planted string is still in the scrubbed text.
"""

from __future__ import annotations

import re
from collections import defaultdict


def overlaps(left: dict, right: dict) -> bool:
    inter = max(0, min(left["end"], right["end"]) - max(left["start"], right["start"]))
    if inter <= 0:
        return False
    shorter = min(left["end"] - left["start"], right["end"] - right["start"])
    if shorter <= 0:
        return False
    return inter / shorter >= 0.5


def value_present(text: str, value: str) -> bool:
    if not value:
        return False
    pattern = r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text) is not None


def score_corpus(notes: list[dict], truths: list[dict], predicted: dict[str, list[dict]]) -> dict:
    """predicted maps note_id -> findings with entity_type/start/end. Truth uses type/start/end/value."""
    truth_by_id = {row["note_id"]: row for row in truths}
    per_type: dict[str, dict] = defaultdict(
        lambda: {"support": 0, "matched_truth": 0, "matched_pred": 0, "predicted": 0, "leaks": 0, "misses": []}
    )
    for note in notes:
        truth = truth_by_id[note["note_id"]]
        preds = predicted.get(note["note_id"], [])
        scrubbed = note.get("scrubbed_text", "")
        for entity in truth["entities"]:
            bucket = per_type[entity["type"]]
            bucket["support"] += 1
            hit = any(
                pred["entity_type"] == entity["type"] and overlaps(entity, pred) for pred in preds
            )
            if hit:
                bucket["matched_truth"] += 1
            elif len(bucket["misses"]) < 30:
                bucket["misses"].append(
                    {"note_id": note["note_id"], "value": entity["value"], "start": entity["start"]}
                )
            if value_present(scrubbed, entity["value"]):
                bucket["leaks"] += 1
        for pred in preds:
            bucket = per_type[pred["entity_type"]]
            bucket["predicted"] += 1
            if any(entity["type"] == pred["entity_type"] and overlaps(entity, pred) for entity in truth["entities"]):
                bucket["matched_pred"] += 1
    rows = []
    for entity_type, bucket in sorted(per_type.items()):
        support = bucket["support"]
        predicted_n = bucket["predicted"]
        rows.append(
            {
                "type": entity_type,
                "support": support,
                "recall": (bucket["matched_truth"] / support) if support else None,
                "precision": (bucket["matched_pred"] / predicted_n) if predicted_n else None,
                "predicted": predicted_n,
                "leaks": bucket["leaks"],
                "misses": bucket["misses"],
            }
        )
    return {"per_type": rows}


def markdown_table(report: dict) -> str:
    lines = [
        "| Entity | Support | Recall | Precision | Value still in scrubbed text |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in report["per_type"]:
        if row["support"] == 0 and row["predicted"] == 0:
            continue
        lines.append(
            "| {type} | {support} | {recall} | {precision} | {leaks} |".format(
                type=row["type"],
                support=row["support"],
                recall=_fmt(row["recall"]),
                precision=_fmt(row["precision"]),
                leaks=row["leaks"],
            )
        )
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"
