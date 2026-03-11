"""Fronius Solar API data collector.

Polls the Fronius inverter's local REST API and extracts metrics
for PV production, battery status, grid interaction, and inverter health.
"""

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)


# Battery mode string -> numeric mapping
BATTERY_MODE_MAP = {
    "normal": 0,
    "charge": 1,
    "discharge": 2,
    "nearly depleted": 3,
    "standby": 4,
}


@dataclass
class FroniusMetric:
    """A single metric extracted from the Fronius API."""

    name: str
    value: float
    unit: str
    description: str


class FroniusCollector:
    """Collects metrics from the Fronius Solar API v1.

    Polls four endpoints:
    - GetPowerFlowRealtimeData (power flow overview)
    - GetStorageRealtimeData (battery details)
    - GetMeterRealtimeData (grid meter per-phase data)
    - GetInverterRealtimeData (inverter status and DC data)
    """

    def __init__(self, base_url: str):
        """Initialize the Fronius collector.

        Args:
            base_url: Fronius inverter base URL, e.g. 'http://192.168.1.200'
        """
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
        })

    def collect_all(self, endpoints: dict[str, str]) -> list[FroniusMetric]:
        """Run a full collection cycle across all endpoints.

        Args:
            endpoints: Dict mapping endpoint name -> URL path

        Returns:
            List of all extracted metrics
        """
        all_metrics: list[FroniusMetric] = []

        for name, path in endpoints.items():
            url = f"{self.base_url}{path}"
            try:
                data = self._fetch_json(url)
                if data is None:
                    continue

                parser = getattr(self, f"_parse_{name}", None)
                if parser:
                    metrics = parser(data)
                    all_metrics.extend(metrics)
                    logger.debug(
                        "Collected %d metrics from '%s'", len(metrics), name
                    )
                else:
                    logger.warning("No parser for endpoint '%s'", name)

            except Exception:
                logger.exception("Error collecting from Fronius endpoint '%s'", name)

        # Add calculated/derived metrics
        all_metrics.extend(self._calculate_derived(all_metrics))

        return all_metrics

    def _fetch_json(self, url: str) -> dict | None:
        """Fetch JSON data from a Fronius API endpoint.

        Args:
            url: Full URL to fetch

        Returns:
            Parsed JSON dict, or None on error
        """
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            # Check Fronius API status
            status = data.get("Head", {}).get("Status", {})
            if status.get("Code", -1) != 0:
                logger.warning(
                    "Fronius API error for %s: Code=%s Reason=%s",
                    url,
                    status.get("Code"),
                    status.get("Reason", "unknown"),
                )
                return None

            return data

        except requests.exceptions.Timeout:
            logger.warning("Timeout fetching %s", url)
            return None
        except requests.exceptions.ConnectionError:
            logger.warning("Connection error fetching %s", url)
            return None
        except requests.exceptions.RequestException:
            logger.exception("Request error fetching %s", url)
            return None

    def _parse_powerflow(self, data: dict) -> list[FroniusMetric]:
        """Parse GetPowerFlowRealtimeData response.

        Extracts power flow data: PV production, grid, load, battery,
        energy totals, autonomy, and self-consumption.
        """
        metrics: list[FroniusMetric] = []
        body = data.get("Body", {}).get("Data", {})

        # Site-level data - handle both possible locations
        # Firmware variants: Body.Data.Site (common) or Body.Data.Inverters.Site
        site = body.get("Site") or body.get("Inverters", {}).get("Site", {})

        site_metrics = [
            ("p_pv", "P_PV", "W", "Current PV production"),
            ("p_grid", "P_Grid", "W", "Grid power (+ import, - export)"),
            ("p_load", "P_Load", "W", "Current load/consumption"),
            ("p_akku", "P_Akku", "W", "Battery power (+ charge, - discharge)"),
            ("e_day", "E_Day", "Wh", "Energy produced today"),
            ("e_year", "E_Year", "Wh", "Energy produced this year"),
            ("e_total", "E_Total", "Wh", "Total energy produced"),
            ("rel_autonomy", "rel_Autonomy", "%", "Autonomy percentage"),
            ("rel_self_consumption", "rel_SelfConsumption", "%", "Self-consumption percentage"),
        ]

        for metric_name, api_key, unit, description in site_metrics:
            value = site.get(api_key)
            if value is not None:
                metrics.append(FroniusMetric(
                    name=f"fronius.powerflow.{metric_name}",
                    value=float(value),
                    unit=unit,
                    description=description,
                ))

        # Inverter-level data (inverter "1") - key is uppercase "Inverters"
        inverters = body.get("Inverters", body.get("inverters", {}))
        inv1 = inverters.get("1", {})

        if inv1:
            soc = inv1.get("SOC")
            if soc is not None:
                metrics.append(FroniusMetric(
                    name="fronius.powerflow.soc",
                    value=float(soc),
                    unit="%",
                    description="Battery state of charge",
                ))

            battery_mode = inv1.get("Battery_Mode", "").lower()
            mode_value = BATTERY_MODE_MAP.get(battery_mode, -1)
            metrics.append(FroniusMetric(
                name="fronius.powerflow.battery_mode",
                value=float(mode_value),
                unit="",
                description="Battery mode (0=normal, 1=charge, 2=discharge)",
            ))

        return metrics

    def _parse_storage(self, data: dict) -> list[FroniusMetric]:
        """Parse GetStorageRealtimeData response.

        Extracts battery details: SOC, voltage, current, temperature, capacity.
        """
        metrics: list[FroniusMetric] = []
        body = data.get("Body", {}).get("Data", {})

        # Storage data is keyed by device ID (usually "0")
        for device_id, storage in body.items():
            controller = storage.get("Controller", {})
            if not controller:
                continue

            storage_metrics = [
                ("soc", "StateOfCharge_Relative", "%", "Battery state of charge"),
                ("voltage_dc", "Voltage_DC", "V", "Battery DC voltage"),
                ("current_dc", "Current_DC", "A", "Battery DC current"),
                ("temperature_cell", "Temperature_Cell", "°C", "Battery cell temperature"),
                ("capacity_maximum", "Capacity_Maximum", "Ah", "Maximum battery capacity"),
                ("designed_capacity", "DesignedCapacity", "Ah", "Designed battery capacity"),
                ("status_battery_cell", "Status_BatteryCell", "", "Battery cell status code"),
            ]

            for metric_name, api_key, unit, description in storage_metrics:
                value = controller.get(api_key)
                if value is not None:
                    metrics.append(FroniusMetric(
                        name=f"fronius.storage.{metric_name}",
                        value=float(value),
                        unit=unit,
                        description=description,
                    ))

            # Only process first storage device
            break

        return metrics

    def _parse_meter(self, data: dict) -> list[FroniusMetric]:
        """Parse GetMeterRealtimeData response (Scope=System).

        Extracts grid meter data: per-phase power, voltage, current,
        frequency, energy totals, and power factor.
        """
        metrics: list[FroniusMetric] = []
        body = data.get("Body", {}).get("Data", {})

        # Meter data is keyed by device ID (usually "0")
        for device_id, meter in body.items():
            if not isinstance(meter, dict):
                continue

            meter_metrics = [
                ("power_real_p_sum", "PowerReal_P_Sum", "W", "Total real power at meter"),
                ("power_real_p_phase_1", "PowerReal_P_Phase_1", "W", "Phase 1 real power"),
                ("power_real_p_phase_2", "PowerReal_P_Phase_2", "W", "Phase 2 real power"),
                ("power_real_p_phase_3", "PowerReal_P_Phase_3", "W", "Phase 3 real power"),
                ("voltage_ac_phase_1", "Voltage_AC_Phase_1", "V", "Phase 1 AC voltage"),
                ("voltage_ac_phase_2", "Voltage_AC_Phase_2", "V", "Phase 2 AC voltage"),
                ("voltage_ac_phase_3", "Voltage_AC_Phase_3", "V", "Phase 3 AC voltage"),
                ("current_ac_phase_1", "Current_AC_Phase_1", "A", "Phase 1 AC current"),
                ("current_ac_phase_2", "Current_AC_Phase_2", "A", "Phase 2 AC current"),
                ("current_ac_phase_3", "Current_AC_Phase_3", "A", "Phase 3 AC current"),
                ("current_ac_sum", "Current_AC_Sum", "A", "Total AC current"),
                ("frequency", "Frequency_Phase_Average", "Hz", "Grid frequency"),
                ("energy_real_consumed", "EnergyReal_WAC_Sum_Consumed", "Wh", "Total energy consumed from grid"),
                ("energy_real_produced", "EnergyReal_WAC_Sum_Produced", "Wh", "Total energy fed to grid"),
                ("energy_real_abs_minus", "EnergyReal_WAC_Minus_Absolute", "Wh", "Absolute energy fed to grid"),
                ("energy_real_abs_plus", "EnergyReal_WAC_Plus_Absolute", "Wh", "Absolute energy from grid"),
                ("power_apparent_s_sum", "PowerApparent_S_Sum", "W", "Total apparent power"),
                ("power_factor_sum", "PowerFactor_Sum", "", "Power factor"),
                ("power_reactive_q_sum", "PowerReactive_Q_Sum", "W", "Total reactive power"),
            ]

            for metric_name, api_key, unit, description in meter_metrics:
                value = meter.get(api_key)
                if value is not None:
                    metrics.append(FroniusMetric(
                        name=f"fronius.meter.{metric_name}",
                        value=float(value),
                        unit=unit,
                        description=description,
                    ))

            # Only process first meter
            break

        return metrics

    def _parse_inverter(self, data: dict) -> list[FroniusMetric]:
        """Parse GetInverterRealtimeData response (CommonInverterData).

        Extracts inverter data: AC/DC power, voltages, currents,
        energy totals, and device status.
        """
        metrics: list[FroniusMetric] = []
        body = data.get("Body", {}).get("Data", {})

        # Value fields have {Unit, Value} structure
        value_metrics = [
            ("pac", "PAC", "W", "Inverter AC power output"),
            ("day_energy", "DAY_ENERGY", "Wh", "Today's energy production"),
            ("year_energy", "YEAR_ENERGY", "Wh", "This year's energy production"),
            ("total_energy", "TOTAL_ENERGY", "Wh", "Lifetime energy production"),
            ("uac", "UAC", "V", "Inverter AC voltage"),
            ("iac", "IAC", "A", "Inverter AC current"),
            ("fac", "FAC", "Hz", "Inverter AC frequency"),
            ("udc", "UDC", "V", "DC string 1 voltage"),
            ("udc_2", "UDC_2", "V", "DC string 2 voltage"),
            ("idc", "IDC", "A", "DC string 1 current"),
            ("idc_2", "IDC_2", "A", "DC string 2 current"),
            ("sac", "SAC", "VA", "Inverter apparent power"),
        ]

        for metric_name, api_key, unit, description in value_metrics:
            field = body.get(api_key)
            if isinstance(field, dict):
                value = field.get("Value")
                if value is not None:
                    metrics.append(FroniusMetric(
                        name=f"fronius.inverter.{metric_name}",
                        value=float(value),
                        unit=unit,
                        description=description,
                    ))

        # Device status (flat numeric fields)
        device_status = body.get("DeviceStatus", {})
        if device_status:
            status_code = device_status.get("StatusCode")
            if status_code is not None:
                metrics.append(FroniusMetric(
                    name="fronius.inverter.status_code",
                    value=float(status_code),
                    unit="",
                    description="Inverter status code",
                ))

            error_code = device_status.get("ErrorCode")
            if error_code is not None:
                metrics.append(FroniusMetric(
                    name="fronius.inverter.error_code",
                    value=float(error_code),
                    unit="",
                    description="Inverter error code",
                ))

        return metrics

    def _calculate_derived(
        self, metrics: list[FroniusMetric]
    ) -> list[FroniusMetric]:
        """Calculate derived metrics from the collected raw data.

        Splits bidirectional power values into separate import/export
        and charge/discharge metrics for easier graphing.
        """
        derived: list[FroniusMetric] = []

        # Build lookup for quick access
        lookup = {m.name: m.value for m in metrics}

        # Grid import/export split
        p_grid = lookup.get("fronius.powerflow.p_grid")
        if p_grid is not None:
            derived.append(FroniusMetric(
                name="fronius.calculated.grid_import",
                value=max(0.0, p_grid),
                unit="W",
                description="Power imported from grid",
            ))
            derived.append(FroniusMetric(
                name="fronius.calculated.grid_export",
                value=max(0.0, -p_grid),
                unit="W",
                description="Power exported to grid",
            ))

        # Battery charge/discharge split
        p_akku = lookup.get("fronius.powerflow.p_akku")
        if p_akku is not None:
            derived.append(FroniusMetric(
                name="fronius.calculated.battery_charge",
                value=max(0.0, p_akku),
                unit="W",
                description="Battery charging power",
            ))
            derived.append(FroniusMetric(
                name="fronius.calculated.battery_discharge",
                value=max(0.0, -p_akku),
                unit="W",
                description="Battery discharging power",
            ))

        # Self-consumption power (PV production minus grid export)
        p_pv = lookup.get("fronius.powerflow.p_pv")
        if p_pv is not None and p_grid is not None:
            grid_export = max(0.0, -p_grid)
            self_consumption = max(0.0, p_pv - grid_export)
            derived.append(FroniusMetric(
                name="fronius.calculated.self_consumption_power",
                value=self_consumption,
                unit="W",
                description="PV power consumed directly (not exported)",
            ))

        # Load absolute (P_Load is negative in the API)
        p_load = lookup.get("fronius.powerflow.p_load")
        if p_load is not None:
            derived.append(FroniusMetric(
                name="fronius.calculated.load_absolute",
                value=abs(p_load),
                unit="W",
                description="Absolute house consumption",
            ))

        return derived
