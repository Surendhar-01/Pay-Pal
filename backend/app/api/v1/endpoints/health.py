from datetime import datetime, timezone
from fastapi import APIRouter, status
from app.core.config import get_settings
from app.core.supabase import check_supabase_health
from app.schemas.health import HealthCheckResponse, ServiceStatus
from app.services.ai.health import check_ai_health

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Comprehensive System Health Check",
    description="Returns detailed application status, version, environment, and connected service health.",
)
async def get_health() -> HealthCheckResponse:
    settings = get_settings()
    db_status, db_msg = check_supabase_health()
    ai_status = await check_ai_health(settings)

    services = {
        "api": ServiceStatus(
            status="operational",
            message="Backend API is responding.",
        ),
        "database": ServiceStatus(
            status=db_status,
            message=db_msg
        ),
        "ai": ai_status,
    }

    # Determine overall status. The endpoint stays healthy (HTTP 200) so the
    # process is not recycled; dependency problems surface as "degraded".
    overall_status = "ok"
    if db_status == "degraded" or ai_status.status != "operational":
        overall_status = "degraded"

    return HealthCheckResponse(
        status=overall_status,
        timestamp=datetime.now(timezone.utc).isoformat(),
        service=settings.PROJECT_NAME,
        version=settings.VERSION,
        environment=settings.ENVIRONMENT,
        services=services
    )


@router.get(
    "/health/ping",
    status_code=status.HTTP_200_OK,
    summary="Lightweight Ping Probe",
    description="Minimal latency endpoint for container liveness / readiness probes.",
)
async def ping():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
