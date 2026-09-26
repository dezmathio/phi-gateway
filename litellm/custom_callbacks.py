"""Record the messages LiteLLM is about to send to the provider, after pre-call guardrails."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from litellm.integrations.custom_logger import CustomLogger

_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


def _content(message):
    if isinstance(message, dict):
        return {"role": message.get("role"), "content": message.get("content")}
    return {"role": getattr(message, "role", None), "content": getattr(message, "content", None)}


def _request_id(kwargs: dict) -> str | None:
    user = kwargs.get("user")
    if isinstance(user, str) and _ID.match(user):
        return user
    found = _find_id(kwargs.get("litellm_params") or {})
    if found:
        return found
    return _find_id(kwargs.get("metadata") or {})


def _find_id(value, depth: int = 0) -> str | None:
    if depth > 5:
        return None
    if isinstance(value, dict):
        token = value.get("phi_request_id") or value.get("user")
        if isinstance(token, str) and _ID.match(token):
            return token
        for child in value.values():
            found = _find_id(child, depth + 1)
            if found:
                return found
    return None


def _write(model, messages, kwargs) -> None:
    request_id = _request_id(kwargs or {})
    if not request_id:
        return
    directory = Path(os.environ.get("PHI_CAPTURE_DIR", "/captures"))
    try:
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "phi_request_id": request_id,
            "model": model,
            "messages": [_content(message) for message in (messages or [])],
        }
        target = directory / f"{request_id}.json"
        temporary = directory / f".{request_id}.tmp"
        temporary.write_text(json.dumps(payload))
        temporary.replace(target)
    except OSError:
        return


class CaptureMaskedPrompt(CustomLogger):
    def log_pre_api_call(self, model, messages, kwargs):
        _write(model, messages, kwargs)

    async def async_log_pre_api_call(self, model, messages, kwargs):
        _write(model, messages, kwargs)


proxy_handler_instance = CaptureMaskedPrompt()
