"""
Station Catalog — StationProvider Interface
=============================================

Provider-neutral istasyon kaynağı arayüzü. Google ve OCM adapter'ları
bu interface'i implement eder; upstream kod sadece NormalizedStation
listesi alır.
"""

from abc import ABC, abstractmethod
from typing import List, Protocol

from app.infrastructure.station_catalog.models import NormalizedStation


class StationProvider(Protocol):
    """
    Bir istasyon veri kaynağının ortak arayüzü.

    Implementasyonlar (Google, OCM, ...) raw provider response'unu alır,
    NormalizedStation listesine çevirir. Filtreleme/skorlama ve seçim
    upstream'de (station_finder) yapılır.
    """

    name: str
    """Provider tanımlayıcısı: 'google' veya 'ocm'."""

    @abstractmethod
    async def search_nearby(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        max_results: int = 20,
    ) -> List[NormalizedStation]:
        """
        Belirtilen koordinatın etrafındaki istasyonları normalize edilmiş halde döner.

        Bilinmeyen kW veya availability olan istasyonlar elenmez; downstream
        kod power_known / availability_status alanlarına göre karar verir.
        """
        ...
