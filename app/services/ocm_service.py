from typing import List, Optional, Dict, Any
from app.services.base_service import BaseService, ExternalAPIError
from app.utils.config_manager import config
from app.utils.cache_manager import cacheable
from app.utils.logger import get_logger
from app.models import (
    StationInfo,
    GeoPoint,
    ConnectorInfo,
    StationAmenity,
    PlugType,
    ChargerType,
)

logger = get_logger("OCMService")


class OCMService(BaseService):
    """
    Open Charge Map (OCM) API ile konuşan servis.
    Ham JSON'u bizim domain modellerimize (StationInfo, ConnectorInfo vs.)
    dönüştürmekten sorumlu.
    """

    # OCM ConnectionTypeID -> Bizim PlugType Enum eşleşmesi
    TYPE_MAPPING = {
        2: PlugType.CHADEMO,      # CHAdeMO
        25: PlugType.TYPE2,       # Type 2 (Socket Only)
        1036: PlugType.TYPE2,     # Type 2 (Tethered)
        33: PlugType.CCS2,        # CCS (Type 2 Combo)
        27: PlugType.TESLA,       # Tesla Supercharger
        30: PlugType.TESLA,       # Tesla (Destination gibi)
    }

    def __init__(self):
        super().__init__(base_url="https://api.openchargemap.io/v3")
        self.api_key = config.get_ocm_api_key()

    # ============================================================
    # 1) Public API – Nearby Stations
    # ============================================================
    @cacheable(prefix="ocm_nearby", ttl_seconds=3600)
    async def get_nearby_stations(
        self,
        lat: float,
        lon: float,
        radius_km: float = 10.0,
        max_results: int = 50,
        min_power_kw: Optional[float] = None,
    ) -> List[StationInfo]:
        """
        Belirli bir koordinatın etrafındaki istasyonları getirir ve
        StationInfo listesi olarak döner.
        """

        params = {
            "latitude": lat,
            "longitude": lon,
            "distance": radius_km,
            "distanceunit": "KM",
            "maxresults": max_results,
            "compact": False,   # Detaylı veri
            "verbose": False,
            "key": self.api_key,
        }

        try:
            raw_list = await self.request(
                method="GET",
                endpoint="/poi",
                params=params,
            )
        except ExternalAPIError as e:
            logger.error(
                "OCM request failed",
                lat=lat,
                lon=lon,
                radius_km=radius_km,
                error=e.detail,
            )
            return []

        if not raw_list:
            return []

        stations: List[StationInfo] = []

        for raw in raw_list:
            try:
                station = self._map_to_station_model(raw, min_power_kw=min_power_kw)
                if station:
                    stations.append(station)
            except Exception as e:
                logger.warning(
                    "Station parse error",
                    station_id=raw.get("ID"),
                    error=str(e),
                )
                continue

        return stations

    # ============================================================
    # 2) Mapping Helpers – JSON → Domain Model
    # ============================================================

    def _map_to_station_model(
        self,
        raw: Dict[str, Any],
        min_power_kw: Optional[float] = None,
    ) -> Optional[StationInfo]:
        """
        OCM'den gelen tek bir ham station JSON'unu StationInfo modeline çevirir.
        Kritik veri yoksa veya uygun soket yoksa None döner.
        """
        address_info = raw.get("AddressInfo", {}) or {}

        lat = address_info.get("Latitude")
        lon = address_info.get("Longitude")

        if lat is None or lon is None:
            return None

        # 1) Soketleri ayrıştır
        connections = raw.get("Connections", []) or []
        connectors: List[ConnectorInfo] = []

        for conn in connections:
            connector = self._map_to_connector(conn)
            if connector is None:
                continue

            # min_power_kw filtresi (backend tarafında)
            if min_power_kw is not None and connector.power_kw < min_power_kw:
                continue

            connectors.append(connector)

        # Hiç geçerli connector yoksa istasyonu eklemiyoruz
        if not connectors:
            return None

        # 2) Olanaklar (Amenities) – Basit metin analizi
        general_comments = (raw.get("GeneralComments") or "").lower()
        usage_title = (raw.get("UsageType", {}) or {}).get("Title", "").lower()

        amenities = StationAmenity(
            has_food=("restoran" in general_comments) or ("restaurant" in general_comments) or ("food" in general_comments),
            has_toilet=("toilet" in general_comments) or ("wc" in general_comments),
            has_shopping=("market" in general_comments) or ("shop" in general_comments),
            has_wifi=("wifi" in general_comments),
            is_24_7=("24/7" in general_comments)
            or ("24h" in general_comments)
            or ("24 hour" in general_comments)
            or (raw.get("StatusType", {}) or {}).get("IsOperational", True),
        )

        operator_title = (raw.get("OperatorInfo", {}) or {}).get("Title", "Unknown Operator")

        return StationInfo(
            id=str(raw.get("ID")),
            name=address_info.get("Title", "Unknown Station"),
            operator=operator_title,
            location=GeoPoint(
                lat=lat,
                lon=lon,
                address=address_info.get("AddressLine1"),
            ),
            rating=0.0,  # OCM rating vermiyor; Google Places ile zenginleştireceğiz
            user_ratings_total=None,
            connectors=connectors,
            amenities=amenities,
            distance_from_route_km=0.0,  # StationFinder dolduracak
        )

    def _map_to_connector(self, conn: Dict[str, Any]) -> Optional[ConnectorInfo]:
        """
        OCM Connection objesini bizim ConnectorInfo modeline çevirir.
        ConnectionTypeID hem int hem dict gelebileceği için güvenli parse edilir.
        """
        # ConnectionTypeID parse
        raw_type = conn.get("ConnectionTypeID")
        if isinstance(raw_type, dict):
            conn_type_id = raw_type.get("ID")
        else:
            conn_type_id = raw_type

        plug_type = self.TYPE_MAPPING.get(conn_type_id)

        # Eğer mapping tablomuzda yoksa, ConnectionType.Title üzerinden tahmin etmeye çalışalım
        if not plug_type:
            title = ((conn.get("ConnectionType") or {}).get("Title") or "").lower()
            if "type 2" in title:
                plug_type = PlugType.TYPE2
            elif "ccs" in title:
                plug_type = PlugType.CCS2
            elif "chademo" in title:
                plug_type = PlugType.CHADEMO
            elif "tesla" in title:
                plug_type = PlugType.TESLA
            else:
                # Tanımlayamadıysak şimdilik istasyonu es geçiyoruz
                return None

        # Güç bilgisi
        power_kw = conn.get("PowerKW")
        if power_kw is None:
            # PowerKW yoksa Voltage * Amps üzerinden tahmin etmeyi deneyebiliriz
            voltage = conn.get("Voltage")
            amps = conn.get("Amps")
            if voltage and amps:
                power_kw = (voltage * amps) / 1000.0
            else:
                power_kw = 0.0

        power_kw = float(power_kw)

        # AC / DC / HPC sınıflandırması
        charger_type = ChargerType.AC
        if power_kw >= 150:
            charger_type = ChargerType.HPC
        elif power_kw >= 24 or plug_type in (PlugType.CCS2, PlugType.CHADEMO, PlugType.TESLA):
            charger_type = ChargerType.DC

        # StatusTypeID parse
        raw_status = conn.get("StatusTypeID")
        if isinstance(raw_status, dict):
            status_id = raw_status.get("ID")
        else:
            status_id = raw_status

        status_str = "Unknown"
        if status_id == 50:
            status_str = "Available"
        elif status_id == 100:
            status_str = "Occupied"
        elif status_id == 200:
            status_str = "OutOfOrder"

        return ConnectorInfo(
            plug_type=plug_type,
            charger_type=charger_type,
            power_kw=power_kw,
            status=status_str,
            price_per_kwh=None,  # OCM genellikle fiyat vermez; operatör API'si gerek
            currency="TRY",
        )


# Tek instance
ocm_service = OCMService()
