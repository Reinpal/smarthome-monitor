# Home and Solar & battery — #5 / spec #1

## Delivered interface

- `grafana/provisioning/dashboards/home.json`: **`home-energy`**.
- `grafana/provisioning/dashboards/photovoltaik.json`: retained **`pv-overview`**.
- Both default to **this local calendar month**, `now/M` → `now`, one-minute
  refresh, privately configured timezone. Use native day/month selections.
- Home has six headline cards, one daily chart, separate power/SOC snapshot,
  compact comfort/field-age context, equivalent previous-month comparison and
  coverage table. Headline tooltips explain units, confidence and boundaries;
  detail and coverage links retain the selection.
- Solar separates site/PV/grid/battery energy, specific yield, explicitly
  unavailable direct solar self-consumption, financial breakdown, observed night
  contribution, endpoint snapshots and coverage. Benefit is **avoided purchases
  + export revenue**; variable purchases **minus** export revenue is distinct.
  Price confidence is visible in separate context panels, independently of
  telemetry completeness. Each headline has only its metric target: a surviving
  price/coverage frame must not silently replace a missing headline value.

`python -m scraper.provision render` uses the existing private configuration and
provider; see [private configuration](private-configuration.md). No plugin,
service, exporter, database, recording-rule family or fixed report was added.
Public JSON contains target **markers**, not private prices or example telemetry.
Without private rendering, targets intentionally return no data. The calculation
verification dashboards remain independent; these overviews do not embed reports.

Heating and both technical diagnostics are owned by #6. Useful original solar
technical panels belong in that slice, not this overview. The renderer resolves
`homeNavigation: true` against the local template set, adding only existing UIDs:
`home-energy`, `pv-overview`, `heatpump-overview`, `heatpump-diagnostics`, and
`solar-battery-diagnostics`. Ordinary rendering provisions the whole template set;
if provisioning a subset manually, also remove links to omitted destinations.
No network lookup or private Grafana inspection is involved in staged navigation.

## Measurement and missing-data behavior

Contracts and limitations are inherited from [measurement contracts](measurement-contracts.md)
and [period calculations](period-calculations.md), not redefined by layout:

- Site household demand and DC PV generator energy are estimated power integrals,
  nominal 60-second exported/recorded resolution, using accepted linear source
  intervals. Hybrid inverter AC output is **not** pure PV production.
- Grid energy is one validated meter's absolute gauge differences, in kWh after
  Wh conversion. Resets/gaps are not repaired or zero-filled.
- Self-sufficiency includes pre-period battery inventory. Direct solar
  self-consumption remains unavailable: AC/DC losses and inventory allocation
  do not establish a verified direct-use or onsite-retained share.
- Battery charge/discharge is battery-side energy; SOC × private usable capacity
  is an inventory estimate, not separately measured AC household delivery.
- Specific yield is estimated DC kWh / configured kWp, not weather-normalized
  equipment efficiency. VD electricity and matching-period energy efficiency
  retain the compressor-accounting boundary, not unverified whole-system/NHZ
  electrical coverage. No short-period annualization or invented prior year.
- A main headline can show **Observed prefix** only when continuous coverage
  starts at the requested boundary and the tail is still fresh. **Full period**
  is a separate dynamic label. Coverage panel **90** supplies exact endpoints,
  covered seconds and full-period flags. A recent coverage endpoint alone does
  not validate a derived quantity with missing/mismatched dependencies.
- Financial estimates use dated all-in variable import prices and monthly export
  rates. Fixed charges, restricted credits, instalments and balances never become
  avoided savings. Carried-forward/expired prices remain visibly provisional.
- Power/comfort snapshots check per-field presence, receipt age, endpoint health,
  finite values, source uniqueness and contract version. Cached export timestamps
  do not refresh readings. Snapshot panels are **right now when To=now**; an
  absolute historical selection deliberately shows its endpoint, explicitly
  labelled as historical rather than falsely claiming current live power.

## Native calendar query seam (closes #4's comparison gap)

`render_period_targets` delegates calendar markers to `scraper/calendar_promql.py`.
The renderer injects hidden native Prometheus **query variables**, refreshed on
load/time-range changes. Variables resolve installation-local boundaries to
numeric UTC epochs. They are computation plumbing, not private-data security.
The native picker, period links and refresh drive actual queries without Python
rerendering. Unlike a `1M` shift, both comparison windows use equal elapsed time.

```json
{"refId":"A", "calendarMetric":"household", "calendarMode":"daily"}
{"refId":"A", "calendarMetric":"household", "calendarMode":"current"}
{"refId":"B", "calendarMetric":"household", "calendarMode":"previous"}
{"refId":"C", "calendarMetric":"household", "calendarMode":"change"}
{"refId":"D", "calendarMetric":"household", "calendarMode":"current", "periodField":"coverage"}
```

Existing `PeriodQueries` keys with a supported coverage contract are reusable
(including heating energy and matching-period ratios; not the deliberately
unsupported solar-self-consumption key). Optional `legendFormat` changes presentation.

**Comparison:** available only for a local month-start selection ending within
that month. First find the current quantity's valid continuous observed prefix;
cap its duration to the previous month's elapsed duration. Require **both**
resulting comparison windows to have complete, valid, matching-duration history.
The main headline remains independent and is never shortened to fit February.
The comparison displays the elapsed duration actually used. Percentage change is
unavailable for a zero previous denominator. The zero used internally when no
valid comparison window exists is a hidden **duration sentinel**, never an energy
fallback; all comparison targets require positive duration and valid values.

**Daily chart:** one instant result per complete local date, not rolling 24-hour
energy and not an average of period totals. A March DST day can contain 23 hours,
an October day 25; half-hour changes are supported too. Partial selected/current
dates and invalid dates are omitted, not drawn as zero. One calendar month is the
supported scale (at most 32 touched local dates); longer selections withhold the
chart. A 31-day autumn month is not rejected by a hardcoded 744-hour limit.
Absent dates are absent bars; another valid date can still be shown. Three
separate, non-stacked columns retain site/DC/grid boundaries.

Daily targets return `day` (local day-start **epoch seconds**) and `quantity`
labels. Use the transform sequence in Home/Solar panel **70**:

1. `labelsToFields`, `mode: columns`, `valueLabel: quantity`.
2. **`merge`** — required explicitly in Grafana 12.1.1.
3. `organize`, exclude query `Time`; optionally rename quantity columns.
4. `convertFieldType`, `day` → `time`, `dateFormat: X`.
5. `sortBy`, `day` ascending.
6. `formatTime`, `timeField: day`, `outputFormat: YYYY-MM-DD`; renderer sets timezone.

Use a native bar chart with `xField: day`. Native Inspect → Data → Apply panel
transformations exposes the same date rows/values; this is not a generated image.
Coverage tables similarly pivot labelled period targets, with an explicit merge.

### Bracketing, timezone horizon and query cost

A source observation crossing midnight is often recorded **after** midnight.
Simply querying only `@ midnight` would make normal jittered days permanently
incomplete. Calendar targets therefore pin to each numeric closing boundary with
`lookahead="48h"` (PromQL negative offset) and a correspondingly padded subquery
window. They still **clip all energy and coverage to the exact selected bounds**.
The allowance covers the existing maximum one-day freshness threshold plus
receipt delay. It only permits already-stored bracketing observations to arrive;
it does not predict future readings, bridge rejected gaps or extrapolate a tail.
Live comparisons use the actually observed prefix when a closing observation
has not arrived. Tests cover off-minute records and late boundary brackets.

Timezone metadata is generated from installed IANA TZDB for **2000–2100**, not
from household history. Ambiguous/nonexistent wall-clock boundaries and dates
outside that horizon fail closed (calendar variable warnings/unavailable data),
rather than selecting an arbitrary DST fold. Berlin 23/25-hour dates, Lord Howe's
half-hour change and Kathmandu's quarter-hour offset are tested. Regenerate after
TZDB rule updates. This metadata never backfills missing measurement history.

Native boundary variables and 32 independently clipped buckets trade query size
for no new runtime component. #7 compacts repeated timezone spans, reducing the
fictional Home/Solar payloads from 3.41/3.61 MB to 0.99/1.17 MB (including removal
of duplicated headline evaluation in qualification labels) without changing
calendar or measurement semantics. Use the existing POST datasource. See the [integrated hardware
benchmark and deployment gates](acceptance.md#hardware-query-and-resource-acceptance);
production concurrency budgets are **not** certified.
Larger/multi-year daily plots are intentionally not offered. The original
selected-period target defaults are unchanged for callers not using calendars.

## Observed overnight battery coverage

```json
{"refId":"A", "calendarMetric":"overnight_coverage", "calendarMode":"overnight"}
```

Choose the latest completed **local 18:00–06:00** window wholly inside the native
selection. This is an explicit operational window, **not** sunset/sunrise; it is
11/12/13 hours across normal European DST changes. Use the same marker with
`battery_discharge`, `battery_inventory_change`, `grid_import`, `night_start`
and `night_end` for the accompanying evidence. Endpoints are Grafana milliseconds.

Coverage is the **observed non-grid household share** during that window, not
separately metered AC battery delivery. Require complete aligned household,
import, PV, battery charge/discharge and SOC observations; measured zero PV;
zero charging; non-increasing SOC; a configured battery; and valid household
energy/non-grid balance. Show battery-side discharge, estimated inventory change
and imports alongside the percentage and exact night boundaries. Missing PV is
not nighttime zero. Any nonzero PV/charging, internal gap, missing SOC, stale or
unbracketed tail, or selection not containing the whole night withholds the
assessment. Other-generation and unused-grid-charging assumptions remain explicit.

There is no inferred minimum-SOC threshold, depletion-time prediction, estimated
hours-to-empty, future runtime guarantee or battery ROI claim. Observed inventory
loss is not attributed loss-free AC energy. Native time controls and refresh
recompute the assessment for the selected history.

## Verification, privacy and deployment boundary

Run from the implementation checkout with fictional fixtures only:

```sh
PROMETHEUS_TEST_BINARY=/path/to/prometheus \
PROMTOOL_TEST_BINARY=/path/to/promtool \
GRAFANA_TEST_HOME=/path/to/unpacked/grafana \
CHROMIUM_TEST_BINARY=/path/to/chromium \
  /path/to/venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Final verification: **66 tests passed, no skips**, including real Prometheus/
promtool **3.5.1**, Grafana **12.1.1**, system Chromium via Playwright, and the
existing offline configuration/privacy/storage guard regressions.
`git diff --check` and private-path ignore checks passed. The privileged storage integration
test and live deployment checks were intentionally not run.

`tests/test_home_solar.py` covers templates/units/navigation, source-receipt
freshness, reference-vs-native shorter/leap/DST month bounds, complete daily
buckets/partial dates/missing dates, jittered closing observations, live comparison
prefixes, zero/missing night PV, charging, SOC gaps, no battery, 11/12/13-hour
nights, and a real provisioned Grafana browser journey. The browser inspects
transformed daily data, follows period links, changes native time controls,
refreshes night queries, checks unavailable history, reprices retained history
by private reprovisioning/reopening, and smoke-tests a 390-pixel viewport.
Accepted-interval fixtures use real OTLP/Prometheus storage; the existing
`test_period_promql.py` tests source → recording-rule provenance, signs, resets,
missing/stale measurements and tariffs. These are complementary, not a claimed
month-long live collection run.

Services bind only disposable loopback ports and temporary storage. Browser
external requests are blocked; analytics/plugin preinstallation are disabled.
During #5, no live service, original checkout, private input, real reading or
screenshot was accessed or changed. Subsequent #7 [read-only validation](acceptance.md)
records deployment gaps without changing the live stack. No production
permissions, device metrology or live new-dashboard acceptance is claimed.

Follow the existing private setup/deployment/rollback procedure. Initial interval
rules require a separately approved deployment; raw/interval history is not
rewritten or backfilled here. Tariff corrections require regeneration and
**reopening** provisioned dashboards, not a TSDB rewrite. Preserve `/data`,
retention and existing storage mounts; no push, merge or issue closing is part
of this implementation.
