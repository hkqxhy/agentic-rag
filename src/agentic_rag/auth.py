from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditEventModel, AuthSessionModel, UserModel
from .security import client_fingerprint, hash_session_token


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_user(self, email: str, username: str, password_hash: str) -> UserModel:
        user = UserModel(
            email=email.strip().casefold(),
            username=username.strip().casefold(),
            password_hash=password_hash,
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def find_user(self, identifier: str) -> UserModel | None:
        normalized = identifier.strip().casefold()
        return await self.session.scalar(
            select(UserModel).where(
                or_(UserModel.email == normalized, UserModel.username == normalized)
            )
        )

    async def create_session(
        self,
        user_id: UUID,
        token_hash: str,
        fingerprint: str,
        ttl_seconds: int,
    ) -> AuthSessionModel:
        session = AuthSessionModel(
            user_id=user_id,
            token_hash=token_hash,
            client_fingerprint=fingerprint,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        self.session.add(session)
        await self.session.flush()
        return session

    async def resolve_session(self, token_hash: str) -> UserModel | None:
        now = datetime.now(UTC)
        row = await self.session.execute(
            select(AuthSessionModel, UserModel)
            .join(UserModel, UserModel.id == AuthSessionModel.user_id)
            .where(
                AuthSessionModel.token_hash == token_hash,
                AuthSessionModel.revoked_at.is_(None),
                AuthSessionModel.expires_at > now,
                UserModel.is_active.is_(True),
            )
        )
        match = row.one_or_none()
        if match is None:
            return None
        auth_session, user = match
        if auth_session.last_seen_at < now - timedelta(minutes=5):
            auth_session.last_seen_at = now
            await self.session.flush()
        return user

    async def revoke_session(self, token_hash: str) -> UUID | None:
        auth_session = await self.session.scalar(
            select(AuthSessionModel).where(
                AuthSessionModel.token_hash == token_hash,
                AuthSessionModel.revoked_at.is_(None),
            )
        )
        if auth_session is None:
            return None
        auth_session.revoked_at = datetime.now(UTC)
        await self.session.flush()
        return auth_session.user_id


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        event_type: str,
        outcome: str,
        *,
        actor_user_id: UUID | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        request_id: str | None = None,
        fingerprint: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditEventModel(
                actor_user_id=actor_user_id,
                event_type=event_type,
                outcome=outcome,
                target_type=target_type,
                target_id=target_id,
                request_id=request_id,
                client_fingerprint=fingerprint,
                event_metadata=metadata or {},
            )
        )
        await self.session.flush()


async def require_user(request: Request) -> UserModel:
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    async with request.app.state.database.session() as session:
        user = await AuthRepository(session).resolve_session(hash_session_token(token))
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        await session.commit()
        return user


def request_fingerprint(request: Request) -> str:
    return client_fingerprint(request, request.app.state.settings)
