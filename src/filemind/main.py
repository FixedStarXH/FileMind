"""FastAPI application entry point."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from filemind.config import get_settings
from filemind.routes_auth import router as account_router
from filemind.routes_documents import router as documents_router
from filemind.routes_library import router as library_router
from filemind.routes_tasks import router as tasks_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.database_engine = (
        create_async_engine(settings.database_url, pool_pre_ping=True)
        if settings.database_url
        else None
    )
    yield
    engine = app.state.database_engine
    if engine is not None:
        await engine.dispose()


app = FastAPI(
    title="FileMind API",
    description="Private study and job-search document workspace",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(account_router)
app.include_router(library_router)
app.include_router(documents_router)
app.include_router(tasks_router)


@app.middleware("http")
async def attach_request_id(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, _exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "服务暂时无法处理请求",
                "details": {},
            },
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    code = str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {"code": code, "message": code, "details": {}},
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id, **(exc.headers or {})},
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, _exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=422,
        content={
            "error": {"code": "VALIDATION_ERROR", "message": "请求参数无效", "details": {}},
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


@app.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready", tags=["health"])
async def ready(request: Request) -> JSONResponse:
    settings = get_settings()
    checks: dict[str, str] = {}
    engine = request.app.state.database_engine

    if engine is None:
        checks["database"] = "not_configured"
    else:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "unavailable"

    if not settings.redis_url:
        checks["broker"] = "not_configured"
    else:
        client = None
        try:
            from redis.asyncio import Redis

            client = Redis.from_url(settings.redis_url, socket_connect_timeout=1)
            await cast(Awaitable[bool], client.ping())
            checks["broker"] = "ok"
        except Exception:
            checks["broker"] = "unavailable"
        finally:
            if client is not None:
                await client.aclose()

    try:
        checks["storage"] = "ok" if settings.storage_root.is_dir() else "unavailable"
    except OSError:
        checks["storage"] = "unavailable"

    checks["deepseek"] = "configured" if settings.has_deepseek_key else "not_configured"
    ready_state = all(checks[key] == "ok" for key in ("database", "broker", "storage"))
    status_code = 200 if ready_state else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if ready_state else "not_ready", "checks": checks},
    )
