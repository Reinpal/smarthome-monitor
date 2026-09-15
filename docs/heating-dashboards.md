# Heating, hot water and technical diagnostics — #6 / spec #1

## Destinations and daily use

| Dashboard | Stable UID | Template |
|---|---|---|
| Heating & hot water | `heatpump-overview` (retained) | `grafana/provisioning/dashboards/heatpump.json` |
| Heat-pump diagnostics | `heatpump-diagnostics` | `grafana/provisioning/dashboards/heatpump-diagnostics.json` |
| Solar & battery diagnostics | `solar-battery-diagnostics` | `grafana/provisioning/dashboards/solar-battery-diagnostics.json` |

The heating overview defaults to **today in the installation timezone**, refreshing
every minute. Use the native time picker or Today / Yesterday / This month /
Previous month shortcuts. A previous-month shortcut selects a complete local
calendar month; it is **not** an automatic equivalent-elapsed comparison.
Dashboard and data links preserve the selected instants, independently of the
Home landing page. Grafana may serialize these as ISO timestamps rather than
milliseconds; they represent the same period. Existing overview UID is unchanged;
old individual technical panel links should now use the diagnostic UID and retained
panel ID. Invalid annualized panels deliberately have no replacement.

Home is `home-energy`; Solar & battery retains `pv-overview`. Those two overview
templates and the shared calendar-query extension belong to the parallel #5 slice.
`homeNavigation: true` adds only destinations present in the local template set;
standalone delivery therefore does not create a broken Home link. Deploy the
combined template set, not a manually copied subset.

## What the cards mean

Every energy/ratio card is a real native Prometheus **instant** selected-period
query, resolved from public `periodMetric` / `periodField` markers during private
rendering. No fixed reports, HTML snapshots or cached financial results power
this UX. Each value has an immediately adjacent coverage panel showing duration,
full-period versus observed-prefix status, and the observation endpoint. They
are separate panels intentionally: Grafana otherwise silently omits an absent
value frame while still showing coverage. A stale value must instead show
**Unavailable**. The cost coverage panel independently shows confirmed or
provisional prices. A price's confidence does not certify telemetry.

- **Heating / water electricity:** separate VD cumulative MWh differences × 1000,
  in kWh. Device-reported compressor-related accounting, not an independent
  whole-system electrical meter.
- **Heating / water heat:** separate device VD heat differences in kWh; not
  calibrated room-delivered or useful tap heat.
- **Period efficiency:** matching-period VD heat / VD electricity, kWh/kWh. Never
  average daily ratios. Zero electricity makes the ratio unavailable; zero heat
  with positive electricity is a valid zero. Not instantaneous COP or JAZ.
- **Separate NHZ heat:** manufacturer/source-distinguished auxiliary heat in kWh,
  including legitimately observed zero. Missing NHZ is unavailable. No separately
  verified NHZ electricity exists; do not add NHZ heat only to the VD numerator or
  assume auxiliary electricity equals heat.
- **VD grid-tariff reference cost:** estimated VD electricity at date-effective
  household variable import tariffs. **Not actual attributed spending** during
  solar/battery supply. Fixed charges and restricted credits are excluded. Financial
  reference and efficiency remain separate.

Fans, circulation pumps, controls, standby, external auxiliary equipment and
whole-system defrost accounting remain unverified. See the authoritative
[measurement contracts](measurement-contracts.md) and
[interactive calculation contract](period-calculations.md).

A current value can be a **continuous observed prefix**, not a full requested
period or an extrapolation. Leading/internal gaps, stale tails, resets and
mismatched input periods withhold values. Derived coverage can describe only the
left dependency; a displayed coverage duration does not make an unavailable
combined value valid. No `or vector(0)`, short-period annualization, invented annual
history, energy-certificate comparison or area-based benchmark is used.

## Daily charts and comparable months

The daily bar chart uses `calendarMetric` / `calendarMode: daily` markers from
#5's shared native calendar extension. It shows separate heating/water VD kWh for
**complete local days**, next to outdoor and flow/return conditions. Native query
variables recalculate local boundaries on time changes; labels are local day-start
dates in the installation timezone. Partially selected/current days and days with
gaps, stale tails or resets are omitted, never shown as zero. Select one calendar
month (at most 32 touched local dates, including a 745-hour autumn month);
today’s still-in-progress energy remains available on the cards. The daily chart
uses the shared transform sequence, including `merge` after `labelsToFields`.

The two **Equivalent MTD** panels use native `current` / `previous` calendar
markers for combined VD electricity and matching-period VD efficiency. Select
This month, or a local month-start-to-date range: both current and previous windows
are capped to the quantity's current valid observed prefix and the shorter prior
month's elapsed duration, and **both** require complete valid history. The main headline retains the original requested range.
Non-month selections or missing comparable history remain unavailable. Ratios
are each computed from matching energy sums, never averaged daily ratios. No
year-over-year history is invented. Local/DST semantics, non-hour timezones and
shorter-month behavior are owned/tested by the shared #5 extension; the #6 tests
add actual heating target and native chart-transformation acceptance.

## Comfort, cycling and diagnostics

Actual/target room and tank temperatures, outdoor conditions and flow/return
remain in the overview. These are sensor/control-zone context, not whole-house
comfort coverage, tap temperatures or a heat meter. Weather beside consumption
is context, not weather normalization. A before/after change cannot establish a
setting's causal effect without comparable weather, duration and comfort.

The concise cycling summary is **observed starts in the sampled span**:
last-minus-first fresh cumulative starts on one-minute query samples inside the
selection. It rejects observed resets and missing/stale query minutes; it neither
extrapolates nor reconstructs exact on/off cycles. Boundary activity before the
first accepted sample may be omitted (up to one minute). Cached samples remain
valid only within the configured field/endpoint freshness window. An unobserved
reset or internally frozen device cannot be disproven. No generic technician
limits, starts/year projections or equipment-life advice are asserted.

Diagnostics preserve useful device day/lifetime ratios (explicitly **not annual**),
raw VD/NHZ energy and runtime/start counters, internal temperatures, pressures,
compressor/fan speeds, hydraulics, humidity, optional parsed device states and
electrical readings. Bar readings use bar units, not mbar; mixed electrical and
hydraulic series have explicit per-series units. Unsupported V×A whole-system
power and extrapolated gauge-counter `increase` summaries are absent.

Solar/battery diagnostics retain phase power/voltage/current, frequency/power
factor, hybrid inverter AC totals/output and optional day/year counters, DC string
readings, storage SOC/temperature/voltage/current, device/error codes and momentary
device percentages. These percentages are not period ratios. Positive battery
current charges; hybrid AC output is not pure PV production. API capacity units
remain unverified and are not used as usable energy. Neutral colors are not
manufacturer-specific normal limits.

Every device target requires unambiguous contract-v2 values and metadata,
per-field presence, fresh field and endpoint receipt time under the configured
source threshold, and aligned export timestamps. Missing compressor state is
**unknown**, never off. Timeseries do not connect gaps. Collection receipt-age /
threshold and field-presence panels are separate: successful endpoint collection
alone cannot certify each measurement. Legacy history without metadata is withheld,
not retrospectively declared fresh. Receipt freshness cannot prove sensor-internal
freshness. No raw history is changed or deleted.

## Optional native annotations

All three dashboards enable Grafana's built-in dashboard annotation query as
**Settings & comfort notes**; no custom datasource, service or automated control.

1. Use a Grafana account with annotation-write permission (normally Editor/Admin,
   subject to your access policy). Viewer access does not grant note creation.
2. Ctrl/Cmd-click a timeseries point, or drag a time region, and choose **Add
   annotation**. Write a brief setting/comfort note; optional tags `settings` and
   `comfort` help find it. This is a note, not a control command.
3. Use the annotation toggle to show/hide notes; use native annotation editing to
   correct/remove them. Built-in dashboard notes belong to that dashboard, not
   automatically to every linked dashboard.
4. Offline notes are equally acceptable. Do not publish actual settings, comfort
   history, household notes or screenshots as fixtures or issue attachments.

Annotations persist in Grafana's database. Back it up privately: it may contain
personal notes and rendered tariff values. Provisioning a dashboard template does
not back up its notes. The isolated acceptance test exercises native annotation
creation/read/delete and verifies the actual browser annotation query; live role
permissions and the deployed annotation editor remain unverified.

## Provisioning, privacy and rollback

Follow [private configuration](private-configuration.md) for offline validation and
rendering. The existing glob includes both new diagnostic JSON files automatically;
no provider, datasource, service, public port or collection architecture was added.
Templates contain no private installation parameters or prices. Rendering resolves
the configured timezone/currency and date-effective queries under ignored
`private/generated/`; viewers/query inspectors and Grafana backups can see those
rendered prices. Hidden variables are not access control.

Tariff corrections require validation, regeneration and reopening the reprovisioned
dashboard. Retained interval history is repriced at query time; no financial TSDB
rewrite. Initial rule mounting/reload and deployment require separate approval.
Rollback restores previous private generated templates/configuration only; leave
all Prometheus/Grafana databases and storage paths untouched. Never use `down -v`,
restore over `/data`, or overwrite stored history as a dashboard rollback.

## Repeatable fictional verification

From this implementation checkout, using installed disposable-test dependencies:

```sh
PROMTOOL_TEST_BINARY=/path/to/promtool \
  python -m unittest discover -s tests -p 'test_heating_dashboards.py' -v
PROMETHEUS_TEST_BINARY=/path/to/prometheus \
GRAFANA_TEST_HOME=/path/to/unpacked/grafana \
CHROMIUM_TEST_BINARY=/path/to/chromium \
  python -m unittest discover -s tests -p 'test_heating_browser.py' -v
python -m unittest discover -s tests -v
git diff --check
```

- Template tests check units, real marker resolution, visible per-metric coverage,
  price confidence, navigation, native annotations, nonoverlapping grid positions
  and absence of retired calculations.
- Actual promtool tests run **every rendered target**, and exercise matching ratios,
  kWh conversion, valid-zero/missing NHZ, zero denominators, resets, partial periods,
  stale cached fields, missing metadata, ambiguous series and sampled cycling.
- Real isolated Grafana/Prometheus browser tests exercise fictional accepted interval
  facts, native period switching/refresh, displayed values/context, annotation
  persistence/browser query, both diagnostic links with preserved time, explicit
  stale-tail unavailability despite retained coverage, unavailable history and a
  390px smoke check.
- `test_heating_calendar.py` tests the actual daily/MTD heating targets and native
  daily bar-chart transformations against the integrated #5 extension. It explicitly
  skips if that parallel dependency is absent. During #6 implementation, the combined
  source snapshot is tested in a disposable copy; no shared files in either agent's
  worktree are modified. The source-to-recording-rule seam is independently
  tested by promtool; this is not a month-long live recording run.

Verification result for this slice: local full suite **65 tests, 63 passed and 2
explicit calendar-dependency skips**. The disposable combined #5/#6 snapshot then
passed **all 9 heating tests**, including those two calendar tests and both browser
journeys. `git diff --check` and private-path ignore checks passed. The combined
repository-wide acceptance run after merging the parallel slices is recorded below.

### Combined Home/heating integration

The #5 shared calendar/navigation extension is now integrated. Both heating and
Solar measurement assertions were preserved when resolving the shared test files.
Heating uses the finalized daily `merge` transform and metric-specific comparison
variables; its query fixture substitutes integer Grafana duration values rather
than invalid decimal PromQL duration tokens. No duplicate query API was added.

Full integrated offline acceptance: **75 tests passed, zero skips**, including
Prometheus/promtool, Grafana/Chromium, heating daily/MTD charts, navigation and
Home/Solar browser journeys. The temporary #5 dependency is resolved. #7 still
owns separately authorized deployment/permissions, live data acceptance and
production resource/concurrency validation; this run makes no live claims.

All fixtures are fictional; test services bind loopback with temporary databases,
and browser requests outside the isolated Grafana origin are blocked. Binaries used:
Prometheus/promtool 3.5.1, Grafana 12.1.1 and system Chromium via Playwright. Tests
requiring binaries explicitly skip without the environment variables. No live
stack or original checkout was read, restarted, deployed or mutated. No real device
metrology, live history acceptance, deployed permissions or billing-grade accuracy
is claimed; production validation belongs to separately authorized #7 acceptance.
