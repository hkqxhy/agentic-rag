from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    username: str
    created_at: datetime


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=10, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().casefold()


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class AuthResponse(BaseModel):
    user: UserView


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=120)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8_000)


class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    role: MessageRole
    content: str
    created_at: datetime


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ActiveRunView(BaseModel):
    run_id: UUID
    status: RunStatus


class ConversationDetail(ConversationSummary):
    messages: list[MessageView]
    active_run: ActiveRunView | None = None


class RunAccepted(BaseModel):
    run_id: UUID
    conversation_id: UUID
    input_message: MessageView
    status: RunStatus


class HealthResponse(BaseModel):
    status: str
    service: str
    dependencies: dict[str, str] = Field(default_factory=dict)
