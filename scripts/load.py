#!/usr/bin/env python3
"""Load generated notes into each tenant's SQLite file through the ingest scrubber."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingest import ingest_note
from app.notes import read_jsonl
from app.settings import DATA_DIR, TENANTS

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", type=Path, default=ROOT / "tests" / "fixtures" / "notes.jsonl")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    counts = {tenant_id: 0 for tenant_id in TENANTS}
    for note in read_jsonl(args.notes):
        ingest_note(
            note["tenant_id"],
            note["text"],
            note_id=note["note_id"],
            encounter_id=note.get("encounter_id"),
            mask_medications=TENANTS[note["tenant_id"]]["mask_medications"],
            data_dir=args.data_dir,
        )
        counts[note["tenant_id"]] += 1
    for tenant_id, count in counts.items():
        print(f"{tenant_id}: stored {count} scrubbed notes in {args.data_dir / 'tenants' / (tenant_id + '.sqlite')}")


if __name__ == "__main__":
    main()
