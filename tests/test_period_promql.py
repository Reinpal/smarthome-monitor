"""Actual generated recording rules and Grafana expressions, exercised by promtool.

No live Prometheus writes. All sources/prices/history are fictional; test files
and tool output stay in disposable directories. Requires official promtool.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

from scraper.installation import parse_installation
from scraper.periods import SOURCES, calendar_period
from scraper.period_promql import PREFIX, PeriodQueries, recording_rules, render_interactive_dashboard
from test_period_calculations import configuration


PROMTOOL = os.environ.get('PROMTOOL_TEST_BINARY')


def source_inputs(*, missing=False, reset=False, cached=False):
    series = {'smarthome_measurement_contract_version': '2 2 2 2'}
    for key, source in SOURCES.items():
        if source.kind == 'counter':
            value = '1000 1010 1020 1030' if source.unit == 'Wh' else '1 1.001 1.002 1.003'
            if reset and key == 'grid_import':
                value = '1000 1010 5 15'
        elif source.kind == 'soc':
            value = '60 59 58 57'
        elif source.kind == 'power':
            value = '1500 1500 1500 1500'
        elif source.kind == 'load':
            value = '-1000 -1000 -1000 -1000'
        else:
            value = '-300 300 300 300'
        series[source.prom_name] = value
        series['smarthome_measurement_last_success_seconds{metric=' + json.dumps(source.name) + '}'] = '0 0 0 0' if cached else '0 60 120 180'
        series['smarthome_measurement_present{metric=' + json.dumps(source.name) + '}'] = '1 0 1 1' if missing and key == 'household' else '1 1 1 1'
        attrs = '{collector=' + json.dumps(source.collector) + ',source=' + json.dumps(source.endpoint) + '}'
        series['smarthome_collection_last_success_seconds' + attrs] = '0 60 120 180'
        series['smarthome_collection_stale_after_seconds' + attrs] = '900 900 900 900' if source.collector == 'isg' else '180 180 180 180'
    return [{'series': key, 'values': value} for key, value in series.items()]


@unittest.skipUnless(PROMTOOL, 'set PROMTOOL_TEST_BINARY for actual recording/PromQL tests')
class InteractiveQueryTests(unittest.TestCase):
    def run_promtool(self, tests, *, rules=True):
        with tempfile.TemporaryDirectory(prefix='fictional-period-rules-') as directory:
            root = Path(directory)
            (root / 'rules.json').write_text(json.dumps(recording_rules()))
            # PromQL uses binary floats; cumulative differences lose low bits.
            # Assert nanounit agreement, not promtool's one-mantissa-bit default.
            tests = deepcopy(tests)
            for case in tests:
                for check in case['promql_expr_test']:
                    if rules:
                        # These fixtures timestamp exports exactly on rule ticks.
                        # The production offset intentionally defers their facts
                        # until the next tick. Query after that tick, keeping the
                        # requested energy bounds unchanged.
                        amount, unit = re.fullmatch(r'(\d+)([sm])', check['eval_time']).groups()
                        check['eval_time'] = f'{int(amount) * (60 if unit == "m" else 1) + 60}s'
                    check['expr'] = 'round((' + check['expr'] + '), 0.000000001)'
                    for sample in check['exp_samples']:
                        sample['value'] = round(sample['value'], 9)
            test = {'rule_files': [str(root / 'rules.json')] if rules else [], 'evaluation_interval': '60s',
                    'fuzzy_compare': True, 'tests': tests}
            (root / 'tests.json').write_text(json.dumps(test))
            result = subprocess.run([PROMTOOL, 'test', 'rules', str(root / 'tests.json')], capture_output=True, text=True, timeout=120)
            diagnostic = '\n'.join(line[-350:] for line in (result.stdout + result.stderr).splitlines()
                                   if 'exp:' in line or 'got:' in line or 'err:' in line or 'error' in line.lower())
            self.assertEqual(result.returncode, 0, diagnostic)

    def config(self):
        raw = configuration()
        raw['installation']['timezone'] = 'UTC'
        raw['import_tariffs'] = [raw['import_tariffs'][0]]
        raw['import_tariffs'][0].update({'from': '1970-01-01', 'until': '1971-01-01'})
        raw['export_tariffs'] = [raw['export_tariffs'][0]]
        raw['export_tariffs'][0]['month'] = '1970-01'
        return parse_installation(raw)

    def test_real_source_rules_to_selectable_headline_queries(self):
        q = PeriodQueries(self.config(), start='0', end='120', window='120s')
        expected = {'household': 1/30, 'pv': .05, 'grid_import': .02, 'grid_export': .02,
                    'self_sufficiency': 40, 'battery_charge': .00125, 'battery_discharge': .00625,
                    'battery_inventory_change': -.23, 'heatpump_electricity': 4, 'heatpump_heat': 4,
                    'heatpump_ratio': 1, 'import_cost': .02 * .228, 'export_revenue': .02 * .08,
                    'avoided_cost': (1/30 - .02) * .228, 'net_grid_cost': .02 * (.228 - .08),
                    'solar_battery_benefit': (1/30 - .02) * .228 + .02 * .08}
        expressions = [{'expr': q.metrics[k], 'eval_time': '2m', 'exp_samples': [{'labels': '{}', 'value': v}]} for k, v in expected.items()]
        expressions += [{'expr': q.coverage['household'], 'eval_time': '2m', 'exp_samples': [{'labels': '{}', 'value': 120}]},
                        {'expr': q.complete['household'], 'eval_time': '2m', 'exp_samples': [{'labels': '{}', 'value': 1}]},
                        {'expr': q.price_status['import_cost'], 'eval_time': '2m', 'exp_samples': [{'labels': '{}', 'value': 1}]}]
        self.run_promtool([{'interval': '60s', 'input_series': source_inputs(), 'promql_expr_test': expressions}])

    def test_completed_source_queries_preserve_full_coverage_and_validity_gates(self):
        cases = []
        for start, end, mode in ((0, 120, 'full'), (0, 150, 'partial_tail'),
                                 (-60, 120, 'leading'), (0, 180, 'missing'),
                                 (0, 180, 'reset'), (0, 180, 'cached'), (0, 960, 'stale')):
            q = PeriodQueries(self.config(), start=str(start), end=str(end), window=f'{end-start}s')
            data = source_inputs(**{mode: True}) if mode in ('missing', 'reset', 'cached') else source_inputs()
            key = 'grid_import' if mode == 'reset' else 'household'
            expected = [{'labels': '{}', 'value': 1/30}] if mode == 'full' else []
            cases.append({'interval': '60s', 'input_series': data, 'promql_expr_test': [
                {'expr': expr, 'eval_time': f'{end}s', 'exp_samples': expected} for expr in
                (q.completed_sources[key], f'({q.metrics[key]}) and ({q.complete[key]} == 1)')]})
        self.run_promtool(cases)

    def test_real_rule_resets_missing_cached_and_unequal_periods_withhold(self):
        q = PeriodQueries(self.config(), start='0', end='180', window='180s')
        tests = []
        for inputs, keys in ((source_inputs(reset=True), ('grid_import', 'import_cost', 'solar_battery_benefit')),
                             (source_inputs(missing=True), ('household', 'avoided_cost', 'self_sufficiency')),
                             (source_inputs(cached=True), ('pv', 'grid_import', 'solar_battery_benefit'))):
            tests.append({'interval': '60s', 'input_series': inputs,
                          'promql_expr_test': [{'expr': q.metrics[k], 'eval_time': '3m', 'exp_samples': []} for k in keys]})
        self.run_promtool(tests)

    def test_missing_metadata_ambiguous_source_and_expired_endpoint_fail_closed(self):
        q = PeriodQueries(self.config(), start='0', end='180', window='180s')
        missing = [item for item in source_inputs() if not item['series'].startswith('smarthome_measurement_last_success_seconds')]
        ambiguous = source_inputs() + [{'series': 'fronius_powerflow_p_load_watts{job="fictional-second-exporter"}', 'values': '-1000 -1000 -1000 -1000'}]
        unhealthy = source_inputs()
        for item in unhealthy:
            if item['series'].startswith('smarthome_collection_last_success_seconds'):
                item['values'] = '0 0 0 0'
        self.run_promtool([{'interval': '60s', 'input_series': data, 'promql_expr_test': [
            {'expr': q.metrics['household'], 'eval_time': '3m', 'exp_samples': []},
            {'expr': q.metrics['avoided_cost'], 'eval_time': '3m', 'exp_samples': []},
        ]} for data in (missing, ambiguous, unhealthy)])

    def test_live_prefix_zero_negative_cash_and_nearly_flat_power(self):
        q = PeriodQueries(self.config(), start='0', end='150', window='150s')
        checks = [{'expr': q.metrics['household'], 'eval_time': '150s', 'exp_samples': [{'labels': '{}', 'value': 1/30}]},
                  {'expr': q.complete['household'], 'eval_time': '150s', 'exp_samples': [{'labels': '{}', 'value': 0}]},
                  {'expr': q.coverage['household'], 'eval_time': '150s', 'exp_samples': [{'labels': '{}', 'value': 120}]}]
        self.run_promtool([{'interval': '60s', 'input_series': source_inputs(), 'promql_expr_test': checks}])
        q = PeriodQueries(self.config(), start='0', end='120', window='120s')
        data = source_inputs()
        for item in data:
            if item['series'] == 'fronius_powerflow_p_pv_watts':
                item['values'] = '0 0 0 0'
            if item['series'] == 'fronius_powerflow_p_load_watts':
                item['values'] = '-1000 -1000.0000000001 -1000.0000000002 -1000.0000000003'
            if item['series'] == 'fronius_meter_energy_real_abs_minus_Wh':
                item['values'] = '1000 1100 1200 1300'
        self.run_promtool([{'interval': '60s', 'input_series': data, 'promql_expr_test': [
            {'expr': q.metrics['pv'], 'eval_time': '2m', 'exp_samples': [{'labels': '{}', 'value': 0}]},
            {'expr': q.metrics['household'], 'eval_time': '2m', 'exp_samples': [{'labels': '{}', 'value': 1/30}]},
            {'expr': q.metrics['net_grid_cost'], 'eval_time': '2m', 'exp_samples': [{'labels': '{}', 'value': .02*.228 - .2*.08}]},
        ]}])

    def interval_inputs(self, start, seconds, *, missing_index=None):
        # Existing accepted interval facts, sampled by the 60s group. Absolute
        # observation times are fictional; rule-gating provenance is tested above.
        count = int(seconds / 60)
        series = []
        for key, source in SOURCES.items():
            for field in ('start_seconds', 'end_seconds', 'left', 'right', 'rate', 'zero_seconds', 'shape', 'stale_after_seconds', 'valid_until_seconds'):
                values = ['_']
                for i in range(1, count + 1):
                    if i == missing_index:
                        values.append('stale')
                        continue
                    if field == 'start_seconds':
                        value = start + (i - 1) * 60
                    elif field == 'end_seconds':
                        value = start + i * 60
                    elif field == 'stale_after_seconds':
                        value = 180
                    elif field == 'valid_until_seconds':
                        value = start + i * 60 + 180
                    elif field == 'rate':
                        value = (.4 if key == 'grid_import' else .2) / 3600 if source.kind == 'counter' else -1/36000 if source.kind == 'soc' else 0
                    elif field == 'zero_seconds':
                        value = start
                    elif field == 'shape':
                        value = 1
                    elif source.kind == 'counter':
                        value = 100 + (i - (field == 'left')) / 60 * (.4 if key == 'grid_import' else .2)
                    elif source.kind == 'soc':
                        value = 60 - (i - (field == 'left')) / 600
                    else:
                        value = 1000  # Normalized positive power after sign handling.
                    values.append(str(value))
                series.append({'series': PREFIX + field + '{metric=' + json.dumps(key) + '}', 'values': ' '.join(values)})
        return series

    def test_actual_panel_promql_tariff_change_correction_provisional_and_fixed_exclusion(self):
        raw = configuration()
        installation = parse_installation(raw)
        start = datetime(2025, 6, 30, 21, 59, tzinfo=timezone.utc).timestamp()
        q = PeriodQueries(installation, start=str(start), end=str(start + 120), window='120s')
        corrected = deepcopy(raw)
        corrected['import_tariffs'][1]['components']['energy']['amount'] = .20
        corrected['import_tariffs'][1]['status'] = 'confirmed'
        corrected['import_tariffs'][0]['fixed_charges'][0]['amount'] = 9000
        corrected['import_tariffs'][0]['discounts'][1]['amount'] = 8000
        updated = PeriodQueries(parse_installation(corrected), start=str(start), end=str(start + 120), window='120s')
        expressions = []
        for queries, expected, status in ((q, .4/60*(.228+.24), 2), (updated, .4/60*(.228+.29), 1)):
            expressions.extend([
                {'expr': queries.target('import_cost')['expr'], 'eval_time': '2m', 'exp_samples': [{'labels': '{}', 'value': expected}]},
                {'expr': queries.target('import_cost', field='price_status')['expr'], 'eval_time': '2m', 'exp_samples': [{'labels': '{}', 'value': status}]},
                {'expr': queries.target('avoided_cost')['expr'], 'eval_time': '2m', 'exp_samples': [{'labels': '{}', 'value': expected * 1.5}]},
            ])
        self.run_promtool([{'interval': '60s', 'input_series': self.interval_inputs(start, 120), 'promql_expr_test': expressions}], rules=False)

    def test_selectable_local_dst_days_and_internal_gap_coverage(self):
        installation = parse_installation(configuration())
        tests = []
        for start_day, end_day, hours in ((date(2025, 3, 30), date(2025, 3, 31), 23),
                                          (date(2025, 10, 26), date(2025, 10, 27), 25)):
            period = calendar_period(start_day, end_day, installation)
            q = PeriodQueries(installation, start=str(period.start.timestamp()), end=str(period.end.timestamp()), window=f'{int(period.seconds)}s')
            data = self.interval_inputs(period.start.timestamp(), period.seconds)
            evaluation = f'{int(period.seconds)}s'
            # promtool hardcodes a 10,000-sample query budget (server default 50M).
            # Full 25h power and 31-day queries run against the real server/browser
            # in test_period_queries; keep this unit test within promtool's budget.
            power_checks = [{'expr': q.target('household')['expr'], 'eval_time': evaluation,
                             'exp_samples': [{'labels': '{}', 'value': hours}]}] if hours == 23 else []
            tests.append({'interval': '60s', 'input_series': data, 'promql_expr_test': power_checks + [
                {'expr': q.target('household', field='complete')['expr'], 'eval_time': evaluation, 'exp_samples': [{'labels': '{}', 'value': 1}]},
                {'expr': q.target('import_cost')['expr'], 'eval_time': evaluation,
                 'exp_samples': [{'labels': '{}', 'value': hours * .4 * (.228 if hours == 23 else .24)}]},
            ]})
        # One missing internal interval is never excused by an otherwise fresh end.
        q = PeriodQueries(installation, start='0', end='180', window='180s')
        tests.append({'interval': '60s', 'input_series': self.interval_inputs(0, 180, missing_index=2),
                      'promql_expr_test': [{'expr': q.metrics['household'], 'eval_time': '3m', 'exp_samples': []},
                                          {'expr': q.coverage['household'], 'eval_time': '3m', 'exp_samples': [{'labels': '{}', 'value': 120}]}]})
        self.run_promtool(tests, rules=False)

    def test_every_generated_interactive_panel_expression_parses(self):
        installation = self.config()
        dashboard = render_interactive_dashboard(installation)
        self.assertEqual(dashboard['time'], {'from': 'now/M', 'to': 'now'})
        self.assertEqual(dashboard['refresh'], '1m')
        expressions = []
        for panel in dashboard['panels']:
            for target in panel.get('targets', []):
                expression = target['expr'].replace('${__from}', '0').replace('${__to}', '120000').replace('${__range_s}', '120')
                # Empty history: no source should become a zero; price confidence
                # is independent and remains queryable without telemetry.
                if panel['id'] == 9 or target.get('legendFormat') == 'solar_self_consumption':
                    continue
                expressions.append({'expr': expression, 'eval_time': '2m', 'exp_samples': []})
        self.run_promtool([{'interval': '60s', 'input_series': [], 'promql_expr_test': expressions}], rules=False)


if __name__ == '__main__':
    unittest.main()
