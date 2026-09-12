# Health monitoring

## Where to look

Open **Grafana → Alerting → Alert rules → SmartHome Health**. Rules are
provisioned from `grafana/provisioning/alerting/health.yml`; edit the file rather
than the UI. Evaluation runs every minute.

No contact points, credentials, or notification policies are provisioned by this
project. No working outbound notification channel has been configured. Until you
configure a contact point and routing in Grafana, check alert states in the UI.
Grafana's built-in default email receiver is not a configured delivery channel.

## Rules

| Rule | Condition | Pending period |
|---|---|---|
| Device collection is stale | Last non-empty successful read older than 3 collection intervals, minimum 180 seconds | 2 minutes |
| Scraper metrics are not arriving | Latest health sample older than 180 seconds, or absent | 2 minutes |
| Host metrics collector unavailable | Node exporter scrape failed or missing | 2 minutes |
| Host filesystem low on space or inodes | Less than 10% available space or inodes on physical filesystems | 5 minutes |
| Host filesystem became read-only | Physical filesystem reports read-only | 2 minutes |
| External data disk mount missing | Expected ext4 mount `/mnt/external` absent | 2 minutes |

No-data and query errors are configured as Alerting, not silently healthy. Grafana
may show temporary pending/no-data states during initial startup before the first
OTLP export (every 60 seconds). Actual detection also depends on the one-minute
rule evaluation schedule.

With current collection intervals, ISG becomes stale after 15 minutes and Fronius
after 3 minutes, plus the pending/evaluation delay. Each ISG page and Fronius
endpoint is tracked independently, so partial failures do not hide behind other
successful reads. Zero-valued readings (e.g. solar power at night) are valid.
A timestamp of zero means a configured source has never returned any metrics.
A disabled Fronius collector registers no Fronius sources and will not alert.

Optional endpoints that consistently return no measurements will alert. If a
device does not support an endpoint, deliberately remove it from
`scraper/config.py`; do not suppress real collection failures. This mechanism
checks endpoint/page freshness, not completeness of every individual measurement
or whether a device's internal sensor is frozen.

## Metrics

- `smarthome_collection_last_success_seconds{collector,source}`: Unix timestamp
  updated only after a real non-empty collection, never by periodic cached exports.
- `smarthome_collection_stale_after_seconds{collector,source}`: configured
  freshness threshold.
- Node exporter provides filesystem capacity/inodes/read-only state, CPU, memory,
  disk I/O, load and available thermal sensors. Prometheus scrapes it every 30s.

Node exporter has a read-only host filesystem mount for host visibility, drops
all capabilities and has no published host port. Its HTTP endpoint is accessible
only to containers on the monitoring network. Only relevant collectors are
enabled; this is **not SMART disk-health monitoring**.

The expected disk mount alert is specific to this Pi's `/mnt/external` ext4 HDD.
Update that rule if storage is moved. Disk alerts do not automatically repair a
filesystem or prevent startup on an unmounted data path.

## Applying changes and testing

```bash
docker compose config -q
docker compose build scraper
docker compose run --rm --no-deps -v "$PWD/tests:/app/tests:ro" scraper \
  python -m unittest discover -s tests -v
docker compose up -d
```

Recreating/restarting LGTM loads provisioned rule changes. Alternatively use
Grafana's authenticated `POST /api/admin/provisioning/alerting/reload` endpoint.
Prometheus config changes require a reload/restart as well.

Verify all rules have healthy evaluations in Grafana and inspect both freshness
metrics in Explore. Unit tests cover initial failures, partial failures, empty
responses and the fact that exporting health does not update last-success time.

## Still separate work

- Notification contact point and routing, followed by a delivery test.
- External uptime monitoring: a stopped Pi/Grafana cannot evaluate or send alerts.
- Automated off-device backups and restore testing.
- SMART checks and additional CPU/temperature/memory thresholds if needed.
