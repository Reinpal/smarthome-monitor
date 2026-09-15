# Measurement contracts — issue #2 / spec #1

This is the source-verification handoff to #4–#7, not completed period/financial
calculations or billing-grade verification. Only semantic findings and fictional
examples are recorded. No deployment, restart, data rewrite or history deletion
was performed. Private configuration/provisioning and personal constants belong
to the parallel #3 slice.

## Primary evidence

Sources actually read during implementation:

- **Fronius Solar API V1**, document `42,0410,2012,EN`, revision
  `021-15052025`: [manufacturer PDF](https://www.fronius.com/~/downloads/Solar%20Energy/Operating%20Instructions/42%2C0410%2C2012.pdf).
  §4.1.5 (pp. 12–13): AC power/energy and missing GEN24 day/year counters;
  §4.8.5–4.8.7 (pp. 47–48): visibility, placement, signs and duplicate meter
  counters; §4.9.7 (p. 55): battery current direction and capacity channel names;
  §4.11 (pp. 65–67): powerflow signs, PV AC/DC boundary, instantaneous ratios,
  and energy update frequency.
- **Fronius OpenAPI specification**, linked by the manual §2.2.1:
  [manufacturer download](https://www.fronius.com/QR-link/0025), `solarApiv1.json`,
  `T_Controller`. Like the PDF, it declares numeric capacity fields **without
  a unit**. Neither establishes that they are Ah or usable kWh.
- **STIEBEL ELTRON Modbus TCP/IP software documentation**:
  [manufacturer-hosted manual](https://www.stiebel-eltron.ch/content/dam/ste/ch/de/downloads/kundenservice/smart-home/Modbus/Modbus%20Bedienungsanleitung.pdf),
  §6, block 4 (registers 3501–3516; printed p. 8), and §7 for integral ventilation
  devices. VD heat, NHZ heat and VD electrical energy are distinct; daily energy
  is kWh, cumulative registers have kWh/MWh parts. This is terminology/unit
  evidence, **not** a replacement protocol or proof of this installation's
  electrical metering boundary. The collector reads the HTML's displayed values
  and units; do not apply Modbus register factors or add register parts to HTML.
- **Prometheus** [OTLP ingestion guide](https://prometheus.io/docs/guides/opentelemetry/):
  default `UnderscoreEscapingWithSuffixes`, HTTP receiver and resource labels.
  [Function reference](https://prometheus.io/docs/prometheus/latest/querying/functions/):
  `increase` extrapolates and expects counters; `delta` also extrapolates;
  `idelta` returns the last two gauge samples' difference. None establishes
  observed period coverage or repairs an unknown device reset.

Manufacturer/API guarantees, collector behavior, and private observations are
separate below. A readable endpoint is not proof of sensor calibration or every
field's freshness.

## Private read-only validation outcome

Live source responses were inspected in memory and only allowlisted pass/fail
and semantic limitations were reported. Docker inspection and existing
Prometheus GET queries were read-only. No snapshots, addresses, device IDs,
serials, response bodies, timestamps, readings or screenshots are fixtures.

| Check | Outcome |
|---|---|
| All four Fronius endpoints return measurements | PASS |
| `P_PV`, `P_Load`, `P_Grid`, `P_Akku`, `E_Total` available | PASS |
| Unique enabled/visible grid-interconnection meter; powerflow location grid | PASS |
| Import and export duplicate meter pairs agree | PASS (point-in-time, not an independent meter validation) |
| Single enabled storage; nonzero current and `P_Akku` have opposite signs | PASS; both directions additionally exercised synthetically |
| Capacity channels numeric | PASS; capacity **unit/usable-energy interpretation remains unverified** |
| Powerflow and inverter day/year energy | UNAVAILABLE in source / recent stored history; no zero fallback |
| VD heating/water daily and cumulative heat/electricity, NHZ cumulative heat | PASS: numeric displayed energy with kWh/MWh units |
| Outdoor, flow/return, room/water actual and target, inverter power, VD starts and runtimes | PASS: numeric fields available |
| Expected parsed compressor boolean status | UNAVAILABLE in this check; do not fabricate a cycling timeline |
| Representative raw power/grid/VD cumulative series queryable | PASS |
| Full sampled recent-month coverage / prior-year samples | FAIL: incomplete history, representative internal gaps; annual comparisons unsupported |
| Endpoint collection-health instrumentation | PASS |
| Per-measurement observation/presence metadata in running deployment | NOT YET AVAILABLE; introduced here, not deployed |

History inspection used presence-only five-minute windows sampled every five
minutes over a rolling month, separately checking older boundary samples. The
checks found both incomplete leading history and internal gaps; they do **not**
publish household dates or prove absence of shorter gaps. A five-year retention
setting is not five years of data. Even within stored coverage, the old cached
exports cannot retrospectively prove individual source observations. Full-month
or annual headline comparisons must not be promoted from this history.

## Transport, names and cadence (applies to all rows)

- Fronius nominal poll: **30 s**, four sequential HTTP requests. ISG nominal
  scrape: **300 s**, five HTML pages. Both are configurable; request latency and
  the shared main loop add jitter. Do not assume exact clock-aligned samples.
- OTel observable export: **60 s** by default. It samples the latest collector
  cache, not all 30 s power readings. Prometheus OTLP reception is not a device
  poll. A power integral based on existing stored series has nominal **60 s**
  resolution, not 30 s; actual observation intervals must be checked.
- All numeric instruments remain **observable gauges**, including absolute
  energy, starts and runtimes, preserving existing stored series. The Python
  `is_counter_metric` classification is not a Prometheus monotonic-counter type.
  Device cumulative readings may decrease after reset/replacement/firmware work.
- Dots become underscores; known units gain suffixes (`W` → `_watts`, `Cel` →
  `_celsius`, `%` → `_percent`, `h` → `_hours`). Energy suffixes retain `Wh`,
  `kWh`, `MWh`. No implicit numeric energy conversion occurs in OTLP translation.
  Fictional integration tests verify actual translated names with Prometheus.
- Below, names are **OTel names** before translation unless written as PromQL.
  Unlabelled device series support one grid meter and one storage device. This
  collector is not a multi-device aggregation model.

## Solar, household and battery inputs

| Input / OTel series | Source, units, sign and reset semantics | Boundary, duplicates, availability and allowed use |
|---|---|---|
| PV power `fronius.powerflow.p_pv` | Powerflow `Site.P_PV`; W gauge, positive production, null when unavailable | GEN24/Symo Hybrid **DC PV generator**, SnapInverter AC output (§4.11). Available. Defensible PV period alternative: integrate observed `P_PV` with declared DC boundary, cadence and gaps. No verified pure-PV cumulative counter is exported. |
| Household power `fronius.powerflow.p_load`; `fronius.calculated.load_absolute` | `Site.P_Load`; W gauge, negative consumption, positive load-path generation; calculated consumption `max(0,-P_Load)` | Device-calculated site load, not an independently metered whole-house energy total. Available. Integration is an estimate for the primary-meter site boundary; backup/unmetered circuits and exact equipment inclusion need installation confirmation. Do not turn positive generation into consumption with `abs`. |
| Grid power `fronius.powerflow.p_grid`; `fronius.calculated.grid_import`, `.grid_export` | `Site.P_Grid`; W gauge, positive import, negative export; split `max(0,P_Grid)` / `max(0,-P_Grid)` | Available. Duplicate diagnostic power `fronius.meter.power_real_p_sum` has these signs **only for location 0**. Do not add them. |
| Grid import `fronius.meter.energy_real_abs_plus` | Meter `EnergyReal_WAC_Plus_Absolute`; Wh cumulative absolute gauge | Preferred grid-import period input, conditional on unique location 0. Available. `energy_real_consumed` (`EnergyReal_WAC_Sum_Consumed`) is its duplicate here, not additional energy. |
| Grid export `fronius.meter.energy_real_abs_minus` | Meter `EnergyReal_WAC_Minus_Absolute`; Wh cumulative absolute gauge | Preferred grid-export period input, conditional on location 0. Available. `energy_real_produced` (`EnergyReal_WAC_Sum_Produced`) is its duplicate here. |
| Battery `fronius.powerflow.p_akku`; `fronius.calculated.battery_charge`, `.battery_discharge` | `Site.P_Akku`; W gauge, **negative charge, positive discharge**; split `max(0,-P_Akku)` / `max(0,P_Akku)` | Available. Battery-side device power; not household AC delivered power and not a stored cumulative charge/discharge counter. Integrate only with coverage/loss qualification. |
| SOC `fronius.powerflow.soc`, `fronius.storage.soc` | Powerflow inverter `SOC` / storage controller `StateOfCharge_Relative`; percent gauges | Available, duplicate views sampled at different times; choose one, never add. SOC is not energy. Overnight coverage is observed power contribution/depletion, not future runtime. |
| Storage DC current / voltage `fronius.storage.current_dc`, `.voltage_dc` | Storage controller `Current_DC` A, `Voltage_DC` V gauges; **positive current charges** (§4.9.7), opposite `P_Akku` | Available diagnostic corroboration, not another energy source to add. Multiplying/negating asynchronous readings is not an AC battery energy counter. |
| Capacity `fronius.storage.capacity_maximum_raw`, `.designed_capacity_raw` | `Capacity_Maximum`, `DesignedCapacity`; numeric gauges with **unit unverified** | Replaces unsupported `_Ah` exports with explicitly unitless diagnostic names. Values resembling Wh are not proof. Neither is validated usable capacity. Use separately validated private usable battery kWh (#3); manufacturer confirmation needed before using raw capacities for energy. |
| Inverter `fronius.inverter.pac`, `.total_energy`; site `.e_total` | `PAC` W signed AC gauge; `TOTAL_ENERGY` / `E_Total` Wh cumulative AC gauges. GEN24 totals update about every 5 min (§4.1.5/§4.11) | Available. Hybrid AC output can be supplied by the battery; these do not establish pure-PV generation. Site/device totals overlap for this single inverter: choose one for diagnostics, do not sum. |
| `.powerflow.e_day`, `.e_year`; `.inverter.day_energy`, `.year_energy` | Wh AC gauges since device day/year reset, not lifetime totals | Unavailable here. Manufacturer documents GEN24 nulls. Existing panels now label optional **AC** counters; no invented zero or annualization. |
| `.powerflow.rel_autonomy`, `.rel_self_consumption` | Device instantaneous percent gauges (§4.11), not period ratios | Diagnostic only, never average into energy ratios. `Battery_Mode` is an operating-mode enum, not a reliable direction signal. |

The meter collector now requires exactly one location-0 meter with `Visible=1`
and `Enable=1`. `Visible=0` means incomplete/outdated. Missing validity metadata
or missing/ambiguous placement yields unavailable, not the first arbitrary response entry. A location
change requires a new boundary validation before using cumulative differences.
Storage requires `Enable=1` and rejects multiple controllers rather
than switching silently. Firmware metadata absent from these checks does not
prove physical wiring or measurement validity.

### Derived headline contracts for #4/#5 (not implemented in #2)

- **Household energy:** integrate consumption-only load power. No household
  cumulative meter is currently exported. Classify as estimated/device-calculated
  site demand, not billing-meter household electricity.
- **Self-sufficiency:** `(household energy - grid import) / household energy`,
  only for matching site/period coverage and a supported non-grid-supply boundary.
  Negative/inconsistent balances need qualification/withholding, not clamping
  away evidence. Zero demand is unavailable. Battery energy stored before the
  period can supply demand during it; this is not same-period direct PV use.
- **PV self-consumption:** DC PV minus AC export is not direct household solar
  use. If an onsite-retained estimate is offered, explicitly account for or
  qualify battery inventory change and conversion/storage losses. Do not promote
  an unsupported AC/DC ratio as exact. The old
  `fronius.calculated.self_consumption_power` has been retired for this reason.
- **Battery charge/discharge energy:** power integration, battery-side boundary.
  Neither counter nor AC household contribution is directly available. SOC times
  configured usable capacity is an estimated inventory, not another production
  counter. Assume grid charging unused only as spec #1's stated limitation; this
  collector does not verify that assumption.
- **Specific PV yield:** estimated generator energy / privately configured kWp;
  capacity from #3, not API identity fields. Not weather-normalized efficiency.
- **Financial headlines:** measured grid-import/export differences and qualified
  non-grid household supply feed date-effective private prices (#3/#4). Prices,
  discounts, fixed charges and currency are not telemetry. Avoided purchases plus
  export revenue is benefit; import cost minus export revenue is a distinct cash
  estimate. No financial result until both price and telemetry coverage support it.

## Heating, hot water and comfort inputs

Prefix `heatpump.waermepumpe.` below unless stated otherwise.

| Input / series suffix | Unit/type/reset | Coverage and availability |
|---|---|---|
| `waermemenge.vd_heizen_tag`, `.vd_warmwasser_tag` | kWh gauge since device's daily reset | Available device-reported VD heat by purpose, not metered room-delivered heat. Midnight/reset timezone must be confirmed before calendar allocation. |
| `waermemenge.vd_heizen_summe`, `.vd_warmwasser_summe` | MWh cumulative gauge since counter start/reset | Available; preferred matching-period VD heat differences. Do not add daily values, alternate page balances or individual-device totals to these totals. |
| `leistungsaufnahme.vd_heizen_tag`, `.vd_warmwasser_tag` | kWh daily energy gauges, **not kW power** despite the section name | Available VD electrical energy, matching the purpose/day of VD heat. |
| `leistungsaufnahme.vd_heizen_summe`, `.vd_warmwasser_summe` | MWh cumulative energy gauges | Available; preferred period electricity input with **VD boundary**. Not verified whole-system electricity. |
| `waermemenge.nhz_heizen_summe`, `.nhz_warmwasser_summe` | MWh cumulative auxiliary heat gauges | Available, including valid zero. Manufacturer distinguishes NHZ heat from VD heat. No separate verified NHZ electrical-energy input is exported; do not silently add NHZ heat only to a VD efficiency numerator or assume heat equals electricity. |
| `prozessdaten.inverter_aufnahmeleistung` | kW device gauge | Available inverter-reported power, not verified three-phase whole-system real power. `STROM INVERTER * SPANNUNG INVERTER` is retired: phase count, power factor and AC/DC boundary are not established. |
| `prozessdaten.aussentemperatur`, `.vorlauftemperatur`, `.ruecklauftemperatur` | °C gauges; no reset semantics | Available outdoor/flow/return conditions. `heatpump.calculated.vorlauf_ruecklauf_spread` is their simultaneous °C difference, not a heat meter. |
| `heatpump.anlage.warmwasser.isttemperatur`, `.solltemperatur` | °C actual/target gauges | Available tank/control readings, not tap comfort or delivered heat. |
| `heatpump.anlage.raumtemperatur.isttemperatur_1`, `.solltemperatur_1` | °C actual/target gauges | Available sensor/control-zone context, not whole-house comfort coverage. |
| `starts.verdichter`; `laufzeit.vd_heizen`, `.vd_warmwasser`, `.vd_abtauen` | starts count / h cumulative gauges, resettable | Starts/heating/water runtime available. Differences support qualified cycling summaries, not reconstructed exact on/off cycles. Never annualize a short rate into equipment-life advice. |
| `heatpump.status_anlage.betriebsstatus.verdichter` | Boolean gauge when actually parsed | Not available in the private check. Missing is not off. Do not interpolate a reassuring state timeline from cached exports or starts. |
| `heatpump.energiebilanz.*` | Displayed rolling/summary gauges, not lifetime counters | Alternative overlapping device summaries, not verified historical calendar series. Do not add to VD totals or use rolling labels as evidence of stored history. |

**Equipment limitation:** source names and the manufacturer manual distinguish
VD (compressor-related accounting) and NHZ (electrical auxiliary reheating).
They do not establish calibrated inclusion of fans, pumps, controls, standby,
external heaters, defrost accounting or all installed equipment. Whole-system
power/efficiency and auxiliary electrical contribution remain unsupported without
model-specific boundary evidence or an independent electrical meter.

The existing `heatpump.calculated.cop_*` names are retained for compatibility,
but descriptions/panels now call them **device VD energy ratios** (day or since
counter start), not instantaneous COP, JAZ, or whole-system efficiency. Ratios
normalize each input independently to kWh. Daily raw series normalize to kWh,
cumulative HTML energy to MWh; unknown units/negative energy/boolean placeholders
are unavailable. Other instrument-unit changes are rejected rather than silently
changing an immutable OTel instrument's scale. A zero numerator with positive
input is a valid zero ratio; missing input or zero denominator removes the ratio.
For a selected period, #4 must divide matching **energy differences**, not average
these daily/lifetime ratios. Grid-tariff heat-pump cost is a VD-boundary reference,
not actual grid-attributed expenditure during battery/PV supply.

## Freshness, completeness and usable coverage

Contract version **2** introduces:

- `smarthome_measurement_contract_version = 2`: exporter semantics marker, not
  proof of source freshness or historical validity.
- `smarthome_measurement_last_success_seconds{metric="<OTel name>"}`: collector
  receipt time of that valid numeric field, unchanged by cached exports. Derived
  fields refresh only when all their required inputs are valid in that result.
- `smarthome_measurement_present{metric="<OTel name>"}`: whether the last processed
  result contained a valid value. Missing/invalid fields remove cached values;
  known missing fields report 0. Never-seen measurements have no metadata series.
  ISG transport failures retain last results until their age threshold; Fronius's
  full-cycle result removes failed endpoints. In either case, check age too.

Endpoint health from [monitoring.md](monitoring.md) remains separate. A single
valid zero can make an endpoint successful while another field is missing.
Use both presence and per-field age, as well as exporter/endpoint health. The
source threshold is `max(180 s, 3 * configured collection interval)` (default
Fronius 180 s / ISG 900 s). Receipt time does **not** verify an internally frozen
sensor. Fronius controller/meter `TimeStamp` and validity metadata may help a
future device-specific validation; `Head.Timestamp` is not per-field freshness.

For a single selected installation, this **verification query** returns a real
zero but withholds missing/stale PV. Select matching resource/job labels before
adapting this to multiple exporters. The illustrative threshold is the default;
production calculations must use the configured source threshold.

```promql
fronius_powerflow_p_pv_watts
and on() (smarthome_measurement_present{metric="fronius.powerflow.p_pv"} == 1)
and on() (time() - smarthome_measurement_last_success_seconds{metric="fronius.powerflow.p_pv"} < 180)
```

Do not use `timestamp(power)` to infer a read: periodic OTLP export updates it
without contacting the device. Prometheus's lookback can retain an omitted
series, so omission alone does not replace the presence gate. Never `or vector(0)`
a missing energy input. Existing diagnostics are not yet a full freshness-gated
homeowner experience; #4–#7 must carry these checks into every new headline.

### Period acceptance policy for dependents

1. Use one validated cumulative series per physical boundary. Convert Wh to kWh
   by /1000, MWh to kWh by *1000. Require valid bracketing observations and stable
   units/equipment identity. Positive sample differences are observed increments;
   a decrease flags reset/replacement, **not negative consumption**. Withhold
   exact period totals spanning an unaccounted reset; do not hide lost pre-reset
   energy via a clamp or extrapolating `increase`.
2. Cumulative counters can bridge some collection gaps if reset/boundary
   continuity is defensible. They cannot recover within-gap daily/tariff timing
   or prove no intervening reset. Qualify allocation uncertainty; withhold exact
   financial/calendar splits that require unavailable interval detail.
3. Integrated power is **estimated**, even with complete samples. Use actual
   valid observation/export intervals; do not integrate repeated cached exports
   as fresh observations, bridge intervals beyond the freshness threshold, or
   replace missing/null nighttime production with zero. Declare integration
   method (e.g. trapezoids), nominal 60 s resolution, covered duration, largest
   gap and boundary. Qualified observed-subperiod energy is not a full-period
   total. A requested complete headline must be withheld if gaps cannot be
   bounded defensibly; do not publish an arbitrary accuracy percentage.
4. Local calendar/DST boundaries come from #3, not device reset names. Require
   equivalent elapsed comparison coverage and both endpoints. Prior-year and
   complete-month comparisons are unavailable with the history validated here.
   No short-period annualization, invented past energy or billing-grade claim.
5. Metadata before this change is incomplete. No migration can turn old cached
   scrape timestamps into original field observations. Retroactive calculations
   must explicitly carry **legacy freshness unknown** even where raw counters
   are queryable; unsupported periods remain unavailable.

## Compatibility, verification and follow-up

- No existing raw history is deleted. Raw `P_Akku` signs never changed. Historical
  **calculated** battery charge/discharge values before contract version 2 were
  inverted; recompute from raw `P_Akku` for historical analysis, do not join old
  and corrected derived samples blindly. Positive-load `load_absolute` history
  has the former absolute-value defect; prefer raw `P_Load` and a stated policy.
- Existing heat ratios before version 2 require revalidation of their raw units.
  Known-wrong direct-self-consumption, V*A-power and Ah-capacity series stop
  receiving new observations; retained old series are not deleted or renamed in
  storage. Do not select them as fallback sources. Rollback of code does not
  overwrite TSDB data but **reintroduces old semantics**; revalidate before use.
- Narrow dashboard changes remove retired derived targets, relabel AC counters
  and instantaneous percentages, and remove the false JAZ/authoritative ratio
  thresholds. Personal constants/area benchmarks belong to #3; final cycling,
  comparison, layout and technical threshold work remains #5/#6/#7.

### Reproduce fictional tests (no live Docker)

```sh
python -m venv /tmp/measurement-tests-venv
/tmp/measurement-tests-venv/bin/pip install -r scraper/requirements.txt
/tmp/measurement-tests-venv/bin/python -m unittest discover -s tests -v
# Download/unpack an official Prometheus binary for your OS/architecture first.
PROMETHEUS_TEST_BINARY=/path/to/prometheus \
  /tmp/measurement-tests-venv/bin/python -m unittest discover -s tests -v
git diff --check
```

`tests/test_measurements.py`: fictional HTTP JSON/HTML → existing collection
cycles → real observable SDK data, signs, zero, missing/invalid siblings, meter
placement/duplicates, optional counters, unit changes, resets, NHZ separation,
ratio invalidation and observation timestamps.

`tests/test_measurement_queries.py`: isolated loopback Prometheus and disposable
TSDB, the real SDK's encoded OTLP payload → translated stored series → actual
Grafana target queries plus presence/age queries. Covers charging, discharging,
zero PV, raw resets, mixed kWh/MWh, missing fields and cached export timestamps.
Tested with official Prometheus **3.5.1**. The existing privileged storage/mount
integration test was not run (outside this read-only, source-semantics slice).
The isolated query test bypasses the production LGTM gRPC
relay, not the OTLP encoding or Prometheus translation; no fixtures are sent to
live monitoring. Without `PROMETHEUS_TEST_BINARY`, this test explicitly skips.

`tests/test_measurement_dashboards.py`: JSON and narrowly corrected source claims,
not full provisioning/browser acceptance.

### Repeat private validation without publishing telemetry

1. Load endpoint credentials/addresses privately, never print environment files,
   remote URLs, inspect output, raw HTTP errors or response bodies. Use read-only
   GETs to the four paths in `Config.fronius_endpoints` and ISG pages. Keep raw
   values in memory. Compare only the field, unit, meter-placement, visibility,
   duplicate and sign predicates above; output allowlisted pass/fail only.
2. Use existing Prometheus GET APIs. For each required series, evaluate
   `sum(present_over_time(SERIES[5m])) or vector(0)` via `query_range` at 5 min
   steps over the period, and inspect boundary samples. **Aggregate before the
   fallback** so label sets match; otherwise zeros become a different series
   and can conceal gaps. Trim leading absence only when separately recording
   incomplete history, then check internal gaps. This is stored presence, not
   device-read coverage. Keep exact dates/durations private.
3. After a separately authorized deployment, check version 2, per-measurement
   presence and observation-age queries in private Explore. Do not regard this
   document's read-only checks as verification of a deployment that has not run.
4. Record only pass/fail and anonymized limitations. Full Grafana provisioning,
   UI layout, tariffs, DST calculations and equipment-specific metrology remain
   dependent-ticket acceptance. No push, merge or issue closing is part of this
   implementation.
