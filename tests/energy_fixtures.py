"""Standalone fictional energy fixture; disposable loopback services only."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

import requests
from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

from scraper.installation import parse_installation
from scraper.period_promql import PREFIX
from test_period_calculations import configuration
from test_measurements import SourceFixture

HEATING_RATES = {'heating_electricity': .1 / 60, 'heating_heat': .3 / 60,
                 'water_electricity': .05 / 60, 'water_heat': .15 / 60,
                 'heating_aux_heat': 0, 'water_aux_heat': 0}
SITE_RATES = {'household': 1000, 'grid_import': .4 / 3600, 'grid_export': .2 / 3600}


class EnergyFixture:
    def __enter__(self):
        self._cleanup = ExitStack()
        try:
            self._source = self._cleanup.enter_context(SourceFixture())
            root = Path(self._cleanup.enter_context(tempfile.TemporaryDirectory(prefix='fictional-energy-')))
            (root / 'prometheus.yml').write_text('scrape_configs: []\n')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            self.base = f'http://127.0.0.1:{port}'
            process = subprocess.Popen([
                os.environ['PROMETHEUS_TEST_BINARY'], '--config.file=' + str(root / 'prometheus.yml'),
                '--storage.tsdb.path=' + str(root / 'data'), '--web.listen-address=127.0.0.1:' + str(port),
                '--web.enable-otlp-receiver', '--storage.tsdb.retention.time=100y',
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.process = process
            def stop():
                process.terminate()
                process.wait(timeout=10)
            self._cleanup.callback(stop)
            for _ in range(100):
                try:
                    if requests.get(self.base + '/-/ready', timeout=.2).ok:
                        break
                except requests.RequestException:
                    pass
                time.sleep(.05)
            else:
                raise AssertionError('Isolated Prometheus did not become ready')
            self.raw = configuration()
            self.installation = parse_installation(self.raw)
            self.start = datetime(2025, 6, 30, 21, 59, tzinfo=timezone.utc)
            return self
        except BaseException:
            self._cleanup.close()
            raise

    def __exit__(self, *exc):
        return self._cleanup.__exit__(*exc)

    def solar(self, data, at):
        self._source.solar(data, at)

    def heatpump(self, data, at):
        self._source.heatpump(data, at)

    def _ingest(self, request):
        response = requests.post(self.base + '/api/v1/otlp/v1/metrics', data=request.SerializeToString(),
                                 headers={'Content-Type': 'application/x-protobuf'}, timeout=20)
        if not response.ok:
            raise AssertionError('Fictional interval/source ingestion failed')

    def push(self, timestamp):
        data = self._source.reader.get_metrics_data()
        data = replace(data, resource_metrics=[replace(r, scope_metrics=[replace(s, metrics=[
            replace(m, data=replace(m.data, data_points=[
                replace(p, time_unix_nano=int(timestamp * 1e9)) for p in m.data.data_points
            ])) for m in s.metrics]) for s in r.scope_metrics]) for r in data.resource_metrics])
        self._ingest(encode_metrics(data))

    def query(self, expression, at):
        response = requests.post(self.base + '/api/v1/query', data={'query': expression, 'time': at}, timeout=60)
        body = response.json()
        if body['status'] != 'success':
            raise AssertionError('Fictional native query failed')
        return body['data']['result']

    def source(self, index, *, reset=False, missing=False, stamp=None):
        at = (self.start + timedelta(minutes=index)).timestamp() if stamp is None else stamp
        site = {'P_Load': -1000, 'P_PV': 1500, 'P_Akku': -300 if index == 0 else 300}
        if missing:
            site.pop('P_Load')
        self.solar({'powerflow': {'Site': site, 'Inverters': {'1': {'SOC': 60 - index}}},
                    'meter': {'fictional-grid': {'Meter_Location_Current': 0, 'Visible': 1, 'Enable': 1,
                                                'EnergyReal_WAC_Plus_Absolute': 10 if reset else 1000 + index * 10,
                                                'EnergyReal_WAC_Minus_Absolute': 500 + index * 5}}}, at)
        self.heatpump({'WÄRMEMENGE': {'VD HEIZEN SUMME': f'{100 + index * .003}MWh',
                                    'VD WARMWASSER SUMME': f'{20 + index * .001}MWh',
                                    'NHZ HEIZEN SUMME': '0MWh', 'NHZ WARMWASSER SUMME': '0MWh'},
                       'LEISTUNGSAUFNAHME': {'VD HEIZEN SUMME': f'{20000 + index}kWh',
                                            'VD WARMWASSER SUMME': f'{1000 + index * .5}kWh'}}, at)
        self.push(at)

    def push_intervals(self, start, minutes, *, rates=None, multiplier=1, missing=None, receipt_delay=0):
        """Ingest accepted 60s facts, in bounded batches. Rates are kWh/s or W.

        The source -> rule rejection path is covered independently by promtool.
        A missing (key, minute) models a rejected interval, never zero energy.
        """
        rates = SITE_RATES if rates is None else rates
        for first in range(1, minutes + 1, 1440):
            request = ExportMetricsServiceRequest()
            scope = request.resource_metrics.add().scope_metrics.add()
            for key, rate in rates.items():
                power = key in ('household', 'pv', 'battery_charge', 'battery_discharge')
                for field in ('start_seconds', 'end_seconds', 'left', 'right', 'rate', 'zero_seconds', 'shape', 'stale_after_seconds', 'valid_until_seconds'):
                    metric = scope.metrics.add(name=PREFIX + field)
                    for i in range(first, min(minutes + 1, first + 1440)):
                        if missing == (key, i):
                            continue
                        at = start + i * 60
                        value = {'start_seconds': at - 60, 'end_seconds': at,
                                 'rate': 0 if power else rate * multiplier,
                                 'zero_seconds': start, 'shape': 1, 'stale_after_seconds': 180,
                                 'valid_until_seconds': at + 180,
                                 'left': rate * multiplier if power else 100 + (i - 1) * 60 * rate * multiplier,
                                 'right': rate * multiplier if power else 100 + i * 60 * rate * multiplier}[field]
                        point = metric.gauge.data_points.add(time_unix_nano=int((at + receipt_delay) * 1e9), as_double=value)
                        point.attributes.add(key='metric').value.string_value = key
            self._ingest(request)
