"""One SQLite file per tenant. Rows hold scrubbed text and finding metadata, never the original span text."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.scrub import Finding
from app.settings import DATA_DIR, TENANTS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


class TenantStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            create table if not exists notes (
                id text primary key,
                encounter_id text,
                scrubbed_text text not null,
                findings_json text not null,
                embedding_json text,
                created_at text not null
            );
            """
        )
        self._conn.commit()

    def upsert(
        self,
        note_id: str,
        scrubbed_text: str,
        findings: list[Finding] | list[dict],
        *,
        encounter_id: str | None = None,
        embedding: list[float] | None = None,
    ) -> None:
        payload = []
        for finding in findings:
            if isinstance(finding, Finding):
                payload.append(finding.public())
            else:
                payload.append(
                    {
                        "entity_type": finding["entity_type"],
                        "start": finding["start"],
                        "end": finding["end"],
                        "score": finding["score"],
                        "recognizer": finding["recognizer"],
                    }
                )
        with self._lock:
            self._conn.execute(
                """
                insert into notes (id, encounter_id, scrubbed_text, findings_json, embedding_json, created_at)
                values (?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    encounter_id = excluded.encounter_id,
                    scrubbed_text = excluded.scrubbed_text,
                    findings_json = excluded.findings_json,
                    embedding_json = coalesce(excluded.embedding_json, notes.embedding_json),
                    created_at = excluded.created_at
                """,
                (
                    note_id,
                    encounter_id,
                    scrubbed_text,
                    json.dumps(payload),
                    json.dumps(embedding) if embedding is not None else None,
                    _now(),
                ),
            )
            self._conn.commit()

    def set_embedding(self, note_id: str, embedding: list[float]) -> None:
        with self._lock:
            self._conn.execute(
                "update notes set embedding_json = ? where id = ?",
                (json.dumps(embedding), note_id),
            )
            self._conn.commit()

    def count(self) -> int:
        row = self._conn.execute("select count(*) as n from notes").fetchone()
        return int(row["n"])

    def search_embedding(self, vector: list[float], k: int = 3) -> list[dict]:
        rows = self._conn.execute(
            "select id, encounter_id, scrubbed_text, embedding_json from notes where embedding_json is not null"
        ).fetchall()
        scored: list[tuple[float, dict]] = []
        for row in rows:
            other = json.loads(row["embedding_json"])
            scored.append((cosine(vector, other), row))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits = []
        for score, row in scored[:k]:
            if score <= 0:
                continue
            hits.append(
                {
                    "id": row["id"],
                    "encounter_id": row["encounter_id"],
                    "scrubbed_text": row["scrubbed_text"],
                    "score": round(score, 3),
                }
            )
        return hits

    def search_keyword(self, query: str, k: int = 3) -> list[dict]:
        tokens = [token for token in re.findall(r"[a-z0-9-]{4,}", query.lower())]
        rows = self._conn.execute("select id, encounter_id, scrubbed_text from notes").fetchall()
        scored: list[tuple[int, dict]] = []
        for row in rows:
            haystack = row["scrubbed_text"].lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "id": row["id"],
                "encounter_id": row["encounter_id"],
                "scrubbed_text": row["scrubbed_text"],
                "score": score,
            }
            for score, row in scored[:k]
        ]


_stores: dict[str, TenantStore] = {}
_stores_lock = threading.Lock()


def store_for(tenant_id: str, data_dir: Path | None = None) -> TenantStore:
    if tenant_id not in TENANTS:
        raise KeyError(tenant_id)
    root = data_dir or DATA_DIR
    with _stores_lock:
        existing = _stores.get(f"{root}:{tenant_id}")
        if existing is not None:
            return existing
        path = root / "tenants" / f"{tenant_id}.sqlite"
        created = TenantStore(path)
        _stores[f"{root}:{tenant_id}"] = created
        return created


def reset_stores() -> None:
    """Test helper. Does not delete files."""
    with _stores_lock:
        _stores.clear()
