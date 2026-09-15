"""Optional isolated Prometheus: fictional source -> SDK -> OTLP -> real PromQL.

Set PROMETHEUS_TEST_BINARY to an unpacked official Prometheus binary. This starts
only a loopback subprocess with a disposable TSDB, never Docker or a live service.
"""

from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest

from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
import requests

import test_measurements as fixtures


@unittest.skipUnless(os.environ.get('PROMETHEUS_TEST_BINARY'), 'set PROMETHEUS_TEST_BINARY for real PromQL tests')
class MeasurementQueryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MeasurementPathTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.directory = tempfile.TemporaryDirectory(prefix='fictional-measurements-')
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / 'prometheus.yml').write_text('scrape_configs: []\n')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.base = f'http://127.0.0.1:{port}'
        self.process = subprocess.Popen([
            os.environ['PROMETHEUS_TEST_BINARY'], '--config.file=' + str(root / 'prometheus.yml'),
            '--storage.tsdb.path=' + str(root / 'data'), '--web.listen-address=127.0.0.1:' + str(port),
            '--web.enable-otlp-receiver',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop)
        for _ in range(100):
            try:
                if requests.get(self.base + '/-/ready', timeout=0.2).ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.05)
        else:
            self.fail('Isolated Prometheus did not become ready')

    def stop(self):
        self.process.terminate()
        self.process.wait(timeout=10)

    def push(self, timestamp):
        data = self.fixture.reader.get_metrics_data()
        # Only observation time changes; values/units/attributes come from the real SDK.
        data = replace(data, resource_metrics=[replace(r, scope_metrics=[replace(s, metrics=[
            replace(m, data=replace(m.data, data_points=[
                replace(p, time_unix_nano=int(timestamp * 1e9)) for p in m.data.data_points
            ])) for m in s.metrics]) for s in r.scope_metrics]) for r in data.resource_metrics])
        r = requests.post(self.base + '/api/v1/otlp/v1/metrics',
                          data=encode_metrics(data).SerializeToString(),
                          headers={'Content-Type': 'application/x-protobuf'}, timeout=5)
        self.assertTrue(r.ok, r.text)

    def query(self, expression, timestamp):
        r = requests.get(self.base + '/api/v1/query', params={'query': expression, 'time': timestamp}, timeout=5)
        data = r.json()
        self.assertEqual(data['status'], 'success', data)
        return [float(p['value'][1]) for p in data['data']['result']]

    def test_source_export_names_panels_resets_missing_and_cached_queries(self):
        start = int(time.time()) - 900
        for i, (battery, grid, total) in enumerate([(-300, -200, 1000), (400, 200, 1300), (0, 0, 20)]):
            now = start + i * 60
            self.fixture.solar({'powerflow': {'Site': {'P_PV': 1000 if i == 0 else 0, 'P_Akku': battery,
                                                       'P_Grid': grid, 'P_Load': -500}},
                                'meter': {'fictional-grid': {'Meter_Location_Current': 0,
                                                            'Visible': 1, 'Enable': 1,
                                                            'EnergyReal_WAC_Plus_Absolute': total}}}, now)
            self.fixture.heatpump({'WÄRMEMENGE': {'VD HEIZEN SUMME': '1,2MWh'},
                                   'LEISTUNGSAUFNAHME': {'VD HEIZEN SUMME': '400kWh'}}, now)
            self.push(now)
            self.assertEqual(self.query('fronius_meter_energy_real_abs_plus_Wh', now), [total])
            self.assertEqual(self.query('heatpump_waermepumpe_leistungsaufnahme_vd_heizen_summe_MWh', now), [0.4])
            self.assertEqual(self.query('smarthome_measurement_contract_version', now), [2])

        # Existing Grafana targets genuinely query the translated OTLP series.
        dashboard = json.loads(Path('grafana/provisioning/dashboards/photovoltaik.json').read_text())
        panel = next(p for p in dashboard['panels'] if p['id'] == 23)
        self.assertEqual([self.query(t['expr'], start) for t in panel['targets']], [[300], [0]])
        self.assertEqual([self.query(t['expr'], start + 60) for t in panel['targets']], [[0], [400]])
        heating = json.loads(Path('grafana/provisioning/dashboards/heatpump.json').read_text())
        panel = next(p for p in heating['panels'] if p['id'] == 24)
        lifetime = next(t['expr'] for t in panel['targets'] if 'gesamt' in t['expr'])
        self.assertEqual(self.query(lifetime, start + 120), [3])
        # Do not reinterpret a gauge reset as negative consumption or billing energy.
        self.assertEqual(self.query('idelta(fronius_meter_energy_real_abs_plus_Wh[2m])', start + 120), [-1280])

        raw = 'fronius_powerflow_p_pv_watts'
        name = 'fronius.powerflow.p_pv'
        guarded = (raw + ' and on() (smarthome_measurement_present{metric="' + name + '"} == 1)'
                   + ' and on() (time() - smarthome_measurement_last_success_seconds{metric="' + name + '"} < 180)')
        self.assertEqual(self.query(guarded, start + 120), [0])  # Zero is observed, not missing.
        self.fixture.solar({'powerflow': {'Site': {'P_Grid': 0}}}, start + 180)
        self.push(start + 180)
        self.assertEqual(self.query(guarded, start + 180), [])  # Missing sibling despite healthy endpoint.
        # Prometheus lookback alone would retain the old sample; presence gate prevents that.
        self.assertEqual(self.query(raw, start + 180), [0])
        self.fixture.solar({'powerflow': {'Site': {'P_PV': 50}}}, start + 240)
        self.push(start + 240)
        self.assertEqual(self.query(guarded, start + 240), [50])
        self.push(start + 480)  # Cached OTLP push is NOT a new source read.
        self.assertEqual(self.query(raw, start + 480), [50])
        self.assertEqual(self.query(guarded, start + 480), [])
        self.assertEqual(self.query('timestamp(' + raw + ')', start + 480), [start + 480])
        self.assertEqual(self.query('smarthome_measurement_last_success_seconds{metric="' + name + '"}', start + 480), [start + 240])


if __name__ == '__main__':
    unittest.main()
