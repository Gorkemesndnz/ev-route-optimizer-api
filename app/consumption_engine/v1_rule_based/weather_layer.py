# Sıcaklığa bağlı verimlilik çarpanı (V1 Kuralları)
# 1.0 = ideal, 1.15 = %15 daha fazla tüketim

IDEAL_TEMPERATURE_C = 20.0  # En ideal sıcaklık (ne ısıtma ne soğutma)


def get_weather_efficiency_multiplier(temperature_celsius: float) -> float:
    """
    Sıcaklığa göre verimlilik çarpanını hesaplar.
    1.0 ideal demektir. 1.0'dan büyükse (örn: 1.15) %15 daha fazla tüketim demektir.
    """
    if temperature_celsius > (IDEAL_TEMPERATURE_C + 5):
        # Çok sıcak (klima kullanımı)
        penalty = ((temperature_celsius - IDEAL_TEMPERATURE_C) / 5) * 0.04
        return 1.0 + penalty

    elif temperature_celsius < (IDEAL_TEMPERATURE_C - 5):
        # Soğuk hava (batarya verimsizliği + ısıtma)
        penalty = ((IDEAL_TEMPERATURE_C - temperature_celsius) / 5) * 0.07
        return 1.0 + penalty

    else:
        # İdeal sıcaklık aralığı (15-25 C)
        return 1.0
