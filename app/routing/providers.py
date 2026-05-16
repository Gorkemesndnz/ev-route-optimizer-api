"""Routing provider interfaces and Google routing adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Protocol

from app.models import GeoPoint
from app.routing.canonical import CanonicalRoute
from app.services.base_service import BaseService
from app.services.google_service import google_maps
from app.utils.config_manager import config


GOOGLE_ROUTES_FIELD_MASK = ",".join([
    "routes.duration",
    "routes.staticDuration",
    "routes.distanceMeters",
    "routes.description",
    "routes.routeLabels",
    "routes.polyline.encodedPolyline",
    "routes.legs.distanceMeters",
    "routes.legs.duration",
    "routes.legs.staticDuration",
    "routes.legs.polyline.encodedPolyline",
    "routes.legs.startLocation",
    "routes.legs.endLocation",
    "routes.legs.steps.navigationInstruction.instructions",
])


class RoutingProvider(Protocol):
    provider_name: str
    api_version: str

    async def get_route_alternatives(
        self,
        start: GeoPoint,
        end: GeoPoint,
        *,
        alternatives: bool = True,
        departure_time: Optional[int] = None,
        traffic_model: str = "best_guess",
        avoidances: Optional[List[str]] = None,
        waypoints: Optional[List[GeoPoint]] = None,
    ) -> List[CanonicalRoute]:
        ...


class GoogleDirectionsProvider:
    provider_name = "google_directions"
    api_version = "legacy_directions_v1"

    def __init__(self, maps_service=google_maps):
        self._maps_service = maps_service

    async def get_route_alternatives(
        self,
        start: GeoPoint,
        end: GeoPoint,
        *,
        alternatives: bool = True,
        departure_time: Optional[int] = None,
        traffic_model: str = "best_guess",
        avoidances: Optional[List[str]] = None,
        waypoints: Optional[List[GeoPoint]] = None,
    ) -> List[CanonicalRoute]:
        response = await self._maps_service.get_route_alternatives_cached(
            start=start,
            end=end,
            alternatives=alternatives,
            departure_time=departure_time,
            traffic_model=traffic_model,
            avoidances=avoidances,
            waypoints=waypoints,
        )

        status = response.get("status")
        if status != "OK":
            error_msg = response.get("error_message", "Unknown route provider error")
            raise ValueError(f"Google Directions API error: {status} - {error_msg}")

        return [
            CanonicalRoute.from_google_directions_route(
                route=route,
                index=index,
                provider=self.provider_name,
            )
            for index, route in enumerate(response.get("routes", []))
        ]


class GoogleRoutesProvider:
    provider_name = "google_routes"
    api_version = "routes_v2_computeRoutes"

    def __init__(
        self,
        service: Optional[BaseService] = None,
        api_key: Optional[str] = None,
    ):
        self._service = service or BaseService(
            "https://routes.googleapis.com",
            name="GoogleRoutes",
        )
        self._api_key = api_key if api_key is not None else config.get_google_api_key()

    async def get_route_alternatives(
        self,
        start: GeoPoint,
        end: GeoPoint,
        *,
        alternatives: bool = True,
        departure_time: Optional[int] = None,
        traffic_model: str = "best_guess",
        avoidances: Optional[List[str]] = None,
        waypoints: Optional[List[GeoPoint]] = None,
    ) -> List[CanonicalRoute]:
        """Google Routes v2 (computeRoutes) primary provider.

        Note: ``traffic_model`` is accepted for symmetry with the
        ``RoutingProvider`` Protocol and ``GoogleDirectionsProvider``, but is
        NOT consumed here. Google Routes v2 selects ``routingPreference``
        (TRAFFIC_AWARE_OPTIMAL vs TRAFFIC_UNAWARE) based on
        ``departure_time`` presence; an explicit ``traffic_model`` argument
        is silently ignored.
        """
        body = _build_google_routes_body(
            start=start,
            end=end,
            alternatives=alternatives,
            departure_time=departure_time,
            avoidances=avoidances,
            waypoints=waypoints,
        )

        response = await self._service.request(
            method="POST",
            endpoint="/directions/v2:computeRoutes",
            headers={
                "X-Goog-Api-Key": self._api_key,
                "X-Goog-FieldMask": GOOGLE_ROUTES_FIELD_MASK,
            },
            json=body,
        )

        routes = response.get("routes", []) if isinstance(response, dict) else []
        if not routes:
            raise ValueError("Google Routes API returned no routes")

        canonical_routes = [
            CanonicalRoute.from_google_routes_route(
                route=route,
                index=index,
                provider=self.provider_name,
            )
            for index, route in enumerate(routes)
        ]
        canonical_routes = [
            route for route in canonical_routes
            if route.polyline and route.legs and route.distance_km > 0
        ]
        if not canonical_routes:
            raise ValueError("Google Routes API response could not be normalized")
        return canonical_routes


def _build_google_routes_body(
    *,
    start: GeoPoint,
    end: GeoPoint,
    alternatives: bool,
    departure_time: Optional[int],
    avoidances: Optional[List[str]],
    waypoints: Optional[List[GeoPoint]],
) -> dict:
    avoidances = avoidances or []
    waypoints = waypoints or []

    body = {
        "origin": _routes_waypoint(start),
        "destination": _routes_waypoint(end),
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE_OPTIMAL" if departure_time else "TRAFFIC_UNAWARE",
        "computeAlternativeRoutes": bool(alternatives and not waypoints),
        "routeModifiers": {
            "avoidTolls": "tolls" in avoidances,
            "avoidHighways": "highways" in avoidances,
            "avoidFerries": "ferries" in avoidances,
        },
    }

    if departure_time:
        body["departureTime"] = datetime.fromtimestamp(
            departure_time,
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")

    if waypoints:
        # Conservative first pass: no via/side-of-road flags here. The app keeps
        # station-side validation in the existing Snap-to-Roads/station layer.
        body["intermediates"] = [_routes_waypoint(point) for point in waypoints]

    return body


def _routes_waypoint(point: GeoPoint) -> dict:
    return {
        "location": {
            "latLng": {
                "latitude": point.lat,
                "longitude": point.lon,
            }
        }
    }
