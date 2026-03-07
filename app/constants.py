"""
EV Route Optimizer - Merkezi Sabitler
======================================

Tüm modüller tarafından paylaşılan sabitler.
DRY prensibi: Tek kaynak, tek değişiklik.

Kullanım:
    from app.constants import HARD_MIN_SOC, TARGET_ARRIVAL_SOC
"""

# =============================================================================
# SOC / BATARYA GÜVENLİK LİMİTLERİ
# =============================================================================

# HARD_MIN: Mutlak minimum - bunun altına düşmemeli (güvenlik)
HARD_MIN_SOC = 8.0  # %8 - kritik minimum, bunun altı tehlikeli

# TARGET: Tercih edilen hedefler - mümkünse ulaşılmalı
TARGET_ARRIVAL_SOC = 15.0  # Varışta tercih edilen
TARGET_CHARGE_MIN_SOC = 12.0  # İstasyona varışta tercih edilen
TARGET_MIN_SOC = 15.0  # Tercih edilen minimum (alias)

# Güvenlik marjı (Dinamikleşti - safety_heuristics.py kullanılıyor)
# Artık statik SAFETY_BUFFER_PERCENT veya HOTSPOT_SOC_BUFFER kullanılmıyor.

# Şarj limitleri
MAX_CHARGE_LIMIT = 100.0  # Maksimum şarj seviyesi
MIN_CHARGE_THRESHOLD_PERCENT = 10.0  # Acil şarj eşiği - bunun altında hotspot oluştur

# Eski sabitler (geriye uyumluluk)
MIN_SOC_RANGE = (HARD_MIN_SOC, 20.0)  # Şarj eşiği aralığı
TARGET_SOC_RANGE = (75.0, 95.0)  # Şarj hedefi aralığı
ARRIVAL_SOC_RANGE = (HARD_MIN_SOC, 20.0)  # Varış hedefi aralığı


# =============================================================================
# VARSAYILAN DEĞERLER
# =============================================================================

DEFAULT_PASSENGER_COUNT = 1
DEFAULT_CHILD_COUNT = 0
DEFAULT_EXTRA_LOAD_KG = 0.0
DEFAULT_TEMPERATURE_C = 20.0


# =============================================================================
# ŞARJ DURAĞI OPTİMİZASYONU
# =============================================================================

MIN_DISTANCE_BETWEEN_STOPS_KM = 50.0  # Şarj durakları arası minimum mesafe

# Optimizer sabitleri
STOP_PENALTY_MINUTES = 60.0  # Her ek durak = 60 dk ceza (park, bul, bekle, çık)
SHORT_INTERVAL_PENALTY_MINUTES = 15.0  # Kısa aralıklı durak penaltisi (dk)
MIN_DRIVING_INTERVAL_MINUTES = 75.0  # 1.25 saatten kısa sürüş aralıkları penalize edilir


# =============================================================================
# DİNAMİK TARGET SOC
# =============================================================================

TARGET_SOC_MIN = 65  # Minimum hedef SOC
TARGET_SOC_MAX = 100  # Maksimum hedef SOC (V2.6: %100'e kadar şarj seçeneği)
TARGET_SOC_STEP = 5  # %5 aralıklarla dene (hız için)


# =============================================================================
# HOTSPOT ÜRETİM SINIRLARI
# =============================================================================

MAX_MIN_REQUIRED_SOC = 50.0  # min_required_soc üst sınırı
# Sonuç: Yukarıdaki MAX_MIN_REQUIRED_SOC sınırı korunuyor, buffer dinamik


# =============================================================================
# UZUN ROTA İÇİN DİNAMİK HEDEFLER
# =============================================================================

# Birden fazla şarj durağı olan rotalarda daha düşük hedefler
MULTI_STOP_TARGET_MIN = 72  # Ara duraklar için minimum hedef
MULTI_STOP_TARGET_MAX = 82  # Ara duraklar için maksimum hedef
FINAL_STOP_TARGET_MIN = 60  # Son durak için minimum hedef
FINAL_STOP_TARGET_MAX = 85  # Son durak için maksimum hedef

# Varış SOC hedefi (uzun rotalarda düşük tutulmalı)
DEFAULT_ARRIVAL_SOC = 20.0  # Varsayılan varış hedefi %20
LONG_ROUTE_ARRIVAL_SOC = 15.0  # Uzun rotalarda %15 yeterli


# =============================================================================
# SAFE HARBOR (GÜVENLİ LİMAN) PARAMETRELERİ
# =============================================================================

# Varışta "yakın istasyon var mı?" kontrolü için başlangıç yarıçapı
SAFE_HARBOR_NEARBY_RADIUS_KM = 10.0  # 10 km = kısa sürüş mesafesi

# Kademeli arama yarıçapları — en yakın kurtarıcı istasyonu bulmak için
SAFE_HARBOR_SEARCH_RADII_KM = [10, 25, 50, 80, 120]  # km

# Dönüş tüketimine ek güvenlik payı
SAFE_HARBOR_BUFFER_PERCENT = 5.0  # %5 güvenlik

# Maksimum dönüş mesafesi (aşılırsa uyarı)
SAFE_HARBOR_MAX_RETURN_KM = 120.0

# Kaç kurtarıcı istasyon hedeflenmeli (tek istasyon bağımlılığı olmasın)
SAFE_HARBOR_MIN_RESCUE_STATIONS = 3  # En az 2-3 istasyona yetecek batarya
