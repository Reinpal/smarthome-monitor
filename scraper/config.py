"""Configuration loaded from environment variables."""

import os


class Config:
    """Application configuration from environment variables."""

    def __init__(self):
        # --- ISG Heat Pump Configuration ---
        self.isg_base_url = os.environ.get("ISG_BASE_URL", "http://192.168.1.100")
        self.scrape_interval = int(os.environ.get("SCRAPE_INTERVAL_SECONDS", "300"))
        self.otlp_endpoint = os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://lgtm:4317"
        )
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")

        # ISG pages to scrape (path suffix -> page name for metric namespacing)
        self.isg_pages = {
            "?s=1,1": "waermepumpe",
            "?s=1,0": "anlage",
            "?s=1,8": "energiebilanz",
            "?s=2,0": "status_anlage",
            "?s=2,2": "status_waermepumpe",
        }

        # --- Fronius Solar API Configuration ---
        self.fronius_enabled = os.environ.get(
            "FRONIUS_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        self.fronius_base_url = os.environ.get(
            "FRONIUS_BASE_URL", "http://192.168.1.200"
        )
        self.fronius_poll_interval = int(
            os.environ.get("FRONIUS_POLL_INTERVAL_SECONDS", "30")
        )

        # Fronius API endpoints to poll
        self.fronius_endpoints = {
            "powerflow": "/solar_api/v1/GetPowerFlowRealtimeData.fcgi",
            "storage": "/solar_api/v1/GetStorageRealtimeData.cgi",
            "meter": "/solar_api/v1/GetMeterRealtimeData.cgi?Scope=System",
            "inverter": "/solar_api/v1/GetInverterRealtimeData.cgi?Scope=Device&DeviceId=1&DataCollection=CommonInverterData",
        }

    @property
    def isg_urls(self) -> dict[str, str]:
        """Return full URLs for each ISG page."""
        base = self.isg_base_url.rstrip("/")
        return {
            page_name: f"{base}/{path}"
            for path, page_name in self.isg_pages.items()
        }

    @property
    def fronius_urls(self) -> dict[str, str]:
        """Return full URLs for each Fronius API endpoint."""
        base = self.fronius_base_url.rstrip("/")
        return {
            name: f"{base}{path}"
            for name, path in self.fronius_endpoints.items()
        }
