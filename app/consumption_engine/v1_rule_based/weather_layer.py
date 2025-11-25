from app.models import WeatherInfo, WeatherCondition
import math


class WeatherEffectCalculator:
    """
    Hava Durumu Katmanı (V1.5 – Heading Destekli):

    ✔ Sıcaklık etkisi
    ✔ Rüzgar hızı etkisi
    ✔ Rüzgar yönü (Headwind, Tailwind, Crosswind)
    ✔ Yağış etkisi
    """

    # -----------------------------
    # 1) Sıcaklık Faktörü
    # -----------------------------
    @staticmethod
    def temperature_factor(temp_c: float) -> float:
        if temp_c <= -10:
            return 1.40
        elif temp_c <= 0:
            return 1.25
        elif temp_c <= 10:
            return 1.15
        elif temp_c <= 18:
            return 1.05
        elif temp_c >= 40:
            return 1.20
        elif temp_c >= 28:
            return 1.10
        return 1.00

    # -----------------------------
    # 2) Rüzgar Yönü + Hızı Faktörü
    # -----------------------------
    @staticmethod
    def wind_factor(wind_speed_mps: float, vehicle_heading_deg: float, wind_direction_deg: float) -> float:
        """
        Rüzgar hızı + aracın gittiği yöne göre rüzgar yönü:
        - Headwind → tüketimi 10–25% artırır
        - Crosswind → 3–10% artırır
        - Tailwind → 3–10% azaltabilir
        """

        if wind_speed_mps < 1:  # neredeyse rüzgarsız
            return 1.00

        # Rüzgarın araca göre göreceli açısı
        relative_angle = abs(vehicle_heading_deg - wind_direction_deg) % 360
        if relative_angle > 180:
            relative_angle = 360 - relative_angle

        # Rüzgar türü
        if relative_angle <= 45:  # Headwind
            base = 1.10
        elif relative_angle <= 135:  # Crosswind
            base = 1.05
        else:  # Tailwind
            base = 0.95

        # Rüzgar hızının etkisi
        if wind_speed_mps <= 3:
            speed_factor = 1.00
        elif wind_speed_mps <= 7:
            speed_factor = 1.05
        elif wind_speed_mps <= 12:
            speed_factor = 1.10
        else:
            speed_factor = 1.20

        final = base * speed_factor

        # Tailwind hafif indirim sağlasın (0.90–1.00 arası)
        if relative_angle >= 135:
            final = max(0.90, min(1.00, final))

        return min(1.30, max(0.90, final))

    # -----------------------------
    # 3) Yağış Faktörü
    # -----------------------------
    @staticmethod
    def precipitation_factor(condition: WeatherCondition) -> float:
        if condition == WeatherCondition.RAIN:
            return 1.10
        elif condition == WeatherCondition.SNOW:
            return 1.25
        elif condition == WeatherCondition.FOG:
            return 1.05
        return 1.00

    # -----------------------------
    # 4) Final Weather Factor
    # -----------------------------
    @staticmethod
    def calculate_weather_factor(weather: WeatherInfo, vehicle_heading_deg: float) -> float:
        temp_f = WeatherEffectCalculator.temperature_factor(weather.temp_c)
        wind_f = WeatherEffectCalculator.wind_factor(
            wind_speed_mps=weather.wind_speed_mps,
            vehicle_heading_deg=vehicle_heading_deg,
            wind_direction_deg=weather.wind_direction_deg
        )
        precip_f = WeatherEffectCalculator.precipitation_factor(weather.condition)

        final = temp_f * wind_f * precip_f

        # Aşırı uçları engelle:
        return max(0.9, min(2.5, final))
