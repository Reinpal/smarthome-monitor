"""Private installation inputs and date-effective tariffs (no I/O at import time).

Money uses Decimal in configured currency, never cents. Date ranges are local
calendar dates, start inclusive/end exclusive. This is lookup, not billing.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_CONFIG = Path("private/installation.json")


class ConfigurationError(ValueError):
    """Safe to display: contains field paths, never supplied values."""


class PriceUnavailable(LookupError):
    """There is no known price on or before the requested local date."""


def _fail(path, reason):
    raise ConfigurationError(f"{path}: {reason}")


def _object(value, path, required, optional=()):
    if not isinstance(value, dict):
        _fail(path, "expected object")
    if set(value) - set(required) - set(optional):
        _fail(path, "unknown field (remove private identifiers and unsupported inputs)")
    for key in required:
        if key not in value:
            _fail(f"{path}.{key}", "required")
    return value


def _number(value, path, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        _fail(path, "expected finite number")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        _fail(path, "expected finite number")
    if not result.is_finite() or result < 0 or (positive and result == 0):
        _fail(path, "must be finite and positive" if positive else "must be finite and nonnegative")
    return result


def _choice(value, path, choices):
    if value not in choices:
        _fail(path, "expected one of " + ", ".join(choices))
    return value


def _date(value, path):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail(path, "expected ISO date YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(path, "invalid calendar date")


def _list(value, path, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        _fail(path, "expected nonempty list" if nonempty else "expected list")
    return value


def _vat(value, path):
    result = _number(value, path)
    if result > 1:
        _fail(path, "expected fraction between 0 and 1")
    return result


def _money(value, path, vat_rate, extra=()):
    _object(value, path, ("amount", "vat_basis", *extra))
    amount = _number(value["amount"], path + ".amount")
    basis = _choice(value["vat_basis"], path + ".vat_basis", ("net", "gross"))
    return amount * (1 + vat_rate) if basis == "net" else amount


@dataclass(frozen=True)
class FixedCharge:
    period: str  # month or year; never part of avoided purchase savings
    gross_amount: Decimal


@dataclass(frozen=True)
class ImportTariff:
    start: date
    end: date
    status: str
    components_gross_per_kwh: tuple[tuple[str, Decimal], ...]
    universal_discount_gross_per_kwh: Decimal
    restricted_credits_gross: tuple[Decimal, ...]
    fixed_charges: tuple[FixedCharge, ...]

    @property
    def gross_per_kwh(self):
        return sum((amount for _, amount in self.components_gross_per_kwh), Decimal(0)) - self.universal_discount_gross_per_kwh


@dataclass(frozen=True)
class ExportTariff:
    start: date
    end: date
    status: str
    gross_per_kwh: Decimal


@dataclass(frozen=True)
class PriceLookup:
    local_date: date
    currency: str
    gross_per_kwh: Decimal
    status: str  # confirmed/provisional; independent of telemetry coverage
    source_status: str
    effective_from: date
    effective_until: date  # original exclusive expiry, even on carry-forward
    carried_forward: bool
    tariff: ImportTariff | ExportTariff


@dataclass(frozen=True)
class Installation:
    panel_kwp: Decimal
    usable_battery_kwh: Decimal
    area_m2: Decimal | None
    commissioning_date: date | None
    currency: str
    timezone: ZoneInfo
    import_tariffs: tuple[ImportTariff, ...]
    export_tariffs: tuple[ExportTariff, ...]

    def local_date(self, when: date | datetime) -> date:
        if isinstance(when, datetime):
            if when.tzinfo is None or when.utcoffset() is None:
                raise ValueError("tariff lookup requires an aware datetime or local date")
            return when.astimezone(self.timezone).date()
        if not isinstance(when, date):
            raise TypeError("tariff lookup requires an aware datetime or local date")
        return when

    def _lookup(self, tariffs, when, carry_forward):
        day = self.local_date(when)
        candidates = [tariff for tariff in tariffs if tariff.start <= day]
        if not candidates:
            raise PriceUnavailable("no known price before requested local date")
        tariff = candidates[-1]
        carried = day >= tariff.end
        if carried and not carry_forward:
            raise PriceUnavailable("price expired; carry-forward disabled")
        return PriceLookup(day, self.currency, tariff.gross_per_kwh,
                           "provisional" if carried else tariff.status, tariff.status,
                           tariff.start, tariff.end, carried, tariff)

    def import_price(self, when: date | datetime, *, carry_forward=True) -> PriceLookup:
        return self._lookup(self.import_tariffs, when, carry_forward)

    def export_price(self, when: date | datetime, *, carry_forward=True) -> PriceLookup:
        return self._lookup(self.export_tariffs, when, carry_forward)


def parse_installation(value) -> Installation:
    """Validate a decoded JSON object. Unknown fields are rejected for privacy."""
    _object(value, "config", ("version", "installation", "import_tariffs", "export_tariffs"))
    if type(value["version"]) is not int or value["version"] != 1:
        _fail("config.version", "expected version 1")
    info = _object(value["installation"], "installation",
                   ("panel_kwp", "usable_battery_kwh", "currency", "timezone"),
                   ("area_m2", "commissioning_date"))
    panel = _number(info["panel_kwp"], "installation.panel_kwp", positive=True)
    battery = _number(info["usable_battery_kwh"], "installation.usable_battery_kwh")
    area = _number(info["area_m2"], "installation.area_m2", positive=True) if "area_m2" in info else None
    commissioning = _date(info["commissioning_date"], "installation.commissioning_date") if "commissioning_date" in info else None
    currency = info["currency"]
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        _fail("installation.currency", "expected three-letter uppercase currency code")
    try:
        if not isinstance(info["timezone"], str):
            raise ValueError
        timezone = ZoneInfo(info["timezone"])
    except (ValueError, ZoneInfoNotFoundError):
        _fail("installation.timezone", "expected installed IANA timezone")

    imports = []
    for i, raw in enumerate(_list(value["import_tariffs"], "import_tariffs", nonempty=True)):
        path = f"import_tariffs[{i}]"
        _object(raw, path, ("from", "until", "status", "vat_rate", "components", "discounts", "fixed_charges"))
        start, end = _date(raw["from"], path + ".from"), _date(raw["until"], path + ".until")
        if end <= start:
            _fail(path, "until must be after from (exclusive end)")
        status = _choice(raw["status"], path + ".status", ("confirmed", "provisional"))
        vat = _vat(raw["vat_rate"], path + ".vat_rate")
        components = _object(raw["components"], path + ".components", ("energy", "network", "levies"))
        amounts = tuple((name, _money(components[name], path + ".components." + name, vat)) for name in ("energy", "network", "levies"))
        discount = Decimal(0)
        credits = []
        for j, item in enumerate(_list(raw["discounts"], path + ".discounts")):
            field = f"{path}.discounts[{j}]"
            amount = _money(item, field, vat, ("scope",))
            scope = _choice(item["scope"], field + ".scope", ("all_imports", "restricted_credit"))
            if scope == "all_imports":
                discount += amount
            else:
                credits.append(amount)
        if discount > sum(amount for _, amount in amounts):
            _fail(path + ".discounts", "universal discounts exceed variable components")
        fixed = []
        for j, item in enumerate(_list(raw["fixed_charges"], path + ".fixed_charges")):
            field = f"{path}.fixed_charges[{j}]"
            amount = _money(item, field, vat, ("period",))
            period = _choice(item["period"], field + ".period", ("month", "year"))
            fixed.append(FixedCharge(period, amount))
        imports.append(ImportTariff(start, end, status, amounts, discount, tuple(credits), tuple(fixed)))
    imports.sort(key=lambda tariff: tariff.start)
    if any(left.end > right.start for left, right in zip(imports, imports[1:])):
        _fail("import_tariffs", "overlapping periods")

    exports = []
    for i, raw in enumerate(_list(value["export_tariffs"], "export_tariffs", nonempty=True)):
        path = f"export_tariffs[{i}]"
        _object(raw, path, ("month", "status", "vat_rate", "price"))
        if not isinstance(raw["month"], str) or not re.fullmatch(r"\d{4}-\d{2}", raw["month"]):
            _fail(path + ".month", "expected YYYY-MM")
        start = _date(raw["month"] + "-01", path + ".month")
        try:
            end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        except ValueError:
            _fail(path + ".month", "month outside supported range")
        status = _choice(raw["status"], path + ".status", ("confirmed", "provisional"))
        vat = _vat(raw["vat_rate"], path + ".vat_rate")
        exports.append(ExportTariff(start, end, status, _money(raw["price"], path + ".price", vat)))
    exports.sort(key=lambda tariff: tariff.start)
    if any(left.start == right.start for left, right in zip(exports, exports[1:])):
        _fail("export_tariffs", "duplicate monthly prices")
    return Installation(panel, battery, area, commissioning, currency, timezone, tuple(imports), tuple(exports))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("config", "duplicate JSON field")
        result[key] = value
    return result


def load_installation(path=DEFAULT_CONFIG) -> Installation:
    """Reload for corrections; no global cache and no zero-price fallback."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal,
                           object_pairs_hook=_unique_object)
    except OSError:
        raise ConfigurationError("config: cannot read private configuration file") from None
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, ConfigurationError):
            raise
        raise ConfigurationError("config: invalid JSON encoding or syntax") from None
    return parse_installation(value)
