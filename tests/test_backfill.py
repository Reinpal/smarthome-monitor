"""Fictional bounded legacy reconstruction and actual interactive panel queries."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest

import requests

from scraper.backfill import BackfillError, PREFIX, canonical, plan, reconstruct, stage
from scraper.installation import parse_installation
from scraper.period_promql import PeriodQueries
from scraper.periods import SOURCES
from scraper.provision import render_dashboard
from scraper.calendar_promql import calendar_variables
from test_period_calculations import configuration
from test_home_solar import substitute


def rows(key, points, instance='fictional-a', **labels):
    return [{'metric': {'__name__': SOURCES[key].prom_name, 'job': 'fictional', 'instance': instance,
                        'service_instance_id': instance, **labels}, 'values': points}]


class ReconstructionTests(unittest.TestCase):
    def test_counter_gap_reconciliation_units_reset_and_unavailable_long_gap(self):
        for key, scale in [('grid_import', .001), ('heating_electricity', 1000)]:
            data = rows(key, [(0, 100), (600, 110), (1200, 110), (1800, 2), (2400, 4), (100000, 6)])
            facts, report = reconstruct(data, key, 0, 100000)
            self.assertAlmostEqual(report['energy_kwh'], 12*scale)
            self.assertAlmostEqual(report['independent_segment_delta_kwh'], 12*scale)
            self.assertAlmostEqual(report['counter_reconciliation_error_kwh'], 0)
            self.assertEqual(report['rejected_pairs']['reset'], 1)
            self.assertEqual(report['rejected_pairs']['gap'], 1)
            self.assertEqual(report['covered_seconds'], 1800)
            self.assertEqual(report['freshness'], 'unknown')
            self.assertTrue(all(v['stale_after_seconds'] == 0 for _, v in facts))

    def test_restart_overlap_no_double_count_and_conflict_not_arbitrary_winner(self):
        first = rows('grid_import', [(0, 1000), (60, 1010), (120, 1020)])
        second = rows('grid_import', [(60, 1010), (90, 1015), (120, 1020), (180, 1030)], 'fictional-b')
        _, report = reconstruct(first+second, 'grid_import', 0, 180)
        self.assertAlmostEqual(report['energy_kwh'], .03)
        self.assertEqual(report['covered_seconds'], 180)
        second[0]['values'][1] = (90, 1020)
        _, report = reconstruct(first+second, 'grid_import', 0, 180)
        self.assertTrue(report['overlap_conflicts'])
        self.assertLess(report['covered_seconds'], 180)
        with self.assertRaises(BackfillError):
            canonical(first + rows('grid_import', [(180, 1030)], job='another-installation'))
        # A routine non-overlapping restart continues the SAME absolute meter.
        _, report = reconstruct(first+rows('grid_import', [(180, 1030)], 'new'), 'grid_import', 0, 180)
        self.assertAlmostEqual(report['energy_kwh'], .03)

    def test_raw_signs_cached_plateaus_short_and_long_power_gaps(self):
        for key in ('battery_charge', 'battery_discharge'):
            _, report = reconstruct(rows(key, [(0, -1000), (600, 1000)]), key, 0, 600)
            self.assertAlmostEqual(report['energy_kwh'], 1000*150/3600000)
        _, report = reconstruct(rows('household', [(0,-1000),(600,-1000),(1800,-1000)]), 'household', 0, 1800)
        self.assertAlmostEqual(report['energy_kwh'], 1/6)
        self.assertEqual(report['equal_reading_seconds'], 600)
        self.assertEqual(report['rejected_pairs']['gap'], 1)
        _, report = reconstruct(rows('household', [(0,1000),(60,1000)]), 'household', 0, 60)
        self.assertEqual(report['energy_kwh'], 0)
        self.assertEqual(report['covered_seconds'], 60)

    def test_cutoff_clips_live_start_and_never_forges_metadata(self):
        data = {SOURCES['grid_import'].prom_name: rows('grid_import', [(0,1000),(600,1100)]),
                'smarthome_period_v1_start_seconds': [{'metric': {'metric':'grid_import'}, 'values': [[660, 330]]}]}
        facts, report = plan(data, 0, 600)
        self.assertEqual(facts['grid_import'][-1][1]['end_seconds'], 330)
        self.assertAlmostEqual(report['sources']['grid_import']['energy_kwh'], .055)
        counter = report['sources']['grid_import']
        self.assertEqual(counter['raw_bracketing_readings'], [[0,1000],[600,1100]])
        self.assertTrue(counter['raw_bracketing_reconciles'])
        self.assertAlmostEqual(counter['raw_bracketing_delta_kwh'], .1)
        self.assertAlmostEqual(counter['boundary_allocation_kwh'], .045)
        self.assertNotIn('soc', facts)
        from scraper.backfill import openmetrics
        text = ''.join(openmetrics(facts))
        self.assertNotIn('smarthome_measurement_', text)
        self.assertNotIn('smarthome_period_v1_', text)
        self.assertIn('provenance="legacy_estimate"', text)

    def test_private_dry_run_repeat_and_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'stage'
            data = {SOURCES['pv'].prom_name: rows('pv', [(0,1000),(600,1000)])}
            first = stage(data, 0, 600, output)
            self.assertEqual(first, stage(data, 0, 600, output))
            self.assertFalse((output/'blocks').exists())
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(BackfillError):
                stage(data, 0, 300, output)
            with self.assertRaises(BackfillError):
                plan(data, 0, 600, power_gap=901)
            with self.assertRaises(BackfillError):
                plan({**data, PREFIX+'end_seconds': [{'values': [[60,60]]}]}, 0, 600)
            with self.assertRaises(BackfillError):
                reconstruct(rows('pv', [(0, 1), (60, 2)]), 'grid_import', 0, 60)


@contextmanager
def staged_prometheus(blocks):
    binary = os.environ['PROMETHEUS_TEST_BINARY']
    config = blocks.parent/'prometheus.yml'
    config.write_text('scrape_configs: []\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    process = subprocess.Popen([binary, '--config.file='+str(config), '--storage.tsdb.path='+str(blocks),
                                '--web.listen-address=127.0.0.1:'+str(port), '--storage.tsdb.retention.time=100y'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            try:
                if requests.get(base+'/-/ready',timeout=.2).ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(.05)
        else:
            raise AssertionError('Private test Prometheus unavailable')
        yield base
    finally:
        process.terminate()
        process.wait(timeout=15)


@unittest.skipUnless(os.environ.get('PROMTOOL_TEST_BINARY') and os.environ.get('PROMETHEUS_TEST_BINARY'), 'set isolated binaries')
class InteractiveBackfillTests(unittest.TestCase):
    def test_nonminute_cutoff_join_no_duplicate_lookback_or_observation_endpoint(self):
        # Legacy [0,90], followed by the independently recorded live [90,150].
        # Both series coexist in lookback: provenance-aware freshness must not
        # count the final legacy sample again when a live sample arrives.
        from scraper.backfill import FIELDS
        installation = parse_installation(configuration())
        facts, _ = reconstruct(rows('household', [(0,-1000),(180,-1000)]), 'household', 0, 90)
        inputs=[]
        for field in FIELDS:
            inputs.append({'series': PREFIX+field+'{metric="household",provenance="legacy_estimate"}',
                           'values': '_ ' + ' '.join(str(v[field]) for _,v in facts) + ' _'})
        live={'start_seconds':90,'end_seconds':150,'left':1000,'right':1000,'rate':0,
              'zero_seconds':90,'shape':1,'stale_after_seconds':180,'valid_until_seconds':330}
        inputs += [{'series':'smarthome_period_v1_'+field+'{metric="household"}',
                    'values':'_ _ _ '+str(value)} for field,value in live.items()]
        q=PeriodQueries(installation,start='0',end='150',window='240s')
        checks=[{'expr':expr,'eval_time':'180s','exp_samples':[{'labels':'{}','value':expected}]} for expr,expected in
                [(q.metrics['household'],150/3600),(q.coverage['household'],150),
                 (q.observed_until['household'],150),(q.telemetry_status['household'],2)]]
        fresh=PeriodQueries(installation,start='90',end='150',window='240s')
        checks.append({'expr':fresh.telemetry_status['household'],'eval_time':'180s',
                       'exp_samples':[{'labels':'{}','value':1}]})
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'queries.json'
            path.write_text(json.dumps({'tests':[{'interval':'60s','input_series':inputs,'promql_expr_test':checks}], 'fuzzy_compare':True}))
            result=subprocess.run([os.environ['PROMTOOL_TEST_BINARY'],'test','rules',str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_staged_blocks_selected_period_daily_chart_prices_and_confidence(self):
        raw = configuration()
        raw['installation']['timezone'] = 'UTC'
        installation = parse_installation(raw)
        start = datetime(2025,7,1,tzinfo=timezone.utc).timestamp()
        end = start+86400
        data = {}
        rates = {'grid_import': 400/3600, 'grid_export': 200/3600,
                 'heating_electricity': .001/3600, 'water_electricity': .0005/3600,
                 'heating_heat': .003/3600, 'water_heat': .0015/3600}
        for key in ('household','pv',*rates):
            # Deliberate bounded collection outage, plus changing exporter IDs.
            points = [(start+i*60, 100+rates[key]*i*60 if key in rates else -1000 if key=='household' else 1500)
                      for i in range(1441) if not 100 < i < 110]
            data[SOURCES[key].prom_name] = rows(key,points[:720])+rows(key,points[720:],'fictional-b')
        data[SOURCES['battery_discharge'].prom_name] = rows('battery_discharge', [(start+i*60,300) for i in range(1441)])
        with tempfile.TemporaryDirectory(prefix='fictional-backfill-') as tmp:
            output=Path(tmp)/'stage'
            report = stage(data,start,end,output,apply=True,promtool=os.environ['PROMTOOL_TEST_BINARY'])
            manifest=(output/'blocks.json').read_bytes()
            stage(data,start,end,output,apply=True,promtool=os.environ['PROMTOOL_TEST_BINARY'])
            self.assertEqual(manifest,(output/'blocks.json').read_bytes())
            self.assertAlmostEqual(report['sources']['grid_import']['energy_kwh'],9.6)
            with staged_prometheus(output/'blocks') as base:
                def query(expr,at=end):
                    body=requests.post(base+'/api/v1/query',data={'query':expr,'time':at},timeout=60).json()
                    self.assertEqual(body['status'],'success', str(body)[:300])
                    return body['data']['result']
                q=PeriodQueries(installation,start=str(start),end=str(end),window='24h')
                for key,expected in [('household',24),('grid_import',9.6),('pv',36),('heatpump_electricity',36),('heatpump_ratio',3),('battery_charge',0),('battery_discharge',7.2)]:
                    result=query(q.metrics[key])
                    self.assertEqual(len(result),1,key)
                    self.assertAlmostEqual(float(result[0]['value'][1]),expected,places=7)
                    self.assertEqual(float(query(q.telemetry_status[key])[0]['value'][1]),2)
                expected = 9.6*float(installation.import_price(datetime(2025,7,1,tzinfo=timezone.utc)).gross_per_kwh)
                self.assertAlmostEqual(float(query(q.metrics['import_cost'])[0]['value'][1]),expected)
                self.assertEqual(float(query(q.price_status['import_cost'])[0]['value'][1]),2)  # expired price carry-forward
                self.assertEqual(query('smarthome_measurement_contract_version'),[])
                home=json.loads(Path('grafana/provisioning/dashboards/home.json').read_text())
                rendered=render_dashboard(home,installation)
                variables={'__from':int(start*1000),'__to':int(end*1000),'__range_s':86400}
                for variable in calendar_variables(installation):
                    rows_=query(substitute(variable['query']['query'][13:-1],variables))
                    variables[variable['name']]=rows_[0]['value'][1]
                headline=next(p for p in rendered['panels'] if p['id']==1)['targets'][0]
                result=query(substitute(headline['expr'],variables))
                self.assertTrue(result, 'Legacy headline missing: ' + str({f: query(substitute(getattr(PeriodQueries(installation), f)['household'],variables)) for f in ('metrics','complete','telemetry_status')}))
                self.assertIn('Legacy estimate',result[0]['metric']['confidence'])
                self.assertEqual(result[0]['metric']['coverage'],'Full period')
                solar=render_dashboard(json.loads(Path('grafana/provisioning/dashboards/photovoltaik.json').read_text()),installation)
                for panel_id in (106,107):
                    battery=next(p for p in solar['panels'] if p['id']==panel_id)['targets'][0]
                    result=query(substitute(battery['expr'],variables))
                    self.assertEqual(result[0]['metric']['confidence'],'Legacy estimate')
                chart=next(p for p in rendered['panels'] if p['id']==70)
                result=query(substitute(chart['targets'][0]['expr'],variables))
                self.assertEqual(len(result),1)
                self.assertAlmostEqual(float(result[0]['value'][1]),24)
                self.assertEqual(float(result[0]['metric']['day']),start)
                self.assertTrue(any(t.get('periodField')=='coverage' for p in home['panels'] for t in p.get('targets',[])))
                if os.environ.get('GRAFANA_TEST_HOME') and os.environ.get('CHROMIUM_TEST_BINARY'):
                    self.browser(rendered,base,start,end)

    def browser(self, rendered, prometheus, start, end):
        from period_browser import grafana
        from playwright.sync_api import sync_playwright, expect
        with grafana([rendered],prometheus) as (base,_), sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=os.environ['CHROMIUM_TEST_BINARY'],headless=True)
            try:
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.route('**/*',lambda route: route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                url=base+'/d/home-energy?from='+str(int(start*1000))+'&to='+str(int(end*1000))
                page.goto(url+'&viewPanel=1',wait_until='networkidle',timeout=90000)
                expect(page.locator('body')).to_contain_text('Legacy estimate',timeout=60000)
                page.goto(url+'&viewPanel=70&inspect=70&inspectTab=data',wait_until='networkidle',timeout=90000)
                drawer=page.locator('.rc-drawer-open')
                drawer.get_by_role('button',name='Expand query row',exact=True).click()
                switch=drawer.get_by_role('switch').first
                if not switch.is_checked():
                    page.locator('label[for="'+switch.get_attribute('id')+'"]').click()
                expect(drawer.get_by_role('table')).to_contain_text('24.00',timeout=60000)
            finally:
                browser.close()
