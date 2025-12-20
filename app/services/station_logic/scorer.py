"""
Station Scorer - İstasyon Skorlama Motoru
==========================================

station_finder.py'den çıkarılmış skorlama mantığı.
Ağırlıklı skorlama, rating hesaplama ve popülerlik bonusu.

Kullanım:
    from app.services.station_logic import StationScorer
    
    scorer = StationScorer()
    score = scorer.calculate_score(deviation_minutes=5, power_kw=150, rating=4.5, ...)
"""

from typing import Optional
from app.utils.logger import get_logger

logger = get_logger("StationScorer")


# =============================================================================
# SCORING CONSTANTS
# =============================================================================

# Skorlama ağırlıkları (V2.9)
WEIGHT_DEVIATION = 0.30
WEIGHT_POWER = 0.20
WEIGHT_RATING = 0.25
WEIGHT_AMENITIES = 0.15
WEIGHT_POPULARITY = 0.10

# Greedy selection ağırlıkları
GREEDY_WEIGHT_POWER = 0.30
GREEDY_WEIGHT_DEVIATION = 0.25
GREEDY_WEIGHT_RATING = 0.25
GREEDY_WEIGHT_AMENITIES = 0.10
GREEDY_WEIGHT_POPULARITY = 0.10

# Rating sabitleri
RATING_CONFIDENCE_THRESHOLD = 100
RATING_PRIOR = 3.0

# Popülerlik eşikleri
POPULARITY_HIGH_THRESHOLD = 200
POPULARITY_VERY_HIGH_THRESHOLD = 500

# Amenity bonus değerleri (toplam max 1.0)
AMENITY_BONUS_TOILET = 0.25
AMENITY_BONUS_FOOD = 0.20
AMENITY_BONUS_SHOPPING = 0.15
AMENITY_BONUS_PARKING = 0.15
AMENITY_BONUS_OPEN_NOW = 0.25

# Diğer
MAX_DEVIATION_MINUTES = 15.0


# =============================================================================
# STATION SCORER CLASS
# =============================================================================

class StationScorer:
    """
    İstasyon skorlama motoru.
    
    V2.9 Güncellemeleri:
    - Rating ağırlığı artırıldı (%15 → %25)
    - Popülerlik skoru eklendi (yüksek yorum sayısı = bonus)
    - Weighted rating (az yorumlu istasyonlar dezavantajlı)
    """
    
    def __init__(
        self,
        weight_deviation: float = WEIGHT_DEVIATION,
        weight_power: float = WEIGHT_POWER,
        weight_rating: float = WEIGHT_RATING,
        weight_amenities: float = WEIGHT_AMENITIES,
        weight_popularity: float = WEIGHT_POPULARITY
    ):
        self.weight_deviation = weight_deviation
        self.weight_power = weight_power
        self.weight_rating = weight_rating
        self.weight_amenities = weight_amenities
        self.weight_popularity = weight_popularity
    
    def calculate_score(
        self,
        deviation_minutes: float,
        power_kw: float,
        max_power_kw: float,
        rating: float,
        user_ratings_total: int = 0,
        has_toilet: bool = False,
        has_food: bool = False,
        has_shopping: bool = False,
        has_parking: bool = False,
        is_open_now: Optional[bool] = None
    ) -> float:
        """
        İstasyon için ağırlıklı skor hesapla.
        
        Args:
            deviation_minutes: Rota sapması (dakika)
            power_kw: İstasyon gücü (kW)
            max_power_kw: Batch içindeki max güç
            rating: Google/OCM rating (0-5)
            user_ratings_total: Toplam yorum sayısı
            has_toilet, has_food, etc.: Amenity bilgileri
        
        Returns:
            0.0 - 1.0 arası ağırlıklı skor
        """
        deviation_score = self._calculate_deviation_score(deviation_minutes)
        power_score = self._calculate_power_score(power_kw, max_power_kw)
        rating_score = self._calculate_rating_score(rating, user_ratings_total)
        amenities_score = self.calculate_amenities_score(
            has_toilet, has_food, has_shopping, has_parking, is_open_now
        )
        popularity_score = self.calculate_popularity_score(user_ratings_total)
        
        return (
            self.weight_deviation * deviation_score +
            self.weight_power * power_score +
            self.weight_rating * rating_score +
            self.weight_amenities * amenities_score +
            self.weight_popularity * popularity_score
        )
    
    def calculate_greedy_score(
        self,
        power_kw: float,
        max_power_kw: float,
        deviation_km: float,
        max_deviation_km: float,
        rating: float,
        user_ratings_total: int = 0,
        has_toilet: bool = False,
        has_food: bool = False,
        has_shopping: bool = False,
        has_parking: bool = False,
        is_open_now: Optional[bool] = None
    ) -> float:
        """
        Greedy selection için skor hesapla.
        """
        power_score = power_kw / max_power_kw if max_power_kw > 0 else 0
        deviation_score = 1 - (deviation_km / max_deviation_km) if max_deviation_km > 0 else 1
        rating_score = self._calculate_rating_score(rating, user_ratings_total)
        amenities_score = self.calculate_amenities_score(
            has_toilet, has_food, has_shopping, has_parking, is_open_now
        )
        popularity_score = self.calculate_popularity_score(user_ratings_total)
        
        return (
            GREEDY_WEIGHT_POWER * power_score +
            GREEDY_WEIGHT_DEVIATION * deviation_score +
            GREEDY_WEIGHT_RATING * rating_score +
            GREEDY_WEIGHT_AMENITIES * amenities_score +
            GREEDY_WEIGHT_POPULARITY * popularity_score
        )
    
    def _calculate_deviation_score(self, deviation_minutes: float) -> float:
        """Sapma skoru: 0 dakika = 1.0, 15+ dakika = 0.0"""
        return max(0, 1 - (deviation_minutes / MAX_DEVIATION_MINUTES))
    
    def _calculate_power_score(self, power_kw: float, max_power_kw: float) -> float:
        """Güç skoru: max güce oranla normalize"""
        return power_kw / max_power_kw if max_power_kw > 0 else 0
    
    def _calculate_rating_score(self, rating: float, user_ratings_total: int) -> float:
        """Rating skoru: Weighted rating ile 0-1 normalize"""
        weighted_rating = self.calculate_weighted_rating(rating, user_ratings_total)
        return weighted_rating / 5.0
    
    def calculate_weighted_rating(self, rating: float, user_ratings_total: int) -> float:
        """
        Weighted rating hesapla.
        
        Az yorumlu istasyonlarda rating güvenilirliği düşük olduğundan,
        yorum sayısına göre rating'i bir prior ile karıştırır.
        
        Formül: weighted = confidence * rating + (1 - confidence) * prior
        """
        if user_ratings_total <= 0:
            return RATING_PRIOR
        
        confidence = min(1.0, user_ratings_total / RATING_CONFIDENCE_THRESHOLD)
        weighted = confidence * rating + (1 - confidence) * RATING_PRIOR
        
        return weighted
    
    def calculate_amenities_score(
        self,
        has_toilet: bool = False,
        has_food: bool = False,
        has_shopping: bool = False,
        has_parking: bool = False,
        is_open_now: Optional[bool] = None
    ) -> float:
        """
        Amenities (tesis olanakları) skoru hesapla.
        
        Returns:
            0.0 - 1.0 arası amenities skoru
        """
        score = 0.0
        
        if has_toilet:
            score += AMENITY_BONUS_TOILET
        if has_food:
            score += AMENITY_BONUS_FOOD
        if has_shopping:
            score += AMENITY_BONUS_SHOPPING
        if has_parking:
            score += AMENITY_BONUS_PARKING
        if is_open_now is True:
            score += AMENITY_BONUS_OPEN_NOW
        
        return min(1.0, score)
    
    def calculate_popularity_score(self, user_ratings_total: int) -> float:
        """
        Popülerlik skoru hesapla.
        
        Returns:
            0.0 - 1.0 arası popülerlik skoru
        """
        if user_ratings_total >= POPULARITY_VERY_HIGH_THRESHOLD:
            return 1.0
        elif user_ratings_total >= POPULARITY_HIGH_THRESHOLD:
            return 0.7
        elif user_ratings_total >= 50:
            return 0.4
        elif user_ratings_total >= 10:
            return 0.2
        else:
            return 0.0


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

station_scorer = StationScorer()
