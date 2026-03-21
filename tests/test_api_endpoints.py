"""
EV Route Optimizer — Integration (API Endpoint) Testleri
=========================================================

Tüm endpoint'leri HTTP düzeyinde test eder.
Gerçek API çağrıları yapılır (mock yok).

Grup 1: Health & Info (3 test) — Dış API yok
Grup 2: Vehicle Catalog (4 test) — Dış API yok
Grup 3: Route Optimization (5 test) — Gerçek API çağrısı (Grup 3a offline, 3b online)
Grup 4: Feedback & Switch (3 test) — Gerçek API çağrısı
Grup 5: Geocode (1 test) — Google Geocoding API

Run: pytest tests/test_api_endpoints.py -v
Run only offline: pytest tests/test_api_endpoints.py -v -k "not slow"
"""

import pytest

# Test sabitleri — conftest.py'den otomatik inject edilen fixture'lar dışında
# Bu sabitler doğrudan kullanılır (fixture değil)

# İstanbul koordinatları (test başlangıç noktası)
ISTANBUL_LAT = 41.0082
ISTANBUL_LON = 28.9784

# Ankara koordinatları (test varış noktası)
ANKARA_LAT = 39.9334
ANKARA_LON = 32.8597

# Bilinen test araç modeli
TEST_VEHICLE_ID = "abarth_500e_hatchback_2024"


# =============================================================================
# GRUP 1: HEALTH & INFO — Dış API çağrısı yok
# =============================================================================

class TestHealthAndInfo:
    """Temel sistem durumu endpoint'leri."""

    def test_api_info_returns_json(self, client):
        """GET /api/info → API bilgileri JSON olarak dönmeli."""
        response = client.get("/api/info")
        data = response.json()

        assert response.status_code == 200
        assert "version" in data
        assert "available_vehicles" in data
        assert isinstance(data["available_vehicles"], list)
        assert len(data["available_vehicles"]) > 0

    def test_health_check(self, client):
        """GET /health → Tüm componentlar sağlıklı olmalı."""
        response = client.get("/health")
        data = response.json()

        assert response.status_code == 200
        assert data["status"] == "ok"
        assert "components" in data
        assert data["components"]["vehicle_models"] == "ok"
        assert "test_vehicle" in data
        assert data["test_vehicle"]["battery_kwh"] > 0


# =============================================================================
# GRUP 2: VEHICLE CATALOG — Dış API çağrısı yok
# =============================================================================

class TestVehicleCatalog:
    """Araç katalog endpoint'leri."""

    def test_list_brands(self, client):
        """GET /vehicles/brands → Marka listesi boş olmamalı."""
        response = client.get("/vehicles/brands")
        data = response.json()

        assert response.status_code == 200
        assert data["status"] == "success"
        assert isinstance(data["brands"], list)
        assert len(data["brands"]) > 0

    def test_vehicles_by_brand(self, client):
        """GET /vehicles/by_brand?brand=Abarth → Abarth araçları dönmeli."""
        response = client.get("/vehicles/by_brand", params={"brand": "Abarth"})
        data = response.json()

        assert response.status_code == 200
        assert data["status"] == "success"
        assert isinstance(data["vehicles"], list)
        # Her araçta gerekli alanlar olmalı
        if len(data["vehicles"]) > 0:
            vehicle = data["vehicles"][0]
            assert "id" in vehicle
            assert "display_name" in vehicle
            assert "battery_kwh" in vehicle
            assert vehicle["battery_kwh"] > 0

    def test_vehicles_search(self, client):
        """GET /vehicles/search?query=500E → Arama sonucu dönmeli."""
        response = client.get("/vehicles/search", params={"query": "500E"})
        data = response.json()

        assert response.status_code == 200
        assert data["status"] == "success"
        assert isinstance(data["vehicles"], list)

    def test_vehicles_by_brand_missing_param(self, client):
        """GET /vehicles/by_brand (brand yok) → 422 Validation Error."""
        response = client.get("/vehicles/by_brand")
        assert response.status_code == 422


# =============================================================================
# GRUP 3: ROUTE OPTIMIZATION
# =============================================================================

class TestRouteOptimizationValidation:
    """Rota optimizasyonu — validation testleri (dış API çağrısı yok)."""

    def test_optimize_route_missing_fields(self, client):
        """POST /optimize_route boş body → 422 Validation Error."""
        response = client.post("/optimize_route", json={})
        assert response.status_code == 422

    def test_optimize_route_invalid_soc_negative(self, client):
        """POST /optimize_route SOC=-5 → 422 (Pydantic ge=0 kısıtı)."""
        response = client.post("/optimize_route", json={
            "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": TEST_VEHICLE_ID,
            "current_soc_percent": -5
        })
        assert response.status_code == 422

    def test_optimize_route_invalid_soc_over_100(self, client):
        """POST /optimize_route SOC=150 → 422 (Pydantic le=100 kısıtı)."""
        response = client.post("/optimize_route", json={
            "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": TEST_VEHICLE_ID,
            "current_soc_percent": 150
        })
        assert response.status_code == 422


@pytest.mark.slow
class TestRouteOptimizationReal:
    """Rota optimizasyonu — gerçek API çağrıları."""

    def test_optimize_route_success(self, client):
        """POST /optimize_route İstanbul→Ankara → Başarılı rota planı."""
        response = client.post("/optimize_route", json={
            "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": TEST_VEHICLE_ID,
            "current_soc_percent": 85
        })
        data = response.json()

        assert response.status_code == 200
        assert data["status"] == "success"
        assert data["total_distance_km"] > 0
        assert data["total_duration_minutes"] > 0
        assert isinstance(data["legs"], list)
        assert len(data["legs"]) > 0

        # İlk bacak sürüş olmalı
        first_leg = data["legs"][0]
        assert first_leg["type"] == "drive"
        assert first_leg["distance_km"] > 0

    def test_optimize_route_invalid_vehicle(self, client):
        """POST /optimize_route geçersiz araç → Hata mesajı (200 + error status)."""
        response = client.post("/optimize_route", json={
            "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": "nonexistent_vehicle_xyz",
            "current_soc_percent": 80
        })
        data = response.json()

        # Endpoint 200 döner ama status'ta hata bildirir
        assert response.status_code == 200
        assert "error" in data["status"]


# =============================================================================
# GRUP 4: FEEDBACK & SWITCH — Gerçek API çağrıları
# =============================================================================

@pytest.mark.slow
class TestFeedbackAndSwitch:
    """İstasyon feedback ve değiştirme endpoint'leri."""

    def test_station_feedback_success(self, client):
        """POST /station_feedback → Yeni rota hesaplama."""
        response = client.post("/station_feedback", json={
            "station_id": "test_station_001",
            "feedback_type": "station_broken",
            "current_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "current_soc_percent": 70,
            "destination": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": TEST_VEHICLE_ID,
            "excluded_station_ids": []
        })
        data = response.json()

        assert response.status_code == 200
        assert data["status"] == "success"
        assert data["route"] is not None
        assert data["recalculate_type"] == "full_route"

    def test_switch_station_success(self, client):
        """POST /switch_station → Yeni rota hesaplama."""
        response = client.post("/switch_station", json={
            "original_station_id": "old_station_001",
            "new_station_id": "new_station_002",
            "new_station": {
                "id": "new_station_002",
                "name": "Test Şarj İstasyonu",
                "location": {"lat": 40.2, "lon": 30.5},
                "connectors": [
                    {
                        "plug_type": "CCS2",
                        "charger_type": "DC",
                        "power_kw": 150
                    }
                ]
            },
            "leg_index": 1,
            "current_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "current_soc_percent": 60,
            "destination": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": TEST_VEHICLE_ID,
            "battery_capacity_kwh": 51.0
        })
        data = response.json()

        assert response.status_code == 200
        assert data["status"] == "success"
        assert data["route"] is not None

    def test_feedback_invalid_type(self, client):
        """POST /station_feedback geçersiz feedback_type → 422."""
        response = client.post("/station_feedback", json={
            "station_id": "test_station",
            "feedback_type": "invalid_type_xyz",
            "current_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "current_soc_percent": 80,
            "destination": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": TEST_VEHICLE_ID
        })
        assert response.status_code == 422


# =============================================================================
# GRUP 5: GEOCODE — Google Geocoding API
# =============================================================================

@pytest.mark.slow
class TestGeocode:
    """Adres → koordinat çevirme."""

    def test_geocode_success(self, client):
        """GET /geocode?address=Istanbul → Geçerli koordinat."""
        response = client.get("/geocode", params={"address": "Istanbul, Turkey"})
        data = response.json()

        assert response.status_code == 200
        assert data["status"] == "success"
        assert "location" in data
        # İstanbul koordinatları kabaca doğru mu?
        assert 40.5 < data["location"]["lat"] < 41.5
        assert 28.5 < data["location"]["lon"] < 29.5
