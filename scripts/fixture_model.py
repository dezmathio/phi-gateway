#!/usr/bin/env python3
"""OpenAI-compatible stand-in used when no model server is running.

The reply quotes the placeholders it actually received, so the fourth panel still
shows that the call went through the gateway. It is not a clinical model.
Point CHAT_MODEL at Ollama or a hosted provider for a real completion.
"""

from __future__ import annotations

import hashlib
import math
import re

import uvicorn
from fastapi import FastAPI

app = FastAPI()
_TAG = re.compile(r"<[A-Z][A-Z0-9_]*>")


def hashed_embed(text: str, dims: int = 64) -> list[float]:
    vector = [0.0] * dims
    for token in re.findall(r"[a-z0-9<>_-]{2,}", text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        vector[digest[0] % dims] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/chat/completions")
def chat(body: dict):
    user = ""
    for message in body.get("messages") or []:
        if message.get("role") == "user":
            user = message.get("content") or ""
    tags = _TAG.findall(user)
    unique = []
    for tag in tags:
        if tag not in unique:
            unique.append(tag)
    reply = (
        "Fixture chart summary. This process is not a language model; it only proves the gateway "
        "forwarded a prompt. Placeholders in that prompt: "
        + (", ".join(unique[:20]) if unique else "none")
        + "."
    )
    prompt_tokens = max(1, len(user) // 4)
    completion_tokens = max(1, len(reply) // 4)
    return {
        "id": "chatcmpl-fixture",
        "object": "chat.completion",
        "model": body.get("model") or "fixture-chart",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": reply},
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


@app.post("/v1/embeddings")
def embeddings(body: dict):
    incoming = body.get("input")
    if isinstance(incoming, str):
        incoming = [incoming]
    return {
        "object": "list",
        "model": body.get("model") or "fixture-embed",
        "data": [
            {"object": "embedding", "index": index, "embedding": hashed_embed(text or "")}
            for index, text in enumerate(incoming or [""])
        ],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8088)
