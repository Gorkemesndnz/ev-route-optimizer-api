"""Pricing Service v1.0
======================

EV şarj istasyonu fiyat hesaplama servisi.

Türkiye'deki şarj ağlarının güncel fiyatlarını içeren 
data/charging_tariffs.json dosyasını kullanır.

Desteklenen markalar: Eşarj, ZES, Trugo, Voltrun, Beefull, Tesla, Shell vb.
Bilinmeyen markalar için varsayılan 11 TL/kWh kullanılır.

Kullanım:
    from app.services.pricing_service import pricing_service
    
    price = pricing_service.get_price_per_kwh(
        station_name="ZES Ankara",
        power_kw=150,
        socket_type="DC"
    )
    
    cost = pricing_service.estimate_charge_cost(
        station_name="Eşarj Migros",
        energy_kwh=25.0,
        power_kw=120
    )
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from functools import lru_cache
from app.utils.logger import get_logger

logger = get_logger("pricing_service")

# Varsayılan fiyat (bilinmeyen markalar için)
DEFAULT_PRICE_PER_KWH = 11.0  # TL


class PricingService:
    """EV şarj istasyonu fiyat servisi."""
    
    def __init__(self):
        self._tariffs: List[Dict[str, Any]] = []
        self._brand_lookup: Dict[str, Dict[str, Any]] = {}
        self._load_tariffs()
    
    def _load_tariffs(self):
        """charging_tariffs.json dosyasını yükler."""
        try:
            tariff_path = Path(__file__).parent.parent.parent / "data" / "charging_tariffs.json"
            
            if not tariff_path.exists():
                logger.warning(f"Tariff file not found: {tariff_path}")
                return
            
            with open(tariff_path, "r", encoding="utf-8") as f:
                self._tariffs = json.load(f)
            
            # Marka lookup tablosu oluştur (hızlı erişim için)
            for brand_data in self._tariffs:
                brand_name = brand_data["brand"].lower()
                self._brand_lookup[brand_name] = brand_data
                
                # Alias'ları da ekle
                for alias in brand_data.get("aliases", []):
                    self._brand_lookup[alias.lower()] = brand_data
            
            logger.info(f"Loaded {len(self._tariffs)} charging tariffs, {len(self._brand_lookup)} brand aliases")
            
        except Exception as e:
            logger.error(f"Failed to load tariffs: {e}")
    
    def _find_brand(self, station_name: str) -> Optional[Dict[str, Any]]:
        """
        İstasyon adından marka eşleştirmesi yapar.
        
        Args:
            station_name: İstasyon adı (örn: "ZES Ankara Otoyol", "Eşarj - Migros")
        
        Returns:
            Marka verisi veya None
        """
        if not station_name:
            return None
        
        name_lower = station_name.lower()
        
        # Önce tam eşleşme dene
        for alias, brand_data in self._brand_lookup.items():
            if alias in name_lower:
                return brand_data
        
        return None
    
    def get_price_per_kwh(
        self,
        station_name: str,
        power_kw: float = 50.0,
        socket_type: str = "DC"
    ) -> float:
        """
        İstasyon için kWh başına fiyat döndürür.
        
        Args:
            station_name: İstasyon adı
            power_kw: Şarj gücü (kW)
            socket_type: Soket tipi ("DC" veya "AC")
        
        Returns:
            Fiyat (TL/kWh)
        """
        brand_data = self._find_brand(station_name)
        
        if not brand_data:
            logger.debug(f"Unknown brand for '{station_name}', using default {DEFAULT_PRICE_PER_KWH} TL/kWh")
            return DEFAULT_PRICE_PER_KWH
        
        # İlgili soket tipini bul
        tariffs = brand_data.get("tariffs", [])
        socket_upper = socket_type.upper()
        
        for tariff in tariffs:
            if tariff.get("socket_type", "").upper() == socket_upper:
                threshold = tariff.get("power_threshold_kw", 0)
                
                if power_kw < threshold:
                    price = tariff.get("below_price", DEFAULT_PRICE_PER_KWH)
                else:
                    price = tariff.get("above_price", DEFAULT_PRICE_PER_KWH)
                
                logger.debug(
                    f"Price for '{station_name}': {price} TL/kWh",
                    brand=brand_data["brand"],
                    power_kw=power_kw,
                    socket_type=socket_type
                )
                return price
        
        # Soket tipi bulunamadı, varsayılan
        return DEFAULT_PRICE_PER_KWH
    
    def estimate_charge_cost(
        self,
        station_name: str,
        energy_kwh: float,
        power_kw: float = 50.0,
        socket_type: str = "DC"
    ) -> float:
        """
        Tahmini şarj maliyeti hesaplar.
        
        Args:
            station_name: İstasyon adı
            energy_kwh: Şarj edilecek enerji (kWh)
            power_kw: Şarj gücü (kW)
            socket_type: Soket tipi
        
        Returns:
            Maliyet (TL)
        """
        price_per_kwh = self.get_price_per_kwh(station_name, power_kw, socket_type)
        return energy_kwh * price_per_kwh
    
    def get_all_brands(self) -> List[str]:
        """Tüm marka isimlerini döndürür."""
        return [t["brand"] for t in self._tariffs]
    
    def get_average_dc_price(self) -> float:
        """Ortalama DC fiyatı hesaplar."""
        prices = []
        for brand_data in self._tariffs:
            for tariff in brand_data.get("tariffs", []):
                if tariff.get("socket_type", "").upper() == "DC":
                    prices.append(tariff.get("below_price", 0))
                    prices.append(tariff.get("above_price", 0))
        
        if prices:
            return sum(prices) / len(prices)
        return DEFAULT_PRICE_PER_KWH


# Singleton instance
pricing_service = PricingService()
