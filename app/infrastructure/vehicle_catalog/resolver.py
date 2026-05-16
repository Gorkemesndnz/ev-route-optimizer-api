"""
Vehicle Spec Resolver
=====================

VehiclePayload (MSSQL'den .NET araciligiyla gelen veri) ->
VehicleSpec (hesaplama motorunun kullandigi dataclass) donusumu.

Eksik alanlar icin fallback zinciri uygular.
"""

from typing import Optional, List
from app.infrastructure.vehicle_catalog.models import (
    VehicleSpec,
    ChargeCurve,
    ChargeCurvePoint,
    ConnectorType,
    VehicleType,
    DEFAULT_WEIGHTS_BY_TYPE,
)
from app.models import VehiclePayload
from app.utils.logger import get_logger

logger = get_logger("vehicle_resolver")


# =========================================================================
# FALLBACK DEFAULTS
# =========================================================================

_DEFAULT_DRAG_COEFFICIENTS = {
    "sedan": 0.28,
    "hatchback": 0.30,
    "suv": 0.32,
    "compact": 0.29,
    "car": 0.28,
}

_DEFAULT_FRONTAL_AREAS = {
    "sedan": 2.2,
    "hatchback": 2.1,
    "suv": 2.6,
    "compact": 2.0,
    "car": 2.2,
}

_VEHICLE_TYPE_MAP = {
    "suv": VehicleType.SUV,
    "sedan": VehicleType.CAR,
    "hatchback": VehicleType.HATCHBACK,
    "compact": VehicleType.COMPACT,
    "car": VehicleType.CAR,
    "motorbike": VehicleType.MOTORBIKE,
    "microcar": VehicleType.MICROCAR,
}


# =========================================================================
# CORE RESOLVER
# =========================================================================

def _resolve_base_consumption_wh_km(payload: VehiclePayload) -> float:
    """
    Baz tuketimi coz — birden fazla kaynaktan fallback zinciri.

    Oncelik sirasi:
    1. efficiency_wh_km (gercek dunya ortalamasi)
    2. wltp_vehicle_consumption_wh_km
    3. wltp_nominal_consumption_wh_km
    4. efficiency_mild_combined_wh_km
    5. Son care: 175 Wh/km
    """
    candidates = [
        payload.efficiency_wh_km,
        payload.wltp_vehicle_consumption_wh_km,
        payload.wltp_nominal_consumption_wh_km,
        payload.efficiency_mild_combined_wh_km,
    ]

    for val in candidates:
        if val is not None and val > 0:
            return val

    logger.warning(
        f"Vehicle {payload.slug}: No consumption data found, using 175 Wh/km default"
    )
    return 175.0


def _resolve_curb_weight(payload: VehiclePayload) -> int:
    """Arac agirligini coz — eksikse vehicle type'a gore default."""
    if payload.curb_weight_kg and payload.curb_weight_kg > 0:
        return payload.curb_weight_kg

    vtype = _VEHICLE_TYPE_MAP.get(payload.vehicle_type.lower(), VehicleType.CAR)
    default = DEFAULT_WEIGHTS_BY_TYPE.get(vtype, 1700)
    logger.warning(
        f"Vehicle {payload.slug}: curb_weight_kg missing, using {default}kg "
        f"(default for {payload.vehicle_type})"
    )
    return default


def _resolve_connector_type(payload: VehiclePayload) -> ConnectorType:
    """Konnektor tipini coz."""
    try:
        return ConnectorType.from_string(payload.connector_type)
    except (ValueError, KeyError):
        return ConnectorType.CCS


def _resolve_vehicle_type(payload: VehiclePayload) -> VehicleType:
    """VehicleType enum'una cevir."""
    return _VEHICLE_TYPE_MAP.get(payload.vehicle_type.lower(), VehicleType.CAR)


def _resolve_charge_curve(payload: VehiclePayload) -> Optional[ChargeCurve]:
    """Sarj egrisini coz — payload'da varsa donustur."""
    if not payload.charge_curve:
        return None

    points = []
    for point in payload.charge_curve:
        try:
            soc = point.soc if hasattr(point, 'soc') else point.get("soc", 0)
            power = point.power_kw if hasattr(point, 'power_kw') else point.get("power_kw", 0)
            if soc > 0 or power > 0:
                points.append(ChargeCurvePoint(soc_percent=soc, power_kw=power))
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid charge curve point: {e}")
            continue

    if not points:
        return None

    return ChargeCurve(
        vehicle_id=payload.slug,
        is_measured=True,
        points=points,
    )


def resolve_vehicle_spec(payload: VehiclePayload) -> VehicleSpec:
    """
    VehiclePayload -> VehicleSpec donusumu (fallback zincirleri ile).

    MSSQL'deki zengin arac verisi Python hesaplama motorunun
    bekledigi VehicleSpec dataclass'ina donusturulur.
    Eksik alanlar icin akilli fallback'ler uygulanir.

    Args:
        payload: .NET Gateway'den gelen arac verisi

    Returns:
        VehicleSpec: Hesaplama motoru icin hazir arac profili
    """
    base_consumption = _resolve_base_consumption_wh_km(payload)
    curb_weight = _resolve_curb_weight(payload)
    connector_type = _resolve_connector_type(payload)
    vehicle_type = _resolve_vehicle_type(payload)
    vtype_key = payload.vehicle_type.lower()

    # Drag coefficient fallback
    drag_cd = payload.drag_coefficient
    if not drag_cd or drag_cd <= 0:
        drag_cd = _DEFAULT_DRAG_COEFFICIENTS.get(vtype_key, 0.28)

    # Frontal area fallback
    frontal_area = payload.frontal_area_m2
    if not frontal_area or frontal_area <= 0:
        frontal_area = _DEFAULT_FRONTAL_AREAS.get(vtype_key, 2.2)

    # fastcharge_power_avg fallback
    fc_avg = payload.fastcharge_power_avg_kw
    if not fc_avg or fc_avg <= 0:
        fc_avg = payload.fastcharge_power_max_kw * 0.65

    spec = VehicleSpec(
        id=payload.slug,
        source_id=str(payload.id),
        brand=payload.brand,
        model=payload.model,
        variant=payload.variant or "",
        year=payload.year,
        display_name=f"{payload.brand} {payload.model} {payload.variant} ({payload.year})".strip(),
        battery_capacity_kwh=payload.battery_useable_kwh,
        base_consumption_wh_km=base_consumption,
        connector_type=connector_type,
        ac_max_kw=payload.ac_charge_power_kw,
        dc_max_kw=payload.fastcharge_power_max_kw,
        charging_voltage=payload.charging_voltage,
        curb_weight_kg=curb_weight,
        auxiliary_power_kw=1.2,  # Sabit — MSSQL'de bu veri yok
        regen_efficiency=0.65,  # Sabit — arac bazli override gelecekte
        regen_max_power_kw=payload.regen_max_power_kw,
        battery_chemistry=payload.battery_chemistry,
        has_real_curve=payload.charge_curve is not None and len(payload.charge_curve) > 0,
        vehicle_type=vehicle_type,
        drag_coefficient=drag_cd,
        frontal_area_m2=frontal_area,
        # Sprint 2: Soğuk hava ve preconditioning bilgileri payload'dan geçer.
        # Resolver'da fallback yok — kaynak veride yoksa default False kalır.
        has_heat_pump=payload.heat_pump,
        battery_preconditioning=payload.battery_preconditioning,
        charge_curve=_resolve_charge_curve(payload)
    )

    logger.info(
        "Resolved VehicleSpec from MSSQL payload",
        vehicle=payload.slug,
        battery_kwh=spec.battery_capacity_kwh,
        consumption_wh_km=spec.base_consumption_wh_km,
        curb_weight=spec.curb_weight_kg,
        drag_cd=drag_cd,
        frontal_area=frontal_area,
        dc_max_kw=spec.dc_max_kw,
        heat_pump=spec.has_heat_pump,
        battery_preconditioning=spec.battery_preconditioning,
    )

    return spec
