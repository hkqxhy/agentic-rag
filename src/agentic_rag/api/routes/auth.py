from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError

from agentic_rag.auth import AuditRepository, AuthRepository, request_fingerprint, require_user
from agentic_rag.models import UserModel
from agentic_rag.schemas import AuthResponse, LoginRequest, RegisterRequest, UserView
from agentic_rag.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    private_identifier,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


async def _enforce_auth_rate_limit(request: Request, identifier: str) -> None:
    settings = request.app.state.settings
    for scope, limit in (
        (f"auth:client:{request_fingerprint(request)}", settings.auth_rate_limit),
        (
            f"auth:identity:{private_identifier(identifier, settings)}",
            settings.auth_identity_rate_limit,
        ),
    ):
        result = await request.app.state.broker.consume_rate_limit(
            scope,
            limit,
            settings.auth_rate_window_seconds,
        )
        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many authentication attempts",
                headers={"Retry-After": str(result.retry_after_seconds)},
            )


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        expires=datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds),
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, response: Response) -> AuthResponse:
    await _enforce_auth_rate_limit(request, payload.email)
    password_hash = await hash_password(payload.password)
    fingerprint = request_fingerprint(request)
    token = generate_session_token()
    async with request.app.state.database.session() as session:
        repository = AuthRepository(session)
        try:
            user = await repository.create_user(payload.email, payload.username, password_hash)
            await repository.create_session(
                user.id,
                hash_session_token(token),
                fingerprint,
                request.app.state.settings.session_ttl_seconds,
            )
            await AuditRepository(session).record(
                "auth.register",
                "success",
                actor_user_id=user.id,
                request_id=request.state.request_id,
                fingerprint=fingerprint,
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            await AuditRepository(session).record(
                "auth.register",
                "conflict",
                request_id=request.state.request_id,
                fingerprint=fingerprint,
            )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email or username is already registered",
            ) from exc
    _set_session_cookie(response, request, token)
    return AuthResponse(user=UserView.model_validate(user))


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, request: Request, response: Response) -> AuthResponse:
    await _enforce_auth_rate_limit(request, payload.identifier)
    fingerprint = request_fingerprint(request)
    async with request.app.state.database.session() as session:
        repository = AuthRepository(session)
        user = await repository.find_user(payload.identifier)
        verified = await verify_password(payload.password, user.password_hash if user else None)
        if user is None or not user.is_active or not verified:
            await AuditRepository(session).record(
                "auth.login",
                "failure",
                request_id=request.state.request_id,
                fingerprint=fingerprint,
            )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        token = generate_session_token()
        await repository.create_session(
            user.id,
            hash_session_token(token),
            fingerprint,
            request.app.state.settings.session_ttl_seconds,
        )
        await AuditRepository(session).record(
            "auth.login",
            "success",
            actor_user_id=user.id,
            request_id=request.state.request_id,
            fingerprint=fingerprint,
        )
        await session.commit()
    _set_session_cookie(response, request, token)
    return AuthResponse(user=UserView.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> Response:
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        async with request.app.state.database.session() as session:
            user_id = await AuthRepository(session).revoke_session(hash_session_token(token))
            await AuditRepository(session).record(
                "auth.logout",
                "success",
                actor_user_id=user_id,
                request_id=request.state.request_id,
                fingerprint=request_fingerprint(request),
            )
            await session.commit()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=AuthResponse)
async def me(user: Annotated[UserModel, Depends(require_user)]) -> AuthResponse:
    return AuthResponse(user=UserView.model_validate(user))
