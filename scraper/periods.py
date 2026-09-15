"""Period calculation interface v1. No I/O, global cache, or device assumptions here.

Inputs are fresh, contract-v2 observations returned by period_query. Public
results are JSON-safe and include unavailable/coverage independently of prices.
"""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import math

from scraper.installation import PriceUnavailable


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime

    def __post_init__(self):
        if any(t.tzinfo is None or t.utcoffset() is None for t in (self.start, self.end)):
            raise ValueError("period: aware endpoints required")
        if self.end.timestamp() <= self.start.timestamp():
            raise ValueError("period: end must follow start")

    @property
    def seconds(self):
        return self.end.timestamp() - self.start.timestamp()


def calendar_period(start: date, end: date, installation) -> Period:
    """Local dates, inclusive start/exclusive end; never assume 24-hour days."""
    return Period(datetime.combine(start, time.min, installation.timezone),
                  datetime.combine(end, time.min, installation.timezone))


def month_comparison(period, installation):
    """MTD only. Cap BOTH comparison windows to the shorter elapsed duration."""
    local = period.start.astimezone(installation.timezone)
    if local.day != 1 or local.timetz().replace(tzinfo=None) != time.min:
        return None
    next_month = (local.date().replace(day=28) + timedelta(days=4)).replace(day=1)
    if period.end.timestamp() > calendar_period(local.date(), next_month, installation).end.timestamp():
        return None
    previous = (local.date() - timedelta(days=1)).replace(day=1)
    prior = calendar_period(previous, local.date(), installation)
    elapsed = min(period.seconds, prior.seconds)
    def window(start):
        return Period(start, datetime.fromtimestamp(start.timestamp() + elapsed, timezone.utc))
    return window(period.start), window(prior.start)


@dataclass(frozen=True)
class Source:
    name: str
    unit: str
    kind: str
    boundary: str
    collector: str = "fronius"
    endpoint: str = "powerflow"

    @property
    def prom_name(self):
        return self.name.replace('.', '_') + '_' + self.unit


SOURCES = {
    "household": Source("fronius.powerflow.p_load", "watts", "load", "device-calculated primary-meter site demand"),
    "pv": Source("fronius.powerflow.p_pv", "watts", "power", "DC PV generator; not hybrid AC output"),
    "grid_import": Source("fronius.meter.energy_real_abs_plus", "Wh", "counter", "unique validated grid-interconnection meter", endpoint="meter"),
    "grid_export": Source("fronius.meter.energy_real_abs_minus", "Wh", "counter", "unique validated grid-interconnection meter", endpoint="meter"),
    "battery_charge": Source("fronius.powerflow.p_akku", "watts", "charge", "battery-side, not household AC delivery"),
    "battery_discharge": Source("fronius.powerflow.p_akku", "watts", "discharge", "battery-side, not household AC delivery"),
    "soc": Source("fronius.powerflow.soc", "percent", "soc", "SOC times private usable capacity; inventory estimate"),
}
for purpose in ("heating", "water"):
    suffix = "heizen" if purpose == "heating" else "warmwasser"
    for quantity, section, device in (("electricity", "leistungsaufnahme", "vd"), ("heat", "waermemenge", "vd"), ("aux_heat", "waermemenge", "nhz")):
        SOURCES[f"{purpose}_{quantity}"] = Source(
            f"heatpump.waermepumpe.{section}.{device}_{suffix}_summe", "MWh", "counter",
            "device VD accounting, not whole-system/delivered-room metering" if device == "vd" else "NHZ heat only; electrical input unverified",
            "isg", "waermepumpe")


@dataclass(frozen=True)
class Observation:
    at: float
    value: float
    # Threshold from the historical endpoint-health export, not a hardcoded cadence.
    threshold: float


@dataclass(frozen=True)
class History:
    observations: tuple[Observation, ...] = ()
    invalid: tuple[float, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Edge:
    start: float
    end: float
    left: float
    right: float


def _edges(history, source):
    edges, reasons = [], set(history.reasons)
    invalid = sorted(history.invalid)
    for a, b in zip(history.observations, history.observations[1:]):
        if b.at <= a.at:
            raise ValueError("history: observations must be strictly ordered")
        if b.at - a.at > min(a.threshold, b.threshold):
            reasons.add("collection gap exceeds freshness threshold")
            continue
        index = bisect_right(invalid, a.at)
        if index < len(invalid) and invalid[index] < b.at:
            reasons.add("missing/stale field inside interval")
            continue
        if source.kind == "counter" and b.value < a.value:
            reasons.add("counter reset/replacement; lost energy not reconstructed")
            continue
        edges.append(Edge(a.at, b.at, a.value, b.value))
    return edges, reasons


def _linear(edge, at):
    return edge.left + (edge.right - edge.left) * (at - edge.start) / (edge.end - edge.start)


def _positive_area(a, b, seconds):
    # Integrate the positive part of a linear signed signal, including zero crossing.
    if a >= 0 and b >= 0:
        return (a + b) * seconds / 2
    if a <= 0 and b <= 0:
        return 0.0
    return max(a, b) ** 2 * seconds / (2 * abs(b - a))


def _result(value, unit, status, boundary, coverage=0, largest_gap=0, reasons=(), observed=None, tariff="not_applicable"):
    if value is not None and not math.isfinite(value):
        value = None
        reasons = (*reasons, "nonfinite calculation; unavailable")
    if observed is not None and not math.isfinite(observed):
        observed = None
    return dict(value=value, unit=unit, status=status if value is not None else "unavailable",
                boundary=boundary, covered_seconds=coverage, largest_gap_seconds=largest_gap,
                reasons=sorted(set(reasons)), observed_value=observed, tariff_status=tariff)


def _energy(edges, source, period, reasons):
    start, end = period.start.timestamp(), period.end.timestamp()
    covered, total, cursor, largest, interpolated = 0.0, 0.0, start, 0.0, False
    first, last = None, None
    for edge in edges:
        a, b = max(start, edge.start), min(end, edge.end)
        if b <= a:
            continue
        largest = max(largest, a - cursor)
        cursor = b
        left, right = _linear(edge, a), _linear(edge, b)
        if first is None:
            first = left
        last = right
        covered += b - a
        interpolated |= a != edge.start or b != edge.end
        if source.kind == "counter":
            total += (right - left) * (0.001 if source.unit == "Wh" else 1000)
        elif source.kind != "soc":
            sign = -1 if source.kind in ("load", "charge") else 1
            total += _positive_area(sign * left, sign * right, b - a) / 3600000
    largest = max(largest, end - cursor)
    complete = math.isclose(covered, period.seconds, abs_tol=0.001)
    notes = set(reasons) if not complete else set()
    if not complete:
        notes.add("incomplete period; observed subtotal is not a headline")
    if source.kind == "counter":
        notes.add("absolute gauge differences; stable equipment assumed; unobserved resets cannot be proven absent")
        status = "estimated" if interpolated else "device-reported"
        if interpolated:
            notes.add("linear boundary allocation within fresh observation intervals")
    else:
        status = "estimated"
        notes.add("linear SOC endpoint interpolation; not a power integral" if source.kind == "soc" else
                  "piecewise-linear power integration; nominal 60 s exports, actual observation intervals; no gap bridging")
    if source.kind == "soc":
        total = last - first if complete else None
    return _result(total if complete else None, "percent" if source.kind == "soc" else "kWh", status,
                   source.boundary, covered, largest, notes, total)


def _combine(inputs, operation, unit, boundary, *, tariff="not_applicable", allow_negative=False):
    values = [r["value"] for r in inputs]
    reasons = {note for r in inputs for note in r["reasons"]}
    value = None
    if all(v is not None for v in values):
        try:
            value = operation(*values)
        except ZeroDivisionError:
            reasons.add("zero denominator")
        if value is not None and (not math.isfinite(value) or (value < 0 and not allow_negative)):
            value = None
            reasons.add("inconsistent energy balance; not clamped")
    else:
        reasons.add("required matching-period input unavailable")
    status = "estimated" if tariff != "not_applicable" or any(r['status'] == 'estimated' for r in inputs) else "calculated"
    return _result(value, unit, status, boundary,
                   min(r["covered_seconds"] for r in inputs), max(r["largest_gap_seconds"] for r in inputs),
                   reasons, tariff=tariff)


def _days(period, installation):
    start, end = period.start.timestamp(), period.end.timestamp()
    day = installation.local_date(period.start)
    while start < end:
        tomorrow = day + timedelta(days=1)
        boundary = datetime.combine(tomorrow, time.min, installation.timezone).timestamp()
        stop = min(end, boundary)
        yield Period(datetime.fromtimestamp(start, timezone.utc), datetime.fromtimestamp(stop, timezone.utc))
        start, day = stop, tomorrow


def calculate_period(histories, installation, period):
    """Return report v1 from query histories. No prices/configuration cached.

    Conservative policy: every required interval must be covered; power remains
    estimated, counter boundary allocation is linear only within fresh intervals.
    """
    prepared = {key: _edges(histories.get(key, History(reasons=("source/legacy freshness unavailable",))), source)
                for key, source in SOURCES.items()}
    def energy(window):
        return {key: _energy(prepared[key][0], source, window, prepared[key][1]) for key, source in SOURCES.items()}
    metrics = energy(period)
    inventory = metrics.pop("soc")
    inventory.update(unit="kWh", boundary=SOURCES["soc"].boundary,
                     reasons=inventory['reasons'] + ["SOC inventory estimate, not production; unknown conversion/storage losses"])
    for field in ("value", "observed_value"):
        if inventory[field] is not None:
            inventory[field] *= float(installation.usable_battery_kwh) / 100
    metrics["battery_inventory_change"] = inventory
    metrics["self_sufficiency"] = _combine([metrics["household"], metrics["grid_import"]],
        lambda load, grid: (load - grid) / load * 100, "%", "non-grid site supply, including pre-period battery inventory; grid charging assumed unused")
    metrics["solar_self_consumption"] = _result(None, "%", "unavailable", "DC production versus AC export",
        reasons=("unsupported AC/DC loss boundary; retained DC minus AC export is not direct household use",
                 "battery inventory change is shown separately; grid charging assumed unused"))
    metrics["specific_yield"] = _combine([metrics["pv"]], lambda pv: pv / float(installation.panel_kwp),
        "kWh/kWp", "estimated DC generator yield; not weather-normalized efficiency")
    for quantity in ("electricity", "heat", "aux_heat"):
        metrics["heatpump_" + quantity] = _combine([metrics["heating_" + quantity], metrics["water_" + quantity]],
            lambda a, b: a + b, "kWh", SOURCES["heating_" + quantity].boundary)
    for purpose in ("heating", "water", "heatpump"):
        metrics[purpose + "_ratio"] = _combine([metrics[purpose + "_heat"], metrics[purpose + "_electricity"]],
            lambda heat, electricity: heat / electricity, "kWh/kWh", "matching-period device VD ratio; excludes NHZ, not COP/JAZ/whole system")

    daily, priced = [], {}
    for window in _days(period, installation):
        day_energy = energy(window)
        day_money = {}
        for key, input_keys, kind, operation in (
            ("import_cost", ("grid_import",), "import", lambda a: a),
            ("export_revenue", ("grid_export",), "export", lambda a: a),
            ("avoided_cost", ("household", "grid_import"), "import", lambda a, b: a - b),
            ("heating_reference_cost", ("heating_electricity",), "import", lambda a: a),
            ("water_reference_cost", ("water_electricity",), "import", lambda a: a),
        ):
            inputs = [day_energy[k] for k in input_keys]
            lookup = None
            try:
                lookup = getattr(installation, kind + "_price")(window.start)
            except PriceUnavailable:
                pass
            result = _combine(inputs, operation, installation.currency,
                "variable tariff estimate; fixed charges/restricted credits excluded; unused grid charging assumed",
                tariff=lookup.status if lookup else "unavailable")
            if lookup is None:
                result.update(value=None, status="unavailable", reasons=result["reasons"] + ["price unavailable; no zero fallback"])
            elif result["value"] is not None:
                result["value"] = float(Decimal(str(result["value"])) * lookup.gross_per_kwh)
                result["reasons"].append("linear temporal allocation within fresh counter intervals; not billing grade")
                if lookup.carried_forward:
                    result["reasons"].append("expired price carried forward provisionally")
            if "reference" in key:
                result["boundary"] = "VD electricity at grid tariff; not actual attributed expenditure"
            day_money[key] = result
            priced.setdefault(key, []).append(result)
        daily.append(dict(start=window.start.isoformat(), end=window.end.isoformat(), seconds=window.seconds,
                          metrics={**day_energy, **day_money}))
    for key, parts in priced.items():
        tariff = "unavailable" if any(p["tariff_status"] == "unavailable" for p in parts) else (
            "provisional" if any(p["tariff_status"] == "provisional" for p in parts) else "confirmed")
        metrics[key] = _combine(parts, lambda *values: sum(values), installation.currency, parts[0]["boundary"], tariff=tariff)
        metrics[key]["covered_seconds"] = sum(p["covered_seconds"] for p in parts)
    for key, left, right, operation in (
        ("solar_battery_benefit", "avoided_cost", "export_revenue", lambda a, b: a + b),
        ("net_grid_cost", "import_cost", "export_revenue", lambda a, b: a - b),
        ("heatpump_reference_cost", "heating_reference_cost", "water_reference_cost", lambda a, b: a + b),
    ):
        inputs = [metrics[left], metrics[right]]
        tariff = "unavailable" if any(r["tariff_status"] == "unavailable" for r in inputs) else (
            "provisional" if any(r["tariff_status"] == "provisional" for r in inputs) else "confirmed")
        # Net cash can legitimately be negative (export revenue exceeds imports).
        result = _combine(inputs, operation,
                          installation.currency, "variable grid cost minus revenue; NOT solar benefit" if key == "net_grid_cost" else (
                              "VD grid-tariff reference, not attributed spending" if key == "heatpump_reference_cost" else "avoided purchases plus export revenue; not investment profit"),
                          tariff=tariff, allow_negative=key == "net_grid_cost")
        metrics[key] = result
    return dict(version=1, start=period.start.isoformat(), end=period.end.isoformat(), seconds=period.seconds,
                timezone=str(installation.timezone), metrics=metrics, daily=daily,
                limitations=["Grid charging assumed unused, not verified", "Stable equipment identity and units required; no hidden-reset reconstruction",
                             "No billing reconciliation, fixed charges, restricted credits, instalments or balances", "Legacy history without per-field freshness is unavailable"])
