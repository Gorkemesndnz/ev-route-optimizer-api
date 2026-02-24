"""
Insight Service (InsightEngine)
================================

🧠 V3.5: Akıllı Seyahat Asistanı — Kural-Tabanlı Insight Engine

Rota hesaplaması tamamlandıktan sonra, mevcut verilerden
deterministik kurallara dayalı seyahat ipuçları üretir.

Sıfır ek bağımlılık, sıfır ek API çağrısı.
Tüm insight'lar template-based ve veri-güdümlüdür.

Kural Kategorileri:
1. 🏔️ Rakım Analizi — Dik tırmanış/iniş uyarıları
2. 🌤️ Hava Durumu Etkisi — Soğuk/sıcak/yağış etkileri
3. ⚡ Şarj Stratejisi — Uzun bekleme, çoklu durak optimizasyonu
4. 🐢 Hız/Verimlilik — SOC kritik durumları, enerji tasarrufu
5. 💰 Maliyet — Şarj maliyeti bilgileri
6. 🌱 Çevre — CO2 tasarruf bilgisi
"""

from typing import List, Optional, Dict, Any
from app.models import RouteInsight, InsightType, WeatherInfo, WeatherCondition
from app.utils.logger import get_logger

logger = get_logger("InsightEngine")


class InsightEngine:
    """
    Kural-tabanlı seyahat insight üreticisi.
    
    Rota hesaplama pipeline'ı tamamlandıktan sonra çağrılır.
    Mevcut hesaplama verilerini analiz ederek kullanıcıya
    anlamlı, bağlama özel ipuçları üretir.
    """
    
    def analyze(
        self,
        route_distance_km: float,
        route_duration_min: float,
        elevation_gain_m: float,
        elevation_loss_m: float,
        total_consumption_kwh: float,
        start_soc: float,
        end_soc: float,
        charge_stops: int,
        battery_kwh: float,
        start_weather: Optional[WeatherInfo] = None,
        end_weather: Optional[WeatherInfo] = None,
        avg_weather: Optional[WeatherInfo] = None,
        legs: Optional[List] = None,
        co2_savings_kg: float = 0.0,
        total_charging_cost: float = 0.0,
    ) -> List[RouteInsight]:
        """
        Tüm kuralları çalıştırarak insight listesi üretir.
        
        Args:
            Rota hesaplamasından elde edilen tüm veriler.
            
        Returns:
            Relevance score'a göre sıralanmış RouteInsight listesi.
        """
        insights: List[RouteInsight] = []
        
        try:
            # 1. Rakım kuralları
            insights.extend(self._check_elevation(
                elevation_gain_m, elevation_loss_m,
                end_soc, route_distance_km
            ))
            
            # 2. Hava durumu kuralları
            insights.extend(self._check_weather(
                start_weather, end_weather, avg_weather,
                route_distance_km, total_consumption_kwh, battery_kwh
            ))
            
            # 3. Şarj stratejisi kuralları
            insights.extend(self._check_charging_strategy(
                legs, charge_stops, end_soc, route_distance_km
            ))
            
            # 4. SOC / Verimlilik kuralları
            insights.extend(self._check_efficiency(
                start_soc, end_soc, total_consumption_kwh,
                route_distance_km, battery_kwh, charge_stops
            ))
            
            # 5. Maliyet bilgileri
            insights.extend(self._check_cost(
                total_charging_cost, charge_stops
            ))
            
            # 6. Çevre (CO2)
            insights.extend(self._check_environment(
                co2_savings_kg, route_distance_km
            ))
            
        except Exception as e:
            logger.warning(f"InsightEngine analysis failed: {e}")
        
        # Relevance score'a göre sırala (yüksek → düşük)
        insights.sort(key=lambda i: i.relevance_score, reverse=True)
        
        logger.info(f"InsightEngine produced {len(insights)} insights")
        return insights
    
    # =========================================================================
    # 1. RAKIM ANALİZİ
    # =========================================================================
    def _check_elevation(
        self,
        gain_m: float,
        loss_m: float,
        end_soc: float,
        distance_km: float,
    ) -> List[RouteInsight]:
        insights = []
        
        net_climb = gain_m - loss_m
        
        # Kural 1.1: Çok dik tırmanış (>500m toplam)
        if gain_m > 500:
            severity = min(1.0, gain_m / 1500)  # 1500m'de max
            insights.append(RouteInsight(
                type=InsightType.WARN,
                title="Dik Tırmanış Uyarısı",
                message=(
                    f"Rotanızda toplam {gain_m:.0f}m yükselti var. "
                    f"Tırmanış sırasında batarya normalden daha hızlı tükenecek. "
                    f"Ancak inişlerde rejeneratif frenleme ile enerji geri kazanılacak."
                ),
                icon="🏔️",
                relevance_score=round(0.6 + severity * 0.3, 2),
            ))
        
        # Kural 1.2: Yüksek net tırmanış + düşük SOC
        if net_climb > 300 and end_soc < 20:
            insights.append(RouteInsight(
                type=InsightType.WARN,
                title="Yükselti + Düşük Batarya Uyarısı",
                message=(
                    f"Rotanızda {net_climb:.0f}m net yükselti var ve varışta "
                    f"batarya %{end_soc:.0f}'e düşecek. Güvenli bir seyahat için "
                    f"şarj durağında biraz daha fazla şarj etmeyi düşünebilirsiniz."
                ),
                icon="⛰️",
                relevance_score=0.85,
            ))
        
        # Kural 1.3: Pozitif regen kazanımı (çok fazla iniş)
        if loss_m > 400 and loss_m > gain_m * 1.5:
            regen_hint_kwh = round((loss_m - gain_m) * 0.002, 1)  # Yaklaşık tahmin
            insights.append(RouteInsight(
                type=InsightType.SAVING,
                title="İniş Avantajı",
                message=(
                    f"Rotanızda {loss_m:.0f}m iniş var (tırmanış: {gain_m:.0f}m). "
                    f"İniş bölgelerinde rejeneratif frenleme ile yaklaşık "
                    f"{regen_hint_kwh} kWh enerji geri kazanabilirsiniz."
                ),
                icon="📉",
                relevance_score=0.45,
            ))
        
        return insights
    
    # =========================================================================
    # 2. HAVA DURUMU ETKİSİ
    # =========================================================================
    def _check_weather(
        self,
        start_weather: Optional[WeatherInfo],
        end_weather: Optional[WeatherInfo],
        avg_weather: Optional[WeatherInfo],
        distance_km: float,
        consumption_kwh: float,
        battery_kwh: float,
    ) -> List[RouteInsight]:
        insights = []
        
        weather = avg_weather or start_weather
        if not weather:
            return insights
        
        temp = weather.temp_c
        condition = weather.condition
        wind_speed = weather.wind_speed_mps
        
        # Kural 2.1: Soğuk hava (<5°C)
        if temp < 5:
            efficiency_loss = round(max(5, min(30, (5 - temp) * 2)), 0)
            insights.append(RouteInsight(
                type=InsightType.WARN,
                title="Soğuk Hava Uyarısı",
                message=(
                    f"Rota boyunca sıcaklık {temp:.0f}°C. Soğuk havada batarya "
                    f"verimliliği yaklaşık %{efficiency_loss:.0f} düşebilir. "
                    f"Menzil tahminleri buna göre ayarlanmıştır. "
                    f"Isıtmayı ekonomik modda kullanmanızı öneririz."
                ),
                icon="❄️",
                relevance_score=0.80,
            ))
        
        # Kural 2.2: Sıcak hava (>35°C)
        elif temp > 35:
            insights.append(RouteInsight(
                type=InsightType.INFO,
                title="Sıcak Hava Bilgisi",
                message=(
                    f"Rota boyunca sıcaklık {temp:.0f}°C. Yüksek sıcaklıkta "
                    f"klima kullanımı enerji tüketimini artırabilir. "
                    f"Şarj hızı da yüksek batarya sıcaklığında düşebilir."
                ),
                icon="🌡️",
                relevance_score=0.55,
            ))
        
        # Kural 2.3: Yağmur/Kar
        if condition == WeatherCondition.RAIN:
            insights.append(RouteInsight(
                type=InsightType.WARN,
                title="Yağışlı Hava",
                message=(
                    "Rota boyunca yağış bekleniyor. Yol tutuşu zayıflayabilir ve "
                    "lastik sürtünmesi nedeniyle enerji tüketimi %5-10 artabilir. "
                    "Hızınızı uyarlayarak güvenli sürüş yapın."
                ),
                icon="🌧️",
                relevance_score=0.70,
            ))
        elif condition == WeatherCondition.SNOW:
            insights.append(RouteInsight(
                type=InsightType.WARN,
                title="Kar Yağışı Uyarısı",
                message=(
                    "Rota üzerinde kar yağışı var. Enerji tüketimi %15-25 "
                    "artabilir. Kış lastikleri kontrol edin ve hızınızı düşürün. "
                    "Menzil tahminleri kar koşullarına göre ayarlanmıştır."
                ),
                icon="🌨️",
                relevance_score=0.90,
            ))
        
        # Kural 2.4: Güçlü rüzgar (>8 m/s ≈ 29 km/h)
        if wind_speed > 8:
            wind_kmh = round(wind_speed * 3.6, 0)
            insights.append(RouteInsight(
                type=InsightType.INFO,
                title="Güçlü Rüzgar",
                message=(
                    f"Rota boyunca {wind_kmh:.0f} km/s hızında rüzgar var. "
                    f"Karşı rüzgar enerji tüketimini artırabilir. "
                    f"Menzil hesaplamaları rüzgar etkisini içermektedir."
                ),
                icon="💨",
                relevance_score=0.50,
            ))
        
        # Kural 2.5: Başlangıç/varış arası farklı hava durumu
        if start_weather and end_weather:
            temp_diff = abs(start_weather.temp_c - end_weather.temp_c)
            if temp_diff > 8:
                insights.append(RouteInsight(
                    type=InsightType.INFO,
                    title="Sıcaklık Farkı",
                    message=(
                        f"Başlangıçta {start_weather.temp_c:.0f}°C, "
                        f"varışta {end_weather.temp_c:.0f}°C bekleniyor. "
                        f"{temp_diff:.0f}°C'lik fark seyahat boyunca "
                        f"tüketim değişikliğine neden olabilir."
                    ),
                    icon="🌡️",
                    relevance_score=0.45,
                ))
        
        return insights
    
    # =========================================================================
    # 3. ŞARJ STRATEJİSİ
    # =========================================================================
    def _check_charging_strategy(
        self,
        legs: Optional[List],
        charge_stops: int,
        end_soc: float,
        distance_km: float,
    ) -> List[RouteInsight]:
        insights = []
        
        if not legs:
            return insights
        
        # Şarj bacaklarını bul
        charge_legs = [l for l in legs if getattr(l, 'type', '') == 'charge']
        
        # Kural 3.1: Uzun şarj bekleme (>40 dk)
        for i, cl in enumerate(charge_legs):
            duration = getattr(cl, 'duration_minutes', 0)
            if duration > 40:
                target_soc = getattr(cl, 'target_soc_percent', 80)
                quick_soc = min(target_soc, 60)
                time_save = round(duration * 0.3, 0)  # Yaklaşık %30 tasarruf
                insights.append(RouteInsight(
                    type=InsightType.TIP,
                    title=f"{i+1}. Durakta Şarj İpucu",
                    message=(
                        f"Bu istasyonda tahmini bekleme süresi {duration:.0f} dk. "
                        f"Sadece %{quick_soc:.0f}'a kadar şarj ederek yaklaşık "
                        f"{time_save:.0f} dk kazanabilirsiniz. Batarya %80 üzeri "
                        f"şarj hızı belirgin şekilde yavaşlar."
                    ),
                    icon="⚡",
                    relevance_score=0.65,
                ))
                break  # Sadece en uzun durak için göster
        
        # Kural 3.2: Çoklu şarj durağı (>2)
        if charge_stops > 2:
            total_charge_time = sum(
                getattr(l, 'duration_minutes', 0) for l in charge_legs
            )
            insights.append(RouteInsight(
                type=InsightType.INFO,
                title="Çoklu Şarj Durağı",
                message=(
                    f"Rotanızda {charge_stops} şarj durağı planlandı "
                    f"(toplam ~{total_charge_time:.0f} dk bekleme). "
                    f"Uzun yolculuklarda her durakta %60-80 arası şarj "
                    f"etmek, toplam süreyi optimize eder."
                ),
                icon="🔌",
                relevance_score=0.55,
            ))
        
        # Kural 3.3: Şarj gerekmeden varılabilir
        if charge_stops == 0 and end_soc > 20:
            insights.append(RouteInsight(
                type=InsightType.SAVING,
                title="Şarj Gerekmez",
                message=(
                    f"Bu rotayı şarj durağı olmadan tamamlayabilirsiniz! "
                    f"Varışta bataryanız %{end_soc:.0f} seviyesinde olacak."
                ),
                icon="✅",
                relevance_score=0.70,
            ))
        
        return insights
    
    # =========================================================================
    # 4. SOC / VERİMLİLİK
    # =========================================================================
    def _check_efficiency(
        self,
        start_soc: float,
        end_soc: float,
        consumption_kwh: float,
        distance_km: float,
        battery_kwh: float,
        charge_stops: int,
    ) -> List[RouteInsight]:
        insights = []
        
        # Tüketim oranı (Wh/km)
        wh_per_km = (consumption_kwh / distance_km * 1000) if distance_km > 0 else 0
        
        # Kural 4.1: Kritik varış SOC (<10%)
        if end_soc < 10 and charge_stops > 0:
            insights.append(RouteInsight(
                type=InsightType.WARN,
                title="Kritik Batarya Seviyesi",
                message=(
                    f"Varışta batarya %{end_soc:.0f} seviyesinde olacak. "
                    f"Hızınızı 10-20 km/s düşürerek batarya seviyenizi artırabilirsiniz. "
                    f"Ayrıca HVAC (klima/ısıtma) kullanımını azaltmayı düşünün."
                ),
                icon="🔋",
                relevance_score=0.90,
            ))
        
        # Kural 4.2: Verimli sürüş (düşük Wh/km)
        if wh_per_km > 0 and wh_per_km < 150 and distance_km > 50:
            insights.append(RouteInsight(
                type=InsightType.SAVING,
                title="Verimli Rota",
                message=(
                    f"Bu rota {wh_per_km:.0f} Wh/km tüketim oranıyla oldukça verimli. "
                    f"Düz bir güzergah ve uygun hava koşulları menzili artırıyor."
                ),
                icon="🌿",
                relevance_score=0.40,
            ))
        
        # Kural 4.3: Yüksek tüketim (>250 Wh/km)
        elif wh_per_km > 250 and distance_km > 30:
            insights.append(RouteInsight(
                type=InsightType.INFO,
                title="Yüksek Enerji Tüketimi",
                message=(
                    f"Bu rotanın tüketim oranı {wh_per_km:.0f} Wh/km ile "
                    f"ortalamanın üzerinde. Bunun nedeni yüksek rakım, "
                    f"kötü hava koşulları veya ek yük olabilir."
                ),
                icon="📊",
                relevance_score=0.50,
            ))
        
        # Kural 4.4: Uzun yol (>300km)
        if distance_km > 300:
            estimated_hours = round(distance_km / 90, 1)  # ~90km/h ort.
            insights.append(RouteInsight(
                type=InsightType.TIP,
                title="Uzun Yol İpucu",
                message=(
                    f"{distance_km:.0f} km'lik bu yolculukta tahmini "
                    f"~{estimated_hours} saat sürüş yapacaksınız. "
                    f"Her 2 saatte bir mola vermeniz önerilir. "
                    f"Molalarınızı şarj durakları ile eşleştirin."
                ),
                icon="🛣️",
                relevance_score=0.35,
            ))
        
        return insights
    
    # =========================================================================
    # 5. MALİYET   
    # =========================================================================
    def _check_cost(
        self,
        total_charging_cost: float,
        charge_stops: int,
    ) -> List[RouteInsight]:
        insights = []
        
        if charge_stops > 0 and total_charging_cost > 0:
            avg_cost = total_charging_cost / charge_stops
            insights.append(RouteInsight(
                type=InsightType.INFO,
                title="Şarj Maliyeti",
                message=(
                    f"Toplam tahmini şarj maliyeti: {total_charging_cost:.2f} ₺ "
                    f"({charge_stops} durak, ortalama {avg_cost:.2f} ₺/durak). "
                    f"Gerçek fiyatlar istasyon operatörüne göre değişebilir."
                ),
                icon="💰",
                relevance_score=0.50,
            ))
        
        return insights
    
    # =========================================================================
    # 6. ÇEVRE (CO2)
    # =========================================================================
    def _check_environment(
        self,
        co2_savings_kg: float,
        distance_km: float,
    ) -> List[RouteInsight]:
        insights = []
        
        if co2_savings_kg > 1:
            trees = round(co2_savings_kg / 21, 1)
            insights.append(RouteInsight(
                type=InsightType.SAVING,
                title="Çevre Katkınız",
                message=(
                    f"Bu yolculukta {co2_savings_kg:.1f} kg CO2 tasarrufu "
                    f"sağladınız. Bu, yaklaşık {trees} ağacın yıllık "
                    f"CO2 absorpsiyonuna eşdeğer!"
                ),
                icon="🌱",
                relevance_score=0.35,
            ))
        
        return insights


# Tek instance
insight_engine = InsightEngine()
