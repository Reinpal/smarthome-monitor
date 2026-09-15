"""Completed local-month trends over real Prometheus and fictional history."""
from datetime import date
import json
import os
from pathlib import Path
import unittest

from scraper.calendar_promql import CalendarQueries, calendar_variables
from scraper.installation import load_installation
from scraper.periods import calendar_period
from scraper.provision import render_dashboard
from energy_fixtures import EnergyFixture
from test_home_solar import substitute

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('home', 'photovoltaik', 'heatpump')


def dashboard(name, installation):
    return render_dashboard(json.loads((ROOT / f'grafana/provisioning/dashboards/{name}.json').read_text()), installation)


class MonthlyTemplateTests(unittest.TestCase):
    def test_monthly_mode_is_a_native_complete_bucket_query(self):
        installation = load_installation(ROOT / 'installation.example.json')
        targets = CalendarQueries(installation).targets({'calendarMetric': 'household', 'calendarMode': 'monthly'})
        self.assertEqual(len(targets), 12)
        self.assertTrue(all(t['instant'] and not t['range'] for t in targets))
        self.assertTrue(all('month' in t['expr'] for t in targets))
        for name in NAMES:
            panel = next(p for p in dashboard(name, installation)['panels'] if p['id'] == 75)
            self.assertEqual(panel['type'], 'barchart')
            self.assertEqual(panel['fieldConfig']['defaults']['unit'], 'kwatth')
            self.assertIn('completed local months', panel['title'])
            self.assertIn('now-12M/M', panel['links'][0]['url'])
            self.assertEqual(panel['transformations'][-1]['options']['outputFormat'], 'YYYY-MM')


@unittest.skipUnless(os.environ.get('PROMETHEUS_TEST_BINARY'), 'set isolated Prometheus binary')
class MonthlyQueryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = self.enterContext(EnergyFixture())
        self.installation = self.fixture.installation

    def variables(self, period):
        values = {'__from': int(period.start.timestamp()*1000), '__to': int(period.end.timestamp()*1000), '__range_s': int(period.seconds)}
        for variable in calendar_variables(self.installation, comparison_keys=(), monthly=True):
            rows = self.fixture.query(substitute(variable['query']['query'][13:-1], values), period.end.timestamp())
            self.assertEqual(len(rows), 1, 'Calendar boundary unavailable')
            values[variable['name']] = rows[0]['value'][1]
        return values

    def months(self, targets, period):
        values = self.variables(period)
        rows = [row for target in targets
                for row in self.fixture.query(substitute(target['expr'], values), period.end.timestamp())]
        return {float(row['metric']['month']): float(row['value'][1]) for row in rows}

    def test_complete_local_months_dst_leap_year_partial_edges_gaps_and_no_history(self):
        # Includes leap February, spring/fall DST, a year boundary and rejected intervals.
        cases = ((date(2024, 2, 1), date(2024, 3, 1), 696, None),
                 (date(2024, 3, 1), date(2024, 4, 1), 743, None),
                 (date(2024, 4, 1), date(2024, 5, 1), 720, ('household', 100)),
                 (date(2024, 10, 1), date(2024, 11, 1), 745, None),
                 (date(2024, 12, 1), date(2025, 1, 1), 744, None))
        expected = {}
        for start, end, hours, missing in cases:
            period = calendar_period(start, end, self.installation)
            self.assertEqual(period.seconds / 3600, hours)
            # Closing facts arrive after midnight, as real recording rules can.
            self.fixture.push_intervals(period.start.timestamp(), int(period.seconds/60),
                                        rates={'household': 1000}, missing=missing, receipt_delay=30)
            if missing is None:
                expected[period.start.timestamp()] = hours
        target = CalendarQueries(self.installation).targets({'calendarMetric': 'household', 'calendarMode': 'monthly'})
        selected = calendar_period(date(2024, 1, 1), date(2025, 1, 1), self.installation)
        self.assertEqual(self.months(target, selected), expected)
        partial = calendar_period(date(2024, 2, 2), date(2024, 12, 15), self.installation)
        self.assertEqual(self.months(target, partial), {k: v for k, v in expected.items() if v in (743, 745)})
        # No annualization, zero filling, leading/tail partial totals or silent truncation.
        for start, end in ((date(2023, 1, 1), date(2024, 1, 1)),
                           (date(2024, 2, 1), date(2024, 2, 15)),
                           (date(2024, 1, 1), date(2025, 2, 1))):
            self.assertEqual(self.months(target, calendar_period(start, end, self.installation)), {})

    @unittest.skipUnless(os.environ.get('GRAFANA_TEST_HOME') and os.environ.get('CHROMIUM_TEST_BINARY'), 'set isolated browser binaries')
    def test_all_three_native_trend_panels_render_and_react_to_selection_and_refresh(self):
        from period_browser import grafana
        from test_heating_browser import wait_provisioned
        from playwright.sync_api import sync_playwright, expect
        period = calendar_period(date(2025, 2, 1), date(2025, 4, 1), self.installation)
        self.fixture.push_intervals(period.start.timestamp(), int(period.seconds/60),
                                    rates={'household': 1000, 'grid_import': .4/3600, 'pv': 1500,
                                           'heating_electricity': .1/60, 'water_electricity': .05/60})
        dashboards = [dashboard(name, self.installation) for name in NAMES]
        with grafana(dashboards, self.fixture.base) as (base, _), sync_playwright() as pw:
            wait_provisioned(base, dashboards)
            browser = pw.chromium.launch(executable_path=os.environ['CHROMIUM_TEST_BINARY'], headless=True)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
                failures = []
                page.on('response', lambda r: failures.append(r.status) if '/api/ds/query' in r.url and r.status >= 400 else None)
                for d in dashboards:
                    panel = next(p for p in d['panels'] if p['id'] == 75)
                    target = panel['targets'][:12]
                    factor = 6 if d['uid'] == 'heatpump-overview' else 1
                    rows = self.months(target, period)
                    self.assertEqual(len(rows), 2)
                    for actual, expected in zip(sorted(rows.values()), (672*factor, 743*factor)):
                        self.assertAlmostEqual(actual, expected)
                    url = base + '/d/' + d['uid']
                    def inspect(selected):
                        query = f'?from={int(selected.start.timestamp()*1000)}&to={int(selected.end.timestamp()*1000)}'
                        page.goto(url + query + '&viewPanel=75&inspect=75&inspectTab=data', wait_until='networkidle', timeout=90000)
                        expect(page.get_by_role('heading', name=panel['title'], exact=True)).to_be_visible(timeout=60000)
                        # Bars/ticks are canvas-rendered. Inspect the actual native
                        # transformed rows, not date strings in the time picker.
                        drawer = page.locator('.rc-drawer-open')
                        drawer.get_by_role('button', name='Expand query row', exact=True).click()
                        # Heating also has an annotation switch outside Inspect.
                        switch = drawer.get_by_role('switch').first
                        if not switch.is_checked():
                            page.locator('label[for="' + switch.get_attribute('id') + '"]').click()
                        return drawer.get_by_role('table')
                    table = inspect(period)
                    expect(table).to_contain_text('2025-02', timeout=60000)
                    expect(table).to_contain_text('2025-03', timeout=60000)
                    expect(table).to_contain_text('Heating VD' if factor == 6 else '743.00', timeout=60000)
                    expect(page.locator('body')).not_to_contain_text('Bar charts require a string or time field')
                    page.keyboard.press('Escape')  # Close native Inspect drawer before toolbar interaction.
                    expect(table).not_to_be_visible()
                    with page.expect_response(lambda r: '/api/ds/query' in r.url):
                        page.get_by_role('button', name='Refresh', exact=True).click()
                    partial = calendar_period(date(2025, 2, 1), date(2025, 3, 15), self.installation)
                    table = inspect(partial)
                    expect(table).to_contain_text('2025-02', timeout=60000)
                    expect(table).not_to_contain_text('2025-03', timeout=60000)
                    page.keyboard.press('Escape')
                    expect(table).not_to_be_visible()
                    page.get_by_role('button', name='Move time range backwards', exact=True).click()
                    expect(page.get_by_role('region', name=panel['title'], exact=True)).to_contain_text('Unavailable', timeout=60000)
                    self.assertFalse(failures, 'Monthly native datasource request failed')
            finally:
                browser.close()
