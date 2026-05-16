"""
Station Catalog — Google Places Adapter
=========================================

Google Places (New) `evChargeOptions` raw response'unu kayıpsız NormalizedStation
modeline çevirir.

`maxChargeRateKw` yoksa → `power_known=False` (atılmaz, konservatif planlanır).
`availableCount` yoksa → `availability_status=unknown` (skor penalty alır).
"""

from typing import Any, Dict, List, Optional

from app.infrastructure.station_catalog.constants import PROVIDER_GOOGLE
from app.infrastructure.station_catalog.models import (
    NormalizedConnector,
    NormalizedStation,
    PowerSource,
)


# Google connectorAggregation 'type' alanı → bizim plug_type string'imiz
# Google enum literal'leri: https://developers.google.com/maps/documentation/places/web-service/data-fields
_PLUG_TYPE_MAP: Dict[str, str] = {
    "EV_CONNECTOR_TYPE_CCS_COMBO_2": "CCS2",
    "EV_CONNECTOR_TYPE_CCS_COMBO_1": "CCS1",
    "EV_CONNECTOR_TYPE_CHADEMO": "CHADEMO",
    "EV_CONNECTOR_TYPE_TYPE_2": "TYPE2",
    "EV_CONNECTOR_TYPE_TESLA": "TESLA",
    "EV_CONNECTOR_TYPE_J1772": "J1772",
    "EV_CONNECTOR_TYPE_OTHER": "OTHER",
    "EV_CONNECTOR_TYPE_UNSPECIFIED": "UNKNOWN",
}


def _parse_plug_type(google_type: Optional[str]) -> str:
    if not google_type:
        return "UNKNOWN"
    return _PLUG_TYPE_MAP.get(google_type, "UNKNOWN")


def parse_google_connector(raw: Dict[str, Any]) -> NormalizedConnector:
    """
    Tek bir `connectorAggregation` element'ini NormalizedConnector'a çevirir.

    raw örneği:
        {
          "type": "EV_CONNECTOR_TYPE_CCS_COMBO_2",
          "maxChargeRateKw": 150,
          "count": 4,
          "availableCount": 2,
          "outOfServiceCount": 0,
          "availabilityLastUpdateTime": "2024-01-15T10:30:00Z"
        }
    """
    raw_kw = raw.get("maxChargeRateKw")
    has_kw = raw_kw is not None

    return NormalizedConnector(
        plug_type=_parse_plug_type(raw.get("type")),
        power_kw=float(raw_kw) if has_kw else None,
        power_known=has_kw,
        power_source=PowerSource.GOOGLE_PLACES if has_kw else PowerSource.UNKNOWN,
        count=int(raw.get("count") or 1),
        available_count=raw.get("availableCount"),
        out_of_service_count=raw.get("outOfServiceCount"),
        availability_last_update_time=raw.get("availabilityLastUpdateTime"),
    )


def parse_google_place(raw: Dict[str, Any]) -> NormalizedStation:
    """
    Bir Google Places (New) `place` objesini NormalizedStation'a çevirir.

    raw, `places.searchNearby` response'unun `places[i]` elemanı şeklinde olabilir
    veya legacy converter çıktısı (`{"place_id":..., "geometry":..., ev_charge_options:...}`)
    şeklinde olabilir; her iki şekli de tolere eder.
    """
    # 1) Google Places (New) ham formatı
    if "evChargeOptions" in raw or "displayName" in raw:
        place_id = raw.get("id") or raw.get("place_id") or ""
        display_name = raw.get("displayName") or {}
        if isinstance(display_name, dict):
            name = display_name.get("text") or ""
        else:
            name = str(display_name)

        location = raw.get("location") or {}
        lat = float(location.get("latitude") or location.get("lat") or 0.0)
        lon = float(location.get("longitude") or location.get("lng") or 0.0)

        ev_options = raw.get("evChargeOptions") or {}
        connector_aggregation = ev_options.get("connectorAggregation") or []

        connectors = [parse_google_connector(c) for c in connector_aggregation]

        return NormalizedStation(
            source_provider=PROVIDER_GOOGLE,
            source_id=str(place_id),
            place_id=str(place_id) if place_id else None,
            name=name or "Unknown Station",
            lat=lat,
            lon=lon,
            address=raw.get("formattedAddress"),
            connectors=connectors,
            rating=raw.get("rating"),
            user_ratings_total=raw.get("userRatingCount"),
            business_status=raw.get("businessStatus"),
            types=list(raw.get("types") or []),
        )

    # 2) Legacy converter formatı (search_ev_charging_stations_new çıktısı)
    place_id = raw.get("place_id") or ""
    geometry = raw.get("geometry") or {}
    location = geometry.get("location") or {}
    lat = float(location.get("lat") or 0.0)
    lon = float(location.get("lng") or 0.0)

    ev_options = raw.get("ev_charge_options") or {}
    connector_aggregation = ev_options.get("connectorAggregation") or []
    connectors = [parse_google_connector(c) for c in connector_aggregation]

    # Eğer connectorAggregation legacy converter tarafından silinmişse,
    # max_power_kw alanından tek konektörlük fallback üret
    if not connectors:
        max_kw = raw.get("max_power_kw")
        connector_count = raw.get("connector_count") or 1
        has_kw = max_kw is not None and float(max_kw) > 0
        connectors = [
            NormalizedConnector(
                plug_type="CCS2",
                power_kw=float(max_kw) if has_kw else None,
                power_known=has_kw,
                power_source=PowerSource.GOOGLE_PLACES if has_kw else PowerSource.UNKNOWN,
                count=int(connector_count),
            )
        ]

    return NormalizedStation(
        source_provider=PROVIDER_GOOGLE,
        source_id=str(place_id),
        place_id=str(place_id) if place_id else None,
        name=raw.get("name") or "Unknown Station",
        lat=lat,
        lon=lon,
        address=raw.get("vicinity") or raw.get("formatted_address"),
        connectors=connectors,
        rating=raw.get("rating"),
        user_ratings_total=raw.get("user_ratings_total"),
        business_status=raw.get("business_status"),
        types=list(raw.get("types") or []),
    )


def parse_google_places(raw_list: List[Dict[str, Any]]) -> List[NormalizedStation]:
    """Bir liste Google place objesini NormalizedStation listesine çevirir."""
    return [parse_google_place(p) for p in raw_list]
