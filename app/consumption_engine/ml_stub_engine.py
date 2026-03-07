"""
Faz 4: MLConsumptionEngine — ML Stub (Proxy)

Şimdilik PhysicsConsumptionEngine'e delege eder.
ML modeli hazır olduğunda bu sınıf gerçek inference ile değiştirilecek.
"""

from typing import List, Optional
from app.consumption_engine.base import ConsumptionEngine
from app.consumption_engine.physics_engine import PhysicsConsumptionEngine
from app.soc_simulator import SegmentWithConsumption
from app.utils.logger import get_logger

logger = get_logger("MLEngine")


class MLConsumptionEngine(ConsumptionEngine):
    """
    ML tabanlı tüketim motoru — STUB.
    
    Şimdilik PhysicsConsumptionEngine'e proxy olarak çalışır.
    Gelecekte XGBoost/LightGBM modeli burada inference yapacak.
    """
    
    def __init__(self):
        self._physics_fallback = PhysicsConsumptionEngine()
    
    def estimate(
        self,
        vehicle,
        segments,
        weather_checkpoints: Optional[list] = None,
        temperature_celsius: float = 20.0,
        wind_speed_mps: float = 0.0,
        weather_condition: str = "clear",
        extra_load_kg: float = 0.0,
        passenger_count: int = 1,
        child_count: int = 0
    ) -> List[SegmentWithConsumption]:
        """ML stub — fizik motoruna fallback yapar."""
        
        logger.info("ML engine stub active — falling back to physics engine")
        
        return self._physics_fallback.estimate(
            vehicle=vehicle,
            segments=segments,
            weather_checkpoints=weather_checkpoints,
            temperature_celsius=temperature_celsius,
            wind_speed_mps=wind_speed_mps,
            weather_condition=weather_condition,
            extra_load_kg=extra_load_kg,
            passenger_count=passenger_count,
            child_count=child_count
        )
