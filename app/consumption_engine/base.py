"""
Faz 4: ConsumptionEngine — Abstract Base Class (ABC)

Tüm tüketim motorlarının (Fizik, ML, Hibrit) uyması gereken kontrat.
Dönüş tipi açıkça List[SegmentWithConsumption] olarak belirtilmiştir;
bu SOCSimulator'ün downstream beklentisidir.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from app.soc_simulator import SegmentWithConsumption


class ConsumptionEngine(ABC):
    """
    EV tüketim hesaplama motorlarının soyut arayüzü.
    
    Her motor bu sınıfı miras almalı ve `estimate()` metodunu
    aynı imza ve dönüş tipiyle uygulamalıdır.
    """
    
    @abstractmethod
    def estimate(
        self,
        vehicle,
        segments,
        weather_checkpoints: Optional[list] = None,
        temperature_celsius: float = 20.0,
        wind_speed_mps: float = 0.0,
        weather_condition: str = "clear",
        extra_load_kg: float = 0.0,
        passenger_count: int = 1,
        child_count: int = 0
    ) -> List[SegmentWithConsumption]:
        """
        Verilen segmentler için tüketim hesapla.
        
        Args:
            vehicle: Araç fizik profili (VehiclePhysicsProfile)
            segments: Rota segmentleri (List[RouteSegment])
            weather_checkpoints: Hava durumu kontrol noktaları
            temperature_celsius: Fallback sıcaklık (°C)
            wind_speed_mps: Fallback rüzgar hızı (m/s)
            weather_condition: Fallback hava durumu
            extra_load_kg: Ekstra yük (kg)
            passenger_count: Yetişkin yolcu sayısı
            child_count: Çocuk yolcu sayısı
            
        Returns:
            List[SegmentWithConsumption]: SOCSimulator için hazır segment listesi.
            Döndürülen listenin eleman sayısı girişteki segments ile
            BİREBİR eşleşmeli, aksi takdirde SOCSimulator çökecektir.
        """
        ...
