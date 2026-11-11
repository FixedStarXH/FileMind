"""Administrative account commands."""

import argparse
import asyncio
from getpass import getpass
from datetime import UTC, datetime

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import delete, select

from filemind.config import get_settings
from filemind.models import Session, User
from filemind.security import hash_password


async def create_user(email: str, password: str) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    try:
        normalized = str(TypeAdapter(EmailStr).validate_python(email.strip())).casefold()
    except ValidationError as exc:
        raise SystemExit("邮箱格式无效") from exc
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            existing = await session.scalar(select(User.id).where(User.email == normalized))
            if existing:
                raise SystemExit("该邮箱已存在")
            session.add(
                User(
                    email=normalized,
                    password_hash=hash_password(password),
                    quota_bytes=settings.user_quota_bytes,
                    used_bytes=0,
                    reserved_bytes=0,
                    ai_enabled=False,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def reset_password(email: str, password: str) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    try:
        normalized = str(TypeAdapter(EmailStr).validate_python(email.strip())).casefold()
    except ValidationError as exc:
        raise SystemExit("邮箱格式无效") from exc
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user = await session.scalar(select(User).where(User.email == normalized))
            if user is None:
                raise SystemExit("没有找到该邮箱对应的账户")
            user.password_hash = hash_password(password)
            user.password_changed_at = datetime.now(UTC)
            await session.execute(delete(Session).where(Session.user_id == user.id))
            await session.commit()
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="filemind")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create-user")
    create_parser.add_argument("email")
    reset_parser = subparsers.add_parser("reset-password")
    reset_parser.add_argument("email")
    args = parser.parse_args()
    password = getpass("新密码（至少12位）: ")
    confirm = getpass("再次输入密码: ")
    if len(password) < 12 or password != confirm:
        raise SystemExit("密码不匹配或少于12位")
    if args.command == "create-user":
        asyncio.run(create_user(args.email, password))
        print("账户已创建")
    else:
        asyncio.run(reset_password(args.email, password))
        print("密码已重置，原有登录会话已注销")


if __name__ == "__main__":
    main()
