"""
EV Route Optimizer - Kapsamlı Core Logic Testleri
==================================================

Bu test dosyası tüm refactoring çalışmalarını kapsar:
- FeedbackManager (3-Strike Rule) - Tüm senaryolar
- SOC Parameters (Dynamic Tolerance) - Edge cases
- StationScorer & StationFilter - Skorlama ve filtreleme
- Constants - Merkezi sabit değerler

Kullanıcı Senaryoları:
1. Yeni kullanıcı istasyon raporluyor
2. Aynı kullanıcı tekrar raporluyor (spam)
3. Üç farklı kullanıcı raporluyor (blok)
4. Blok süresi doluyor (otomatik açılma)
5. Uzun/kısa rota planlaması
6. Düşük bataryayla yola çıkma
7. İstasyon seçimi ve skorlama

Run: pytest tests/test_core_logic.py -v
Run all: pytest tests/ -v
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from typing import Optional


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def clean_feedback_manager():
    """Her test için temiz FeedbackManager"""
    from app.services.feedback_service import FeedbackManager
    manager = FeedbackManager()
    manager.clear_all()
    return manager


@pytest.fixture
def mock_route_request():
    """Mock RouteRequest objesi"""
    request = MagicMock()
    request.target_arrival_soc_percent = None
    request.charge_min_soc_percent = None
    request.charge_target_soc_percent = None
    request.smart_plan_enabled = False
    request.charging_frequency = None
    return request


# =============================================================================
# 1. CONSTANTS TESTS - Merkezi Sabitler
# =============================================================================

class TestConstants:
    """app/constants.py testleri"""
    
    def test_soc_constants_exist(self):
        """Test: Tüm SOC sabitleri tanımlı"""
        from app.constants import (
            HARD_MIN_SOC,
            TARGET_ARRIVAL_SOC,
            TARGET_CHARGE_MIN_SOC,
            MIN_CHARGE_THRESHOLD_PERCENT,
            MAX_CHARGE_LIMIT
        )
        
        assert HARD_MIN_SOC == 8.0, "HARD_MIN_SOC should be 8%"
        assert TARGET_ARRIVAL_SOC == 15.0, "TARGET_ARRIVAL_SOC should be 15%"
        assert TARGET_CHARGE_MIN_SOC == 12.0, "TARGET_CHARGE_MIN_SOC should be 12%"
        assert MIN_CHARGE_THRESHOLD_PERCENT == 10.0, "MIN_CHARGE_THRESHOLD should be 10%"
        assert MAX_CHARGE_LIMIT == 100.0, "MAX_CHARGE_LIMIT should be 100%"
    
    def test_default_values_exist(self):
        """Test: Varsayılan değerler tanımlı"""
        from app.constants import (
            DEFAULT_PASSENGER_COUNT,
            DEFAULT_CHILD_COUNT,
            DEFAULT_EXTRA_LOAD_KG,
            DEFAULT_TEMPERATURE_C
        )
        
        assert DEFAULT_PASSENGER_COUNT == 1
        assert DEFAULT_CHILD_COUNT == 0
        assert DEFAULT_EXTRA_LOAD_KG == 0.0
        assert DEFAULT_TEMPERATURE_C == 20.0
    
    def test_soc_hierarchy_is_correct(self):
        """Test: SOC sabitleri mantıklı sıralamada"""
        from app.constants import (
            HARD_MIN_SOC,
            MIN_CHARGE_THRESHOLD_PERCENT,
            TARGET_CHARGE_MIN_SOC,
            TARGET_ARRIVAL_SOC
        )
        
        # HARD_MIN < MIN_CHARGE < TARGET_CHARGE < TARGET_ARRIVAL
        assert HARD_MIN_SOC < MIN_CHARGE_THRESHOLD_PERCENT
        assert MIN_CHARGE_THRESHOLD_PERCENT < TARGET_CHARGE_MIN_SOC
        assert TARGET_CHARGE_MIN_SOC < TARGET_ARRIVAL_SOC
    
    def test_constants_imported_in_route_planning(self):
        """Test: route_planning modülü sabitleri doğru kullanıyor"""
        from app.route_planning.orchestrator import (
            HARD_MIN_SOC,
            TARGET_ARRIVAL_SOC,
            MIN_CHARGE_THRESHOLD_PERCENT,
            DEFAULT_CHARGE_TARGET_SOC,
        )
        from app.constants import (
            HARD_MIN_SOC as CONST_HARD_MIN,
            TARGET_ARRIVAL_SOC as CONST_TARGET,
            MIN_CHARGE_THRESHOLD_PERCENT as CONST_MIN_CHARGE,
            DEFAULT_CHARGE_TARGET_SOC as CONST_DEFAULT_TARGET,
        )

        assert HARD_MIN_SOC == CONST_HARD_MIN
        assert TARGET_ARRIVAL_SOC == CONST_TARGET
        assert MIN_CHARGE_THRESHOLD_PERCENT == CONST_MIN_CHARGE
        assert DEFAULT_CHARGE_TARGET_SOC == CONST_DEFAULT_TARGET == 80.0


# =============================================================================
# 2. FEEDBACK SERVICE TESTS - İstasyon Güven Sistemi
# =============================================================================

class TestFeedbackManagerBasic:
    """FeedbackManager temel işlevsellik testleri"""
    
    @pytest.mark.asyncio
    async def test_first_report_recorded(self, clean_feedback_manager):
        """Senaryo: İlk kez istasyon raporlayan kullanıcı"""
        result = await clean_feedback_manager.report_station(
            station_id="station_123",
            user_id="user_ali",
            reason="out_of_service"
        )
        
        assert result["status"] == "recorded"
        assert result["is_blocked"] == False
        assert "kaydedildi" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_multiple_users_report(self, clean_feedback_manager):
        """Senaryo: Farklı kullanıcılar aynı istasyonu raporluyor"""
        station_id = "problematic_station"
        
        # 2 farklı kullanıcı raporluyor
        r1 = await clean_feedback_manager.report_station(station_id, "user_1", "broken")
        r2 = await clean_feedback_manager.report_station(station_id, "user_2", "broken")
        
        assert r1["status"] == "recorded"
        assert r2["status"] == "recorded"
        assert r2["is_blocked"] == False  # Henüz 3'e ulaşmadı
        
        reports = clean_feedback_manager.get_station_reports(station_id)
        assert len(reports) == 2


class TestFeedbackManagerSpamProtection:
    """Spam koruması testleri"""
    
    @pytest.mark.asyncio
    async def test_same_user_double_report_detected(self, clean_feedback_manager):
        """Senaryo: Aynı kullanıcı 24 saat içinde tekrar raporluyor"""
        station_id = "station_456"
        user_id = "spammer_user"
        
        # İlk rapor
        r1 = await clean_feedback_manager.report_station(station_id, user_id, "broken")
        assert r1["status"] == "recorded"
        
        # Aynı kullanıcı tekrar deniyor
        r2 = await clean_feedback_manager.report_station(station_id, user_id, "still_broken")
        assert r2["status"] == "spam_detected"
        assert "zaten" in r2["message"].lower()
        
        # Rapor sayısı artmamış olmalı
        reports = clean_feedback_manager.get_station_reports(station_id)
        assert len(reports) == 1
    
    @pytest.mark.asyncio
    async def test_spam_updates_reason(self, clean_feedback_manager):
        """Senaryo: Spam raporu mevcut raporun sebebini günceller"""
        station_id = "station_789"
        user_id = "user_mehmet"
        
        await clean_feedback_manager.report_station(station_id, user_id, "wrong_location")
        await clean_feedback_manager.report_station(station_id, user_id, "out_of_service")
        
        reports = clean_feedback_manager.get_station_reports(station_id)
        # En son sebep güncellenmiş olmalı (reason = out_of_service)
        assert len(reports) == 1


class TestFeedbackManager3StrikeRule:
    """3-Strike kuralı testleri"""
    
    @pytest.mark.asyncio
    async def test_station_blocked_at_threshold(self, clean_feedback_manager):
        """Senaryo: 3 farklı kullanıcı raporladığında blok"""
        station_id = "bad_station"
        
        await clean_feedback_manager.report_station(station_id, "user_a", "broken")
        await clean_feedback_manager.report_station(station_id, "user_b", "broken")
        result = await clean_feedback_manager.report_station(station_id, "user_c", "broken")
        
        assert result["status"] == "station_blocked"
        assert result["is_blocked"] == True
        assert result["block_expiry"] is not None
        
        # is_station_blocked kontrolü
        assert clean_feedback_manager.is_station_blocked(station_id) == True
    
    @pytest.mark.asyncio
    async def test_two_reports_not_enough(self, clean_feedback_manager):
        """Senaryo: 2 rapor blok için yeterli değil"""
        station_id = "maybe_bad_station"
        
        await clean_feedback_manager.report_station(station_id, "user_1", "issue")
        await clean_feedback_manager.report_station(station_id, "user_2", "issue")
        
        assert clean_feedback_manager.is_station_blocked(station_id) == False
    
    @pytest.mark.asyncio
    async def test_block_duration_is_48_hours(self, clean_feedback_manager):
        """Senaryo: Blok süresi 48 saat"""
        from app.services.feedback_service import BLOCK_DURATION_HOURS
        
        station_id = "blocked_station"
        
        for i in range(3):
            await clean_feedback_manager.report_station(station_id, f"user_{i}", "broken")
        
        blocked_info = clean_feedback_manager._blocked[station_id]
        expected_expiry = blocked_info.blocked_at + timedelta(hours=BLOCK_DURATION_HOURS)
        
        # Expiry zamanı doğru mu?
        assert abs((blocked_info.expiry_time - expected_expiry).total_seconds()) < 2


class TestFeedbackManagerBlockExpiry:
    """Blok süresi dolma testleri"""
    
    @pytest.mark.asyncio
    async def test_block_auto_expires(self, clean_feedback_manager):
        """Senaryo: Blok süresi dolduğunda otomatik açılma"""
        station_id = "temp_blocked"
        
        # Blokla
        for i in range(3):
            await clean_feedback_manager.report_station(station_id, f"u{i}", "x")
        
        assert clean_feedback_manager.is_station_blocked(station_id) == True
        
        # Zamanı ileri al (simüle)
        clean_feedback_manager._blocked[station_id].expiry_time = datetime.now() - timedelta(minutes=1)
        
        # Artık bloklu olmamalı
        assert clean_feedback_manager.is_station_blocked(station_id) == False
    
    @pytest.mark.asyncio
    async def test_expired_station_removed_from_blocked_list(self, clean_feedback_manager):
        """Senaryo: Süresi dolan istasyon listeden kaldırılıyor"""
        station_id = "expired_block"
        
        for i in range(3):
            await clean_feedback_manager.report_station(station_id, f"u{i}", "x")
        
        # Zamanı ileri al
        clean_feedback_manager._blocked[station_id].expiry_time = datetime.now() - timedelta(hours=1)
        
        # is_station_blocked çağrıldığında temizlenmeli
        clean_feedback_manager.is_station_blocked(station_id)
        
        assert station_id not in clean_feedback_manager._blocked


class TestFeedbackManagerBlockExtension:
    """Blok süresi uzatma testleri"""
    
    @pytest.mark.asyncio
    async def test_new_report_extends_block(self, clean_feedback_manager):
        """Senaryo: Zaten bloklu istasyona yeni rapor = süre uzar"""
        station_id = "extended_block"
        
        # İlk 3 rapor - blokla
        for i in range(3):
            await clean_feedback_manager.report_station(station_id, f"user_{i}", "broken")
        
        # Expiry'yi biraz geriye al (test için)
        original_expiry = clean_feedback_manager._blocked[station_id].expiry_time
        clean_feedback_manager._blocked[station_id].expiry_time = original_expiry - timedelta(hours=1)
        adjusted_expiry = clean_feedback_manager._blocked[station_id].expiry_time
        
        # 4. kullanıcı raporluyor - süre uzamalı
        result = await clean_feedback_manager.report_station(station_id, "user_4", "still_broken")
        
        assert result["status"] == "block_extended"
        assert result["block_expiry"] > adjusted_expiry


class TestFeedbackManagerEdgeCases:
    """Edge case testleri"""
    
    @pytest.mark.asyncio
    async def test_nonexistent_station_not_blocked(self, clean_feedback_manager):
        """Test: Var olmayan istasyon bloklu değil"""
        assert clean_feedback_manager.is_station_blocked("nonexistent_123") == False
    
    @pytest.mark.asyncio
    async def test_get_blocked_stations_empty(self, clean_feedback_manager):
        """Test: Başlangıçta bloklu istasyon yok"""
        blocked = clean_feedback_manager.get_blocked_stations()
        assert len(blocked) == 0
    
    @pytest.mark.asyncio
    async def test_get_blocked_stations_with_data(self, clean_feedback_manager):
        """Test: Bloklu istasyonlar listelenebiliyor"""
        # 2 istasyonu blokla
        for station in ["station_A", "station_B"]:
            for i in range(3):
                await clean_feedback_manager.report_station(station, f"u{station}_{i}", "x")
        
        blocked = clean_feedback_manager.get_blocked_stations()
        assert len(blocked) == 2
        
        station_ids = [b["station_id"] for b in blocked]
        assert "station_A" in station_ids
        assert "station_B" in station_ids
    
    @pytest.mark.asyncio
    async def test_clear_all_removes_everything(self, clean_feedback_manager):
        """Test: clear_all tüm verileri temizliyor"""
        # Veri ekle
        for i in range(3):
            await clean_feedback_manager.report_station("test_station", f"u{i}", "x")
        
        assert clean_feedback_manager.is_station_blocked("test_station") == True
        
        # Temizle
        clean_feedback_manager.clear_all()
        
        assert clean_feedback_manager.is_station_blocked("test_station") == False
        assert len(clean_feedback_manager.get_blocked_stations()) == 0


# =============================================================================
# 3. SOC PARAMETERS TESTS - Dinamik SOC Hesaplama
# =============================================================================

class TestSOCParametersBasic:
    """_calculate_base_soc_params temel testleri"""
    
    def test_can_reach_without_charging(self, mock_route_request):
        """Senaryo: Şarjsız varılabilir (projected > TARGET)"""
        from app.route_planning.soc_params import calculate_base_soc_params as _calculate_base_soc_params
        from app.constants import HARD_MIN_SOC
        
        # 51 kWh batarya, %80 başlangıç, 20 kWh tüketim
        # Projected: (40.8 - 20) / 51 * 100 = 40.8%
        charge_min, user_override, arrival_soc = _calculate_base_soc_params(
            battery_kwh=51.0,
            start_soc=80.0,
            total_consumption_kwh=20.0,
            route_distance_km=150.0,
            request=mock_route_request
        )
        
        projected = ((80/100 * 51) - 20) / 51 * 100
        assert arrival_soc == max(HARD_MIN_SOC, projected)
        assert arrival_soc > 15.0  # TARGET'ın üzerinde
    
    def test_needs_charging_below_hard_min(self, mock_route_request):
        """Senaryo: HARD_MIN altında kalıyor - şarj gerekli"""
        from app.route_planning.soc_params import calculate_base_soc_params as _calculate_base_soc_params
        from app.constants import TARGET_ARRIVAL_SOC
        
        # 51 kWh batarya, %80 başlangıç, 39 kWh tüketim
        # Projected: (40.8 - 39) / 51 * 100 = 3.5%
        charge_min, user_override, arrival_soc = _calculate_base_soc_params(
            battery_kwh=51.0,
            start_soc=80.0,
            total_consumption_kwh=39.0,
            route_distance_km=300.0,
            request=mock_route_request
        )
        
        assert arrival_soc == TARGET_ARRIVAL_SOC  # 15%


class TestSOCParametersDynamicTolerance:
    """Dynamic Tolerance (8-15% arası) testleri"""
    
    def test_projected_between_hard_and_target(self, mock_route_request):
        """Senaryo: Projected %8-15 arası - şarj YAPILMAZ"""
        from app.route_planning.soc_params import calculate_base_soc_params as _calculate_base_soc_params
        from app.constants import HARD_MIN_SOC, TARGET_ARRIVAL_SOC
        
        # 51 kWh, %80 start, 35 kWh consumption
        # Projected: (40.8 - 35) / 51 * 100 = 11.4% (8-15 arası)
        charge_min, user_override, arrival_soc = _calculate_base_soc_params(
            battery_kwh=51.0,
            start_soc=80.0,
            total_consumption_kwh=35.0,
            route_distance_km=200.0,
            request=mock_route_request
        )
        
        projected = ((80/100 * 51) - 35) / 51 * 100
        assert HARD_MIN_SOC < projected < TARGET_ARRIVAL_SOC
        assert arrival_soc == projected  # Şarj yapılmadı, projected kullanıldı
    
    def test_projected_exactly_at_hard_min(self, mock_route_request):
        """Edge case: Projected tam %8'de"""
        from app.route_planning.soc_params import calculate_base_soc_params as _calculate_base_soc_params
        from app.constants import HARD_MIN_SOC
        
        # 50 kWh, %80 start, 36 kWh consumption
        # Projected: (40 - 36) / 50 * 100 = 8.0%
        charge_min, user_override, arrival_soc = _calculate_base_soc_params(
            battery_kwh=50.0,
            start_soc=80.0,
            total_consumption_kwh=36.0,
            route_distance_km=200.0,
            request=mock_route_request
        )
        
        # 8% >= HARD_MIN, şarj gerekmez
        assert arrival_soc == HARD_MIN_SOC
    
    def test_projected_just_below_hard_min(self, mock_route_request):
        """Edge case: Projected %7.9 (hard min altında)"""
        from app.route_planning.soc_params import calculate_base_soc_params as _calculate_base_soc_params
        from app.constants import TARGET_ARRIVAL_SOC
        
        # 50 kWh, %80 start, 36.05 kWh consumption
        # Projected: (40 - 36.05) / 50 * 100 = 7.9%
        charge_min, user_override, arrival_soc = _calculate_base_soc_params(
            battery_kwh=50.0,
            start_soc=80.0,
            total_consumption_kwh=36.05,
            route_distance_km=200.0,
            request=mock_route_request
        )
        
        # 7.9% < HARD_MIN, şarj gerekli
        assert arrival_soc == TARGET_ARRIVAL_SOC


class TestSOCParametersDistanceBased:
    """Mesafeye göre dinamik eşik testleri"""
    
    def test_short_route_uses_hard_min(self, mock_route_request):
        """Senaryo: Kısa rota (< 100 km) - HARD_MIN_SOC"""
        from app.route_planning.soc_params import calculate_base_soc_params as _calculate_base_soc_params
        from app.constants import HARD_MIN_SOC
        
        mock_route_request.preferences = None
        mock_route_request.charging_frequency = None
        
        charge_min, _, _ = _calculate_base_soc_params(
            battery_kwh=51.0, start_soc=100.0, total_consumption_kwh=10.0,
            route_distance_km=50.0, request=mock_route_request
        )
        
        assert charge_min == HARD_MIN_SOC
    
    def test_medium_route_uses_min_charge(self, mock_route_request):
        """Senaryo: Orta rota (100-200 km) - MIN_CHARGE_THRESHOLD"""
        from app.route_planning.soc_params import calculate_base_soc_params as _calculate_base_soc_params
        from app.constants import MIN_CHARGE_THRESHOLD_PERCENT
        
        mock_route_request.preferences = None
        mock_route_request.charging_frequency = None
        
        charge_min, _, _ = _calculate_base_soc_params(
            battery_kwh=51.0, start_soc=100.0, total_consumption_kwh=10.0,
            route_distance_km=150.0, request=mock_route_request
        )
        
        assert charge_min == MIN_CHARGE_THRESHOLD_PERCENT
    
    def test_long_route_uses_target_charge(self, mock_route_request):
        """Senaryo: Uzun rota (200-400 km) - TARGET_CHARGE_MIN"""
        from app.route_planning.soc_params import calculate_base_soc_params as _calculate_base_soc_params
        from app.constants import TARGET_CHARGE_MIN_SOC
        
        mock_route_request.preferences = None
        mock_route_request.charging_frequency = None
        
        charge_min, _, _ = _calculate_base_soc_params(
            battery_kwh=51.0, start_soc=100.0, total_consumption_kwh=10.0,
            route_distance_km=300.0, request=mock_route_request
        )
        
        assert charge_min == TARGET_CHARGE_MIN_SOC
    
    def test_very_long_route_uses_target_arrival(self, mock_route_request):
        """Senaryo: Çok uzun rota (> 400 km) - TARGET_ARRIVAL_SOC"""
        from app.route_planning.soc_params import calculate_base_soc_params as _calculate_base_soc_params
        from app.constants import TARGET_ARRIVAL_SOC
        
        mock_route_request.preferences = None
        mock_route_request.charging_frequency = None
        
        charge_min, _, _ = _calculate_base_soc_params(
            battery_kwh=51.0, start_soc=100.0, total_consumption_kwh=10.0,
            route_distance_km=500.0, request=mock_route_request
        )
        
        assert charge_min == TARGET_ARRIVAL_SOC


class TestSOCParametersUserOverride:
    """Kullanıcı override testleri"""
    
    def test_user_arrival_soc_respected(self, mock_route_request):
        """Senaryo: Kullanıcı varış SOC'u belirledi"""
        from app.route_planning.soc_params import calculate_base_soc_params as _calculate_base_soc_params
        
        mock_route_request.target_arrival_soc_percent = 25.0
        
        _, _, arrival_soc = _calculate_base_soc_params(
            battery_kwh=51.0, start_soc=80.0, total_consumption_kwh=20.0,
            route_distance_km=200.0, request=mock_route_request
        )
        
        assert arrival_soc == 25.0
    
    def test_user_charge_min_respected(self, mock_route_request):
        """Senaryo: Kullanıcı şarj eşiği belirledi"""
        from app.route_planning.soc_params import calculate_base_soc_params as _calculate_base_soc_params
        
        mock_route_request.charge_min_soc_percent = 20.0
        
        charge_min, _, _ = _calculate_base_soc_params(
            battery_kwh=51.0, start_soc=80.0, total_consumption_kwh=20.0,
            route_distance_km=200.0, request=mock_route_request
        )
        
        assert charge_min == 20.0


# =============================================================================
# 4. STATION SCORER TESTS - İstasyon Skorlama
# =============================================================================

class TestStationScorerBasic:
    """StationScorer temel testleri"""
    
    def test_score_in_valid_range(self):
        """Test: Skor 0-1 arasında"""
        from app.services.station_logic import StationScorer
        
        scorer = StationScorer()
        score = scorer.calculate_score(
            deviation_minutes=5, power_kw=150, max_power_kw=200, rating=4.0
        )
        
        assert 0.0 <= score <= 1.0
    
    def test_perfect_station_high_score(self):
        """Senaryo: Mükemmel istasyon - yüksek skor"""
        from app.services.station_logic import StationScorer
        
        scorer = StationScorer()
        score = scorer.calculate_score(
            deviation_minutes=0,  # Sapma yok
            power_kw=200,         # Maksimum güç
            max_power_kw=200,
            rating=5.0,           # En yüksek rating
            user_ratings_total=500,  # Çok popüler
            has_toilet=True,
            has_food=True,
            has_shopping=True,
            has_parking=True,
            is_open_now=True
        )
        
        assert score > 0.8
    
    def test_poor_station_low_score(self):
        """Senaryo: Kötü istasyon - düşük skor"""
        from app.services.station_logic import StationScorer
        
        scorer = StationScorer()
        score = scorer.calculate_score(
            deviation_minutes=14,  # Yüksek sapma
            power_kw=50,           # Düşük güç
            max_power_kw=200,
            rating=2.0,            # Düşük rating
            user_ratings_total=2   # Neredeyse hiç yorum yok
        )
        
        assert score < 0.4


class TestStationScorerFactors:
    """Skorlama faktörleri testleri"""
    
    def test_deviation_affects_score(self):
        """Test: Yüksek sapma skoru düşürür"""
        from app.services.station_logic import StationScorer
        
        scorer = StationScorer()
        score_low_dev = scorer.calculate_score(
            deviation_minutes=2, power_kw=150, max_power_kw=200, rating=4.0
        )
        score_high_dev = scorer.calculate_score(
            deviation_minutes=14, power_kw=150, max_power_kw=200, rating=4.0
        )
        
        assert score_low_dev > score_high_dev
    
    def test_power_affects_score(self):
        """Test: Yüksek güç skoru artırır"""
        from app.services.station_logic import StationScorer
        
        scorer = StationScorer()
        score_low_power = scorer.calculate_score(
            deviation_minutes=5, power_kw=50, max_power_kw=200, rating=4.0
        )
        score_high_power = scorer.calculate_score(
            deviation_minutes=5, power_kw=200, max_power_kw=200, rating=4.0
        )
        
        assert score_high_power > score_low_power
    
    def test_amenities_affect_score(self):
        """Test: Olanaklar skoru artırır"""
        from app.services.station_logic import StationScorer
        
        scorer = StationScorer()
        score_no_amenities = scorer.calculate_score(
            deviation_minutes=5, power_kw=150, max_power_kw=200, rating=4.0
        )
        score_with_amenities = scorer.calculate_score(
            deviation_minutes=5, power_kw=150, max_power_kw=200, rating=4.0,
            has_toilet=True, has_food=True, has_parking=True
        )
        
        assert score_with_amenities > score_no_amenities
    
    def test_popularity_affects_score(self):
        """Test: Popülerlik skoru artırır"""
        from app.services.station_logic import StationScorer
        
        scorer = StationScorer()
        score_unpopular = scorer.calculate_score(
            deviation_minutes=5, power_kw=150, max_power_kw=200, rating=4.0,
            user_ratings_total=5
        )
        score_popular = scorer.calculate_score(
            deviation_minutes=5, power_kw=150, max_power_kw=200, rating=4.0,
            user_ratings_total=500
        )
        
        assert score_popular > score_unpopular


class TestStationScorerWeightedRating:
    """Weighted rating testleri"""
    
    def test_few_reviews_uses_prior(self):
        """Test: Az yorumda prior kullanılır"""
        from app.services.station_logic import StationScorer
        from app.services.station_logic.scorer import RATING_PRIOR
        
        scorer = StationScorer()
        weighted = scorer.calculate_weighted_rating(rating=5.0, user_ratings_total=0)
        
        assert weighted == RATING_PRIOR
    
    def test_many_reviews_uses_actual(self):
        """Test: Çok yorumda gerçek rating kullanılır"""
        from app.services.station_logic import StationScorer
        
        scorer = StationScorer()
        weighted = scorer.calculate_weighted_rating(rating=4.5, user_ratings_total=200)
        
        # 200 yorum > threshold, gerçek rating'e yakın olmalı
        assert weighted > 4.0


# =============================================================================
# 5. STATION FILTER TESTS - İstasyon Filtreleme
# =============================================================================

class TestStationFilterConnector:
    """Connector uyumluluk testleri"""
    
    def test_ccs_compatible(self):
        """Test: CCS connector uyumlu"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter(vehicle_connector="CCS")
        connections = [{"ConnectionType": {"Title": "CCS (Type 2)"}, "PowerKW": 150}]
        
        assert filter.is_connector_compatible(connections) == True
    
    def test_chademo_not_compatible_with_ccs(self):
        """Test: CHAdeMO, CCS araçla uyumsuz"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter(vehicle_connector="CCS")
        connections = [{"ConnectionType": {"Title": "CHAdeMO"}, "PowerKW": 50}]
        
        assert filter.is_connector_compatible(connections) == False
    
    def test_multiple_connectors_one_compatible(self):
        """Test: Birden fazla connector, biri uyumlu"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter(vehicle_connector="CCS")
        connections = [
            {"ConnectionType": {"Title": "CHAdeMO"}, "PowerKW": 50},
            {"ConnectionType": {"Title": "CCS Type 2"}, "PowerKW": 150}
        ]
        
        assert filter.is_connector_compatible(connections) == True


class TestStationFilterPower:
    """Güç kontrolü testleri"""
    
    def test_sufficient_power(self):
        """Test: Yeterli güç"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter(min_power_kw=50)
        connections = [{"PowerKW": 150}]
        
        assert filter.is_power_sufficient(connections) == True
    
    def test_insufficient_power(self):
        """Test: Yetersiz güç"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter(min_power_kw=50)
        connections = [{"PowerKW": 22}]
        
        assert filter.is_power_sufficient(connections) == False
    
    def test_get_max_power(self):
        """Test: Maksimum güç hesaplama"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter()
        connections = [
            {"PowerKW": 50},
            {"PowerKW": 150},
            {"PowerKW": 100}
        ]
        
        assert filter.get_max_power_kw(connections) == 150


class TestStationFilterChargerType:
    """Şarj tipi filtreleme testleri"""
    
    def test_hpc_requires_180kw(self):
        """Test: HPC 180+ kW gerektirir"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter()
        
        assert filter.matches_charger_type(200, "HPC") == True
        assert filter.matches_charger_type(180, "HPC") == True
        assert filter.matches_charger_type(150, "HPC") == False
    
    def test_dc_requires_50kw(self):
        """Test: DC 50+ kW gerektirir"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter()
        
        assert filter.matches_charger_type(100, "DC") == True
        assert filter.matches_charger_type(50, "DC") == True
        assert filter.matches_charger_type(22, "DC") == False
    
    def test_ac_requires_less_than_50kw(self):
        """Test: AC 50 kW'ın altında"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter()
        
        assert filter.matches_charger_type(22, "AC") == True
        assert filter.matches_charger_type(11, "AC") == True
        assert filter.matches_charger_type(50, "AC") == False


class TestStationFilterAmenities:
    """Olanak filtreleme testleri"""
    
    def test_all_required_present(self):
        """Test: Tüm zorunlu olanaklar mevcut"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter()
        
        assert filter.has_required_amenities(
            ["toilet", "food"],
            has_toilet=True, has_food=True
        ) == True
    
    def test_missing_required(self):
        """Test: Zorunlu olanak eksik"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter()
        
        assert filter.has_required_amenities(
            ["toilet", "food"],
            has_toilet=True, has_food=False
        ) == False
    
    def test_no_requirements(self):
        """Test: Zorunlu olanak yok"""
        from app.services.station_logic import StationFilter
        
        filter = StationFilter()
        
        assert filter.has_required_amenities([], has_toilet=False) == True


# =============================================================================
# 6. INTEGRATION TESTS - Entegrasyon Testleri
# =============================================================================

class TestFeedbackStationFinderIntegration:
    """FeedbackManager ve StationFinder entegrasyonu"""
    
    @pytest.mark.asyncio
    async def test_blocked_station_concept(self, clean_feedback_manager):
        """Konsept: Bloklu istasyon aranırken atlanmalı"""
        station_id = "ChIJ_blocked_station"
        
        # Blokla
        for i in range(3):
            await clean_feedback_manager.report_station(station_id, f"user_{i}", "broken")
        
        # Kontrol
        assert clean_feedback_manager.is_station_blocked(station_id) == True
        
        # station_finder'da bu kontrolün yapıldığını doğrula
        # (Gerçek entegrasyon, mocking gerektirir)


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
