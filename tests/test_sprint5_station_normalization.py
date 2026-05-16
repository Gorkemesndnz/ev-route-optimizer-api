"""
Sprint 5 — Station Normalization Tests
========================================

docs/archive/Yapilacaklar.md §17.8 (Sprint 5) kabul kriterleri:

  - Google `connectorAggregation` -> NormalizedConnector mapping kayıpsız
  - OCM `Connections` -> NormalizedConnector mapping
  - Unknown kW istasyon adayda kalır, power_known=False, planning 50 kW
  - Availability missing istasyon elenmez, availability_status='unknown'
  - Tüm soketler out-of-service ise availability_status='unavailable'
  - OCM enrichment: Google unknown kW + OCM kW yakın → power_source='ocm_crossref'
  - Provider id'leri korunur (Google place_id, OCM kendi ID'si)
"""

from __future__ import annotations

from app.infrastructure.station_catalog import (
    UNKNOWN_DC_POWER_PLANNING_KW,
    UNKNOWN_POWER_PLANNING_KW,
    AvailabilityStatus,
    NormalizedConnector,
    NormalizedStation,
    PowerSource,
    enrich_unknown_power_from_ocm,
    parse_google_place,
    parse_google_places,
    parse_ocm_station,
    parse_ocm_stations,
)


# =============================================================================
# A) Google connectorAggregation kayıpsız mapping
# =============================================================================

class TestGoogleConnectorAggregation:
    def test_full_connector_aggregation_lossless(self):
        raw = {
            "id": "ChIJ_full",
            "displayName": {"text": "Full Station"},
            "location": {"latitude": 40.0, "longitude": 30.0},
            "evChargeOptions": {
                "connectorCount": 6,
                "connectorAggregation": [
                    {
                        "type": "EV_CONNECTOR_TYPE_CCS_COMBO_2",
                        "maxChargeRateKw": 180,
                        "count": 4,
                        "availableCount": 2,
                        "outOfServiceCount": 0,
                        "availabilityLastUpdateTime": "2024-05-01T10:00:00Z",
                    },
                    {
                        "type": "EV_CONNECTOR_TYPE_CHADEMO",
                        "maxChargeRateKw": 50,
                        "count": 2,
                        "availableCount": 0,
                        "outOfServiceCount": 0,
                    },
                ],
            },
            "businessStatus": "OPERATIONAL",
            "rating": 4.5,
            "userRatingCount": 120,
        }

        station = parse_google_place(raw)

        assert station.source_provider == "google"
        assert station.source_id == "ChIJ_full"
        assert station.place_id == "ChIJ_full"
        assert len(station.connectors) == 2

        ccs = station.connectors[0]
        assert ccs.plug_type == "CCS2"
        assert ccs.power_kw == 180.0
        assert ccs.power_known is True
        assert ccs.power_source == PowerSource.GOOGLE_PLACES
        assert ccs.count == 4
        assert ccs.available_count == 2
        assert ccs.out_of_service_count == 0
        assert ccs.availability_last_update_time == "2024-05-01T10:00:00Z"

        chademo = station.connectors[1]
        assert chademo.plug_type == "CHADEMO"
        assert chademo.power_kw == 50.0
        assert chademo.available_count == 0

        # Station-level
        assert station.max_power_kw == 180.0
        assert station.power_known is True
        assert station.planning_power_kw == 180.0
        # En az bir konektörde available_count > 0 → AVAILABLE
        assert station.availability_status == AvailabilityStatus.AVAILABLE
        assert station.total_available_count == 2
        assert station.rating == 4.5
        assert station.user_ratings_total == 120

    def test_unknown_kw_preserves_status_and_uses_planning_fallback(self):
        raw = {
            "id": "ChIJ_no_kw",
            "displayName": {"text": "No kW Station"},
            "location": {"latitude": 40.0, "longitude": 30.0},
            "evChargeOptions": {
                "connectorAggregation": [
                    {"type": "EV_CONNECTOR_TYPE_CCS_COMBO_2", "count": 1}
                ]
            },
        }
        station = parse_google_place(raw)
        assert station.power_known is False
        assert station.max_power_kw is None
        assert station.planning_power_kw == UNKNOWN_DC_POWER_PLANNING_KW

    def test_availability_unknown_when_no_counts(self):
        raw = {
            "id": "ChIJ_no_avail",
            "displayName": {"text": "No Availability"},
            "location": {"latitude": 40.0, "longitude": 30.0},
            "evChargeOptions": {
                "connectorAggregation": [
                    {
                        "type": "EV_CONNECTOR_TYPE_CCS_COMBO_2",
                        "maxChargeRateKw": 100,
                        "count": 2,
                    }
                ]
            },
        }
        station = parse_google_place(raw)
        assert station.availability_status == AvailabilityStatus.UNKNOWN
        assert station.total_available_count is None

    def test_availability_unavailable_when_all_out_of_service(self):
        raw = {
            "id": "ChIJ_all_oos",
            "displayName": {"text": "All Out Of Service"},
            "location": {"latitude": 40.0, "longitude": 30.0},
            "evChargeOptions": {
                "connectorAggregation": [
                    {
                        "type": "EV_CONNECTOR_TYPE_CCS_COMBO_2",
                        "maxChargeRateKw": 150,
                        "count": 2,
                        "availableCount": 0,
                        "outOfServiceCount": 2,
                    }
                ]
            },
        }
        station = parse_google_place(raw)
        assert station.availability_status == AvailabilityStatus.UNAVAILABLE

    def test_legacy_converter_format_supported(self):
        """search_ev_charging_stations_new legacy çıktısı da parse edilir."""
        legacy = {
            "place_id": "ChIJ_legacy",
            "name": "Legacy Station",
            "geometry": {"location": {"lat": 40.0, "lng": 30.0}},
            "rating": 4.0,
            "user_ratings_total": 10,
            "business_status": "OPERATIONAL",
            "vicinity": "Some Address",
            "ev_charge_options": {
                "connectorCount": 2,
                "connectorAggregation": [
                    {
                        "type": "EV_CONNECTOR_TYPE_CCS_COMBO_2",
                        "maxChargeRateKw": 150,
                        "count": 2,
                        "availableCount": 1,
                    }
                ],
            },
        }
        station = parse_google_place(legacy)
        assert station.place_id == "ChIJ_legacy"
        assert station.max_power_kw == 150.0
        assert station.connectors[0].available_count == 1


# =============================================================================
# B) OCM Connections kayıpsız mapping
# =============================================================================

class TestOcmAdapter:
    def test_ocm_connection_with_powerkw(self):
        raw = {
            "ID": 12345,
            "AddressInfo": {
                "Title": "OCM Station",
                "Latitude": 41.0,
                "Longitude": 29.0,
                "AddressLine1": "Some Road",
            },
            "Connections": [
                {
                    "ConnectionTypeID": 33,  # CCS2
                    "PowerKW": 175,
                    "StatusTypeID": 50,
                    "Quantity": 2,
                }
            ],
            "StatusType": {"IsOperational": True},
        }
        station = parse_ocm_station(raw)
        assert station is not None
        assert station.source_provider == "ocm"
        assert station.source_id == "12345"
        assert station.place_id is None
        assert len(station.connectors) == 1
        c = station.connectors[0]
        assert c.plug_type == "CCS2"
        assert c.power_kw == 175.0
        assert c.power_known is True
        assert c.power_source == PowerSource.OCM
        assert c.available_count == 1  # status 50 → 1
        assert c.out_of_service_count == 0
        assert station.availability_status == AvailabilityStatus.AVAILABLE

    def test_ocm_voltage_amps_fallback(self):
        raw = {
            "ID": 9999,
            "AddressInfo": {"Title": "VA", "Latitude": 41.0, "Longitude": 29.0},
            "Connections": [
                {"ConnectionTypeID": 33, "Voltage": 400, "Amps": 125}
            ],
        }
        station = parse_ocm_station(raw)
        assert station is not None
        c = station.connectors[0]
        assert c.power_known is True
        assert c.power_kw == 50.0  # 400 * 125 / 1000

    def test_ocm_status_out_of_order_marks_oos(self):
        raw = {
            "ID": 7,
            "AddressInfo": {"Title": "OOS", "Latitude": 41.0, "Longitude": 29.0},
            "Connections": [
                {"ConnectionTypeID": 33, "PowerKW": 150, "StatusTypeID": 200}
            ],
        }
        station = parse_ocm_station(raw)
        assert station is not None
        c = station.connectors[0]
        assert c.available_count == 0
        assert c.out_of_service_count == 1
        assert station.availability_status == AvailabilityStatus.UNAVAILABLE

    def test_ocm_missing_address_returns_none(self):
        raw = {"ID": 1, "AddressInfo": {}, "Connections": []}
        assert parse_ocm_station(raw) is None


# =============================================================================
# C) OCM enrichment: Google unknown kW + nearby OCM kW
# =============================================================================

class TestOcmEnrichment:
    def test_enrichment_fills_unknown_kw_from_ocm(self):
        google_unknown = NormalizedStation(
            source_provider="google",
            source_id="ChIJ_g1",
            place_id="ChIJ_g1",
            name="Google Unknown",
            lat=41.000,
            lon=29.000,
            address=None,
            connectors=[
                NormalizedConnector(
                    plug_type="CCS2",
                    power_kw=None,
                    power_known=False,
                    power_source=PowerSource.UNKNOWN,
                    count=1,
                )
            ],
        )
        ocm_known = NormalizedStation(
            source_provider="ocm",
            source_id="11111",
            place_id=None,
            name="OCM Known",
            lat=41.0008,  # ~88m
            lon=29.0008,
            address=None,
            connectors=[
                NormalizedConnector(
                    plug_type="CCS2",
                    power_kw=180.0,
                    power_known=True,
                    power_source=PowerSource.OCM,
                    count=2,
                )
            ],
        )

        enriched = enrich_unknown_power_from_ocm(
            [google_unknown], [ocm_known], radius_km=0.2
        )
        assert enriched[0].power_known is True
        assert enriched[0].max_power_kw == 180.0
        assert enriched[0].connectors[0].power_source == PowerSource.OCM_CROSSREF

    def test_enrichment_does_not_touch_known_power(self):
        google_known = NormalizedStation(
            source_provider="google",
            source_id="ChIJ_g2",
            place_id="ChIJ_g2",
            name="Google Known",
            lat=41.0,
            lon=29.0,
            address=None,
            connectors=[
                NormalizedConnector(
                    plug_type="CCS2",
                    power_kw=120.0,
                    power_known=True,
                    power_source=PowerSource.GOOGLE_PLACES,
                    count=2,
                )
            ],
        )
        ocm_other = NormalizedStation(
            source_provider="ocm",
            source_id="2",
            place_id=None,
            name="OCM",
            lat=41.0001,
            lon=29.0001,
            address=None,
            connectors=[
                NormalizedConnector(
                    plug_type="CCS2",
                    power_kw=200.0,
                    power_known=True,
                    power_source=PowerSource.OCM,
                    count=1,
                )
            ],
        )

        enriched = enrich_unknown_power_from_ocm([google_known], [ocm_other])
        assert enriched[0].connectors[0].power_kw == 120.0  # değişmedi
        assert enriched[0].connectors[0].power_source == PowerSource.GOOGLE_PLACES

    def test_enrichment_skips_when_no_nearby_ocm(self):
        google_unknown = NormalizedStation(
            source_provider="google",
            source_id="ChIJ_far",
            place_id="ChIJ_far",
            name="Far Google",
            lat=41.0,
            lon=29.0,
            address=None,
            connectors=[
                NormalizedConnector(
                    plug_type="CCS2",
                    power_kw=None,
                    power_known=False,
                    power_source=PowerSource.UNKNOWN,
                    count=1,
                )
            ],
        )
        ocm_far = NormalizedStation(
            source_provider="ocm",
            source_id="3",
            place_id=None,
            name="Far OCM",
            lat=41.5,  # ~50 km uzakta
            lon=29.5,
            address=None,
            connectors=[
                NormalizedConnector(
                    plug_type="CCS2",
                    power_kw=150.0,
                    power_known=True,
                    power_source=PowerSource.OCM,
                    count=1,
                )
            ],
        )
        enriched = enrich_unknown_power_from_ocm([google_unknown], [ocm_far])
        assert enriched[0].power_known is False
        assert enriched[0].planning_power_kw == UNKNOWN_DC_POWER_PLANNING_KW


# =============================================================================
# D) Provider id korunması
# =============================================================================

class TestProviderIdPreservation:
    def test_google_place_id_preserved_in_source_id_and_place_id(self):
        raw = {
            "id": "ChIJ_keep_me",
            "displayName": {"text": "X"},
            "location": {"latitude": 0.0, "longitude": 0.0},
            "evChargeOptions": {"connectorAggregation": []},
        }
        station = parse_google_place(raw)
        assert station.place_id == "ChIJ_keep_me"
        assert station.source_id == "ChIJ_keep_me"
        assert station.source_provider == "google"

    def test_ocm_id_preserved_no_place_id(self):
        raw = {
            "ID": 42,
            "AddressInfo": {"Title": "X", "Latitude": 0.0, "Longitude": 0.0},
            "Connections": [
                {"ConnectionTypeID": 33, "PowerKW": 50}
            ],
        }
        station = parse_ocm_station(raw)
        assert station is not None
        assert station.source_id == "42"
        assert station.place_id is None
        assert station.source_provider == "ocm"


# =============================================================================
# E) Bulk parsing
# =============================================================================

class TestBulkParsing:
    def test_parse_google_places_batch(self):
        items = [
            {
                "id": f"ChIJ_{i}",
                "displayName": {"text": f"S{i}"},
                "location": {"latitude": 0.0, "longitude": 0.0},
                "evChargeOptions": {
                    "connectorAggregation": [
                        {
                            "type": "EV_CONNECTOR_TYPE_CCS_COMBO_2",
                            "maxChargeRateKw": 100 + i,
                            "count": 1,
                        }
                    ]
                },
            }
            for i in range(3)
        ]
        stations = parse_google_places(items)
        assert len(stations) == 3
        assert stations[0].max_power_kw == 100.0
        assert stations[2].max_power_kw == 102.0

    def test_parse_ocm_stations_batch_skips_invalid(self):
        items = [
            {  # geçerli
                "ID": 1,
                "AddressInfo": {"Title": "A", "Latitude": 41.0, "Longitude": 29.0},
                "Connections": [{"ConnectionTypeID": 33, "PowerKW": 100}],
            },
            {  # geçersiz: lokasyon yok
                "ID": 2,
                "AddressInfo": {},
                "Connections": [],
            },
        ]
        stations = parse_ocm_stations(items)
        assert len(stations) == 1
        assert stations[0].source_id == "1"
