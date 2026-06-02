"""
EV Route Optimizer — Integration (API Endpoint) Testleri
=========================================================

Tüm endpoint'leri HTTP düzeyinde test eder.
Tüm response'lar ApiResponse[T] wrapper içinde döner:
    {"success": bool, "data": T | None, "error": str | None}

Mimari notlar:
- Vehicle catalog endpoint'leri (.NET MSSQL backend'e taşındı) bu API'de YOK.
- /geocode endpoint'i de FastAPI tarafında değil (frontend Google JS SDK kullanır).
- Status alanı artık MultiStopRouteResponse içinde (data.status), kök seviyede değil.

Run: pytest tests/test_api_endpoints.py -v
Run only offline: pytest tests/test_api_endpoints.py -v -k "not slow"
"""

import pytest

ISTANBUL_LAT = 41.0082
ISTANBUL_LON = 28.9784
ANKARA_LAT = 39.9334
ANKARA_LON = 32.8597

# Vehicle catalog .NET tarafına taşındığı için artık FastAPI tarafında ID bazlı
# araç bulamaz; route optimization testleri vehicle_payload alanı üzerinden çalışır.


# =============================================================================
# GRUP 1: HEALTH & INFO — Dış API çağrısı yok
# =============================================================================

class TestHealthAndInfo:
    """Temel sistem durumu endpoint'leri (ApiResponse wrapped)."""

    def test_api_info_returns_json(self, client):
        """GET /api/info → ApiResponse içinde version + environment dönmeli."""
        response = client.get("/api/info")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert "version" in data
        assert "environment" in data
        assert "message" in data

    def test_health_check(self, client):
        """GET /health → ApiResponse içinde status='ok' veya fail dönmeli."""
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        # Vehicle catalog deprecated → fail beklenir; success ise status=='ok' olmalı
        if body["success"]:
            assert body["data"]["status"] == "ok"
        else:
            assert body["error"] is not None


# =============================================================================
# GRUP 2: ROUTE OPTIMIZATION — Validation
# =============================================================================

class TestRouteOptimizationValidation:
    """Validation testleri — dış API çağrısı yok."""

    def test_optimize_route_missing_fields(self, client):
        """POST /optimize_route boş body → 422 Validation Error."""
        response = client.post("/optimize_route", json={})
        assert response.status_code == 422

    def test_internal_optimize_route_missing_fields(self, client):
        """POST /internal/routes/optimize boş body → 422 Validation Error."""
        response = client.post("/internal/routes/optimize", json={})
        assert response.status_code == 422

    def test_optimize_route_invalid_soc_negative(self, client):
        """POST /optimize_route SOC=-5 → 422 (Pydantic ge=0 kısıtı)."""
        response = client.post("/optimize_route", json={
            "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": "test_vehicle",
            "current_soc_percent": -5,
        })
        assert response.status_code == 422

    def test_optimize_route_invalid_soc_over_100(self, client):
        """POST /optimize_route SOC=150 → 422 (Pydantic le=100 kısıtı)."""
        response = client.post("/optimize_route", json={
            "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": "test_vehicle",
            "current_soc_percent": 150,
        })
        assert response.status_code == 422

    def test_optimize_route_invalid_optimization_mode(self, client):
        """optimization_mode geçersiz → 422 (Faz 2 validator)."""
        response = client.post("/optimize_route", json={
            "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": "test_vehicle",
            "current_soc_percent": 80,
            "optimization_mode": "ultrafast_invalid",
        })
        assert response.status_code == 422

    def test_optimize_route_error_status_plan_is_non_2xx(self, client, monkeypatch):
        """plan_route hata statulu plan dondururse 200 success zarfina sarilmamali."""
        from app.models.route_models import MultiStopRouteResponse
        from app.routers import optimize as optimize_router

        async def fake_plan_route(request):
            return MultiStopRouteResponse(
                status="error_unknown",
                total_distance_km=0,
                total_duration_minutes=0,
                total_co2_savings_kg=0,
                legs=[],
                message="alignment failed",
            )

        monkeypatch.setattr(optimize_router, "plan_route", fake_plan_route)

        response = client.post("/optimize_route", json={
            "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": "test_vehicle",
            "current_soc_percent": 80,
        })

        assert response.status_code == 502
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "ROUTE_PLANNING_FAILED"

    def test_optimize_route_no_route_with_constraints_is_typed_422(self, client, monkeypatch):
        """Yol tercihi nedeniyle rota bulunamazsa bos legs success zarfina sarilmamali."""
        from app.models.route_models import MultiStopRouteResponse
        from app.routers import optimize as optimize_router

        async def fake_plan_route(request):
            return MultiStopRouteResponse(
                status="NO_ROUTE_WITH_CONSTRAINTS",
                total_distance_km=0,
                total_duration_minutes=0,
                total_co2_savings_kg=0,
                legs=[],
                message="Yol/köprü tercihlerine uyan rota bulunamadı",
            )

        monkeypatch.setattr(optimize_router, "plan_route", fake_plan_route)

        response = client.post("/optimize_route", json={
            "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": "test_vehicle",
            "current_soc_percent": 80,
        })

        assert response.status_code == 422
        body = response.json()
        assert body["success"] is False
        assert body["data"] is None
        assert body["error"]["code"] == "NO_ROUTE_WITH_CONSTRAINTS"
        assert "Yol/köprü tercihlerine uyan rota bulunamadı" in body["error"]["message"]

    def test_optimize_route_hotspot_alignment_error_has_typed_code(self, client, monkeypatch):
        """Hotspot alignment upstream error olarak non-2xx donmeli."""
        from app.routers import optimize as optimize_router
        from app.services.base_service import ExternalAPIError

        async def fake_plan_route(request):
            raise ExternalAPIError("HotspotAlignment", 502, "drift exceeds tolerance")

        monkeypatch.setattr(optimize_router, "plan_route", fake_plan_route)

        response = client.post("/optimize_route", json={
            "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": "test_vehicle",
            "current_soc_percent": 80,
        })

        assert response.status_code == 502
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "HOTSPOT_ALIGNMENT_FAILED"

    def test_optimize_route_too_many_waypoints_error_has_typed_code(self, client, monkeypatch):
        from app.routers import optimize as optimize_router
        from app.services.base_service import ExternalAPIError

        async def fake_plan_route(request):
            raise ExternalAPIError(
                "GoogleDirections",
                400,
                "MAX_WAYPOINTS_EXCEEDED",
                code="TOO_MANY_WAYPOINTS",
            )

        monkeypatch.setattr(optimize_router, "plan_route", fake_plan_route)

        response = client.post("/optimize_route", json={
            "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "waypoints": [{"lat": 40.8438, "lon": 31.1565}],
            "vehicle_model_id": "test_vehicle",
            "current_soc_percent": 80,
        })

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "TOO_MANY_WAYPOINTS"


# =============================================================================
# GRUP 3: FEEDBACK & SWITCH — Validation
# =============================================================================

class TestFeedbackValidation:
    """Feedback endpoint validation."""

    def test_feedback_invalid_type(self, client):
        """POST /station_feedback geçersiz feedback_type → 422."""
        response = client.post("/station_feedback", json={
            "station_id": "test_station",
            "feedback_type": "invalid_type_xyz",
            "current_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "current_soc_percent": 80,
            "destination": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": "test_vehicle",
        })
        assert response.status_code == 422

    def test_internal_feedback_invalid_type(self, client):
        """POST /internal/stations/feedback geçersiz feedback_type → 422."""
        response = client.post("/internal/stations/feedback", json={
            "station_id": "station_123",
            "feedback_type": "invalid",
            "original_route_request": {
                "start_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
                "end_location": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
                "vehicle_model_id": "test_vehicle",
            },
            "destination": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": "test_vehicle",
        })
        assert response.status_code == 422

    def test_internal_switch_station_missing_fields(self, client):
        """POST /internal/stations/switch boş body → 422."""
        response = client.post("/internal/stations/switch", json={})
        assert response.status_code == 422

    def test_station_feedback_recompute_error_keeps_feedback_before_502(self, client, monkeypatch):
        from app.routers import stations as stations_router
        from app.services.base_service import ExternalAPIError

        events = []

        async def fake_report_station(station_id, user_id, reason):
            events.append(("feedback", station_id, reason))
            return {"message": "Feedback kaydedildi.", "is_blocked": False}

        async def fake_plan_route(request):
            events.append(("plan_route", request.vehicle_model_id))
            raise ExternalAPIError("SnapToRoads", 502, "snap failed")

        monkeypatch.setattr(stations_router.feedback_manager, "report_station", fake_report_station)
        monkeypatch.setattr(stations_router, "plan_route", fake_plan_route)

        response = client.post("/station_feedback", json={
            "station_id": "station_123",
            "feedback_type": "station_broken",
            "current_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "current_soc_percent": 80,
            "destination": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": "test_vehicle",
        })

        assert response.status_code == 502
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "EXTERNAL_API_ERROR"
        assert events == [
            ("feedback", "station_123", "station_broken"),
            ("plan_route", "test_vehicle"),
        ]

    def test_switch_station_recompute_error_keeps_feedback_before_502(self, client, monkeypatch):
        from app.routers import stations as stations_router
        from app.services.base_service import ExternalAPIError

        events = []

        async def fake_report_station(station_id, user_id, reason):
            events.append(("feedback", station_id, reason))
            return {"message": "Feedback kaydedildi.", "is_blocked": False}

        async def fake_plan_route(request):
            events.append(("plan_route", request.vehicle_model_id))
            raise ExternalAPIError("PolylineCorridor", 502, "corridor failed")

        monkeypatch.setattr(stations_router.feedback_manager, "report_station", fake_report_station)
        monkeypatch.setattr(stations_router, "plan_route", fake_plan_route)

        response = client.post("/switch_station", json={
            "original_station_id": "old_station",
            "new_station_id": "new_station",
            "new_station": {
                "id": "new_station",
                "name": "New Station",
                "location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
                "connectors": [
                    {
                        "plug_type": "CCS2",
                        "charger_type": "DC",
                        "power_kw": 120,
                        "status": "Available",
                    }
                ],
            },
            "leg_index": 0,
            "current_location": {"lat": ISTANBUL_LAT, "lon": ISTANBUL_LON},
            "current_soc_percent": 80,
            "destination": {"lat": ANKARA_LAT, "lon": ANKARA_LON},
            "vehicle_model_id": "test_vehicle",
            "battery_capacity_kwh": 60,
        })

        assert response.status_code == 502
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "EXTERNAL_API_ERROR"
        assert events == [
            ("feedback", "old_station", "user_switched"),
            ("plan_route", "test_vehicle"),
        ]


class TestMapStationsStatusSemantics:
    def test_map_stations_empty_result_is_success(self, client, monkeypatch):
        from app.routers import stations as stations_router

        async def fake_get_map_stations(**kwargs):
            return []

        monkeypatch.setattr(stations_router.google_maps, "get_map_stations", fake_get_map_stations)

        response = client.get("/api/map_stations", params={
            "lat": ISTANBUL_LAT,
            "lon": ISTANBUL_LON,
            "radius_km": 10,
            "zoom": 12,
        })

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"] == []

    def test_map_stations_provider_error_is_non_2xx(self, client, monkeypatch):
        from app.routers import stations as stations_router
        from app.services.base_service import ExternalAPIError

        async def fake_get_map_stations(**kwargs):
            raise ExternalAPIError("GooglePlaces", 503, "provider down")

        monkeypatch.setattr(stations_router.google_maps, "get_map_stations", fake_get_map_stations)

        response = client.get("/api/map_stations", params={
            "lat": ISTANBUL_LAT,
            "lon": ISTANBUL_LON,
            "radius_km": 10,
            "zoom": 12,
        })

        assert response.status_code == 502
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "STATION_PROVIDER_ERROR"


# =============================================================================
# GRUP 4: API RESPONSE WRAPPER CONTRACT
# =============================================================================

class TestApiResponseContract:
    """Tüm endpoint'lerin ApiResponse şablonuna uyduğunu doğrular."""

    @pytest.mark.parametrize("path", ["/api/info", "/health"])
    def test_response_has_wrapper_keys(self, client, path):
        response = client.get(path)
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) >= {"success", "data", "error"}

    def test_validation_error_uses_apiresponse(self, client):
        """422 cevapları da ApiResponse şablonunda dönmeli."""
        response = client.post("/optimize_route", json={})
        assert response.status_code == 422
        body = response.json()
        # Global validation handler ApiResponse.fail döner
        assert body["success"] is False
        assert body["error"] is not None


class TestInternalServiceAuth:
    def test_internal_auth_secret_is_not_required_when_not_configured(self, client, monkeypatch):
        monkeypatch.delenv("FASTAPI_INTERNAL_AUTH_SECRET", raising=False)

        response = client.post("/optimize_route", json={})

        assert response.status_code == 422
        body = response.json()
        assert body["success"] is False

    def test_internal_auth_secret_rejects_protected_route_without_header(self, client, monkeypatch):
        monkeypatch.setenv("FASTAPI_INTERNAL_AUTH_SECRET", "test-secret")

        response = client.post("/optimize_route", json={})

        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INTERNAL_AUTH_REQUIRED"

    def test_internal_auth_secret_rejects_canonical_route_without_header(self, client, monkeypatch):
        monkeypatch.setenv("FASTAPI_INTERNAL_AUTH_SECRET", "test-secret")

        response = client.post("/internal/routes/optimize", json={})

        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INTERNAL_AUTH_REQUIRED"

    @pytest.mark.parametrize("path", [
        "/optimize_route",
        "/internal/routes/optimize",
        "/station_feedback",
        "/internal/stations/feedback",
        "/switch_station",
        "/internal/stations/switch",
        "/trips/test_trip/outcome",
        "/internal/trips/test_trip/outcome",
    ])
    def test_internal_auth_secret_rejects_all_protected_paths_without_header(self, client, monkeypatch, path):
        monkeypatch.setenv("FASTAPI_INTERNAL_AUTH_SECRET", "test-secret")

        response = client.post(path, json={})

        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INTERNAL_AUTH_REQUIRED"

    def test_internal_auth_secret_allows_protected_route_with_valid_header(self, client, monkeypatch):
        monkeypatch.setenv("FASTAPI_INTERNAL_AUTH_SECRET", "test-secret")

        response = client.post(
            "/optimize_route",
            json={},
            headers={"X-IYONTREE-Internal-Secret": "test-secret"},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["success"] is False

    def test_internal_auth_secret_allows_canonical_route_with_valid_header(self, client, monkeypatch):
        monkeypatch.setenv("FASTAPI_INTERNAL_AUTH_SECRET", "test-secret")

        response = client.post(
            "/internal/routes/optimize",
            json={},
            headers={"X-IYONTREE-Internal-Secret": "test-secret"},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["success"] is False

    def test_internal_auth_does_not_protect_health_endpoint(self, client, monkeypatch):
        monkeypatch.setenv("FASTAPI_INTERNAL_AUTH_SECRET", "test-secret")

        response = client.get("/health")

        assert response.status_code == 200
