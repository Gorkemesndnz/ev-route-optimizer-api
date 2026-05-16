"""
Objective Function — J(plan) Pareto Skor Hesabı
================================================

J(plan) = w_t·T_norm + w_c·C_norm + w_e·E_norm + w_s·S_norm

T (Time):    toplam süre (sürüş + şarj + bekleme), dakika
C (Cost):    toplam yakıt+şarj maliyeti, TL
E (Energy):  batarya degradation proxy (>%80 SOC süresi), dakika
S (Safety):  varış marjı cezası (düşük marj → yüksek S)

Hard constraint: min_arrival_soc_margin < 0 → J = +inf (yolda kalır, plan reddedilir)
"""

from dataclasses import dataclass
from typing import Optional

from app.optimization.modes import ParetoWeights


# =============================================================================
# NORMALIZATION BOUNDS
# =============================================================================
# 🔧 F-24: Sabit sınırlar 200-500 km rotaları için kalibre edilmişti.
# 1200+ km uzun rotalarda T ve C değerleri WORST değerine saturasyon ulaşıyordu
# (T_n=1.0, C_n=1.0) → solver aralarında fark göremez, sadece E ve S'e bakardı.
# Çözüm: T_WORST ve C_WORST mesafeye göre ölçeklendirilir; sabitler "baseline" olur.

T_IDEAL_MIN = 60.0      # 1 saat = ideal (kısa rota single-stop)
T_WORST_BASE = 600.0    # 10 saat = 500 km baseline worst

C_IDEAL_TL = 50.0
C_WORST_BASE = 800.0    # 500 km baseline worst

E_IDEAL_MIN = 0.0       # %80 üstünde hiç süre geçirme
E_WORST_MIN = 120.0     # 2 saat %80+ şarj = degradation kötü

S_IDEAL = 0.0           # Marj büyük (rahat)
S_WORST = 10.0          # Marj 0'a yakın (riskli)

# Ölçekleme referans mesafesi: bu mesafe için sabit WORST değerleri geçerli
_T_C_REFERENCE_KM = 500.0


# =============================================================================
# DATA CLASS
# =============================================================================

@dataclass
class PlanMetrics:
    """Bir planın ham metrikleri."""
    total_drive_time_min: float
    total_charge_time_min: float
    total_wait_time_min: float = 0.0     # istasyon kuyruğu (şimdilik 0)
    total_cost_tl: float = 0.0           # kwh × tarife (+ opsiyonel yol ücreti)
    high_soc_minutes: float = 0.0        # %80 üstü şarj süresi
    min_arrival_soc_margin: float = 5.0  # arrival_soc - dynamic_buffer
    num_stops: int = 0


@dataclass
class ObjectiveBreakdown:
    """Skor breakdown — debug ve UI için."""
    J: float
    T_value: float
    C_value: float
    E_value: float
    S_value: float
    T_normalized: float
    C_normalized: float
    E_normalized: float
    S_normalized: float
    is_hard_violation: bool


# =============================================================================
# CORE
# =============================================================================

def normalize(value: float, ideal: float, worst: float) -> float:
    """[0,1] aralığına çek. value <= ideal → 0; value >= worst → 1."""
    if worst <= ideal:
        return 0.0
    return max(0.0, min(1.0, (value - ideal) / (worst - ideal)))


def calculate_objective(
    metrics: PlanMetrics,
    weights: ParetoWeights,
    total_distance_km: float = _T_C_REFERENCE_KM,
) -> ObjectiveBreakdown:
    """
    Ana skor fonksiyonu.

    🔧 F-24: `total_distance_km` parametresi eklendi.
    T_WORST ve C_WORST mesafeye orantılı ölçeklenir; böylece 1200+ km uzun
    rotalarda T_n/C_n saturasyona ulaşmaz ve solver combo farklarını görebilir.

    Returns:
        ObjectiveBreakdown(J, ham değerler, normalize değerler, hard violation)
    """
    # Hard constraint: güvenli değilse direkt reddet
    if metrics.min_arrival_soc_margin < 0:
        return ObjectiveBreakdown(
            J=float("inf"),
            T_value=0.0, C_value=0.0, E_value=0.0, S_value=0.0,
            T_normalized=0.0, C_normalized=0.0, E_normalized=0.0, S_normalized=1.0,
            is_hard_violation=True,
        )

    # 🔧 F-24: Mesafeye göre ölçekleme (min 1.0 — kısa rotalarda sıkışmasın)
    scale = max(1.0, total_distance_km / _T_C_REFERENCE_KM)
    t_worst = T_WORST_BASE * scale
    c_worst = C_WORST_BASE * scale

    T = metrics.total_drive_time_min + metrics.total_charge_time_min + metrics.total_wait_time_min
    C = metrics.total_cost_tl
    E = metrics.high_soc_minutes
    # Safety: 0'a yakın marj → yüksek değer
    S = max(0.0, S_WORST - metrics.min_arrival_soc_margin)

    T_n = normalize(T, T_IDEAL_MIN, t_worst)
    C_n = normalize(C, C_IDEAL_TL, c_worst)
    E_n = normalize(E, E_IDEAL_MIN, E_WORST_MIN)
    S_n = normalize(S, S_IDEAL, S_WORST)

    J = (weights.w_time * T_n
         + weights.w_cost * C_n
         + weights.w_battery * E_n
         + weights.w_safety * S_n)

    return ObjectiveBreakdown(
        J=J,
        T_value=T, C_value=C, E_value=E, S_value=S,
        T_normalized=T_n, C_normalized=C_n, E_normalized=E_n, S_normalized=S_n,
        is_hard_violation=False,
    )
