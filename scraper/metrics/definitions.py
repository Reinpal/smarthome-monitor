"""Metric definitions and unit mappings for OpenTelemetry export."""

import math


def energy_in_kwh(value: float, unit: str) -> float | None:
    """Normalize a nonnegative device energy reading; unknown units are unavailable."""
    factor = {"Wh": 0.001, "kWh": 1.0, "KWh": 1.0, "MWh": 1000.0}.get(unit)
    if factor is None or not math.isfinite(value) or value < 0:
        return None
    result = value * factor
    return result if math.isfinite(result) else None


# Map ISG/Fronius units to OTel-compatible unit strings
# See: https://opentelemetry.io/docs/specs/semconv/general/metrics/
UNIT_MAP = {
    "°C": "Cel",
    "bar": "bar",
    "mbar": "mbar",
    "V": "V",
    "A": "A",
    "Hz": "Hz",
    "W": "W",
    "Wh": "Wh",
    "kW": "kW",
    "kWh": "kWh",
    "KWh": "kWh",
    "MWh": "MWh",
    "Ah": "Ah",
    "VA": "VA",
    "var": "var",
    "l/min": "l/min",
    "%": "%",
    "h": "h",
    "min": "min",
    "": "",
}


def get_otel_unit(isg_unit: str) -> str:
    """Convert an ISG unit to an OpenTelemetry-compatible unit string."""
    return UNIT_MAP.get(isg_unit, isg_unit)


# Device cumulative readings (can reset). Exported as gauges for compatibility.
# Daily/yearly reset values and rolling energy balances are not lifetime totals.
# These are identified by page_name.section_normalized.key_normalized patterns.
COUNTER_PATTERNS = [
    # Wärmepumpe page - Wärmemenge (cumulative energy totals)
    "waermepumpe.waermemenge.vd_heizen_summe",
    "waermepumpe.waermemenge.vd_warmwasser_summe",
    "waermepumpe.waermemenge.nhz_heizen_summe",
    "waermepumpe.waermemenge.nhz_warmwasser_summe",
    # Wärmepumpe page - Leistungsaufnahme (cumulative consumption totals)
    "waermepumpe.leistungsaufnahme.vd_heizen_summe",
    "waermepumpe.leistungsaufnahme.vd_warmwasser_summe",
    # Wärmepumpe page - Laufzeit (cumulative runtimes)
    "waermepumpe.laufzeit.vd_heizen",
    "waermepumpe.laufzeit.vd_warmwasser",
    "waermepumpe.laufzeit.vd_abtauen",
    "waermepumpe.laufzeit.nhz_1",
    "waermepumpe.laufzeit.nhz_2",
    "waermepumpe.laufzeit.nhz_1_2",
    "waermepumpe.laufzeit.starts_abtauen",
    # Wärmepumpe page - Starts
    "waermepumpe.starts.verdichter",
]

# Fronius metrics that represent cumulative counters
FRONIUS_COUNTER_PATTERNS = [
    # Energy totals (monotonically increasing)
    "fronius.powerflow.e_total",
    "fronius.inverter.total_energy",
    # Grid meter cumulative energy
    "fronius.meter.energy_real_consumed",
    "fronius.meter.energy_real_produced",
    "fronius.meter.energy_real_abs_minus",
    "fronius.meter.energy_real_abs_plus",
]


def is_counter_metric(metric_name: str) -> bool:
    """Check if a metric should be treated as a counter (vs gauge).

    Args:
        metric_name: Full metric name like 'heatpump.waermepumpe.starts.verdichter'
                     or 'fronius.powerflow.e_total'

    Returns:
        True if this metric is a cumulative counter.
    """
    # ISG heatpump metrics: strip the 'heatpump.' prefix
    if metric_name.startswith("heatpump."):
        short_name = metric_name.removeprefix("heatpump.")
        return short_name in COUNTER_PATTERNS

    # Fronius metrics: match directly
    if metric_name.startswith("fronius."):
        return metric_name in FRONIUS_COUNTER_PATTERNS

    return False
