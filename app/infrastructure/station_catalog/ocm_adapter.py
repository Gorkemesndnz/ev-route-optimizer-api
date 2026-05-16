"""
Station Catalog — OpenChargeMap (OCM) Adapter
================================================

OCM `/poi` raw response'unu kayıpsız NormalizedStation modeline çevirir.

OCM'de PowerKW alanı genelde dolu gelir; doluysa power_known=True ve
PowerSource.OCM. Eksikse Voltage * Amps fallback'i denenir, o da yoksa
power_known=False ile devam edilir.

Availability bilgisi OCM'de StatusTypeID üzerinden gelir:
  - 50  → Available    → available_count=1
  - 100 → Occupied     → available_count=0
  - 200 → OutOfOrder   → out_of_service_count=1
  - Diğer / yok        → None (unknown)
"""

from typing import Any, Dict, List, Optional

from app.infrastructure.station_catalog.constants import PROVIDER_OCM
from app.infrastructure.station_catalog.models import (
    NormalizedConnector,
    NormalizedStation,
    PowerSource,
)
from app.utils.geo import haversine_km


# OCM ConnectionTypeID -> plug ailesi (string)
_OCM_PLUG_TYPE_MAP: Dict[int, str] = {
    2: "CHADEMO",
    25: "TYPE2",
    1036: "TYPE2",
    33: "CCS2",
    27: "TESLA",
    30: "TESLA",
}


def _parse_ocm_plug_type(conn: Dict[str, Any]) -> str:
    raw_type = conn.get("ConnectionTypeID")
    type_id: Optional[int]
    if isinstance(raw_type, dict):
        type_id = raw_type.get("ID")
    else:
        type_id = raw_type

    if type_id in _OCM_PLUG_TYPE_MAP:
        return _OCM_PLUG_TYPE_MAP[type_id]

    title = ((conn.get("ConnectionType") or {}).get("Title") or "").lower()
    if "type 2" in title:
        return "TYPE2"
    if "ccs" in title:
        return "CCS2"
    if "chademo" in title:
        return "CHADEMO"
    if "tesla" in title:
        return "TESLA"
    return "UNKNOWN"


def _parse_ocm_power(conn: Dict[str, Any]) -> tuple[Optional[float], bool, PowerSource]:
    """
    OCM Connection objesinden (power_kw, power_known, power_source) üretir.
    PowerKW yoksa Voltage*Amps fallback'i denenir.
    """
    raw_kw = conn.get("PowerKW")
    if raw_kw is not None and float(raw_kw) > 0:
        return float(raw_kw), True, PowerSource.OCM

    voltage = conn.get("Voltage")
    amps = conn.get("Amps")
    if voltage and amps:
        derived = (float(voltage) * float(amps)) / 1000.0
        if derived > 0:
            return derived, True, PowerSource.OCM

    return None, False, PowerSource.UNKNOWN


def _parse_ocm_availability(conn: Dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    """
    OCM StatusTypeID → (available_count, out_of_service_count).

    OCM connector başına status verir; bizim NormalizedConnector "count" temellidir
    ama veri çoğunlukla connector başına 1 kayıt olduğu için 0/1 değeri yeterlidir.
    """
    raw_status = conn.get("StatusTypeID")
    status_id: Optional[int]
    if isinstance(raw_status, dict):
        status_id = raw_status.get("ID")
    else:
        status_id = raw_status

    if status_id == 50:
        return 1, 0
    if status_id == 100:
        return 0, 0
    if status_id == 200:
        return 0, 1
    # Bilinmiyor
    return None, None


def parse_ocm_connector(conn: Dict[str, Any]) -> NormalizedConnector:
    """OCM Connections[i] → NormalizedConnector."""
    power_kw, power_known, power_source = _parse_ocm_power(conn)
    available_count, out_of_service_count = _parse_ocm_availability(conn)

    return NormalizedConnector(
        plug_type=_parse_ocm_plug_type(conn),
        power_kw=power_kw,
        power_known=power_known,
        power_source=power_source,
        count=int(conn.get("Quantity") or 1),
        available_count=available_count,
        out_of_service_count=out_of_service_count,
        availability_last_update_time=None,  # OCM availability timestamp vermez
    )


def parse_ocm_station(raw: Dict[str, Any]) -> Optional[NormalizedStation]:
    """OCM /poi response item → NormalizedStation. Lokasyonu yoksa None döner."""
    address_info = raw.get("AddressInfo") or {}
    lat = address_info.get("Latitude")
    lon = address_info.get("Longitude")

    if lat is None or lon is None:
        return None

    connections = raw.get("Connections") or []
    connectors = [parse_ocm_connector(c) for c in connections]

    operator_info = raw.get("OperatorInfo") or {}

    return NormalizedStation(
        source_provider=PROVIDER_OCM,
        source_id=str(raw.get("ID") or ""),
        place_id=None,  # OCM'de Google place_id yok
        name=address_info.get("Title") or "Unknown Station",
        lat=float(lat),
        lon=float(lon),
        address=address_info.get("AddressLine1"),
        connectors=connectors,
        rating=None,  # OCM rating vermez
        user_ratings_total=None,
        business_status="OPERATIONAL"
        if (raw.get("StatusType") or {}).get("IsOperational", True)
        else "CLOSED_TEMPORARILY",
        types=[],
    )


def parse_ocm_stations(raw_list: List[Dict[str, Any]]) -> List[NormalizedStation]:
    """Liste OCM POI → NormalizedStation listesi."""
    out: List[NormalizedStation] = []
    for raw in raw_list:
        station = parse_ocm_station(raw)
        if station is not None:
            out.append(station)
    return out


# =============================================================================
# Enrichment: Google unknown kW istasyona OCM'den kW yapıştır
# =============================================================================

def enrich_unknown_power_from_ocm(
    google_stations: List[NormalizedStation],
    ocm_stations: List[NormalizedStation],
    radius_km: float = 0.2,
) -> List[NormalizedStation]:
    """
    Google istasyonlarından power_known=False olanlara, yakındaki (≤200m)
    OCM istasyonun bilinen kW'sini yapıştırır.

    Yapıştırma sırasında ilgili konektörlerin power_source alanı
    `OCM_CROSSREF` olarak işaretlenir; böylece response'ta veri kaynağı
    izlenebilir.

    Bu fonksiyon yeni liste üretmez; girişteki NormalizedStation nesnelerini
    yerinde günceller ve aynı listeyi geri döner.
    """
    if not google_stations or not ocm_stations:
        return google_stations

    # OCM istasyonlarından (lat, lon, max_kW) tablosu üret
    ocm_index: List[tuple[float, float, float]] = []
    for station in ocm_stations:
        max_kw = station.max_power_kw
        if max_kw is None or max_kw <= 0:
            continue
        ocm_index.append((station.lat, station.lon, max_kw))

    if not ocm_index:
        return google_stations

    for g_station in google_stations:
        if g_station.power_known:
            continue

        # En yakın OCM istasyonu bul
        match_kw: Optional[float] = None
        for ocm_lat, ocm_lon, ocm_kw in ocm_index:
            if haversine_km(g_station.lat, g_station.lon, ocm_lat, ocm_lon) <= radius_km:
                match_kw = ocm_kw
                break

        if match_kw is None:
            continue

        # Tüm bilinmeyen konektörleri OCM crossref ile doldur
        for connector in g_station.connectors:
            if connector.power_known:
                continue
            connector.power_kw = match_kw
            connector.power_known = True
            connector.power_source = PowerSource.OCM_CROSSREF

    return google_stations
