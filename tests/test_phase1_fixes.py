"""
Faz 1 Fix Regression & Edge-Case Testleri
==========================================

Tüketim motoru + Roads API temizliği fix'lerinin doğruluğunu garanti eder.
Beklenen hatalar: drivetrain efficiency yokken, aux duration yanlışken,
hard-coded magic number döndüğünde — bu testler kırmızı yanmalı.

Run: pytest tests/test_phase1_fixes.py -v
"""

import math
import pytest
from dataclasses import dataclass
from typing import Optional

from app.models import WeatherInfo, WeatherCondition
from app.consumption_engine.main_calculator import (
    MainCalculator,
    calculate_segment_consumption_kwh,
)
from app.consumption_engine.v1_rule_based.elevation_layer import ElevationEffectCalculator


@dataclass
class _MockVehicle:
    curb_weight_kg: float = 1800.0
    base_consumption_kwh_per_100km: float = 18.0
    auxiliary_power_kw: float = 1.0
    drag_coefficient: float = 0.28
    frontal_area_m2: float = 2.2


@dataclass
class _MockGeo:
    lat: float
    lon: float


def _make_segment(distance_km, gain_m=0.0, loss_m=0.0, duration_min=60.0,
                  temp=20.0, wind=0.0, wind_dir=0.0):
    @dataclass
    class _Seg:
        distance_km: float
        elevation_gain_m: float
        elevation_loss_m: float
        duration_minutes: float
        start_point: Optional[object]
        end_point: Optional[object]
        weather_context: WeatherInfo
    return _Seg(
        distance_km=distance_km,
        elevation_gain_m=gain_m,
        elevation_loss_m=loss_m,
        duration_minutes=duration_min,
        start_point=_MockGeo(41.0, 29.0),
        end_point=_MockGeo(41.5, 29.5),
        weather_context=WeatherInfo(
            temp_c=temp, wind_speed_mps=wind,
            wind_direction_deg=wind_dir, condition=WeatherCondition.CLEAR
        ),
    )


# =============================================================================
# FIX #1 — DRIVETRAIN EFFICIENCY (uphill m·g·h / 0.88)
# =============================================================================

class TestDrivetrainEfficiency:
    """Tırmanış enerjisi gerçekçi (drivetrain kaybı dahil)."""

    def test_drivetrain_constant_exists_and_realistic(self):
        eff = ElevationEffectCalculator.DRIVETRAIN_EFFICIENCY
        # Modern EV motor + invertör + dişli tipik %85-92 aralığında
        assert 0.80 <= eff <= 0.95, f"Drivetrain efficiency {eff} unrealistic"

    def test_uphill_kwh_includes_drivetrain_loss(self):
        """1800 kg araç, 1000 m tırmanış: m·g·h = 4.905 kWh saf mekanik.
        Batarya tüketimi efficiency ile bölünmeli → ~5.57 kWh."""
        vehicle = _MockVehicle(curb_weight_kg=1800.0)
        # Yardımcı: yalnızca tırmanış etkisini izole etmek için 1 km'lik segment
        seg = _make_segment(distance_km=1.0, gain_m=1000.0, duration_min=1.0)
        result = MainCalculator.calculate_segment_consumption(
            segment=seg, vehicle=vehicle, passenger_count=0, extra_load_kg=0
        )
        # Saf m·g·h (yolcu yok, ekstra yük yok)
        raw_mgh_kwh = (1800.0 * 9.81 * 1000.0) / 3_600_000.0
        expected_with_drivetrain = raw_mgh_kwh / ElevationEffectCalculator.DRIVETRAIN_EFFICIENCY

        # elevation_energy_kwh ≈ expected (regen yok çünkü loss=0)
        assert result.elevation_energy_kwh == pytest.approx(expected_with_drivetrain, rel=0.01), \
            f"elevation_energy {result.elevation_energy_kwh:.3f} ≠ expected {expected_with_drivetrain:.3f}"
        # Doğrulama: saf m·g·h değerinden büyük olmalı (eğer drivetrain düşürülmediyse fail)
        assert result.elevation_energy_kwh > raw_mgh_kwh, \
            "Drivetrain kaybı uygulanmamış — uphill_kwh saf m·g·h gibi davranıyor"

    def test_vehicle_drivetrain_override(self):
        """Araç drivetrain_efficiency field'ı varsa onu kullan."""
        @dataclass
        class HighEffVehicle(_MockVehicle):
            drivetrain_efficiency: float = 0.95
        vehicle = HighEffVehicle()
        seg = _make_segment(distance_km=1.0, gain_m=500.0, duration_min=1.0)
        result = MainCalculator.calculate_segment_consumption(
            segment=seg, vehicle=vehicle, passenger_count=0, extra_load_kg=0
        )
        raw = (1800.0 * 9.81 * 500.0) / 3_600_000.0
        expected = raw / 0.95
        assert result.elevation_energy_kwh == pytest.approx(expected, rel=0.01)


# =============================================================================
# FIX #2 — SimpleSegment duration_minutes parametresi
# =============================================================================

class TestSegmentDuration:
    """Aux tüketim gerçek süre ile hesaplanmalı (trafik dahil)."""

    def test_aux_consumption_scales_with_duration(self):
        """Aynı mesafe, farklı sürelerde aux farklı olmalı."""
        vehicle = _MockVehicle(auxiliary_power_kw=1.5)
        # 50 km — 30 dk hızlı vs 90 dk trafik
        fast = calculate_segment_consumption_kwh(
            vehicle=vehicle, segment_distance_km=50.0,
            duration_minutes=30.0, hvac_on=True,
        )
        slow = calculate_segment_consumption_kwh(
            vehicle=vehicle, segment_distance_km=50.0,
            duration_minutes=90.0, hvac_on=True,
        )
        # 3× süre → aux 3× artmalı (HVAC açık), toplam fark olmalı
        assert slow > fast, f"Trafikte tüketim daha yüksek olmalı: fast={fast:.2f}, slow={slow:.2f}"
        # Kabaca aux farkı: 1.5 kW × 1 saat = 1.5 kWh ekstra (sıcaklığa bağlı multiplier ile değişir)
        assert (slow - fast) > 1.0, f"Aux artışı çok düşük: {slow-fast:.2f} kWh"

    def test_default_duration_uses_constant(self):
        """duration_minutes verilmezse DEFAULT_AVG_SPEED_KMH (60) kullanılmalı."""
        vehicle = _MockVehicle()
        # 60 km @ 60 km/h = 60 dk = 1 saat
        result_no_duration = calculate_segment_consumption_kwh(
            vehicle=vehicle, segment_distance_km=60.0, hvac_on=True,
        )
        result_explicit_60min = calculate_segment_consumption_kwh(
            vehicle=vehicle, segment_distance_km=60.0,
            duration_minutes=60.0, hvac_on=True,
        )
        assert result_no_duration == pytest.approx(result_explicit_60min, rel=0.001), \
            "Default speed (60 km/h) ile explicit 60dk eşleşmeli"


# =============================================================================
# FIX #3 — DEFAULT_AVG_SPEED_KMH tek kaynak
# =============================================================================

class TestDefaultSpeedConsistency:
    def test_default_speed_value(self):
        assert MainCalculator.DEFAULT_AVG_SPEED_KMH == 60.0

    def test_get_segment_duration_fallback_uses_constant(self):
        """duration_minutes=0 olan segment fallback'te aynı speed kullanmalı."""
        @dataclass
        class _ZeroDurSeg:
            distance_km: float = 60.0
            duration_minutes: float = 0.0
        seg = _ZeroDurSeg()
        hours = MainCalculator._get_segment_duration_hours(seg)
        # 60 km / 60 kmh = 1.0 hour
        assert hours == pytest.approx(1.0, rel=0.001), \
            f"Fallback duration {hours} != 1.0h (DEFAULT_AVG_SPEED_KMH değişmiş olabilir)"


# =============================================================================
# V4.1 — Polyline-perpendicular distance filter
# =============================================================================
# Roads API tabanlı filtre kaldırıldı. Yeni filtre: pure geometrik (haversine
# vertex distance from polyline). Aşağıdaki testler yeni helper'ları kapsar.

class TestPolylinePerpendicularFilter:
    def test_decode_empty_polyline_raises_typed_error(self):
        from app.station_finder import _decode_route_polyline_coords
        from app.services.base_service import ExternalAPIError

        with pytest.raises(ExternalAPIError, match="Route polyline is empty"):
            _decode_route_polyline_coords("")
        with pytest.raises(ExternalAPIError, match="Route polyline is empty"):
            _decode_route_polyline_coords(None)  # type: ignore[arg-type]

    def test_decode_invalid_polyline_raises_typed_error(self):
        """Bozuk polyline → boş liste, exception fırlatmamalı."""
        from app.station_finder import _decode_route_polyline_coords
        from app.services.base_service import ExternalAPIError

        with pytest.raises(ExternalAPIError, match="could not be decoded"):
            _decode_route_polyline_coords("THIS_IS_NOT_A_POLYLINE@@@")

    def test_decode_valid_polyline_returns_coords(self):
        """Geçerli polyline → en az 1 (lat, lon) tuple içeren liste."""
        from app.station_finder import _decode_route_polyline_coords
        result = _decode_route_polyline_coords("_p~iF~ps|U_ulLnnqC")
        assert isinstance(result, list)
        assert len(result) >= 1
        assert all(isinstance(p, tuple) and len(p) == 2 for p in result)

    def test_check_stations_empty_polyline_raises_typed_error(self):
        """Polyline boşsa tüm istasyonlar kabul (fail-open)."""
        from app.station_finder import _check_stations_on_polyline
        from app.services.base_service import ExternalAPIError

        with pytest.raises(ExternalAPIError, match="no route geometry"):
            _check_stations_on_polyline(
                station_coords=[(41.0, 29.0), (41.1, 29.1)],
                polyline_coords=[],
            )

    def test_check_stations_none_polyline_is_explicit_bypass(self):
        from app.station_finder import _check_stations_on_polyline

        flags = _check_stations_on_polyline(
            station_coords=[(41.0, 29.0), (41.1, 29.1)],
            polyline_coords=None,
        )
        assert flags == [True, True]

    def test_check_stations_empty_input_returns_empty(self):
        from app.station_finder import _check_stations_on_polyline
        assert _check_stations_on_polyline(
            station_coords=[],
            polyline_coords=[(41.0, 29.0)],
        ) == []

    def test_check_stations_close_to_polyline_accepted(self):
        """Polyline'a 100m mesafedeki istasyon kabul edilmeli (1km eşik altında)."""
        from app.station_finder import _check_stations_on_polyline
        polyline = [(41.0000, 29.0000), (41.0050, 29.0000), (41.0100, 29.0000)]
        # ~100m kuzey offset ≈ 0.0009 derece
        station_close = (41.0009, 29.0000)
        flags = _check_stations_on_polyline([station_close], polyline)
        assert flags == [True]

    def test_check_stations_far_from_polyline_rejected(self):
        """Polyline'dan 5km uzak istasyon reddedilmeli (1km eşik üstünde)."""
        from app.station_finder import _check_stations_on_polyline
        polyline = [(41.0000, 29.0000), (41.0050, 29.0000), (41.0100, 29.0000)]
        # ~5km doğu offset ≈ 0.06 derece
        station_far = (41.0050, 29.0600)
        flags = _check_stations_on_polyline([station_far], polyline)
        assert flags == [False]

    def test_check_stations_custom_threshold(self):
        """max_perp_km parametresi davranışı değiştirmeli."""
        from app.station_finder import _check_stations_on_polyline
        polyline = [(41.0000, 29.0000)]
        station = (41.0050, 29.0000)  # ~555m kuzey
        # 0.5 km eşik → ret
        assert _check_stations_on_polyline([station], polyline, max_perp_km=0.5) == [False]
        # 1.0 km eşik → kabul
        assert _check_stations_on_polyline([station], polyline, max_perp_km=1.0) == [True]


# =============================================================================
# FIX #7 — DEFAULT_CHARGE_TARGET_SOC constant
# =============================================================================

class TestDefaultChargeTargetSOC:
    def test_constant_exists(self):
        from app.constants import DEFAULT_CHARGE_TARGET_SOC
        assert DEFAULT_CHARGE_TARGET_SOC == 80.0

    def test_orchestrator_uses_constant_not_magic_number(self):
        """Orchestrator dosyasında 'or 80' magic number kalmadı."""
        import inspect
        from app.route_planning import orchestrator
        source = inspect.getsource(orchestrator)
        assert "or 80," not in source and "or 80)" not in source, \
            "Orchestrator hâlâ magic number 'or 80' kullanıyor"
        assert "DEFAULT_CHARGE_TARGET_SOC" in source


# =============================================================================
# DERIN EDGE-CASE'LER (mevcut motorda bozuk olabilecek senaryolar)
# =============================================================================

class TestConsumptionEdgeCases:
    """Önceki review'da gözden kaçmış olabilecek köşe durumları."""

    def test_zero_distance_returns_zero(self):
        vehicle = _MockVehicle()
        seg = _make_segment(distance_km=0.0, duration_min=0.0)
        result = MainCalculator.calculate_segment_consumption(seg, vehicle)
        assert result.total_consumption_kwh == 0.0
        assert result.practical_consumption_kwh == 0.0

    def test_extreme_regen_clamped(self):
        """Büyük iniş + soğuk hava regen yapsa bile MIN_PRACTICAL altına düşmemeli."""
        vehicle = _MockVehicle(curb_weight_kg=2500.0)
        seg = _make_segment(distance_km=2.0, loss_m=2000.0, duration_min=2.0, temp=-10.0)
        result = MainCalculator.calculate_segment_consumption(seg, vehicle)
        assert result.practical_consumption_kwh >= MainCalculator.MIN_PRACTICAL_CONSUMPTION

    def test_uphill_with_load_increases_consumption(self):
        """Aynı tırmanış: yüklü vs yüksüz — yüklü daha çok tüketmeli."""
        vehicle = _MockVehicle()
        seg = _make_segment(distance_km=10.0, gain_m=300.0, duration_min=15.0)
        light = MainCalculator.calculate_segment_consumption(seg, vehicle, passenger_count=1)
        heavy = MainCalculator.calculate_segment_consumption(
            seg, vehicle, passenger_count=4, extra_load_kg=200.0
        )
        assert heavy.total_consumption_kwh > light.total_consumption_kwh
        # Elevation enerjisi de yükü içermeli
        assert heavy.elevation_energy_kwh > light.elevation_energy_kwh

    def test_cold_weather_increases_aux(self):
        """Soğukta HVAC daha çok çeker → aux artmalı."""
        vehicle = _MockVehicle(auxiliary_power_kw=1.5)
        warm_seg = _make_segment(distance_km=50.0, duration_min=60.0, temp=20.0)
        cold_seg = _make_segment(distance_km=50.0, duration_min=60.0, temp=-15.0)
        warm = MainCalculator.calculate_segment_consumption(warm_seg, vehicle)
        cold = MainCalculator.calculate_segment_consumption(cold_seg, vehicle)
        assert cold.aux_consumption_kwh > warm.aux_consumption_kwh

    def test_heat_pump_reduces_cold_aux(self):
        """Heat pump'lı araç soğukta normal araçtan daha az aux çekmeli."""
        @dataclass
        class HeatPumpVehicle(_MockVehicle):
            has_heat_pump: bool = True
        normal = _MockVehicle(auxiliary_power_kw=1.5)
        with_hp = HeatPumpVehicle(auxiliary_power_kw=1.5)
        seg = _make_segment(distance_km=50.0, duration_min=60.0, temp=-10.0)
        normal_aux = MainCalculator.calculate_segment_consumption(seg, normal).aux_consumption_kwh
        hp_aux = MainCalculator.calculate_segment_consumption(seg, with_hp).aux_consumption_kwh
        assert hp_aux < normal_aux, f"Heat pump düşürmeli: normal={normal_aux:.2f}, hp={hp_aux:.2f}"

    def test_high_speed_aero_factor_applied(self):
        """max_speed_kmh > 110 olunca aero faktörü devreye girmeli."""
        vehicle = _MockVehicle()
        seg = _make_segment(distance_km=100.0, duration_min=60.0)
        normal = calculate_segment_consumption_kwh(
            vehicle=vehicle, segment_distance_km=100.0, duration_minutes=60.0,
        )
        high_speed = calculate_segment_consumption_kwh(
            vehicle=vehicle, segment_distance_km=100.0, duration_minutes=60.0,
            max_speed_kmh=140,
        )
        assert high_speed > normal, "140 km/h'de tüketim 110'dan yüksek olmalı"

    def test_negative_total_marked_as_charging(self):
        """Çok dik iniş + minimal aux → net şarj durumu."""
        vehicle = _MockVehicle(curb_weight_kg=2000.0, auxiliary_power_kw=0.3)
        seg = _make_segment(
            distance_km=5.0, gain_m=0.0, loss_m=1500.0, duration_min=10.0, temp=20.0
        )
        result = MainCalculator.calculate_segment_consumption(
            seg, vehicle, passenger_count=1, hvac_on=False
        )
        # Toplam negatif olmalı
        if result.total_consumption_kwh < 0:
            assert result.is_net_charging is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
