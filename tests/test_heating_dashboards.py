"""Heating/diagnostics acceptance with fictional data and disposable local tools."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import unittest

from scraper.installation import load_installation
from scraper.provision import render_dashboard
import test_period_promql as period_fixtures
from test_period_promql import source_inputs

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('heatpump', 'heatpump-diagnostics', 'solar-battery-diagnostics')


def templates():
    return [json.loads((ROOT / f'grafana/provisioning/dashboards/{name}.json').read_text()) for name in NAMES]


def panel(dashboard, id):
    return next(p for p in dashboard['panels'] if p['id'] == id)


def expression(target, seconds=120):
    return target['expr'].replace('${__from}', '0').replace('${__to}', str(seconds * 1000)).replace('${__range_s}', str(seconds))


def fixture(raw, metric, *, values='10 11 12 13', observed='0 60 120 180', present='1 1 1 1', endpoint='waermepumpe', collector='isg', threshold='900 900 900 900'):
    attrs = '{collector=' + json.dumps(collector) + ',source=' + json.dumps(endpoint) + '}'
    return [{'series': name, 'values': value} for name, value in (
        (raw, values), ('smarthome_measurement_present{metric=' + json.dumps(metric) + '}', present),
        ('smarthome_measurement_last_success_seconds{metric=' + json.dumps(metric) + '}', observed),
        ('smarthome_collection_last_success_seconds' + attrs, '0 60 120 180'),
        ('smarthome_collection_stale_after_seconds' + attrs, threshold),
        ('smarthome_measurement_contract_version', '2 2 2 2'))]


class HeatingTemplateTests(unittest.TestCase):
    def test_public_markers_resolve_to_real_instant_queries_with_per_metric_context(self):
        original = templates()[0]
        rendered = render_dashboard(original, load_installation(ROOT / 'installation.example.json'))
        self.assertEqual(original['uid'], 'heatpump-overview')
        self.assertEqual(original['time'], {'from': 'now/d', 'to': 'now'})
        self.assertFalse(original.get('timepicker', {}).get('hidden', False))
        self.assertEqual(original['refresh'], '1m')
        keys = set()
        for p in original['panels']:
            markers = [t for t in p.get('targets', []) if 'periodMetric' in t]
            if not markers or p['id'] not in range(40,49):
                continue
            markers += panel(original, p['id'] + 100)['targets']
            keys.add(markers[0]['periodMetric'])
            self.assertEqual({t['periodField'] for t in markers} & {'metrics', 'coverage', 'complete', 'observed_until'},
                             {'metrics', 'coverage', 'complete', 'observed_until'})
            actual = panel(rendered, p['id'])
            for t in actual['targets']:
                self.assertTrue(t['instant'])
                self.assertFalse(t['range'])
                self.assertIn('${__to}', t['expr'])
                self.assertNotIn('periodMetric', t)
            self.assertEqual(actual['fieldConfig']['defaults']['noValue'], 'Unavailable')
        self.assertEqual(keys, {'heating_electricity', 'heating_heat', 'heating_ratio', 'water_electricity', 'water_heat',
                               'water_ratio', 'heating_aux_heat', 'water_aux_heat', 'heatpump_reference_cost'})
        cost = panel(rendered, 48)
        self.assertEqual(cost['fieldConfig']['defaults']['unit'], 'currencyEUR')
        self.assertNotIn('configured_currency', json.dumps(cost))
        self.assertIn('NOT actual attributed spending', cost['description'])

    def test_diagnostic_markers_preserve_range_modes_and_shared_freshness_policy(self):
        for original in templates():
            rendered = render_dashboard(original, load_installation(ROOT / 'installation.example.json'))
            for p in original['panels']:
                for marker, target in zip(p.get('targets', []), panel(rendered, p['id']).get('targets', [])):
                    self.assertNotIn('smarthome_measurement_contract_version', marker.get('expr', ''),
                                     'Freshness policy must not remain expanded in templates')
                    if 'diagnosticQuery' not in marker:
                        continue
                    self.assertNotIn('expr', marker)
                    self.assertNotIn('diagnosticQuery', target)
                    for field in ('instant', 'range', 'legendFormat', 'refId'):
                        self.assertEqual(marker[field], target[field])
                    for policy in ('smarthome_measurement_contract_version', 'smarthome_measurement_present',
                                   'smarthome_measurement_last_success_seconds', 'smarthome_collection_last_success_seconds',
                                   'smarthome_collection_stale_after_seconds', 'count(', 'timestamp('):
                        self.assertIn(policy, target['expr'])
                    self.assertNotIn('__fresh_', target['expr'])

    def test_calendar_markers_use_complete_daily_energy_and_matching_comparisons(self):
        dashboard = templates()[0]
        daily = panel(dashboard, 70)
        self.assertEqual(daily['type'], 'barchart')
        self.assertEqual({t['calendarMetric'] for t in daily['targets']}, {'heating_electricity','water_electricity'})
        self.assertTrue(all(t['calendarMode'] == 'daily' for t in daily['targets']))
        self.assertEqual(daily['options']['stacking'], 'none')
        self.assertEqual([t['id'] for t in daily['transformations']],
                         ['labelsToFields', 'merge', 'organize', 'convertFieldType', 'sortBy', 'formatTime'])
        self.assertIn('Current/partial', daily['description'])
        for id, key in ((71,'heatpump_electricity'),(72,'heatpump_ratio')):
            targets = panel(dashboard,id)['targets']
            self.assertEqual({t['calendarMode'] for t in targets}, {'current','previous'})
            self.assertTrue(all(t['calendarMetric'] == key for t in targets))
        self.assertTrue(dashboard['homeNavigation'])

    def test_navigation_annotations_layout_and_no_retired_claims(self):
        for dashboard in (render_dashboard(t, load_installation(ROOT / 'installation.example.json')) for t in templates()):
            self.assertEqual(dashboard['annotations']['list'][0]['datasource']['uid'], '-- Grafana --')
            self.assertEqual(dashboard['annotations']['list'][0]['type'], 'dashboard')
            ids = [p['id'] for p in dashboard['panels']]
            self.assertEqual(len(ids), len(set(ids)))
            for link in dashboard['links']:
                self.assertTrue(link['includeTime'])
                self.assertTrue(link['url'].startswith('/d/'))
            for p in dashboard['panels']:
                g = p['gridPos']
                self.assertLessEqual(g['x'] + g['w'], 24)
                for q in dashboard['panels']:
                    if p['id'] >= q['id']:
                        continue
                    h = q['gridPos']
                    self.assertFalse(g['x'] < h['x']+h['w'] and h['x'] < g['x']+g['w'] and g['y'] < h['y']+h['h'] and h['y'] < g['y']+g['h'], 'Overlapping panels')
                for t in p.get('targets', []):
                    expr = t.get('expr', '')
                    for forbidden in ('increase(', '* 365', 'inbetriebnahme', 'wohnflaeche', 'or vector(0)', 'inverter_leistung_berechnet', 'capacity_maximum_Ah'):
                        self.assertNotIn(forbidden, expr)
                    if expr.startswith('(heatpump_') or expr.startswith('(fronius_'):
                        self.assertIn('smarthome_measurement_present', expr)
                        self.assertIn('smarthome_measurement_last_success_seconds', expr)
                        self.assertIn('smarthome_collection_stale_after_seconds', expr)
                        self.assertIn('count(', expr)
                defaults = p.get('fieldConfig', {}).get('defaults', {})
                if defaults:
                    self.assertEqual(len(defaults['thresholds']['steps']), 1)
                    self.assertEqual(defaults['custom']['spanNulls'], False)
        hp = templates()[1]
        self.assertEqual(panel(hp, 10)['fieldConfig']['defaults']['unit'], 'pressurebar')
        self.assertIn('megwatth', json.dumps(panel(hp, 8)['fieldConfig']))
        self.assertIn('flowlpm', json.dumps(panel(hp, 13)['fieldConfig']))
        self.assertIn('amp', json.dumps(panel(hp, 14)['fieldConfig']))
        self.assertIn('NHZ', panel(hp, 5)['description'])


@unittest.skipUnless(os.environ.get('PROMTOOL_TEST_BINARY'), 'set PROMTOOL_TEST_BINARY for isolated PromQL acceptance')
class HeatingPromQLTests(unittest.TestCase):
    run_promtool = period_fixtures.InteractiveQueryTests.run_promtool
    config = period_fixtures.InteractiveQueryTests.config

    def test_every_actual_panel_query_parses_and_empty_history_withholds(self):
        checks = []
        for template in templates():
            for p in render_dashboard(template, load_installation(ROOT / 'installation.example.json'))['panels']:
                for t in p.get('targets', []):
                    # Calendar targets are owned by #5's parallel shared extension.
                    # Its variable-dependent queries have dedicated integration tests.
                    if 'calendarMetric' in t or '${cal_' in t.get('expr', ''):
                        continue
                    checks.append({'expr': expression(t), 'eval_time': '2m', 'exp_samples': []})
        self.run_promtool([{'interval': '60s', 'input_series': [], 'promql_expr_test': checks}], rules=False)

    def test_actual_period_panels_units_ratios_zero_auxiliary_and_partial_periods(self):
        rendered = render_dashboard(templates()[0], self.config())
        data = source_inputs()
        for item in data:
            if item['series'].startswith('heatpump_waermepumpe_waermemenge_vd_'):
                item['values'] = '1 1.003 1.006 1.009'
            if item['series'].startswith('heatpump_waermepumpe_waermemenge_nhz_'):
                item['values'] = '0 0 0 0'
        checks = []
        for id, value in ((40, 2), (41, 6), (42, 3), (43, 2), (44, 6), (45, 3), (46, 0), (47, 0), (48, 4*.228)):
            checks.append({'expr': expression(panel(rendered, id)['targets'][0]), 'eval_time': '2m', 'exp_samples': [{'labels': '{}', 'value': value}]})
        for id, index, value in ((40, 0, 2), (140, 0, 120), (140, 1, 0), (140, 2, 120000)):
            checks.append({'expr': expression(panel(rendered, id)['targets'][index], 150), 'eval_time': '150s', 'exp_samples': [{'labels': '{}', 'value': value}]})
        self.run_promtool([{'interval': '60s', 'input_series': data, 'promql_expr_test': checks}])
        variants = []
        for mode in ('missing_aux', 'zero_input', 'reset', 'stale'):
            mutated = deepcopy(data)
            for item in mutated:
                if mode == 'missing_aux' and 'smarthome_measurement_present' in item['series'] and '.nhz_' in item['series']:
                    item['values'] = '1 0 1 1'
                if mode == 'zero_input' and item['series'].startswith('heatpump_waermepumpe_leistungsaufnahme_vd_'):
                    item['values'] = '1 1 1 1'
                if mode == 'reset' and item['series'].startswith('heatpump_waermepumpe_leistungsaufnahme_vd_'):
                    item['values'] = '1 .5 .6 .7'
                if mode == 'stale' and item['series'].startswith('smarthome_measurement_last_success_seconds'):
                    item['values'] = '0 0 0 0'
            ids = (46,47) if mode == 'missing_aux' else (42,45) if mode == 'zero_input' else (40,42,48)
            end = 960 if mode == 'stale' else 120
            variants.append({'interval': '60s', 'input_series': mutated, 'promql_expr_test': [
                {'expr': expression(panel(rendered, id)['targets'][0], end), 'eval_time': str(end)+'s', 'exp_samples': []} for id in ids]})
        self.run_promtool(variants)

    def test_diagnostic_metadata_gates_and_labels_survive_shared_rendering(self):
        target = panel(render_dashboard(templates()[0], self.config()), 1)['targets'][2]
        raw = 'heatpump_waermepumpe_prozessdaten_aussentemperatur_celsius'
        metric = 'heatpump.waermepumpe.prozessdaten.aussentemperatur'
        cases = []
        for mode in ('valid', 'version', 'threshold', 'future_field', 'stale_endpoint', 'duplicate_metadata', 'timestamp_skew'):
            data = fixture(raw + '{job="fictional"}', metric, values='-2 -2 -2 -2')
            if mode == 'duplicate_metadata':
                data.append({'series': 'smarthome_measurement_present{metric="' + metric + '",job="other"}', 'values': '1 1 1 1'})
            for item in data:
                name = item['series']
                if mode == 'version' and name == 'smarthome_measurement_contract_version':
                    item['values'] = '1 1 1 1'
                if mode == 'threshold' and name.startswith('smarthome_collection_stale_after'):
                    item['values'] = '90000 90000 90000 90000'
                if mode == 'future_field' and name.startswith('smarthome_measurement_last_success'):
                    item['values'] = '1000 1000 1000 1000'
                if mode == 'stale_endpoint' and name.startswith('smarthome_collection_last_success'):
                    item['values'] = '-900 -900 -900 -900'
                if mode == 'timestamp_skew' and name.startswith('smarthome_measurement_present'):
                    item['values'] = '1 1 _ _'
            cases.append({'interval': '60s', 'input_series': data, 'promql_expr_test': [
                {'expr': expression(target), 'eval_time': '2m', 'exp_samples':
                 [{'labels': '{job="fictional"}', 'value': -2}] if mode == 'valid' else []}]})
        solar = render_dashboard(templates()[2], self.config())
        for id, name, expected in ((31, 'powerflow.e_day', 2000), (32, 'powerflow.e_year', 2000),
                                   (33, 'powerflow.e_total', 2), (42, 'meter.energy_real_consumed', 2000),
                                   (43, 'meter.energy_real_produced', 2000)):
            for present in ('1 1 1 1', '1 0 0 0'):
                data = fixture('fronius_' + name.replace('.', '_') + '_Wh', 'fronius.' + name,
                               values='2000000 2000000 2000000 2000000', present=present,
                               collector='fronius', endpoint=name.split('.')[0])
                cases.append({'interval': '60s', 'input_series': data, 'promql_expr_test': [
                    {'expr': expression(panel(solar, id)['targets'][0]), 'eval_time': '2m',
                     'exp_samples': [{'labels': '{}', 'value': expected}] if present == '1 1 1 1' else []}]})
        self.run_promtool(cases, rules=False)

    def test_cycling_counts_all_aligned_samples_in_non_aligned_selections(self):
        cycling = panel(render_dashboard(templates()[0], self.config()), 60)['targets'][0]
        cases = []
        for start, end, expected in ((0, 120, 1), (0, 150, 1), (30, 150, 1),
                                     (30, 180, 2), (61, 180, 1), (0, 179, 1)):
            for mode in ('fresh', 'gap', 'reset', 'stale'):
                data = fixture('heatpump_waermepumpe_starts_verdichter',
                               'heatpump.waermepumpe.starts.verdichter',
                               values='10 11 1 2' if mode == 'reset' else '10 11 12 13',
                               present='1 0 0 1' if mode == 'gap' else '1 1 1 1',
                               observed='0 0 0 0' if mode == 'stale' else '0 60 120 180',
                               threshold='120 120 120 120')
                expr = cycling['expr'].replace('${__from}', str(start * 1000)).replace('${__to}', str(end * 1000)).replace('${__range_s}', str(end - start))
                cases.append({'interval': '60s', 'input_series': data, 'promql_expr_test': [
                    {'expr': expr, 'eval_time': f'{end}s', 'exp_samples':
                     [{'labels': '{}', 'value': expected}] if mode == 'fresh' or (mode == 'reset' and start >= 60) else []}]})
        # Grafana rounds the range macro to whole seconds even with millisecond
        # picker endpoints. Count the grid actually queried, not rounded bounds.
        data = fixture('heatpump_waermepumpe_starts_verdichter', 'heatpump.waermepumpe.starts.verdichter')
        expr = cycling['expr'].replace('${__from}', '59900').replace('${__to}', '180000').replace('${__range_s}', '120')
        cases.append({'interval': '60s', 'input_series': data, 'promql_expr_test': [
            {'expr': expr, 'eval_time': '180s', 'exp_samples': [{'labels': '{}', 'value': 1}]}]})
        self.run_promtool(cases, rules=False)

    def test_field_freshness_zero_missing_cached_ambiguous_and_cycling_resets(self):
        main, hp, solar = [render_dashboard(t, self.config()) for t in templates()]
        cases = []
        for raw, name, t, collector, endpoint in (
            ('heatpump_waermepumpe_prozessdaten_aussentemperatur_celsius', 'heatpump.waermepumpe.prozessdaten.aussentemperatur', panel(main, 1)['targets'][2], 'isg','waermepumpe'),
            ('heatpump_status_anlage_betriebsstatus_verdichter', 'heatpump.status_anlage.betriebsstatus.verdichter', panel(hp, 21)['targets'][0], 'isg','status_anlage'),
            ('fronius_storage_current_dc_amperes', 'fronius.storage.current_dc', panel(solar,26)['targets'][0], 'fronius','storage')):
            for mode in ('zero', 'missing', 'cached', 'ambiguous'):
                data = fixture(raw, name, values='0 0 0 0', present='1 0 0 0' if mode=='missing' else '1 1 1 1', observed='0 0 0 0' if mode=='cached' else '0 60 120 180', endpoint=endpoint, collector=collector, threshold='120 120 120 120')
                if mode == 'ambiguous': data.append({'series':raw+'{job="other-fictional-exporter"}', 'values':'0 0 0 0'})
                cases.append({'interval':'60s','input_series':data,'promql_expr_test':[{'expr':expression(t),'eval_time':'2m','exp_samples':[{'labels':'{}','value':0}] if mode=='zero' else []}]})
        cycling = panel(main,60)['targets'][0]
        for values, present, expected in (('10 11 12 13','1 1 1 1',1),('10 10 10 10','1 1 1 1',0),('10 11 1 2','1 1 1 1',None),('10 11 12 13','1 0 1 1',None)):
            data=fixture('heatpump_waermepumpe_starts_verdichter','heatpump.waermepumpe.starts.verdichter',values=values,present=present)
            cases.append({'interval':'60s','input_series':data,'promql_expr_test':[{'expr':expression(cycling),'eval_time':'2m','exp_samples':[] if expected is None else [{'labels':'{}','value':expected}]}]})
        self.run_promtool(cases,rules=False)


if __name__ == '__main__':
    unittest.main()
