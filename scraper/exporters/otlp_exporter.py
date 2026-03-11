"""OpenTelemetry OTLP metrics exporter for ISG and Fronius data."""

import logging

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from scraper.metrics.definitions import get_otel_unit, is_counter_metric
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
        metrics.set_meter_provider(self.meter_provider)

        self.meter = metrics.get_meter("isg_heatpump", "1.0.0")
        self.fronius_meter = metrics.get_meter("fronius_solar", "1.0.0")

        # Cache for created instruments to avoid re-creating them
        self._gauges: dict[str, metrics.ObservableGauge] = {}
        self._gauge_values: dict[str, float] = {}
        self._counters: dict[str, metrics.ObservableUpDownCounter] = {}
        self._counter_values: dict[str, float] = {}

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

        We use ObservableGauge even for counters because the ISG reports
        absolute cumulative values (not deltas). Prometheus will handle
        the counter semantics on its side.
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
        exported = 0

        for value in values:
            if value.numeric_value is None:
                logger.debug(
                    "Skipping non-numeric value: %s/%s = '%s'",
                    value.section,
                    value.key,
                    value.raw_value,
                )
                continue

            metric_name = build_metric_name(page_name, value.section, value.key)
            description = f"{value.section} - {value.key}"

            if is_counter_metric(metric_name):
                self._get_or_create_counter(metric_name, value.unit, description)
                self._counter_values[metric_name] = value.numeric_value
            else:
                self._get_or_create_gauge(metric_name, value.unit, description)
                self._gauge_values[metric_name] = value.numeric_value

            exported += 1

        # Also export calculated COP metrics
        exported += self._export_calculated_metrics(page_name, values)

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
        exported = 0

        for fm in fronius_metrics:
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
        """Calculate and export derived metrics like COP.

        COP = Wärmemenge / Leistungsaufnahme (for matching periods)
        """
        if page_name != "waermepumpe":
            return 0

        # Build lookup: section -> key -> numeric_value
        lookup: dict[str, dict[str, float]] = {}
        for v in values:
            if v.numeric_value is not None:
                if v.section not in lookup:
                    lookup[v.section] = {}
                lookup[v.section][v.key] = v.numeric_value

        exported = 0
        waermemenge = lookup.get("WÄRMEMENGE", {})
        leistungsaufnahme = lookup.get("LEISTUNGSAUFNAHME", {})

        # Calculate COP for each matching pair
        cop_pairs = [
            ("VD HEIZEN TAG", "cop_heizen_tag", "COP Heating (daily)"),
            ("VD HEIZEN SUMME", "cop_heizen_gesamt", "COP Heating (total)"),
            ("VD WARMWASSER TAG", "cop_warmwasser_tag", "COP Hot Water (daily)"),
            ("VD WARMWASSER SUMME", "cop_warmwasser_gesamt", "COP Hot Water (total)"),
        ]

        for key, metric_suffix, description in cop_pairs:
            heat = waermemenge.get(key)
            power = leistungsaufnahme.get(key)
            if heat is not None and power is not None and power > 0:
                # Ensure same units (both could be kWh or MWh but ratio is the same)
                cop = heat / power
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

        # Calculate instantaneous electrical power (V * A)
        strom = prozessdaten.get("STROM INVERTER")
        spannung = prozessdaten.get("SPANNUNG INVERTER")
        if strom is not None and spannung is not None:
            power_w = strom * spannung
            metric_name = "heatpump.calculated.inverter_leistung_berechnet"
            self._get_or_create_gauge(
                metric_name, "kW", "Calculated inverter power (V * A)"
            )
            self._gauge_values[metric_name] = round(power_w / 1000, 2)
            exported += 1

        return exported

    def shutdown(self):
        """Flush and shut down the meter provider."""
        self.meter_provider.shutdown()
