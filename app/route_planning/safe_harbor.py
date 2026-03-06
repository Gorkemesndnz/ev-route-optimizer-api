"""
Safe Harbor (Güvenli Liman) Algoritması
========================================

Varış noktasında şarj istasyonu yoksa, aracın en yakın kurtarıcı
istasyonlara dönebilecek yeterli enerjiyle varmasını garanti eder.

V2: Her istasyon için bireysel SOC hesaplaması yapılır.
    Varsayılan olarak EN YAKIN istasyona göre minimum şarj belirlenir.
    Kullanıcı frontend'de istasyon seçerek hedef SOC'u değiştirebilir.

Adımlar:
  A) Varış noktası çevresinde (10km) istasyon taraması
  B) İstasyon yoksa kademeli yarıçapla en yakın 2-3 istasyonu bul
  C) HER istasyon için bireysel Ghost Leg tüketim hesaplaması
  D) En yakın istasyona göre dinamik minimum varış SOC'u belirle
     (kullanıcı daha uzak istasyon seçerse SOC otomatik güncellenir)

Kullanım:
    from app.route_planning.safe_harbor import calculate_safe_harbor_soc

    result = await calculate_safe_harbor_soc(
        destination=GeoPoint(lat=40.5, lon=32.1),
        vehicle=vehicle,
        battery_capacity_kwh=50.8,
    )
    if not result.is_destination_covered:
        # Varsayılan: en yakın istasyona göre
        arrival_soc = result.dynamic_min_arrival_soc
        # Kullanıcı seçimi: result.rescue_stations[i].required_arrival_soc
"""

from dataclasses import dataclass, field
from typing import Optional, List
from app.models import GeoPoint
from app.utils.logger import get_logger
from app.utils.geo import haversine_km
from app.constants import (
    SAFE_HARBOR_NEARBY_RADIUS_KM,
    SAFE_HARBOR_SEARCH_RADII_KM,
    SAFE_HARBOR_BUFFER_PERCENT,
    SAFE_HARBOR_MAX_RETURN_KM,
    SAFE_HARBOR_MIN_RESCUE_STATIONS,
    HARD_MIN_SOC,
)

logger = get_logger("safe_harbor")


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class RescueStation:
    """
    Kurtarıcı istasyon bilgisi — her istasyona özel SOC hesabı içerir.

    Frontend'de varış kartında gösterilecek bilgiler:
      - name, distance_km, max_power_kw, rating (istasyon bilgisi)
      - return_consumption_kwh (bu istasyona gitmek için tüketim)
      - return_soc_needed (bu istasyona gitmek için gereken SOC %)
      - required_arrival_soc (varışta bu kadar SOC olmalı ki bu istasyona gidebilsin)
      - is_selected (varsayılan seçili mi?)
    """
    name: str
    location: GeoPoint
    place_id: str
    distance_km: float              # Haversine mesafe (varıştan)
    max_power_kw: float = 0.0       # Şarj gücü
    rating: float = 0.0
    # --- Per-station SOC hesaplamaları ---
    route_distance_km: float = 0.0          # Gerçek rota mesafesi
    return_consumption_kwh: float = 0.0     # Bu istasyona dönüş tüketimi (kWh)
    return_soc_needed: float = 0.0          # Bu istasyona dönüş tüketimi (SOC %)
    required_arrival_soc: float = 0.0       # Varışta en az bu kadar SOC olmalı
    elevation_gain_m: float = 0.0           # Dönüş tırmanış
    elevation_loss_m: float = 0.0           # Dönüş iniş
    is_selected: bool = False               # Varsayılan seçili mi?


@dataclass
class SafeHarborResult:
    """
    Safe Harbor hesaplama sonucu.

    V2: Her istasyonun bireysel SOC gereksinimi hesaplanır.
        dynamic_min_arrival_soc = seçili istasyonun (varsayılan: en yakın) gereksinimi.

    Attributes:
        is_destination_covered: Varışta yeterli istasyon var mı?
        rescue_stations: 2-3 kurtarıcı istasyon (her biri kendi SOC hesabıyla)
        selected_station_index: Varsayılan seçili istasyon indeksi (en yakın = 0)
        dynamic_min_arrival_soc: Seçili istasyona göre minimum varış SOC
        search_radius_used_km: Hangi yarıçapta bulundu
        warning_message: Kullanıcıya gösterilecek uyarı (varsa)
    """
    is_destination_covered: bool
    rescue_stations: List[RescueStation] = field(default_factory=list)
    selected_station_index: int = 0
    dynamic_min_arrival_soc: float = 0.0
    search_radius_used_km: float = 0.0
    warning_message: Optional[str] = None

    @property
    def selected_station(self) -> Optional[RescueStation]:
        """Seçili kurtarıcı istasyonu döndürür."""
        if self.rescue_stations and 0 <= self.selected_station_index < len(self.rescue_stations):
            return self.rescue_stations[self.selected_station_index]
        return None

    @property
    def return_soc_needed(self) -> float:
        """Seçili istasyona dönüş SOC ihtiyacı."""
        s = self.selected_station
        return s.return_soc_needed if s else 0.0

    @property
    def return_consumption_kwh(self) -> float:
        """Seçili istasyona dönüş tüketimi (kWh)."""
        s = self.selected_station
        return s.return_consumption_kwh if s else 0.0

    @property
    def farthest_rescue_distance_km(self) -> float:
        """En uzak kurtarıcı mesafesi."""
        if self.rescue_stations:
            return self.rescue_stations[-1].route_distance_km
        return 0.0


# =============================================================================
# GHOST LEG CALCULATOR — Tek istasyon için dönüş tüketimi hesapla
# =============================================================================

async def _calculate_ghost_leg_for_station(
    destination: GeoPoint,
    station: RescueStation,
    vehicle,
    battery_capacity_kwh: float,
    passenger_count: int,
    extra_load_kg: float,
    child_count: int,
    temperature_celsius: float,
) -> RescueStation:
    """
    Bir kurtarıcı istasyon için Ghost Leg hesaplaması yapar.
    Station nesnesini güncelleyerek döner.
    """
    from app.services.google_service import google_maps
    from app.consumption_engine.main_calculator import calculate_segment_consumption_kwh

    ghost_distance_km = station.distance_km
    elev_gain = 0.0
    elev_loss = 0.0

    # Google Directions ile gerçek rota mesafesi + elevation
    try:
        ghost_route = await google_maps.get_route_alternatives(
            start=destination,
            end=station.location,
            alternatives=False,
        )

        if ghost_route and ghost_route.get("routes"):
            ghost_leg = ghost_route["routes"][0]["legs"][0]
            ghost_distance_km = ghost_leg["distance"]["value"] / 1000
            ghost_polyline = ghost_route["routes"][0].get(
                "overview_polyline", {}
            ).get("points", "")

            # Elevation verisi al
            if ghost_polyline:
                try:
                    elev_stats = await google_maps.get_elevation_stats(ghost_polyline)
                    elev_gain = elev_stats.get("gain_m", 0.0)
                    elev_loss = elev_stats.get("loss_m", 0.0)
                except Exception as e:
                    logger.warning(
                        f"Ghost Leg elevation failed for {station.name}: {e}"
                    )
        else:
            ghost_distance_km = station.distance_km * 1.3
    except Exception as e:
        ghost_distance_km = station.distance_km * 1.3
        logger.warning(f"Ghost Leg route failed for {station.name}: {e}")

    # Fizik motoru ile tüketim hesapla
    consumption_kwh = calculate_segment_consumption_kwh(
        vehicle=vehicle,
        segment_distance_km=ghost_distance_km,
        segment_elevation_gain_m=elev_gain,
        segment_elevation_loss_m=elev_loss,
        temperature_celsius=temperature_celsius,
        extra_load_kg=extra_load_kg,
        passenger_count=passenger_count,
        child_count=child_count,
    )

    # SOC hesapla
    return_soc = (consumption_kwh / battery_capacity_kwh) * 100
    required_arrival = return_soc + SAFE_HARBOR_BUFFER_PERCENT
    required_arrival = max(required_arrival, HARD_MIN_SOC)

    # Üst sınır
    if required_arrival > 60.0:
        required_arrival = 60.0

    # Station nesnesini güncelle
    station.route_distance_km = round(ghost_distance_km, 1)
    station.return_consumption_kwh = round(consumption_kwh, 2)
    station.return_soc_needed = round(return_soc, 1)
    station.required_arrival_soc = round(required_arrival, 1)
    station.elevation_gain_m = round(elev_gain, 1)
    station.elevation_loss_m = round(elev_loss, 1)

    logger.info(
        f"  Ghost Leg → {station.name}: "
        f"route={ghost_distance_km:.1f}km, "
        f"elev=+{elev_gain:.0f}/-{elev_loss:.0f}m, "
        f"consumption={consumption_kwh:.2f}kWh, "
        f"required_soc={required_arrival:.1f}%"
    )

    return station


# =============================================================================
# CORE ALGORITHM
# =============================================================================

async def calculate_safe_harbor_soc(
    destination: GeoPoint,
    vehicle,
    battery_capacity_kwh: float,
    passenger_count: int = 1,
    extra_load_kg: float = 0.0,
    child_count: int = 0,
    temperature_celsius: float = 20.0,
    selected_place_id: str = None,
) -> SafeHarborResult:
    """
    Safe Harbor hesaplaması (V2 — Per-Station SOC).

    Her kurtarıcı istasyon için bireysel tüketim hesaplanır.
    Varsayılan olarak EN YAKIN istasyon seçilir (minimum şarj).
    Frontend'de kullanıcı istasyonlar arasından seçim yapabilir.

    Args:
        destination: Varış noktası koordinatları
        vehicle: Araç modeli (VehicleSpec)
        battery_capacity_kwh: Batarya kapasitesi (kWh)
        passenger_count: Yolcu sayısı
        extra_load_kg: Ekstra yük (kg)
        child_count: Çocuk sayısı
        temperature_celsius: Sıcaklık (°C)

    Returns:
        SafeHarborResult: Her istasyona özel SOC hesabıyla sonuç
    """
    from app.services.google_service import google_maps

    logger.info(
        f"🏠 Safe Harbor check started for destination: "
        f"({destination.lat:.4f}, {destination.lon:.4f})"
    )

    # =========================================================================
    # ADIM A: Varış Noktası Taraması (Destination Check)
    # =========================================================================
    nearby_radius_m = int(SAFE_HARBOR_NEARBY_RADIUS_KM * 1000)

    try:
        nearby_stations = await google_maps.search_ev_charging_stations_new(
            lat=destination.lat,
            lon=destination.lon,
            radius_m=nearby_radius_m,
            max_results=5,
        )
    except Exception as e:
        logger.warning(f"Safe Harbor nearby search failed: {e}")
        nearby_stations = []

    # Gerçek DC şarj istasyonlarını filtrele (min 50kW)
    valid_nearby = [
        s for s in nearby_stations
        if s.get("max_power_kw", 0) >= 50 or s.get("connector_count", 0) > 0
    ]

    if len(valid_nearby) >= 2:
        # Varışta yeterli istasyon var — normal akış
        logger.info(
            f"✅ Safe Harbor: Destination has {len(valid_nearby)} charging stations "
            f"within {SAFE_HARBOR_NEARBY_RADIUS_KM}km. Normal operation."
        )
        return SafeHarborResult(
            is_destination_covered=True,
            search_radius_used_km=SAFE_HARBOR_NEARBY_RADIUS_KM,
        )

    logger.warning(
        f"⚠️ Safe Harbor: Only {len(valid_nearby)} station(s) within "
        f"{SAFE_HARBOR_NEARBY_RADIUS_KM}km. Activating Safe Harbor protocol."
    )

    # =========================================================================
    # ADIM B: En Yakın Kurtarıcı İstasyonları Bul (Find Rescue Chargers)
    # =========================================================================
    # Önce Adım A'da bulunan (ama yetersiz sayıda olan) istasyonları ekle
    rescue_stations: List[RescueStation] = []
    search_radius_used = SAFE_HARBOR_NEARBY_RADIUS_KM  # Varsayılan
    
    for station in valid_nearby:
        loc = station.get("geometry", {}).get("location", {})
        slat = loc.get("lat", 0)
        slng = loc.get("lng", 0)
        distance = haversine_km(destination.lat, destination.lon, slat, slng)
        
        rescue_stations.append(RescueStation(
            name=station.get("name", "Unknown"),
            location=GeoPoint(lat=slat, lon=slng),
            place_id=station.get("place_id", ""),
            distance_km=round(distance, 1),
            max_power_kw=station.get("max_power_kw", 0),
            rating=station.get("rating", 0),
        ))

    # Eğer hala 3 istasyon yoksa, yarıçapı büyüterek aramaya devam et
    if len(rescue_stations) < SAFE_HARBOR_MIN_RESCUE_STATIONS:
        for radius_km in SAFE_HARBOR_SEARCH_RADII_KM:
            # Daha önce bulunan yakın istasyonlar zaten var, daha geniş ara
            if radius_km <= SAFE_HARBOR_NEARBY_RADIUS_KM:
                continue

            radius_m = int(radius_km * 1000)
            try:
                found = await google_maps.search_ev_charging_stations_new(
                    lat=destination.lat,
                    lon=destination.lon,
                    radius_m=radius_m,
                    max_results=20,
                )
            except Exception as e:
                logger.warning(f"Safe Harbor search at {radius_km}km failed: {e}")
                continue

            # DC istasyonları filtrele ve mesafeye göre sırala
            for station in found:
                loc = station.get("geometry", {}).get("location", {})
                slat = loc.get("lat", 0)
                slng = loc.get("lng", 0)

                if slat == 0 and slng == 0:
                    continue

                power_kw = station.get("max_power_kw", 0)
                connector_count = station.get("connector_count", 0)

                # En az DC seviyesinde olmalı
                if power_kw < 50 and connector_count == 0:
                    continue

                distance = haversine_km(destination.lat, destination.lon, slat, slng)
                place_id = station.get("place_id", "")

                # Daha önce eklenmemişse ekle
                already_added = any(r.place_id == place_id for r in rescue_stations)
                if not already_added:
                    rescue_stations.append(RescueStation(
                        name=station.get("name", "Unknown"),
                        location=GeoPoint(lat=slat, lon=slng),
                        place_id=place_id,
                        distance_km=round(distance, 1),
                        max_power_kw=power_kw,
                        rating=station.get("rating", 0),
                    ))

            # Yeterli istasyon bulunduysa dur
            if len(rescue_stations) >= SAFE_HARBOR_MIN_RESCUE_STATIONS:
                logger.info(
                    f"Found {len(rescue_stations)} rescue stations within {radius_km}km"
                )
                search_radius_used = radius_km
                break
        else:
            search_radius_used = SAFE_HARBOR_SEARCH_RADII_KM[-1]

    # Mesafeye göre sırala
    rescue_stations.sort(key=lambda s: s.distance_km)

    if not rescue_stations:
        # Hiç kurtarıcı istasyon bulunamadı
        logger.error(
            f"🚨 Safe Harbor CRITICAL: No rescue stations found within "
            f"{SAFE_HARBOR_SEARCH_RADII_KM[-1]}km of destination!"
        )
        return SafeHarborResult(
            is_destination_covered=False,
            search_radius_used_km=search_radius_used,
            dynamic_min_arrival_soc=50.0,  # Güvenli yüksek değer
            warning_message=(
                f"⚠️ DİKKAT: Varış noktanızın {SAFE_HARBOR_SEARCH_RADII_KM[-1]}km "
                f"çevresinde şarj istasyonu bulunamadı! "
                f"Yeterli batarya ile yola çıktığınızdan emin olun."
            ),
        )

    # İlk N istasyonu al (en yakın 2-3)
    rescue_stations = rescue_stations[:SAFE_HARBOR_MIN_RESCUE_STATIONS]

    logger.info(
        f"🛟 Rescue stations found: {len(rescue_stations)} "
        f"[nearest={rescue_stations[0].distance_km}km, "
        f"farthest={rescue_stations[-1].distance_km}km]"
    )

    # =========================================================================
    # ADIM C: HER İSTASYON İÇİN Ghost Leg Hesaplaması
    # =========================================================================
    logger.info("Calculating Ghost Leg for each rescue station...")

    for station in rescue_stations:
        await _calculate_ghost_leg_for_station(
            destination=destination,
            station=station,
            vehicle=vehicle,
            battery_capacity_kwh=battery_capacity_kwh,
            passenger_count=passenger_count,
            extra_load_kg=extra_load_kg,
            child_count=child_count,
            temperature_celsius=temperature_celsius,
        )

    # =========================================================================
    # ADIM D: İstasyon seçimi (Varsayılan: En yakın, veya kullanıcı seçimi)
    # =========================================================================
    selected_index = 0  # Varsayılan: En yakın istasyon
    
    # Kullanıcı bir istasyon seçmiş mi kontrol et
    if selected_place_id:
        for i, rs in enumerate(rescue_stations):
            if rs.place_id == selected_place_id:
                selected_index = i
                logger.info(f"Using USER-SELECTED rescue station: {rs.name}")
                break

    rescue_stations[selected_index].is_selected = True
    selected_station = rescue_stations[selected_index]

    dynamic_min_arrival_soc = selected_station.required_arrival_soc

    logger.info(
        f"🏠 Safe Harbor RESULT (V2 — Per-Station): "
        f"selected={selected_station.name} ({selected_station.route_distance_km:.1f}km), "
        f"arrival_soc={dynamic_min_arrival_soc:.1f}%"
    )

    # Tüm seçenekleri logla
    for i, rs in enumerate(rescue_stations):
        marker = "→" if i == selected_index else " "
        logger.info(
            f"  {marker} [{i+1}] {rs.name}: "
            f"{rs.route_distance_km:.1f}km, "
            f"tüketim={rs.return_consumption_kwh:.2f}kWh, "
            f"gerekli_soc=%{rs.required_arrival_soc:.0f}"
        )

    # Uyarı mesajı oluştur
    station_names = ", ".join(rs.name for rs in rescue_stations)
    warning_msg = (
        f"🏠 Safe Harbor Aktif: Varış noktanız yakınında şarj istasyonu yok. "
        f"En yakın {len(rescue_stations)} istasyon: {station_names}. "
        f"Dönüş için bataryada en az %{dynamic_min_arrival_soc:.0f} kalacak "
        f"(en yakın istasyona göre)."
    )

    max_distance = max(rs.route_distance_km for rs in rescue_stations)
    if max_distance > SAFE_HARBOR_MAX_RETURN_KM:
        warning_msg += (
            f" ⚠️ En uzak kurtarıcı {max_distance:.0f}km mesafede — "
            f"batarya durumunuzu yakından takip edin."
        )

    return SafeHarborResult(
        is_destination_covered=False,
        rescue_stations=rescue_stations,
        selected_station_index=selected_index,
        dynamic_min_arrival_soc=round(dynamic_min_arrival_soc, 1),
        search_radius_used_km=search_radius_used,
        warning_message=warning_msg,
    )
