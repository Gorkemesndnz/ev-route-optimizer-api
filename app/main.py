"""
Akıllı EV Rota Asistanı API - v2.0
===================================
V2.0 Core: Stateless Computation Engine
Endpoints are now separated using APIRouter for maintainability.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.utils.logger import get_logger
from app.utils.config_manager import config
from app.infrastructure.vehicle_catalog import get_available_vehicle_ids
from app.services.base_service import close_global_client, ExternalAPIError
from app.core.api_response import ApiResponse

# Import Routers
from app.routers import optimize, stations, dev

logger = get_logger("main_api")

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
        "Available vehicle models",
        vehicle_count=len(get_available_vehicle_ids()),
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
    ["http://localhost:5173", "http://localhost:3000"]
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
    allow_headers=["Content-Type", "Authorization"],
)

# Global Exception Handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning("Request validation failed", path=str(request.url.path), errors=exc.errors())
    return JSONResponse(
        status_code=422,
        content=ApiResponse.fail("Geçersiz istek verisi", exc.errors()).model_dump()
    )


@app.exception_handler(ExternalAPIError)
async def external_api_exception_handler(request: Request, exc: ExternalAPIError):
    logger.error("Unhandled external API error", path=str(request.url.path), source=exc.source, status_code=exc.status_code, detail=str(exc))
    return JSONResponse(
        status_code=502,
        content=ApiResponse.fail(f"Dış API hatası ({exc.source})").model_dump()
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.critical("Unhandled server error", path=str(request.url.path), error=str(exc), exc_type=type(exc).__name__)
    detail = str(exc) if config.is_debug() else "İç sunucu hatası"
    return JSONResponse(
        status_code=500,
        content=ApiResponse.fail(detail).model_dump()
    )


# Include Routers
app.include_router(optimize.router)
app.include_router(stations.router)
app.include_router(dev.router)
