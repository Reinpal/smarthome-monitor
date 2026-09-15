# Period calculations — issue #4 / spec #1

Implements two **connected** verification paths in the existing stack. No service,
collector thread, financial exporter, database, or plugin was added. Nothing was
deployed/restarted; no live/original-checkout inputs or history were read or changed.

## Interactive path for dashboard agents (#5/#6)

**Use this path for the final dashboards, not the fixed report.**

```
existing source/OTLP gauges + contract-v2 field/endpoint health
  → existing Prometheus: generated 60-second interval recording rules
  → native Grafana instant PromQL: selected from/to + private dated prices
  → period-selectable verification dashboard (native time picker, 1m refresh)
```

`python -m scraper.provision render` now generates:

- `private/generated/period-rules/intervals.json` (valid YAML via JSON): telemetry
  interval rules, **no prices**. The existing private Compose override mounts this
  directory read-only. Base Prometheus configuration has an optional rule-file
  glob; without the override there are no matching files and no new rules.
- `private/generated/dashboards/period-selectable.json`: real native Prometheus
  targets, UID **`period-selectable`**, installation timezone, `now/M` → `now`,
  one-minute refresh. This minimal surface contains six headlines, coverage,
  full-period flags, observation endpoints, price confidence, and detailed energy,
  battery/financial/VD-purpose tables. Layout refinement belongs to #5/#6.
- The existing dashboards/provider remain provisioned. Public templates contain
  no prices; the selectable public placeholder explicitly says unavailable.

### Stable interface v1

```python
from scraper.period_promql import PeriodQueries, render_period_targets, recording_rules

queries = PeriodQueries(installation)   # validated immutable Installation from #3
value_target = queries.target("solar_battery_benefit", ref_id="A")
coverage_target = queries.target("solar_battery_benefit", field="coverage")
complete_target = queries.target("solar_battery_benefit", field="complete")
price_target = queries.target("solar_battery_benefit", field="price_status")
endpoint_target = queries.target("solar_battery_benefit", field="observed_until")
```

Targets use datasource UID `prometheus`, instant queries, Grafana `${__from}` /
`${__to}` in milliseconds and `${__range_s}` in elapsed seconds. They respond to
native time selection, dashboard links carrying `from`/`to`, and refresh. Keep
`instant=True`, `range=False`; **do not** turn a selected-period total into a
rolling timeseries by enabling range queries. Datasource POST is configured to
avoid URL-length limits. Never combine different exporter installations implicitly.

For public dashboard JSON, use this marker in `targets` instead of copying private
query strings:

```json
{"refId": "A", "periodMetric": "household", "periodField": "metrics"}
```

The existing `scraper.provision.render_dashboard` automatically resolves markers
through `render_period_targets`, including targets inside nested rows. #5 adds
optional `periodQualification: true` (dynamic Full period / Observed prefix legend)
and `periodLabels: {"headline": "...", "aspect": "..."}` for coverage-table pivots.
An optional `PeriodQueries(..., at="<numeric epoch or Grafana numeric variable>")`
pins all interval subqueries and observation/freshness endpoints to a historical
window. Optional `lookahead="48h"` on a pinned query permits already-stored
bracketing observations recorded after a calendar boundary, while energy remains
clipped to the exact start/end; its subquery window must include that padding.
It does not change default selected-period semantics. `at` must resolve to a
number because PromQL's `@` modifier does not accept arithmetic. Calendar rendering
is delegated to `scraper/calendar_promql.py`; existing callers need no changes. Optional
`fieldConfig.defaults.unit: "configured_currency"` resolves to the configured
Grafana currency unit. No expression in an unrendered marker means no data, not
zero. This is the supported integration seam: dashboard agents need no changes
to tariff or calculation logic. Direct Python callers can instead use the target
method and `render_interactive_dashboard(installation)` as working examples.

Stable metric keys and units:

| Keys | Unit / interpretation |
|---|---|
| `household`, `pv`, `grid_import`, `grid_export` | kWh; device-calculated site demand / DC generator estimate / one grid meter's absolute gauge differences |
| `battery_charge`, `battery_discharge`, `battery_inventory_change` | kWh; battery-side integration / SOC × private usable capacity inventory change (can be negative) |
| `specific_yield` | kWh/kWp; not weather-normalized efficiency |
| `self_sufficiency` | percent non-grid-supplied site demand; includes pre-period battery inventory |
| `solar_self_consumption` | unavailable (no samples); AC/DC losses and battery allocation are not verified |
| `heating_electricity`, `water_electricity`, `heatpump_electricity` | kWh, VD equipment accounting, not independently metered whole-system electricity |
| `heating_heat`, `water_heat`, `heatpump_heat` | kWh device VD heat, not calibrated room-delivered heat |
| `heating_ratio`, `water_ratio`, `heatpump_ratio` | matching-period kWh/kWh; not instantaneous COP, JAZ, or annual efficiency |
| `heating_aux_heat`, `water_aux_heat`, `heatpump_aux_heat` | NHZ heat only; not auxiliary electricity, not added to the VD ratio numerator |
| `import_cost`, `export_revenue`, `avoided_cost`, `solar_battery_benefit`, `net_grid_cost` | configured currency; respectively variable purchases, export remuneration, equivalent non-grid household supply at grid tariff, avoided + revenue, purchases − revenue |
| `heating_reference_cost`, `water_reference_cost`, `heatpump_reference_cost` | configured currency; VD electricity at household grid tariff, **not actual attributed spending** |

`PeriodQueries.metrics`, `.coverage`, `.complete`, `.price_status`, and
`.observed_until` are the underlying expression dictionaries; `.units` supplies
Grafana unit identifiers for metric targets. `coverage` is
seconds; `complete` is 1 for full coverage, 0 otherwise; price status is 1 confirmed,
2 provisional, absent before the first known rate. The raw observation endpoint
expression returns epoch seconds; `target(..., field="observed_until")` converts
to milliseconds for Grafana date units. Price confidence is separate from missing
telemetry. Derived coverage/endpoints refer to the left dependency; the value
itself is withheld unless **both** dependencies have equal endpoints/coverage.
Do not infer valid combined data from its coverage query alone.

### Recording/coverage contract

The `smarthome_period_v1_` prefix is a versioned derived family, not replacements
for raw history. Nine gauges per `metric` key record `start_seconds`,
`end_seconds`, `left`, `right`, `rate`, `zero_seconds`, `shape`, `stale_after_seconds`,
`valid_until_seconds` (the earlier field/endpoint freshness deadline).
Counter endpoints are kWh; power endpoints are signed, consumption-direction W;
SOC is percent. `rate` is endpoint difference / actual observed seconds;
`zero_seconds` is the linear zero crossing (start for a flat signal). `shape` is
1 for nonnegative endpoints, -1 for nonpositive endpoints, 0 for a sign crossing.
These derived facts keep month-scale queries within Prometheus sample budgets;
near-flat positive power uses a local trapezoid to avoid far-away-zero cancellation.

Rules require one series for every field/health input, contract version 2,
presence, finite valid values, receipt-time and endpoint freshness, and matching
export timestamps within two seconds (SDK scopes can differ slightly). Historical
configured endpoint thresholds are used, not hardcoded 180/900 s. Thresholds over
one day are unsupported. Previous/current source observations must advance within
both thresholds. A counter decrease removes that interval; **no `increase`,
negative energy, reset clamp, or extrapolated repair**. Omitted/stale/multiple
series are not zero. Resource/equipment identity must remain stable; an unobserved
reset cannot be disproven by software.

The 60-second recording group samples the latest exported observations. This
adds resampling to the nominal 60-second OTLP cadence; it is **estimated**, not a
reconstruction of every 30-second device poll. The query integrates the positive
part of linear signed power, including sign crossings, and proportionally
allocates counter increments only inside accepted observation intervals.
Selection and tariff boundaries clip those intervals. Missing internal/leading
coverage withholds a headline. Neither power nor counters bridge gaps exceeding
the historical freshness threshold in this conservative implementation.

**Current or unbracketed trailing boundaries:** interactive queries may show a
continuous **observed prefix**, ending at the latest accepted subquery observation,
only while its trailing age is below that source's threshold. The endpoint,
covered seconds and full-period flag must accompany headlines. This is not an
extrapolated total for the requested range. A historic selection may also end a
recording tick short of its requested boundary: it stays qualified partial, never
silently extrapolated. Full-period-only callers additionally gate their target
with `queries.complete[key] == 1`. Exact offline boundary verification can use the
bracketing raw-history path below. Sub-minute ranges are not a supported UX.

Use local Grafana calendar selections in the configured timezone: DST days may
contain 23/25 hours. No `/24` assumption is made. A 31-day interactive period has
been exercised against real Prometheus and Grafana. Larger multi-month/annual
headline ranges and hundreds of tariff revisions have **not** been performance
validated; prefer individual calendar-month selections for headlines and the
[completed-month trend panels](home-solar.md#completed-month-trends) for longer
energy comparisons. These use bounded, independently coverage-gated local months,
not one annual headline integral. A missing interval cannot
be made valid by changing the Grafana resolution.

### Prices, corrections and privacy

Every import component, VAT basis, all-import discount and monthly export rate
comes from the validated #3 snapshot. Fixed charges and restricted credits never
enter these queries. Date ranges become UTC instants using installation-local
midnights, including DST. Gaps/expiry are split into provisional carry-forward
segments, not retrospectively labelled confirmed. Nothing backfills before the
first known price. Prices are applied to each clipped telemetry interval, not
once to an entire multi-tariff range. Financial results remain estimates.

Correct a dated private tariff, validate, then regenerate private dashboards.
Grafana's existing provider rereads the queries; refresh/reopen the dashboard.
**Previously recorded intervals are repriced at query time**, including historical
periods. No financial metrics are persisted and no TSDB rewrite/backfill/restart
is needed for a tariff-only correction. Existing user sessions may hold the old
dashboard definition until reopened; refresh alone does not fetch a changed
provisioned definition. The browser test verifies reprovision + reopen + refreshed
queries against the same retained interval history.

Generated queries now contain effective prices (but no bills, component breakdown,
fixed charges, credits, or identifiers). Authorized Grafana viewers/query inspectors
and Grafana backups can see those prices. The same trusted-LAN access/privacy
controls apply; hidden variables are not security. Keep all generated files under
ignored `private/`. Do not publish screenshots, query dumps or tool errors from
private queries.

## Deterministic raw-history query and fixed report

This second path supports exact bracketing, detailed reasons, fictional reference
tests and comparisons without changing the live stack. It is **not the final UX**.

- `Period(start, end)`: aware instants, inclusive start/exclusive end.
- `calendar_period(local_start_date, local_end_date, installation)` handles DST.
- `query_report(PrometheusHistory(endpoint, labels={...}), installation, period)`
  returns JSON-safe report version 1; no cross-call history/price cache.
- `calculate_period(histories, installation, period)` is the same engine with
  supplied `History` / `Observation` fixtures. Missing source keys mean unavailable.
- `render_panel(report, keys, panel_id=..., title=...)` and
  `period_render.render_dashboard(report)` create native fixed-period text panels.
  They explicitly hide the time picker and require rerender. UID `period-verification`.

The production reader issues read-only raw range-vector GET queries in daily
chunks, with boundary padding. It rejects ambiguous/changed identities and aligns
per-field metadata/endpoint health to exports, not Prometheus lookback timestamps.
Repeated cached exports are deduplicated by collector observation time. Missing
legacy metadata withholds results; raw response bodies/errors are never logged.
Each metric returns `value` (or null), `unit`, `status` (device-reported/calculated/
estimated/unavailable), `covered_seconds`, `largest_gap_seconds`, `observed_value`
(subtotal only), `boundary`, `reasons`, and independent `tariff_status`.

This engine requires complete coverage for a headline, unlike the explicitly
qualified observed-prefix native path. It supplies local daily allocations and
MTD comparisons: both comparison windows are capped to the shorter month's
**elapsed** duration, without truncating the main requested headline. Both must
have observations; no short-period annualization or invented prior year. The #5
`calendarMetric` targets now implement the same comparison model in **native,
time-picker-driven Grafana queries** and test it against this reference. Live
comparisons additionally cap to the current valid observed prefix, then require
complete equal-duration history in both resulting windows. Non-month-start selections,
missing previous history and zero comparison denominators remain unavailable.
Unsupported prior-year history stays unavailable in both paths. Do not use a
Grafana `1M` time shift as an equivalent-elapsed comparison. The #5 native calendar extension now supplies completed local daily buckets and
interactive equivalent-elapsed comparisons; see [Home and Solar](home-solar.md).
These instant period targets must still never be averaged into a daily chart.

Offline command (read-only Prometheus access must be provided privately through
`PERIOD_PROMETHEUS_URL`; optional `PERIOD_PROMETHEUS_LABELS` is a JSON object of
exact resource/job labels, not printed):

```sh
python -m scraper.period_report --from 2025-03-30 --until 2025-03-31
# Fictional dates. Requires private configuration; writes only generated artifacts.
```

A missing config/query or invalid period exits nonzero with a sanitized error and
leaves previous dashboard outputs intact. No CLI response prints measurements,
prices, addresses or selectors. A normal `provision render` replaces the optional
fixed report with its unavailable placeholder; the interactive dashboard is always
regenerated. The rendered set is staged but not a multi-file transactional update.

## Verification and deployment boundary

All tests are fictional and disposable. Reproduce from this implementation checkout:

```sh
python -m venv /tmp/period-tests-venv
/tmp/period-tests-venv/bin/pip install -r scraper/requirements.txt playwright
PROMETHEUS_TEST_BINARY=/path/to/prometheus \
PROMTOOL_TEST_BINARY=/path/to/promtool \
GRAFANA_TEST_HOME=/path/to/unpacked/grafana \
CHROMIUM_TEST_BINARY=/path/to/chromium \
  /tmp/period-tests-venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Final result: **56 tests passed**, including real Grafana browser and monthly
query acceptance; `git diff --check` and private-artifact ignore checks passed.
Tested binaries: official Prometheus/promtool **3.5.1**, Grafana **12.1.1**, system
Chromium via Playwright. The full existing Prometheus configuration additionally
passed `promtool check config` with **3.10.0** (3.5.1 predates existing retention/
native-histogram configuration fields). Optional binary-dependent tests explicitly skip when
variables are absent. Test Grafana/Prometheus bind only loopback, use fresh temporary
storage, disable analytics/plugin preinstallation, and receive fictional data.
No Docker, live mounts, live services or household screenshots are involved.

- `test_period_calculations.py`: real query/result/render interface; units, signs,
  exact sign-crossing integration, battery inventory, matching VD ratios, zero,
  resets, gaps/partial totals, VAT/discounts/fixed exclusion, corrections/expiry,
  missing prices, both DST days and shorter/equal elapsed comparisons.
- `test_period_promql.py`: real promtool source → generated recording rules →
  actual Grafana expressions; failures, DST and repricing/confirmed confidence.
- `test_period_queries.py`: fictional HTTP source → existing collector/SDK/OTLP →
  isolated Prometheus → reference panels; real Grafana provisioning/browser; a
  retained 31-day interval dataset → real monthly/daily native panel results,
  refresh and historical tariff correction without changing stored intervals.
  Accepted-interval fixtures are used for month-scale tests; actual recording-rule
  provenance is tested separately by promtool, not claimed as a month-long live run.

The initial opt-in rule mount/configuration requires a **separately approved**
existing-stack deployment/config reload. None was performed here. Interval records
begin only when those rules run; no backfill of unknown legacy freshness is
implemented. Existing raw history/retention/storage paths remain intact. A future
rollback can remove the opt-in mount/generated dashboards during maintenance;
leave all TSDB data untouched. Re-enabling rules after an outage leaves a genuine
coverage gap. No device, billing-grade metrology, deployed permissions, production
resource budget, or live browser acceptance is claimed.
