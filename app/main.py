"""HTTP API and the single demo page."""

from __future__ import annotations

import random
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.auth import AuthError, authorize
from app.gateway import Gateway, redact_key
from app.pipeline import answer_question, corpus, gap_note, run_note, seed_fixtures
from app.settings import MAX_NOTE_CHARS, TENANTS

STATIC = Path(__file__).resolve().parent / "static" / "index.html"


class NoteIn(BaseModel):
    text: str
    note_id: str | None = None
    encounter_id: str | None = None


class IngestBody(BaseModel):
    text: str | None = None
    note_id: str | None = None
    encounter_id: str | None = None
    notes: list[NoteIn] | None = None
    mask_medications: bool | None = None


class RunBody(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_NOTE_CHARS)
    question: str | None = Field(default=None, max_length=800)
    mask_medications: bool | None = None


class QueryBody(BaseModel):
    question: str = Field(min_length=1, max_length=800)


def _bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


def _auth(authorization: str | None, tenant_id: str, gateway: Gateway) -> dict:
    try:
        return authorize(_bearer(authorization), tenant_id, gateway.keys)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    gateway = Gateway()
    app.state.gateway = gateway
    from app.scrub import get_scrubber

    get_scrubber().load()
    try:
        await gateway.ensure_keys()
    except Exception as exc:  # proxy can be down in local scrub-only use
        gateway.last_error = str(exc)
    try:
        await seed_fixtures(gateway)
    except Exception as exc:
        gateway.last_error = gateway.last_error or str(exc)
    yield
    await gateway.aclose()


app = FastAPI(title="PHI Gateway", lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(STATIC, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
async def health():
    gateway: Gateway = app.state.gateway
    return {
        "app": "ok",
        "litellm": "ok" if await gateway.healthy() else "down",
        "presidio": "ok" if await gateway.presidio_healthy() else "down",
        "gateway_error": gateway.last_error,
    }


@app.get("/api/bootstrap")
async def bootstrap():
    gateway: Gateway = app.state.gateway
    notes = corpus()
    samples = {}
    for tenant_id in TENANTS:
        pool = [note for note in notes if note["tenant_id"] == tenant_id and note.get("demo_clear", True)]
        if not pool:
            pool = [note for note in notes if note["tenant_id"] == tenant_id]
        if pool:
            picked = random.choice(pool)
            samples[tenant_id] = {
                "note_id": picked["note_id"],
                "tenant_id": picked["tenant_id"],
                "text": picked["text"],
            }
    tenants = []
    for tenant_id, meta in TENANTS.items():
        info = gateway.keys.get(tenant_id) or {}
        key = info.get("key")
        spend = None
        max_budget = meta["max_budget"]
        if key:
            try:
                remote = await gateway.key_info(key)
                spend = remote.get("spend")
                if remote.get("max_budget") is not None:
                    max_budget = remote.get("max_budget")
            except Exception:
                pass
        tenants.append(
            {
                "id": tenant_id,
                "label": meta["label"],
                "alias": meta["alias"],
                "mask_medications": meta["mask_medications"],
                "key": key,
                "key_redacted": redact_key(key) if key else None,
                "spend": spend,
                "max_budget": max_budget,
            }
        )
    return {
        "tenants": tenants,
        "samples": samples,
        "gateway_gap": gap_note(),
        "health": {
            "litellm": "ok" if gateway.keys and not gateway.last_error else "down",
            "detail": gateway.last_error,
        },
    }


@app.post("/api/tenants/{tenant_id}/ingest")
async def ingest(tenant_id: str, body: IngestBody, authorization: str | None = Header(default=None)):
    gateway: Gateway = app.state.gateway
    _auth(authorization, tenant_id, gateway)
    from app.ingest import ingest_note

    items = body.notes or []
    if body.text:
        items = [NoteIn(text=body.text, note_id=body.note_id, encounter_id=body.encounter_id), *items]
    if not items:
        raise HTTPException(status_code=400, detail="Send text or notes.")
    stored = []
    echoed = []
    for item in items:
        if len(item.text) > MAX_NOTE_CHARS:
            raise HTTPException(status_code=400, detail="Note is too long.")
        result = ingest_note(
            tenant_id,
            item.text,
            note_id=item.note_id,
            encounter_id=item.encounter_id,
            mask_medications=body.mask_medications,
        )
        echoed.append({"text": item.text, "findings": result["findings"], "scrubbed_text": result["scrubbed_text"]})
        stored.append({"note_id": result["note_id"], "findings": result["findings"]})
    return {"stored": stored, "echo": echoed}


@app.post("/api/tenants/{tenant_id}/run")
async def run(tenant_id: str, body: RunBody, authorization: str | None = Header(default=None)):
    gateway: Gateway = app.state.gateway
    info = _auth(authorization, tenant_id, gateway)
    result = await run_note(
        gateway,
        tenant_id=tenant_id,
        api_key=info["key"],
        text=body.text,
        question=body.question,
        mask_medications=body.mask_medications,
    )
    result["key_alias"] = info.get("alias")
    result["key_redacted"] = redact_key(info["key"])
    return result


@app.post("/api/tenants/{tenant_id}/query")
async def query(tenant_id: str, body: QueryBody, authorization: str | None = Header(default=None)):
    gateway: Gateway = app.state.gateway
    info = _auth(authorization, tenant_id, gateway)
    result = await answer_question(
        gateway,
        tenant_id=tenant_id,
        api_key=info["key"],
        question=body.question,
    )
    result["key_alias"] = info.get("alias")
    result["key_redacted"] = redact_key(info["key"])
    return result
