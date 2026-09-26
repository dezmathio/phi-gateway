"""Tenant comes from the virtual key. A key for Clinic A cannot open Clinic B."""

from __future__ import annotations

import secrets

from app.settings import TENANTS


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _same(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)


def authorize(api_key: str, tenant_id: str, keys: dict[str, dict]) -> dict:
    if tenant_id not in TENANTS:
        raise AuthError(404, f"Unknown tenant '{tenant_id}'.")
    if not api_key:
        raise AuthError(401, "Missing API key.")
    for owner_id, info in keys.items():
        if _same(info.get("key", ""), api_key):
            if owner_id != tenant_id:
                alias = info.get("alias", owner_id)
                raise AuthError(
                    403,
                    f"Key alias {alias} is bound to {owner_id} and cannot query {tenant_id}.",
                )
            return info
    raise AuthError(401, "Unknown API key.")
