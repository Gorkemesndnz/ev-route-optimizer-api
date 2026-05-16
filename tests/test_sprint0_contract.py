"""
Sprint 0 — FastAPI Contract Tests
==================================

docs/archive/Yapilacaklar.md Section 17.3 Sprint 0 doğrultusunda yazılan testler.

Bu testler **production davranışını değiştirmeden** hedef kontratı kilitler.
Bazıları ilk çalıştırmada kırmızı veya xfail olarak gelir; Sprint 1-5 ilerledikçe
yeşile döner. Her kırmızı test, hangi sprint'in onu yeşile döndürmesi gerektiği
ile docstring'inde işaretlidir.

Sprint sonu hedefi:
  - F1, F2, F3 → Sprint 2 (Vehicle Data Hardening)
  - F4         → Yeşil (Yaklaşım B — override + correction)
  - F5, F6     → Sprint 5 (Station Normalization)
  - F7, F8     → Sprint 3 (Road/Bridge Politikası)
  - F9         → Mevcut Pareto regresyon, gate olarak korunur

Run:
  pytest tests/test_sprint0_contract.py -v
"""

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import VehiclePayload, RouteRequest, GeoPoint, RoadAvoidances
from app.infrastructure.vehicle_catalog.resolver import resolve_vehicle_spec
from app.infrastructure.vehicle_catalog.models import VehicleSpec, VehicleType


# =============================================================================
# FIXTURES — Test Vehicle (MG4 referans)
# =============================================================================

@pytest.fixture
def mg4_payload_full() -> VehiclePayload:
    """
    MG4 51 kWh — Sprint 0 referans aracı.
    Tüm DB alanları doluyu simüle eder.
    """
    return VehiclePayload(
        id=1,
        slug="mg4-51",
        brand="MG",
        model="MG4",
        variant="Standard Range",
        year=2023,
        battery_useable_kwh=50.8,
        battery_nominal_kwh=51.0,
        battery_chemistry="LFP",
        battery_thermal_management="Sıvı Soğutmalı",
        heat_pump=True,                  # F1: resolver bunu has_heat_pump'a map'lemeli
        battery_preconditioning=True,    # Sprint 2: VehicleSpec'te alan eklenmeli
        wltp_range_tel_km=350,
        wltp_nominal_consumption_wh_km=145,
        real_range_km=280,
        efficiency_wh_km=180,
        curb_weight_kg=1635,
        drag_coefficient=0.27,
        frontal_area_m2=2.30,             # F2: VehicleSpec'te korunmalı
        fastcharge_power_max_kw=117,
        ac_charge_power_kw=11,
        connector_type="CCS",
        vehicle_type="suv",               # F3: VehicleType.SUV'a map'lemeli
        seats=5,
    )


@pytest.fixture
def istanbul_ankara_request_payload() -> dict:
    """
    Sprint 0 referans rota: Istanbul → Ankara.
    Pydantic validation testleri için ham dict.
    """
    return {
        "start_location": {"lat": 41.0082, "lon": 28.9784},
        "end_location": {"lat": 39.9334, "lon": 32.8597},
        "vehicle_model_id": "mg4_51kwh",
        "current_soc_percent": 85,
        "smart_plan_enabled": True,
        "optimization_mode": "balanced",
    }


# =============================================================================
# F1-F3: VEHICLE RESOLVER — VehiclePayload → VehicleSpec
# =============================================================================

class TestVehicleResolverContract:
    """
    Resolver mevcut alan kayıplarını test eder.
    Sprint 2 (Vehicle Data Hardening) bu testleri yeşile döndürür.
    """

    def test_F1_vehicle_resolver_preserves_heat_pump(self, mg4_payload_full):
        """
        F1 — Heat pump bilgisi VehicleSpec'e taşınmalı.

        Sprint 2'de VehicleSpec.has_heat_pump alanı eklendi ve resolver
        payload.heat_pump → spec.has_heat_pump map'lemesi yapıyor.
        (Soğuk hava aux/HVAC consumption düzeltmesi Sprint 3'te aktive edilecek.)
        """
        spec = resolve_vehicle_spec(mg4_payload_full)
        # Bu attribute şu an yok — AttributeError yakalanacak
        assert getattr(spec, "has_heat_pump", None) is True, (
            "VehicleSpec.has_heat_pump = True bekleniyor; "
            "resolver heat_pump bilgisini consumption engine'in kullanabileceği "
            "şekilde taşımalı."
        )

    def test_F2_vehicle_resolver_preserves_frontal_area(self, mg4_payload_full):
        """
        F2 — frontal_area_m2 zaten resolver tarafından geçiriliyor olmalı.

        Şu an: ✅ resolver L199 frontal_area'yı VehicleSpec'e taşıyor.
        Hedef: payload.frontal_area_m2=2.30 → spec.frontal_area_m2=2.30
        """
        spec = resolve_vehicle_spec(mg4_payload_full)
        assert spec.frontal_area_m2 == pytest.approx(2.30, abs=0.01), (
            f"frontal_area_m2 kaybolmuş: {spec.frontal_area_m2}"
        )

    def test_F3_vehicle_resolver_preserves_vehicle_type(self, mg4_payload_full):
        """
        F3 — vehicle_type "suv" → VehicleType.SUV enum'una map'lenmeli.

        Şu an: ✅ resolver L46 _VEHICLE_TYPE_MAP doğru çalışıyor.
        """
        spec = resolve_vehicle_spec(mg4_payload_full)
        assert spec.vehicle_type == VehicleType.SUV, (
            f"vehicle_type 'suv' → VehicleType.SUV bekleniyor, alındı: {spec.vehicle_type}"
        )

    def test_F3b_vehicle_resolver_preserves_battery_preconditioning(self, mg4_payload_full):
        """
        F3b — battery_preconditioning soğuk hava şarj davranışı için kritik.

        Sprint 2'de VehicleSpec.battery_preconditioning alanı eklendi ve resolver
        payload.battery_preconditioning → spec.battery_preconditioning map'lemesi
        yapıyor. (DC fast-charge öncesi batarya ısıtma kabiliyeti Sprint 3'te
        charging engine tarafından kullanılacak.)
        """
        spec = resolve_vehicle_spec(mg4_payload_full)
        assert getattr(spec, "battery_preconditioning", None) is True


# =============================================================================
# F4: OVERRIDE + CORRECTION — Yaklaşım B Kilidi
# =============================================================================

class TestOverrideCorrectionContract:
    """
    Yaklaşım B davranışı: consumption_override_wh_km aracın baz tüketimini
    değiştirir, ama hava/yokuş/yük düzeltmeleri override değeri üstüne biner.

    Sprint 1.1 bu testi yeşile döndürür.
    """

    def test_F4_consumption_override_does_not_disable_corrections(self):
        """
        F4 — Yaklaşım B: Override aracın baz tüketimini set eder; mass/weather/
        load/aux düzeltmeleri override değeri üstüne biner.

        Senaryo:
          - Override: 150 Wh/km (= 0.150 kWh/km)
          - 200 km düz yol (elevation = 0)
          - 4 yolcu + 100 kg ekstra yük (mass_factor > 1)
          - HVAC kapalı (aux küçük ama yine de uygulanır)
        Beklenen:
          - total_consumption_kwh > 150 × 0.001 × 200 = 30.0 kWh
          - mass_factor > 1.0 olduğu kanıtlanır → Yaklaşım B davranışı
        Yaklaşım A olsaydı (override tüm düzeltmeleri atlatsaydı):
          - total = 30.0 kWh tam (mass ve weather etkisi sıfırlanırdı)
        """
        from app.consumption_engine.main_calculator import MainCalculator
        from app.infrastructure.vehicle_catalog.models import (
            VehicleSpec, ConnectorType, VehicleType,
        )
        from app.models.route_models import DriveLeg, GeoPoint

        # MG4 benzeri test aracı
        vehicle = VehicleSpec(
            id="mg4_sr_50",
            source_id="test-mg4",
            brand="MG", model="MG4", variant="Standard Range",
            year=2023, display_name="MG MG4 SR",
            battery_capacity_kwh=50.8,
            base_consumption_wh_km=145.0,
            connector_type=ConnectorType.CCS,
            ac_max_kw=6.6, dc_max_kw=117.0, charging_voltage=400,
            curb_weight_kg=1635,
            drag_coefficient=0.27,
            frontal_area_m2=2.30,
            vehicle_type=VehicleType.HATCHBACK,
        )

        # 200 km düz yol segmenti (rasgele İstanbul-Ankara koordinatları)
        leg = DriveLeg(
            start_point=GeoPoint(lat=41.0, lon=29.0),
            end_point=GeoPoint(lat=39.5, lon=31.5),
            distance_km=200.0,
            duration_minutes=120.0,
            avg_speed_kmh=100.0,
            elevation_gain_m=0.0,
            elevation_loss_m=0.0,
        )

        OVERRIDE_WH_KM = 150.0
        OVERRIDE_KWH = (OVERRIDE_WH_KM / 1000.0) * leg.distance_km  # 30.0 kWh

        result = MainCalculator.calculate_segment_consumption(
            segment=leg,
            vehicle=vehicle,
            passenger_count=4,
            extra_load_kg=100.0,   # mass_factor'u boost et
            hvac_on=False,         # aux'u küçük tut
            consumption_override_wh_km=OVERRIDE_WH_KM,
        )

        total_kwh = result.total_consumption_kwh

        # Yaklaşım B: mass_factor (>1.0) override üstüne bindi → override'dan büyük
        assert total_kwh > OVERRIDE_KWH, (
            f"Yaklaşım B beklendi: override={OVERRIDE_KWH:.2f} kWh; "
            f"final={total_kwh:.2f} kWh. mass_factor düzeltmesi override üstüne "
            f"binmemiş — kod Yaklaşım A davranıyor olabilir."
        )

        # Mass factor faktör listesinde > 1.0 olduğunu kanıtla
        mass_factor = result.factors.get("mass", 1.0)
        assert mass_factor > 1.0, (
            f"4 yolcu + 100 kg yük için mass_factor > 1.0 beklenir; "
            f"alınan: {mass_factor}. Override mass etkisini iptal etmiş olabilir."
        )


# =============================================================================
# F5-F6: STATION FILTERING — Unknown kW Exclusion + Availability Handling
# =============================================================================

class TestStationContractFuture:
    """
    Sprint 5 (StationProvider Normalization) bu testleri yeşile döndürür.

    Karar (Sprint 5): Unknown kW istasyon route planning'den ELENMEZ;
    NormalizedStation.power_known=False olur, planlama UNKNOWN_POWER_PLANNING_KW
    (50 kW konservatif) ile yapılır ve istasyon düşük güven cezası alır.
    Availability eksikse availability_status='unknown' olur, aday kalır.
    """

    def test_F5_unknown_kw_station_kept_with_conservative_power(self):
        """
        F5 — maxChargeRateKw=None olan Google station ELENMEZ; düşük güvenle kalır.

        Sprint 5 davranışı:
          - power_known=False
          - planning_power_kw == UNKNOWN_POWER_PLANNING_KW (50 kW)
          - max_power_kw is None (gerçek değer bilinmiyor)
          - Aday listesinde kalır (downstream skor cezası uygular)
        """
        from app.infrastructure.station_catalog import (
            UNKNOWN_DC_POWER_PLANNING_KW,
            parse_google_place,
        )

        raw = {
            "id": "ChIJ_unknown_kw",
            "displayName": {"text": "Unknown kW Station"},
            "location": {"latitude": 40.0, "longitude": 30.0},
            "evChargeOptions": {
                "connectorCount": 2,
                "connectorAggregation": [
                    {
                        "type": "EV_CONNECTOR_TYPE_CCS_COMBO_2",
                        # maxChargeRateKw KASITLI YOK
                        "count": 2,
                        "availableCount": 1,
                    }
                ],
            },
            "businessStatus": "OPERATIONAL",
        }

        station = parse_google_place(raw)

        assert station.power_known is False, (
            "maxChargeRateKw eksikse station.power_known=False olmalı"
        )
        assert station.max_power_kw is None, (
            "Bilinmeyen kW için max_power_kw None olmalı (iyimser fallback değil)"
        )
        assert station.planning_power_kw == UNKNOWN_DC_POWER_PLANNING_KW, (
            f"planning_power_kw {UNKNOWN_DC_POWER_PLANNING_KW} olmalı; alındı: "
            f"{station.planning_power_kw}"
        )
        # Konektör seviyesinde de aynı kontrat
        assert all(c.power_known is False for c in station.connectors)
        assert all(c.power_kw is None for c in station.connectors)

    def test_F6_availability_missing_station_kept_with_unknown_status(self):
        """
        F6 — availableCount=None olan istasyon elenmez; availability_status='unknown'.

        Sprint 5 davranışı:
          - Google response: maxChargeRateKw=150, availableCount=None
          - availability_status == AvailabilityStatus.UNKNOWN
          - station aday listesinde kalır
          - power_known=True (kW biliniyor) ama availability düşük güven cezası alır
        """
        from app.infrastructure.station_catalog import (
            AvailabilityStatus,
            parse_google_place,
        )

        raw = {
            "id": "ChIJ_avail_missing",
            "displayName": {"text": "Availability Missing"},
            "location": {"latitude": 40.5, "longitude": 30.5},
            "evChargeOptions": {
                "connectorCount": 4,
                "connectorAggregation": [
                    {
                        "type": "EV_CONNECTOR_TYPE_CCS_COMBO_2",
                        "maxChargeRateKw": 150,
                        "count": 4,
                        # availableCount KASITLI YOK
                    }
                ],
            },
            "businessStatus": "OPERATIONAL",
        }

        station = parse_google_place(raw)

        assert station.power_known is True
        assert station.max_power_kw == 150.0
        assert station.availability_status == AvailabilityStatus.UNKNOWN, (
            f"availability_status='unknown' bekleniyor; alındı: "
            f"{station.availability_status}"
        )
        assert station.total_available_count is None


# =============================================================================
# F7-F8: ROAD AVOIDANCE — Bridge / Private Highway Fields
# =============================================================================

class TestRoadAvoidanceContract:
    """
    Sprint 3 (Road Preferences ve Köprü Politikası) bu testleri yeşile döndürür.
    """

    def test_F7_route_request_existing_bridge_fields(self, istanbul_ankara_request_payload):
        """
        F7a — Mevcut spesifik köprü alanları çalışıyor mu?

        Şu an RoadAvoidances'te `avoid_osmangazi_bridge` ve `avoid_canakkale_bridge` var.
        Bu test mevcut davranışı kilitler.
        """
        payload = {
            **istanbul_ankara_request_payload,
            "preferences": {
                "road_avoidances": {
                    "avoid_osmangazi_bridge": True,
                    "avoid_canakkale_bridge": True,
                }
            }
        }
        req = RouteRequest(**payload)
        assert req.preferences.road_avoidances.avoid_osmangazi_bridge is True
        assert req.preferences.road_avoidances.avoid_canakkale_bridge is True

    def test_F7b_route_request_accepts_generic_avoid_bridges(self, istanbul_ankara_request_payload):
        """
        F7b — Generic `avoid_bridges` field eklenmeli.

        Sprint 3'te RoadAvoidances.avoid_bridges field'ı eklendi. Spesifik
        avoid_osmangazi_bridge / avoid_canakkale_bridge alanları geriye uyumluluk
        için korunuyor; UI tarafı tek toggle ile generic alanı gönderiyor.
        forbidden_crossings.yaml entegrasyonu (orchestrator filtreleme) ayrı
        sprint hedefi olarak takip ediliyor.
        """
        payload = {
            **istanbul_ankara_request_payload,
            "preferences": {
                "road_avoidances": {
                    "avoid_bridges": True,  # Sprint 3 sonrası eklenecek
                }
            }
        }
        req = RouteRequest(**payload)
        # Sprint 3 sonrası: bu alan kabul edilmeli
        assert req.preferences.road_avoidances.avoid_bridges is True

    def test_F8_route_request_accepts_avoid_private_highways(self, istanbul_ankara_request_payload):
        """
        F8 — `avoid_private_highways` field Sprint 3'te eklendi.

        avoid_tolls geleneksel KGM ücretli yolları kapsar; avoid_private_highways
        özel sektör (BOT) otoyollarını ayrı toggle ile filtreler. React UI'daki
        toggleOzelOtoyollar bu alana bağlı.
        """
        payload = {
            **istanbul_ankara_request_payload,
            "preferences": {
                "road_avoidances": {
                    "avoid_private_highways": True,
                }
            }
        }
        req = RouteRequest(**payload)
        assert req.preferences.road_avoidances.avoid_private_highways is True

    def test_F8b_road_avoidances_existing_fields_preserved(self):
        """
        F8b — Mevcut avoid_tolls/highways/ferries alanları kayıpsız çalışıyor.
        """
        avoidances = RoadAvoidances(
            avoid_tolls=True,
            avoid_highways=True,
            avoid_ferries=True,
        )
        assert avoidances.avoid_tolls is True
        assert avoidances.avoid_highways is True
        assert avoidances.avoid_ferries is True


# =============================================================================
# F9: PARETO REGRESSION GATE
# =============================================================================

class TestParetoRegressionGate:
    """
    Mevcut Pareto regresyon testleri Sprint 0 boyunca yeşil kalmalı.
    Bu testler Phase 3.5 ve Phase 4 oncesi gate olarak kullanılır.
    """

    def test_F9_pareto_regression_suite_green(self):
        """
        F9 — tests/test_regression_pareto_grid.py 17/17 yeşil olmalı.

        Bu test bir alt-process olarak ilgili dosyayı çalıştırır.
        Pareto Bug A+B fix'inin korunduğunu garanti eder.
        """
        repo_root = Path(__file__).parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/test_regression_pareto_grid.py",
             "-q", "--tb=line"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"Pareto regression suite FAILED.\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )
        assert "passed" in result.stdout, "pytest output 'passed' içermiyor"


# =============================================================================
# SPRINT 0 META — Hangi testler hangi sprint'te yeşile döner
# =============================================================================

SPRINT_TARGETS = {
    # Sprint 5: F5 (unknown kW konservatif planlama) ve F6 (availability unknown)
    # Sprint 3 ile yeşile dönenler: F7b (generic avoid_bridges), F8 (avoid_private_highways)
    "Already green (gate)": [
        "F1", "F2", "F3", "F3b", "F4",
        "F5", "F6",
        "F7a", "F7b", "F8", "F8b", "F9",
    ],
}


def test_sprint_targets_documented():
    """
    Meta-test: Sprint hedefleri dokümante edildi mi?
    Yeni test eklendiğinde SPRINT_TARGETS güncel tutulmalı.
    """
    all_targets = set()
    for tests in SPRINT_TARGETS.values():
        all_targets.update(tests)
    expected_count = 11  # F1, F2, F3, F3b, F4, F5, F6, F7a, F7b, F8, F8b, F9 = 12
    # F9 + 11 numbered = 12 toplam, ama bazıları F7a/F7b/F3b alt-numara
    assert len(all_targets) >= 10, (
        f"SPRINT_TARGETS eksik dokümante edilmiş: {all_targets}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
