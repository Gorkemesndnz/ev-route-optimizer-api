"""
Faz 4: PhysicsConsumptionEngine — Fizik Tabanlı Tüketim Motoru

Mevcut MainCalculator.calculate_route_consumption() fonksiyonunu
ConsumptionEngine ABC'si arkasına saran ince bir proxy katmanı.
main_calculator.py hiçbir şekilde değiştirilmez.
"""

from typing import List, Optional
from app.consumption_engine.base import ConsumptionEngine
from app.consumption_engine.main_calculator import calculate_route_consumption
from app.soc_simulator import SegmentWithConsumption
from app.utils.logger import get_logger

logger = get_logger("PhysicsEngine")


class PhysicsConsumptionEngine(ConsumptionEngine):
    """
    Kural tabanlı fizik motoru.
    MainCalculator'ın tüm hesaplama katmanlarını (elevation, load, weather)
    doğrudan kullanır.
    """
    
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
        child_count: int = 0,
        driving_style_multiplier: float = 1.0,
        hvac_on: bool = True,
        max_speed_kmh: Optional[int] = None,
        consumption_override_wh_km: Optional[float] = None
    ) -> List[SegmentWithConsumption]:
        """MainCalculator proxy — aynı imza, aynı dönüş tipi."""
        
        logger.debug("PhysicsConsumptionEngine.estimate() called")
        
        return calculate_route_consumption(
            vehicle=vehicle,
            segments=segments,
            weather_checkpoints=weather_checkpoints,
            temperature_celsius=temperature_celsius,
            wind_speed_mps=wind_speed_mps,
            weather_condition=weather_condition,
            extra_load_kg=extra_load_kg,
            passenger_count=passenger_count,
            child_count=child_count,
            driving_style_multiplier=driving_style_multiplier,
            hvac_on=hvac_on,
            max_speed_kmh=max_speed_kmh,
            consumption_override_wh_km=consumption_override_wh_km
        )
