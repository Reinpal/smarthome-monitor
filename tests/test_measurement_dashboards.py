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
        # AC lifetime/day counters moved out of the homeowner overview into the
        # independently owned diagnostics. Production uses the validated DC key.
        solar_panels = {p['id']: p for p in solar['panels']}
        self.assertEqual(solar_panels[100]['targets'][0]['periodMetric'], 'pv')
        self.assertIn('NOT hybrid inverter AC', solar_panels[100]['description'])
        self.assertEqual(solar_panels[109]['targets'][0]['periodMetric'], 'solar_self_consumption')
        self.assertIn('Unavailable', solar_panels[109]['description'])
        heating_panels = {p['id']: p for p in heating['panels']}
        self.assertIn('keine JAZ', heating_panels[24]['title'])
        self.assertEqual(len(heating_panels[24]['fieldConfig']['defaults']['thresholds']['steps']), 1)
        self.assertIn('NHZ', heating_panels[5]['description'])
        self.assertIn('Gesamtsystem', heating_panels[27]['description'])


if __name__ == '__main__':
    unittest.main()
