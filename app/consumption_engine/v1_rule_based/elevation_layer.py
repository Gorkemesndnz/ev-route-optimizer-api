class ElevationEffectCalculator:
    """
    Eğim (Elevation) Katmanı:
    - Yokuş yukarı çıkarken potansiyel enerji artışı tüketimi artırır
    - Yokuş aşağı inerken regen ile enerji geri kazanılır
    """

    GRAVITY = 9.81          # m/s^2
    REGEN_EFFICIENCY = 0.65 # Yokuş iniş regen verimliliği
    JOULE_TO_KWH = 3_600_000.0

    @staticmethod
    def calculate_elevation_energy(
        mass_kg: float,
        elevation_gain_m: float,
        elevation_loss_m: float
    ) -> float:
        """
        Eğim kaynaklı NET enerji farkını (kWh) döner.
        Pozitif -> tüketim artışı
        Negatif -> regen ile geri kazanım
        """
        # Yukarı tırmanış
        uphill_joule = mass_kg * ElevationEffectCalculator.GRAVITY * elevation_gain_m
        uphill_kwh = uphill_joule / ElevationEffectCalculator.JOULE_TO_KWH

        # İniş - regen
        downhill_joule = mass_kg * ElevationEffectCalculator.GRAVITY * elevation_loss_m
        regen_kwh = (downhill_joule / ElevationEffectCalculator.JOULE_TO_KWH) \
                    * ElevationEffectCalculator.REGEN_EFFICIENCY

        return uphill_kwh - regen_kwh

    @staticmethod
    def elevation_factor(
        mass_kg: float,
        gain_m: float,
        loss_m: float,
        base_consumption_kwh_per_km: float,
        segment_distance_km: float
    ) -> float:
        """
        Segment bazlı tüketim katsayısı.
        İnişte regen etkisini abartmamak için clamp uygulanır.
        """
        if segment_distance_km <= 0:
            return 1.0

        elevation_kwh = ElevationEffectCalculator.calculate_elevation_energy(
            mass_kg, gain_m, loss_m
        )

        base_segment_kwh = base_consumption_kwh_per_km * segment_distance_km
        safe_base = max(base_segment_kwh, 0.001)

        factor = (base_segment_kwh + elevation_kwh) / safe_base

        # Mantıklı sınırlar: 0.5 (iniş), 3.0 (çok dik tırmanış)
        return max(0.5, min(3.0, factor))
