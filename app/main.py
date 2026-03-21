"""
Akıllı EV Rota Asistanı API - v2.0
===================================
V2.0 Core: Stateless Computation Engine
Endpoints are now separated using APIRouter for maintainability.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.utils.logger import get_logger
from app.utils.config_manager import config
from app.infrastructure.vehicle_catalog import get_available_vehicle_ids
from app.services.base_service import close_global_client

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
_cors_origins = ["*"] if config.is_debug() else [
    "https://ev-route-optimizer.com",
    "https://www.ev-route-optimizer.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"] if not config.is_debug() else ["*"],
    allow_headers=["Content-Type", "Authorization"] if not config.is_debug() else ["*"],
)

# Include Routers
app.include_router(optimize.router)
app.include_router(stations.router)
app.include_router(dev.router)
