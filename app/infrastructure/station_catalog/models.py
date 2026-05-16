"""
Station Catalog — Normalized Models
=====================================

Provider-neutral istasyon ve konektör modelleri.

Google ve OCM raw datası bu modele indirgenir; optimizer bu modeli kullanır.
"power_known" / "availability_status" alanları konservatif planlamayı ve düşük
güven skor cezasını mümkün kılar.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from app.infrastructure.station_catalog.constants import (
    UNKNOWN_AC_POWER_PLANNING_KW,
    UNKNOWN_DC_POWER_PLANNING_KW,
    UNKNOWN_POWER_PLANNING_KW,
)


class AvailabilityStatus(str, Enum):
    """
    Bir istasyonun anlık kullanılabilirlik durumu.

    - AVAILABLE: availableCount > 0, route adayı, normal skor.
    - UNAVAILABLE: tüm soketler out-of-service. Route adayı OLMAZ.
    - UNKNOWN: availability bilgisi eksik. Route adayı kalır ama skor penalty alır.
    """
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class PowerSource(str, Enum):
    """
    power_kw değerinin kaynağı.

    - GOOGLE_PLACES: Google evChargeOptions.connectorAggregation.maxChargeRateKw
    - OCM: OCM Connections.PowerKW
    - OCM_CROSSREF: Google'da kW yoktu, OCM aynı konumda doldurdu (enrichment)
    - UNKNOWN: hiçbir kaynak doldurmadı; UNKNOWN_POWER_PLANNING_KW konservatif kullanılır.
    """
    GOOGLE_PLACES = "google_places"
    OCM = "ocm"
    OCM_CROSSREF = "ocm_crossref"
    UNKNOWN = "unknown"


@dataclass
class NormalizedConnector:
    """
    Bir konektör tipi/grubu için normalize edilmiş bilgi.

    Google `connectorAggregation` ve OCM `Connections` aynı yapıya çevrilir.
    """
    plug_type: str
    """Plug ailesi (CCS2 / Type2 / CHAdeMO / Tesla / unknown)."""

    power_kw: Optional[float]
    """Maksimum şarj gücü (kW). None ise power_known=False."""

    power_known: bool
    """power_kw kaynak data'sında mevcut muydu?"""

    power_source: PowerSource
    """power_kw değerinin nereden geldiği."""

    count: int = 1
    """Bu tip soketin istasyondaki adedi (Google connectorCount; OCM 1)."""

    available_count: Optional[int] = None
    """Şu an boş soket sayısı. None ise availability bilinmiyor."""

    out_of_service_count: Optional[int] = None
    """Out-of-service soket sayısı."""

    availability_last_update_time: Optional[str] = None
    """Google availabilityLastUpdateTime ISO string. Bilinmiyorsa None."""

    @property
    def planning_power_kw(self) -> float:
        """
        Planning hesaplarında kullanılacak konservatif güç değeri.

        - power_known=True ise gerçek güç
        - power_known=False ise UNKNOWN_POWER_PLANNING_KW (=50 kW konservatif)
        """
        if self.power_known and self.power_kw is not None and self.power_kw > 0:
            return float(self.power_kw)
        plug = (self.plug_type or "").upper()
        if plug in ("TYPE2", "J1772"):
            return UNKNOWN_AC_POWER_PLANNING_KW
        if plug in ("CCS2", "CCS1", "CHADEMO", "TESLA"):
            return UNKNOWN_DC_POWER_PLANNING_KW
        return UNKNOWN_POWER_PLANNING_KW


@dataclass
class NormalizedStation:
    """
    Provider-neutral istasyon temsili.

    Google Places ve OCM POI raw data'sı bu yapıya çevrilir; downstream
    (station_finder, leg_builder, safe_harbor) bu modeli kullanır.
    """
    source_provider: str
    """'google' veya 'ocm' — primer veri kaynağı."""

    source_id: str
    """Provider'ın kendi id'si (Google place_id veya OCM ID)."""

    place_id: Optional[str]
    """Google place_id. Google kaynaklıysa source_id ile aynıdır; OCM ise None."""

    name: str
    lat: float
    lon: float
    address: Optional[str]

    connectors: List[NormalizedConnector]

    rating: Optional[float] = None
    user_ratings_total: Optional[int] = None
    business_status: Optional[str] = None
    is_open_now: Optional[bool] = None
    types: List[str] = field(default_factory=list)

    @property
    def max_power_kw(self) -> Optional[float]:
        """
        En yüksek bilinen güç. Tüm konektörler bilinmiyorsa None.
        """
        known = [c.power_kw for c in self.connectors if c.power_known and c.power_kw is not None]
        if not known:
            return None
        return max(known)

    @property
    def power_known(self) -> bool:
        """En az bir konektörün gücü biliniyorsa True."""
        return any(c.power_known for c in self.connectors)

    @property
    def planning_power_kw(self) -> float:
        """
        Planning için kullanılacak güç.

        - En az bir konektör için power_known=True ise → max known kW
        - Aksi halde UNKNOWN_POWER_PLANNING_KW
        """
        max_known = self.max_power_kw
        if max_known is not None and max_known > 0:
            return float(max_known)
        if not self.connectors:
            return UNKNOWN_POWER_PLANNING_KW
        return max(c.planning_power_kw for c in self.connectors)

    @property
    def availability_status(self) -> AvailabilityStatus:
        """
        İstasyon-bazlı availability durumu.

        - HİÇBİR konektörün availability'si bilinmiyorsa → UNKNOWN
        - En az bir konektörde availableCount > 0 → AVAILABLE
        - Hepsi out_of_service ise → UNAVAILABLE
        - Karışık → AVAILABLE (en az birinde boş yer var)
        """
        any_known = any(c.available_count is not None for c in self.connectors)
        if not any_known:
            return AvailabilityStatus.UNKNOWN

        any_available = any(
            (c.available_count or 0) > 0 for c in self.connectors
        )
        if any_available:
            return AvailabilityStatus.AVAILABLE

        # Hiç available_count > 0 yok; kontrol et tümü out-of-service mi
        all_out_of_service = all(
            c.available_count is not None
            and c.available_count == 0
            and (c.out_of_service_count or 0) > 0
            for c in self.connectors
        )
        if all_out_of_service:
            return AvailabilityStatus.UNAVAILABLE

        # Karışık veya belirsiz — adayda kalır ama unknown
        return AvailabilityStatus.UNKNOWN

    @property
    def total_available_count(self) -> Optional[int]:
        """Toplam boş soket sayısı; hiç bilgi yoksa None."""
        known = [c.available_count for c in self.connectors if c.available_count is not None]
        return sum(known) if known else None

    @property
    def total_out_of_service_count(self) -> Optional[int]:
        """Toplam out-of-service soket sayısı; hiç bilgi yoksa None."""
        known = [c.out_of_service_count for c in self.connectors if c.out_of_service_count is not None]
        return sum(known) if known else None

    @property
    def availability_last_update_time(self) -> Optional[str]:
        """En yeni availabilityLastUpdateTime — herhangi bir konektörden."""
        times = [
            c.availability_last_update_time
            for c in self.connectors
            if c.availability_last_update_time
        ]
        return max(times) if times else None
