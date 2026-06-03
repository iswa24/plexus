"""Caller identity.

This is the single point where identity enters the system. In production:
  - validate the bearer token (OIDC / JWT) against your IdP, and
  - for Trino On-Behalf-Of, exchange it for a downstream token here.
The Principal is then propagated to every connector so row-level security
holds per app user.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass
class Principal:
    username: str
    token: Optional[str] = None


def principal_from_headers(headers: Mapping[str, str]) -> Principal:
    auth = headers.get("authorization", "") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else None
    user = (
        headers.get("x-user")
        or headers.get("x-remote-user")
        or "demo-user"
    )
    return Principal(username=user, token=token)
