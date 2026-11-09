"""M1 account and settings endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from filemind.auth import Auth, LoginBody, PasswordBody, SettingsPatch, WriteAuth
from filemind.config import get_settings
from filemind.database import get_db
from filemind.models import Session, User
from filemind.security import csrf_token, hash_password, new_token, token_hash, verify_password

router = APIRouter(prefix="/api/v1", tags=["account"])
_DUMMY_HASH = hash_password("filemind-invalid-password-hash")


def user_payload(user: User) -> dict[str, object]:
    return {"id": str(user.id), "email": user.email}


def settings_payload(user: User) -> dict[str, object]:
    config = get_settings()
    return {
        "ai_enabled": user.ai_enabled,
        "quota_bytes": user.quota_bytes,
        "used_bytes": user.used_bytes,
        "reserved_bytes": user.reserved_bytes,
        "capabilities": {
            "deepseek": config.has_deepseek_key,
            "keyword_search": True,
            "vector_search": False,
        },
    }


async def enforce_login_limits(request: Request, email: str) -> None:
    config = get_settings()
    if not config.redis_url:
        raise HTTPException(503, "LOGIN_SERVICE_UNAVAILABLE")
    client = Redis.from_url(config.redis_url, socket_connect_timeout=1)
    try:
        ip = request.client.host if request.client else "unknown"
        buckets = (
            (f"login:email:{token_hash(email)}", 10),
            (f"login:ip:{token_hash(ip)}", 30),
        )
        for key, maximum in buckets:
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, 900)
            if count > maximum:
                raise HTTPException(429, "RATE_LIMITED", headers={"Retry-After": "900"})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "LOGIN_SERVICE_UNAVAILABLE") from exc
    finally:
        await client.aclose()


def set_session_cookie(response: Response, raw_token: str) -> None:
    config = get_settings()
    response.set_cookie(
        key=config.session_cookie_name,
        value=raw_token,
        httponly=True,
        secure=config.app_env == "production",
        samesite="lax",
        path="/",
        max_age=config.session_ttl_days * 86400,
    )


@router.post("/auth/login")
async def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    config = get_settings()
    session_secret = config.session_secret
    origin = request.headers.get("origin")
    if not origin or origin.rstrip("/") != config.app_origin.rstrip("/"):
        raise HTTPException(403, "CSRF_INVALID")
    if not config.has_session_secret or session_secret is None or not config.redis_url:
        raise HTTPException(503, "LOGIN_SERVICE_UNAVAILABLE")
    await enforce_login_limits(request, body.email)

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    password_hash = user.password_hash if user else _DUMMY_HASH
    valid = verify_password(password_hash, body.password)
    if user is None or not user.is_active or not valid:
        raise HTTPException(401, "INVALID_CREDENTIALS")

    raw_token = new_token()
    csrf = csrf_token(raw_token, session_secret.get_secret_value())
    session = Session(
        user_id=user.id,
        token_hash=token_hash(raw_token),
        csrf_hash=token_hash(csrf),
        expires_at=datetime.now(UTC) + timedelta(days=config.session_ttl_days),
    )
    db.add(session)
    await db.commit()
    set_session_cookie(response, raw_token)
    return {
        "data": {"user": user_payload(user), "csrf_token": csrf},
        "request_id": request.state.request_id,
    }


@router.get("/auth/me")
async def me(request: Request, auth: Auth) -> dict[str, object]:
    user, _session, raw_token = auth
    config = get_settings()
    if not config.has_session_secret or config.session_secret is None:
        raise HTTPException(503, "LOGIN_SERVICE_UNAVAILABLE")
    csrf = csrf_token(raw_token, config.session_secret.get_secret_value())
    return {
        "data": {"user": user_payload(user), "csrf_token": csrf},
        "request_id": request.state.request_id,
    }


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    _user, session, _token = auth
    await db.delete(session)
    await db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        get_settings().session_cookie_name, path="/", httponly=True, samesite="lax"
    )
    return response


@router.post("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordBody,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    user, _session, _token = auth
    if not verify_password(user.password_hash, body.old_password):
        raise HTTPException(400, "INVALID_OLD_PASSWORD")
    user.password_hash = hash_password(body.new_password)
    user.password_changed_at = datetime.now(UTC)
    await db.execute(delete(Session).where(Session.user_id == user.id))
    await db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        get_settings().session_cookie_name, path="/", httponly=True, samesite="lax"
    )
    return response


@router.get("/settings")
async def read_settings(request: Request, auth: Auth) -> dict[str, object]:
    return {"data": settings_payload(auth[0]), "request_id": request.state.request_id}


@router.patch("/settings")
async def update_settings(
    body: SettingsPatch,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    user = auth[0]
    if body.ai_enabled and not get_settings().has_deepseek_key:
        raise HTTPException(403, "AI_NOT_CONFIGURED")
    user.ai_enabled = body.ai_enabled
    await db.commit()
    return {"data": settings_payload(user), "request_id": request.state.request_id}
