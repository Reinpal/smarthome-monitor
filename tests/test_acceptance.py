"""Final integration budgets and privacy guardrails; fictional inputs only."""
from contextlib import redirect_stdout
from datetime import datetime
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, urlparse
import unittest
from unittest.mock import patch

from scraper.installation import load_installation
from scraper.provision import render_dashboard

ROOT = Path(__file__).resolve().parents[1]


class AcceptanceTests(unittest.TestCase):
    def test_generated_overview_payload_budget(self):
        installation = load_installation(ROOT / 'installation.example.json')
        for name in ('home.json', 'photovoltaik.json', 'heatpump.json'):
            with self.subTest(dashboard=name):
                template = json.loads((ROOT / 'grafana/provisioning/dashboards' / name).read_text())
                dashboard = render_dashboard(template, installation)
                # Bound the actual Grafana payload, including hidden query variables,
                # not just panel targets. This is a fictional two-tariff budget.
                self.assertLess(len(json.dumps(dashboard).encode()), 1_700_000)
                queries = [v['query']['query'] for v in dashboard['templating']['list'] if v['type'] == 'query']
                queries += [t['expr'] for p in dashboard['panels'] for t in p.get('targets', [])]
                self.assertLess(max(map(len, queries)), 128_000)
                for panel in dashboard['panels']:
                    for target in panel.get('targets', []):
                        if target.get('legendFormat') == '{{coverage}}':
                            self.assertLess(len(target['expr']), 16_000, 'Qualification must not double the expensive value query')

    def test_private_paths_are_ignored_and_not_tracked_or_staged(self):
        paths = ['.env', '.env.production', 'private/installation.json',
                 'private/generated/dashboards/home.json',
                 'private/generated/period-rules/intervals.json',
                 'private/backups/installation.json', 'data/grafana/grafana.db',
                 'backups/fictional.tar.gz']
        for path in paths:
            self.assertEqual(subprocess.run(['git', 'check-ignore', '-q', path], cwd=ROOT).returncode, 0)
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
        self.assertFalse(any(p.startswith(('private/', 'data/', 'backups/')) or
                             (p.startswith('.env') and p != '.env.example') for p in tracked))
        # Index contents include staged additions; no filenames or contents on failure.
        self.assertEqual(subprocess.run(['git', 'check-ignore', '-q', 'installation.example.json'], cwd=ROOT).returncode, 1)


class ReadonlyAuditTests(unittest.TestCase):
    def test_failed_private_requests_cannot_escape_the_allowlisted_report(self):
        spec = importlib.util.spec_from_file_location('readonly_audit', ROOT / 'deploy/check-insights-readonly.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sentinel = 'FICTIONAL_PRIVATE_RESPONSE_DO_NOT_PUBLISH'
        calls = []
        def unavailable(port, path, parameters):
            calls.append((port, path))
            raise ValueError(sentinel)
        result = module.audit({'Mounts': [], 'NetworkSettings': {'Ports': {}}}, unavailable)
        self.assertNotIn(sentinel, json.dumps(result))
        self.assertLessEqual(set(result.values()), {'PASS', 'FAIL', 'UNAVAILABLE'})
        self.assertTrue(calls)
        self.assertTrue(all(port in (3000, 9090) and path.startswith('/api/') for port, path in calls))
        self.assertEqual(result['contract_v2_deployed'], 'UNAVAILABLE')
        self.assertEqual(result['representative_recent_month_presence_complete'], 'UNAVAILABLE')
        output = StringIO()
        with patch.object(module.subprocess, 'run', side_effect=RuntimeError(sentinel)), redirect_stdout(output):
            self.assertEqual(module.main(), 1)
        self.assertEqual(output.getvalue(), 'live_readonly_access: UNAVAILABLE\n')


@unittest.skipUnless(all(os.environ.get(k) for k in ('PROMETHEUS_TEST_BINARY', 'GRAFANA_TEST_HOME', 'CHROMIUM_TEST_BINARY')),
                     'set isolated binary variables for combined homeowner acceptance')
class CombinedBrowserTests(unittest.TestCase):
    def test_home_to_both_overviews_and_diagnostics_preserves_period_and_layout(self):
        from period_browser import grafana
        from playwright.sync_api import sync_playwright, expect
        from test_heating_browser import wait_provisioned
        from energy_fixtures import EnergyFixture, HEATING_RATES
        fixture = self.enterContext(EnergyFixture())
        self.stack = fixture
        self.start = fixture.start.timestamp()
        fixture.push_intervals(self.start, 60)
        fixture.push_intervals(self.start, 60, rates=HEATING_RATES)
        fixture.source(0, stamp=self.start + 3600)
        names = ('home.json', 'photovoltaik.json', 'heatpump.json',
                 'heatpump-diagnostics.json', 'solar-battery-diagnostics.json')
        dashboards = [render_dashboard(json.loads((ROOT / 'grafana/provisioning/dashboards' / n).read_text()), fixture.installation)
                      for n in names]
        with grafana(dashboards, self.stack.base) as (base, _), sync_playwright() as pw:
            wait_provisioned(base, dashboards)
            browser = pw.chromium.launch(executable_path=os.environ['CHROMIUM_TEST_BINARY'], headless=True)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
                failures = []
                # Navigation/resize can discard Chrome's response-body cache.
                # This journey checks HTTP health plus actual visible values;
                # the populated benchmark separately checks every query body.
                page.on('response', lambda response: failures.append(response.status)
                        if '/api/ds/query' in response.url and response.status >= 400 else None)
                def settled():
                    page.wait_for_load_state('networkidle', timeout=90000)
                    self.assertFalse(failures, 'Combined native datasource HTTP failure')
                page.goto(base + f'/d/home-energy?from={int(self.start*1000)}&to={int((self.start+3600)*1000)}',
                          wait_until='networkidle', timeout=90000)
                expect(page.get_by_role('region', name='Household electricity', exact=True)).to_contain_text('1.00', timeout=60000)
                # Same accepted VD intervals feed Home and the heating overview.
                expect(page.get_by_role('region', name='Heat-pump electricity · VD', exact=True)).to_contain_text('9.00', timeout=60000)
                settled()
                for link, uid, heading in (
                    ('Solar & battery', 'pv-overview', 'PV production · DC estimate'),
                    ('Heating & hot water', 'heatpump-overview', 'Heating · VD electricity'),
                    ('Heating diagnostics', 'heatpump-diagnostics', 'Technical context — not manufacturer limits'),
                    ('Solar diagnostics', 'solar-battery-diagnostics', 'Solar & battery technical context'),
                ):
                    page.get_by_role('link', name=link, exact=True).click()
                    page.wait_for_url('**/d/' + uid + '**', timeout=60000)
                    expect(page.get_by_role('heading', name=heading, exact=True)).to_be_visible(timeout=60000)
                    selection = parse_qs(urlparse(page.url).query)
                    for field, expected in (('from', self.start), ('to', self.start + 3600)):
                        value = selection[field][0]
                        actual = float(value) / 1000 if value.isdigit() else datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
                        self.assertEqual(actual, expected)
                    settled()
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.wait_for_function('document.documentElement.scrollWidth <= window.innerWidth', timeout=10000)
                    expect(page.get_by_role('heading', name=heading, exact=True)).to_be_visible(timeout=60000)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                    settled()
                    page.set_viewport_size({'width': 1440, 'height': 1000})
                    settled()
                    page.locator('a[href*="/d/home-energy"]').click()
                    expect(page.get_by_role('region', name='Household electricity', exact=True)).to_contain_text('1.00', timeout=60000)
                    settled()
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
