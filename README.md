# SmartHome Monitor

A Dockerized monitoring stack for **Stiebel Eltron ISG** heat pumps and **Fronius** solar inverters. Scrapes metrics from local device APIs and exports them via OpenTelemetry to a Grafana LGTM stack (Loki, Grafana, Tempo, Mimir) with pre-built dashboards.

## Features

- **ISG Heat Pump Scraping** -- Parses HTML pages from the Stiebel Eltron ISG web interface (temperatures, energy counters, operating status, etc.)
- **Fronius Solar API Polling** -- Collects real-time data from the Fronius Solar API (power flow, battery storage, meter readings, inverter data)
- **Calculated Metrics** -- Derives COP (Coefficient of Performance), temperature spreads, and inverter power from raw readings
- **OpenTelemetry Export** -- Pushes all metrics via OTLP/gRPC to a local collector
- **Grafana Dashboards** -- Ships with provisioned dashboards for heat pump and PV overview

## Architecture

```
ISG Heat Pump (LAN)  ──HTML──>  ┌──────────┐  ──OTLP/gRPC──>  ┌───────────┐
                                │  Scraper  │                   │ Grafana   │
Fronius Inverter (LAN) ──JSON─> └──────────┘                   │ LGTM Stack│
                                                                └───────────┘
```

The **scraper** container runs two independent collection loops:
- ISG: scrapes HTML pages every 5 minutes (configurable)
- Fronius: polls JSON API every 30 seconds (configurable)

Both push metrics to the **LGTM** container (Grafana + Prometheus/Mimir + OpenTelemetry Collector).

## Prerequisites

- Docker and Docker Compose
- A Stiebel Eltron ISG (Internet Service Gateway) on your local network
- (Optional) A Fronius solar inverter with the Solar API enabled

## Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/<your-username>/smarthome-monitor.git
   cd smarthome-monitor
   ```

2. **Create your environment file**
   ```bash
   cp .env.example .env
   ```

3. **Edit `.env`** with your device IPs and preferences:
   ```env
   ISG_BASE_URL=http://<your-isg-ip>
   FRONIUS_BASE_URL=http://<your-fronius-ip>
   ```

4. **Start the stack**
   ```bash
   docker compose up -d
   ```

5. **Open Grafana** at [http://localhost:3000](http://localhost:3000) (default credentials: `admin` / `admin`, configurable via `GF_SECURITY_ADMIN_PASSWORD` in `.env`)

## Configuration

All configuration is done via environment variables in the `.env` file. See [`.env.example`](.env.example) for all available options:

| Variable | Default | Description |
|---|---|---|
| `ISG_BASE_URL` | `http://192.168.1.100` | IP/URL of your Stiebel Eltron ISG |
| `SCRAPE_INTERVAL_SECONDS` | `300` | How often to scrape the ISG (seconds) |
| `FRONIUS_ENABLED` | `true` | Enable/disable Fronius collector |
| `FRONIUS_BASE_URL` | `http://192.168.1.200` | IP/URL of your Fronius inverter |
| `FRONIUS_POLL_INTERVAL_SECONDS` | `30` | How often to poll the Fronius API (seconds) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://lgtm:4317` | OTLP gRPC endpoint (change only for external collectors) |
| `LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `GF_SECURITY_ADMIN_PASSWORD` | `admin` | Grafana admin password |

## Dashboards

Two provisioned Grafana dashboards are included:

### Heat Pump (Waermepumpe)
Monitors temperatures, energy consumption, COP, compressor status, and more from the ISG.

The dashboard includes two template variables you should adjust to match your setup:
- **Wohnflaeche (m2)**: Your heated living area in square meters (used for per-m2 energy calculations)
- **Inbetriebnahme**: Commissioning date of your heat pump as a Unix timestamp (used for lifetime calculations)

Both can be changed directly in the Grafana dashboard settings under **Variables**.

### Photovoltaik (PV Overview)
Shows solar production, grid feed-in/consumption, battery status, and power flow from the Fronius inverter.

> **Note**: The dashboards ship with timezone set to `Europe/Vienna`. Change this in the dashboard settings (gear icon > General > Timezone) if you're in a different timezone.

## Project Structure

```
smarthome-monitor/
├── docker-compose.yml          # Stack definition (LGTM + scraper)
├── Dockerfile                  # Scraper container image
├── .env.example                # Template for environment config
├── grafana/
│   └── provisioning/
│       ├── dashboards/         # Provisioned Grafana dashboards
│       │   ├── dashboards.yml
│       │   ├── heatpump.json
│       │   └── photovoltaik.json
│       └── datasources/
│           └── datasources.yml
└── scraper/
    ├── main.py                 # Entry point with dual collection loop
    ├── config.py               # Environment-based configuration
    ├── collectors/
    │   └── fronius_collector.py # Fronius Solar API client
    ├── scrapers/
    │   └── isg_scraper.py      # ISG HTML page fetcher
    ├── parsers/
    │   └── isg_parser.py       # ISG HTML parser (German formats, units, booleans)
    ├── metrics/
    │   └── definitions.py      # OTel metric unit mappings
    ├── exporters/
    │   └── otlp_exporter.py    # OTLP/gRPC metrics exporter
    └── requirements.txt
```

## Disabling Fronius

If you don't have a Fronius inverter, set `FRONIUS_ENABLED=false` in your `.env` file. The scraper will skip the Fronius collection loop entirely.

## Contributing

Contributions are welcome! This project was built for a specific hardware setup (Stiebel Eltron WPE + Fronius Gen24) but should work with other ISG-compatible heat pumps and Fronius inverters.

If you'd like to add support for additional devices or improve the dashboards, feel free to open an issue or submit a pull request.

## License

This project is provided as-is for personal/homelab use.
