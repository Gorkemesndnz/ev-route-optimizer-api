"""Canonical route models shared by routing, energy, and response code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.models import GeoPoint


@dataclass(frozen=True)
class CanonicalLeg:
    index: int
    start_point: GeoPoint
    end_point: GeoPoint
    distance_km: float
    duration_min: float
    duration_in_traffic_min: float
    raw_leg: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class CanonicalRoute:
    provider: str
    provider_route_id: str
    summary: str
    distance_km: float
    duration_min: float
    duration_in_traffic_min: float
    traffic_ratio: float
    polyline: str
    legs: List[CanonicalLeg]
    raw_route: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_google_directions_route(
        cls,
        route: Dict[str, Any],
        index: int,
        provider: str = "google_directions",
    ) -> "CanonicalRoute":
        legs = [
            _canonical_leg_from_google(leg=leg, index=leg_index)
            for leg_index, leg in enumerate(route.get("legs", []))
        ]

        distance_km = sum(leg.distance_km for leg in legs)
        duration_min = sum(leg.duration_min for leg in legs)
        duration_in_traffic_min = sum(leg.duration_in_traffic_min for leg in legs)
        traffic_ratio = (
            duration_in_traffic_min / duration_min
            if duration_min > 0 and duration_in_traffic_min > 0
            else 1.0
        )

        return cls(
            provider=provider,
            provider_route_id=f"{provider}:{index}",
            summary=route.get("summary") or f"Route {index + 1}",
            distance_km=distance_km,
            duration_min=duration_min,
            duration_in_traffic_min=duration_in_traffic_min,
            traffic_ratio=traffic_ratio,
            polyline=route.get("overview_polyline", {}).get("points", ""),
            legs=legs,
            raw_route=route,
        )

    @classmethod
    def from_google_routes_route(
        cls,
        route: Dict[str, Any],
        index: int,
        provider: str = "google_routes",
    ) -> "CanonicalRoute":
        legs = [
            _canonical_leg_from_google_routes(leg=leg, index=leg_index)
            for leg_index, leg in enumerate(route.get("legs", []))
        ]

        route_distance_km = (route.get("distanceMeters") or 0) / 1000.0
        distance_km = route_distance_km or sum(leg.distance_km for leg in legs)

        static_duration_min = _parse_duration_seconds(route.get("staticDuration"))
        traffic_duration_min = _parse_duration_seconds(route.get("duration"))

        duration_min = static_duration_min
        if duration_min is None:
            duration_min = sum(leg.duration_min for leg in legs)
        if duration_min is None or duration_min <= 0:
            duration_min = traffic_duration_min or 0.0

        duration_in_traffic_min = traffic_duration_min
        if duration_in_traffic_min is None:
            duration_in_traffic_min = sum(leg.duration_in_traffic_min for leg in legs)
        if duration_in_traffic_min is None or duration_in_traffic_min <= 0:
            duration_in_traffic_min = duration_min

        traffic_ratio = (
            duration_in_traffic_min / duration_min
            if duration_min > 0 and duration_in_traffic_min > 0
            else 1.0
        )

        summary = route.get("description")
        if not summary and route.get("routeLabels"):
            summary = ", ".join(str(label) for label in route.get("routeLabels", []))

        return cls(
            provider=provider,
            provider_route_id=f"{provider}:{index}",
            summary=summary or f"Route {index + 1}",
            distance_km=distance_km,
            duration_min=duration_min,
            duration_in_traffic_min=duration_in_traffic_min,
            traffic_ratio=traffic_ratio,
            polyline=route.get("polyline", {}).get("encodedPolyline", ""),
            legs=legs,
            raw_route=route,
        )

    def to_google_directions_route(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "overview_polyline": {"points": self.polyline},
            "legs": [
                {
                    "distance": {"value": int(round(leg.distance_km * 1000))},
                    "duration": {"value": int(round(leg.duration_min * 60))},
                    "duration_in_traffic": {"value": int(round(leg.duration_in_traffic_min * 60))},
                    "start_location": {"lat": leg.start_point.lat, "lng": leg.start_point.lon},
                    "end_location": {"lat": leg.end_point.lat, "lng": leg.end_point.lon},
                    "steps": _legacy_steps_from_raw_leg(leg.raw_leg),
                }
                for leg in self.legs
            ],
        }


def _canonical_leg_from_google(leg: Dict[str, Any], index: int) -> CanonicalLeg:
    distance_km = (leg.get("distance", {}).get("value") or 0) / 1000.0
    duration_min = (leg.get("duration", {}).get("value") or 0) / 60.0
    duration_in_traffic_value = leg.get("duration_in_traffic", {}).get("value")
    duration_in_traffic_min = (
        duration_in_traffic_value / 60.0
        if duration_in_traffic_value is not None
        else duration_min
    )

    start = leg.get("start_location") or {}
    end = leg.get("end_location") or {}

    return CanonicalLeg(
        index=index,
        start_point=GeoPoint(lat=float(start.get("lat", 0.0)), lon=float(start.get("lng", 0.0))),
        end_point=GeoPoint(lat=float(end.get("lat", 0.0)), lon=float(end.get("lng", 0.0))),
        distance_km=distance_km,
        duration_min=duration_min,
        duration_in_traffic_min=duration_in_traffic_min,
        raw_leg=leg,
    )


def _canonical_leg_from_google_routes(leg: Dict[str, Any], index: int) -> CanonicalLeg:
    distance_km = (leg.get("distanceMeters") or 0) / 1000.0
    static_duration_min = _parse_duration_seconds(leg.get("staticDuration"))
    traffic_duration_min = _parse_duration_seconds(leg.get("duration"))
    duration_min = static_duration_min if static_duration_min is not None else (traffic_duration_min or 0.0)
    duration_in_traffic_min = traffic_duration_min if traffic_duration_min is not None else duration_min

    start = ((leg.get("startLocation") or {}).get("latLng") or {})
    end = ((leg.get("endLocation") or {}).get("latLng") or {})

    return CanonicalLeg(
        index=index,
        start_point=GeoPoint(
            lat=float(start.get("latitude", 0.0)),
            lon=float(start.get("longitude", 0.0)),
        ),
        end_point=GeoPoint(
            lat=float(end.get("latitude", 0.0)),
            lon=float(end.get("longitude", 0.0)),
        ),
        distance_km=distance_km,
        duration_min=duration_min,
        duration_in_traffic_min=duration_in_traffic_min,
        raw_leg=leg,
    )


def _parse_duration_seconds(value: Optional[Any]) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) / 60.0
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("s"):
        text = text[:-1]
    try:
        return float(text) / 60.0
    except ValueError:
        return None


def _legacy_steps_from_raw_leg(raw_leg: Dict[str, Any]) -> List[Dict[str, Any]]:
    directions_steps = raw_leg.get("steps")
    if (
        directions_steps
        and isinstance(directions_steps[0], dict)
        and directions_steps[0].get("html_instructions") is not None
    ):
        return directions_steps

    legacy_steps = []
    for step in directions_steps or []:
        if not isinstance(step, dict):
            continue
        instruction = ((step.get("navigationInstruction") or {}).get("instructions") or "")
        legacy_steps.append({"html_instructions": instruction})
    return legacy_steps
