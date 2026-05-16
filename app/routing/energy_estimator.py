"""Deterministic energy estimation over canonical route segment features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol

from app.consumption_engine import get_engine
from app.routing.segment_features import SegmentFeatureSet
from app.soc_simulator import SegmentWithConsumption


@dataclass(frozen=True)
class EnergyEstimateRequest:
    segment_features: SegmentFeatureSet
    vehicle: object
    weather_checkpoints: Optional[list] = None
    temperature_celsius: float = 20.0
    wind_speed_mps: float = 0.0
    weather_condition: str = "clear"
    extra_load_kg: float = 0.0
    passenger_count: int = 1
    child_count: int = 0
    driving_style_multiplier: float = 1.0
    hvac_on: bool = True
    max_speed_kmh: Optional[int] = None
    consumption_override_wh_km: Optional[float] = None


@dataclass(frozen=True)
class EnergyEstimate:
    route_id: str
    segments: List[SegmentWithConsumption]
    total_consumption_kwh: float


class EnergyEstimator(Protocol):
    def estimate(self, request: EnergyEstimateRequest) -> EnergyEstimate:
        ...


class ConsumptionEngineEnergyEstimator:
    """Adapter that keeps the existing consumption engine behind Sprint 4 API."""

    def __init__(self, engine=None):
        self._engine = engine or get_engine()

    def estimate(self, request: EnergyEstimateRequest) -> EnergyEstimate:
        segments_with_consumption = self._engine.estimate(
            vehicle=request.vehicle,
            segments=request.segment_features.segments,
            weather_checkpoints=request.weather_checkpoints,
            temperature_celsius=request.temperature_celsius,
            wind_speed_mps=request.wind_speed_mps,
            weather_condition=request.weather_condition,
            extra_load_kg=request.extra_load_kg,
            passenger_count=request.passenger_count,
            child_count=request.child_count,
            driving_style_multiplier=request.driving_style_multiplier,
            hvac_on=request.hvac_on,
            max_speed_kmh=request.max_speed_kmh,
            consumption_override_wh_km=request.consumption_override_wh_km,
        )
        return EnergyEstimate(
            route_id=request.segment_features.canonical_route.provider_route_id,
            segments=segments_with_consumption,
            total_consumption_kwh=sum(s.consumption_kwh for s in segments_with_consumption),
        )
