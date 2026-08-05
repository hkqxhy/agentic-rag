from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets

from fastapi import Request
from pwdlib import PasswordHash

from .settings import Settings

PASSWORD_HASH = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = PASSWORD_HASH.hash("agentic-rag-dummy-password")


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(PASSWORD_HASH.hash, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    candidate_hash = password_hash or _DUMMY_PASSWORD_HASH
    verified = await asyncio.to_thread(PASSWORD_HASH.verify, password, candidate_hash)
    return bool(password_hash) and verified


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", maxsplit=1)[0].strip()
    return request.client.host if request.client else "unknown"


def client_fingerprint(request: Request, settings: Settings) -> str:
    source = f"{client_ip(request, settings)}\n{request.headers.get('user-agent', '')}"
    return hmac.new(
        settings.audit_hash_key.encode("utf-8"),
        source.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def private_identifier(value: str, settings: Settings) -> str:
    return hmac.new(
        settings.audit_hash_key.encode("utf-8"),
        value.strip().casefold().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
