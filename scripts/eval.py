#!/usr/bin/env python3
"""Score the ingest scrubber against notes_truth.jsonl and print recall, precision, and misses."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.metrics import markdown_table, score_corpus
from app.notes import read_jsonl
from app.scrub import get_scrubber

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", type=Path, default=ROOT / "tests" / "fixtures" / "notes.jsonl")
    parser.add_argument("--truth", type=Path, default=ROOT / "tests" / "fixtures" / "notes_truth.jsonl")
    args = parser.parse_args()
    notes = read_jsonl(args.notes)
    truths = read_jsonl(args.truth)
    scrubber = get_scrubber()
    predicted: dict[str, list[dict]] = {}
    scored_notes = []
    for note in notes:
        scrubbed, findings = scrubber.scrub(note["text"], mask_medications=True)
        predicted[note["note_id"]] = [finding.public() for finding in findings]
        scored_notes.append({**note, "scrubbed_text": scrubbed})
    report = score_corpus(scored_notes, truths, predicted)
    print("Ingest scrubber, medication masking on. Overlap coefficient >= 0.5, same entity type.")
    print()
    print(markdown_table(report))
    print()
    for row in report["per_type"]:
        if not row["misses"]:
            continue
        print(f"Misses for {row['type']}:")
        for miss in row["misses"]:
            print(f"  {miss['note_id']}: {miss['value']}")
        print()


if __name__ == "__main__":
    main()
