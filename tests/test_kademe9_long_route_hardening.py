"""
Kademe 9 — Uzun rota plan fizibilite hardening regresyon testleri.

Kapsanan bulgular (docs/agent-inbox/claude-findings.md):
- PMR-20260612-001: Polyline corridor orneklemesi >1000 km rotalarda kesiliyordu.
- PMR-20260612-002: _merge_close_hotspots sarj duragini SOC yorungesini
  duzeltmeden dusuruyordu.
- PMR-20260612-003: Hotspot alignment drift toleransi 40 km sabitti; uzun
  rotalardaki mesru kaymalar 502 uretiyordu.
- PMR-20260612-004: 50 km ustu yaricaplarda Google Places cagrisi clamp
  nedeniyle ayni sonucu donduruyordu (test_phase1_fixes icinde guncellendi,
  burada dogrudan fetch seviyesinde kilitleniyor).
"""

import math
from types import SimpleNamespace

import polyline as polyline_lib
import pytest

from app.models import GeoPoint
from app.services.base_service import ExternalAPIError
from app.soc_simulator import ChargeHotspot, SOCSimulator


# =============================================================================
# PMR-20260612-001 — Adaptif polyline orneklemesi
# =============================================================================

class TestLongRoutePolylineSampling:
    @staticmethod
    def _build_l_shaped_route():
        """~1100 km dogu + ~300 km kuzey, 0.002 derece adimlarla yogun rota."""
        coords = []
        lat = 38.0
        lon = 27.0
        # Dogu bacagi: lon 27.0 -> 39.5 (~1095 km @ lat 38)
        steps_east = int((39.5 - 27.0) / 0.002)
        for i in range(steps_east + 1):
            coords.append((lat, round(27.0 + i * 0.002, 6)))
        # Kuzey bacagi: lat 38.0 -> 40.7 (~300 km)
        steps_north = int((40.7 - 38.0) / 0.002)
        for i in range(1, steps_north + 1):
            coords.append((round(38.0 + i * 0.002, 6), 39.5))
        return coords

    def test_sampling_covers_full_route_beyond_1000km(self):
        from app.services.station_logic.polyline_filter import (
            _POLYLINE_MAX_SAMPLES,
            decode_route_polyline_coords,
        )

        coords = self._build_l_shaped_route()
        encoded = polyline_lib.encode(coords)

        sampled = decode_route_polyline_coords(encoded)

        # Ornek sayisi sinirin makul ustunde patlamamali
        assert len(sampled) <= _POLYLINE_MAX_SAMPLES + 5

        # Kuzey bacagi (km ~1100 sonrasi) orneklemede temsil edilmeli:
        # kose ile varis arasinda ara nokta olmali (eski kod tek kiris birakiyordu).
        north_leg_samples = [
            (lat, lon) for lat, lon in sampled
            if abs(lon - 39.5) < 0.01 and 38.1 < lat < 40.6
        ]
        assert len(north_leg_samples) > 10

        # Varis noktasi korunmali
        assert sampled[-1] == coords[-1]

    def test_station_after_km_1100_is_accepted_on_long_route(self):
        from app.services.station_logic.polyline_filter import (
            check_stations_on_polyline,
            decode_route_polyline_coords,
            min_distance_to_polyline_km,
        )

        coords = self._build_l_shaped_route()
        encoded = polyline_lib.encode(coords)
        sampled = decode_route_polyline_coords(encoded)

        # Kuzey bacaginin ortasinda, yolun tam ustunde istasyon (km ~1210)
        station = (39.0, 39.5)
        distance = min_distance_to_polyline_km(station[0], station[1], sampled)
        flags = check_stations_on_polyline([station], sampled, max_perp_km=1.0)

        assert distance < 1.0
        assert flags == [True]


# =============================================================================
# PMR-20260612-002 — Fizibilite-farkindali hotspot merge
# =============================================================================

def _hotspot(km: float, soc: float, target: float = 80.0) -> ChargeHotspot:
    return ChargeHotspot(
        segment_index=0,
        location=GeoPoint(lat=39.0, lon=32.0),
        soc_at_point=soc,
        distance_from_start_km=km,
        remaining_distance_km=max(0.0, 1000.0 - km),
        min_required_soc=10.0,
        recommended_charge_to=target,
    )


class TestFeasibilityAwareHotspotMerge:
    @staticmethod
    def _simulator(target_arrival_soc: float = 15.0) -> SOCSimulator:
        return SOCSimulator(
            battery_capacity_kwh=60.0,
            start_soc=80.0,
            target_arrival_soc=target_arrival_soc,
            charge_min_soc=20.0,
            charge_target_soc=80.0,
        )

    def test_safe_merge_corrects_downstream_soc(self):
        sim = self._simulator()
        hotspots = [
            _hotspot(200.0, soc=18.0, target=80.0),
            _hotspot(260.0, soc=65.0, target=80.0),  # 60 km sonra — merge adayi
            _hotspot(500.0, soc=25.0, target=80.0),
        ]

        merged, debt = sim._merge_close_hotspots(hotspots, final_soc=40.0)

        assert [h.distance_from_start_km for h in merged] == [200.0, 500.0]
        # Dusurulen duragin sarj borcu (80 - 65 = 15) sonraki duragin
        # varis SOC'una yansitilmali: 25 - 15 = 10.
        assert merged[-1].soc_at_point == pytest.approx(10.0)
        # Borc bir sonraki durakta (sarj target'a resetledigi icin) kapandi.
        assert debt == pytest.approx(0.0)

    def test_unsafe_merge_is_cancelled_when_next_checkpoint_drops_below_floor(self):
        sim = self._simulator()
        hotspots = [
            _hotspot(200.0, soc=18.0, target=80.0),
            _hotspot(260.0, soc=65.0, target=80.0),
            _hotspot(500.0, soc=20.0, target=80.0),  # 20 - 15 = 5 < %10 taban
        ]

        merged, debt = sim._merge_close_hotspots(hotspots, final_soc=40.0)

        # Merge iptal: uc durak da korunur, SOC degerleri degismez.
        assert [h.distance_from_start_km for h in merged] == [200.0, 260.0, 500.0]
        assert [h.soc_at_point for h in merged] == [18.0, 65.0, 20.0]
        assert debt == pytest.approx(0.0)

    def test_last_stop_merge_returns_final_soc_debt(self):
        sim = self._simulator(target_arrival_soc=15.0)
        hotspots = [
            _hotspot(200.0, soc=18.0, target=80.0),
            _hotspot(260.0, soc=65.0, target=80.0),  # son durak, merge adayi
        ]

        merged, debt = sim._merge_close_hotspots(hotspots, final_soc=60.0)

        # 60 - 15 = 45 >= target_arrival(15) -> merge gecerli; borc caller'a doner.
        assert [h.distance_from_start_km for h in merged] == [200.0]
        assert debt == pytest.approx(15.0)

    def test_last_stop_merge_cancelled_when_arrival_target_violated(self):
        sim = self._simulator(target_arrival_soc=15.0)
        hotspots = [
            _hotspot(200.0, soc=18.0, target=80.0),
            _hotspot(260.0, soc=65.0, target=80.0),
        ]

        merged, debt = sim._merge_close_hotspots(hotspots, final_soc=25.0)

        # 25 - 15 = 10 < target_arrival(15) -> merge iptal.
        assert len(merged) == 2
        assert debt == pytest.approx(0.0)


# =============================================================================
# PMR-20260612-003 — Oransal alignment drift toleransi
# =============================================================================

class TestAlignmentDriftTolerance:
    def test_tolerance_scales_with_route_distance(self):
        from app.route_planning.orchestrator import _alignment_drift_tolerance_km

        assert _alignment_drift_tolerance_km(1500.0) == pytest.approx(75.0)
        assert _alignment_drift_tolerance_km(300.0) == pytest.approx(40.0)
        assert _alignment_drift_tolerance_km(None) == pytest.approx(40.0)
        assert _alignment_drift_tolerance_km(0.0) == pytest.approx(40.0)

    @staticmethod
    def _station_result(hotspot_km: float) -> SimpleNamespace:
        return SimpleNamespace(
            hotspot=SimpleNamespace(distance_from_start_km=hotspot_km),
            best_station=None,
            stations=[],
        )

    def test_45km_drift_accepted_with_long_route_tolerance(self):
        from app.route_planning.orchestrator import _align_station_results_to_hotspots

        hotspots = [SimpleNamespace(distance_from_start_km=1045.0)]
        station_results = [self._station_result(1000.0), self._station_result(200.0)]

        aligned = _align_station_results_to_hotspots(
            hotspots,
            station_results,
            drift_tolerance_km=75.0,  # 1500 km rota icin oransal tolerans
        )

        assert len(aligned) == 1
        assert aligned[0].hotspot.distance_from_start_km == 1000.0

    def test_45km_drift_rejected_with_default_tolerance(self):
        from app.route_planning.orchestrator import _align_station_results_to_hotspots

        hotspots = [SimpleNamespace(distance_from_start_km=1045.0)]
        station_results = [self._station_result(1000.0), self._station_result(200.0)]

        with pytest.raises(ExternalAPIError):
            _align_station_results_to_hotspots(hotspots, station_results)


# =============================================================================
# PMR-20260612-004 — 50 km ustu yaricapta Google atlanir
# =============================================================================

class TestGoogleRadiusClampSkip:
    @pytest.mark.asyncio
    async def test_fetch_skips_google_beyond_50km_and_uses_ocm(self, monkeypatch):
        import app.station_finder as station_finder
        from app.station_finder import CorridorSearcher

        google_calls = []
        ocm_calls = []

        async def fake_google_search(*, lat, lon, radius_m, max_results):
            google_calls.append(radius_m)
            return []

        async def fake_ocm_search(*, lat, lon, radius_km):
            ocm_calls.append(radius_km)
            return [{
                "ID": 1,
                "AddressInfo": {"Title": "OCM DC", "Latitude": lat, "Longitude": lon},
                "Connections": [{"PowerKW": 150, "ConnectionType": {"Title": "CCS"}}],
                "StatusType": {"IsOperational": True},
            }]

        monkeypatch.setattr(
            station_finder.google_maps,
            "search_ev_charging_stations_new",
            fake_google_search,
        )
        monkeypatch.setattr(
            station_finder.ocm_service,
            "get_nearby_stations_raw",
            fake_ocm_search,
        )

        searcher = CorridorSearcher(
            vehicle_model_id="test",
            corridor_length_km=50.0,
            vehicle_spec=SimpleNamespace(display_name="Test EV", connector_type="CCS"),
        )
        hotspot = SimpleNamespace(
            segment_index=1,
            location=GeoPoint(lat=40.0, lon=29.0),
            route_polyline_coords=None,
            soc_at_point=14.0,
            distance_from_start_km=533.0,
            remaining_distance_km=200.0,
        )

        stations, source = await searcher._fetch_stations_google_first(
            hotspot, search_radius_km=80
        )

        assert google_calls == []
        assert ocm_calls == [80]
        assert source == "ocm"
        assert len(stations) == 1

    @pytest.mark.asyncio
    async def test_fetch_still_calls_google_at_50km(self, monkeypatch):
        import app.station_finder as station_finder
        from app.station_finder import CorridorSearcher

        google_calls = []

        async def fake_google_search(*, lat, lon, radius_m, max_results):
            google_calls.append(radius_m)
            return [{
                "place_id": "g1",
                "name": "Google DC",
                "business_status": "OPERATIONAL",
                "geometry": {"location": {"lat": lat, "lng": lon}},
                "connector_count": 2,
                "max_power_kw": 150,
                "types": ["electric_vehicle_charging_station"],
            }]

        monkeypatch.setattr(
            station_finder.google_maps,
            "search_ev_charging_stations_new",
            fake_google_search,
        )

        searcher = CorridorSearcher(
            vehicle_model_id="test",
            corridor_length_km=50.0,
            vehicle_spec=SimpleNamespace(display_name="Test EV", connector_type="CCS"),
        )
        hotspot = SimpleNamespace(
            segment_index=1,
            location=GeoPoint(lat=40.0, lon=29.0),
            route_polyline_coords=None,
            soc_at_point=14.0,
            distance_from_start_km=533.0,
            remaining_distance_km=200.0,
        )

        stations, source = await searcher._fetch_stations_google_first(
            hotspot, search_radius_km=50
        )

        assert google_calls == [50_000]
        assert source == "google"
        assert len(stations) == 1
