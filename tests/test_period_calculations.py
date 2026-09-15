"""Fictional public query/result/panel acceptance; never reads private inputs."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scraper.installation import parse_installation
from scraper.periods import History, Observation, Period, SOURCES, calendar_period
from scraper.period_query import query_report
from scraper.period_render import render_dashboard, render_panel
from scraper.provision import render


def configuration():
    return json.loads(Path('installation.example.json').read_text())


class FictionalHistory:
    """Constant invented powers and cumulative gauges, fresh 60-second reads."""
    def __init__(self, transform=None):
        self.transform = transform

    def read(self, period):
        histories = {}
        start, end = period.start.timestamp(), period.end.timestamp()
        times = [start + n * 60 for n in range(int(period.seconds // 60) + 1)]
        if times[-1] < end:
            times.append(end)
        for key, source in SOURCES.items():
            def value(at):
                hours = (at - start) / 3600
                if source.kind == 'counter':
                    kw = {'grid_import': 0.4, 'grid_export': 0.2,
                          'heating_electricity': 0.1, 'water_electricity': 0.05,
                          'heating_heat': 0.3, 'water_heat': 0.1,
                          'heating_aux_heat': 0, 'water_aux_heat': 0}[key]
                    scale = 1000 if source.unit == 'Wh' else 0.001
                    return (100 + kw * hours) * scale
                if source.kind == 'load':
                    return -1000
                if source.kind == 'power':
                    return 1500
                if source.kind == 'soc':
                    return 60 - hours * 0.01
                return 100  # Positive raw battery power discharges.
            histories[key] = History(tuple(Observation(at, value(at), 900 if source.collector == 'isg' else 180) for at in times))
        return self.transform(histories, period) if self.transform else histories


class PeriodAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.raw = configuration()
        self.installation = parse_installation(self.raw)
        self.period = calendar_period(date(2025, 1, 10), date(2025, 1, 11), self.installation)

    def report(self, reader=None, period=None, installation=None, compare=False):
        return query_report(reader or FictionalHistory(), installation or self.installation, period or self.period, compare=compare)

    def test_query_to_native_panels_energy_money_and_boundaries(self):
        report = self.report()
        m = report['metrics']
        expected = {'household': 24, 'pv': 36, 'grid_import': 9.6, 'grid_export': 4.8,
                    'self_sufficiency': 60, 'battery_charge': 0, 'battery_discharge': 2.4,
                    'battery_inventory_change': -0.0276, 'specific_yield': 36 / 8.4,
                    'import_cost': 9.6 * .228, 'export_revenue': 4.8 * .08,
                    'avoided_cost': 14.4 * .228, 'solar_battery_benefit': 14.4 * .228 + 4.8 * .08,
                    'net_grid_cost': 9.6 * .228 - 4.8 * .08,
                    'heatpump_electricity': 3.6, 'heatpump_heat': 9.6,
                    'heatpump_ratio': 9.6 / 3.6, 'heating_ratio': 3, 'water_ratio': 2,
                    'heatpump_aux_heat': 0, 'heatpump_reference_cost': 3.6 * .228}
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertAlmostEqual(m[key]['value'], value)
        self.assertIsNone(m['solar_self_consumption']['value'])
        self.assertEqual(m['grid_import']['status'], 'device-reported')
        self.assertEqual(m['pv']['status'], 'estimated')
        self.assertEqual(m['solar_battery_benefit']['tariff_status'], 'confirmed')
        dashboard = render_dashboard(report)
        self.assertEqual(dashboard['uid'], 'period-verification')
        self.assertTrue(dashboard['timepicker']['hidden'])
        text = '\n'.join(p['options']['content'] for p in dashboard['panels'])
        for key in expected:
            self.assertIn(key, text)
        self.assertIn('24.0000 kWh', text)
        self.assertIn('confirmed', text)
        self.assertIn('unsupported AC/DC loss boundary', text)
        self.assertIn('not actual attributed expenditure', text)
        json.dumps(dashboard, allow_nan=False)

    def test_dst_days_and_tariff_transition_expiry_correction_reach_panels(self):
        for start, end, hours in [(date(2025, 3, 30), date(2025, 3, 31), 23),
                                  (date(2025, 10, 26), date(2025, 10, 27), 25)]:
            period = calendar_period(start, end, self.installation)
            report = self.report(period=period)
            self.assertAlmostEqual(report['metrics']['household']['value'], hours)
            self.assertEqual(report['daily'][0]['seconds'], hours * 3600)
            self.assertIn(str(hours * 3600), render_dashboard(report)['panels'][4]['options']['content'])
        period = calendar_period(date(2025, 6, 30), date(2025, 7, 2), self.installation)
        report = self.report(period=period)
        self.assertAlmostEqual(report['metrics']['import_cost']['value'], 9.6 * (.228 + .24))
        self.assertEqual(report['metrics']['import_cost']['tariff_status'], 'provisional')
        self.assertEqual(report['metrics']['export_revenue']['tariff_status'], 'provisional')
        corrected = deepcopy(self.raw)
        corrected['import_tariffs'][1]['status'] = 'confirmed'
        corrected['import_tariffs'][1]['components']['energy']['amount'] = .20
        updated = self.report(period=period, installation=parse_installation(corrected))
        self.assertAlmostEqual(updated['metrics']['import_cost']['value'], 9.6 * (.228 + .29))
        before, after = (render_panel(r, ['import_cost'], panel_id=1, title='Cost')['options']['content'] for r in (report, updated))
        self.assertNotEqual(before, after)
        self.assertIn('confirmed', after)
        # The same-demand baseline is variable prices only, unaffected by fixed/conditional credits.
        corrected['import_tariffs'][0]['fixed_charges'][0]['amount'] = 9000
        corrected['import_tariffs'][0]['discounts'][1]['amount'] = 8000
        self.assertEqual(self.report(installation=parse_installation(corrected))['metrics']['avoided_cost']['value'],
                         self.report()['metrics']['avoided_cost']['value'])
        unknown = self.report(period=calendar_period(date(2024, 12, 1), date(2024, 12, 2), self.installation))
        self.assertIsNone(unknown['metrics']['import_cost']['value'])
        self.assertEqual(unknown['metrics']['import_cost']['tariff_status'], 'unavailable')
        expired = self.report(period=calendar_period(date(2026, 1, 1), date(2026, 1, 2), self.installation))
        self.assertEqual(expired['metrics']['import_cost']['tariff_status'], 'provisional')

    def test_reset_gap_missing_zero_and_inconsistent_balance(self):
        def reset(h, p):
            points = h['grid_import'].observations
            h['grid_import'] = History(tuple(replace(o, value=o.value - (100000 if i > 5 else 0)) for i, o in enumerate(points)))
            return h
        m = self.report(FictionalHistory(reset))['metrics']
        for key in ('grid_import', 'self_sufficiency', 'import_cost', 'avoided_cost', 'solar_battery_benefit'):
            self.assertIsNone(m[key]['value'])
        self.assertIsNotNone(m['pv']['value'])
        self.assertTrue(any('reset' in note for note in m['grid_import']['reasons']))
        def gaps(h, p):
            for key in ('pv', 'household'):
                h[key] = replace(h[key], observations=h[key].observations[:5] + h[key].observations[20:])
            h.pop('water_aux_heat')
            return h
        report = self.report(FictionalHistory(gaps))
        m = report['metrics']
        self.assertIsNone(m['pv']['value'])
        self.assertGreater(m['pv']['largest_gap_seconds'], 180)
        self.assertGreater(m['pv']['observed_value'], 0)
        self.assertIsNone(m['heatpump_aux_heat']['value'])
        self.assertEqual(m['heating_aux_heat']['value'], 0)
        self.assertIn('Observed subtotal only', render_dashboard(report)['panels'][0]['options']['content'])
        def zero(h, p):
            for key in ('pv', 'household', 'grid_import', 'heating_electricity', 'water_electricity'):
                h[key] = History(tuple(replace(o, value=0) for o in h[key].observations))
            return h
        m = self.report(FictionalHistory(zero))['metrics']
        self.assertEqual(m['pv']['value'], 0)
        self.assertIsNone(m['self_sufficiency']['value'])
        self.assertIsNone(m['heatpump_ratio']['value'])
        self.assertEqual(m['import_cost']['value'], 0)
        def inconsistent(h, p):
            h['household'] = History(tuple(replace(o, value=50) for o in h['household'].observations))
            return h
        m = self.report(FictionalHistory(inconsistent))['metrics']
        self.assertEqual(m['household']['value'], 0)  # Positive load-path generation isn't demand.
        self.assertIsNone(m['avoided_cost']['value'])

    def test_signed_zero_crossing_integration_not_split_endpoint_trapezoids(self):
        period = Period(self.period.start, self.period.start + timedelta(minutes=1))
        def crossing(h, p):
            for key in ('battery_charge', 'battery_discharge'):
                a, b = h[key].observations
                h[key] = History((replace(a, value=-600), replace(b, value=600)))
            return h
        m = self.report(FictionalHistory(crossing), period)['metrics']
        self.assertAlmostEqual(m['battery_charge']['value'], .0025)
        self.assertAlmostEqual(m['battery_discharge']['value'], .0025)

    def test_equal_elapsed_short_month_and_missing_previous_history(self):
        period = calendar_period(date(2025, 3, 1), date(2025, 4, 1), self.installation)
        report = self.report(period=period, compare=True)
        c = report['comparison']
        self.assertEqual(c['current']['seconds'], 28 * 86400)
        self.assertEqual(c['previous']['seconds'], c['current']['seconds'])
        self.assertEqual(report['seconds'], 31 * 86400 - 3600)
        self.assertEqual(report['year_over_year']['status'], 'unavailable')
        def missing_prior(h, p):
            return {} if p.start.month == 2 else h
        report = self.report(FictionalHistory(missing_prior), period, compare=True)
        self.assertIsNone(report['comparison']['previous']['metrics']['household']['value'])
        self.assertIn('Unavailable', render_dashboard(report)['panels'][5]['options']['content'])
        # Partial MTD compares elapsed seconds, not a full previous month.
        partial = Period(period.start, period.start + timedelta(hours=37))
        c = self.report(period=partial, compare=True)['comparison']
        self.assertEqual(c['previous']['seconds'], 37 * 3600)

    def test_subinterval_boundary_allocation_and_missing_brackets(self):
        period = Period(self.period.start + timedelta(seconds=15), self.period.start + timedelta(seconds=105))
        base = Period(self.period.start, self.period.start + timedelta(seconds=120))
        class Reader:
            def read(self, p):
                return FictionalHistory().read(base)
        m = self.report(Reader(), period)['metrics']
        self.assertAlmostEqual(m['household']['value'], .025)
        self.assertAlmostEqual(m['grid_import']['value'], .01)
        self.assertEqual(m['grid_import']['status'], 'estimated')
        class Missing:
            def read(self, p):
                return {k: replace(v, observations=v.observations[1:]) for k, v in Reader().read(p).items()}
        self.assertIsNone(self.report(Missing(), period)['metrics']['import_cost']['value'])

    def test_public_dashboard_target_markers_resolve_only_during_private_render(self):
        from scraper.provision import render_dashboard as render_private
        template = {'uid': 'fictional-home', 'panels': [
            {'id': 1, 'targets': [{'refId': 'A', 'periodMetric': 'household'}]},
            {'id': 2, 'panels': [{'id': 3, 'targets': [{'refId': 'B', 'periodMetric': 'avoided_cost', 'periodField': 'metrics'}],
                                 'fieldConfig': {'defaults': {'unit': 'configured_currency'}}}]},
        ]}
        original = deepcopy(template)
        rendered = render_private(template, self.installation)
        self.assertEqual(template, original)
        self.assertEqual(rendered['uid'], 'fictional-home')
        target = rendered['panels'][0]['targets'][0]
        self.assertIn('${__from}', target['expr'])
        self.assertTrue(target['instant'])
        self.assertFalse(target['range'])
        self.assertNotIn('periodMetric', target)
        nested = rendered['panels'][1]['panels'][0]
        self.assertEqual(nested['fieldConfig']['defaults']['unit'], 'currencyEUR')
        self.assertIn('0.228', nested['targets'][0]['expr'])

    def test_private_provisioning_and_safe_failure(self):
        report = self.report()
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                render(self.installation, period_report=report)
                dashboard = json.loads(Path('private/generated/dashboards/period-verification.json').read_text())
                self.assertIn('24.0000 kWh', dashboard['panels'][0]['options']['content'])
                interactive = json.loads(Path('private/generated/dashboards/period-selectable.json').read_text())
                self.assertEqual(interactive['refresh'], '1m')
                self.assertIn('${__from}', interactive['panels'][0]['targets'][0]['expr'])
                rules = Path('private/generated/period-rules/intervals.json')
                self.assertEqual(rules.stat().st_mode & 0o777, 0o644)
                self.assertEqual(len(json.loads(rules.read_text())['groups'][0]['rules']), 9 * len(SOURCES))
                self.assertNotIn('0.228', rules.read_text())  # No financial facts recorded.
            finally:
                os.chdir(previous)
        from scraper.period_report import main
        from io import StringIO
        with patch.dict('os.environ', {'PERIOD_PROMETHEUS_URL': 'http://fictional-secret.invalid'}), patch('sys.stderr', new_callable=StringIO) as err:
            self.assertEqual(main(['--config', '/not-present-fictional-config', '--from', '2025-01-01', '--until', '2025-01-02']), 1)
            self.assertNotIn('fictional-secret', err.getvalue())
            self.assertNotIn('/not-present', err.getvalue())


if __name__ == '__main__':
    unittest.main()
