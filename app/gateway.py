"""Client for the LiteLLM proxy and for the Presidio services that proxy calls."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

from app.settings import (
    CAPTURE_DIR,
    CHAT_MODEL,
    EMBED_MODEL,
    GATEWAY_ENTITIES,
    KEYS_PATH,
    LITELLM_BASE_URL,
    LITELLM_MASTER_KEY,
    PRESIDIO_ANALYZER_API_BASE,
    PRESIDIO_ANONYMIZER_API_BASE,
    ROOT,
    TENANTS,
)

AD_HOC_PATH = ROOT / "litellm" / "ad_hoc_recognizers.json"
_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


class GatewayError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def load_ad_hoc() -> list[dict]:
    if not AD_HOC_PATH.exists():
        return []
    return json.loads(AD_HOC_PATH.read_text())


class Gateway:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0))
        self.keys: dict[str, dict] = {}
        self.last_error: str | None = None

    async def aclose(self) -> None:
        await self.client.aclose()

    def _master(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {LITELLM_MASTER_KEY}"}

    async def healthy(self) -> bool:
        try:
            response = await self.client.get(f"{LITELLM_BASE_URL}/health/liveliness", timeout=3)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def presidio_healthy(self) -> bool:
        try:
            response = await self.client.get(PRESIDIO_ANALYZER_API_BASE + "health", timeout=3)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure_keys(self) -> dict[str, dict]:
        if not await self.healthy():
            self.last_error = "LiteLLM proxy is not reachable."
            self.keys = _read_keys()
            return self.keys
        disk = _read_keys()
        if disk and await self._keys_valid(disk):
            self.keys = disk
            self.last_error = None
            return disk
        fresh: dict[str, dict] = {}
        for tenant_id, meta in TENANTS.items():
            await self._delete_alias(meta["alias"])
            created = await self._generate(tenant_id, meta)
            fresh[tenant_id] = created
        KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
        KEYS_PATH.write_text(json.dumps(fresh, indent=2))
        self.keys = fresh
        self.last_error = None
        return fresh

    async def _keys_valid(self, keys: dict) -> bool:
        for info in keys.values():
            try:
                await self.key_info(info["key"])
            except GatewayError:
                return False
        return True

    async def _delete_alias(self, alias: str) -> None:
        response = await self.client.post(
            f"{LITELLM_BASE_URL}/key/delete",
            headers=self._master(),
            json={"key_aliases": [alias]},
        )
        if response.status_code < 400:
            return
        # A missing alias is fine on first boot. Other errors surface at generate time.

    async def _generate(self, tenant_id: str, meta: dict) -> dict:
        response = await self.client.post(
            f"{LITELLM_BASE_URL}/key/generate",
            headers=self._master(),
            json={
                "key_alias": meta["alias"],
                "models": [CHAT_MODEL, EMBED_MODEL],
                "max_budget": meta["max_budget"],
                "budget_duration": "30d",
                "metadata": {"tenant_id": tenant_id},
            },
        )
        if response.status_code >= 400:
            raise GatewayError(response.status_code, f"key generate failed for {tenant_id}: {response.text[:300]}")
        body = response.json()
        token = body.get("key") or body.get("token")
        if not token:
            raise GatewayError(502, f"key generate for {tenant_id} returned no key")
        return {
            "key": token,
            "alias": meta["alias"],
            "max_budget": meta["max_budget"],
            "tenant_id": tenant_id,
        }

    async def key_info(self, api_key: str) -> dict:
        response = await self.client.get(
            f"{LITELLM_BASE_URL}/key/info",
            headers=self._master(),
            params={"key": api_key},
            timeout=10,
        )
        if response.status_code >= 400:
            raise GatewayError(response.status_code, response.text[:300])
        body = response.json()
        info = body.get("info") if isinstance(body, dict) else None
        if not isinstance(info, dict):
            info = body if isinstance(body, dict) else {}
        return info

    async def embed(self, api_key: str, text: str) -> list[float]:
        response = await self.client.post(
            f"{LITELLM_BASE_URL}/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": EMBED_MODEL, "input": text},
            timeout=60,
        )
        if response.status_code >= 400:
            raise GatewayError(response.status_code, response.text[:400])
        data = response.json()["data"][0]["embedding"]
        return [float(value) for value in data]

    async def chat(self, api_key: str, messages: list[dict], request_id: str) -> httpx.Response:
        return await self.client.post(
            f"{LITELLM_BASE_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": CHAT_MODEL,
                "messages": messages,
                "max_tokens": 180,
                "temperature": 0.2,
                "user": request_id,
                "metadata": {"phi_request_id": request_id},
                "guardrails": ["presidio-pre-call"],
            },
        )

    async def mask_text(self, text: str) -> tuple[str, list[dict]]:
        """Replay the gateway entity list against the same Presidio HTTP service the proxy uses."""
        if not text.strip():
            return text, []
        analyze = await self.client.post(
            PRESIDIO_ANALYZER_API_BASE + "analyze",
            json={
                "text": text,
                "language": "en",
                "entities": GATEWAY_ENTITIES,
                "score_threshold": SCORE_THRESHOLD_LOCAL,
                "ad_hoc_recognizers": load_ad_hoc(),
            },
            timeout=60,
        )
        if analyze.status_code >= 400:
            raise GatewayError(analyze.status_code, analyze.text[:300])
        raw_results = analyze.json()
        findings = []
        analyzer_results = []
        for item in raw_results:
            if item.get("score", 0) < SCORE_THRESHOLD_LOCAL:
                continue
            finding = {
                "entity_type": item["entity_type"],
                "start": item["start"],
                "end": item["end"],
                "score": round(float(item["score"]), 4),
                "recognizer": (item.get("analysis_explanation") or {}).get("recognizer_name")
                or (item.get("recognition_metadata") or {}).get("recognizer_name")
                or "presidio",
            }
            findings.append(finding)
            analyzer_results.append(
                {
                    "entity_type": item["entity_type"],
                    "start": item["start"],
                    "end": item["end"],
                    "score": item["score"],
                }
            )
        if not analyzer_results:
            return text, []
        anonymize = await self.client.post(
            PRESIDIO_ANONYMIZER_API_BASE + "anonymize",
            json={"text": text, "analyzer_results": analyzer_results},
            timeout=30,
        )
        if anonymize.status_code >= 400:
            raise GatewayError(anonymize.status_code, anonymize.text[:300])
        return anonymize.json()["text"], findings

    async def read_capture(self, request_id: str) -> dict | None:
        if not _ID.match(request_id):
            return None
        path = CAPTURE_DIR / f"{request_id}.json"
        for _ in range(20):
            if path.exists():
                try:
                    return json.loads(path.read_text())
                except json.JSONDecodeError:
                    return None
            await asyncio.sleep(0.05)
        return None


SCORE_THRESHOLD_LOCAL = 0.4


def _read_keys() -> dict:
    if not KEYS_PATH.exists():
        return {}
    try:
        data = json.loads(KEYS_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def redact_key(api_key: str) -> str:
    if len(api_key) < 10:
        return "sk-…"
    return f"{api_key[:7]}…{api_key[-4:]}"
