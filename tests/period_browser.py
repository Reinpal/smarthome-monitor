"""Disposable loopback Grafana for fictional period acceptance only."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

import requests


@contextmanager
def grafana(dashboards, prometheus=None):
    with tempfile.TemporaryDirectory(prefix='fictional-period-grafana-') as directory:
        root = Path(directory)
        provisioning = root / 'provisioning'
        (provisioning / 'dashboards').mkdir(parents=True)
        output = root / 'dashboards'
        output.mkdir()
        for dashboard in dashboards:
            (output / (dashboard['uid'] + '.json')).write_text(json.dumps(dashboard))
        (provisioning / 'dashboards/provider.yml').write_text(
            'apiVersion: 1\nproviders:\n  - name: fictional-period\n    type: file\n    updateIntervalSeconds: 1\n    options:\n      path: ' + str(output) + '\n')
        if prometheus:
            (provisioning / 'datasources').mkdir()
            (provisioning / 'datasources/prometheus.yaml').write_text(json.dumps({
                'apiVersion': 1, 'datasources': [{'name': 'Prometheus', 'type': 'prometheus', 'uid': 'prometheus',
                                                'access': 'proxy', 'url': prometheus, 'isDefault': True,
                                                'jsonData': {'httpMethod': 'POST'}}]}))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        config = root / 'grafana.ini'
        config.write_text(f'''[server]
http_addr = 127.0.0.1
http_port = {port}
[paths]
data = {root / 'data'}
logs = {root / 'logs'}
plugins = {root / 'plugins'}
provisioning = {provisioning}
[auth.anonymous]
enabled = true
org_role = Viewer
[analytics]
reporting_enabled = false
check_for_updates = false
check_for_plugin_updates = false
[plugins]
preinstall_disabled = true
[security]
admin_password = fictional-test-only
''')
        home = Path(os.environ['GRAFANA_TEST_HOME'])
        env = {k: v for k, v in os.environ.items() if not k.startswith('GF_')}
        process = subprocess.Popen([str(home / 'bin/grafana'), 'server', '--homepath', str(home), '--config', str(config)],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = f'http://127.0.0.1:{port}'
            for _ in range(200):
                try:
                    if requests.get(base + '/api/health', timeout=.2).ok:
                        break
                except requests.RequestException:
                    pass
                time.sleep(.1)
            else:
                raise AssertionError('Isolated Grafana failed to become ready')
            yield base, output
        finally:
            process.terminate()
            process.wait(timeout=15)
