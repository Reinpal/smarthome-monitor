"""Home/Solar public templates and native queries; fictional, isolated fixtures only."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import time
import unittest

import requests

from scraper.installation import load_installation, parse_installation
from scraper.provision import render_dashboard
from scraper.calendar_promql import CalendarQueries, calendar_variables, zone_offset
from scraper.period_promql import PREFIX, PeriodQueries
from scraper.periods import calendar_period, month_comparison
from energy_fixtures import EnergyFixture

ROOT = Path(__file__).resolve().parents[1]


def template(name):
    return json.loads((ROOT / 'grafana/provisioning/dashboards' / name).read_text())


def substitute(expression, variables):
    return re.sub(r'\$\{([^}]+)\}', lambda m: str(variables[m[1]]), expression)


class HomeTemplateTests(unittest.TestCase):
    def test_layout_contracts_and_no_private_template_values(self):
        home, solar = template('home.json'), template('photovoltaik.json')
        self.assertEqual(home['uid'], 'home-energy')
        self.assertEqual(solar['uid'], 'pv-overview')
        for dashboard in (home, solar):
            self.assertEqual(dashboard['time'], {'from': 'now/M', 'to': 'now'})
            self.assertEqual(dashboard['timezone'], 'browser')
            self.assertNotIn('hideTimepicker', dashboard.get('timepicker', {}))
            ids = [p['id'] for p in dashboard['panels']]
            self.assertEqual(len(ids), len(set(ids)))
            for p in dashboard['panels']:
                self.assertLessEqual(p['gridPos']['x'] + p['gridPos']['w'], 24)
                for target in p.get('targets', []):
                    self.assertNotIn('expr', target)  # Render markers, not public prices/readings.
            self.assertEqual(next(p for p in dashboard['panels'] if p['id'] == 70)['type'], 'barchart')
            gaps = next(p for p in dashboard['panels'] if p['id'] == 71)
            self.assertEqual(gaps['type'], 'table')
            self.assertEqual([t['periodField'] for t in gaps['targets']], ['missing_seconds'] * 3)
            self.assertEqual([t['id'] for t in gaps['transformations']],
                             ['labelsToFields', 'merge', 'organize', 'convertFieldType', 'sortBy', 'formatTime'])
            self.assertIn('monthly self-sufficiency', json.dumps(dashboard))
            self.assertIn('complete', json.dumps(next(p for p in dashboard['panels'] if p['id'] == 90)).lower())
        heads = [p for p in home['panels'] if p['gridPos']['y'] == 0]
        self.assertEqual(len(heads), 6)
        self.assertEqual([p['targets'][0]['periodMetric'] for p in heads],
                         ['household', 'grid_import', 'self_sufficiency', 'solar_battery_benefit', 'heatpump_electricity', 'heatpump_ratio'])
        for p in heads:
            self.assertEqual(len(p['targets']),1)  # Context must not hide an absent value frame.
            self.assertIn('Selected local period', p['description'])
            self.assertTrue(p['targets'][0]['periodQualification'])
            self.assertTrue(all('${__from}' in link['url'] and '${__to}' in link['url'] for link in p['links']))
        self.assertNotIn('1M', json.dumps(home.get('timepicker')))
        self.assertNotIn(109, [p['id'] for p in solar['panels']])
        self.assertNotIn('solar_self_consumption', json.dumps(solar))
        battery_row = [p for p in solar['panels'] if p['gridPos']['y'] == 5]
        self.assertEqual([p['id'] for p in battery_row], [105, 106, 107, 108])
        self.assertEqual([(p['gridPos']['x'], p['gridPos']['w']) for p in battery_row],
                         [(0, 6), (6, 6), (12, 6), (18, 6)])
        self.assertIn('self_sufficiency', json.dumps(solar))
        self.assertIn('battery_discharge', json.dumps(solar))

    def test_rendered_targets_links_and_configuration_are_connected(self):
        installation = load_installation(ROOT / 'installation.example.json')
        for name in ('home.json', 'photovoltaik.json'):
            before = template(name)
            dashboard = render_dashboard(before, installation)
            self.assertEqual(before, template(name))
            known = {json.loads(p.read_text())['uid'] for p in (ROOT / 'grafana/provisioning/dashboards').glob('*.json')}
            for link in dashboard['links']:
                self.assertIn(link['url'].split('/')[-1], known)
                self.assertTrue(link['includeTime'])
            self.assertEqual(dashboard['timezone'], 'Europe/Berlin')
            for p in dashboard['panels']:
                for target in p.get('targets', []):
                    self.assertIn('expr', target)
                    self.assertTrue(target['instant'])
                    self.assertFalse(target['range'])
                    self.assertEqual(target['datasource']['uid'], 'prometheus')
                    self.assertNotIn('or vector(0)', target['expr'])
            variables = {v['name']: v for v in dashboard['templating']['list']}
            self.assertEqual(variables['cal_day_00']['refresh'], 2)
            self.assertTrue(variables['cal_previous_end']['skipUrlSync'])
            self.assertEqual(len([v for v in variables if v.startswith('cal_day_') and v != 'cal_day_wall']), 33)
            self.assertEqual(next(p for p in dashboard['panels'] if p['id'] == 70)['transformations'][-1]['options']['timezone'], 'Europe/Berlin')


@unittest.skipUnless(os.environ.get('PROMETHEUS_TEST_BINARY'), 'set PROMETHEUS_TEST_BINARY for isolated Home/Solar acceptance')
class HomeQueryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = self.enterContext(EnergyFixture())
        self.base = self.fixture.base
        self.installation = self.fixture.installation

    def query(self, expression, at):
        return self.fixture.query(expression, at)

    def variables(self, period, installation=None):
        values = {'__from': int(period.start.timestamp()*1000), '__to': int(period.end.timestamp()*1000), '__range_s': int(period.seconds)}
        for variable in calendar_variables(installation or self.installation):
            query = variable['query']['query'][len('query_result('):-1]
            result = self.query(substitute(query, values), period.end.timestamp())
            self.assertEqual(len(result), 1, 'Calendar query variable unavailable')
            value = float(result[0]['value'][1])
            self.assertEqual(value, int(value))
            values[variable['name']] = int(value)
        return values

    def value(self, target, values, expected=None):
        result = self.query(substitute(target['expr'], values), values['__to']/1000)
        if expected is None:
            self.assertEqual(result, [])
        else:
            self.assertEqual(len(result), 1)
            self.assertAlmostEqual(float(result[0]['value'][1]), expected, places=6)
        return result

    def push_night(self, period, *, missing=None, pv=0, charge=0, discharge=600):
        """Accepted interval facts via real OTLP, not a fake Grafana datasource.

        Recording-rule source provenance/reset/sign gates are independently tested
        in test_period_promql. A night begins with fictional 90% SOC and depletes
        at battery-side power / fictional usable capacity; no household readings.
        """
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
        request = ExportMetricsServiceRequest()
        scope = request.resource_metrics.add().scope_metrics.add()
        start, minutes = period.start.timestamp(), int(period.seconds/60)
        for key in ('household','grid_import','grid_export','pv','battery_charge','battery_discharge','soc'):
            for field in ('start_seconds','end_seconds','left','right','rate','zero_seconds','shape','stale_after_seconds','valid_until_seconds'):
                metric = scope.metrics.add(name=PREFIX + field)
                for i in range(1, minutes+1):
                    if missing == (key,i):
                        continue
                    at = start + i*60
                    rate = (.4 if key == 'grid_import' else 0)/3600 if key.startswith('grid_') else -discharge/1000/float(self.installation.usable_battery_kwh)/36 if key=='soc' else 0
                    def endpoint(index):
                        if key.startswith('grid_'):return 100+index*60*rate
                        if key=='soc':return 90+index*60*rate
                        return {'household':1000,'pv':pv,'battery_charge':charge,'battery_discharge':discharge}[key]
                    value={'start_seconds':at-60,'end_seconds':at,'left':endpoint(i-1),'right':endpoint(i),'rate':rate,'zero_seconds':start,'shape':1,'stale_after_seconds':180,'valid_until_seconds':at+180}[field]
                    point=metric.gauge.data_points.add(time_unix_nano=int(at*1e9),as_double=value)
                    point.attributes.add(key='metric').value.string_value=key
        response=requests.post(self.base+'/api/v1/otlp/v1/metrics',data=request.SerializeToString(),headers={'Content-Type':'application/x-protobuf'},timeout=20)
        self.assertTrue(response.ok,'Fictional night ingestion failed')

    def test_fresh_endpoint_snapshots_use_field_reads_not_export_timestamps(self):
        from scraper.home_queries import live_target
        self.fixture.source(0)
        at=self.fixture.start.timestamp()
        for key,expected in [('pv',1500),('household',1000),('battery_power',-300),('soc',60)]:
            result=self.query(live_target({'liveMetric':key})['expr'],at)
            self.assertAlmostEqual(float(result[0]['value'][1]),expected)
        self.fixture.source(1,missing=True)
        self.assertEqual(self.query(live_target({'liveMetric':'household'})['expr'],at+60),[])
        self.fixture.push(at+360)  # Cached export is not another device read.
        self.assertEqual(self.query(live_target({'liveMetric':'pv'})['expr'],at+360),[])
        age=self.query(live_target({'liveMetric':'pv','liveField':'age'})['expr'],at+360)
        self.assertEqual(float(age[0]['value'][1]),300)

    def test_qualified_headline_keeps_valid_zero_and_withholds_missing_value(self):
        from scraper.periods import Period
        period = Period(self.fixture.start, self.fixture.start + timedelta(minutes=2))
        self.push_night(period, pv=0)
        dashboard = render_dashboard(template('photovoltaik.json'), self.installation)
        target = next(p for p in dashboard['panels'] if p['id'] == 100)['targets'][0]
        for start, end, label in (
            (period.start, period.end, 'Full period'),
            (period.start, period.end + timedelta(seconds=30), 'Observed prefix'),
            (period.start, period.end + timedelta(seconds=181), None),
            (period.start - timedelta(minutes=1), period.end, None),
        ):
            values = {'__from': int(start.timestamp()*1000), '__to': int(end.timestamp()*1000),
                      '__range_s': int(end.timestamp()-start.timestamp())}
            rows = self.query(substitute(target['expr'], values), end.timestamp())
            if label is None:
                self.assertEqual(rows, [], 'Coverage label must not substitute for an absent headline')
            else:
                self.assertEqual(len(rows), 1)
                self.assertEqual(float(rows[0]['value'][1]), 0)
                self.assertEqual(rows[0]['metric']['coverage'], label)

    def test_native_calendar_boundaries_match_reference_dst_shorter_months_and_non_hour_zone(self):
        cases = [(date(2025,3,1),date(2025,4,1)), (date(2024,3,1),date(2024,4,1)),
                 (date(2025,10,1),date(2025,11,1)),(date(2025,1,1),date(2025,1,16)),
                 (date(2025,5,1),date(2025,5,2))]
        for start,end in cases:
            period=calendar_period(start,end,self.installation)
            variables=self.variables(period)
            current,previous=month_comparison(period,self.installation)
            self.assertEqual(variables['cal_current_end'],current.end.timestamp())
            self.assertEqual(variables['cal_previous_start'],previous.start.timestamp())
            self.assertEqual(variables['cal_previous_end'],previous.end.timestamp())
            self.assertEqual(variables['cal_compare_seconds'],current.seconds)
        for zone,day,hours in [('Europe/Berlin',date(2025,3,30),23),('Europe/Berlin',date(2025,10,26),25),('Australia/Lord_Howe',date(2025,10,5),23.5),('Asia/Kathmandu',date(2025,6,1),24)]:
            raw=deepcopy(self.fixture.raw);raw['installation']['timezone']=zone
            installation=parse_installation(raw)
            period=calendar_period(day,day+timedelta(days=1),installation)
            values=self.variables(period,installation)
            self.assertEqual(values['cal_day_00'],period.start.timestamp())
            self.assertEqual(values['cal_day_01']-values['cal_day_00'],hours*3600)
        # Outside generated TZDB horizon, and ambiguous/nonexistent local times:
        for wall in (datetime(2025,3,30,2,30,tzinfo=timezone.utc),datetime(2025,10,26,2,30,tzinfo=timezone.utc)):
            expr='vector('+zone_offset(str(wall.timestamp()),self.installation.timezone,wall=True)+') < Inf > -Inf'
            self.assertEqual(self.query(expr,wall.timestamp()),[])
        self.assertEqual(self.query('vector('+zone_offset('0',self.installation.timezone)+') < Inf > -Inf',0),[])

    def test_compact_timezone_candidates_preserve_transition_and_horizon_uniqueness(self):
        from zoneinfo import ZoneInfo
        from scraper.calendar_promql import zone_spans
        for name in ('Europe/Berlin', 'Australia/Lord_Howe', 'Asia/Kathmandu', 'Pacific/Apia', 'UTC'):
            zone = ZoneInfo(name)
            spans = zone_spans(zone)
            # Historical political/date-line changes as well as regular DST.
            transitions = [(a, b) for a, b in zip(spans, spans[1:])
                           if 2011 <= datetime.fromtimestamp(b[0], timezone.utc).year <= 2012]
            for before, after in transitions:
                boundary = after[0]
                for at in (boundary - 1, boundary, boundary + 1):
                    expr = 'vector(' + zone_offset(str(at), zone) + ') < Inf > -Inf'
                    rows = self.query(expr, at)
                    self.assertEqual(float(rows[0]['value'][1]), datetime.fromtimestamp(at, zone).utcoffset().total_seconds())
                low, high = sorted((boundary + before[2], boundary + after[2]))
                for wall in (low, (low + high) // 2, high - 1):
                    expr = 'vector(' + zone_offset(str(wall), zone, wall=True) + ') < Inf > -Inf'
                    self.assertEqual(self.query(expr, boundary), [], 'Gap/fold must not pick an arbitrary offset')
                expr = 'vector(' + zone_offset(str(high), zone, wall=True) + ') < Inf > -Inf'
                self.assertEqual(float(self.query(expr, boundary)[0]['value'][1]), after[2])
            for at, available in ((spans[0][0] - 1, False), (spans[0][0], True),
                                  (spans[-1][1] - 1, True), (spans[-1][1], False)):
                rows = self.query('vector(' + zone_offset(str(at), zone) + ') < Inf > -Inf', at)
                self.assertEqual(bool(rows), available)

    def test_native_previous_month_queries_require_equal_complete_history(self):
        period=calendar_period(date(2025,3,1),date(2025,4,1),self.installation)
        current,previous=month_comparison(period,self.installation)
        self.fixture.push_intervals(previous.start.timestamp(),int(previous.seconds/60))
        self.fixture.push_intervals(period.start.timestamp(),int(period.seconds/60))
        values=self.variables(period)
        q=CalendarQueries(self.installation)
        for mode,expected in [('current',672),('previous',672),('change',0)]:
            self.value(q.target({'calendarMetric':'household','calendarMode':mode}),values,expected)
        # Main headline is NOT shortened to February's 28-day duration (March DST).
        self.value(PeriodQueries(self.installation).target('household'),values,743)
        absent=calendar_period(date(2025,2,1),date(2025,2,2),self.installation)
        self.value(q.target({'calendarMetric':'household','calendarMode':'previous'}),self.variables(absent))
        middle=calendar_period(date(2025,3,2),date(2025,3,3),self.installation)
        self.value(q.target({'calendarMetric':'household','calendarMode':'current'}),self.variables(middle))

    def test_jittered_boundary_bracketing_and_live_comparison_prefix(self):
        from scraper.periods import Period
        start=datetime(2025,5,1,tzinfo=self.installation.timezone)
        previous=datetime(2025,4,1,tzinfo=self.installation.timezone)
        # Source observations arrive 30 seconds off the recording grid. The
        # closing observation for a full day arrives AFTER that day's midnight.
        self.fixture.push_intervals(previous.timestamp()-30,1441)
        self.fixture.push_intervals(start.timestamp()-30,1441)
        period=Period(start,start+timedelta(days=1))
        values=self.variables(period)
        q=CalendarQueries(self.installation)
        rows=self.query(substitute(q.target({'calendarMetric':'household','calendarMode':'daily'})['expr'],values),period.end.timestamp())
        self.assertEqual([float(r['value'][1]) for r in rows],[24])
        self.value(q.target({'calendarMetric':'household','calendarMode':'current'}),values,24)
        self.value(q.target({'calendarMetric':'household','calendarMode':'previous'}),values,24)
        # A live-like selection ends 30s after the latest actual observation.
        # Compare its observed prefix to exactly equal elapsed previous coverage.
        live=Period(start,start+timedelta(days=1,seconds=60))
        values=self.variables(live)
        self.assertEqual(values['cal_household_seconds'],86430)
        self.value(q.target({'calendarMetric':'household','calendarMode':'current'}),values,86430/3600)
        self.value(q.target({'calendarMetric':'household','calendarMode':'previous'}),values,86430/3600)

    def test_daily_chart_is_local_complete_buckets_not_rolling_energy(self):
        period=calendar_period(date(2025,3,29),date(2025,3,31),self.installation)
        self.fixture.push_intervals(period.start.timestamp(),int(period.seconds/60))
        values=self.variables(period)
        target=CalendarQueries(self.installation).target({'calendarMetric':'household','calendarMode':'daily'})
        rows=self.query(substitute(target['expr'],values),period.end.timestamp())
        self.assertEqual(sorted(float(r['value'][1]) for r in rows),[23,24])
        self.assertEqual(sorted(int(r['metric']['day']) for r in rows),[values['cal_day_00'],values['cal_day_01']])
        # A partly selected date and the current incomplete date cannot masquerade
        # as complete daily totals. Full day still displayed; no zero-filled bar.
        from scraper.periods import Period
        partial=Period(period.start+timedelta(hours=1),period.end)
        vals=self.variables(partial)
        rows=self.query(substitute(target['expr'],vals),partial.end.timestamp())
        self.assertEqual([float(r['value'][1]) for r in rows],[23])
        # A 31-day autumn month lasts 745 hours: do not reject it with a 744h cap.
        october=calendar_period(date(2025,10,1),date(2025,11,1),self.installation)
        dst_day=calendar_period(date(2025,10,26),date(2025,10,27),self.installation)
        self.fixture.push_intervals(dst_day.start.timestamp(),int(dst_day.seconds/60))
        rows=self.query(substitute(target['expr'],self.variables(october)),october.end.timestamp())
        self.assertEqual([float(r['value'][1]) for r in rows],[25])  # Other dates absent, not zero bars.

    def test_daily_missing_seconds_keeps_incomplete_and_absent_dates_visible(self):
        # Spring DST: a missing day is 23h, not an assumed 24h. One missing
        # minute must remain visible as a gap, never as a zero-energy bar.
        period = calendar_period(date(2025, 3, 29), date(2025, 3, 31), self.installation)
        self.fixture.push_intervals(period.start.timestamp(), 1440,
                                    missing=('household', 720))
        values = self.variables(period)
        q = CalendarQueries(self.installation)
        marker = {'calendarMetric': 'household', 'calendarMode': 'daily'}
        energy = self.query(substitute(q.target(marker)['expr'], values), period.end.timestamp())
        self.assertEqual(energy, [])
        target = q.target({**marker, 'periodField': 'missing_seconds'})
        rows = self.query(substitute(target['expr'], values), period.end.timestamp())
        self.assertEqual({int(r['metric']['day']): float(r['value'][1]) for r in rows},
                         {values['cal_day_00']: 60, values['cal_day_01']: 23 * 3600})
        # A complete dependency remains visibly complete (0 missing seconds).
        target = q.target({**marker, 'calendarMetric': 'grid_import', 'periodField': 'missing_seconds'})
        rows = self.query(substitute(target['expr'], values), period.end.timestamp())
        self.assertEqual(sorted(float(r['value'][1]) for r in rows), [0, 23 * 3600])
        from scraper.periods import Period
        partial = Period(period.start + timedelta(hours=1), period.end - timedelta(hours=1))
        self.assertEqual(self.query(substitute(target['expr'], self.variables(partial)),
                                    partial.end.timestamp()), [])

    def test_observed_night_queries_dst_and_measured_conditions_not_runtime_forecast(self):
        from scraper.periods import Period
        for day,hours in [(date(2025,3,29),11),(date(2025,6,1),12),(date(2025,10,25),13)]:
            start=datetime.combine(day,datetime.min.time(),self.installation.timezone)+timedelta(hours=18)
            end=datetime.combine(day+timedelta(days=1),datetime.min.time(),self.installation.timezone)+timedelta(hours=6)
            period=Period(start,end)
            self.push_night(period)
            values=self.variables(period)
            q=CalendarQueries(self.installation)
            for key,expected in [('overnight_coverage',60),('battery_discharge',hours*.6),('battery_inventory_change',-hours*.6),('grid_import',hours*.4),('night_start',start.timestamp()*1000),('night_end',end.timestamp()*1000)]:
                self.value(q.target({'calendarMetric':key,'calendarMode':'overnight'}),values,expected)
            self.assertEqual(values['cal_night_end']-values['cal_night_start'],hours*3600)

    def test_night_missing_pv_charging_and_outside_selection_withhold(self):
        from scraper.periods import Period
        q=CalendarQueries(self.installation)
        target=q.target({'calendarMetric':'overnight_coverage','calendarMode':'overnight'})
        for i,kwargs in enumerate([{'missing':('pv',300)}, {'pv':1}, {'charge':1}, {'missing':('soc',400)}]):
            start=datetime(2025,6,i+1,18,tzinfo=self.installation.timezone)
            period=Period(start,start+timedelta(hours=12))
            self.push_night(period,**kwargs)
            self.value(target,self.variables(period))
        period=Period(datetime(2025,6,8,18,tzinfo=self.installation.timezone),datetime(2025,6,9,6,tzinfo=self.installation.timezone))
        self.push_night(period)
        partial=Period(period.start+timedelta(hours=1),period.end)
        self.value(target,self.variables(partial))
        raw=deepcopy(self.fixture.raw);raw['installation']['usable_battery_kwh']=0
        self.value(CalendarQueries(parse_installation(raw)).target({'calendarMetric':'overnight_coverage','calendarMode':'overnight'}),self.variables(period))

    @unittest.skipUnless(os.environ.get('GRAFANA_TEST_HOME') and os.environ.get('CHROMIUM_TEST_BINARY'),
                         'set Grafana and Chromium binaries for daily-gap browser acceptance')
    def test_daily_gap_table_renders_incomplete_date_in_grafana(self):
        from period_browser import grafana
        from playwright.sync_api import sync_playwright, expect
        period = calendar_period(date(2025, 3, 29), date(2025, 3, 30), self.installation)
        self.fixture.push_intervals(period.start.timestamp(), 1440, missing=('household', 720))
        dashboard = render_dashboard(template('home.json'), self.installation)
        with grafana([dashboard], self.base) as (base, _), sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.environ['CHROMIUM_TEST_BINARY'], headless=True)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
                page.goto(base + '/d/home-energy?viewPanel=71&from=' + str(int(period.start.timestamp()*1000))
                          + '&to=' + str(int(period.end.timestamp()*1000)), wait_until='networkidle', timeout=120000)
                expect(page.get_by_role('row').filter(has_text='2025-03-29')).to_be_visible(timeout=90000)
                row = page.get_by_role('row').filter(has_text='2025-03-29')
                expect(row).to_contain_text('1.0 min')
                expect(row).to_contain_text('0 s')
                expect(row).to_contain_text('1.0 day')
                expect(page.locator('body')).to_contain_text('missing time (0 = complete)')
            finally:
                browser.close()

    @unittest.skipUnless(os.environ.get('GRAFANA_TEST_HOME') and os.environ.get('CHROMIUM_TEST_BINARY'),
                         'set GRAFANA_TEST_HOME and CHROMIUM_TEST_BINARY for native Home/Solar browser acceptance')
    def test_provisioned_browser_time_navigation_daily_comparison_night_and_no_data(self):
        from period_browser import grafana
        from playwright.sync_api import sync_playwright, expect
        from scraper.periods import Period
        february=calendar_period(date(2025,2,1),date(2025,3,1),self.installation)
        march=calendar_period(date(2025,3,1),date(2025,4,1),self.installation)
        self.fixture.push_intervals(february.start.timestamp(),int(february.seconds/60))
        self.fixture.push_intervals(march.start.timestamp(),int(march.seconds/60))
        night=Period(datetime(2025,6,1,18,tzinfo=self.installation.timezone),datetime(2025,6,2,6,tzinfo=self.installation.timezone))
        self.push_night(night)
        dashboards=[render_dashboard(template(name),self.installation) for name in ('home.json','photovoltaik.json')]
        with grafana(dashboards,self.base) as (base,output), sync_playwright() as pw:
            for dashboard in dashboards:
                for _ in range(240):
                    if requests.get(base+'/api/dashboards/uid/'+dashboard['uid'],timeout=5).ok:
                        break
                    time.sleep(.25)
                else:
                    self.fail('Home/Solar provisioning did not complete')
            browser=pw.chromium.launch(executable_path=os.environ['CHROMIUM_TEST_BINARY'],headless=True)
            try:
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.route('**/*',lambda route: route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                errors=[]
                def check_response(request):
                    # Headers may arrive before the body. Inspect only finished
                    # requests so navigation cannot discard an in-flight body.
                    if '/api/ds/query' in request.url:
                        body=request.response().json()
                        for result in body.get('results',{}).values():
                            if result.get('error'):errors.append(result['error'])
                page.on('requestfinished',check_response)
                def selection(period):
                    return f'?from={int(period.start.timestamp()*1000)}&to={int(period.end.timestamp()*1000)}'
                selected=selection(march)
                page.goto(base+'/d/home-energy'+selected,wait_until='networkidle',timeout=120000)
                expect(page.locator('body')).to_contain_text('743.00',timeout=90000)
                expect(page.locator('body')).to_contain_text('Full period',timeout=30000)
                expect(page.locator('body')).to_contain_text('Provisional',timeout=30000)
                expect(page.locator('body')).to_contain_text('Unavailable')  # No fabricated VD inputs.
                page.get_by_role('link',name='Solar & battery',exact=True).click()
                page.wait_for_url('**/d/pv-overview**',timeout=60000)
                from urllib.parse import urlparse, parse_qs
                linked=parse_qs(urlparse(page.url).query)
                for field,instant in [('from',march.start),('to',march.end)]:
                    self.assertEqual(datetime.fromisoformat(linked[field][0].replace('Z','+00:00')).timestamp(),instant.timestamp())
                expect(page.locator('body')).to_contain_text('743.00',timeout=90000)
                page.goto(base+'/d/home-energy'+selected+'&viewPanel=80',wait_until='networkidle',timeout=120000)
                expect(page.locator('body')).to_contain_text('672.00',timeout=90000)
                expect(page.locator('body')).to_contain_text('Previous · same elapsed seconds')
                # Native transformed chart data is inspectable, not a static plot.
                page.goto(base+'/d/home-energy'+selected+'&viewPanel=70&inspect=70&inspectTab=data',wait_until='networkidle',timeout=120000)
                expect(page.locator('body')).to_contain_text('Household (estimate)',timeout=90000)
                page.get_by_role('button',name='Expand query row',exact=True).click()
                switch_id=page.get_by_role('switch').first.get_attribute('id')
                page.locator('label[for="'+switch_id+'"]').click()
                page.get_by_role('table').hover()
                page.mouse.wheel(0,2400)
                expect(page.get_by_role('row',name='2025-03-30 23.00 kWh 9.20 kWh',exact=True)).to_be_visible(timeout=30000)
                self.assertFalse(errors, 'Grafana native query error: '+str(errors[:1]))
                page.goto(base+'/d/pv-overview'+selection(night)+'&viewPanel=140',wait_until='networkidle',timeout=120000)
                expect(page.locator('body')).to_contain_text('60.00',timeout=90000)
                with page.expect_response(lambda response:'/api/ds/query' in response.url,timeout=30000):
                    page.get_by_role('button',name='Refresh',exact=True).click()
                # A native picker control changes the window without Python rendering.
                page.get_by_role('button',name='Move time range backwards',exact=True).click()
                expect(page.locator('body')).to_contain_text('Unavailable',timeout=90000)
                page.goto(base+'/d/pv-overview'+selection(night)+'&viewPanel=122',wait_until='networkidle',timeout=120000)
                expect(page.locator('body')).to_contain_text('1.64',timeout=90000)
                corrected=deepcopy(self.fixture.raw)
                corrected['import_tariffs'][0]['components']['energy']['amount']=.20
                updated=render_dashboard(template('photovoltaik.json'),parse_installation(corrected))
                (output/'pv-overview.json').write_text(json.dumps(updated))
                for _ in range(120):
                    provisioned=requests.get(base+'/api/dashboards/uid/pv-overview',timeout=5).json()
                    if '* 0.324' in json.dumps(provisioned):break
                    time.sleep(.25)
                else:self.fail('Corrected Solar queries were not reprovisioned')
                page.reload(wait_until='networkidle',timeout=120000)
                expect(page.locator('body')).to_contain_text('2.33',timeout=90000)
                # Missing history withholds, rather than reusing the previous night.
                absent=calendar_period(date(2025,6,5),date(2025,6,6),self.installation)
                page.goto(base+'/d/pv-overview'+selection(absent)+'&viewPanel=140',wait_until='networkidle',timeout=120000)
                expect(page.locator('body')).to_contain_text('Unavailable',timeout=90000)
                page.goto(base+'/d/pv-overview'+selection(absent)+'&viewPanel=122',wait_until='networkidle',timeout=120000)
                expect(page.locator('body')).to_contain_text('Unavailable',timeout=90000)
                self.assertFalse(errors, 'Grafana native query error: '+str(errors[:1]))
                page.set_viewport_size({'width':390,'height':844})
                page.goto(base+'/d/home-energy'+selection(night),wait_until='networkidle',timeout=120000)
                expect(page.get_by_text('Household electricity',exact=True)).to_be_visible(timeout=90000)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),410)
            finally:
                browser.close()


if __name__=='__main__':
    unittest.main()
