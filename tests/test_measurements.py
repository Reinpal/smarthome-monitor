"""Fictional source -> existing cycles -> real OTel SDK acceptance fixtures."""

import unittest
from unittest.mock import Mock, patch

from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from scraper.collectors.fronius_collector import FroniusCollector
from scraper.config import Config
from scraper.exporters.otlp_exporter import OTLPExporter
from scraper.main import fronius_collect_cycle, isg_scrape_cycle
from scraper.scrapers.isg_scraper import ISGScraper


def response(data):
    result = Mock()
    result.json.return_value = {"Head": {"Status": {"Code": 0}}, "Body": {"Data": data}}
    return result


def html_page(sections):
    # All values are invented; never use device snapshots here.
    return "".join(
        '<table class="info"><tr><th>' + section + '</th></tr>'
        + "".join('<tr><td class="key">' + key + '</td><td class="value">'
                  + value + '</td></tr>' for key, value in rows.items())
        + '</table>' for section, rows in sections.items()
    )


def points(data):
    return {m.name: m for r in data.resource_metrics for s in r.scope_metrics for m in s.metrics}


class MeasurementPathTests(unittest.TestCase):
    def setUp(self):
        self.reader = InMemoryMetricReader()
        with patch('scraper.exporters.otlp_exporter.OTLPMetricExporter'), patch(
            'scraper.exporters.otlp_exporter.PeriodicExportingMetricReader', return_value=self.reader
        ):
            self.exporter = OTLPExporter('http://unused.invalid')
        self.addCleanup(self.exporter.shutdown)
        self.collector = FroniusCollector('http://solar.invalid')
        self.isg = ISGScraper('http://heatpump.invalid')
        self.endpoints = Config().fronius_endpoints
        self.exporter.health.register('fronius', self.endpoints, 30)
        self.exporter.health.register('isg', ['waermepumpe'], 300)

    def solar(self, data, now=1000):
        replies = [response(data.get(name, {})) for name in self.endpoints]
        with patch.object(self.collector.session, 'get', side_effect=replies), patch(
            'scraper.exporters.otlp_exporter.time.time', return_value=now
        ):
            fronius_collect_cycle(self.collector, self.exporter, self.endpoints)

    def heatpump(self, sections, now=1000):
        reply = Mock(text=html_page(sections))
        with patch.object(self.isg.session, 'get', return_value=reply), patch(
            'scraper.exporters.otlp_exporter.time.time', return_value=now
        ):
            isg_scrape_cycle(self.isg, self.exporter, {'?fictional': 'waermepumpe'})

    def snapshot(self):
        return points(self.reader.get_metrics_data())

    def value(self, name, snapshot=None):
        snapshot = self.snapshot() if snapshot is None else snapshot
        return snapshot[name].data.data_points[0].value

    def metadata(self, name, snapshot=None):
        snapshot = self.snapshot() if snapshot is None else snapshot
        return {p.attributes['metric']: p.value for p in snapshot[name].data.data_points}

    def test_battery_grid_and_load_signs_including_zero(self):
        for battery, grid, load, charge, discharge, imported, exported, demand in [
            (-400, -200, -600, 400, 0, 0, 200, 600),
            (500, 300, -800, 0, 500, 300, 0, 800),
            (0, 0, 0, 0, 0, 0, 0, 0),
            (0, 0, 25, 0, 0, 0, 0, 0),
        ]:
            with self.subTest(battery=battery, grid=grid, load=load):
                self.solar({'powerflow': {'Site': {'P_Akku': battery, 'P_Grid': grid, 'P_Load': load, 'P_PV': 0}}})
                actual = self.snapshot()
                for suffix, expected in [('battery_charge', charge), ('battery_discharge', discharge),
                                         ('grid_import', imported), ('grid_export', exported), ('load_absolute', demand)]:
                    self.assertEqual(self.value('fronius.calculated.' + suffix, actual), expected)
                self.assertNotIn('fronius.calculated.self_consumption_power', actual)

    def test_partial_invalid_fields_do_not_hide_valid_zero_or_refresh_missing(self):
        self.solar({'powerflow': {'Site': {'P_PV': 800, 'P_Akku': -300}}}, 1000)
        self.solar({'powerflow': {'Site': {'P_PV': 0, 'P_Akku': None, 'P_Grid': 'bad', 'P_Load': float('nan')},
                                  'Inverters': {'1': {'SOC': 0, 'Battery_Mode': None}}}}, 1060)
        actual = self.snapshot()
        self.assertEqual(self.value('fronius.powerflow.p_pv', actual), 0)
        self.assertEqual(self.value('fronius.powerflow.soc', actual), 0)
        self.assertNotIn('fronius.powerflow.p_akku', actual)
        self.assertNotIn('fronius.powerflow.battery_mode', actual)
        self.assertNotIn('fronius.calculated.battery_charge', actual)
        observed = self.metadata('smarthome_measurement_last_success_seconds', actual)
        self.assertEqual(observed['fronius.powerflow.p_pv'], 1060)
        self.assertEqual(observed['fronius.powerflow.p_akku'], 1000)
        self.assertEqual(self.metadata('smarthome_measurement_present', actual)['fronius.powerflow.p_akku'], 0)
        # More periodic exports do not turn a cached measurement into a new read.
        self.assertEqual(observed, self.metadata('smarthome_measurement_last_success_seconds'))
        source = actual['smarthome_collection_last_success_seconds'].data.data_points
        self.assertEqual(next(p.value for p in source if p.attributes['source'] == 'powerflow'), 1060)

    def test_meter_selection_directions_duplicates_units_and_resets(self):
        for total in [1000, 1200, 20]:  # Reset is preserved, not clamped or made monotonic.
            self.solar({'meter': {
                'fictional-subload': {'Meter_Location_Current': 256, 'EnergyReal_WAC_Plus_Absolute': 9999},
                'fictional-grid': {'Meter_Location_Current': 0, 'Visible': 1, 'Enable': 1,
                                   'PowerReal_P_Sum': -70, 'EnergyReal_WAC_Plus_Absolute': total,
                                   'EnergyReal_WAC_Sum_Consumed': total,
                                   'EnergyReal_WAC_Minus_Absolute': 500, 'EnergyReal_WAC_Sum_Produced': 500,
                                   'PowerApparent_S_Sum': 80, 'PowerReactive_Q_Sum': 10}}})
            actual = self.snapshot()
            self.assertEqual(self.value('fronius.meter.energy_real_abs_plus', actual), total)
            self.assertEqual(self.value('fronius.meter.energy_real_consumed', actual), total)
            self.assertEqual(self.value('fronius.meter.energy_real_abs_minus', actual), 500)
            self.assertEqual(self.value('fronius.meter.power_real_p_sum', actual), -70)
            self.assertEqual(actual['fronius.meter.power_apparent_s_sum'].unit, 'VA')
            self.assertEqual(actual['fronius.meter.power_reactive_q_sum'].unit, 'var')
        for meter in [
            {'x': {'Meter_Location_Current': 1, 'EnergyReal_WAC_Plus_Absolute': 10}},
            {'x': {'EnergyReal_WAC_Plus_Absolute': 10}},
            {'x': {'Meter_Location_Current': 0, 'Visible': 0, 'EnergyReal_WAC_Plus_Absolute': 10}},
            {'x': {'Meter_Location_Current': 0}, 'y': {'Meter_Location_Current': 0}},
        ]:
            self.solar({'meter': meter})
            self.assertNotIn('fronius.meter.energy_real_abs_plus', self.snapshot())

    def test_storage_capacity_is_not_falsely_exported_as_ah(self):
        self.solar({'storage': {'fictional-battery': {'Controller': {
            'Enable': 1, 'StateOfCharge_Relative': 0, 'Current_DC': 2,
            'Capacity_Maximum': 6000, 'DesignedCapacity': 7000}}}})
        actual = self.snapshot()
        self.assertEqual(self.value('fronius.storage.current_dc', actual), 2)
        self.assertEqual(actual['fronius.storage.capacity_maximum_raw'].unit, '')
        self.assertNotIn('fronius.storage.capacity_maximum', actual)
        self.solar({'storage': {'a': {'Controller': {'StateOfCharge_Relative': 10}},
                                'b': {'Controller': {'StateOfCharge_Relative': 90}}}})
        self.assertNotIn('fronius.storage.soc', self.snapshot())

    def test_ac_energy_is_not_pv_and_optional_daily_yearly_are_absent(self):
        self.solar({'powerflow': {'Site': {'P_PV': 0, 'P_Akku': 500, 'E_Day': None, 'E_Year': None, 'E_Total': 9000}},
                    'inverter': {'PAC': {'Value': 470, 'Unit': 'W'},
                                 'TOTAL_ENERGY': {'Value': 9000, 'Unit': 'Wh'},
                                 'DAY_ENERGY': {'Value': None, 'Unit': 'Wh'}}})
        actual = self.snapshot()
        self.assertEqual(self.value('fronius.powerflow.p_pv', actual), 0)
        self.assertEqual(self.value('fronius.inverter.pac', actual), 470)
        self.assertIn('battery', actual['fronius.inverter.total_energy'].description)
        self.assertNotIn('fronius.powerflow.e_day', actual)
        self.assertNotIn('fronius.powerflow.e_year', actual)
        self.solar({'inverter': {'PAC': {'Value': 1, 'Unit': 'kW'},
                                 'TOTAL_ENERGY': {'Value': 20, 'Unit': 'Wh'}}})
        self.assertNotIn('fronius.inverter.pac', self.snapshot())
        self.assertEqual(self.value('fronius.inverter.total_energy'), 20)

    def test_heat_energy_units_ratio_reset_and_auxiliary_boundary(self):
        self.heatpump({'WÄRMEMENGE': {'VD HEIZEN SUMME': '1,2MWh', 'VD HEIZEN TAG': '12kWh',
                                      'NHZ HEIZEN SUMME': '0MWh'},
                       'LEISTUNGSAUFNAHME': {'VD HEIZEN SUMME': '400kWh', 'VD HEIZEN TAG': '4KWh'},
                       'PROZESSDATEN': {'STROM INVERTER': '4A', 'SPANNUNG INVERTER': '230V',
                                       'VORLAUFTEMPERATUR': '30,5°C', 'RÜCKLAUFTEMPERATUR': '26°C'}})
        actual = self.snapshot()
        self.assertEqual(self.value('heatpump.calculated.cop_heizen_gesamt', actual), 3)
        self.assertEqual(self.value('heatpump.calculated.cop_heizen_tag', actual), 3)
        name = 'heatpump.waermepumpe.leistungsaufnahme.vd_heizen_summe'
        self.assertEqual(actual[name].unit, 'MWh')
        self.assertEqual(self.value(name, actual), 0.4)
        self.assertEqual(self.value('heatpump.waermepumpe.waermemenge.nhz_heizen_summe', actual), 0)
        self.assertEqual(self.value('heatpump.calculated.vorlauf_ruecklauf_spread', actual), 4.5)
        self.assertNotIn('heatpump.calculated.inverter_leistung_berechnet', actual)
        # A unit change stays on the same established MWh series. Reset remains visible.
        self.heatpump({'LEISTUNGSAUFNAHME': {'VD HEIZEN SUMME': '0,01MWh'}})
        self.assertEqual(self.value(name), 0.01)
        self.assertNotIn('heatpump.calculated.cop_heizen_gesamt', self.snapshot())

    def test_zero_unknown_missing_energy_withholds_ratio_and_cached_value(self):
        for invalid in ['0kWh', '4kW', 'Aus', '---', '-2kWh']:
            with self.subTest(invalid=invalid):
                self.heatpump({'WÄRMEMENGE': {'VD HEIZEN TAG': '12kWh'},
                               'LEISTUNGSAUFNAHME': {'VD HEIZEN TAG': '4kWh'}})
                self.assertEqual(self.value('heatpump.calculated.cop_heizen_tag'), 3)
                self.heatpump({'WÄRMEMENGE': {'VD HEIZEN TAG': '12kWh'},
                               'LEISTUNGSAUFNAHME': {'VD HEIZEN TAG': invalid}}, 1060)
                self.assertNotIn('heatpump.calculated.cop_heizen_tag', self.snapshot())
        self.heatpump({'WÄRMEMENGE': {'VD HEIZEN TAG': '0kWh'},
                       'LEISTUNGSAUFNAHME': {'VD HEIZEN TAG': '2kWh'}})
        self.assertEqual(self.value('heatpump.calculated.cop_heizen_tag'), 0)
        self.heatpump({})
        self.assertNotIn('heatpump.calculated.cop_heizen_tag', self.snapshot())

    def test_metadata_only_response_does_not_invent_battery_mode_or_success(self):
        self.solar({'powerflow': {'Inverters': {'1': {'DT': 1, 'Battery_Mode': None}}}})
        self.assertNotIn('fronius.powerflow.battery_mode', self.snapshot())
        self.assertEqual(self.exporter.health.snapshot()[('fronius', 'powerflow')][0], 0)

    def test_failed_endpoint_replaces_values_without_losing_other_endpoints(self):
        self.solar({'powerflow': {'Site': {'P_PV': 600}},
                    'inverter': {'PAC': {'Value': 550, 'Unit': 'W'}}}, 1000)
        replies = [response({}), response({}), response({}), response({'PAC': {'Value': 0, 'Unit': 'W'}})]
        replies[0].json.return_value = {'Head': {'Status': {'Code': 1}}, 'Body': {'Data': {'Site': {'P_PV': 999}}}}
        with patch.object(self.collector.session, 'get', side_effect=replies), self.assertLogs(level='WARNING'):
            fronius_collect_cycle(self.collector, self.exporter, self.endpoints)
        self.assertNotIn('fronius.powerflow.p_pv', self.snapshot())
        self.assertEqual(self.value('fronius.inverter.pac'), 0)
        self.assertEqual(self.exporter.health.snapshot()[('fronius', 'powerflow')][0], 1000)

    def test_german_grouped_energy_is_converted_without_losing_precision(self):
        self.heatpump({'WÄRMEMENGE': {'VD HEIZEN SUMME': '1.234,5kWh'}})
        self.assertAlmostEqual(self.value('heatpump.waermepumpe.waermemenge.vd_heizen_summe'), 1.2345)


if __name__ == '__main__':
    unittest.main()
