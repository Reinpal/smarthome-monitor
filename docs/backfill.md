# Bounded historical reconstruction — issue #9

This is a one-shot, private **legacy estimate**, not contract-v2 observations.
It uses the existing interval/query path and dashboards; no new service, prices
in storage, collector changes, raw-history replacement or battery metrology.
`smarthome_legacy_v1_*{metric="…",provenance="legacy_estimate"}` is separate from
live `smarthome_period_v1_*`. No presence, observation-time, contract-version or
endpoint-health metadata is fabricated.

## Estimation policy

- Read actual raw range-vector samples and labels, not configured retention or
  lookback-filled query-range values. Bound each request to at most 370 days.
  Require the existing source contracts and the operator's confirmation that
  these streams describe the **same physical installation and units**. Ignore
  only `instance` and `service_instance_id` changes. Other identity changes abort.
- Agreeing concurrent exports are deduplicated, never added. Asynchronous overlap
  must agree under linear interpolation. Conflicting overlap and adjacent pairs
  are unavailable, not an arbitrary preferred exporter. Nonoverlapping restarts
  can continue the same absolute counter; decreases reject the reset interval.
- Grid absolute Wh → kWh; heat-pump VD/NHZ cumulative MWh → kWh. Monotonic counter
  pairs may bridge **at most 24 hours** of collection outage. This is conditional
  continuity, not proof of an unobserved reset/replacement. Known equipment changes
  or unknown reset periods must be excluded by the operator. No reset clamp,
  `increase`, extrapolated energy or additional duplicate meter counter.
- Raw PV, signed household and signed `P_Akku` use positive-part linear integrals.
  Negative `P_Load` consumes; negative `P_Akku` charges, positive discharges. Old
  derived battery/load series are never inputs. Power pairs bridge **at most 15
  minutes** as explicitly estimated interpolation, not validated freshness. This
  deliberately differs from the live 180-second freshness policy: metadata does
  not exist in legacy history. Longer gaps remain unavailable, never zero.
- Equal readings may be genuine plateaus or cached exports. They are retained as
  estimates and their duration is reported; no timestamp/plateau heuristic can
  establish historical device freshness. SOC inventory is **not** reconstructed.
  No new battery-loss, self-consumption or overnight model is introduced.
- Resample accepted pairs into energy-equivalent 60-second facts. Any minute with
  uncovered time is omitted in full (except explicitly clipped request edges).
  This preserves accepted minute power energy/sign crossings and counter totals;
  clipping *inside* a minute assumes uniform allocation. Counter allocation across
  outages, day and tariff boundaries is approximate. Prices remain date-effective,
  query-time and independently provisional. Fixed charges are still excluded.
- Per-source cutoff is the earlier of requested `until` and the **first retained
  live interval start**, not its recording timestamp. No legacy interval crosses
  that cutoff. The final partial minute can coexist with a live interval; queries
  count each once by provenance and take the maximum observation endpoint, not
  the sum. Never run a second overlapping plan. Fresh inventory is required before
  operator installation; staged input is not an eternal authorization.

A report includes actual bounds, identity/conflict checks, rejected pairs,
covered duration, plateaus, gaps, energy, raw first/last bracketing readings and
independent accepted-segment differences. `raw_bracketing_reconciles` is false
across a reset, conflict or rejected gap; the first/last delta must **not** then be
presented as recovered consumption. Boundary interpolation and discarded minute
energy are separate from reconciliation error. All reports are private.

## Interactive behavior

The existing `PeriodQueries` and calendar panels read both namespaces through a
raw selector, preserving sample timestamps. `telemetry_status` is 1 for validated
observations only and 2 when the selected interval includes legacy estimates;
no covered time yields no status. Combined values take the less-confident input.

Home/Solar headline legends say **Legacy estimate**, including charge/discharge.
Coverage tables and Heating's coverage cards expose **Legacy estimate · freshness
unknown**. A visible overview notice qualifies daily/monthly charts and states the
gap policy. Full period still means temporal coverage, **not measured freshness**.
Long gaps or absent leading history still withhold complete headlines/days/months.
Month-to-date does not require a finished calendar month. A missing price still
withholds financial results; a confirmed price does not upgrade telemetry.

`valid_until_seconds` in the legacy namespace is only an exact reconstructed
coverage endpoint (+1ms comparison tolerance), not a freshness claim;
`stale_after_seconds=0`. Old history cannot acquire a current observed-prefix tail
merely through Prometheus lookback. No right-now snapshot gate is changed.

## Private dry-run and block staging

Use a protected directory (0700) and request file (0600). Example inputs below are
**fictional**. Put actual epochs/paths only in the protected request file; do not
paste selectors, credentials, outputs or dates into command logs/public tickets.
The CLI accepts no live storage destination and makes no HTTP writes.

```json
{
  "start": 1735689600,
  "until": 1738368000,
  "output": "/protected/private/stage"
}
```

`PERIOD_PROMETHEUS_URL` supplies the private read-only endpoint in memory. HTTP
proxies/redirects are disabled and errors sanitized. The endpoint must select one
installation; multiple non-restart identities abort. Raw source inventory includes
one day of bracketing padding. The optional `inventory` request key points to a
protected gzip JSON of raw Prometheus matrices keyed by metric name, including
all retained `smarthome_period_v1_start_seconds`. Keep full identities, not just
an arbitrarily selected exporter. Existing legacy history aborts inventory/plans.
The CLI requires the output beneath the protected request directory, and its last
sample more than three hours old (outside the usual live head window).

```sh
umask 077
python -m scraper.backfill --request /protected/private/request.json
# Default: report.json + deterministic intervals.om only. Review them privately.
python -m scraper.backfill --request /protected/private/request.json \
  --apply --promtool /path/to/deployed-compatible/promtool
# --apply means CREATE PRIVATE BLOCKS, never install them in the live TSDB.
```

`--apply` splits input by UTC sample date to bound promtool's repeated input scans,
creates `stage/blocks/`, and records per-file SHA256 in `blocks.json`. The report
contains the deterministic OpenMetrics SHA256. Identical repeated staging is a
no-op, preserving block IDs; altered input or incomplete/modified staging fails
closed. Inspect a failed stage privately, then use a new directory, not blind
retries over partial blocks. Never point this tool at a real TSDB.

Validate a **copy** of staged blocks using disposable loopback Prometheus and the
rendered existing dashboard queries. Starting Prometheus can compact blocks, so
never use the canonical hashed `stage/blocks` as the validation server's storage.
Check selected-period energy, counter reconciliation, confidence, daily chart,
tariff status and legacy/live joining. Render actual installation prices only in
protected staging, not the watched deployment directory. Do not expose a test
Grafana port or enable remote-write/admin APIs on the production server.

## Operator apply — separately reviewed maintenance only

The implementation agent does **not** perform these steps. No service stop/reload,
production import, live filesystem extraction or dashboard publication is part
of dry-run. Before approval, provide the report, hashes and exact block list.

1. Verify existing storage guard, disk space, current deployment/project identity,
   TSDB path and a consistent recovery backup. Keep the previous complete generated
   dashboard set separately. Preserve the coordinator's latest main templates
   (including removal of the unsupported self-consumption tile).
2. Repeat read-only inventory immediately before maintenance. Check that the
   live start for every imported metric is at/after its planned cutoff, source
   identity/units and continuity still agree, and no `smarthome_legacy_v1_*` data
   already exists. If it does, compare the retained import ledger: an already
   applied identical plan is a **no-op**, not permission to copy new block IDs.
   A different/unknown plan is a stop condition. Do not overlay it.
3. Validate staged `blocks.json` against the canonical staged files. Record its
   SHA256, report/OpenMetrics SHA256 and exact block directory names in a private
   import ledger **outside the TSDB**. Stage the current-main dashboard templates
   through the reviewed renderer with actual private configuration; validate their
   queries before changing any watched file.
4. Only in approved maintenance, stop the existing process owning this exact TSDB
   using its normal deployment procedure. Verify it is stopped and the TSDB lock
   is free. Do not change mounts, project, retention, raw files, WAL or head. Do
   **not** use a live filesystem copy as a consistent backup or extract archives
   over the database.
5. For each directory in the reviewed manifest, copy it to a temporary sibling on
   the destination filesystem; verify every file hash, owner/mode and free space,
   then atomically rename to its original block ID in the stopped TSDB. Require
   destination absence; if an exact same-ID/hash block is already present, skip
   it. Any differing same-ID content aborts. Do not copy `report.json`, lock,
   configuration, WAL or validation-server files. Do not regenerate blocks to
   “retry” an import. Track each installed ID in the ledger.
6. Start the **same** existing service against unchanged storage. Check health,
   old raw history, new legacy provenance/counts and ongoing v2 rule health.
   Install the reviewed complete dashboard set via the existing provisioning
   procedure, retaining the previous set; reopen dashboards. Check a recovered
   historical day and month-to-date, qualified confidence and price status, daily
   bars and the cutoff join. No remote-write receiver or new service is needed.

This small tool intentionally does not automate production service control or
filesystem installation: storage paths and ownership are deployment-specific and
must be verified by the coordinator. Block import is not yet live-validated.

## Reversal

- **Immediate, nondestructive UI rollback:** restore the previous rendered
  dashboards (their literal `smarthome_period_v1_*` selectors ignore the separate
  legacy namespace). Restore matching configuration/templates and reopen Grafana.
  Raw and v2 collection remain untouched; estimates disappear from the UI. This
  works even after Prometheus compaction and requires no database deletion.
- **Physical reversal before compaction:** during approved stopped maintenance,
  if *all* installed block IDs and hashes still match the ledger, move only those
  exact directories into private quarantine, then restart. If any imported block
  has compacted/changed/disappeared, **stop**: never remove a merged block that may
  contain raw/live series. Retain the hidden legacy data and ledger; physical
  cleanup then requires a separately reviewed series-scoped operation on a copy.
- Do not restore a snapshot over active storage, delete raw blocks/WAL, shorten
  retention or reimport with new block IDs. Logical rollback is the safe default.

## Fictional tests

`tests/test_backfill.py` covers resets, unit normalization, restart deduplication,
conflicts, signed battery/load, cached plateaus without metadata, bounded/long
gaps, cutoff clipping/joining and repeated staging/collision. Real promtool blocks
are read by isolated Prometheus; rendered Home headline and transformed daily
chart run through Grafana/Chromium, including price confidence. Existing tests
continue to cover DST, tariff transitions/corrections, live freshness rejection,
calendar selection and other panels. No actual history or prices are fixtures.

Verification: full suite **97 passed, zero skips**; after final CLI safety,
reconciliation and provenance refinements, **11 targeted tests passed**, including
real Grafana/Chromium. The 117 live rules and deployed-version public configuration
passed promtool checks. Production import/rollback remains an operator gate.

```sh
PROMETHEUS_TEST_BINARY=/path/to/prometheus \
PROMTOOL_TEST_BINARY=/path/to/promtool \
GRAFANA_TEST_HOME=/path/to/grafana CHROMIUM_TEST_BINARY=/path/to/chromium \
  python -m unittest discover -s tests -v
```
