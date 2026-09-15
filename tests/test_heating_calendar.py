"""#6 integration with the parallel #5 native calendar extension, when present."""
from datetime import date
import importlib.util
import os
import re
import unittest

import test_heating_browser as browser_fixtures
from energy_fixtures import EnergyFixture, HEATING_RATES
from scraper.installation import load_installation
from scraper.periods import calendar_period
from scraper.provision import render_dashboard
from test_heating_dashboards import ROOT, panel, templates


@unittest.skipUnless(importlib.util.find_spec('scraper.calendar_promql') and os.environ.get('PROMETHEUS_TEST_BINARY'),
                     'requires integrated #5 calendar extension and isolated Prometheus')
class HeatingCalendarTests(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(EnergyFixture())
        self.installation=load_installation(ROOT/'installation.example.json')
        self.period=calendar_period(date(2025,5,1),date(2025,5,2),self.installation)
        self.previous=calendar_period(date(2025,4,1),date(2025,4,2),self.installation)
        self.dashboard=render_dashboard(templates()[0],self.installation)
        for period,multiplier in ((self.previous,.5),(self.period,1)):
            self.stack.push_intervals(period.start.timestamp(), 1440, rates=HEATING_RATES, multiplier=multiplier)

    def query(self,expr,at):
        return self.stack.query(expr, at)

    def values(self,period):
        from scraper.calendar_promql import calendar_variables
        values={'__from':int(period.start.timestamp()*1000),'__to':int(period.end.timestamp()*1000),'__range_s':int(period.seconds)}
        for variable in calendar_variables(self.installation, comparison_keys=('heatpump_electricity', 'heatpump_ratio')):
            expression=variable['query']['query'][len('query_result('):-1]
            rows=self.query(self.substitute(expression,values),period.end.timestamp())
            self.assertEqual(len(rows),1,'Native calendar boundary unavailable')
            values[variable['name']]=rows[0]['value'][1]
        return values

    @staticmethod
    def substitute(expr,values):
        return re.sub(r'\$\{([^}]+)\}',lambda m:str(values[m[1]]),expr)

    def test_actual_heating_daily_and_matching_mtd_targets(self):
        values=self.values(self.period)
        for target,expected in zip(panel(self.dashboard,70)['targets'],(144,72)):
            self.assertNotIn('calendarMetric',target)
            self.assertTrue(target['instant'])
            self.assertFalse(target['range'])
            rows=self.query(self.substitute(target['expr'],values),self.period.end.timestamp())
            self.assertEqual(len(rows),1)
            self.assertEqual(float(rows[0]['metric']['day']),self.period.start.timestamp())
            self.assertAlmostEqual(float(rows[0]['value'][1]),expected)
        for id,expected in ((71,(216,108)),(72,(3,3))):
            for target,value in zip(panel(self.dashboard,id)['targets'],expected):
                rows=self.query(self.substitute(target['expr'],values),self.period.end.timestamp())
                self.assertEqual(len(rows),1)
                self.assertAlmostEqual(float(rows[0]['value'][1]),value)
        # Day-start label formatting uses installation zone, not browser locale.
        formatted=next(t for t in panel(self.dashboard,70)['transformations'] if t['id']=='formatTime')
        self.assertEqual(formatted['options']['timezone'],str(self.installation.timezone))
        # Missing next-day history cannot create daily zeroes or matching totals.
        incomplete=calendar_period(date(2025,5,1),date(2025,5,3),self.installation)
        missing=self.values(incomplete)
        for target in panel(self.dashboard,71)['targets']:
            self.assertEqual(self.query(self.substitute(target['expr'],missing),incomplete.end.timestamp()),[])
        for target in panel(self.dashboard,70)['targets']:
            rows=self.query(self.substitute(target['expr'],missing),incomplete.end.timestamp())
            self.assertEqual(len(rows),1)

    @unittest.skipUnless(os.environ.get('GRAFANA_TEST_HOME') and os.environ.get('CHROMIUM_TEST_BINARY'),
                         'set isolated Grafana/Chromium variables for daily chart rendering')
    def test_native_daily_chart_transforms_and_comparison_render(self):
        from period_browser import grafana
        from playwright.sync_api import sync_playwright,expect
        with grafana([self.dashboard],self.stack.base) as (base,_),sync_playwright() as pw:
            browser_fixtures.wait_provisioned(base,[self.dashboard])
            browser=pw.chromium.launch(executable_path=os.environ['CHROMIUM_TEST_BINARY'],headless=True)
            try:
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                selected=f'?from={int(self.period.start.timestamp()*1000)}&to={int(self.period.end.timestamp()*1000)}'
                failures=[]
                def check(response):
                    if '/api/ds/query' in response.url and response.status>=400: failures.append(response.status)
                page.on('response',check)
                page.goto(base+'/d/heatpump-overview'+selected+'&viewPanel=70',wait_until='networkidle',timeout=90000)
                expect(page.get_by_role('heading',name='Daily VD electricity · completed local days',exact=True)).to_be_visible(timeout=60000)
                expect(page.locator('body')).to_contain_text('2025-05-01',timeout=60000)
                expect(page.locator('body')).to_contain_text('Heating VD',timeout=60000)
                expect(page.locator('body')).not_to_contain_text('Bar charts require a string or time field')
                self.assertFalse(failures,'Heating daily native datasource query failed')
                page.goto(base+'/d/heatpump-overview'+selected+'&viewPanel=71',wait_until='networkidle',timeout=90000)
                expect(page.locator('body')).to_contain_text('216.00',timeout=60000)
                expect(page.locator('body')).to_contain_text('108.00',timeout=60000)
                self.assertFalse(failures,'Heating comparison native datasource query failed')
            finally:
                browser.close()


if __name__=='__main__':
    unittest.main()
