"""OpenTelemetry OTLP metrics exporter for ISG and Fronius data."""

import logging
import math
import time

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from scraper.health import CollectionHealth
from scraper.metrics.definitions import energy_in_kwh, get_otel_unit, is_counter_metric
from scraper.parsers.isg_parser import ParsedValue, build_metric_name

logger = logging.getLogger(__name__)


class OTLPExporter:
    """Exports ISG and Fronius metrics to an OpenTelemetry Collector via OTLP/gRPC."""

    def __init__(self, endpoint: str, export_interval_seconds: int = 60):
        """Initialize the OTLP exporter.

        Args:
            endpoint: OTLP gRPC endpoint, e.g. 'http://lgtm:4317'
            export_interval_seconds: How often to push metrics to the collector
        """
        resource = Resource.create(
            {
                "service.name": "smarthome-monitor",
                "service.version": "1.0.0",
                "deployment.environment": "homelab",
            }
        )

        exporter = OTLPMetricExporter(
            endpoint=endpoint,
            insecure=True,  # No TLS for local network
        )

        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=export_interval_seconds * 1000,
        )

        self.meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[reader],
        )
        self.meter = self.meter_provider.get_meter("isg_heatpump", "1.0.0")
        self.fronius_meter = self.meter_provider.get_meter("fronius_solar", "1.0.0")

        self.health = CollectionHealth()
        self.meter.create_observable_gauge(
            "smarthome_measurement_contract_version",
            callbacks=[lambda options: [metrics.Observation(2)]],
            description="Semantic export contract version; not a source-health signal",
        )
        for name, index in (
            ("smarthome_collection_last_success_seconds", 0),
            ("smarthome_collection_stale_after_seconds", 1),
        ):
            def observe_health(options, index=index):
                for (collector, source), values in self.health.snapshot().items():
                    yield metrics.Observation(
                        values[index], {"collector": collector, "source": source}
                    )

            self.meter.create_observable_gauge(name, callbacks=[observe_health])

        # Cache for created instruments to avoid re-creating them
        self._gauges: dict[str, metrics.ObservableGauge] = {}
        self._gauge_values: dict[str, float] = {}
        self._counters: dict[str, metrics.ObservableGauge] = {}
        self._counter_values: dict[str, float] = {}
        self._measurement_last_success: dict[str, float] = {}
        self._instrument_units: dict[str, str] = {}

        def observe_measurements(options):
            # Snapshot: collection and periodic callbacks run on different threads.
            for name, timestamp in self._measurement_last_success.copy().items():
                yield metrics.Observation(timestamp, {"metric": name})

        self.meter.create_observable_gauge(
            "smarthome_measurement_last_success_seconds",
            callbacks=[observe_measurements],
            description="Collector observation time per valid measurement, not export time",
        )

        def observe_presence(options):
            for name in self._measurement_last_success.copy():
                present = name in self._gauge_values or name in self._counter_values
                yield metrics.Observation(int(present), {"metric": name})

        self.meter.create_observable_gauge(
            "smarthome_measurement_present",
            callbacks=[observe_presence],
            description="Valid field in latest processed source result; also check observation age",
        )

    def _clear_values(self, prefixes: tuple[str, ...]) -> None:
        """A new source result replaces its cache; absent/invalid fields are not zero."""
        for cache in (self._gauge_values, self._counter_values):
            for name in list(cache):
                if name.startswith(prefixes):
                    cache.pop(name, None)

    def _accept_unit(self, name: str, unit: str) -> bool:
        # OTel instrument units are immutable. Do not attach a new numeric scale
        # to an existing series after a firmware/display-unit change.
        return self._instrument_units.setdefault(name, unit) == unit

    def _get_or_create_gauge(
        self, name: str, unit: str, description: str
    ) -> None:
        """Create an observable gauge if it doesn't exist yet."""
        if name not in self._gauges:
            otel_unit = get_otel_unit(unit)

            def callback(options, name=name):
                value = self._gauge_values.get(name)
                if value is not None:
                    yield metrics.Observation(value)

            self._gauges[name] = self.meter.create_observable_gauge(
                name=name,
                callbacks=[callback],
                unit=otel_unit,
                description=description,
            )

    def _get_or_create_counter(
        self, name: str, unit: str, description: str
    ) -> None:
        """Create an observable gauge for counter-like values.

        Keep historical gauge series and names intact. These are device absolute
        readings, NOT Prometheus counters; period queries must explicitly handle
        resets and coverage (see docs/measurement-contracts.md).
        """
        if name not in self._counters:
            otel_unit = get_otel_unit(unit)

            def callback(options, name=name):
                value = self._counter_values.get(name)
                if value is not None:
                    yield metrics.Observation(value)

            self._counters[name] = self.meter.create_observable_gauge(
                name=name,
                callbacks=[callback],
                unit=otel_unit,
                description=description,
            )

    def export_values(
        self, page_name: str, values: list[ParsedValue]
    ) -> int:
        """Export parsed ISG values as OTel metrics.

        Args:
            page_name: ISG page identifier (e.g. 'waermepumpe')
            values: List of parsed values from the page

        Returns:
            Number of metrics exported
        """
        prefixes = (f"heatpump.{page_name}.",)
        if page_name == "waermepumpe":
            prefixes += ("heatpump.calculated.",)
        self._clear_values(prefixes)
        observed_at = time.time()
        exported = 0

        for value in values:
            number, unit = value.numeric_value, value.unit
            if number is None or not math.isfinite(number):
                continue

            metric_name = build_metric_name(page_name, value.section, value.key)
            description = f"{value.section} - {value.key}"
            if page_name == "waermepumpe" and value.section in ("WÄRMEMENGE", "LEISTUNGSAUFNAHME"):
                number = energy_in_kwh(number, unit)
                if number is None or value.is_boolean:
                    continue
                # Preserve the established daily kWh / cumulative MWh series.
                unit = "MWh" if value.key.endswith("SUMME") else "kWh"
                if unit == "MWh":
                    number /= 1000
            if not self._accept_unit(metric_name, unit):
                continue

            if is_counter_metric(metric_name):
                self._get_or_create_counter(metric_name, unit, description)
                self._counter_values[metric_name] = number
            else:
                self._get_or_create_gauge(metric_name, unit, description)
                self._gauge_values[metric_name] = number
            self._measurement_last_success[metric_name] = observed_at
            exported += 1

        # Also export calculated COP metrics
        exported += self._export_calculated_metrics(page_name, values)
        if page_name == "waermepumpe":
            for name in self._gauge_values:
                if name.startswith("heatpump.calculated."):
                    self._measurement_last_success[name] = observed_at

        logger.info(
            "Exported %d metrics for page '%s'", exported, page_name
        )
        return exported

    def export_fronius_values(self, fronius_metrics) -> int:
        """Export Fronius metrics as OTel metrics.

        Args:
            fronius_metrics: List of FroniusMetric dataclass instances

        Returns:
            Number of metrics exported
        """
        self._clear_values(("fronius.",))
        observed_at = time.time()
        exported = 0

        for fm in fronius_metrics:
            if not math.isfinite(fm.value) or not self._accept_unit(fm.name, fm.unit):
                continue
            if is_counter_metric(fm.name):
                self._get_or_create_fronius_counter(
                    fm.name, fm.unit, fm.description
                )
                self._counter_values[fm.name] = fm.value
            else:
                self._get_or_create_fronius_gauge(
                    fm.name, fm.unit, fm.description
                )
                self._gauge_values[fm.name] = fm.value

            self._measurement_last_success[fm.name] = observed_at
            exported += 1

        logger.info("Exported %d Fronius metrics", exported)
        return exported

    def _get_or_create_fronius_gauge(
        self, name: str, unit: str, description: str
    ) -> None:
        """Create an observable gauge on the Fronius meter if it doesn't exist."""
        if name not in self._gauges:
            otel_unit = get_otel_unit(unit)

            def callback(options, name=name):
                value = self._gauge_values.get(name)
                if value is not None:
                    yield metrics.Observation(value)

            self._gauges[name] = self.fronius_meter.create_observable_gauge(
                name=name,
                callbacks=[callback],
                unit=otel_unit,
                description=description,
            )

    def _get_or_create_fronius_counter(
        self, name: str, unit: str, description: str
    ) -> None:
        """Create an observable gauge for counter-like Fronius values."""
        if name not in self._counters:
            otel_unit = get_otel_unit(unit)

            def callback(options, name=name):
                value = self._counter_values.get(name)
                if value is not None:
                    yield metrics.Observation(value)

            self._counters[name] = self.fronius_meter.create_observable_gauge(
                name=name,
                callbacks=[callback],
                unit=otel_unit,
                description=description,
            )

    def _export_calculated_metrics(
        self, page_name: str, values: list[ParsedValue]
    ) -> int:
        """Device VD energy ratios, not whole-system COP or annual efficiency."""
        if page_name != "waermepumpe":
            return 0

        # Build lookup: section -> key -> numeric_value
        lookup: dict[str, dict[str, float]] = {}
        for v in values:
            name = build_metric_name(page_name, v.section, v.key)
            if name not in self._gauge_values and name not in self._counter_values:
                continue
            number = v.numeric_value
            if number is None or not math.isfinite(number) or v.is_boolean:
                continue
            if v.section in ("WÄRMEMENGE", "LEISTUNGSAUFNAHME"):
                number = energy_in_kwh(number, v.unit)
            elif v.section == "PROZESSDATEN" and v.unit != "°C":
                continue
            if number is not None:
                lookup.setdefault(v.section, {})[v.key] = number

        exported = 0
        waermemenge = lookup.get("WÄRMEMENGE", {})
        leistungsaufnahme = lookup.get("LEISTUNGSAUFNAHME", {})

        # Calculate COP for each matching pair
        cop_pairs = [
            ("VD HEIZEN TAG", "cop_heizen_tag", "Device VD heating energy ratio since day reset; excludes NHZ"),
            ("VD HEIZEN SUMME", "cop_heizen_gesamt", "Device VD heating lifetime energy ratio; not annual, excludes NHZ"),
            ("VD WARMWASSER TAG", "cop_warmwasser_tag", "Device VD hot-water energy ratio since day reset; excludes NHZ"),
            ("VD WARMWASSER SUMME", "cop_warmwasser_gesamt", "Device VD hot-water lifetime energy ratio; not annual, excludes NHZ"),
        ]

        for key, metric_suffix, description in cop_pairs:
            heat = waermemenge.get(key)
            power = leistungsaufnahme.get(key)
            if heat is not None and power is not None and power > 0:
                # Both inputs were independently converted to kWh.
                cop = heat / power
                if not math.isfinite(cop):
                    continue
                metric_name = f"heatpump.calculated.{metric_suffix}"
                self._get_or_create_gauge(metric_name, "", description)
                self._gauge_values[metric_name] = round(cop, 2)
                exported += 1

        # Calculate Vorlauf/Rücklauf spread
        prozessdaten = lookup.get("PROZESSDATEN", {})
        vorlauf = prozessdaten.get("VORLAUFTEMPERATUR")
        ruecklauf = prozessdaten.get("RÜCKLAUFTEMPERATUR")
        if vorlauf is not None and ruecklauf is not None:
            spread = vorlauf - ruecklauf
            metric_name = "heatpump.calculated.vorlauf_ruecklauf_spread"
            self._get_or_create_gauge(
                metric_name, "°C", "Temperature spread (Vorlauf - Rücklauf)"
            )
            self._gauge_values[metric_name] = round(spread, 1)
            exported += 1

        # V*A is not verified whole-system real power (phase count, power factor,
        # AC/DC boundary and auxiliaries are unknown). Retire the calculated claim.

        return exported

    def shutdown(self):
        """Flush and shut down the meter provider."""
        self.meter_provider.shutdown()
