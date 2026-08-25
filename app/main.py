"""Prism Scanner backend -- FastAPI application entrypoint.

Run it with::

    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Interactive API docs: http://localhost:8000/docs

Host binding matters: the Flutter client defaults to ``http://10.0.2.2:8000``,
the Android emulator's alias for the host machine, so the server must listen on
``0.0.0.0`` rather than ``127.0.0.1`` to be reachable from the emulator.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .db import check_connection
from .logging_config import configure_logging, get_logger
from .opencv_document_scanner import pipeline_status
from .routers import ai_history, auth, billing, cloud, ocr, passport_photo, structure
from .storage import storage_root

configure_logging(settings.log_level)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s", settings.app_name)

    root = storage_root()
    log.info("Storage root: %s", root)

    if check_connection():
        log.info(
            "Connected to MySQL database '%s' at %s:%s",
            settings.mysql_database,
            settings.mysql_host,
            settings.mysql_port,
        )
    else:
        # Not fatal: the server still serves /health and /docs, which is the
        # fastest way for the user to see *why* it isn't working.
        log.error(
            "Could not reach MySQL. Start XAMPP's MySQL service and import "
            "schema.sql, then check MYSQL_* in backend/.env."
        )

    if not settings.jwt_secret:
        log.error("JWT_SECRET is not set -- /auth/email/verify-otp will fail.")
    if (
        not settings.smtp_dev_mode
        and settings.email_provider.lower() == "smtp"
        and not settings.smtp_app_password
    ):
        log.warning(
            "SMTP_APP_PASSWORD is not set -- OTP emails will fail. Set it in "
            "backend/.env, or set SMTP_DEV_MODE=true to log codes instead."
        )

    yield
    log.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title="Prism Scanner API",
    description=(
        "Backend for the Prism Scanner Flutter app: email+OTP auth, Prism Cloud "
        "document sync, passport-photo background replacement, and OpenCV "
        "document scanning."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,  # must stay False while allow_origins can be "*"
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


# ---------------------------------------------------------------------------
# Error envelope
#
# The Flutter client JSON-decodes EVERY response, including errors, and reads
# `data['message']` (see auth_service.dart). FastAPI's default error body is
# {"detail": ...}, which would leave the client showing its generic fallback
# text instead of the real reason. These handlers rewrite every error into
# {"message": ...} so the app can surface something useful.
# ---------------------------------------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    detail = exc.detail
    if not isinstance(detail, str):
        detail = str(detail) if detail else "Request failed."

    if exc.status_code >= 500:
        log.error("%s %s -> %s: %s", request.method, request.url.path, exc.status_code, detail)
    else:
        log.info("%s %s -> %s: %s", request.method, request.url.path, exc.status_code, detail)

    return JSONResponse(
        status_code=exc.status_code,
        content={"message": detail},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    problems = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        problems.append(f"{location or 'body'}: {error.get('msg', 'invalid')}")

    message = "; ".join(problems) or "The request was malformed."
    log.info("%s %s -> 422: %s", request.method, request.url.path, message)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"message": message},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "Something went wrong on the server."},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
app.include_router(auth.router)
app.include_router(cloud.router)
app.include_router(passport_photo.router)
app.include_router(ocr.router)
app.include_router(structure.router)
app.include_router(billing.router)
app.include_router(ai_history.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Liveness check. Always returns ``{"status": "ok"}`` plus diagnostics."""
    return {"status": "ok"}


@app.get("/health/detail", tags=["meta"])
def health_detail() -> dict:
    """Deeper check: MySQL reachability and OpenCV scanner status.

    Useful during setup without reading the logs.
    """
    return {
        "status": "ok",
        "database": {
            "connected": check_connection(),
            "name": settings.mysql_database,
            "host": settings.mysql_host,
            "port": settings.mysql_port,
        },
        "smtp": {
            "configured": bool(settings.smtp_user and settings.smtp_app_password),
            "devMode": settings.smtp_dev_mode,
            "host": settings.smtp_host,
            "port": settings.smtp_port,
        },
        "emailProvider": settings.email_provider,
        "emailConfigured": bool(
            settings.resend_api_key and settings.email_from
        ) if settings.email_provider.lower() == "resend" else bool(
            settings.smtp_user and settings.smtp_app_password
        ),
        "jwtConfigured": bool(settings.jwt_secret),
        "storageRoot": str(settings.storage_path),
        "ocr": pipeline_status(),
    }


@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "name": "Prism Scanner API",
        "docs": "/docs",
        "health": "/health",
    }
