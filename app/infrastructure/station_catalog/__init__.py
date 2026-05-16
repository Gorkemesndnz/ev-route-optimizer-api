"""
Station Catalog
=================

Provider-neutral istasyon normalization katmanı.

Ana modeller:
    NormalizedStation, NormalizedConnector
    AvailabilityStatus, PowerSource

Adapter'lar:
    parse_google_place / parse_google_places  — Google Places (New) raw → NormalizedStation
    parse_ocm_station  / parse_ocm_stations   — OCM /poi raw → NormalizedStation
    enrich_unknown_power_from_ocm             — Google unknown kW istasyonlarına OCM kW yapıştır

Sabitler:
    UNKNOWN_POWER_PLANNING_KW (=50.0): power_known=False olduğunda
        planning hesaplarında kullanılan konservatif güç değeri.
"""

from app.infrastructure.station_catalog.constants import (
    DATA_CONFIDENCE_PENALTY_UNKNOWN_AVAILABILITY,
    DATA_CONFIDENCE_PENALTY_UNKNOWN_POWER,
    DC_POWER_THRESHOLD_KW,
    PROVIDER_GOOGLE,
    PROVIDER_OCM,
    UNKNOWN_AC_POWER_PLANNING_KW,
    UNKNOWN_DC_POWER_PLANNING_KW,
    UNKNOWN_POWER_PLANNING_KW,
)
from app.infrastructure.station_catalog.google_adapter import (
    parse_google_connector,
    parse_google_place,
    parse_google_places,
)
from app.infrastructure.station_catalog.models import (
    AvailabilityStatus,
    NormalizedConnector,
    NormalizedStation,
    PowerSource,
)
from app.infrastructure.station_catalog.ocm_adapter import (
    enrich_unknown_power_from_ocm,
    parse_ocm_connector,
    parse_ocm_station,
    parse_ocm_stations,
)
from app.infrastructure.station_catalog.provider import StationProvider

__all__ = [
    # Models
    "NormalizedStation",
    "NormalizedConnector",
    "AvailabilityStatus",
    "PowerSource",
    # Provider
    "StationProvider",
    # Google
    "parse_google_place",
    "parse_google_places",
    "parse_google_connector",
    # OCM
    "parse_ocm_station",
    "parse_ocm_stations",
    "parse_ocm_connector",
    "enrich_unknown_power_from_ocm",
    # Constants
    "UNKNOWN_POWER_PLANNING_KW",
    "UNKNOWN_AC_POWER_PLANNING_KW",
    "UNKNOWN_DC_POWER_PLANNING_KW",
    "DC_POWER_THRESHOLD_KW",
    "DATA_CONFIDENCE_PENALTY_UNKNOWN_POWER",
    "DATA_CONFIDENCE_PENALTY_UNKNOWN_AVAILABILITY",
    "PROVIDER_GOOGLE",
    "PROVIDER_OCM",
]
