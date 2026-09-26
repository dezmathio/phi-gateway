#!/usr/bin/env python3
"""Join Synthea CSVs into notes plus a ground-truth span file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.notes import write_outputs, write_sample_csvs

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init-sample", action="store_true", help="Write the committed 20-patient CSV fixture, then the notes.")
    parser.add_argument("--csv", type=Path, default=ROOT / "tests" / "fixtures" / "csv")
    parser.add_argument("--notes", type=Path, default=ROOT / "tests" / "fixtures" / "notes.jsonl")
    parser.add_argument("--truth", type=Path, default=ROOT / "tests" / "fixtures" / "notes_truth.jsonl")
    parser.add_argument("--lexicon", type=Path, default=ROOT / "app" / "lexicon" / "medications.txt")
    parser.add_argument("--sample", action="store_true", help="Use the fixed sample templates (hard cases included).")
    args = parser.parse_args()
    if args.init_sample:
        write_sample_csvs(args.csv)
    sample = True if args.sample or args.init_sample else None
    notes, _truths = write_outputs(args.csv, args.notes, args.truth, args.lexicon, sample=sample)
    print(f"wrote {len(notes)} notes to {args.notes}")


if __name__ == "__main__":
    main()
