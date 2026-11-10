"""Authentication dependencies and request models."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from filemind.config import get_settings
from filemind.database import get_db
from filemind.models import Session, User
from filemind.security import csrf_matches, token_hash


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()


class RegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_enabled: bool


async def current_session(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    session_token: Annotated[str | None, Cookie(alias=get_settings().session_cookie_name)] = None,
) -> tuple[User, Session, str]:
    if not session_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "AUTH_REQUIRED")
    result = await db.execute(
        select(Session).where(Session.token_hash == token_hash(session_token))
    )
    session = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if session is None or session.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "AUTH_REQUIRED")
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "AUTH_REQUIRED")
    request.state.user_id = user.id
    return user, session, session_token


async def csrf_guard(
    request: Request,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    auth: tuple[User, Session, str] = Depends(current_session),
) -> tuple[User, Session, str]:
    settings = get_settings()
    origin = request.headers.get("origin")
    if not origin or origin.rstrip("/") != settings.app_origin.rstrip("/"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF_INVALID")
    if not csrf_header or not settings.has_session_secret or settings.session_secret is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF_INVALID")
    expected = csrf_matches(csrf_header, auth[2], settings.session_secret.get_secret_value())
    if not expected or session_hash(csrf_header) != auth[1].csrf_hash:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF_INVALID")
    return auth


Auth = Annotated[tuple[User, Session, str], Depends(current_session)]
WriteAuth = Annotated[tuple[User, Session, str], Depends(csrf_guard)]


def session_hash(value: str) -> str:
    return token_hash(value)
