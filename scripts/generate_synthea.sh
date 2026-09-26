#!/usr/bin/env bash
# Generate a Synthea population. Output CSVs stay under data/ (gitignored).
# The committed test sample is produced by scripts/make_notes.py --init-sample, not by this jar.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/data/synthea"
mkdir -p "$DEST"
JAR="$DEST/synthea-with-dependencies.jar"
if [[ ! -f "$JAR" ]]; then
  curl -L --fail -o "$JAR" \
    https://github.com/synthetichealth/synthea/releases/latest/download/synthea-with-dependencies.jar
fi
java -jar "$JAR" -p 200 -s 42 --exporter.baseDirectory="$DEST/output"
echo "CSV directory: $DEST/output/csv"
echo "Next: python scripts/make_notes.py --csv $DEST/output/csv --notes data/notes.jsonl --truth data/notes_truth.jsonl --lexicon app/lexicon/medications.txt"
