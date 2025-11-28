class LoadEffectCalculator:
    """
    Yük (Load) Katmanı:
    Araç + yolcu + bagaj ağırlığının tüketimi nasıl etkilediğini hesaplar.
    Burada sadece düz yol ve tırmanış için tüketim artırımı modellenir.
    Yokuş iniş (regen) etkisi elevation_layer tarafından hesaplanır.
    """

    # %10 ekstra yük ≈ %1.5 tüketim artışı (gerçek EV telemetri verilerine yakın)
    MASS_COEFFICIENT = 0.15  

    AVG_PASSENGER_WEIGHT = 75  # Ortalama yetişkin ağırlığı (kg)
    AVG_CHILD_WEIGHT = 30     # Ortalama çocuk ağırlığı (kg)

    @staticmethod
    def calculate_mass_factor(
        base_vehicle_weight_kg: float,
        passenger_count: int,
        extra_load_kg: float,
        child_count: int = 0
    ) -> float:
        """
        Toplam ekstra yükün tüketimi ne kadar artıracağını hesaplar.
        Yokuş aşağı geri kazanım (regen) bu katmanda hesaplanmaz!
        
        Args:
            base_vehicle_weight_kg: Araç boş ağırlığı
            passenger_count: Yetişkin yolcu sayısı (75 kg/kişi)
            extra_load_kg: Ekstra yük (bagaj vb.)
            child_count: Çocuk yolcu sayısı (30 kg/çocuk)
        """

        adults_weight = passenger_count * LoadEffectCalculator.AVG_PASSENGER_WEIGHT
        children_weight = child_count * LoadEffectCalculator.AVG_CHILD_WEIGHT
        total_extra_mass = adults_weight + children_weight + extra_load_kg

        if base_vehicle_weight_kg <= 0:
            return 1.0

        mass_ratio = total_extra_mass / base_vehicle_weight_kg

        mass_factor = 1 + (mass_ratio * LoadEffectCalculator.MASS_COEFFICIENT)

        return max(1.0, mass_factor)
