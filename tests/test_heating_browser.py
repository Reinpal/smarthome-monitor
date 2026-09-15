"""Real native Grafana panels/annotations against disposable fictional Prometheus."""
from datetime import datetime
import os
import re
import unittest
from urllib.parse import parse_qs, urlparse

import requests
from energy_fixtures import EnergyFixture, HEATING_RATES
from scraper.installation import load_installation
from scraper.provision import render_dashboard
from test_heating_dashboards import ROOT, templates
from period_browser import wait_provisioned


@unittest.skipUnless(all(os.environ.get(key) for key in ('PROMETHEUS_TEST_BINARY','GRAFANA_TEST_HOME','CHROMIUM_TEST_BINARY')),
                     'set isolated Prometheus/Grafana/Chromium binary variables for browser acceptance')
class HeatingBrowserTests(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(EnergyFixture())
        self.start = 1735689600  # Fictional 2025-01-01 UTC.

    def test_period_selection_refresh_native_annotations_navigation_and_small_screen(self):
        from period_browser import grafana
        from playwright.sync_api import sync_playwright, expect, Error as BrowserError
        self.stack.push_intervals(self.start, 60, rates=HEATING_RATES)
        installation=load_installation(ROOT/'installation.example.json')
        dashboards=[render_dashboard(d,installation) for d in templates()]
        with grafana(dashboards,self.stack.base) as (base,_), sync_playwright() as pw:
            wait_provisioned(base,dashboards)
            browser=pw.chromium.launch(executable_path=os.environ['CHROMIUM_TEST_BINARY'],headless=True)
            try:
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.route('**/*',lambda route: route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                selected=f'?from={self.start*1000}&to={(self.start+3600)*1000}'
                page.goto(base+'/d/heatpump-overview'+selected,wait_until='networkidle',timeout=90000)
                expect(page.get_by_role('heading',name='Heating · VD electricity',exact=True)).to_be_visible(timeout=60000)
                # The actual stat output includes individual value and confidence fields.
                expect(page.locator('body')).to_contain_text('6.00',timeout=60000)
                expect(page.locator('body')).to_contain_text('Full period',timeout=60000)
                expect(page.locator('body')).to_contain_text('Observed through',timeout=60000)
                expect(page.locator('body')).to_contain_text('Confirmed',timeout=60000)
                selected=f'?from={self.start*1000}&to={(self.start+1800)*1000}'
                page.goto(base+'/d/heatpump-overview'+selected,wait_until='networkidle')
                expect(page.locator('body')).to_contain_text('3.00',timeout=60000)
                with page.expect_response(lambda response:'/api/ds/query' in response.url):
                    page.get_by_role('button',name='Refresh',exact=True).click()
                # Real native annotation persistence in the disposable Grafana DB.
                annotation={'dashboardUID':'heatpump-overview','panelId':1,'time':(self.start+900)*1000,
                            'tags':['comfort'],'text':'Fictional comfort note for isolated acceptance'}
                created=requests.post(base+'/api/annotations',auth=('admin','fictional-test-only'),json=annotation,timeout=5)
                self.assertTrue(created.ok,'Native annotation creation failed')
                note_id=created.json()['id']
                stored=requests.get(base+'/api/annotations',params={'dashboardUID':'heatpump-overview','from':self.start*1000,'to':(self.start+1800)*1000},timeout=5)
                self.assertTrue(stored.ok)
                self.assertTrue(any(n['id']==note_id and n['text']==annotation['text'] for n in stored.json()))
                annotation_results=[]
                def capture(response):
                    if '/api/annotations?' in response.url and response.ok:
                        try: annotation_results.extend(response.json())
                        except (ValueError,TypeError,BrowserError): pass
                page.on('response',capture)
                page.goto(base+'/d/heatpump-overview'+selected+'&viewPanel=1',wait_until='networkidle')
                expect(page.get_by_role('heading',name='Outdoor, flow & return conditions',exact=True)).to_be_visible(timeout=15000)
                page.wait_for_timeout(1000)
                self.assertTrue(any(n.get('id')==note_id for n in annotation_results),'Native annotation query did not reach browser')
                page.remove_listener('response',capture)
                page.goto(base+'/d/heatpump-overview'+selected,wait_until='networkidle')
                page.get_by_role('link',name=re.compile('^(Heat-pump diagnostics|Heating diagnostics)$')).click()
                expect(page.get_by_role('heading',name='Technical context — not manufacturer limits',exact=True)).to_be_visible(timeout=60000)
                query=parse_qs(urlparse(page.url).query)
                self.assertEqual(datetime.fromisoformat(query['from'][0]).timestamp(),self.start)
                self.assertEqual(datetime.fromisoformat(query['to'][0]).timestamp(),self.start+1800)
                page.get_by_role('link',name=re.compile('^(Solar & battery diagnostics|Solar diagnostics)$')).click()
                expect(page.get_by_role('heading',name='Solar & battery technical context',exact=True)).to_be_visible(timeout=60000)
                self.assertEqual(parse_qs(urlparse(page.url).query)['from'],query['from'])
                page.get_by_role('link',name='Heating & hot water',exact=True).click()
                expect(page.get_by_role('heading',name='Heating · VD electricity',exact=True)).to_be_visible(timeout=60000)
                # A continuous fresh prefix is qualified, not extrapolated.
                page.goto(base+f'/d/heatpump-overview?from={self.start*1000}&to={(self.start+3630)*1000}',wait_until='networkidle')
                expect(page.locator('body')).to_contain_text('Observed prefix only',timeout=60000)
                # Stale trailing history retains coverage but must withhold the value.
                page.goto(base+f'/d/heatpump-overview?from={self.start*1000}&to={(self.start+4800)*1000}',wait_until='networkidle')
                expect(page.get_by_role('region',name='Heating · VD electricity',exact=True)).to_contain_text('Unavailable',timeout=60000)
                # Leading missing history: period totals must not display a zero.
                page.goto(base+f'/d/heatpump-overview?from={(self.start-86400)*1000}&to={(self.start-82800)*1000}',wait_until='networkidle')
                expect(page.locator('body')).to_contain_text('Unavailable',timeout=60000)
                page.set_viewport_size({'width':390,'height':844})
                expect(page.get_by_role('heading',name='Heating · VD electricity',exact=True)).to_be_visible(timeout=60000)
                self.assertFalse(page.evaluate('document.documentElement.scrollWidth > window.innerWidth'),'Dashboard overflows small viewport')
                removed=requests.delete(base+'/api/annotations/'+str(note_id),auth=('admin','fictional-test-only'),timeout=5)
                self.assertTrue(removed.ok,'Native annotation removal failed')
            finally:
                browser.close()


if __name__=='__main__':
    unittest.main()
