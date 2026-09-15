"""Fictional HTTP -> real collector/SDK -> isolated Prometheus -> report/panels.

Optional real Grafana provisioning/browser verification on loopback. No Docker,
real device/config/history, deployment, or original checkout access.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest

import requests
import test_measurement_queries as prometheus_fixture
from test_period_calculations import configuration
from scraper.installation import parse_installation
from scraper.periods import Period
from scraper.period_query import PrometheusHistory, QueryUnavailable, query_report
from scraper.period_render import render_dashboard


@unittest.skipUnless(os.environ.get('PROMETHEUS_TEST_BINARY'), 'set PROMETHEUS_TEST_BINARY for isolated query acceptance')
class PeriodQueryTests(unittest.TestCase):
    def setUp(self):
        self.stack = prometheus_fixture.MeasurementQueryTests('runTest')
        self.stack.setUp()
        self.addCleanup(self.stack.doCleanups)
        self.raw = configuration()
        self.installation = parse_installation(self.raw)
        self.start = datetime(2025, 6, 30, 21, 59, tzinfo=timezone.utc)
        self.period = Period(self.start, self.start + timedelta(minutes=2))
        self.reader = PrometheusHistory(self.stack.base)

    def source(self, index, *, reset=False, missing=False, stamp=None):
        at = (self.start + timedelta(minutes=index)).timestamp() if stamp is None else stamp
        site = {'P_Load': -1000, 'P_PV': 1500, 'P_Akku': -300 if index == 0 else 300}
        if missing:
            site.pop('P_Load')
        self.stack.fixture.solar({'powerflow': {'Site': site, 'Inverters': {'1': {'SOC': 60 - index}}},
                                  'meter': {'fictional-grid': {'Meter_Location_Current': 0, 'Visible': 1, 'Enable': 1,
                                                              'EnergyReal_WAC_Plus_Absolute': 10 if reset else 1000 + index * 10,
                                                              'EnergyReal_WAC_Minus_Absolute': 500 + index * 5}}}, at)
        self.stack.fixture.heatpump({'WÄRMEMENGE': {'VD HEIZEN SUMME': f'{100 + index * .003}MWh',
                                                  'VD WARMWASSER SUMME': f'{20 + index * .001}MWh',
                                                  'NHZ HEIZEN SUMME': '0MWh', 'NHZ WARMWASSER SUMME': '0MWh'},
                                    'LEISTUNGSAUFNAHME': {'VD HEIZEN SUMME': f'{20000 + index}kWh',
                                                         'VD WARMWASSER SUMME': f'{1000 + index * .5}kWh'}}, at)
        self.stack.push(at)

    def report(self, config=None):
        return query_report(self.reader, parse_installation(config or self.raw), self.period, compare=False)

    def test_real_source_queries_tariff_correction_and_panel_values(self):
        for index in range(3):
            self.source(index)
        report = self.report()
        m = report['metrics']
        self.assertAlmostEqual(m['household']['value'], 1 / 30)
        self.assertAlmostEqual(m['grid_import']['value'], .02)
        self.assertAlmostEqual(m['pv']['value'], .05)
        self.assertAlmostEqual(m['import_cost']['value'], .01 * (.228 + .24))
        self.assertAlmostEqual(m['heating_electricity']['value'], 2)
        self.assertAlmostEqual(m['heating_heat']['value'], 6)
        self.assertAlmostEqual(m['heatpump_ratio']['value'], 8 / 3)
        self.assertAlmostEqual(m['battery_charge']['value'], .00125)
        self.assertAlmostEqual(m['battery_discharge']['value'], .00625)
        self.assertAlmostEqual(m['battery_inventory_change']['value'], -.23)
        self.assertEqual(m['import_cost']['tariff_status'], 'provisional')
        corrected = deepcopy(self.raw)
        corrected['import_tariffs'][1]['status'] = 'confirmed'
        corrected['import_tariffs'][1]['components']['energy']['amount'] = .20
        after = self.report(corrected)
        self.assertAlmostEqual(after['metrics']['import_cost']['value'], .01 * (.228 + .29))
        dashboard = render_dashboard(after)
        text = dashboard['panels'][2]['options']['content']
        self.assertIn('0.0052 EUR', text)
        self.assertIn('confirmed', text)
        self.assertNotEqual(render_dashboard(report)['panels'][2]['options']['content'], text)

    def test_real_missing_cached_reset_legacy_and_zero_do_not_become_headlines(self):
        self.source(0)
        self.source(1, missing=True)
        self.source(2, reset=True)
        m = self.report()['metrics']
        self.assertIsNone(m['household']['value'])
        self.assertIsNone(m['grid_import']['value'])
        self.assertIsNone(m['solar_battery_benefit']['value'])
        self.assertIn('Unavailable', render_dashboard(self.report())['panels'][0]['options']['content'])
        self.stack.push((self.start + timedelta(minutes=6)).timestamp())  # Only cached exports.
        self.period = Period(self.start + timedelta(minutes=2), self.start + timedelta(minutes=6))
        m = self.report()['metrics']
        self.assertIsNone(m['pv']['value'])
        self.assertGreater(m['pv']['largest_gap_seconds'], 180)
        # Querying pre-contract history cannot manufacture observations.
        self.period = Period(self.start - timedelta(days=20), self.start - timedelta(days=19))
        self.assertIsNone(self.report()['metrics']['household']['value'])

    def test_real_dst_elapsed_period_reaches_render(self):
        self.start = datetime(2025, 3, 30, 0, 59, tzinfo=timezone.utc)
        self.period = Period(self.start, self.start + timedelta(minutes=2))
        for i in range(3):
            self.source(i)
        report = self.report()
        self.assertEqual(report['seconds'], 120)
        self.assertAlmostEqual(report['metrics']['household']['value'], 1 / 30)
        self.assertIn('120', render_dashboard(report)['panels'][4]['options']['content'])

    def push_intervals(self, start, minutes):
        """Fictional accepted facts for historical/month-scale query acceptance.

        The source->recording-rule path is independently exercised by promtool;
        these use the real OTLP wire/storage path, not a fake Grafana datasource.
        """
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
        from scraper.period_promql import PREFIX
        for first in range(1, minutes + 1, 1440):
            request = ExportMetricsServiceRequest()
            scope = request.resource_metrics.add().scope_metrics.add()
            for key in ('household', 'grid_import', 'grid_export'):
                for field in ('start_seconds', 'end_seconds', 'left', 'right', 'rate', 'zero_seconds', 'shape', 'stale_after_seconds', 'valid_until_seconds'):
                    metric = scope.metrics.add(name=PREFIX + field)
                    for i in range(first, min(minutes + 1, first + 1440)):
                        at = start + i * 60
                        rate = 0 if key == 'household' else (.4 if key == 'grid_import' else .2) / 3600
                        value = {'start_seconds': at - 60, 'end_seconds': at, 'rate': rate,
                                 'zero_seconds': start, 'shape': 1, 'stale_after_seconds': 180, 'valid_until_seconds': at + 180,
                                 'left': 1000 if key == 'household' else 100 + (i - 1) * 60 * rate,
                                 'right': 1000 if key == 'household' else 100 + i * 60 * rate}[field]
                        point = metric.gauge.data_points.add(time_unix_nano=int(at * 1e9), as_double=value)
                        point.attributes.add(key='metric').value.string_value = key
            response = requests.post(self.stack.base + '/api/v1/otlp/v1/metrics', data=request.SerializeToString(),
                                     headers={'Content-Type': 'application/x-protobuf'}, timeout=20)
            self.assertTrue(response.ok, 'Fictional interval ingestion failed')

    def test_real_selectable_full_dst_days(self):
        from scraper.periods import calendar_period
        from scraper.period_promql import PeriodQueries
        from datetime import date
        for start, end, hours, price in ((date(2025, 3, 30), date(2025, 3, 31), 23, .228),
                                         (date(2025, 10, 26), date(2025, 10, 27), 25, .24)):
            period = calendar_period(start, end, self.installation)
            self.push_intervals(period.start.timestamp(), int(period.seconds / 60))
            queries = PeriodQueries(self.installation, start=str(period.start.timestamp()), end=str(period.end.timestamp()), window=f'{int(period.seconds)}s')
            for field, key, expected in (('metrics', 'household', hours), ('metrics', 'import_cost', hours * .4 * price), ('complete', 'household', 1)):
                expression = queries.target(key, field=field)['expr']
                response = requests.post(self.stack.base + '/api/v1/query', data={'query': expression, 'time': period.end.timestamp()}, timeout=30).json()
                self.assertEqual(response['status'], 'success')
                self.assertAlmostEqual(float(response['data']['result'][0]['value'][1]), expected)

    @unittest.skipUnless(os.environ.get('GRAFANA_TEST_HOME') and os.environ.get('CHROMIUM_TEST_BINARY'),
                         'set GRAFANA_TEST_HOME and CHROMIUM_TEST_BINARY for interactive browser acceptance')
    def test_real_interactive_month_day_refresh_and_historical_price_correction(self):
        from scraper.periods import calendar_period
        from scraper.period_promql import PeriodQueries, render_interactive_dashboard
        from datetime import date
        from period_browser import grafana
        from playwright.sync_api import sync_playwright, expect
        period = calendar_period(date(2025, 6, 1), date(2025, 7, 2), self.installation)
        self.push_intervals(period.start.timestamp(), int(period.seconds / 60))
        queries = PeriodQueries(self.installation, start=str(period.start.timestamp()), end=str(period.end.timestamp()), window=f'{int(period.seconds)}s')
        began = time.monotonic()
        for key, expected in (('household', 744), ('grid_import', 297.6), ('solar_battery_benefit', 113.112)):
            response = requests.post(self.stack.base + '/api/v1/query',
                                     data={'query': queries.metrics[key], 'time': period.end.timestamp()}, timeout=30).json()
            self.assertEqual(response['status'], 'success', 'Monthly calculation query failed')
            self.assertAlmostEqual(float(response['data']['result'][0]['value'][1]), expected)
        self.assertLess(time.monotonic() - began, 30, 'Representative monthly headline queries exceeded a refresh budget')
        initial = render_interactive_dashboard(self.installation)
        corrected = deepcopy(self.raw)
        corrected['import_tariffs'][1]['components']['energy']['amount'] = .20
        corrected['import_tariffs'][1]['status'] = 'confirmed'
        after = render_interactive_dashboard(parse_installation(corrected))
        with grafana([initial], self.stack.base) as (base, output), sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.environ['CHROMIUM_TEST_BINARY'], headless=True)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
                selected = f'?from={int(period.start.timestamp()*1000)}&to={int(period.end.timestamp()*1000)}'
                page.goto(base + '/d/period-selectable' + selected, wait_until='networkidle', timeout=90000)
                expect(page.locator('body')).to_contain_text('744.000', timeout=60000)
                expect(page.locator('body')).to_contain_text('113.112', timeout=60000)
                # Grafana URL time state is the same native picker state, not rerendered Python.
                day = calendar_period(date(2025, 7, 1), date(2025, 7, 2), self.installation)
                selected = f'?from={int(day.start.timestamp()*1000)}&to={int(day.end.timestamp()*1000)}'
                page.goto(base + '/d/period-selectable' + selected, wait_until='networkidle')
                expect(page.locator('body')).to_contain_text('24.000', timeout=60000)
                expect(page.locator('body')).to_contain_text('3.816', timeout=60000)
                with page.expect_response(lambda response: '/api/ds/query' in response.url):
                    page.get_by_role('button', name='Refresh', exact=True).click()
                (output / 'period-selectable.json').write_text(json.dumps(after))
                for _ in range(40):
                    provisioned = requests.get(base + '/api/dashboards/uid/period-selectable', timeout=2).json()
                    if '* 0.29' in json.dumps(provisioned):
                        break
                    time.sleep(.25)
                else:
                    self.fail('Corrected interactive queries were not provisioned')
                page.reload(wait_until='networkidle')
                expect(page.locator('body')).to_contain_text('4.536', timeout=60000)
                # Reload/refresh recalculates retained history; no financial time series rewritten.
                self.assertEqual(provisioned['dashboard']['refresh'], '1m')
            finally:
                browser.close()

    @unittest.skipUnless(os.environ.get('GRAFANA_TEST_HOME') and os.environ.get('CHROMIUM_TEST_BINARY'),
                         'set GRAFANA_TEST_HOME and CHROMIUM_TEST_BINARY for real native-panel browser acceptance')
    def test_real_grafana_provisioned_browser_corrected_results(self):
        for i in range(3):
            self.source(i)
        report = self.report()
        corrected = deepcopy(self.raw)
        corrected['import_tariffs'][1]['status'] = 'confirmed'
        corrected['import_tariffs'][1]['components']['energy']['amount'] = .20
        corrected_report = self.report(corrected)
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory(prefix='fictional-period-grafana-') as directory:
            root = Path(directory)
            provisioning = root / 'provisioning'
            (provisioning / 'dashboards').mkdir(parents=True)
            dashboards = root / 'dashboards'
            dashboards.mkdir()
            target = dashboards / 'period-verification.json'
            target.write_text(json.dumps(render_dashboard(report)))
            (provisioning / 'dashboards/provider.yml').write_text(
                'apiVersion: 1\nproviders:\n  - name: fictional-period\n    type: file\n    updateIntervalSeconds: 1\n    options:\n      path: ' + str(dashboards) + '\n')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            config = root / 'grafana.ini'
            config.write_text(f'''[server]
http_addr = 127.0.0.1
http_port = {port}
[paths]
data = {root / 'data'}
logs = {root / 'logs'}
plugins = {root / 'plugins'}
provisioning = {provisioning}
[auth.anonymous]
enabled = true
org_role = Viewer
[analytics]
reporting_enabled = false
check_for_updates = false
check_for_plugin_updates = false
[plugins]
preinstall_disabled = true
[security]
admin_password = fictional-test-only
''')
            home = Path(os.environ['GRAFANA_TEST_HOME'])
            # Don't inherit private Grafana settings from the host.
            env = {k: v for k, v in os.environ.items() if not k.startswith('GF_')}
            process = subprocess.Popen([str(home / 'bin/grafana'), 'server', '--homepath', str(home), '--config', str(config)],
                                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                base = f'http://127.0.0.1:{port}'
                for _ in range(200):
                    try:
                        if requests.get(base + '/api/health', timeout=.2).ok:
                            break
                    except requests.RequestException:
                        pass
                    time.sleep(.1)
                else:
                    self.fail('Isolated Grafana failed to become ready')
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(executable_path=os.environ['CHROMIUM_TEST_BINARY'], headless=True)
                    try:
                        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                        # External resources/telemetry cannot leave the isolated test browser.
                        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
                        page.goto(base + '/d/period-verification', wait_until='networkidle')
                        page.get_by_text('Site energy', exact=True).wait_for()
                        self.assertIn('0.0333 kWh', page.locator('body').inner_text())
                        def show_panel(title):
                            # Grafana lazily mounts offscreen panels; scroll the dashboard first.
                            for _ in range(20):
                                if page.get_by_text(title, exact=True).count():
                                    page.get_by_text(title, exact=True).scroll_into_view_if_needed()
                                    page.wait_for_timeout(300)
                                    return
                                page.mouse.move(1400, 850)
                                page.mouse.wheel(0, 600)
                                page.wait_for_timeout(200)
                            self.fail('Native Grafana panel did not mount')
                        show_panel('Financial estimates')
                        self.assertIn('0.0047 EUR', page.locator('body').inner_text())
                        self.assertIn('provisional', page.locator('body').inner_text())
                        target.write_text(json.dumps(render_dashboard(corrected_report)))
                        for _ in range(40):
                            data = requests.get(base + '/api/dashboards/uid/period-verification', timeout=2).json()
                            if '0.0052 EUR' in json.dumps(data):
                                break
                            time.sleep(.25)
                        else:
                            self.fail('Corrected native panels were not reprovisioned')
                        page.reload(wait_until='networkidle')
                        show_panel('Financial estimates')
                        self.assertIn('0.0052 EUR', page.locator('body').inner_text())
                        page.goto(base + '/d/period-verification', wait_until='networkidle')
                        show_panel('Battery boundary')
                        self.assertIn('-0.2300 kWh', page.locator('body').inner_text())
                    finally:
                        browser.close()
            finally:
                process.terminate()
                process.wait(timeout=15)


if __name__ == '__main__':
    unittest.main()
