"""Opt-in hardware benchmark; ONLY fictional, disposable loopback services.

Run with the four test-binary variables documented in docs/acceptance.md:
  PYTHONPATH=.:tests python tests/dashboard_benchmark.py
Outputs aggregate sizes/timing/resource costs, never queries or panel readings.
Not a load generator for live endpoints. No endpoint/config command arguments.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import os
from pathlib import Path
import threading
import time

import requests

from scraper.installation import load_installation
from scraper.period_promql import PREFIX
from scraper.periods import SOURCES, calendar_period
from scraper.provision import render_dashboard
from test_home_solar import substitute
from test_measurement_queries import MeasurementQueryTests

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('home.json', 'photovoltaik.json', 'heatpump.json',
         'heatpump-diagnostics.json', 'solar-battery-diagnostics.json')


def populate(stack, installation):
    """Two months of accepted 60s facts for every period source, not live provenance.

    Source -> actual recording-rule gates have separate promtool acceptance.
    Constant synthetic power/SOC, increasing counters, zero NHZ/PV. This stresses
    expressions, not a physically simulated two-month battery. Diagnostics
    deliberately lack raw history (absence is not zero).
    """
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    period = calendar_period(date(2025, 2, 1), date(2025, 4, 1), installation)
    rates = {'grid_import': .4, 'grid_export': .2, 'heating_electricity': .6,
             'water_electricity': .3, 'heating_heat': 1.8, 'water_heat': .9}
    for first in range(0, int(period.seconds / 60), 1440):
        request = ExportMetricsServiceRequest()
        scope = request.resource_metrics.add().scope_metrics.add()
        for key, source in SOURCES.items():
            for field in ('start_seconds', 'end_seconds', 'left', 'right', 'rate',
                          'zero_seconds', 'shape', 'stale_after_seconds', 'valid_until_seconds'):
                metric = scope.metrics.add(name=PREFIX + field)
                for i in range(first + 1, min(first + 1441, int(period.seconds / 60) + 1)):
                    at = period.start.timestamp() + i * 60
                    rate = rates.get(key, 0) / 3600
                    left = 100 + (i - 1) * 60 * rate if source.kind == 'counter' else {
                        'household': 1000, 'pv': 0, 'battery_charge': 0,
                        'battery_discharge': 600, 'soc': 50}.get(key, 0)
                    values = {'start_seconds': at - 60, 'end_seconds': at, 'left': left,
                              'right': left + rate * 60, 'rate': rate, 'zero_seconds': at - 60,
                              'shape': 1, 'stale_after_seconds': 180, 'valid_until_seconds': at + 180}
                    point = metric.gauge.data_points.add(time_unix_nano=int(at * 1e9), as_double=values[field])
                    point.attributes.add(key='metric').value.string_value = key
        response = requests.post(stack.base + '/api/v1/otlp/v1/metrics', data=request.SerializeToString(),
                                 headers={'Content-Type': 'application/x-protobuf'}, timeout=30)
        if not response.ok:
            raise AssertionError('Fictional benchmark ingestion failed')


def benchmark(*, query_panels=True):
    installation = load_installation(ROOT / 'installation.example.json')
    dashboards = [render_dashboard(json.loads((ROOT / 'grafana/provisioning/dashboards' / n).read_text()), installation)
                  for n in NAMES]
    fixture = MeasurementQueryTests('runTest')
    fixture.setUp()
    try:
        populate(fixture, installation)
        period = calendar_period(date(2025, 3, 1), date(2025, 4, 1), installation)
        stop = threading.Event()
        rss = []
        def sample():
            while not stop.wait(.05):
                status = Path(f'/proc/{fixture.process.pid}/status').read_text()
                rss.append(int(next(line.split()[1] for line in status.splitlines() if line.startswith('VmRSS:'))))
        monitor = threading.Thread(target=sample, daemon=True)
        monitor.start()
        try:
            for dashboard in (dashboards if query_panels else ()):
                began = time.monotonic()
                def cpu_seconds():
                    fields = Path(f'/proc/{fixture.process.pid}/stat').read_text().split()
                    return (int(fields[13]) + int(fields[14])) / os.sysconf('SC_CLK_TCK')
                cpu_start = cpu_seconds()
                values = {'__from': int(period.start.timestamp() * 1000), '__to': int(period.end.timestamp() * 1000),
                          '__range_s': int(period.seconds), '__interval': '30m', '__rate_interval': '1h'}
                def query(expression, instant=True):
                    parameters = {'query': substitute(expression, values), 'stats': 'all'}
                    if instant:
                        parameters['time'] = period.end.timestamp()
                    else:
                        parameters.update(start=period.start.timestamp(), end=period.end.timestamp(), step=1800)
                    start = time.monotonic()
                    body = requests.post(fixture.base + '/api/v1/' + ('query' if instant else 'query_range'),
                                         data=parameters, timeout=60).json()
                    if body.get('status') != 'success':
                        raise AssertionError('Fictional benchmark query failed')
                    return body['data'], time.monotonic() - start
                for variable in dashboard['templating']['list']:
                    if variable['type'] == 'query':
                        body, _ = query(variable['query']['query'][len('query_result('):-1])
                        if len(body['result']) != 1:
                            raise AssertionError('Fictional calendar variable unavailable')
                        values[variable['name']] = body['result'][0]['value'][1]
                    else:
                        values[variable['name']] = variable['current']['value']
                variable_seconds = time.monotonic() - began
                targets = [t for p in dashboard['panels'] for t in p.get('targets', []) if t.get('expr')]
                # Force ALL panels, including offscreen ones, at bounded concurrency.
                with ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(lambda t: query(t['expr'], t.get('instant', False)), targets))
                wall_seconds = time.monotonic() - began
                print(json.dumps({'dashboard': dashboard['uid'], 'json_bytes': len(json.dumps(dashboard).encode()),
                                  'targets': len(targets), 'variables_s': round(variable_seconds, 3),
                                  'all_targets_wall_s': round(wall_seconds, 3),
                                  'prometheus_cpu_s': round(cpu_seconds() - cpu_start, 3),
                                  'slowest_target_s': round(max(t for _, t in results), 3),
                                  'peak_query_samples': max(b.get('stats', {}).get('samples', {}).get('peakSamples', 0) for b, _ in results)}), flush=True)
                if wall_seconds >= 60:
                    raise AssertionError('Fictional monthly query set exceeds one-minute refresh budget')
            from period_browser import grafana
            from playwright.sync_api import sync_playwright, expect
            with grafana(dashboards, fixture.base) as (base, _), sync_playwright() as pw:
                from test_heating_browser import wait_provisioned
                wait_provisioned(base, dashboards)
                browser = pw.chromium.launch(executable_path=os.environ['CHROMIUM_TEST_BINARY'], headless=True)
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
                    failures = []
                    responses = []
                    def check(response):
                        if '/api/ds/query' in response.url:
                            body = response.json()
                            for result in body.get('results', {}).values():
                                error = result.get('error', '').lower()
                                if error:
                                    category = next((word for word in ('timeout', 'deadline', 'cancel', 'parse', 'samples', 'limit') if word in error), 'other')
                                    panel_id = response.request.headers.get('x-panel-id', '')
                                    lengths = [len(q.get('expr', '')) for q in (response.request.post_data_json or {}).get('queries', [])]
                                    failures.append({'status': response.status, 'category': category,
                                                     'panel_id': int(panel_id) if panel_id.isdigit() else None,
                                                     'query_bytes': lengths})
                            if response.status >= 400 and not body.get('results'):
                                failures.append({'status': response.status, 'category': 'http'})
                    page.on('response', lambda response: responses.append(response) if '/api/ds/query' in response.url else None)
                    for run in ('cold', 'warm'):
                        for dashboard in dashboards:
                            start = time.monotonic()
                            page.goto(base + '/d/' + dashboard['uid'] + f'?from={int(period.start.timestamp()*1000)}&to={int(period.end.timestamp()*1000)}',
                                      wait_until='networkidle', timeout=120000)
                            expect(page.locator('body')).to_contain_text(dashboard['title'], timeout=60000)
                            if dashboard['uid'] in ('home-energy', 'pv-overview', 'heatpump-overview'):
                                expect(page.locator('body')).to_contain_text('Full period', timeout=60000)
                            page.wait_for_load_state('networkidle', timeout=120000)
                            for response in responses:
                                check(response)
                            responses.clear()
                            if failures:
                                raise AssertionError('Fictional browser datasource failure: ' + json.dumps(failures))
                            heap = page.evaluate('performance.memory.usedJSHeapSize')
                            load_seconds = time.monotonic() - start
                            print(json.dumps({'browser': dashboard['uid'], 'run': run,
                                              'load_s': round(load_seconds, 3), 'js_heap_bytes': heap}), flush=True)
                            if load_seconds >= 60:
                                raise AssertionError('Fictional browser load exceeds one-minute refresh budget')
                finally:
                    browser.close()
        finally:
            stop.set()
            monitor.join()
        print(json.dumps({'prometheus_peak_rss_mib': round(max(rss) / 1024, 1)}))
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    benchmark()
