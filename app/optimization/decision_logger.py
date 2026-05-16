"""
Decision Logger — JSONL Karar Kayıt Sistemi
=============================================

Pareto solver'ın her kararını günlük rotate edilen JSONL dosyalarına yazar.
İleride ML eğitim verisi olarak kullanılır.

KVKK uyumlu: Default olarak koordinatları 1 km'ye yuvarlar.
ENV ile kapatılabilir: LOG_DECISIONS_ANONYMIZE=false → raw koordinat
"""

import json
import os
import uuid
import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.utils.logger import get_logger

logger = get_logger("DecisionLogger")


# =============================================================================
# CONFIG
# =============================================================================

DEFAULT_LOG_DIR = Path("logs")
ANONYMIZE_PRECISION_DEG = 0.01  # ~1 km tolerans
DECISION_SCHEMA_VERSION = "trip_decision_v1_1"
OUTCOME_SCHEMA_VERSION = "trip_outcome_v1"
DEFAULT_MAX_JSONL_BYTES = 50 * 1024 * 1024

_PROCESS_FILE_LOCK = threading.Lock()


def _is_anonymize_enabled() -> bool:
    """ENV'den oku, default True."""
    val = os.getenv("LOG_DECISIONS_ANONYMIZE", "true").lower()
    return val not in ("false", "0", "no", "off")


def _round_coords(lat: float, lon: float) -> Tuple[float, float]:
    """Koordinatları 1 km'ye yuvarla (KVKK)."""
    if not _is_anonymize_enabled():
        return (lat, lon)
    return (
        round(lat / ANONYMIZE_PRECISION_DEG) * ANONYMIZE_PRECISION_DEG,
        round(lon / ANONYMIZE_PRECISION_DEG) * ANONYMIZE_PRECISION_DEG,
    )


def anonymize_coords(lat: Optional[float], lon: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    if lat is None or lon is None:
        return None, None
    return _round_coords(float(lat), float(lon))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_hour_bucket(ts: Optional[datetime] = None) -> str:
    current = ts or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _max_jsonl_bytes() -> int:
    raw = os.getenv("LOG_DECISIONS_MAX_BYTES")
    if not raw:
        return DEFAULT_MAX_JSONL_BYTES
    try:
        return max(1024, int(raw))
    except ValueError:
        logger.warning(f"Invalid LOG_DECISIONS_MAX_BYTES={raw!r}, using default")
        return DEFAULT_MAX_JSONL_BYTES


def _is_full_objective_log_enabled() -> bool:
    env = os.getenv("ENVIRONMENT", "production").lower()
    raw = os.getenv("LOG_DECISIONS_FULL_OBJECTIVE")
    if raw is not None:
        return raw.lower() in ("1", "true", "yes", "on")
    return env in ("development", "test")


def sanitize_objective_breakdown(breakdown: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep full objective details only in debug/test-local logs."""
    if not breakdown:
        return {}
    if _is_full_objective_log_enabled():
        payload = dict(breakdown)
        payload["redacted"] = False
        return payload
    return {
        "J": breakdown.get("J"),
        "is_hard_violation": bool(breakdown.get("is_hard_violation", False)),
        "redacted": True,
    }


@contextmanager
def _cross_process_file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _PROCESS_FILE_LOCK:
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# =============================================================================
# RECORD
# =============================================================================

@dataclass
class OutcomeRecord:
    """
    Trip tamamlandıktan sonra frontend tarafından geri yazılan gerçek değerler.
    Faz 3 ML için critical: predicted vs actual karşılaştırması.

    Aynı trip_id'li DecisionRecord ile join edilir.
    """
    trip_id: str
    timestamp_iso: str  # outcome'un kaydedildiği zaman
    actual_arrival_soc: Optional[float] = None
    actual_total_time_min: Optional[float] = None
    actual_charge_time_min: Optional[float] = None
    actual_total_cost_tl: Optional[float] = None
    actual_num_charges: Optional[int] = None
    user_satisfaction: Optional[int] = None  # 1-5, opsiyonel
    notes: Optional[str] = None
    schema_version: str = OUTCOME_SCHEMA_VERSION
    timestamp_hour_bucket: str = field(default_factory=utc_hour_bucket)


@dataclass
class DecisionRecord:
    """Bir Pareto kararının tam kaydı."""
    # Identity
    trip_id: str
    timestamp_iso: str

    # Context — anonymized
    origin_lat: float
    origin_lon: float
    destination_lat: float
    destination_lon: float
    total_distance_km: float
    vehicle_id: str
    battery_kwh: float
    start_soc: float
    arrival_soc_target: float

    # Mode
    smart_plan_enabled: bool
    optimization_mode: str
    weights: Dict[str, float]

    # Decision
    num_stops: int
    per_stop_target_socs: List[float]
    per_stop_min_required: List[float]
    per_stop_dynamic_buffers: List[float]

    # Predicted outcome
    predicted_total_time_min: float
    predicted_charge_time_min: float
    predicted_total_cost_tl: float
    predicted_arrival_soc_margin: float
    predicted_high_soc_minutes: float
    J_score: float

    # Counterfactual (mevcut greedy ile karşılaştırma — opsiyonel)
    fallback_used: bool = False
    error_messages: List[str] = field(default_factory=list)
    schema_version: str = DECISION_SCHEMA_VERSION
    record_scope: str = "pareto_solver"
    timestamp_hour_bucket: str = field(default_factory=utc_hour_bucket)
    status: str = "success"
    error_code: Optional[str] = None
    total_elapsed_ms: Optional[int] = None
    decision_reason: Optional[str] = None
    provider_metadata: Dict[str, Any] = field(default_factory=dict)
    call_counts: Dict[str, int] = field(default_factory=dict)
    cache: Dict[str, Any] = field(default_factory=dict)
    fallback_usage: Dict[str, Any] = field(default_factory=dict)
    vehicle_profile_hash: Optional[str] = None
    vehicle_spec: Dict[str, Any] = field(default_factory=dict)
    segment_feature_summary: Dict[str, Any] = field(default_factory=dict)
    station_candidate_metadata: Dict[str, Any] = field(default_factory=dict)
    station_candidates: List[Dict[str, Any]] = field(default_factory=list)
    skipped_station_reasons: List[Dict[str, Any]] = field(default_factory=list)
    selected_stations: List[Dict[str, Any]] = field(default_factory=list)
    soc_trajectory: List[Dict[str, Any]] = field(default_factory=list)
    objective_breakdown: Dict[str, Any] = field(default_factory=dict)
    plan_quality_snapshot: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# LOGGER
# =============================================================================

class DecisionLogger:
    """Append-only JSONL yazıcı, günlük rotate."""

    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"DecisionLogger log_dir mkdir failed: {e}")

    def _today_decision_path(self) -> Path:
        return self.log_dir / f"decisions_{date.today().isoformat()}.jsonl"

    def _today_outcome_path(self) -> Path:
        return self.log_dir / f"outcomes_{date.today().isoformat()}.jsonl"

    # Geriye uyumlu alias
    def _today_path(self) -> Path:
        return self._today_decision_path()

    def _iter_day_paths(self, prefix: str, day: date) -> Iterable[Path]:
        base = self.log_dir / f"{prefix}_{day.isoformat()}.jsonl"
        paths = [base]
        paths.extend(sorted(self.log_dir.glob(f"{prefix}_{day.isoformat()}_*.jsonl")))
        return paths

    def _rollover_if_needed(self, path: Path, next_line_bytes: int) -> Path:
        max_bytes = _max_jsonl_bytes()
        if not path.exists() or path.stat().st_size + next_line_bytes <= max_bytes:
            return path

        for index in range(1, 1000):
            rolled = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
            if not rolled.exists():
                path.replace(rolled)
                logger.info(f"DecisionLogger rolled over {path.name} -> {rolled.name}")
                return path

        logger.warning(f"DecisionLogger rollover exhausted for {path.name}; appending to active file")
        return path

    def _append_jsonl(self, path: Path, payload: Dict[str, Any]) -> bool:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        encoded_size = len(line.encode("utf-8"))
        lock_path = self.log_dir / ".decision_logger.lock"
        with _cross_process_file_lock(lock_path):
            write_path = self._rollover_if_needed(path, encoded_size)
            with write_path.open("a", encoding="utf-8", newline="") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        return True

    def write(self, record: DecisionRecord) -> bool:
        """
        Append decision record. Hata olursa False döner ama exception fırlatmaz
        (logging asla ana akışı kırmamalı).
        """
        try:
            return self._append_jsonl(self._today_decision_path(), asdict(record))
        except Exception as e:
            logger.warning(f"DecisionLogger write failed: {e}")
            return False

    def write_outcome(self, outcome: OutcomeRecord) -> bool:
        """
        Append outcome record. Aynı trip_id'li DecisionRecord ile join'lenebilir.
        """
        try:
            self._append_jsonl(self._today_outcome_path(), asdict(outcome))
            logger.info(f"Outcome recorded: trip_id={outcome.trip_id}")
            return True
        except Exception as e:
            logger.warning(f"DecisionLogger write_outcome failed: {e}")
            return False

    def load_decisions_with_outcomes(
        self,
        days_back: int = 7,
    ) -> List[Dict]:
        """
        Son N günün decision + outcome kayıtlarını trip_id ile JOIN edip
        merged dict listesi döner. ML eğitimi için temel adım.

        Returns:
            [{**decision_fields, "outcome": {...} or None}, ...]
        """
        from datetime import timedelta

        merged: Dict[str, Dict] = {}
        outcomes_by_trip: Dict[str, Dict] = {}

        # Son N günün dosyalarını topla
        today = date.today()
        for d in range(days_back):
            day = today - timedelta(days=d)

            for dec_path in self._iter_day_paths("decisions", day):
                if not dec_path.exists():
                    continue
                for line in dec_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                        trip_id = rec.get("trip_id")
                        if trip_id:
                            merged[trip_id] = rec
                    except json.JSONDecodeError:
                        continue

            for out_path in self._iter_day_paths("outcomes", day):
                if not out_path.exists():
                    continue
                for line in out_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                        trip_id = rec.get("trip_id")
                        if trip_id:
                            # En son gelen outcome'u tut (kullanıcı düzeltirse)
                            outcomes_by_trip[trip_id] = rec
                    except json.JSONDecodeError:
                        continue

        # Birleştir
        result = []
        for trip_id, dec in merged.items():
            dec_with_outcome = {**dec, "outcome": outcomes_by_trip.get(trip_id)}
            result.append(dec_with_outcome)
        return result


# =============================================================================
# CONVENIENCE — record builder
# =============================================================================

def build_record(
    *,
    origin: Tuple[float, float],
    destination: Tuple[float, float],
    total_distance_km: float,
    vehicle_id: str,
    battery_kwh: float,
    start_soc: float,
    arrival_soc_target: float,
    smart_plan_enabled: bool,
    optimization_mode: str,
    weights: Dict[str, float],
    pareto_solution,  # ParetoSolution
    error_messages: Optional[List[str]] = None,
    trip_id: Optional[str] = None,  # FAZ 2: dışarıdan trip_id geçilirse onu kullan
) -> DecisionRecord:
    """ParetoSolution'dan DecisionRecord oluştur (anonymize uygulanmış)."""
    o_lat, o_lon = _round_coords(*origin)
    d_lat, d_lon = _round_coords(*destination)

    return DecisionRecord(
        trip_id=trip_id or str(uuid.uuid4())[:12],
        timestamp_iso=datetime.now(timezone.utc).isoformat(),
        origin_lat=o_lat,
        origin_lon=o_lon,
        destination_lat=d_lat,
        destination_lon=d_lon,
        total_distance_km=round(total_distance_km, 1),
        vehicle_id=vehicle_id,
        battery_kwh=battery_kwh,
        start_soc=start_soc,
        arrival_soc_target=arrival_soc_target,
        smart_plan_enabled=smart_plan_enabled,
        optimization_mode=optimization_mode,
        weights=weights,
        num_stops=pareto_solution.metrics.num_stops,
        per_stop_target_socs=pareto_solution.per_stop_target_soc,
        per_stop_min_required=[p.min_target_soc for p in pareto_solution.planned_stops],
        per_stop_dynamic_buffers=[p.dynamic_buffer for p in pareto_solution.planned_stops],
        predicted_total_time_min=round(
            pareto_solution.metrics.total_drive_time_min
            + pareto_solution.metrics.total_charge_time_min, 1
        ),
        predicted_charge_time_min=round(pareto_solution.metrics.total_charge_time_min, 1),
        predicted_total_cost_tl=round(pareto_solution.metrics.total_cost_tl, 2),
        predicted_arrival_soc_margin=round(pareto_solution.metrics.min_arrival_soc_margin, 1),
        predicted_high_soc_minutes=round(pareto_solution.metrics.high_soc_minutes, 1),
        J_score=round(pareto_solution.breakdown.J, 6),
        fallback_used=pareto_solution.fallback_used,
        error_messages=error_messages or [],
        record_scope="pareto_solver",
        objective_breakdown=sanitize_objective_breakdown(asdict(pareto_solution.breakdown)),
    )


# Singleton (orchestrator'dan kolay erişim)
def build_route_decision_record(
    *,
    trip_id: str,
    origin: Tuple[float, float],
    destination: Tuple[float, float],
    total_distance_km: float,
    route_duration_min: float,
    battery_kwh: float,
    start_soc: float,
    arrival_soc_target: float,
    final_soc: float,
    vehicle_spec: Dict[str, Any],
    smart_plan_enabled: bool,
    optimization_mode: str,
    decision_reason: str,
    num_stops: int,
    selected_stations: List[Dict[str, Any]],
    station_candidates: List[Dict[str, Any]],
    skipped_station_reasons: List[Dict[str, Any]],
    soc_trajectory: List[Dict[str, Any]],
    segment_feature_summary: Dict[str, Any],
    plan_quality: Dict[str, Any],
    total_elapsed_ms: int,
    status: str = "success",
    error_code: Optional[str] = None,
    objective_breakdown: Optional[Dict[str, Any]] = None,
    station_candidate_metadata: Optional[Dict[str, Any]] = None,
) -> DecisionRecord:
    """Build the Sprint 8 final route-level decision record."""
    o_lat, o_lon = anonymize_coords(*origin)
    d_lat, d_lon = anonymize_coords(*destination)
    safe_vehicle_spec = {
        key: value for key, value in vehicle_spec.items()
        if value is not None and key not in {"user_id", "email", "ip", "raw_address"}
    }

    return DecisionRecord(
        trip_id=trip_id,
        timestamp_iso=utc_now_iso(),
        origin_lat=float(o_lat or 0.0),
        origin_lon=float(o_lon or 0.0),
        destination_lat=float(d_lat or 0.0),
        destination_lon=float(d_lon or 0.0),
        total_distance_km=round(float(total_distance_km), 1),
        vehicle_id=str(safe_vehicle_spec.get("slug") or safe_vehicle_spec.get("id") or "unknown"),
        battery_kwh=round(float(battery_kwh), 2),
        start_soc=round(float(start_soc), 1),
        arrival_soc_target=round(float(arrival_soc_target), 1),
        smart_plan_enabled=bool(smart_plan_enabled),
        optimization_mode=optimization_mode or "balanced",
        weights={},
        num_stops=int(num_stops),
        per_stop_target_socs=[
            float(item.get("departure_soc")) for item in selected_stations
            if item.get("departure_soc") is not None
        ],
        per_stop_min_required=[],
        per_stop_dynamic_buffers=[],
        predicted_total_time_min=round(float(route_duration_min), 1),
        predicted_charge_time_min=round(sum(float(s.get("charge_time_min") or 0.0) for s in selected_stations), 1),
        predicted_total_cost_tl=round(sum(float(s.get("estimated_cost") or 0.0) for s in selected_stations), 2),
        predicted_arrival_soc_margin=round(float(final_soc) - float(arrival_soc_target), 1),
        predicted_high_soc_minutes=0.0,
        J_score=0.0,
        fallback_used=bool(plan_quality.get("fallback_used", False)),
        error_messages=list(plan_quality.get("warnings", []) or []),
        record_scope="route_final",
        status=status,
        error_code=error_code,
        total_elapsed_ms=int(total_elapsed_ms),
        decision_reason=decision_reason,
        provider_metadata=dict(plan_quality.get("provider_versions", {}) or {}),
        call_counts=dict(plan_quality.get("call_counts", {}) or {}),
        cache=dict(plan_quality.get("cache", {}) or {}),
        fallback_usage={
            "used": bool(plan_quality.get("fallback_used", False)),
            "reasons": list(plan_quality.get("fallback_reasons", []) or []),
        },
        vehicle_profile_hash=stable_hash(safe_vehicle_spec),
        vehicle_spec=safe_vehicle_spec,
        segment_feature_summary=segment_feature_summary,
        station_candidate_metadata=station_candidate_metadata or {},
        station_candidates=station_candidates or [],
        skipped_station_reasons=skipped_station_reasons or [],
        selected_stations=selected_stations,
        soc_trajectory=soc_trajectory,
        objective_breakdown=sanitize_objective_breakdown(objective_breakdown),
        plan_quality_snapshot=plan_quality,
    )


_default_logger: Optional[DecisionLogger] = None


def get_decision_logger() -> DecisionLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = DecisionLogger()
    return _default_logger
