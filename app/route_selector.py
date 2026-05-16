"""Route Selector v3.0
====================

Google Directions API'den alternatif rotalarÄ± alÄ±r ve stratejiye gÃ¶re en uygun olanÄ± seÃ§er.

Stratejiler:
- FASTEST: En kÄ±sa sÃ¼reli rota (trafik dahil)
- EFFICIENT: En az enerji tÃ¼keten rota
- OPTIMAL: SÃ¼re + enerji dengesi (varsayÄ±lan)
- CHEAPEST: En dÃ¼ÅŸÃ¼k ÅŸarj maliyetli rota (fiyat verisi gerektirir)
- RENEWABLE: Yenilenebilir enerji istasyonlarÄ± Ã¶ncelikli (veri gerektirir)

KullanÄ±m:
    from app.route_selector import find_best_route
    from app.models import RouteStrategy
    
    result = await find_best_route(
        origin=GeoPoint(lat=41.0, lon=29.0),
        destination=GeoPoint(lat=39.0, lon=32.0),
        vehicle_model_id="mg4_51kwh",
        strategy=RouteStrategy.FASTEST,
        departure_time=None  # None = ÅŸimdi
    )
"""

import re
import time
import unicodedata
from typing import Dict, Any, List, Union, Optional
import polyline
from app.models import GeoPoint, RouteStrategy, RoadAvoidances
from app.routing import (
    CanonicalRoute,
    ConsumptionEngineEnergyEstimator,
    EnergyEstimateRequest,
    GoogleDirectionsProvider,
    GoogleRoutesProvider,
    SegmentFeatureBuilder,
)
from app.services.pricing_service import pricing_service
from app.infrastructure.vehicle_catalog import get_vehicle_model
from app.utils.config_manager import config
from app.utils.logger import get_logger

logger = get_logger("route_selector")

FORBIDDEN_BRIDGE_ZONES = [
    {
        "id": "15_temmuz",
        "name": "15 Temmuz Åehitler KÃ¶prÃ¼sÃ¼",
        "bbox": (41.035, 29.020, 41.055, 29.060),
    },
    {
        "id": "fsm",
        "name": "Fatih Sultan Mehmet KÃ¶prÃ¼sÃ¼",
        "bbox": (41.080, 29.045, 41.105, 29.095),
    },
    {
        "id": "yss",
        "name": "Yavuz Sultan Selim KÃ¶prÃ¼sÃ¼",
        "bbox": (41.195, 29.090, 41.220, 29.140),
    },
    {
        "id": "osmangazi",
        "name": "Osmangazi KÃ¶prÃ¼sÃ¼",
        "bbox": (40.710, 29.500, 40.760, 29.530),
    },
    {
        "id": "canakkale",
        "name": "1915 Ã‡anakkale KÃ¶prÃ¼sÃ¼",
        "bbox": (40.320, 26.620, 40.350, 26.650),
    },
]

PRIVATE_HIGHWAY_TOKENS = [
    "o-5",
    "o 5",
    "o-6",
    "o 6",
    "o-7",
    "o 7",
    "o-21",
    "o 21",
    "o-33",
    "o 33",
    "kuzey marmara",
    "gebze-orhangazi",
    "gebze orhangazi",
    "istanbul-izmir otoyolu",
    "istanbul izmir otoyolu",
    "ankara-nigde otoyolu",
    "ankara nigde otoyolu",
    "menemen-aliaga-candarli",
    "menemen aliaga candarli",
]

# =============================================================================
# STRATEGY WEIGHTS (OPTIMAL strateji iÃ§in)
# =============================================================================
OPTIMAL_WEIGHT_TIME = 0.4
OPTIMAL_WEIGHT_ENERGY = 0.4
OPTIMAL_WEIGHT_COST = 0.2  # âš ï¸ HenÃ¼z kullanÄ±lmÄ±yor â€” maliyet entegre edilince skora eklenecek (toplam 0.8 â†’ 1.0)

def _parse_geopoint(location: Union[GeoPoint, str]) -> GeoPoint:
    """
    GeoPoint veya string koordinatÄ± GeoPoint'e Ã§evirir.
    
    Args:
        location: GeoPoint objesi veya "lat,lon" formatÄ±nda string
    
    Returns:
        GeoPoint objesi
    """
    if isinstance(location, GeoPoint):
        return location
    
    if isinstance(location, str):
        try:
            lat, lon = location.split(",")
            return GeoPoint(lat=float(lat.strip()), lon=float(lon.strip()))
        except (ValueError, AttributeError) as e:
            raise ValueError(f"GeÃ§ersiz koordinat formatÄ±: {location}") from e
    
    raise TypeError(f"Beklenmeyen tip: {type(location)}")


def _matches_bbox(lat: float, lon: float, bbox: tuple) -> bool:
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _detect_forbidden_bridges(
    coords: List[tuple],
    avoid_all_bridges: bool,
    avoid_osmangazi: bool,
    avoid_canakkale: bool,
) -> List[str]:
    if not coords:
        return []

    blocked_ids = set()
    if avoid_all_bridges:
        blocked_ids.update(zone["id"] for zone in FORBIDDEN_BRIDGE_ZONES)
    if avoid_osmangazi:
        blocked_ids.add("osmangazi")
    if avoid_canakkale:
        blocked_ids.add("canakkale")

    if not blocked_ids:
        return []

    violations = []
    for zone in FORBIDDEN_BRIDGE_ZONES:
        if zone["id"] not in blocked_ids:
            continue
        if any(_matches_bbox(lat, lon, zone["bbox"]) for lat, lon in coords):
            violations.append(zone["name"])
    return violations


def _normalize_route_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value)


def _route_text(route: dict) -> str:
    parts = [route.get("summary", "")]
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            parts.append(step.get("html_instructions", ""))
    return _normalize_route_text(" ".join(parts))


def _detect_private_highway(route: dict) -> List[str]:
    text = _route_text(route)
    if not text:
        return []
    return [token for token in PRIVATE_HIGHWAY_TOKENS if token in text]


def _estimate_route_energy_kwh(
    canonical_route: CanonicalRoute,
    vehicle=None,
    *,
    temperature_celsius: float = 20.0,
    extra_load_kg: float = 0.0,
    energy_estimator=None,
) -> float:
    if vehicle is None:
        return canonical_route.distance_km * 0.18

    try:
        segment_features = SegmentFeatureBuilder(segment_length_km=10.0).build(
            canonical_route=canonical_route,
        )
        estimator = energy_estimator or ConsumptionEngineEnergyEstimator()
        estimate = estimator.estimate(EnergyEstimateRequest(
            segment_features=segment_features,
            vehicle=vehicle,
            temperature_celsius=temperature_celsius,
            extra_load_kg=extra_load_kg,
        ))
        return estimate.total_consumption_kwh
    except Exception as e:
        logger.warning(
            "Route alternative energy estimate failed, using distance fallback",
            route_id=canonical_route.provider_route_id,
            error=str(e),
        )
        return canonical_route.distance_km * 0.18


def _analyze_route(
    route: dict,
    index: int,
    vehicle=None,
    *,
    temperature_celsius: float = 20.0,
    extra_load_kg: float = 0.0,
    energy_estimator=None,
) -> Dict[str, Any]:
    """
    Tek bir rotayÄ± analiz eder - trafik sÃ¼resi dahil.
    
    Args:
        route: Google Directions route objesi
        index: Rota indeksi
    
    Returns:
        Analiz sonucu dict:
        - duration_min: Trafiksiz sÃ¼re
        - duration_in_traffic_min: Trafikli sÃ¼re (varsa, yoksa duration_min)
        - traffic_ratio: Trafik oranÄ± (1.0 = normal, >1 = trafik var)
    """
    try:
        canonical_route = CanonicalRoute.from_google_directions_route(route, index)
        distance_km = canonical_route.distance_km
        duration_min = canonical_route.duration_min
        duration_in_traffic_min = canonical_route.duration_in_traffic_min
        traffic_ratio = canonical_route.traffic_ratio
        
        # Sprint 4: Alternatif rota enerjisi CanonicalRoute -> SegmentFeatureBuilder
        # -> EnergyEstimator zinciriyle hesaplanÄ±r. Estimator baÅŸarÄ±sÄ±zsa mesafe
        # bazlÄ± fallback kullanÄ±lÄ±r; route selection tamamen kÃ¶r kalmaz.
        estimated_consumption_kwh = _estimate_route_energy_kwh(
            canonical_route=canonical_route,
            vehicle=vehicle,
            temperature_celsius=temperature_celsius,
            extra_load_kg=extra_load_kg,
            energy_estimator=energy_estimator,
        )
        
        # Tahmini ÅŸarj maliyeti - ortalama piyasa fiyatÄ± ile hesapla
        # (GerÃ§ek maliyet istasyon seÃ§iminden sonra kesinleÅŸir)
        avg_price_per_kwh = pricing_service.get_average_dc_price()
        estimated_cost_try = estimated_consumption_kwh * avg_price_per_kwh
        
        return {
            "route_index": index,
            "route": route,
            "distance_km": distance_km,
            "duration_min": duration_min,
            "duration_in_traffic_min": duration_in_traffic_min,
            "traffic_ratio": traffic_ratio,
            "estimated_consumption_kwh": estimated_consumption_kwh,
            "energy_estimate_source": "energy_estimator" if vehicle is not None else "distance_fallback",
            "estimated_cost_try": estimated_cost_try,  # Cheapest iÃ§in placeholder
            "polyline": canonical_route.polyline,
            "summary": canonical_route.summary,
            "canonical_route": canonical_route,
        }
    except (KeyError, IndexError) as e:
        logger.warning(f"Rota {index + 1} analizi eksik veri", error=str(e))
        return None


def _analyze_canonical_route(
    canonical_route: CanonicalRoute,
    index: int,
    vehicle=None,
    *,
    temperature_celsius: float = 20.0,
    extra_load_kg: float = 0.0,
    energy_estimator=None,
) -> Dict[str, Any]:
    legacy_route = canonical_route.to_google_directions_route()
    estimated_consumption_kwh = _estimate_route_energy_kwh(
        canonical_route=canonical_route,
        vehicle=vehicle,
        temperature_celsius=temperature_celsius,
        extra_load_kg=extra_load_kg,
        energy_estimator=energy_estimator,
    )
    avg_price_per_kwh = pricing_service.get_average_dc_price()

    return {
        "route_index": index,
        "route": legacy_route,
        "distance_km": canonical_route.distance_km,
        "duration_min": canonical_route.duration_min,
        "duration_in_traffic_min": canonical_route.duration_in_traffic_min,
        "traffic_ratio": canonical_route.traffic_ratio,
        "estimated_consumption_kwh": estimated_consumption_kwh,
        "energy_estimate_source": "energy_estimator" if vehicle is not None else "distance_fallback",
        "estimated_cost_try": estimated_consumption_kwh * avg_price_per_kwh,
        "polyline": canonical_route.polyline,
        "summary": canonical_route.summary,
        "canonical_route": canonical_route,
    }


def _select_by_strategy(
    route_analyses: List[Dict[str, Any]],
    strategy: RouteStrategy,
    battery_kwh: float,
    threshold_percent: float
) -> tuple:
    """
    Stratejiye gÃ¶re en iyi rotayÄ± seÃ§er.
    
    Args:
        route_analyses: Analiz edilmiÅŸ rotalar
        strategy: SeÃ§im stratejisi
        battery_kwh: Batarya kapasitesi (kWh)
        threshold_percent: EÅŸik yÃ¼zdesi
    
    Returns:
        (selected_route_analysis, selection_reason)
    """
    if not route_analyses:
        return None, "no_routes"
    
    # Tek rota varsa direkt seÃ§
    if len(route_analyses) == 1:
        return route_analyses[0], "single_route"
    
    # FASTEST: En kÄ±sa trafikli sÃ¼re
    if strategy == RouteStrategy.FASTEST:
        selected = min(route_analyses, key=lambda x: x["duration_in_traffic_min"])
        logger.info(
            f"FASTEST strategy: {selected['summary']}",
            duration_traffic=round(selected["duration_in_traffic_min"], 1),
            traffic_ratio=f"{selected['traffic_ratio']:.2f}"
        )
        return selected, "fastest_by_traffic_duration"
    
    # EFFICIENT: En az enerji tÃ¼ketimi (en kÄ±sa mesafe)
    if strategy == RouteStrategy.EFFICIENT:
        selected = min(route_analyses, key=lambda x: x["estimated_consumption_kwh"])
        logger.info(
            f"EFFICIENT strategy: {selected['summary']}",
            consumption_kwh=round(selected["estimated_consumption_kwh"], 2),
            distance_km=round(selected["distance_km"], 1)
        )
        return selected, "efficient_by_consumption"
    
    # CHEAPEST: En dÃ¼ÅŸÃ¼k maliyet (placeholder - fiyat verisi gerektirir)
    if strategy == RouteStrategy.CHEAPEST:
        # Åimdilik estimated_cost_try kullanÄ±yoruz
        # GerÃ§ek implementasyonda istasyon fiyatlarÄ± ile hesaplanacak
        selected = min(route_analyses, key=lambda x: x["estimated_cost_try"])
        logger.info(
            f"CHEAPEST strategy: {selected['summary']}",
            estimated_cost=round(selected["estimated_cost_try"], 2),
            note="Placeholder - gerÃ§ek istasyon fiyatlarÄ± ile gÃ¼ncellenecek"
        )
        return selected, "cheapest_by_estimated_cost"
    
    # RENEWABLE: Rota deÄŸil istasyon seÃ§imini etkiler
    # Rota olarak OPTIMAL kullan
    if strategy == RouteStrategy.RENEWABLE:
        logger.info("RENEWABLE strategy: Rota iÃ§in OPTIMAL kullanÄ±lÄ±yor, istasyon filtresi ayrÄ±ca uygulanacak")
        strategy = RouteStrategy.OPTIMAL
    
    # OPTIMAL (varsayÄ±lan): SÃ¼re + Enerji dengesi
    # Normalize edilmiÅŸ skorlama
    min_time = min(r["duration_in_traffic_min"] for r in route_analyses)
    max_time = max(r["duration_in_traffic_min"] for r in route_analyses)
    min_energy = min(r["estimated_consumption_kwh"] for r in route_analyses)
    max_energy = max(r["estimated_consumption_kwh"] for r in route_analyses)
    
    time_range = max_time - min_time if max_time > min_time else 1
    energy_range = max_energy - min_energy if max_energy > min_energy else 1
    
    best_score = float('inf')
    selected = route_analyses[0]
    
    for r in route_analyses:
        # Normalize (0-1 arasÄ±, dÃ¼ÅŸÃ¼k = iyi)
        norm_time = (r["duration_in_traffic_min"] - min_time) / time_range
        norm_energy = (r["estimated_consumption_kwh"] - min_energy) / energy_range
        
        # AÄŸÄ±rlÄ±klÄ± skor (dÃ¼ÅŸÃ¼k = iyi)
        score = OPTIMAL_WEIGHT_TIME * norm_time + OPTIMAL_WEIGHT_ENERGY * norm_energy
        
        if score < best_score:
            best_score = score
            selected = r
    
    logger.info(
        f"OPTIMAL strategy: {selected['summary']}",
        duration_traffic=round(selected["duration_in_traffic_min"], 1),
        consumption_kwh=round(selected["estimated_consumption_kwh"], 2),
        score=round(best_score, 3)
    )
    
    return selected, "optimal_balanced"


# =============================================================================
# MAIN FUNCTION
# =============================================================================

def _parse_departure_time(departure_time_iso: Optional[str]) -> Optional[int]:
    """
    ISO 8601 formatÄ±ndaki Ã§Ä±kÄ±ÅŸ zamanÄ±nÄ± Unix epoch'a Ã§evirir.
    
    Args:
        departure_time_iso: ISO 8601 formatÄ±nda zaman ("2025-11-25T12:30:00Z")
    
    Returns:
        Unix epoch (saniye) veya None (ÅŸimdi iÃ§in)
    """
    if not departure_time_iso:
        return None
    
    try:
        from datetime import datetime
        # ISO 8601 parse
        if departure_time_iso.endswith('Z'):
            dt = datetime.fromisoformat(departure_time_iso.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(departure_time_iso)
        return int(dt.timestamp())
    except Exception as e:
        logger.warning(f"departure_time_iso parse hatasÄ±: {e}, 'now' kullanÄ±lÄ±yor")
        return None


async def _get_canonical_routes_with_fallback(
    *,
    start: GeoPoint,
    end: GeoPoint,
    alternatives: bool,
    departure_time: Optional[int],
    avoidances: Optional[List[str]],
    waypoints: Optional[List[GeoPoint]],
    routing_provider=None,
    fallback_provider=None,
) -> tuple[List[CanonicalRoute], str]:
    primary = routing_provider or GoogleRoutesProvider()
    fallback = fallback_provider or GoogleDirectionsProvider()

    try:
        routes = await primary.get_route_alternatives(
            start=start,
            end=end,
            alternatives=alternatives,
            departure_time=departure_time,
            traffic_model="best_guess",
            avoidances=avoidances,
            waypoints=waypoints,
        )
        if routes:
            return routes, getattr(primary, "provider_name", "routing_provider")
        raise ValueError("primary provider returned no routes")
    except Exception as exc:
        if routing_provider is not None and fallback_provider is None:
            raise
        logger.warning(
            "Primary routing provider failed; falling back to legacy Directions",
            provider=getattr(primary, "provider_name", "unknown"),
            error=str(exc),
        )

    routes = await fallback.get_route_alternatives(
        start=start,
        end=end,
        alternatives=alternatives,
        departure_time=departure_time,
        traffic_model="best_guess",
        avoidances=avoidances,
        waypoints=waypoints,
    )
    if not routes:
        raise ValueError("Fallback routing provider returned no routes")
    return routes, getattr(fallback, "provider_name", "fallback_provider")


async def find_best_route(
    origin: Union[GeoPoint, str],
    destination: Union[GeoPoint, str],
    vehicle_model_id: str,
    extra_load_kg: float = 0.0,
    temperature_celsius: float = 20.0,
    strategy: RouteStrategy = RouteStrategy.OPTIMAL,
    departure_time_iso: Optional[str] = None,
    road_avoidances: Optional[RoadAvoidances] = None,
    waypoints: Optional[List[GeoPoint]] = None,
    vehicle_spec = None,  # Zaten Ã§Ã¶zÃ¼lmÃ¼ÅŸ VehicleSpec (MSSQL'den geliyorsa)
    routing_provider=None,
    fallback_provider=None,
) -> Dict[str, Any]:
    """
    Google'dan alternatif rotalarÄ± alÄ±r ve stratejiye gÃ¶re en uygun olanÄ± seÃ§er.
    
    Args:
        origin: BaÅŸlangÄ±Ã§ noktasÄ± (GeoPoint veya "lat,lon" string)
        destination: BitiÅŸ noktasÄ± (GeoPoint veya "lat,lon" string)
        vehicle_model_id: AraÃ§ model ID'si
        extra_load_kg: Ekstra yÃ¼k (kg)
        temperature_celsius: Ortam sÄ±caklÄ±ÄŸÄ± (Â°C)
        strategy: Rota seÃ§im stratejisi (fastest, efficient, optimal, cheapest, renewable)
        departure_time_iso: ISO 8601 formatÄ±nda Ã§Ä±kÄ±ÅŸ zamanÄ±. None = ÅŸimdi
    
    Returns:
        {
            "selected_route": dict,      # SeÃ§ilen rota (Google format)
            "selection_reason": str,     # SeÃ§im nedeni
            "route_analyses": list,      # TÃ¼m rotalarÄ±n analizi
            "selected_distance_km": float,
            "selected_duration_min": float,  # Trafiksiz sÃ¼re
            "selected_duration_in_traffic_min": float,  # Trafikli sÃ¼re
            "traffic_ratio": float,      # Trafik oranÄ±
            "polyline": str
        }
    
    Raises:
        ValueError: API hatasÄ± veya veri eksikliÄŸi durumunda
    """
    try:
        # --- 1. Parametreleri normalize et ---
        start = _parse_geopoint(origin)
        end = _parse_geopoint(destination)
        departure_time = _parse_departure_time(departure_time_iso)
        
        logger.info(
            "Rota seÃ§imi baÅŸlatÄ±ldÄ±",
            origin=f"{start.lat},{start.lon}",
            destination=f"{end.lat},{end.lon}",
            vehicle=vehicle_model_id,
            strategy=strategy.value,
            departure_time=departure_time_iso or "now"
        )
        
        # --- 2. AraÃ§ bilgisini al ---
        if vehicle_spec:
            vehicle = vehicle_spec
            logger.info(f"Using pre-resolved vehicle: {getattr(vehicle, 'display_name', vehicle_model_id)}")
        else:
            vehicle = get_vehicle_model(vehicle_model_id)
        
        # --- 3. Google'dan alternatif rotalarÄ± al (trafik dahil) ---
        avoidances = []
        avoid_all_bridges = False
        avoid_osmangazi = False
        avoid_canakkale = False
        avoid_private_highways = False
        
        if road_avoidances:
            # Google-native avoid listesi. Bunlar doÄŸrudan Google Directions/Routes
            # tarafÄ±na bÄ±rakÄ±lÄ±r: tolls/highways/ferries.
            if road_avoidances.avoid_tolls: avoidances.append("tolls")
            if road_avoidances.avoid_highways: avoidances.append("highways")
            if road_avoidances.avoid_ferries: avoidances.append("ferries")
            # TÃ¼rkiye'ye Ã¶zel policy alanlarÄ±. Google'da "kÃ¶prÃ¼den kaÃ§Ä±n" veya
            # "Ã¶zel sektÃ¶r otoyolundan kaÃ§Ä±n" native kategorisi olmadÄ±ÄŸÄ± iÃ§in
            # Google alternatifleri geldikten sonra route_selector doÄŸrular.
            avoid_all_bridges = road_avoidances.avoid_bridges
            avoid_osmangazi = road_avoidances.avoid_osmangazi_bridge
            avoid_canakkale = road_avoidances.avoid_canakkale_bridge
            avoid_private_highways = road_avoidances.avoid_private_highways

        logger.info(
            "Route avoidances applied",
            google_avoid=avoidances if avoidances else "none",
            bridge_all=avoid_all_bridges,
            bridge_osmangazi=avoid_osmangazi,
            bridge_canakkale=avoid_canakkale,
            private_highways=avoid_private_highways,
        )

        canonical_routes, routing_provider_name = await _get_canonical_routes_with_fallback(
            start=start,
            end=end,
            alternatives=True,
            departure_time=departure_time,
            avoidances=avoidances if avoidances else None,
            waypoints=waypoints,
            routing_provider=routing_provider,
            fallback_provider=fallback_provider,
        )

        logger.info(
            f"{len(canonical_routes)} rota alternatifi alindi",
            routing_provider=routing_provider_name,
        )
        
        # --- 4. Her rotayÄ± analiz et ---
        route_analyses = []
        rejected_routes = []
        
        for i, canonical_route in enumerate(canonical_routes):
            analysis = _analyze_canonical_route(
                canonical_route,
                i,
                vehicle=vehicle,
                temperature_celsius=temperature_celsius,
                extra_load_kg=extra_load_kg,
            )
            if analysis:
                # Sprint 3.5: Google-native olmayan TÃ¼rkiye Ã¶zel policy kontrolÃ¼.
                # Google route'u Ã¼retir; biz sadece gelen alternatifin kÃ¶prÃ¼/BOT
                # kÄ±sÄ±tÄ±nÄ± ihlal edip etmediÄŸini doÄŸrular ve ihlal edeni eleriz.
                bridge_violations = []
                if avoid_all_bridges or avoid_osmangazi or avoid_canakkale:
                    coords = polyline.decode(analysis["polyline"])
                    bridge_violations = _detect_forbidden_bridges(
                        coords=coords,
                        avoid_all_bridges=avoid_all_bridges,
                        avoid_osmangazi=avoid_osmangazi,
                        avoid_canakkale=avoid_canakkale,
                    )
                    if bridge_violations:
                        logger.warning(
                            f"Rota {i+1} kÃ¶prÃ¼ ihlali yaptÄ±, alternatiflerden eleniyor.",
                            violations=bridge_violations,
                        )

                private_highway_violations = []
                if avoid_private_highways:
                    private_highway_violations = _detect_private_highway(analysis["route"])
                    if private_highway_violations:
                        logger.warning(
                            f"Rota {i+1} Ã¶zel otoyol ihlali yaptÄ±, alternatiflerden eleniyor.",
                            violations=private_highway_violations,
                        )

                analysis["avoidance_violations"] = bridge_violations + private_highway_violations
                if analysis["avoidance_violations"]:
                    rejected_routes.append(analysis)
                    continue
                
                route_analyses.append(analysis)
                logger.info(
                    f"Rota {i + 1} analiz edildi",
                    distance_km=round(analysis["distance_km"], 1),
                    duration_min=round(analysis["duration_min"]),
                    duration_traffic=round(analysis["duration_in_traffic_min"]),
                    traffic_ratio=f"{analysis['traffic_ratio']:.2f}",
                    summary=analysis["summary"],
                    avoidance_violations=analysis["avoidance_violations"],
                )
        
        if not route_analyses:
            if rejected_routes:
                violations = [
                    {
                        "summary": route.get("summary"),
                        "violations": route.get("avoidance_violations", []),
                    }
                    for route in rejected_routes
                ]
                logger.warning(
                    "TÃ¼m rota alternatifleri yol tercihleri nedeniyle elendi",
                    rejected_routes=violations,
                )
                raise ValueError("NO_ROUTE_WITH_CONSTRAINTS: Yol/kÃ¶prÃ¼ tercihlerine uyan rota bulunamadÄ±")
            raise ValueError("HiÃ§bir rota analiz edilemedi")
        
        # --- 5. Stratejiye gÃ¶re en iyi rotayÄ± seÃ§ ---
        threshold_percent = config.get_route_selector_threshold_percent()
        battery_kwh = vehicle.battery_capacity_kwh
        
        selected, reason = _select_by_strategy(
            route_analyses=route_analyses,
            strategy=strategy,
            battery_kwh=battery_kwh,
            threshold_percent=threshold_percent
        )
        
        if not selected:
            raise ValueError("Rota seÃ§ilemedi")
        
        logger.info(
            f"Rota seÃ§ildi: {selected['summary']}",
            strategy=strategy.value,
            reason=reason,
            distance_km=round(selected["distance_km"], 1),
            duration_traffic=round(selected["duration_in_traffic_min"], 1)
        )
        
        return {
            "selected_route": selected["route"],
            "canonical_route": selected.get("canonical_route"),
            "selection_reason": reason,
            "route_analyses": route_analyses,
            "selected_distance_km": selected["distance_km"],
            "selected_duration_min": selected["duration_min"],
            "selected_duration_in_traffic_min": selected["duration_in_traffic_min"],
            "traffic_ratio": selected["traffic_ratio"],
            "estimated_consumption_kwh": selected["estimated_consumption_kwh"],
            "polyline": selected["polyline"],
            "routing_provider": routing_provider_name,
        }
        
    except ValueError:
        # Bilinen hatalar - tekrar raise et
        raise
    except Exception as e:
        logger.error(
            "Route seÃ§imi baÅŸarÄ±sÄ±z",
            error=str(e),
            error_type=type(e).__name__
        )
        raise ValueError(f"Route seÃ§imi baÅŸarÄ±sÄ±z: {str(e)}") from e
