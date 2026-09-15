#!/usr/bin/env python3
"""Opt-in PRIVATE read-only validation. Prints fixed check names/statuses only.

Docker inspect + internal HTTP GET only; no exec, writes, reloads or screenshots.
Run locally with authorized Docker access. Never enable HTTP/debug logging.
Raw responses, credentials, addresses and history remain in memory.
"""
import base64
import ipaddress
import json
import re
import subprocess
import time
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, ProxyHandler, build_opener

DASHBOARDS = ('home-energy', 'pv-overview', 'heatpump-overview',
              'heatpump-diagnostics', 'solar-battery-diagnostics')
SERIES = ('fronius_powerflow_p_load_watts', 'fronius_meter_energy_real_abs_plus_Wh',
          'heatpump_waermepumpe_leistungsaufnahme_vd_heizen_summe_MWh')


def audit(container, get):
    """get(port, path, parameters) returns decoded JSON, never logs errors."""
    results = {}
    def check(name, predicate):
        try:
            results[name] = 'PASS' if predicate() else 'FAIL'
        except Exception:
            results[name] = 'UNAVAILABLE'
    def query(expression):
        body = get(9090, '/api/v1/query', {'query': expression})
        if body.get('status') != 'success':
            raise ValueError('query unavailable')
        return body['data']['result']
    def present(expression):
        return len(query(expression)) == 1
    def dashboard(uid):
        body = get(3000, '/api/dashboards/uid/' + uid, {})
        return body['meta']['provisioned'] and body['dashboard']['uid'] == uid
    check('grafana_database_health', lambda: get(3000, '/api/health', {})['database'] == 'ok')
    def same_stack():
        datasource = get(3000, '/api/datasources/uid/prometheus', {})
        url = urlparse(datasource['url'])
        return datasource['access'] == 'proxy' and url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1') and url.port == 9090
    check('datasource_same_stack_boundary', same_stack)
    check('datasource_uid_and_post', lambda: (
        (d := get(3000, '/api/datasources/uid/prometheus', {}))['type'] == 'prometheus'
        and d['uid'] == 'prometheus' and d.get('jsonData', {}).get('httpMethod') == 'POST'))
    check('grafana_datasource_health', lambda: get(3000, '/api/datasources/uid/prometheus/health', {})['status'] == 'OK')
    check('existing_overviews_provisioned', lambda: all(dashboard(uid) for uid in DASHBOARDS[1:3]))
    check('integrated_dashboards_provisioned', lambda: all(dashboard(uid) for uid in DASHBOARDS))
    check('no_direct_grafana_host_port', lambda: not container['NetworkSettings']['Ports'].get('3000/tcp'))
    check('existing_data_mount', lambda: len([m for m in container['Mounts'] if m['Destination'] == '/data' and m['RW']]) == 1)
    check('five_year_retention', lambda: bool(re.search(r'\btime:\s*5y\b', get(9090, '/api/v1/status/config', {})['data']['yaml'])))
    check('contract_v2_deployed', lambda: present('smarthome_measurement_contract_version == 2'))
    check('field_metadata_deployed', lambda: present('count(smarthome_measurement_present) > 0'))
    check('interval_rules_loaded', lambda: any(g['name'] == 'smarthome-period-intervals-v1' for g in get(9090, '/api/v1/rules', {})['data']['groups']))
    check('intervals_queryable', lambda: present('count(smarthome_period_v1_end_seconds) > 0'))
    for index, series in enumerate(SERIES, 1):
        check(f'representative_source_{index}_queryable', lambda s=series: present('count(' + s + ') == 1'))
    # Presence-only, coarse history check. Not per-field coverage or an energy query.
    # No durations, boundaries, samples or labels leave this function.
    now = int(time.time())
    def coverage(start, end):
        for series in SERIES:
            body = get(9090, '/api/v1/query_range', {
                'query': 'sum(present_over_time(' + series + '[5m])) or vector(0)',
                'start': start, 'end': end, 'step': 300})
            rows = body['data']['result']
            if len(rows) != 1 or len(rows[0]['values']) != (end - start) // 300 + 1:
                return False
            if not all(float(v) == 1 for _, v in rows[0]['values']):
                return False
        return True
    check('representative_recent_month_presence_complete', lambda: coverage(now - 30 * 86400, now))
    check('representative_prior_year_boundary_present', lambda: coverage(now - 365 * 86400 - 300, now - 365 * 86400))
    return results


def main():
    try:
        completed = subprocess.run(['docker', 'inspect', 'lgtm'], capture_output=True, timeout=10, check=True)
        container = json.loads(completed.stdout)[0]
        addresses = [n['IPAddress'] for n in container['NetworkSettings']['Networks'].values() if n.get('IPAddress')]
        address = next(a for a in addresses if ipaddress.ip_address(a).is_private)
        env = dict(item.split('=', 1) for item in container['Config']['Env'] if '=' in item)
        user = env.get('GF_SECURITY_ADMIN_USER', 'admin')
        password = env.get('GF_SECURITY_ADMIN_PASSWORD')
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None  # Never forward credentials to another origin.
        opener = build_opener(ProxyHandler({}), NoRedirect())  # Never use a host HTTP proxy.
        def get(port, path, parameters):
            request = Request(f'http://{address}:{port}' + path + ('?' + urlencode(parameters) if parameters else ''), method='GET')
            if port == 3000 and password:
                token = base64.b64encode((user + ':' + password).encode()).decode()
                request.add_header('Authorization', 'Basic ' + token)
            with opener.open(request, timeout=15) as response:
                return json.load(response)
        results = audit(container, get)
    except Exception:
        print('live_readonly_access: UNAVAILABLE')
        return 1
    for name, status in results.items():
        print(name + ': ' + status)
    return 0 if all(v == 'PASS' for v in results.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
