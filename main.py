"""
ShieldBot — FastAPI Application Entry Point
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

import sentry_sdk
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app.core.config import get_settings
from app.db.redis_client import get_redis
from app.db.session import engine
from app.models.models import Base
from app.services.evidence_store import get_evidence_store

settings = get_settings()
logger = structlog.get_logger()

# ── Prometheus Metrics ────────────────────────────────────────
REQUEST_COUNT = Counter("shieldbot_http_requests_total", "Total HTTP requests", ["method", "endpoint", "status"])
REQUEST_LATENCY = Histogram("shieldbot_http_request_duration_seconds", "HTTP request latency", ["endpoint"])
INCIDENTS_CREATED = Counter("shieldbot_incidents_created_total", "Incidents created", ["type", "severity"])


# ──────────────────────────────────────────────────────────────
# Application Lifecycle
# ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("ShieldBot starting up…")

    if settings.sentry_dsn:
        sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)

    # Create DB tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Ensure MinIO bucket
    evidence_store = get_evidence_store()
    await evidence_store.ensure_bucket()

    # Warm Redis connection
    redis = await get_redis()
    await redis.ping()

    # Load ML model
    from app.ml.risk_scorer import get_scorer
    get_scorer()

    logger.info("ShieldBot startup complete")
    yield

    # Shutdown
    logger.info("ShieldBot shutting down…")
    if redis:
        await redis.aclose()


# ──────────────────────────────────────────────────────────────
# App Creation
# ──────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    application = FastAPI(
        title="ShieldBot API",
        version="1.0.0",
        description="Enterprise Telegram Group Protection System",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.debug else None,
        redoc_url="/api/redoc" if settings.debug else None,
    )

    # ── Middleware ────────────────────────────────────────────
    application.add_middleware(GZipMiddleware, minimum_size=1000)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.perf_counter()
        response: Response = await call_next(request)
        duration = time.perf_counter() - start
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{duration:.4f}s"
        REQUEST_COUNT.labels(request.method, request.url.path, response.status_code).inc()
        REQUEST_LATENCY.labels(request.url.path).observe(duration)
        return response

    # ── Routers ───────────────────────────────────────────────
    from app.api import webhook, groups, incidents, actions, auth, metrics_router

    application.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    application.include_router(webhook.router, prefix="/api/v1/webhook", tags=["webhook"])
    application.include_router(groups.router, prefix="/api/v1/groups", tags=["groups"])
    application.include_router(incidents.router, prefix="/api/v1/incidents", tags=["incidents"])
    application.include_router(actions.router, prefix="/api/v1/actions", tags=["actions"])
    application.include_router(metrics_router.router, prefix="/api/v1/metrics", tags=["metrics"])

    @application.get("/health")
    async def health():
        return {"status": "ok", "version": "1.0.0"}

    @application.get("/metrics")
    async def prometheus_metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return application


app = create_app()

