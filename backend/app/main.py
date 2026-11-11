import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.request_context import request_id_context
from app.core.exceptions import (
    MarketTwinException,
    generic_exception_handler,
    http_exception_handler,
    markettwin_exception_handler,
    validation_exception_handler,
)
from app.core.supabase import check_supabase_health
from app.services.ai.diagnostics import (
    loopback_in_deployed_environment,
    provider_context,
    safe_ollama_base_url,
)

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("markettwin.app")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager for startup and shutdown events."""
    logger.info("=" * 60)
    logger.info(f"Starting {settings.PROJECT_NAME} v{settings.VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT} | Debug: {settings.DEBUG}")
    logger.info(f"Allowed CORS Origins: {settings.cors_origins}")
    logger.info(f"AI target: {provider_context(settings)}")
    if loopback_in_deployed_environment(settings):
        logger.warning(
            "OLLAMA_BASE_URL targets loopback (%s) while ENVIRONMENT=%s. A deployed "
            "backend cannot reach the host machine's Ollama; AI conversations will "
            "return 503 until a reachable endpoint is configured.",
            safe_ollama_base_url(settings.OLLAMA_BASE_URL),
            settings.ENVIRONMENT,
        )
    db_status, db_msg = check_supabase_health()
    logger.info(f"Supabase Status: {db_status} ({db_msg})")
    logger.info("=" * 60)
    if settings.AI_PROVIDER.strip().lower() == "ollama":
        from app.services.ai.ollama_provider import initialize_ollama_http_client

        initialize_ollama_http_client()
    try:
        yield
    finally:
        if settings.AI_PROVIDER.strip().lower() == "ollama":
            from app.services.ai.ollama_provider import close_ollama_http_client

            await close_ollama_http_client()
        logger.info(f"Shutting down {settings.PROJECT_NAME}...")


def create_application() -> FastAPI:
    """FastAPI Application Factory."""
    application = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description="MarketTwin - Demand-Driven Marketplace API. Groups buyer demands and aggregates competitive merchant offers.",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Configure CORS Middleware
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def trace_ai_conversation(request, call_next):
        if not request.url.path.endswith("/ai/conversation"):
            return await call_next(request)

        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_request_id
            if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", supplied_request_id)
            else uuid.uuid4().hex
        )
        token = request_id_context.set(request_id)
        started_at = time.perf_counter()
        logger.info("AI HTTP request started request_id=%s path=%s", request_id, request.url.path)
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = round((time.perf_counter() - started_at) * 1000)
            trace = getattr(request.state, "ai_trace", {})
            logger.error(
                "AI HTTP request failed provider=%s model=%s request_id=%s "
                "input_characters=%s elapsed_ms=%d",
                trace.get("provider", "unknown"),
                trace.get("model", "unknown"),
                request_id,
                trace.get("input_characters", "unknown"),
                elapsed_ms,
            )
            raise
        finally:
            request_id_context.reset(token)

        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        response.headers["X-Request-ID"] = request_id
        trace = getattr(request.state, "ai_trace", {})
        logger.info(
            "AI HTTP response returned provider=%s model=%s request_id=%s "
            "input_characters=%s status_code=%d elapsed_ms=%d",
            trace.get("provider", "unknown"),
            trace.get("model", "unknown"),
            request_id,
            trace.get("input_characters", "unknown"),
            response.status_code,
            elapsed_ms,
        )
        return response

    # Register Exception Handlers
    application.add_exception_handler(MarketTwinException, markettwin_exception_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(Exception, generic_exception_handler)

    # Register API Routers (both /api/v1 and /api for direct endpoint compatibility)
    application.include_router(api_router, prefix=settings.API_V1_STR)
    if settings.API_V1_STR != "/api":
        application.include_router(api_router, prefix="/api")

    # Direct Health Check Alias at root /health
    @application.get("/health", include_in_schema=True, tags=["Health & System Diagnostics"])
    async def root_health():
        from app.api.v1.endpoints.health import get_health
        return await get_health()

    @application.get("/health/ping", include_in_schema=True, tags=["Health & System Diagnostics"])
    async def root_health_ping():
        from app.api.v1.endpoints.health import ping
        return await ping()

    # Root welcome / meta endpoint
    @application.get("/", include_in_schema=False)
    async def root():
        return {
            "name": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "environment": settings.ENVIRONMENT,
            "documentation": "/docs",
            "health_check": "/api/v1/health",
        }

    return application


app = create_application()
