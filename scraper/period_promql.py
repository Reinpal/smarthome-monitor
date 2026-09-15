"""Interactive Grafana interface v1 over freshness-gated interval recording rules.

Recording rules contain telemetry semantics only. Prices are query-time private
constants, so a corrected tariff reprices retained intervals without rewriting
Prometheus history. No new process, service, exporter, or financial TSDB series.
"""
from datetime import datetime, time
import json
import re

from scraper.periods import SOURCES

INTERVAL = 60
PREFIX = 'smarthome_period_v1_'


def recording_rules(*, labels=None):
    """Generate one 60s rule group. Scope exactly one exporter; ambiguity withholds.

    Nine records describe each accepted source-observation interval. Source
    counter units normalize to kWh; power remains signed W for exact clipped
    linear integration in queries. SOC remains percent. No price is recorded.
    """
    labels = dict(labels or {})
    from scraper.period_query import PrometheusHistory
    # Reuse selector validation/escaping without making an HTTP request.
    selector = PrometheusHistory('http://unused.invalid', labels=labels)._selector
    rules = []
    for key, source in SOURCES.items():
        def raw(name, shift=0, **attrs):
            return selector(name, attrs) + (' offset 60s' if shift else '')
        def state(shift):
            fields = {
                'value': raw(source.prom_name, shift),
                'observed': raw('smarthome_measurement_last_success_seconds', shift, metric=source.name),
                'present': raw('smarthome_measurement_present', shift, metric=source.name),
                'version': raw('smarthome_measurement_contract_version', shift),
                'health': raw('smarthome_collection_last_success_seconds', shift, collector=source.collector, source=source.endpoint),
                'threshold': raw('smarthome_collection_stale_after_seconds', shift, collector=source.collector, source=source.endpoint),
            }
            v = {k: f'sum({expr})' for k, expr in fields.items()}
            checks = [f'(count({expr}) == 1)' for expr in fields.values()]
            checks += [f'({v["present"]} == 1)', f'({v["version"]} == 2)',
                       f'({v["threshold"]} > 0)', f'({v["threshold"]} <= 86400)']
            for name in ('observed', 'health'):
                age = f'(time() - {shift} - {v[name]})'
                checks += [f'({age} >= 0)', f'({age} < {v["threshold"]})']
            for expr in fields.values():
                checks += [f'(abs(sum(timestamp({expr})) - sum(timestamp({fields["value"]}))) <= 2)']
            if source.kind in ('counter', 'power', 'soc'):
                checks.append(f'({v["value"]} >= 0)')
            if source.kind == 'soc':
                checks.append(f'({v["value"]} <= 100)')
            # NaN and infinity must not survive as legitimate source observations.
            checks += [f'(abs({v["value"]}) < Inf)']
            return v, checks
        previous, before = state(60)
        current, after = state(0)
        duration = f'({current["observed"]} - {previous["observed"]})'
        checks = before + after + [f'({duration} > 0)', f'({duration} <= {previous["threshold"]})',
                                   f'({duration} <= {current["threshold"]})']
        if source.kind == 'counter':
            checks.append(f'({current["value"]} >= {previous["value"]})')
        valid = ' and '.join(checks)
        factor = .001 if source.unit == 'Wh' else 1000 if source.unit == 'MWh' else -1 if source.kind in ('load', 'charge') else 1
        rate = f'(({current["value"]} - {previous["value"]}) * {factor} / {duration})'
        zero = f'(({previous["observed"]} - ({previous["value"]} * {factor}) / {rate}) and ({rate} != 0)) or ({previous["observed"]} and ({rate} == 0))'
        left, right = f'({previous["value"]} * {factor})', f'({current["value"]} * {factor})'
        shape = f'(vector(1) and ({left} >= 0) and ({right} >= 0)) or (vector(-1) and ({left} <= 0) and ({right} <= 0)) or (vector(0) and (({left} * {right}) < 0))'
        valid_until = f'(({current["observed"]} + {current["health"]} - abs({current["observed"]} - {current["health"]})) / 2 + {current["threshold"]})'
        for field, expr in (('start_seconds', previous['observed']), ('end_seconds', current['observed']),
                            ('left', f'({previous["value"]} * {factor})'), ('right', f'({current["value"]} * {factor})'),
                            ('rate', rate), ('zero_seconds', zero), ('shape', shape),
                            ('stale_after_seconds', current['threshold']), ('valid_until_seconds', valid_until)):
            rules.append({'record': PREFIX + field, 'labels': {'metric': key}, 'expr': f'({expr}) and ({valid})'})
    return {'groups': [{'name': 'smarthome-period-intervals-v1', 'interval': '60s', 'rules': rules}]}


def _utc(day, installation):
    return datetime.combine(day, time.min, installation.timezone).timestamp()


def _spans(tariffs, installation):
    spans = []
    for index, tariff in enumerate(tariffs):
        start, end = _utc(tariff.start, installation), _utc(tariff.end, installation)
        spans.append((start, end, float(tariff.gross_per_kwh), tariff.status))
        next_start = _utc(tariffs[index + 1].start, installation) if index + 1 < len(tariffs) else None
        if next_start is None or end < next_start:
            spans.append((end, next_start, float(tariff.gross_per_kwh), 'provisional'))
    return spans


class PeriodQueries:
    """Stable query generator for #5/#6. All targets are native Prometheus PromQL.

    `metrics[key]` is an instant-query expression. `coverage[key]` is seconds of
    accepted observation coverage; `complete[key]` is 1 only for the entire
    selected range. `price_status[key]`: 1 confirmed / 2 provisional (not data
    confidence). Always render coverage and the observed-prefix qualification.

    Defaults use Grafana millisecond from/to and elapsed-second range macros.
    Literal bounds/range can be supplied for deterministic query tests or a
    separately selected local calendar window. No date/reset-name heuristics.
    """
    def __init__(self, installation, *, start='(${__from} / 1000)', end='(${__to} / 1000)', window='${__range_s}s', at=None, lookahead=None):
        self.installation, self.start, self.end, self.window = installation, start, end, window
        # Optional numeric (or Grafana numeric-variable) historical evaluation.
        # PromQL @ does not accept arithmetic expressions. Defaults stay unchanged.
        self.at = '' if at is None else f' @ {at}'
        if lookahead is not None:
            if at is None:
                raise ValueError('period: lookahead requires a pinned evaluation')
            self.at += f' offset -{lookahead}'
        self.metrics, self.coverage, self.complete, self.price_status, self.observed_until = {}, {}, {}, {}, {}
        self._energy, self._accepted, self._endpoints = {}, {}, {}
        for key, source in SOURCES.items():
            energy = self._sum(self._piece(key, source))
            covered = self._sum(self._duration(key))
            observed = f'sum(max_over_time(({self._record(key, "end_seconds")})[{window}:60s]{self.at}))'
            endpoint = f'(vector({end}) - clamp_min(vector({end}) - {observed}, 0))'
            valid_until = f'sum(last_over_time({self._record(key, "valid_until_seconds")}[{window}]{self.at}))'
            # Only a continuous observed prefix may be a qualified current headline.
            # Leading/internal holes and stale tails are withheld, never zero-filled.
            accepted = (f'({covered} >= ({endpoint} - {start} - 0.001)) and '
                        f'({endpoint} > {start}) and (vector({end}) < {valid_until})')
            self._energy[key], self._accepted[key], self._endpoints[key] = energy, accepted, endpoint
            self.metrics[key] = f'(({energy}) and ({accepted})) < Inf > -Inf'
            self.coverage[key] = covered
            self.complete[key] = f'({covered} >= bool ({end} - {start} - 0.001))'
            self.observed_until[key] = endpoint
        self.metrics['battery_inventory_change'] = f'({self.metrics["soc"]}) * {float(installation.usable_battery_kwh) / 100}'
        self._alias('battery_inventory_change', 'soc')
        self.metrics['specific_yield'] = f'({self.metrics["pv"]}) / {float(installation.panel_kwp)}'
        self._alias('specific_yield', 'pv')
        self._combine('self_sufficiency', 'household', 'grid_import', '(a - b) / a * 100', positive_denominator=True)
        # The validated contract does not establish an AC/DC loss model.
        self.metrics['solar_self_consumption'] = 'vector(0) != 0'
        for quantity in ('electricity', 'heat', 'aux_heat'):
            self._combine('heatpump_' + quantity, 'heating_' + quantity, 'water_' + quantity, 'a + b')
        for purpose in ('heating', 'water', 'heatpump'):
            self._combine(purpose + '_ratio', purpose + '_heat', purpose + '_electricity', 'a / b')
        for key, energy_key, kind in (('import_cost', 'grid_import', 'import'), ('export_revenue', 'grid_export', 'export'),
                                       ('household_grid_baseline', 'household', 'import'),
                                       ('heating_reference_cost', 'heating_electricity', 'import'),
                                       ('water_reference_cost', 'water_electricity', 'import')):
            spans = _spans(getattr(installation, kind + '_tariffs'), installation)
            terms = [self._sum(f'({self._piece(energy_key, SOURCES[energy_key], low, high)}) * {rate}') for low, high, rate, _ in spans]
            value = '(' + ' + '.join(terms) + ')'
            # No backfill before first known price, no fixed/conditional credits.
            self.metrics[key] = f'{value} and ({self._accepted[energy_key]}) and (vector({start}) >= {spans[0][0]})'
            self._alias(key, energy_key)
            provisional = [f'((vector({end}) > bool {low}) * (vector({start}) < bool {high}))' if high is not None
                           else f'(vector({end}) > bool {low})' for low, high, _, status in spans if status == 'provisional']
            self.price_status[key] = f'(1 + clamp_max(({" + ".join(provisional)}), 1)) and (vector({start}) >= {spans[0][0]})'
        self._combine('avoided_cost', 'household_grid_baseline', 'import_cost', 'a - b')
        self._combine('solar_battery_benefit', 'avoided_cost', 'export_revenue', 'a + b')
        self._combine('net_grid_cost', 'import_cost', 'export_revenue', 'a - b', allow_negative=True)
        self._combine('heatpump_reference_cost', 'heating_reference_cost', 'water_reference_cost', 'a + b')
        self.units = {key: 'kwatth' for key in self.metrics}
        self.units.update({key: 'currency' + installation.currency for key in self.price_status})
        self.units.update({key: 'suffix: kWh/kWh' for key in self.metrics if key.endswith('_ratio')})
        self.units.update(soc='percent', self_sufficiency='percent', solar_self_consumption='percent', specific_yield='suffix: kWh/kWp')

    def _alias(self, key, source):
        for mapping in (self.coverage, self.complete, self.observed_until):
            mapping[key] = mapping[source]

    def _combine(self, key, left, right, operation, *, allow_negative=False, positive_denominator=False):
        a, b = self.metrics[left], self.metrics[right]
        expr = re.sub(r'\b[ab]\b', lambda m: f'({a if m[0] == "a" else b})', operation)
        # Do not combine differing observed periods even when each input is valid.
        matching = f'({self.observed_until[left]} == {self.observed_until[right]}) and ({self.coverage[left]} == {self.coverage[right]})'
        value = f'({expr}) and ({matching})'
        if positive_denominator:
            value += f' and (({a}) > 0)'
        if not allow_negative:
            value = f'({value}) >= 0'
        # Infinite ratios and NaN are absent, not reassuring values.
        self.metrics[key] = f'({value}) < Inf > -Inf'
        self._alias(key, left)
        if left in self.price_status and right in self.price_status:
            self.price_status[key] = f'clamp_max(({self.price_status[left]}) + ({self.price_status[right]}) - 1, 2)'

    def _record(self, key, field):
        return PREFIX + field + '{metric=' + json.dumps(key) + '}'

    def _bounds(self, key, low=None, high=None):
        start, end = self._record(key, 'start_seconds'), self._record(key, 'end_seconds')
        a = f'clamp_min({start}, {self.start})'
        b = f'clamp_max({end}, {self.end})'
        if low is not None:
            a = f'clamp_min({a}, {low})'
        if high is not None:
            b = f'clamp_max({b}, {high})'
        return start, end, a, b

    def _duration(self, key, low=None, high=None):
        _, _, a, b = self._bounds(key, low, high)
        return f'clamp_min(({b}) - ({a}), 0)'

    def _piece(self, key, source, low=None, high=None):
        start, end, a, b = self._bounds(key, low, high)
        duration = self._duration(key, low, high)
        left, slope = self._record(key, 'left'), self._record(key, 'rate')
        if source.kind in ('counter', 'soc'):
            return f'({slope} * {duration})'
        zero = self._record(key, 'zero_seconds')
        # F(t) = slope/2 * positive(sign(slope)*(t-zero))². This exact
        # positive-part primitive handles both signs without double-counting a
        # crossing. Recorded slope/zero keep monthly queries within sample budgets.
        primitive = f'({slope} / 2 * (clamp_min(sgn({slope}) * (({b}) - {zero}), 0)^2 - clamp_min(sgn({slope}) * (({a}) - {zero}), 0)^2))'
        shape = self._record(key, 'shape')
        # Use the zero-crossing primitive ONLY when zero lies in this interval.
        # Nearly flat positive power otherwise has a far-away zero and would lose
        # precision subtracting squared absolute timestamps.
        crossing = f'({primitive} and ({shape} == 0))'
        positive = f'((({left} + {slope} * ((({a}) + ({b})) / 2 - {start})) * {duration}) and ({shape} == 1))'
        negative = f'((0 * {shape}) and ({shape} == -1))'
        return f'((({positive} or {negative} or {crossing}) * ({duration} > bool 0)) / 3600000)'

    def _sum(self, expression):
        # Recording evaluations need not be UTC-minute-aligned. Consume each at
        # most once on the 60s subquery grid; stale lookback cannot duplicate energy.
        metric = re.search(r'metric="([^"]+)"', expression).group(1)
        marker = self._record(metric, 'end_seconds')
        # Expression retains metric labels, so timestamp freshness joins by metric.
        fresh = f'((time() - timestamp({marker})) < 60)'
        return f'sum(sum_over_time((({expression}) and on(metric) {fresh})[{self.window}:60s]{self.at}))'

    def target(self, key, *, ref_id='A', field='metrics'):
        """Native Grafana instant target; pass field=coverage/complete/price_status."""
        expression = getattr(self, field)[key]
        if field == 'observed_until':
            expression = f'({expression}) * 1000'  # Grafana date units expect epoch milliseconds.
        return {'refId': ref_id, 'expr': expression, 'instant': True, 'range': False,
                'editorMode': 'code', 'legendFormat': key, 'format': 'time_series',
                'datasource': {'type': 'prometheus', 'uid': 'prometheus'}}


def render_period_targets(dashboard, installation):
    """Resolve public target markers recursively during existing private rendering.

    Template target: {"refId": "A", "periodMetric": "household",
                      "periodField": "metrics"}. No prices enter tracked JSON.
    Missing configuration leaves the public target without an expression (no data).
    Never turn these instant targets into query_range/rolling totals implicitly.
    """
    dashboard = json.loads(json.dumps(dashboard))
    queries = PeriodQueries(installation)
    from scraper.home_queries import render_home_extensions
    dashboard = render_home_extensions(dashboard, installation)
    def panels(items):
        for panel in items:
            for index, target in enumerate(panel.get('targets', [])):
                if 'periodMetric' in target:
                    key = target['periodMetric']
                    resolved = queries.target(key, ref_id=target.get('refId', 'A'), field=target.get('periodField', 'metrics'))
                    if target.get('periodQualification'):
                        value, complete = resolved['expr'], queries.complete[key]
                        # Attach a one-valued label vector; do not evaluate the
                        # expensive energy/financial expression in BOTH branches.
                        # Missing values remain absent even if coverage survives.
                        qualification = ' or '.join(
                            f'label_replace(vector(1) and ({complete} == {flag}), "coverage", "{label}", "", "")'
                            for flag, label in ((1, 'Full period'), (0, 'Observed prefix')))
                        resolved['expr'] = f'({value}) * on() group_left(coverage) ({qualification})'
                        resolved['legendFormat'] = '{{coverage}}'
                    for label, value in target.get('periodLabels', {}).items():
                        resolved['expr'] = f'label_replace(({resolved["expr"]}), {json.dumps(label)}, {json.dumps(value)}, "", "")'
                    panel['targets'][index] = resolved
            defaults = panel.get('fieldConfig', {}).get('defaults', {})
            if defaults.get('unit') == 'configured_currency':
                defaults['unit'] = 'currency' + installation.currency
            panels(panel.get('panels', []))
    panels(dashboard.get('panels', []))
    return dashboard


def render_interactive_dashboard(installation):
    """Minimal real selectable-period surface, not the final #5/#6 layout."""
    queries = PeriodQueries(installation)
    panels = []
    definitions = (
        ('household', 'Household — observed site demand', 'kwatth'),
        ('grid_import', 'Grid import — observed meter energy', 'kwatth'),
        ('self_sufficiency', 'Self-sufficiency — observed non-grid supply', 'percent'),
        ('solar_battery_benefit', 'Solar/battery estimated benefit', 'currency' + installation.currency),
        ('heatpump_electricity', 'Heat-pump VD electricity', 'kwatth'),
        ('heatpump_ratio', 'Matching-period VD energy ratio', 'suffix: kWh/kWh'),
    )
    for i, (key, title, unit) in enumerate(definitions):
        panels.append({'id': i + 1, 'title': title, 'type': 'stat',
                       'gridPos': {'x': (i % 3) * 8, 'y': (i // 3) * 5, 'w': 8, 'h': 5},
                       'description': 'Selected local period, continuous observed prefix only; inspect coverage below. Estimated power uses actual observed intervals at nominal 60s recording/export resolution. Missing/stale/internal gaps and unequal input periods withhold. Not billing grade; unused grid charging assumed. VD excludes unverified NHZ electricity and whole-system loads.',
                       'targets': [queries.target(key)],
                       'fieldConfig': {'defaults': {'unit': unit, 'decimals': 3, 'noValue': 'Unavailable'}, 'overrides': []},
                       'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'values': False}}})
    for i, field in enumerate(('coverage', 'complete', 'price_status', 'observed_until'), 7):
        keys = [k for k, _, _ in definitions if k in getattr(queries, field)]
        panels.append({'id': i, 'title': {'coverage': 'Covered seconds — not assumed full selected period', 'complete': 'Full period? 1 yes / 0 observed prefix only',
                                       'price_status': 'Price confidence: 1 confirmed / 2 provisional', 'observed_until': 'Observed through — required headline endpoint'}[field],
                       'type': 'table', 'gridPos': {'x': 0, 'y': 10 + (i - 7) * 8, 'w': 24, 'h': 8},
                       'targets': [queries.target(k, ref_id=chr(65 + n), field=field) for n, k in enumerate(keys)],
                       'fieldConfig': {'defaults': {'noValue': 'Unavailable', 'unit': 'dateTimeAsIso' if field == 'observed_until' else 'suffix: s' if field == 'coverage' else 'none'}, 'overrides': []},
                       'options': {'showHeader': True}})
    for index, (title, keys) in enumerate((
        ('Solar and battery calculations', ('pv', 'grid_export', 'battery_charge', 'battery_discharge', 'battery_inventory_change', 'specific_yield', 'solar_self_consumption')),
        ('Financial breakdown — not a bill', ('import_cost', 'export_revenue', 'avoided_cost', 'solar_battery_benefit', 'net_grid_cost')),
        ('VD purposes and separate NHZ heat', ('heating_electricity', 'water_electricity', 'heating_heat', 'water_heat', 'heating_ratio', 'water_ratio', 'heating_aux_heat', 'water_aux_heat', 'heating_reference_cost', 'water_reference_cost', 'heatpump_reference_cost')),
    ), 12):
        panels.append({'id': index, 'title': title, 'type': 'table', 'gridPos': {'x': 0, 'y': 52 + (index - 12) * 10, 'w': 24, 'h': 10},
                       'targets': [queries.target(k, ref_id=chr(65 + n)) for n, k in enumerate(keys)],
                       'fieldConfig': {'defaults': {'noValue': 'Unavailable'},
                                       'overrides': [{'matcher': {'id': 'byName', 'options': k},
                                                      'properties': [{'id': 'unit', 'value': queries.units[k]}]} for k in keys]},
                       'options': {'showHeader': True}})
    panels.append({'id': 11, 'title': 'Definitions and limitations', 'type': 'text', 'gridPos': {'x': 0, 'y': 42, 'w': 24, 'h': 10},
                   'options': {'mode': 'markdown', 'content': 'Interactive calculations use retained v1 interval recording rules, not fixed reports. Select local calendar days/months; refresh is enabled. Current headlines are qualified continuous observed prefixes, not extrapolated totals. Leading/internal gaps or a stale trailing boundary withhold values. Full-period and covered-seconds panels are mandatory context. PV is DC generator integration, not hybrid inverter AC output. Solar self-consumption is unavailable: AC/DC losses and storage inventory do not establish direct household solar use. VD efficiency excludes NHZ heat from its numerator. No supported year-over-year history or automatic annualization. Financial targets separate avoided purchases + export revenue from import cost − export revenue; fixed charges and restricted credits are excluded. Regenerate private queries after tariff corrections; old financial values are not stored. See docs/period-calculations.md.'}})
    return {'uid': 'period-selectable', 'title': 'Period calculations — selectable verification', 'schemaVersion': 39,
            'version': 1, 'timezone': str(installation.timezone), 'refresh': '1m', 'time': {'from': 'now/M', 'to': 'now'},
            'tags': ['energy', 'verification'], 'panels': panels}
