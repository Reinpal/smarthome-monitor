"""Source-semantics guardrails, not full dashboard acceptance (#5/#6/#7)."""

import json
from pathlib import Path
import unittest


class MeasurementDashboardTests(unittest.TestCase):
    def test_retired_claims_are_not_queried_and_boundaries_are_visible(self):
        root = Path('grafana/provisioning/dashboards')
        solar = json.loads((root / 'photovoltaik.json').read_text())
        heating = json.loads((root / 'heatpump.json').read_text())
        for dashboard in (solar, heating):
            expressions = [t.get('expr', '') for p in dashboard['panels'] for t in p.get('targets', [])]
            self.assertFalse(any('self_consumption_power' in e or 'inverter_leistung_berechnet' in e for e in expressions))
        solar_panels = {p['id']: p for p in solar['panels']}
        for panel in (31, 32, 33):
            self.assertIn('AC', solar_panels[panel]['title'])
            self.assertIn('Hybrid', solar_panels[panel]['description'])
        self.assertIn('nicht verfügbar', solar_panels[31]['description'])
        diagnostics = json.loads((root / 'heatpump-diagnostics.json').read_text())
        heating_panels = {p['id']: p for p in diagnostics['panels']}
        self.assertIn('not annual', heating_panels[5]['title'])
        self.assertEqual(len(heating_panels[5]['fieldConfig']['defaults']['thresholds']['steps']), 1)
        self.assertIn('NHZ', heating_panels[5]['description'])
        self.assertIn('gesamte', heating_panels[6]['description'])


if __name__ == '__main__':
    unittest.main()
