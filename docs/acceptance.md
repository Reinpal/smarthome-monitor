# Integrated acceptance — #7 / spec #1

## Resolution record and safety boundary

Implementation verification is complete **subject to the deployment gates below**.
This is the local issue-resolution record; tickets remain open. Independent
Standards + Spec reviews follow this work. No push, merge, issue closing, actual
deployment, reload, restart, mount operation or history rewrite was performed.
The live stack and original checkout were read-only. All test writes went to
this implementation checkout or disposable loopback services with fictional data.

**Do not treat these dashboards as billing-grade metrology.** Existing raw data
survives, but new accepted interval history begins only after contract-v2 collection
and recording rules run. Legacy cached export times cannot be backfilled into
known field observation times. A retention setting is not available history.

## Acceptance against the parent

| Parent requirements / user stories | Evidence and qualification |
|---|---|
| Home, topic overviews, separated current power and period energy, links (1–3, 16) | All five dashboards provisioned together in disposable Grafana. Home has six headlines; native links retain the selected instants to both overviews and diagnostics and back. Desktop layout and 390px smoke checks; not a comprehensive mobile/accessibility certification. |
| Self-sufficiency, self-consumption, battery and specific yield (4–5, 10–11) | Actual native queries test signs, inventory, zero/missing production and DST nights. Self-sufficiency includes pre-period battery inventory. Unsupported direct solar self-consumption remains explicitly unavailable; DC minus AC is not direct use. Night coverage is a qualified observed non-grid share, not battery runtime. |
| Financial definitions and private dated rates (6–9) | Source/configuration → query → panel tests separate avoided purchases + export revenue from import cost − revenue; VAT, universal discounts, excluded fixed/restricted charges, expiry/provisional carry-forward and confirmed corrections. Reopening reprovisioned dashboards reprices retained history without a TSDB rewrite. No bill reconciliation. |
| Heat by purpose, efficiency, comfort and notes (12–15) | VD electricity and heat have matched units/periods; NHZ heat is separate, valid zero differs from missing. Comfort/weather remain context, not causal or weather-normalized claims. Native annotation CRUD/query tested only in disposable Grafana. |
| Missing/stale data, comparable periods and annual suppression (17–19) | Real collector/SDK/OTLP, promtool and native Prometheus tests cover counter resets, gaps, cached exports, missing metadata/fields, zero denominators, continuous observed prefixes, shorter/leap months and DST. Daily charts omit invalid/partial dates. Comparisons require complete equal elapsed windows. No invented annual history or annualization. |
| Privacy, deterministic/live checks, safe operations (20–22) | Audit and read-only results below. Automated suites and hardware benchmark are repeatable. Actual deployment and rollback are **not executed**; operator gates remain. |

### Headline audit

The authoritative input inventory is [measurement contracts](measurement-contracts.md).
[Period calculations](period-calculations.md) defines coverage/price fields;
[Home/Solar](home-solar.md) and [Heating/diagnostics](heating-dashboards.md) define
presentation. These are shared definitions, not separate dashboard formulas.

| Headline family | Source/boundary, unit and confidence | Unavailable behavior |
|---|---|---|
| Household, PV, battery charge/discharge | Fresh signed device power, accepted linear intervals at nominal 60s export/recording resolution; site demand, DC generator, battery-side respectively; **estimated kWh** | Missing/leading/internal coverage, reset/invalid intervals or stale tail withhold; no nighttime zero fallback |
| Grid import/export | One validated location-0 meter, cumulative Wh gauge differences /1000; **device-reported kWh**, conservatively allocated in time | Resets/gaps/ambiguous meter withhold, not extrapolated `increase` |
| Self-sufficiency | Matching site load and imports; calculated percent, including previous inventory, not same-period direct PV use | Zero load, negative balance or mismatched periods withhold |
| Financial benefit/cost/reference | Qualified energy × private dated variable prices; configured currency, **estimated**, independent confirmed/provisional price status | Missing prices/energy withhold; financial context cannot replace an absent headline |
| VD electricity/heat/efficiency, NHZ heat | Device purpose counters, MWh ×1000 → kWh; matching VD heat/electricity sums → kWh/kWh; NHZ heat separate | No verified NHZ electricity or whole-system efficiency; zero denominator/mismatched periods withhold |
| Specific yield, inventory, night coverage | Estimated DC kWh/configured kWp; SOC × usable kWh; complete aligned 18:00–06:00 measured night conditions | No weather-normalized efficiency, AC loss-free battery attribution, minimum-SOC or runtime prediction |

Every Home headline has definition/boundary/period tooltips, units, detail links
and explicit Full period / Observed prefix qualification plus coverage panel 90.
Heating value and coverage panels are separate so surviving metadata cannot
masquerade as a value. Derived coverage can describe the left dependency only:
the value additionally requires all dependencies to align. Current snapshots are
fresh at query evaluation, not proof of device-internal freshness; a browser left
open without refresh is not a live safety monitor.

## Private read-only validation

Run locally with authorized Docker access, **never with shell/HTTP debug logging**:

```sh
python deploy/check-insights-readonly.py
```

The script discovers the existing LGTM bridge address/credentials in memory via
`docker inspect`, then uses only internal GET APIs. It does not contact device
APIs, write annotations, execute inside containers, reload provisioning or mutate
storage. Proxies/redirects are disabled for credential safety. Outputs are fixed
check names and PASS/FAIL/UNAVAILABLE; nonzero means at least one gate is unmet.
Do not replace the sanitized output with inspect dumps, screenshots or raw errors.
UNAVAILABLE means the check could not complete, **not** a successful absence test.

Observed results in this session:

| Check | Result |
|---|---|
| Grafana database, same-stack datasource boundary/health, existing overview provisioning | PASS |
| Stable datasource configured for required POST queries | FAIL — running definition predates these changes |
| No directly published Grafana host port, existing writable `/data` mount, five-year retention | PASS (not an independent disk/backup or network-perimeter certification) |
| Three representative raw site/grid/VD inputs queryable uniquely | PASS |
| Integrated five-dashboard provisioning | UNAVAILABLE — new destinations are not deployed |
| Contract-v2 marker, per-field metadata, loaded interval rules, queryable derived intervals | FAIL — not deployed |
| Representative recent-month complete presence / prior-year boundary | FAIL / FAIL — incomplete history; annual comparison unsupported |

The history check is coarse presence in five-minute windows, not per-measurement
coverage, billing validation or evidence that sub-five-minute gaps are absent.
No real dates, readings, labels, addresses, prices, screenshots or inputs are
published. **Live new headline queries and live UI acceptance cannot pass before
an approved deployment**; sending multi-MB generated queries to the old datasource
was deliberately avoided. Annotation-write roles, generated bind permissions,
production rule scheduling and concurrent-user budgets remain deployment gates.

## Privacy audit

- Tracked/index and working-tree content checked for private input/data/archive
  paths, high-confidence credential patterns and exact runtime endpoint/credential
  tokens (comparison in memory, no values printed). No matches found.
- Fictional examples/fixtures remain explicitly fictional. Generated artifacts
  were rendered from the example and audited separately; actual household inputs
  were not copied into tests. Public templates contain markers/unavailable
  parameters, not rendered prices. `.gitignore` excludes private inputs, local
  environment variants, generated dashboards/rules and data/backups; tests check
  ignored paths and the index. `.dockerignore` excludes these from image builds.
- Removed actual recovery timing/history details from public README/storage
  guidance, retaining the safe recovery procedure. Historical Git versions may
  still contain installation/incident details or identifiers. **No history
  rewriting was done or authorized**, and this is not a claim of exhaustive
  forensic secret detection.
- Authorized Grafana viewers/query inspectors and database backups can see
  rendered capacities/prices and private annotations. Ignore rules and hidden
  variables are not access controls. Use trusted-LAN access, protect `.env`,
  `private/` and Grafana/backup contents, and never force-add them.

## Hardware query and resource acceptance

Run the opt-in benchmark, not against a live endpoint:

```sh
export PROMETHEUS_TEST_BINARY=/tmp/prometheus-3.5.1.linux-arm64/prometheus
export PROMTOOL_TEST_BINARY=/tmp/prometheus-3.5.1.linux-arm64/promtool
export GRAFANA_TEST_HOME=/tmp/smarthome-grafana-test
export CHROMIUM_TEST_BINARY=/usr/bin/chromium
PYTHONPATH=.:tests /tmp/smarthome-measurements-venv/bin/python tests/dashboard_benchmark.py
```

It creates two fictional months of accepted 60-second facts for **every** period
source, evaluates all five dashboards including offscreen targets (four concurrent
requests), then loads each in real Grafana/Chromium cold/warm. It emits only
aggregate JSON size, wall/CPU time, query sample peak, browser JS heap and sampled
Prometheus query/browser-phase RSS (not fixture-ingestion peak or total LGTM RSS).
The benchmark fails on query/body errors or a dashboard query set /
browser load reaching the one-minute budget. Browser “cold/warm” labels mean first
and second pass, not a cold TSDB or HTTP-cache comparison (network interception
for privacy disables browser HTTP caching). Diagnostics intentionally have no raw-history fixture here; their
populated semantics are covered by the source/query and diagnostic tests. This is
not a month-long recording-rule run or a faithful LGTM/collector resource model.

The initial size regression test failed for all three overviews: 3.41 / 3.61 /
3.32 MB, mainly repeated timezone expressions in hidden variables. The first fix
groups disjoint same-offset spans and uses PromQL `scalar`'s exactly-one-result
semantics instead of duplicating the entire TZDB expression in `sum` and `count`.
Ambiguous/nonexistent wall times and dates outside 2000–2100 still fail closed.
Transition regressions include Berlin, Lord Howe, Kathmandu, Apia's date-line
change and UTC, including exact fold/gap/horizon boundaries.

Size reduction alone was **not enough**: the populated Solar browser journey
returned a datasource timeout. The Full period / Observed prefix labelling had
also duplicated each expensive headline value query in two branches. The second
fix computes that value once and attaches a one-valued qualification label vector.
Real Prometheus regressions confirm measured zero, full/prefix labels and missing
value suppression even when coverage survives. The subsequent populated cold/warm
browser journeys pass. No timeout/sample limits were increased and no timezone
horizon, sampling, freshness or energy formula was weakened.

### Recorded hardware results

Four ARM64 cores, approximately 7.9 GiB RAM; disposable Prometheus **3.5.1**,
Grafana **12.1.1**, Chromium **145.0.7632.159**. Fictional March (including DST),
two months of all 13 accepted interval sources, two import and two export entries.
One sequential benchmark run before/after, not a statistical throughput guarantee:

| Dashboard | Rendered MB before → after | All-target wall seconds before → after | After Prometheus CPU seconds | Browser first / second pass seconds |
|---|---:|---:|---:|---:|
| Home | 3.410 → 0.987 | 42.9 → 31.2 | 88.4 | 24.4 / 23.0 |
| Solar & battery | 3.610 → 1.168 | 57.3 → 36.6 | 119.2 | 33.2 / 33.1 |
| Heating & hot water | 3.319 → 0.935 | 33.1 → 32.2 | 77.2 | 12.7 / 13.0 |
| Heat-pump diagnostics (no raw fixture here) | 0.162 → 0.162 | 0.5 → 0.5 | 1.6 | 2.2 / 2.3 |
| Solar diagnostics (no raw fixture here) | 0.115 → 0.115 | 0.4 → 0.4 | 1.1 | 2.1 / 2.0 |

All **250 panel targets**, including offscreen ones, and all calendar variables
succeeded; each query set and both browser passes met the 60s budget. CPU seconds
are summed across cores, not elapsed time. Slowest individual target at four-way
concurrency: **14.4s**; peak query samples approximately **312k**. Prometheus sampled
RSS peaked at **230.5 MiB**; reported browser JS heap was **44–78 MB**, not total
Chromium/Grafana/LGTM memory. Payloads fell **68–72%**. Solar's populated browser
timeout is resolved without increasing timeout/sample limits. Monthly queries
still consume substantial CPU: this is bounded single-view acceptance, not a
production multi-user capacity certification. More tariff entries require another
private, fictional-count-matched performance check, never copying actual prices
into the tracked example or benchmark.

Operational limits: use one calendar month, at most 32 touched local dates, for
daily charts and period headlines, and one actively refreshing overview at a time.
For longer energy comparisons use the completed-month trend panel directly
(panel 75, linked from guidance/daily panels), with at most twelve touched local
months. This is not a certified annual headline or multi-user workload. Keep the existing POST datasource;
GET URL limits are not a workaround. The default one-minute refresh is not a
multi-user capacity promise: for exploratory monthly use, prefer manual refresh
or a longer native refresh interval; restore a suitable cadence for right-now
snapshots. Multiple simultaneous dashboards, large tariff histories, multi-month
headline selections, sustained thermals, swaps, real production compaction and
concurrent LGTM workload are not certified. Check those privately before rollout;
if queries approach the refresh/timeout budget, stop the rollout rather than
raising sample limits or exposing Grafana publicly.

## Repeatable automated acceptance

With the four variables above set:

```sh
/tmp/smarthome-measurements-venv/bin/python -m unittest discover -s tests -v
git diff --check
# Public config syntax needs target-version promtool (3.5.1 predates its fields):
/tmp/fictional-period-prometheus-new/promtool check config prometheus/prometheus.yaml
```

`test_acceptance.py` adds actual combined navigation/layout, payload budgets and
sanitized read-only-audit failure checks. Existing tests cover source/config →
query/panel scenarios, units, tariffs, battery states, resets, missing/cached data,
DST, daily transforms, equal-elapsed comparisons, no data, annotations and private
rendering/history sentinels. Binary-dependent tests must show **zero skips** for a
complete acceptance run. Tests use fresh temporary databases and block browser
requests outside the isolated Grafana origin. No privileged storage test is run.

Recorded full suite: **81 tests passed, zero skips**, in **487.537s**. Generated
**117 recording rules** passed promtool 3.5.1 syntax checks; full public Prometheus
configuration passed promtool **3.10.0** (matching its newer config fields).
Combined Compose configuration passed `config --quiet` in a disposable directory
with a fictional `.env`; generated-file ignore checks and `git diff --check` passed.
The separate populated hardware benchmark above passed as well.

## Review-fix acceptance

The five independent review findings are addressed on one fixes branch:

- `freshness_promql.py` owns freshness construction for live snapshots, offset
  recording-rule states and compact diagnostic markers. Diagnostic labels and
  instant/range modes remain intact; age-only snapshots still expose stale age.
- Fixed-report browser acceptance uses the existing disposable `grafana` context,
  which now waits for dashboard provisioning as well as service health.
- `EnergyFixture` exposes source injection, interval ingestion and native queries;
  tests no longer borrow heating test methods or traverse nested test-case stacks.
- Cycling counts exact epoch-minute subquery samples, including fractional minute
  selections and whole-second range rounding, retaining reset/gap/freshness gates
  and requiring two samples.
- Home, Solar and Heating have native completed-local-month trends. Whole covered
  months only; twelve bounded buckets, independent DST/calendar boundaries, no
  partial totals, fabricated history or annualization. Complete-source queries
  reuse interval integration, full coverage and validity deadlines without an
  unnecessary observed-prefix calculation. Unsupported comparison/bucket windows
  use short retrieval sentinels, not zero-energy fallbacks.

`test_monthly_trends.py` exercises synthetic historical local months and actual
native Grafana panels/time selections/refresh. Cycling regressions exercise the
actual rendered panel with promtool. The existing source/rule, privacy, navigation,
annotation and fixture suites remain required. All services are disposable and
loopback-only. Production permissions, deployment, rule scheduling and concurrent
workload gates above remain **unverified and unchanged**.

Final full suite: **88 tests passed, zero skips**, in **872.697s**. After the final
scaled-diagnostic marker consolidation, **six diagnostic/template tests** also
passed (including all rendered query syntax, Wh→kWh/MWh values and missing-field
suppression). All **117 recording rules** passed promtool 3.5.1 syntax checks;
public Prometheus configuration passed promtool 3.10.0. `git diff --check` passed.

The existing disposable benchmark passed with **346 targets**, including the new
monthly targets. It retains the original fictional two-month dataset and selects
one complete March; this is not a populated twelve-month/concurrent-user budget.
All five query sets and all ten browser cold/warm loads remained below its existing
60-second limit, without raising timeouts or sample limits:

| Dashboard | Rendered MB | All-target seconds | Browser first / second seconds |
|---|---:|---:|---:|
| Home | 1.254 | 31.298 | 26.819 / 26.127 |
| Solar & battery | 1.435 | 41.063 | 36.318 / 36.553 |
| Heating & hot water | 1.163 | 34.294 | 14.807 / 14.653 |
| Heat-pump diagnostics | 0.171 | 0.602 | 2.352 / 2.450 |
| Solar diagnostics | 0.120 | 0.453 | 2.322 / 2.205 |

Peak query samples: **325,466**; sampled Prometheus RSS: **230.8 MiB**. The separate
monthly regressions exercise a twelve-month selection with sparse fictional
history (leap February, both DST transitions, gaps and a year boundary), plus
populated two-month native panels on all three overviews. Native Inspect verifies
transformed month rows; refresh, partial selections and backwards time navigation
verify recalculation and unavailable history. No live checks or deployment were run.

## Approved deployment checklist — guidance only, NOT performed

Follow [private setup and rate updates](private-configuration.md) and the existing
[storage guard](storage-recovery.md); this does not redesign backups or storage.

1. **Approval and preflight.** Choose a maintenance window and record the current
   code revision/image IDs, Compose project identity, mount targets, storage path,
   retention and datasource UID privately. Check storage/mount guard health and
   free space. Use the existing deployment directory/project and actual `.env`;
   never let a new worktree/project create a fresh `./data` directory. A missing
   intended disk or unexpected empty history is a stop condition, not a reason
   to bypass `create_host_path: false`.
2. **Private recovery material.** Retain the last known-good code/image and private
   `.env`, `installation.json`, generated dashboards/provider and price-free rules
   outside the public repository. Follow the existing private consistent-backup
   procedure for Grafana notes/database and metrics; a live filesystem copy is not
   automatically a consistent database backup. Confirm a usable private recovery
   copy exists before maintenance; do not redesign/restore the backup system here.
3. **Stage and validate.** Supply actual capacities/timezone/currency and dated
   tariffs using the schema, never the fictional example prices. Run `validate`
   and `render` in a private staging checkout. Generated output replacement is
   per-file, **not** a transactional multi-dashboard publish; do not render into a
   live watched directory while relying on cross-dashboard consistency. Validate
   rule syntax with the deployed Prometheus-compatible `promtool check rules` and
   full config with its `promtool check config`. Verify ignored paths and protected
   parents; container users need read/traverse access to the mounted generated
   children (0755 directories, 0644 files under private 0700 parent).
4. **Deploy only after approval.** Transfer the reviewed code and complete staged
   private outputs into the existing deployment location, retaining the old set.
   The opt-in override changes dashboard/rule mounts only. Verify interpolated
   configuration with `config --quiet`, not a dump containing secrets. For an
   initial contract-v2 rollout, build the scraper and recreate only the existing
   LGTM/scraper services with the same project/storage. LGTM must load the new
   datasource POST setting and Prometheus rule glob; scraper must export v2 field
   metadata. Example maintenance commands (do **not** execute during review):

   ```sh
   # In the existing approved deployment directory, with its private .env:
   docker compose -f docker-compose.yml -f docker-compose.private.yml config --quiet
   docker compose -f docker-compose.yml -f docker-compose.private.yml build scraper
   docker compose -f docker-compose.yml -f docker-compose.private.yml up -d --no-deps --force-recreate lgtm scraper
   ```

   Keep `/data`, image pin, five-year retention, networks and trusted-LAN proxy
   policy unchanged. Do not add a Grafana public port. Do not run `down -v`, prune
   volumes, change project identity, or initialize another metrics database.
5. **Post-deploy gates.** Re-run the sanitized checker. Privately confirm old raw
   history remains at the same boundaries and new v2 measurements/intervals arrive;
   rule health/evaluation durations must fit the 60s interval. Some history gates
   will remain FAIL legitimately: legacy/month/year coverage cannot be invented.
   Confirm generated mount readability, all five provisioned UIDs, actual POST
   datasource queries, period links, stale/no-data display and full versus prefix
   context. Start with short newly covered periods; check one month only when
   actual accepted history exists. Measure query latency/CPU/memory alongside the
   production workload before treating one-minute refresh as sustainable. Test
   native annotation permissions privately only if separately authorized; offline
   notes remain valid. Record only pass/fail and semantic limitations publicly.

### Rates, corrections and optional notes

Update/split nonoverlapping dated import entries and monthly export entries;
validate applicability, VAT basis and confirmed/provisional status. Expired/gap
prices carry forward as **provisional**, never as implicit zero or confirmed.
Corrections deliberately reprice affected historical calculations. Stage a complete
render, install only the intended generated dashboard set, allow the provider to
reread, then **reopen** dashboards: an old tab can retain old query definitions.
Tariff-only changes need no TSDB rewrite, price recording, collector rebuild or
Prometheus/Grafana restart. Keep the last good private input/output for reversal.
Optional native annotation usage/privacy is documented in
[Heating/diagnostics](heating-dashboards.md#optional-native-annotations).

### Rollback without overwriting history

- For a bad tariff/template, restore the previous complete generated dashboard
  set and matching private input (including provider); reopen Grafana. Leave the
  raw/derived metrics, Grafana database and annotations untouched. An old copied
  rendered dashboard must not silently disagree with its retained input.
- For a failed initial rollout, use the recorded previous Compose/code/images and
  previous generated set in the **same project and data location**. Omit the
  private override only if intentionally returning to unavailable public templates.
  Select the recorded prior image IDs through a private Compose override (or
  explicitly rebuild the reviewed previous collector): restoring code alone does
  not select an older image if its build tag was overwritten.
  Reload/recreate affected services only during approved maintenance; preserve the
  existing storage guard and all data mounts. Check old raw-history continuity and
  datasource health again after rollback.
- Disabling rules leaves stored intervals intact. Re-enabling after a collection
  outage leaves a genuine coverage gap; do not fill or delete it. Prefer retaining
  contract-v2 collector semantics even when rolling back UI changes. Rolling back
  pre-v2 collector code reintroduces known sign/unit/freshness defects; those new
  old-semantics samples must not be used by v2 calculations.
- Never restore a database snapshot over active `/data` for a dashboard rollback,
  overwrite Grafana's DB to restore JSON, replay archives into live storage, remove
  WAL/blocks, prune volumes or shorten retention. Database recovery is a separate
  explicitly approved operation on copies, per the storage guide. Actual rollback
  mechanics and production permissions remain untested here.
