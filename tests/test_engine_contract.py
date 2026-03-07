"""
Faz 4: ConsumptionEngine Kontrat Testi

Doğrulamalar:
1. PhysicsConsumptionEngine ve MLConsumptionEngine, ConsumptionEngine ABC'sinin alt sınıfıdır
2. Her iki motor da List[SegmentWithConsumption] döndürür
3. Her iki motor da aynı giriş için aynı sayıda segment döndürür
4. get_engine() factory doğru motoru döndürür
"""

import sys
sys.path.insert(0, '.')

from app.consumption_engine.base import ConsumptionEngine
from app.consumption_engine.physics_engine import PhysicsConsumptionEngine
from app.consumption_engine.ml_stub_engine import MLConsumptionEngine
from app.consumption_engine import get_engine
from app.soc_simulator import SegmentWithConsumption
from app.models import WeatherInfo, WeatherCondition, GeoPoint


def test_abc_subclass():
    """Her iki motor da ABC'nin alt sınıfıdır."""
    physics = PhysicsConsumptionEngine()
    ml = MLConsumptionEngine()
    
    assert isinstance(physics, ConsumptionEngine), "PhysicsEngine, ConsumptionEngine değil!"
    assert isinstance(ml, ConsumptionEngine), "MLEngine, ConsumptionEngine değil!"
    print("✅ TEST 1 PASSED: Her iki motor da ConsumptionEngine ABC alt sınıfı")


def test_factory():
    """get_engine() doğru motoru döndürür."""
    from app.constants import USE_ML_ENGINE
    engine = get_engine()
    
    if USE_ML_ENGINE:
        assert isinstance(engine, MLConsumptionEngine), "Flag True ama MLEngine dönmedi!"
        print(f"✅ TEST 2 PASSED: Factory → MLConsumptionEngine (USE_ML_ENGINE={USE_ML_ENGINE})")
    else:
        assert isinstance(engine, PhysicsConsumptionEngine), "Flag False ama PhysicsEngine dönmedi!"
        print(f"✅ TEST 2 PASSED: Factory → PhysicsConsumptionEngine (USE_ML_ENGINE={USE_ML_ENGINE})")


def test_return_type_and_count():
    """Her iki motor da aynı tip ve aynı sayıda segment döndürür."""
    from dataclasses import dataclass
    from app.infrastructure.vehicle_catalog import get_vehicle_model
    
    vehicle = get_vehicle_model('mg_mg4_electric_51_kwh_2022')
    
    # Basit mock segment
    @dataclass
    class MockSegment:
        index: int = 0
        distance_km: float = 50.0
        elevation_gain_m: float = 100.0
        elevation_loss_m: float = 50.0
        cumulative_distance_km: float = 50.0
        start_point: object = None
        end_point: object = None
        duration_minutes: float = 30.0
        weather_context: object = None
    
    segments = [MockSegment(index=0), MockSegment(index=1, cumulative_distance_km=100.0)]
    
    physics = PhysicsConsumptionEngine()
    ml = MLConsumptionEngine()
    
    physics_result = physics.estimate(vehicle=vehicle, segments=segments)
    ml_result = ml.estimate(vehicle=vehicle, segments=segments)
    
    # Tip kontrolü
    assert isinstance(physics_result, list), "PhysicsEngine list döndürmedi!"
    assert isinstance(ml_result, list), "MLEngine list döndürmedi!"
    
    for item in physics_result:
        assert isinstance(item, SegmentWithConsumption), f"PhysicsEngine yanlış tip: {type(item)}"
    for item in ml_result:
        assert isinstance(item, SegmentWithConsumption), f"MLEngine yanlış tip: {type(item)}"
    
    print(f"✅ TEST 3a PASSED: Her iki motor da List[SegmentWithConsumption] döndürüyor")
    
    # Segment sayısı kontrolü (kullanıcı isteği)
    assert len(physics_result) == len(ml_result), (
        f"Segment sayısı uyuşmuyor! Physics={len(physics_result)}, ML={len(ml_result)}"
    )
    assert len(physics_result) == len(segments), (
        f"Çıktı sayısı giriş ile eşleşmiyor! Giriş={len(segments)}, Çıktı={len(physics_result)}"
    )
    
    print(f"✅ TEST 3b PASSED: Segment sayıları eşleşiyor (giriş={len(segments)}, çıktı={len(physics_result)})")


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 Faz 4: ConsumptionEngine Kontrat Testleri")
    print("=" * 60)
    
    test_abc_subclass()
    test_factory()
    test_return_type_and_count()
    
    print("\n" + "=" * 60)
    print("✅ TÜM KONTRAT TESTLERİ BAŞARILI")
    print("=" * 60)
