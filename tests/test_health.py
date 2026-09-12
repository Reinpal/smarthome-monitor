import unittest
from unittest.mock import Mock, patch

from scraper.health import CollectionHealth
from scraper.main import isg_scrape_cycle
from scraper.collectors.fronius_collector import FroniusCollector, FroniusMetric


class HealthTests(unittest.TestCase):
    def test_never_successful_and_thresholds(self):
        health = CollectionHealth()
        health.register("isg", ["page"], 300)
        health.register("fronius", ["powerflow"], 30)
        self.assertEqual(health.snapshot(), {
            ("isg", "page"): (0.0, 900),
            ("fronius", "powerflow"): (0.0, 180),
        })
        with patch("scraper.health.time.time", return_value=1234):
            health.success("isg", "page")
        self.assertEqual(health.snapshot()[("isg", "page")], (1234, 900))
        # Exporting snapshots does not refresh timestamps.
        self.assertEqual(health.snapshot()[("fronius", "powerflow")][0], 0)

    def test_isg_partial_failure_and_empty_parse(self):
        scraper = Mock()
        scraper.fetch_all_pages.return_value = {"good": "html", "empty": "html"}
        exporter = Mock()
        exporter.export_values.side_effect = [3, 0]
        with patch("scraper.main.parse_isg_page", return_value=[]):
            isg_scrape_cycle(scraper, exporter, {"a": "good", "b": "empty", "c": "missing"})
        exporter.health.success.assert_called_once_with("isg", "good")

    def test_fronius_only_nonempty_success_refreshes(self):
        collector = FroniusCollector("http://unused")
        collector._fetch_json = Mock(side_effect=[{}, None, {}, RuntimeError("failed")])
        collector._parse_good = Mock(return_value=[FroniusMetric("test", 0, "", "")])
        collector._parse_empty = Mock(return_value=[])
        callback = Mock()
        collector.collect_all({"good": "/1", "missing": "/2", "empty": "/3", "error": "/4"}, callback)
        callback.assert_called_once_with("good")


if __name__ == "__main__":
    unittest.main()
