"""One demo pass: ingest scrub, store, retrieve, then a model call through LiteLLM."""

from __future__ import annotations

import json
import re
import uuid

import httpx

from app.gateway import Gateway, GatewayError
from app.ingest import ingest_note
from app.notes import read_jsonl
from app.settings import DEFAULT_QUESTION, FIXTURES_DIR, TENANTS
from app.store import store_for

SYSTEM_PROMPT = (
    "You are a chart assistant. You only receive de-identified notes from one clinic. "
    "Answer using those notes. Do not invent names, dates of birth, addresses, phone numbers, "
    "or SSNs. If the note uses a placeholder such as <PERSON>, keep the placeholder."
)

_TAG = re.compile(r"<[A-Z][A-Z0-9_]*>")


def _tags(text: str) -> set[str]:
    return set(_TAG.findall(text))


def build_messages(question: str, scrubbed_notes: list[str]) -> list[dict]:
    body = "Question: {question}\n\nDe-identified notes from this tenant:\n\n{notes}".format(
        question=question.strip(),
        notes="\n---\n".join(scrubbed_notes[:3]),
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": body},
    ]


async def _retrieve(gateway: Gateway, api_key: str, tenant_id: str, scrubbed: str, question: str) -> dict:
    store = store_for(tenant_id)
    mode = "keyword"
    error = None
    hits: list[dict] = []
    try:
        vector = await gateway.embed(api_key, question + "\n" + scrubbed)
        hits = store.search_embedding(vector, k=3)
        mode = "embedding"
        # The vector above is the question+note vector used for search.
        # Also store an embedding of the note alone so later questions can find it.
        note_vector = await gateway.embed(api_key, scrubbed)
        return {
            "mode": mode,
            "error": None,
            "hits": hits,
            "note_vector": note_vector,
        }
    except (GatewayError, httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
        error = str(exc)
        hits = store.search_keyword(scrubbed, k=3)
        return {"mode": "keyword", "error": error, "hits": hits, "note_vector": None}


async def run_note(
    gateway: Gateway,
    *,
    tenant_id: str,
    api_key: str,
    text: str,
    question: str | None = None,
    mask_medications: bool | None = None,
) -> dict:
    question = (question or DEFAULT_QUESTION).strip()
    ingested = ingest_note(tenant_id, text, mask_medications=mask_medications)
    retrieval = await _retrieve(gateway, api_key, tenant_id, ingested["scrubbed_text"], question)
    if retrieval["note_vector"] is not None:
        store_for(tenant_id).set_embedding(ingested["note_id"], retrieval["note_vector"])

    context = [ingested["scrubbed_text"]]
    for hit in retrieval["hits"]:
        if hit["scrubbed_text"] not in context:
            context.append(hit["scrubbed_text"])
    messages = build_messages(question, context)
    request_id = uuid.uuid4().hex
    reply = None
    error = None
    usage = {}
    cost = None
    model = None
    payload = messages
    payload_source = "ingest_scrub_only"
    gateway_findings: list[dict] = []
    try:
        response = await gateway.chat(api_key, messages, request_id)
        if response.status_code >= 400:
            error = response.text[:500]
        else:
            body = response.json()
            reply = body["choices"][0]["message"]["content"]
            usage = body.get("usage") or {}
            model = body.get("model")
            cost = response.headers.get("x-litellm-response-cost")
    except (GatewayError, httpx.HTTPError) as exc:
        error = str(exc)

    capture = await gateway.read_capture(request_id)
    if capture and capture.get("messages"):
        payload = capture["messages"]
        payload_source = "litellm_pre_api_capture"
        model = model or capture.get("model")
    else:
        try:
            masked_user, gateway_findings = await gateway.mask_text(messages[1]["content"])
            payload = [
                messages[0],
                {"role": "user", "content": masked_user},
            ]
            payload_source = "presidio_gateway_replay"
        except (GatewayError, httpx.HTTPError) as exc:
            detail = getattr(exc, "detail", str(exc))
            error = error or f"Gateway scrub unavailable: {detail}"

    sent_user = payload[-1]["content"] if payload else ""
    added = sorted(_tags(sent_user) - _tags(messages[1]["content"]))
    spend = None
    max_budget = TENANTS[tenant_id]["max_budget"]
    try:
        info = await gateway.key_info(api_key)
        spend = info.get("spend")
        if info.get("max_budget") is not None:
            max_budget = info.get("max_budget")
    except (GatewayError, httpx.HTTPError):
        pass

    return {
        "tenant_id": tenant_id,
        "raw_text": text,
        "findings": ingested["findings"],
        "scrubbed_text": ingested["scrubbed_text"],
        "mask_medications": ingested["mask_medications"],
        "stored_note_id": ingested["note_id"],
        "retrieval": {
            "mode": retrieval["mode"],
            "error": retrieval["error"],
            "hit_ids": [hit["id"] for hit in retrieval["hits"]],
        },
        "model_payload": {"messages": payload, "source": payload_source},
        "gateway_added_placeholders": added,
        "gateway_findings": gateway_findings,
        "reply": reply,
        "error": error,
        "log": {
            "model": model,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cost": _number(cost),
            "spend": spend,
            "max_budget": max_budget,
            "request_id": request_id,
        },
    }


def _number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def answer_question(gateway: Gateway, *, tenant_id: str, api_key: str, question: str) -> dict:
    store = store_for(tenant_id)
    mode = "keyword"
    error = None
    hits: list[dict] = []
    try:
        vector = await gateway.embed(api_key, question)
        hits = store.search_embedding(vector, k=3)
        mode = "embedding"
    except (GatewayError, httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
        error = str(exc)
        hits = store.search_keyword(question, k=3)
    notes = [hit["scrubbed_text"] for hit in hits] or ["No notes are stored for this tenant."]
    messages = build_messages(question, notes)
    request_id = uuid.uuid4().hex
    reply = None
    chat_error = None
    usage = {}
    cost = None
    model = None
    try:
        response = await gateway.chat(api_key, messages, request_id)
        if response.status_code >= 400:
            chat_error = response.text[:500]
        else:
            body = response.json()
            reply = body["choices"][0]["message"]["content"]
            usage = body.get("usage") or {}
            model = body.get("model")
            cost = response.headers.get("x-litellm-response-cost")
    except (GatewayError, httpx.HTTPError) as exc:
        chat_error = str(exc)
    return {
        "tenant_id": tenant_id,
        "retrieval": {"mode": mode, "error": error, "hit_ids": [hit["id"] for hit in hits]},
        "reply": reply,
        "error": chat_error,
        "log": {
            "model": model,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cost": _number(cost),
            "request_id": request_id,
        },
    }


async def seed_fixtures(gateway: Gateway) -> None:
    path = FIXTURES_DIR / "notes.jsonl"
    if not path.exists():
        return
    notes = read_jsonl(path)
    for tenant_id in TENANTS:
        if store_for(tenant_id).count() > 0:
            continue
        api_key = (gateway.keys.get(tenant_id) or {}).get("key")
        for note in notes:
            if note["tenant_id"] != tenant_id:
                continue
            ingested = ingest_note(
                tenant_id,
                note["text"],
                note_id=note["note_id"],
                encounter_id=note.get("encounter_id"),
                mask_medications=TENANTS[tenant_id]["mask_medications"],
            )
            if not api_key:
                continue
            try:
                vector = await gateway.embed(api_key, ingested["scrubbed_text"])
                store_for(tenant_id).set_embedding(note["note_id"], vector)
            except (GatewayError, httpx.HTTPError):
                continue


def corpus() -> list[dict]:
    path = FIXTURES_DIR / "notes.jsonl"
    if not path.exists():
        return []
    return read_jsonl(path)


def gap_note() -> dict:
    path = FIXTURES_DIR / "gateway_gap.json"
    if not path.exists():
        return {
            "note_id": "gap-card",
            "tenant_id": "clinic-a",
            "text": (
                "Patient Maria Lopez (DOB 1984-03-12, SSN 999-12-3456) of 42 Oak St, Fairfax, Virginia, "
                "phone 555-201-8890, email maria.lopez@example.com, seen for hypertension follow-up; "
                "continuing lisinopril 10mg. Copay card 4111-1111-1111-1111 was entered on the slip."
            ),
        }
    return json.loads(path.read_text())
