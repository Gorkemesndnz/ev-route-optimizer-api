# Fizik sabitleri
GRAVITY_MS2 = 9.81  # Yerçekimi ivmesi (m/s^2)
JOULES_PER_KWH = 3_600_000  # 1 kWh'teki Joule miktarı

# Verimlilik varsayımları (V1 için)
MOTOR_EFFICIENCY = 0.90  # Yokuş çıkarken motor verimliliği (%90)
REGENERATION_EFFICIENCY = 0.70  # Yokuş inerken rejenerasyon verimliliği (%70)


def calculate_elevation_energy_kwh(total_weight_kg: int, elevation_gain_meters: float) -> float:
    """
    Verilen ağırlık ve rakım farkı için harcanan (pozitif) veya geri kazanılan (negatif)
    enerjiyi kWh olarak hesaplar.
    Formül: Enerji (J) = m * g * h
    """
    if elevation_gain_meters == 0:
        return 0.0

    # 1. Potansiyel enerjiyi Joule olarak hesapla (m * g * h)
    potential_energy_joules = total_weight_kg * GRAVITY_MS2 * elevation_gain_meters

    # 2. Joule'u kWh'e çevir
    potential_energy_kwh = potential_energy_joules / JOULES_PER_KWH

    # 3. Verimliliği uygula
    if potential_energy_kwh > 0:
        # Yokuş çıkılıyor (enerji harcanıyor)
        return potential_energy_kwh / MOTOR_EFFICIENCY
    else:
        # Yokuş iniliyor (enerji kazanılıyor)
        return potential_energy_kwh * REGENERATION_EFFICIENCY
