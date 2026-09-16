"""Snapshot cards must never display PromQL as their field label."""
import json
from pathlib import Path
import unittest

from scraper.home_queries import live_target
from scraper.installation import load_installation
from scraper.provision import render_dashboard

ROOT = Path(__file__).resolve().parents[1]


class SnapshotLabelTests(unittest.TestCase):
    def test_rendered_home_snapshot_labels_are_readable(self):
        installation = load_installation(ROOT / 'installation.example.json')
        template = json.loads((ROOT / 'grafana/provisioning/dashboards/home.json').read_text())
        rendered = render_dashboard(template, installation)
        expected = {20: 'PV', 21: 'Household', 22: 'Grid', 23: 'Battery power',
                    24: 'Battery charge', 25: 'Outdoor temperature'}
        panels = {panel['id']: panel for panel in rendered['panels']}
        for panel_id, label in expected.items():
            with self.subTest(panel=panel_id):
                target = panels[panel_id]['targets'][0]
                self.assertEqual(target['legendFormat'], label)
                self.assertTrue(target['instant'])
                self.assertIn('smarthome_measurement_present', target['expr'])

    def test_empty_live_legend_uses_metric_name_instead_of_query(self):
        self.assertEqual(live_target({'liveMetric': 'pv', 'legendFormat': ''})['legendFormat'], 'pv')


if __name__ == '__main__':
    unittest.main()
