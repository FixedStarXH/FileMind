"""Database session and base model."""

from collections.abc import AsyncIterator

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    engine = request.app.state.database_engine
    if engine is None:
        raise HTTPException(503, "DATABASE_UNAVAILABLE")
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with session_factory(request)() as session:
        yield session
