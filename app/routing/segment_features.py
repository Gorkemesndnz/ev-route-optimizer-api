"""Build route segment features from a canonical route."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.route_segmenter import DEFAULT_SEGMENT_KM, RouteSegment, RouteSegmenter
from app.routing.canonical import CanonicalRoute


@dataclass
class SegmentFeatureSet:
    canonical_route: CanonicalRoute
    segmenter: RouteSegmenter
    segments: List[RouteSegment]


class SegmentFeatureBuilder:
    """Compatibility wrapper around RouteSegmenter keyed by CanonicalRoute."""

    def __init__(self, segment_length_km: float = DEFAULT_SEGMENT_KM):
        self.segment_length_km = segment_length_km

    def build(
        self,
        canonical_route: CanonicalRoute,
        *,
        total_elevation_gain_m: float = 0.0,
        total_elevation_loss_m: float = 0.0,
        raw_elevations: Optional[List[float]] = None,
    ) -> SegmentFeatureSet:
        segmenter = RouteSegmenter(segment_length_km=self.segment_length_km)
        segments = segmenter.create_segments(
            polyline=canonical_route.polyline,
            total_elevation_gain_m=total_elevation_gain_m,
            total_elevation_loss_m=total_elevation_loss_m,
            raw_elevations=raw_elevations,
        )
        return SegmentFeatureSet(
            canonical_route=canonical_route,
            segmenter=segmenter,
            segments=segments,
        )
