"""
Station Catalog — Sabitler
============================

Sprint 5 sonrası provider-neutral station normalization katmanı için sabitler.
"""

# Konservatif ic planlama gucu:
# Unknown AC 22 kW, unknown DC/HPC 90 kW, connector tipi de bilinmiyorsa 50 kW.
# Response'ta power_known=False kalir; tahmin gercek guc gibi gosterilmez.
UNKNOWN_AC_POWER_PLANNING_KW: float = 22.0
UNKNOWN_DC_POWER_PLANNING_KW: float = 90.0
UNKNOWN_POWER_PLANNING_KW: float = 50.0

# DC eşiği — bu altındaki güç AC kabul edilir.
# Sprint 5: Sadece bilinen güç DC threshold ile karşılaştırılır.
# Bilinmeyen güç (power_known=False) AC kabul edilmez; UNKNOWN_POWER_PLANNING_KW ile devam.
DC_POWER_THRESHOLD_KW: float = 40.0

# Skorlama cezaları — düşük güven istasyonların seçim olasılığını azaltır
# ama tamamen elemez (unavailable hariç).
DATA_CONFIDENCE_PENALTY_UNKNOWN_POWER: float = 0.20  # %20 skor cezası
DATA_CONFIDENCE_PENALTY_UNKNOWN_AVAILABILITY: float = 0.10  # %10 skor cezası

# Provider id namespace prefix'leri — debug/log için.
PROVIDER_GOOGLE: str = "google"
PROVIDER_OCM: str = "ocm"
