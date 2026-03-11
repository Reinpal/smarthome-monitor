"""Metric definitions and unit mappings for OpenTelemetry export."""

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
    "l/min": "l/min",
    "%": "%",
    "h": "h",
    "min": "min",
    "": "",
}


def get_otel_unit(isg_unit: str) -> str:
    """Convert an ISG unit to an OpenTelemetry-compatible unit string."""
    return UNIT_MAP.get(isg_unit, isg_unit)


# Metrics that represent cumulative counters (monotonically increasing).
# All others are treated as gauges.
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
    # Energiebilanz page - cumulative totals (12M and 24M periods)
    "energiebilanz.waermemenge.heizen_1_12_m",
    "energiebilanz.waermemenge.heizen_13_24_m",
    "energiebilanz.waermemenge.warmwasser_1_12_m",
    "energiebilanz.waermemenge.warmwasser_13_24_m",
    "energiebilanz.stromverbrauch.heizen_1_12_m",
    "energiebilanz.stromverbrauch.heizen_13_24_m",
    "energiebilanz.stromverbrauch.warmwasser_1_12_m",
    "energiebilanz.stromverbrauch.warmwasser_13_24_m",
]

# Fronius metrics that represent cumulative counters
FRONIUS_COUNTER_PATTERNS = [
    # Energy totals (monotonically increasing)
    "fronius.powerflow.e_total",
    "fronius.powerflow.e_year",
    "fronius.inverter.total_energy",
    "fronius.inverter.year_energy",
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
