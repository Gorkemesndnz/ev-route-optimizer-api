"""
Feedback Service - İstasyon Güven Yönetimi
==========================================

Crowd-sourcing tabanlı istasyon feedback sistemi.
Kullanıcı reportlarına göre sorunlu istasyonları geçici olarak bloklar.

Kullanım:
    from app.services.feedback_service import feedback_manager
    
    # Report a station
    feedback_manager.report_station("ChIJ...", "user123", "out_of_service")
    
    # Check if blocked
    if feedback_manager.is_station_blocked("ChIJ..."):
        # Skip this station
        pass
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from app.utils.logger import get_logger

logger = get_logger("FeedbackService")


# =============================================================================
# CONSTANTS
# =============================================================================

REPORT_THRESHOLD = 3          # 3 farklı kullanıcı report = blok
BLOCK_DURATION_HOURS = 48     # Blok süresi: 48 saat
REPORT_WINDOW_HOURS = 24      # Report geçerlilik penceresi: 24 saat


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class StationReport:
    """Tek bir kullanıcı report'u."""
    user_id: str
    reason: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class BlockedStation:
    """Bloklanmış istasyon bilgisi."""
    station_id: str
    blocked_at: datetime
    expiry_time: datetime
    report_count: int
    reasons: List[str] = field(default_factory=list)


# =============================================================================
# FEEDBACK MANAGER (Singleton)
# =============================================================================

class FeedbackManager:
    """
    İstasyon feedback yöneticisi.
    
    3-Strike Rule:
    - 24 saat içinde 3 farklı kullanıcıdan report = 48 saat blok
    - Aynı kullanıcı 24 saat içinde tekrar report yapamaz (spam koruması)
    - Zaten bloklu istasyona yeni report = süre uzatılır
    """
    
    _instance = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        # Station reports: {station_id: [StationReport, ...]}
        self._reports: Dict[str, List[StationReport]] = {}
        
        # Blocked stations: {station_id: BlockedStation}
        self._blocked: Dict[str, BlockedStation] = {}
        
        # Async lock for thread-safety
        self._async_lock = asyncio.Lock()
        
        self._initialized = True
        logger.info("FeedbackManager initialized")
    
    async def report_station(
        self,
        station_id: str,
        user_id: str,
        reason: str
    ) -> Dict[str, any]:
        """
        İstasyon report'u kaydet.
        
        Args:
            station_id: İstasyon ID (Google Places veya OCM)
            user_id: Kullanıcı ID
            reason: Report sebebi (out_of_service, wrong_location, etc.)
        
        Returns:
            {
                "status": "recorded" | "spam_detected" | "station_blocked",
                "message": str,
                "is_blocked": bool,
                "block_expiry": datetime | None
            }
        """
        async with self._async_lock:
            now = datetime.now()
            window_start = now - timedelta(hours=REPORT_WINDOW_HOURS)
            
            # Initialize reports list if needed
            if station_id not in self._reports:
                self._reports[station_id] = []
            
            # STEP 1: Spam Check - Aynı kullanıcı 24 saat içinde report yapmış mı?
            existing_report = None
            for report in self._reports[station_id]:
                if report.user_id == user_id and report.timestamp > window_start:
                    existing_report = report
                    break
            
            if existing_report:
                # Update existing report instead of creating duplicate
                existing_report.reason = reason
                existing_report.timestamp = now
                logger.info(
                    f"Spam detected - user {user_id} already reported station {station_id}",
                    station_id=station_id,
                    user_id=user_id
                )
                return {
                    "status": "spam_detected",
                    "message": "Bu istasyonu zaten bildirdiniz. Raporunuz güncellendi.",
                    "is_blocked": self.is_station_blocked(station_id),
                    "block_expiry": self._get_block_expiry(station_id)
                }
            
            # STEP 2: Create new report
            new_report = StationReport(
                user_id=user_id,
                reason=reason,
                timestamp=now
            )
            self._reports[station_id].append(new_report)
            
            # Clean old reports (outside window)
            self._reports[station_id] = [
                r for r in self._reports[station_id]
                if r.timestamp > window_start
            ]
            
            # STEP 3: Count unique users in window
            unique_users: Set[str] = set()
            reasons_list: List[str] = []
            for report in self._reports[station_id]:
                if report.timestamp > window_start:
                    unique_users.add(report.user_id)
                    if report.reason not in reasons_list:
                        reasons_list.append(report.reason)
            
            unique_count = len(unique_users)
            
            logger.info(
                f"Station report recorded",
                station_id=station_id,
                user_id=user_id,
                reason=reason,
                unique_reports=unique_count,
                threshold=REPORT_THRESHOLD
            )
            
            # STEP 4: Check if threshold reached
            if unique_count >= REPORT_THRESHOLD:
                return await self._block_station(station_id, unique_count, reasons_list)
            
            return {
                "status": "recorded",
                "message": f"Raporunuz kaydedildi. ({unique_count}/{REPORT_THRESHOLD} rapor)",
                "is_blocked": False,
                "block_expiry": None
            }
    
    async def _block_station(
        self,
        station_id: str,
        report_count: int,
        reasons: List[str]
    ) -> Dict[str, any]:
        """İstasyonu blokla veya süresini uzat."""
        now = datetime.now()
        
        if station_id in self._blocked:
            # Already blocked - extend duration
            blocked = self._blocked[station_id]
            old_expiry = blocked.expiry_time
            blocked.expiry_time = max(
                blocked.expiry_time,
                now + timedelta(hours=BLOCK_DURATION_HOURS)
            )
            blocked.report_count = report_count
            blocked.reasons = reasons
            
            logger.warning(
                f"Station block extended",
                station_id=station_id,
                old_expiry=old_expiry.isoformat(),
                new_expiry=blocked.expiry_time.isoformat()
            )
            
            return {
                "status": "block_extended",
                "message": f"İstasyon blok süresi uzatıldı. Yeni bitiş: {blocked.expiry_time.strftime('%d.%m.%Y %H:%M')}",
                "is_blocked": True,
                "block_expiry": blocked.expiry_time
            }
        else:
            # New block
            expiry = now + timedelta(hours=BLOCK_DURATION_HOURS)
            self._blocked[station_id] = BlockedStation(
                station_id=station_id,
                blocked_at=now,
                expiry_time=expiry,
                report_count=report_count,
                reasons=reasons
            )
            
            logger.warning(
                f"Station blocked",
                station_id=station_id,
                report_count=report_count,
                reasons=reasons,
                expiry=expiry.isoformat()
            )
            
            return {
                "status": "station_blocked",
                "message": f"İstasyon {report_count} rapor nedeniyle geçici olarak bloklandı. Bitiş: {expiry.strftime('%d.%m.%Y %H:%M')}",
                "is_blocked": True,
                "block_expiry": expiry
            }
    
    def is_station_blocked(self, station_id: str) -> bool:
        """
        İstasyonun bloklu olup olmadığını kontrol et.
        Süresi dolmuşsa otomatik unblock yapar.
        """
        if station_id not in self._blocked:
            return False
        
        blocked = self._blocked[station_id]
        now = datetime.now()
        
        if now > blocked.expiry_time:
            # Block expired - auto unblock
            del self._blocked[station_id]
            logger.info(
                f"Station auto-unblocked (expired)",
                station_id=station_id
            )
            return False
        
        return True
    
    def _get_block_expiry(self, station_id: str) -> Optional[datetime]:
        """Blok bitiş zamanını döndür."""
        if station_id in self._blocked:
            return self._blocked[station_id].expiry_time
        return None
    
    def get_blocked_stations(self) -> List[Dict[str, any]]:
        """Tüm bloklu istasyonları listele (debug için)."""
        # First, clean expired blocks
        now = datetime.now()
        expired = [
            sid for sid, b in self._blocked.items()
            if now > b.expiry_time
        ]
        for sid in expired:
            del self._blocked[sid]
        
        return [
            {
                "station_id": b.station_id,
                "blocked_at": b.blocked_at.isoformat(),
                "expiry_time": b.expiry_time.isoformat(),
                "report_count": b.report_count,
                "reasons": b.reasons,
                "remaining_hours": round((b.expiry_time - now).total_seconds() / 3600, 1)
            }
            for b in self._blocked.values()
        ]
    
    def get_station_reports(self, station_id: str) -> List[Dict[str, any]]:
        """Bir istasyonun son raporlarını getir (debug için)."""
        if station_id not in self._reports:
            return []
        
        window_start = datetime.now() - timedelta(hours=REPORT_WINDOW_HOURS)
        return [
            {
                "user_id": r.user_id[:8] + "...",  # Privacy
                "reason": r.reason,
                "timestamp": r.timestamp.isoformat()
            }
            for r in self._reports[station_id]
            if r.timestamp > window_start
        ]
    
    def clear_all(self) -> None:
        """Tüm verileri temizle (test için)."""
        self._reports.clear()
        self._blocked.clear()
        logger.info("FeedbackManager data cleared")


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

feedback_manager = FeedbackManager()
