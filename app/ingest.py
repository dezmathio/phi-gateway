"""Scrub a note and persist only the scrubbed text plus finding metadata."""

from __future__ import annotations

import uuid
from pathlib import Path

from app.scrub import Scrubber, get_scrubber
from app.settings import TENANTS
from app.store import TenantStore, store_for


def ingest_note(
    tenant_id: str,
    text: str,
    *,
    note_id: str | None = None,
    encounter_id: str | None = None,
    mask_medications: bool | None = None,
    embedding: list[float] | None = None,
    scrubber: Scrubber | None = None,
    data_dir: Path | None = None,
) -> dict:
    if tenant_id not in TENANTS:
        raise KeyError(tenant_id)
    if mask_medications is None:
        mask_medications = bool(TENANTS[tenant_id]["mask_medications"])
    engine = scrubber or get_scrubber()
    scrubbed, findings = engine.scrub(text, mask_medications=mask_medications)
    store: TenantStore = store_for(tenant_id, data_dir)
    stored_id = note_id or f"note-{uuid.uuid4().hex[:12]}"
    store.upsert(
        stored_id,
        scrubbed,
        findings,
        encounter_id=encounter_id,
        embedding=embedding,
    )
    return {
        "note_id": stored_id,
        "tenant_id": tenant_id,
        "scrubbed_text": scrubbed,
        "findings": [finding.public() for finding in findings],
        "mask_medications": mask_medications,
    }
