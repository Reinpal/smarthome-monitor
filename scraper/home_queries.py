"""Small provisioning extensions for homeowner templates; no runtime I/O."""
import json
from pathlib import Path

from scraper.periods import SOURCES, Source

LIVE = {**SOURCES,
        'grid_power': Source('fronius.powerflow.p_grid', 'watts', 'signed', 'grid interconnection'),
        'battery_power': Source('fronius.powerflow.p_akku', 'watts', 'signed', 'battery side'),
        'room': Source('heatpump.anlage.raumtemperatur.isttemperatur_1', 'celsius', 'temperature', 'control zone', 'isg', 'anlage'),
        'room_target': Source('heatpump.anlage.raumtemperatur.solltemperatur_1', 'celsius', 'temperature', 'control zone', 'isg', 'anlage'),
        'water_temperature': Source('heatpump.anlage.warmwasser.isttemperatur', 'celsius', 'temperature', 'tank', 'isg', 'anlage'),
        'water_target': Source('heatpump.anlage.warmwasser.solltemperatur', 'celsius', 'temperature', 'tank', 'isg', 'anlage'),
        'outdoor': Source('heatpump.waermepumpe.prozessdaten.aussentemperatur', 'celsius', 'temperature', 'outdoor sensor', 'isg', 'waermepumpe')}


def live_target(marker):
    """Single-installation endpoint snapshot; OTLP export time is not read time."""
    key = marker['liveMetric']
    source = LIVE[key]
    inputs = {
        'value': source.prom_name,
        'observed': 'smarthome_measurement_last_success_seconds{metric=' + json.dumps(source.name) + '}',
        'present': 'smarthome_measurement_present{metric=' + json.dumps(source.name) + '}',
        'version': 'smarthome_measurement_contract_version',
        'endpoint': f'smarthome_collection_last_success_seconds{{collector="{source.collector}",source="{source.endpoint}"}}',
        'threshold': f'smarthome_collection_stale_after_seconds{{collector="{source.collector}",source="{source.endpoint}"}}',
    }
    s = {k: f'sum({v})' for k, v in inputs.items()}
    checks = [f'(count({v}) == 1)' for v in inputs.values()]
    checks += [f'({s["version"]} == 2)', f'({s["threshold"]} > 0)', f'({s["threshold"]} <= 86400)']
    age = f'(time() - {s["observed"]})'
    checks.append(f'({age} >= 0)')
    if marker.get('liveField') == 'age':
        value = age  # Stale age is useful evidence, not a reassuring missing zero.
    else:
        value = s['value']
        checks += [f'({s["present"]} == 1)', f'({age} < {s["threshold"]})',
                   f'(time() - {s["endpoint"]} >= 0)', f'(time() - {s["endpoint"]} < {s["threshold"]})',
                   f'(abs({value}) < Inf)']
        checks += [f'(abs(sum(timestamp({v})) - sum(timestamp({inputs["value"]}))) <= 2)' for v in inputs.values()]
        if source.kind in ('power', 'counter', 'soc'):
            checks.append(f'({value} >= 0)')
        if source.kind == 'soc':
            checks.append(f'({value} <= 100)')
        if key == 'household':
            value = f'clamp_min(-{value}, 0)'
    return {'refId': marker.get('refId', 'A'), 'expr': f'({value}) and ' + ' and '.join(checks),
            'instant': True, 'range': False, 'editorMode': 'code', 'format': 'time_series',
            'legendFormat': marker.get('legendFormat', key),
            'datasource': {'type': 'prometheus', 'uid': 'prometheus'}}


def render_home_extensions(dashboard, installation):
    """Called only by the shared target resolver; other dashboards are unchanged."""
    from scraper.calendar_promql import CalendarQueries, calendar_variables
    def all_panels(items):
        for panel in items:
            yield panel
            yield from all_panels(panel.get('panels', []))
    panels = list(all_panels(dashboard.get('panels', [])))
    markers = [t for p in panels for t in p.get('targets', [])]
    if any('calendarMetric' in t for t in markers):
        variables = dashboard.setdefault('templating', {}).setdefault('list', [])
        variables[:] = [v for v in variables if not v['name'].startswith('cal_')]
        comparison_keys = {t['calendarMetric'] for t in markers
                           if t.get('calendarMode') in ('current', 'previous', 'change')}
        variables.extend(calendar_variables(installation, comparison_keys))
        calendar = CalendarQueries(installation)
        for panel in panels:
            panel['targets'] = [calendar.target(t) if 'calendarMetric' in t else t for t in panel.get('targets', [])]
            for transformation in panel.get('transformations', []):
                if transformation['id'] == 'formatTime':
                    transformation['options']['timezone'] = str(installation.timezone)
    for panel in panels:
        panel['targets'] = [live_target(t) if 'liveMetric' in t else t for t in panel.get('targets', [])]
    # Staged deliveries cannot promise diagnostics not yet present. Only local
    # template UIDs, never a network lookup or a private Grafana instance.
    if dashboard.pop('homeNavigation', False):
        root = Path(__file__).resolve().parents[1] / 'grafana/provisioning/dashboards'
        known = {json.loads(path.read_text()).get('uid') for path in root.glob('*.json')}
        candidates = [('home-energy', 'Home'), ('pv-overview', 'Solar & battery'),
                      ('heatpump-overview', 'Heating & hot water'),
                      ('solar-battery-diagnostics', 'Solar diagnostics'), ('heatpump-diagnostics', 'Heating diagnostics')]
        dashboard['links'] = [{'title': title, 'type': 'link', 'url': '/d/' + uid,
                               'includeTime': True, 'keepTime': True, 'targetBlank': False}
                              for uid, title in candidates if uid in known and uid != dashboard.get('uid')]
    return dashboard
