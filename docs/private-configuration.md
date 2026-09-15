# Private installation and tariff configuration

Implements issue #3 under spec #1. `installation.example.json` is **entirely
fictional**, including all capacities, dates, area and prices. Do not use its
prices for a real household. `installation.schema.json` is the editor/schema
contract; Python validation additionally enforces semantic constraints.

## Setup and validation (offline)

Requires Python 3.10+ and system IANA timezone data (the existing Python Docker
image includes it). The new modules use only the standard library. Run from the
repository root; do not work in the live checkout during implementation.

```sh
umask 077
mkdir -p private
cp installation.example.json private/installation.json
chmod 700 private
chmod 600 private/installation.json
# Edit locally with a trusted editor; never paste private content into logs/tickets.
python -m scraper.provision validate
python -m scraper.provision render
```

There is one installation file, `private/installation.json`. Device endpoints
and credentials stay in the existing `.env`. `private/` (including generated
artifacts/backups) and local `.env` variants are ignored by Git and excluded
from Docker build context. Ignore rules do not protect files copied elsewhere,
forced Git additions, editor cloud sync or screenshots. Never store bills,
customer/meter/bank identifiers or actual telemetry in the public repository.
Unknown fields are rejected. Errors name schema fields, not supplied values,
unknown keys or input paths. The CLI prints only a summary, never resolved
configuration. Do not use debug dumps or `docker compose config` without `--quiet`
on a real setup: Compose may interpolate private environment inputs.

Required fields:

- `version: 1`; installation `panel_kwp` > 0, `usable_battery_kwh` >= 0 (zero
  explicitly means no battery), uppercase three-letter `currency`, and installed
  IANA `timezone`. Capacities are kWp and **usable** kWh, not Wp, Wh or nominal Ah.
- At least one import tariff and one monthly export rate. Supply explicit zero
  components/rates when genuinely applicable, never to silence missing data.
- Each import tariff has inclusive local `from`, exclusive local `until`,
  `confirmed`/`provisional` status, `vat_rate`, all three `components` (`energy`,
  `network`, `levies`), and explicit `discounts` and `fixed_charges` arrays (empty
  is allowed). Periods may be unordered but must not overlap.
- Each export rate has a unique `month` (`YYYY-MM`), status, VAT rate and price.
  The end is the first local date of the following month, including leap years.

Optional `area_m2` must be positive if supplied. Optional `commissioning_date`
is an ISO local date; omission is not zero. These do not establish a certified
area/heat boundary or prove that counters began at commissioning. Do not enter
instalments or outstanding balances. Validation rejects missing prices/fields,
invalid/nonfinite capacities, malformed dates/months, invalid timezone/VAT basis,
overlaps, duplicate months/JSON keys and discounts exceeding variable charges.
Negative prices are not supported by this version and fail explicitly; time-of-use
and tiered tariffs also require a later schema extension, not invented averaging.

## Money, applicability and confidence

All money is in the configured currency, **not cents**. Variable components and
`all_imports` discounts are currency/kWh. `vat_basis` is `net` or `gross` for each
amount; `vat_rate` is a fraction (fictionally `0.2`, not `20`). Net amounts are
multiplied by `1 + vat_rate` once; gross amounts are unchanged. Export VAT is
explicit and independent of import VAT; enter the actual recipient basis.

Only a verified discount applying to **every imported kWh** belongs under
`scope: all_imports`. `restricted_credit` stores an absolute currency amount,
not currency/kWh, and is **never deducted automatically**. It has no assumed
recurrence or billing entitlement. Conditional/tiered bonuses must not be
converted into universal discounts. Omit them if applicability is unknown.
Fixed charges are absolute currency per `month` or `year`, normalized for VAT
but returned separately. They never contribute to avoided-purchase savings.
This is pricing support for estimates, not billing reconciliation.

Gaps/expired rates are allowed because future prices may be unknown. Lookup
carries the latest earlier rate forward only as **provisional**, preserving its
original expiry and source status. It never backfills before the first known
rate. `carry_forward=False` rejects gaps/expiry. Configured provisional rates
remain provisional even inside their stated dates. Telemetry coverage and price
confidence are separate: a confirmed price does not make missing energy valid.

Update the affected dated entry (or split import periods without overlap), set
confirmed status when justified, validate and reload. Corrections must cause
later calculations to recompute the affected historical periods; do not append
an overlapping replacement or reinterpret all history using today's rate.

## Calculation API for issue #4

`from scraper.installation import load_installation, PriceUnavailable`

- `load_installation(path=Path("private/installation.json")) -> Installation`:
  immutable validated snapshot, no import-time I/O and no global cache. Reload
  after edits/corrections. `ConfigurationError` means unavailable configuration,
  never a zero-price result. The existing scraper image copies this module but
  this slice does not change collector startup or expose tariffs as metrics.
- `Installation.import_price(when, *, carry_forward=True) -> PriceLookup`
- `Installation.export_price(when, *, carry_forward=True) -> PriceLookup`
- `when`: a local `date` or timezone-aware `datetime`. Instants are converted
  using the configured zone (including DST). Naive datetimes are rejected.
- `PriceLookup`: `local_date`, `currency`, Decimal `gross_per_kwh`, `status`,
  `source_status`, `effective_from`, **exclusive** `effective_until`,
  `carried_forward`, and the originating immutable `tariff`.
- For import, `tariff.components_gross_per_kwh`,
  `universal_discount_gross_per_kwh`, `restricted_credits_gross` and
  `fixed_charges` preserve the breakdown. Fixed entries expose `period` and
  Decimal `gross_amount`; neither fixed charges nor restricted credits are in
  `gross_per_kwh`.
- `Installation.import_tariffs` / `export_tariffs`: sorted immutable dated
  entries (`start`, `end`, `status`, `gross_per_kwh`). Use these boundaries to
  split period calculations. Do **not** look up once and apply a price to a
  whole period spanning a tariff change. DST days are not always 24 hours;
  financial allocation across missing temporal measurements remains issue #4.
- `Installation.local_date(when)` and `timezone` provide the shared local-date
  interpretation; optional `area_m2` / `commissioning_date` are `None` when absent.

No HTTP/configuration service, custom UI, new database, or tariff exporter was
added. Later runtime calculation code must receive the file through a private
read-only mount readable by its non-root user (not bake it into the image).
Only generated dashboard parameters are needed by Grafana in this slice.

## Configuration-to-dashboard path

`python -m scraper.provision render` validates before writing anything, then
renders the existing dashboard UIDs and provider into
`private/generated/dashboards/`. Source templates remain unchanged. The provider,
datasource references and dashboard IDs are retained. The original device dashboards include installation parameters, not tariff
prices/credits. Issue #4 additionally generates native selectable-period queries
containing effective variable prices in a separate verification dashboard; #5
also resolves those same markers in Home (`home-energy`) and Solar & battery
(`pv-overview`). See [period calculations](period-calculations.md) and the
[Home/Solar interface](home-solar.md). Credits/fixed charges remain excluded.
Authorized Grafana viewers can inspect generated prices, so protect these artifacts
and Grafana/database backups as private inputs. The existing battery
SOC panel (retained Solar dashboard panel **21**) describes the configured usable kWh via
`${usable_battery_kwh}`. It explicitly distinguishes configuration from measured
remaining energy. Heat-pump variables `wohnflaeche` / `inbetriebnahme_ts` are
also supplied; the latter uses local midnight at commissioning. `panel_kwp` is
available for subsequent validated specific-yield calculations. Hidden Grafana
variables are **not a security boundary**; authorized dashboard viewers can
inspect them. Protect Grafana and its database/backups accordingly.

Without private rendering, public templates use browser timezone and `NaN`
(unavailable) installation placeholders, not realistic defaults or zeroes.
Optional omitted values remain `NaN` when rendered. The #3 parameter-only path does not calculate periods. The implemented #4
[query/render interface](period-calculations.md) now supplies selectable-period
panels and date-effective financial queries through the same private rendering.
The #5 calendar extension adds hidden native query variables for timezone-aware
days, previous-month comparisons and observed overnight windows. These refresh
with Grafana time selection, not with manual report generation. Only configuration,
tariff corrections or timezone-rule updates require regeneration. The generated
queries retain the existing POST datasource; they contain private effective
prices and must stay under the ignored private output path. The #7 timezone-query
compaction/qualification fixes reduce fictional overview payloads to roughly
0.94–1.17 MB; size still
grows with tariff history. See [acceptance and deployment gates](acceptance.md).

The output directory is dedicated to generation: obsolete JSON is removed.
Invalid input leaves the previous rendering intact. Individual files replace
atomically, but a full multi-dashboard update is not a cross-file transaction;
render before planned deployment, not while relying on live reload consistency.
New `private/` is mode 0700; generated child directory is 0755 and files 0644 so
the Grafana container can read its bind mount. The private parent limits host
access. Recheck permissions if the directory already existed.

For a **later approved deployment**, use the opt-in override:

```sh
python -m scraper.provision validate
python -m scraper.provision render
docker compose -f docker-compose.yml -f docker-compose.private.yml config --quiet
# Only during separately approved maintenance:
# docker compose -f docker-compose.yml -f docker-compose.private.yml up -d
```

The override replaces the dashboards bind mount and, since #4, adds a private
price-free interval-rule directory for existing Prometheus. Both are read-only
with `create_host_path: false`. It does not change `/data`, retention, ports,
proxy authentication or collector behavior. The existing Prometheus datasource
uses POST for generated queries; no new datasource or plugin is required.
Grafana remains behind the existing trusted-LAN proxy; never add a public port
or Internet route. Base Compose remains usable without installation config;
private commands fail clearly if it is missing. Regenerate after template or
installation changes. Tariff-only changes require regenerating private queries and reopening the
reprovisioned dashboard, not a Grafana restart or a telemetry-history rewrite. No live deployment/restart was performed for this ticket.

## Privacy audit, backup and rollback

Removed tracked installation area/commissioning defaults and certificate-derived
comparison constants; replaced timezone defaults with browser/configured zones.
The storage guard's previously embedded device identifier now comes from the
host's existing `/etc/fstab` rather than another local input file. Its diagnostic
no longer echoes the unexpected UUID; see [storage guard](storage-recovery.md)
for the explicit UUID-source requirement. **The installed guard was not changed.**
Device-address defaults in `.env.example`/code/docs are generic example addresses,
not copied live inputs. Collector/source-contract changes are independently owned.
Historical versions may contain installation details/identifiers. This work does
not rewrite Git history or claim old exposure has been erased; any cleanup needs
separate approval. No private input/live telemetry was read for these tests.

Back up `private/installation.json` and `.env` to owner-controlled encrypted
storage outside the public repository. Generated dashboards are reproducible,
but Grafana database backups also contain rendered private inputs and annotations.
Local ignored copies are not off-device backups. Do not attach archives to issues.

Rollback is a planned provisioning change: retain a private copy of the previous
known-good input/generated directory, then re-render or restore that directory.
Alternatively omit the private override to return to public templates with
unavailable parameters. Never overwrite the Prometheus/Grafana database, extract
archives over `/data`, run `down -v`, or change the storage path for rollback.
Existing metric history is independent of these generated files.

## Repeatable offline verification

```sh
python -m unittest discover -s tests -p 'test_installation.py' -v
python -m unittest discover -s tests -p 'test_storage_guard_offline.py' -v
git check-ignore -q private/installation.json
git check-ignore -q private/generated/dashboards/heatpump.json
git ls-files private .env  # must be empty; paths only, never dump inputs
```

Tests use only the fictional tracked example and temporary directories. They
cover configuration-to-existing-panel text/variables, preserved UIDs/templates,
VAT/discount/fixed-charge separation, transitions/gaps/expiry/corrections, monthly
boundaries/leap years, DST-aware lookup, unknown/invalid/missing inputs, safe CLI
errors, ignore rules and a history sentinel untouched by rendering. Storage
regressions use fake commands, never real mounts. Live datasource queries, browser
rendering, deployed permissions and the privileged storage integration test are
**not run** during #3. Subsequent integrated browser, sanitized read-only live
checks, hardware budgets and deployment/rollback guidance are recorded in
[final acceptance](acceptance.md); no actual deployment was performed.
