# SmartHome Monitor

A Dockerized monitoring stack for **Stiebel Eltron ISG** heat pumps and **Fronius** solar inverters. Scrapes metrics from local device APIs and exports them via OpenTelemetry to a Grafana LGTM stack (Loki, Grafana, Tempo, Mimir) with pre-built dashboards.

## Features

- **ISG Heat Pump Scraping** -- Parses HTML pages from the Stiebel Eltron ISG web interface (temperatures, energy counters, operating status, etc.)
- **Fronius Solar API Polling** -- Collects real-time data from the Fronius Solar API (power flow, battery storage, meter readings, inverter data)
- **Calculated Metrics** -- Derives device VD energy ratios and temperature spreads; private provisioning adds freshness-gated period energy and dated-price estimates
- **OpenTelemetry Export** -- Pushes all metrics via OTLP/gRPC to a local collector
- **Grafana Dashboards** -- Home, Solar & battery, Heating & hot water, and linked technical diagnostics

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

5. **Open Grafana through your existing trusted-LAN proxy** (the base Compose file does not publish port 3000). Set a strong `GF_SECURITY_ADMIN_PASSWORD` in `.env`; do not expose Grafana publicly.

## Configuration

Device/runtime configuration uses environment variables in `.env`. See [`.env.example`](.env.example) for available options. Installation parameters and tariffs use one ignored `private/installation.json`; see [Private installation configuration](docs/private-configuration.md) for the fictional example, validation, tariff lookup API, and opt-in offline dashboard provisioning.

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

## Metrics retention and backups

Prometheus retains **5 years** of metrics, configured in
[`prometheus/prometheus.yaml`](prometheus/prometheus.yaml). Compose mounts this
configuration read-only and pins the LGTM image by digest. Validate the config
with the target image's `promtool check config` before upgrading.

Data lives at `${DATA_PATH:-./data}` on the host. Create that directory explicitly
on the intended storage before the first start; Compose will not create it.
On the deployed Pi, Docker waits for and verifies the external drive before any
containers start. See [External-drive storage and recovery](docs/storage-recovery.md)
for the installed guard, tests, and recovery procedure.
Persistence survives container recreation, but does **not** protect against
retention expiry or disk failure.
Increasing retention cannot restore previously deleted measurements. Monitor
available disk space as history grows.

Local recovery archives are excluded from Git under `backups/`. The legacy volume
and stopped-stack data were archived privately; `backups/SHA256SUMS` records checksums.
These are one-time local copies on the Pi, **not automated off-device backups**.
Copy them to another device for protection against loss of the Pi.

Never start Prometheus against the original legacy volume for recovery: work on
an extracted copy with sufficiently long retention. Do not extract recovery
archives over the live database. A recurring off-device backup and a tested
restore procedure still need to be set up.

## Health monitoring

Collection freshness and host-storage alerts are provisioned in Grafana under
**Alerting → Alert rules → SmartHome Health**. A private node-exporter service
provides host metrics. Notification delivery is not configured yet.
See [Health monitoring](docs/monitoring.md) for thresholds, operation and limitations.

## Dashboards

Use [private provisioning](docs/private-configuration.md) to resolve the public
no-data templates into native, time-picker-driven dashboards:

- **Home** (`home-energy`): six period headlines, separate power/comfort context,
  completed local daily energy and equal-elapsed previous-month comparisons.
- **Solar & battery** (`pv-overview`): site/DC/grid/battery energy, dated-price
  financial estimates, specific yield and qualified observed overnight coverage.
- **Heating & hot water** (`heatpump-overview`): separate VD electricity/heat,
  matching-period efficiency, NHZ heat, comfort/weather and optional private notes.
- **Heat-pump diagnostics** and **Solar & battery diagnostics**: technical
  measurements and collection health, with period-preserving links.

Home/Solar default to this local calendar month; Heating defaults to today.
Missing/stale/legacy history is unavailable, not zero. Financial benefit is an
estimate, not a bill; VD efficiency is not verified whole-system efficiency.
Unsupported direct solar self-consumption and annual comparisons remain unavailable.

See [Home/Solar](docs/home-solar.md), [Heating/diagnostics](docs/heating-dashboards.md)
and the [final acceptance, performance and deployment/rollback guide](docs/acceptance.md).
**Deployment has not been performed.** Existing raw history is preserved; new
freshness-gated interval history begins only after an approved deployment.

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
