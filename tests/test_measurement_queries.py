"""Optional isolated Prometheus: fictional source -> SDK -> OTLP -> real PromQL.

Set PROMETHEUS_TEST_BINARY to an unpacked official Prometheus binary. This starts
only a loopback subprocess with a disposable TSDB, never Docker or a live service.
"""

import json
import os
from pathlib import Path
import time
import unittest

from energy_fixtures import EnergyFixture


@unittest.skipUnless(os.environ.get('PROMETHEUS_TEST_BINARY'), 'set PROMETHEUS_TEST_BINARY for real PromQL tests')
class MeasurementQueryTests(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(EnergyFixture())
        self.base = self.stack.base
        self.process = self.stack.process  # Benchmark resource sampling only.

    def push(self, timestamp):
        self.stack.push(timestamp)

    def query(self, expression, timestamp):
        return [float(p['value'][1]) for p in self.stack.query(expression, timestamp)]

    def test_interval_rules_wait_for_in_flight_exports(self):
        """An export timestamp precedes its arrival; retrospective offset reads
        must not skip it when a rule tick races OTLP ingestion.

        Drive the real SDK/Prometheus seam in event order without sleeping for
        minute ticks. No samples are delivered before their simulated arrival.
        """
        from scraper.period_promql import recording_rules

        group = recording_rules()['groups'][0]
        delay = float(group.get('query_offset', '0s').removesuffix('s'))
        rules = {r['record']: r['expr'] for r in group['rules']
                 if r['labels']['metric'] == 'household'}
        start = int(time.time()) - 900
        for i in (-2, -1):
            self.stack.source(i, stamp=start + i * 60)
        intervals = []
        for i in range(4):
            # Tick at +0.1s; this minute's timestamped export arrives at +0.5s.
            at = start + i * 60 + .1 - delay
            left = self.query(rules['smarthome_period_v1_start_seconds'], at)
            right = self.query(rules['smarthome_period_v1_end_seconds'], at)
            if left and right:
                intervals.append((left[0], right[0]))
            self.stack.source(i, stamp=start + i * 60)
        self.assertEqual(len(intervals), 4, 'in-flight exports must not lose intervals')
        self.assertTrue(all(b - a == 60 for a, b in intervals))
        self.assertTrue(all(a[1] == b[0] for a, b in zip(intervals, intervals[1:])))

    def test_source_export_names_panels_resets_missing_and_cached_queries(self):
        start = int(time.time()) - 900
        for i, (battery, grid, total) in enumerate([(-300, -200, 1000), (400, 200, 1300), (0, 0, 20)]):
            now = start + i * 60
            self.stack.solar({'powerflow': {'Site': {'P_PV': 1000 if i == 0 else 0, 'P_Akku': battery,
                                                       'P_Grid': grid, 'P_Load': -500}},
                                'meter': {'fictional-grid': {'Meter_Location_Current': 0,
                                                            'Visible': 1, 'Enable': 1,
                                                            'EnergyReal_WAC_Plus_Absolute': total}}}, now)
            self.stack.heatpump({'WÄRMEMENGE': {'VD HEIZEN SUMME': '1,2MWh'},
                                   'LEISTUNGSAUFNAHME': {'VD HEIZEN SUMME': '400kWh'}}, now)
            self.push(now)
            self.assertEqual(self.query('fronius_meter_energy_real_abs_plus_Wh', now), [total])
            self.assertEqual(self.query('heatpump_waermepumpe_leistungsaufnahme_vd_heizen_summe_MWh', now), [0.4])
            self.assertEqual(self.query('smarthome_measurement_contract_version', now), [2])

        # Provisioned homeowner targets genuinely query translated OTLP series.
        # The overview uses signed battery-side power, not the old split plot.
        from scraper.installation import load_installation
        from scraper.provision import render_dashboard
        dashboard = render_dashboard(json.loads(Path('grafana/provisioning/dashboards/photovoltaik.json').read_text()),
                                     load_installation('installation.example.json'))
        panel = next(p for p in dashboard['panels'] if p['id'] == 22)
        self.assertEqual([self.query(t['expr'], start) for t in panel['targets']], [[-300]])
        self.assertEqual([self.query(t['expr'], start + 60) for t in panel['targets']], [[400]])
        heating = render_dashboard(json.loads(Path('grafana/provisioning/dashboards/heatpump-diagnostics.json').read_text()),
                                   load_installation('installation.example.json'))
        panel = next(p for p in heating['panels'] if p['id'] == 5)
        lifetime = next(t['expr'] for t in panel['targets'] if 'gesamt' in t['expr'])
        self.assertEqual(self.query(lifetime, start + 120), [3])
        # Do not reinterpret a gauge reset as negative consumption or billing energy.
        self.assertEqual(self.query('idelta(fronius_meter_energy_real_abs_plus_Wh[2m])', start + 120), [-1280])

        raw = 'fronius_powerflow_p_pv_watts'
        name = 'fronius.powerflow.p_pv'
        guarded = (raw + ' and on() (smarthome_measurement_present{metric="' + name + '"} == 1)'
                   + ' and on() (time() - smarthome_measurement_last_success_seconds{metric="' + name + '"} < 180)')
        self.assertEqual(self.query(guarded, start + 120), [0])  # Zero is observed, not missing.
        self.stack.solar({'powerflow': {'Site': {'P_Grid': 0}}}, start + 180)
        self.push(start + 180)
        self.assertEqual(self.query(guarded, start + 180), [])  # Missing sibling despite healthy endpoint.
        # Prometheus lookback alone would retain the old sample; presence gate prevents that.
        self.assertEqual(self.query(raw, start + 180), [0])
        self.stack.solar({'powerflow': {'Site': {'P_PV': 50}}}, start + 240)
        self.push(start + 240)
        self.assertEqual(self.query(guarded, start + 240), [50])
        self.push(start + 480)  # Cached OTLP push is NOT a new source read.
        self.assertEqual(self.query(raw, start + 480), [50])
        self.assertEqual(self.query(guarded, start + 480), [])
        self.assertEqual(self.query('timestamp(' + raw + ')', start + 480), [start + 480])
        self.assertEqual(self.query('smarthome_measurement_last_success_seconds{metric="' + name + '"}', start + 480), [start + 240])


if __name__ == '__main__':
    unittest.main()
