"""
Akıllı EV Rota Asistanı API - v2.0
===================================
V2.0 Core: Stateless Computation Engine
Endpoints are now separated using APIRouter for maintainability.
"""
import secrets

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.utils.logger import get_logger
from app.utils.config_manager import config
from app.services.base_service import close_global_client, ExternalAPIError
from app.core.api_response import ApiResponse

# Import Routers
from app.routers import optimize, stations, dev, trips

logger = get_logger("main_api")
INTERNAL_AUTH_HEADER = "X-IYONTREE-Internal-Secret"

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle"""
    logger.info(
        "EV Route Optimizer API starting up",
        version="v2.0",
        environment=config.get_environment(),
        debug_mode=config.log_level() == "DEBUG"
    )
    
    logger.info(
        "Vehicle catalog mode",
        mode="gateway_vehicle_spec",
        source=".NET MSSQL payload",
    )
    
    yield
    
    logger.info("EV Route Optimizer API shutting down")
    await close_global_client()


app = FastAPI(
    title="Akıllı EV Rota Asistanı API",
    description="Multi-stop EV route optimization with charging station planning. V2.0 Stateless Compute Engine.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS Configuration
_cors_origins = (
    [
        "http://localhost:5173",  # Vite dev
        "http://localhost:3000",  # CRA dev
        "http://localhost:5146",  # .NET backend (dev swagger/postman direct test)
    ]
    if config.is_debug()
    else [
        "https://ev-route-optimizer.com",
        "https://www.ev-route-optimizer.com",
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", INTERNAL_AUTH_HEADER],
)


LEGACY_INTERNAL_AUTH_PATHS = {
    "/optimize_route",
    "/station_feedback",
    "/switch_station",
}


def _is_trip_outcome_path(path: str, prefix: str) -> bool:
    return path.startswith(prefix) and path.endswith("/outcome")


def _requires_internal_auth(path: str) -> bool:
    if path.startswith("/internal/"):
        return True
    if path in LEGACY_INTERNAL_AUTH_PATHS:
        return True
    return _is_trip_outcome_path(path, "/trips/")


@app.middleware("http")
async def internal_service_auth_middleware(request: Request, call_next):
    internal_secret = config.get_internal_auth_secret().strip()
    if not internal_secret or request.method == "OPTIONS" or not _requires_internal_auth(request.url.path):
        return await call_next(request)

    provided_secret = request.headers.get(INTERNAL_AUTH_HEADER, "")
    if not secrets.compare_digest(provided_secret, internal_secret):
        logger.warning(
            "Internal FastAPI request rejected",
            path=str(request.url.path),
            method=request.method,
        )
        return JSONResponse(
            status_code=401,
            content=ApiResponse.fail("Yetkisiz internal servis istegi", code="INTERNAL_AUTH_REQUIRED").model_dump(),
        )

    return await call_next(request)

# Global Exception Handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # field_validator raised exception nesnesini errors içine koyabilir; JSON-safe hale getir.
    safe_errors = jsonable_encoder(
        exc.errors(),
        custom_encoder={Exception: str, ValueError: str},
    )
    logger.warning("Request validation failed", path=str(request.url.path), errors=safe_errors)
    return JSONResponse(
        status_code=422,
        content=ApiResponse.fail(
            "Geçersiz istek verisi",
            errors=safe_errors,
            code="VALIDATION_ERROR",
        ).model_dump(mode="json")
    )


@app.exception_handler(ExternalAPIError)
async def external_api_exception_handler(request: Request, exc: ExternalAPIError):
    logger.error("Unhandled external API error", path=str(request.url.path), source=exc.source, status_code=exc.status_code, detail=str(exc))
    return JSONResponse(
        status_code=502,
        content=ApiResponse.fail(
            f"Dış API hatası ({exc.source})",
            code="EXTERNAL_API_ERROR",
        ).model_dump()
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.critical("Unhandled server error", path=str(request.url.path), error=str(exc), exc_type=type(exc).__name__)
    detail = str(exc) if config.is_debug() else "İç sunucu hatası"
    return JSONResponse(
        status_code=500,
        content=ApiResponse.fail(detail, code="INTERNAL_ERROR").model_dump()
    )


# Include Routers
app.include_router(optimize.router)
app.include_router(stations.router)
app.include_router(dev.router)
app.include_router(trips.router)
