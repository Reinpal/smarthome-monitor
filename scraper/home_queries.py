"""Small provisioning extensions for homeowner templates; no runtime I/O."""
import json
from pathlib import Path

from scraper.periods import SOURCES, Source
from scraper.freshness_promql import diagnostic_target, freshness_state

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
    age_only = marker.get('liveField') == 'age'
    s, checks = freshness_state(source, age_only=age_only)
    value = f'(time() - {s["observed"]})' if age_only else s['value']
    if key == 'household' and not age_only:
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
        variables.extend(calendar_variables(installation, comparison_keys,
                                            monthly=any(t.get('calendarMode') == 'monthly' for t in markers)))
        calendar = CalendarQueries(installation)
        for panel in panels:
            panel['targets'] = [resolved for t in panel.get('targets', [])
                                for resolved in (calendar.targets(t) if 'calendarMetric' in t else [t])]
            for transformation in panel.get('transformations', []):
                if transformation['id'] == 'formatTime':
                    transformation['options']['timezone'] = str(installation.timezone)
    for panel in panels:
        panel['targets'] = [live_target(t) if 'liveMetric' in t else
                            diagnostic_target(t) if 'diagnosticQuery' in t else t
                            for t in panel.get('targets', [])]
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
