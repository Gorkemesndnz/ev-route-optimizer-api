"""
Trips Router — Outcome Backfill Endpoint
==========================================

Faz 2 Pareto karar sisteminin tamamlayıcısı.
Kullanıcı yolculuğunu bitirdiğinde gerçek değerleri raporlar.
Bu veri Faz 3 ML eğitimi için kullanılır.

Endpoint:
- POST /trips/{trip_id}/outcome — gerçek yolculuk metriklerini kaydet
- GET  /trips/recent — son N günün kararlarını outcome'larıyla birlikte döner (dev/admin)
"""

from datetime import datetime, timezone
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException, Path, Query

from app.models.route_models import TripOutcomeRequest, TripOutcomeResponse
from app.optimization.decision_logger import (
    OutcomeRecord,
    get_decision_logger,
)
from app.utils.logger import get_logger
from app.core.api_response import ApiResponse
from app.core.config import config

router = APIRouter(tags=["Trips & Outcome Backfill"])
logger = get_logger("trips_router")


@router.post(
    "/trips/{trip_id}/outcome",
    response_model=ApiResponse[TripOutcomeResponse],
    summary="Yolculuk gerçekleşen değerlerini geri bildir",
    description=(
        "Kullanıcı yolculuğu bitirdiğinde gerçek arrival_soc, total_time, cost vb. "
        "değerleri raporlar. Faz 3 ML eğitimi için kullanılır. Tüm alanlar opsiyoneldir."
    ),
)
async def submit_trip_outcome(
    payload: TripOutcomeRequest,
    trip_id: str = Path(
        ...,
        min_length=4,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="plan_route response'undan gelen trip_id",
    ),
) -> ApiResponse[TripOutcomeResponse]:
    """Outcome kaydını JSONL'a append eder."""
    record = OutcomeRecord(
        trip_id=trip_id,
        timestamp_iso=datetime.now(timezone.utc).isoformat(),
        actual_arrival_soc=payload.actual_arrival_soc,
        actual_total_time_min=payload.actual_total_time_min,
        actual_charge_time_min=payload.actual_charge_time_min,
        actual_total_cost_tl=payload.actual_total_cost_tl,
        actual_num_charges=payload.actual_num_charges,
        user_satisfaction=payload.user_satisfaction,
        notes=payload.notes,
    )

    success = get_decision_logger().write_outcome(record)
    if not success:
        # Logging soft-fail; ana akış kırılmamalı, ama kullanıcıya hata bildir
        logger.warning(f"Outcome logging failed; soft-success response returned: trip_id={trip_id}")
        return ApiResponse.ok(
            TripOutcomeResponse(
                success=True,
                trip_id=trip_id,
                message="Outcome alindi; kalici log kaydi su anda yazilamadi",
                persisted=False,
                warning="outcome_log_write_failed",
            )
        )

    return ApiResponse.ok(
        TripOutcomeResponse(
            success=True,
            trip_id=trip_id,
            message="Outcome başarıyla kaydedildi",
        )
    )


@router.get(
    "/trips/recent",
    response_model=ApiResponse[List[Dict[str, Any]]],
    summary="Son N günün kararları + outcome'ları (dev/admin)",
    description=(
        "Son `days_back` günün decision + outcome kayıtlarını trip_id ile JOIN edip döner. "
        "ML eğitim verisi hazırlamak veya operasyonel analiz için."
    ),
)
async def list_recent_trips(
    days_back: int = Query(7, ge=1, le=90, description="Kaç gün geriye bakılsın"),
) -> ApiResponse[List[Dict[str, Any]]]:
    if config.is_production():
        raise HTTPException(status_code=404, detail="Not found")
    merged = get_decision_logger().load_decisions_with_outcomes(days_back=days_back)
    logger.info(f"Loaded {len(merged)} decisions (days_back={days_back})")
    return ApiResponse.ok(merged)
