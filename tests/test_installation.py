"""Only fictional inputs; these tests never contact devices, Docker or Grafana."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scraper.installation import ConfigurationError, PriceUnavailable, load_installation, parse_installation
from scraper.provision import render, render_dashboard

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "installation.example.json"


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class TariffTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(EXAMPLE.read_text())
        self.config = load_installation(EXAMPLE)

    def test_vat_discount_fixed_and_restricted_credit_are_separate(self):
        price = self.config.import_price(date(2025, 3, 1))
        self.assertEqual(price.gross_per_kwh, Decimal("0.228"))
        self.assertEqual(price.tariff.components_gross_per_kwh,
                         (("energy", Decimal("0.144")), ("network", Decimal("0.072")), ("levies", Decimal("0.024"))))
        self.assertEqual(price.tariff.universal_discount_gross_per_kwh, Decimal("0.012"))
        self.assertEqual(price.tariff.restricted_credits_gross, (Decimal("17"),))
        self.assertEqual([(x.period, x.gross_amount) for x in price.tariff.fixed_charges],
                         [("month", Decimal("6")), ("year", Decimal("12"))])
        self.assertEqual(price.currency, "EUR")
        self.assertEqual(price.status, "confirmed")
        self.assertFalse(price.carried_forward)

    def test_mixed_vat_basis_is_not_taxed_twice(self):
        self.raw["import_tariffs"][0]["components"]["network"] = {"amount": 0.072, "vat_basis": "gross"}
        self.assertEqual(parse_installation(self.raw).import_price(date(2025, 1, 1)).gross_per_kwh, Decimal("0.228"))

    def test_boundaries_and_visible_expiry(self):
        self.assertEqual(self.config.import_price(date(2025, 6, 30)).gross_per_kwh, Decimal("0.228"))
        change = self.config.import_price(date(2025, 7, 1))
        self.assertEqual(change.gross_per_kwh, Decimal("0.24"))
        self.assertEqual(change.status, "provisional")
        self.assertFalse(change.carried_forward)
        expired = self.config.import_price(date(2026, 1, 1))
        self.assertTrue(expired.carried_forward)
        self.assertEqual(expired.status, "provisional")
        self.assertEqual(expired.effective_from, date(2025, 7, 1))
        self.assertEqual(expired.effective_until, date(2026, 1, 1))
        with self.assertRaises(PriceUnavailable):
            self.config.import_price(date(2026, 1, 1), carry_forward=False)

    def test_gap_carries_only_previous_known_rate_as_provisional(self):
        self.raw["import_tariffs"][1]["from"] = "2025-08-01"
        gap = parse_installation(self.raw).import_price(date(2025, 7, 20))
        self.assertEqual(gap.gross_per_kwh, Decimal("0.228"))
        self.assertEqual(gap.source_status, "confirmed")
        self.assertEqual(gap.status, "provisional")
        self.assertTrue(gap.carried_forward)
        self.assertEqual(gap.effective_until, date(2025, 7, 1))

    def test_no_price_before_history_never_uses_future_rate_or_zero(self):
        for lookup in (self.config.import_price, self.config.export_price):
            with self.assertRaises(PriceUnavailable):
                lookup(date(2024, 12, 31))

    def test_monthly_export_expiry_and_correction_on_reload(self):
        self.assertEqual(self.config.export_price(date(2025, 1, 31)).gross_per_kwh, Decimal("0.08"))
        self.assertEqual(self.config.export_price(date(2025, 2, 1)).gross_per_kwh, Decimal("0.075"))
        expired = self.config.export_price(date(2025, 4, 1))
        self.assertTrue(expired.carried_forward)
        self.assertEqual(expired.effective_until, date(2025, 3, 1))
        with self.assertRaises(PriceUnavailable):
            self.config.export_price(date(2025, 3, 1), carry_forward=False)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fictional.json"
            path.write_text(json.dumps(self.raw))
            before = load_installation(path).export_price(date(2025, 2, 10))
            self.raw["export_tariffs"][1].update(status="confirmed", price={"amount": 0.09, "vat_basis": "gross"})
            self.raw["import_tariffs"][0]["components"]["energy"]["amount"] = 0.13
            path.write_text(json.dumps(self.raw))
            corrected = load_installation(path)
            after = corrected.export_price(date(2025, 2, 10))
            self.assertEqual(before.status, "provisional")
            self.assertEqual(after.status, "confirmed")
            self.assertEqual(after.gross_per_kwh, Decimal("0.09"))
            self.assertEqual(corrected.import_price(date(2025, 2, 10)).gross_per_kwh, Decimal("0.240"))

    def test_local_midnight_and_dst_use_configured_zone(self):
        self.assertEqual(self.config.import_price(datetime(2025, 6, 30, 21, 59, tzinfo=timezone.utc)).gross_per_kwh, Decimal("0.228"))
        self.assertEqual(self.config.import_price(datetime(2025, 6, 30, 22, tzinfo=timezone.utc)).gross_per_kwh, Decimal("0.24"))
        for instant in (datetime(2025, 3, 30, 0, 30, tzinfo=timezone.utc), datetime(2025, 3, 30, 1, 30, tzinfo=timezone.utc)):
            self.assertEqual(self.config.import_price(instant).local_date, date(2025, 3, 30))
        for hour in (0, 1):  # repeated autumn hour resolves to same local day
            self.assertEqual(self.config.import_price(datetime(2025, 10, 26, hour, 30, tzinfo=timezone.utc)).local_date, date(2025, 10, 26))
        with self.assertRaises(ValueError):
            self.config.import_price(datetime(2025, 1, 1))

    def test_month_rollover_leap_year_and_unsorted_input(self):
        self.raw["export_tariffs"] = [
            {"month": month, "status": "confirmed", "vat_rate": 0, "price": {"amount": 0.05, "vat_basis": "gross"}}
            for month in ("2024-12", "2024-02")]
        self.raw["import_tariffs"].reverse()
        config = parse_installation(self.raw)
        self.assertEqual(config.export_price(date(2024, 2, 29)).effective_until, date(2024, 3, 1))
        self.assertEqual(config.export_price(date(2024, 12, 31)).effective_until, date(2025, 1, 1))
        self.assertEqual(config.import_price(date(2025, 1, 1)).status, "confirmed")

    def test_optional_inputs_and_explicit_zero_are_distinct_from_missing(self):
        del self.raw["installation"]["area_m2"]
        del self.raw["installation"]["commissioning_date"]
        self.raw["installation"]["usable_battery_kwh"] = 0
        first = self.raw["import_tariffs"][0]
        for price in first["components"].values():
            price["amount"] = 0
        first["discounts"] = []
        config = parse_installation(self.raw)
        self.assertIsNone(config.area_m2)
        self.assertIsNone(config.commissioning_date)
        self.assertEqual(config.usable_battery_kwh, 0)
        self.assertEqual(config.import_price(date(2025, 1, 1)).gross_per_kwh, 0)

    def test_validation_rejects_invalid_capacities_prices_periods_and_metadata(self):
        cases = [
            (("installation", "panel_kwp"), 0),
            (("installation", "panel_kwp"), -1),
            (("installation", "panel_kwp"), True),
            (("installation", "panel_kwp"), "8.4"),
            (("installation", "usable_battery_kwh"), -1),
            (("installation", "area_m2"), 0),
            (("installation", "panel_kwp"), float("nan")),
            (("installation", "panel_kwp"), float("inf")),
            (("installation", "timezone"), "fictional-not-a-zone"),
            (("installation", "timezone"), None),
            (("installation", "currency"), "eur"),
            (("installation", "commissioning_date"), "2025-02-30"),
            (("import_tariffs",), []),
            (("export_tariffs",), []),
            (("import_tariffs", 1, "from"), "2025-06-30"),
            (("import_tariffs", 0, "until"), "2025-01-01"),
            (("import_tariffs", 0, "status"), "unknown"),
            (("import_tariffs", 0, "vat_rate"), 20),
            (("import_tariffs", 0, "components", "energy", "vat_basis"), "unknown"),
            (("import_tariffs", 0, "discounts", 0, "scope"), "unknown"),
            (("import_tariffs", 0, "discounts", 0, "amount"), 10),
            (("import_tariffs", 0, "fixed_charges", 0, "period"), "day"),
            (("export_tariffs", 1, "month"), "2025-01"),
            (("export_tariffs", 1, "month"), "2025-13"),
            (("export_tariffs", 1, "price", "amount"), -0.1),
        ]
        for keys, value in cases:
            with self.subTest(field=keys):
                raw = deepcopy(self.raw)
                target = raw
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                with self.assertRaises(ConfigurationError):
                    parse_installation(raw)
        for key in ("installation", "import_tariffs", "export_tariffs"):
            raw = deepcopy(self.raw)
            del raw[key]
            with self.assertRaisesRegex(ConfigurationError, "required"):
                parse_installation(raw)
        del self.raw["import_tariffs"][0]["components"]["energy"]
        with self.assertRaisesRegex(ConfigurationError, "required"):
            parse_installation(self.raw)

    def test_errors_do_not_echo_values_unknown_keys_paths_or_json(self):
        sentinel = "FICTIONAL-SENSITIVE-SENTINEL"
        for raw in ({sentinel: sentinel}, {**self.raw, sentinel: sentinel}):
            with self.assertRaises(ConfigurationError) as error:
                parse_installation(raw)
            self.assertNotIn(sentinel, str(error.exception))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / sentinel
            for content in ('{"' + sentinel + '":', '{"version":1,"version":1}', '\xff'):
                path.write_text(content)
                with self.assertRaises(ConfigurationError) as error:
                    load_installation(path)
                self.assertNotIn(sentinel, str(error.exception))
            path.unlink()
            with self.assertRaises(ConfigurationError) as error:
                load_installation(path)
            self.assertNotIn(sentinel, str(error.exception))


class ProvisionTests(unittest.TestCase):
    def test_fictional_config_to_existing_dashboard_value_without_template_changes(self):
        templates = ROOT / "grafana/provisioning/dashboards"
        originals = {p.name: p.read_bytes() for p in templates.iterdir() if p.is_file()}
        with tempfile.TemporaryDirectory() as temp, working_directory(temp):
            # Represents stored history; rendering must never modify it.
            Path("data").mkdir()
            Path("data/history").write_text("fictional history sentinel")
            old_umask = os.umask(0o077)
            try:
                render(load_installation(EXAMPLE))
            finally:
                os.umask(old_umask)
            generated = Path("private/generated/dashboards")
            self.assertEqual(generated.stat().st_mode & 0o777, 0o755)
            self.assertEqual(set(originals), {p.name for p in generated.iterdir()})
            pv = json.loads((generated / "photovoltaik.json").read_text())
            self.assertEqual(pv["uid"], json.loads(originals["photovoltaik.json"])["uid"])
            variables = {v["name"]: v["current"]["value"] for v in pv["templating"]["list"]}
            soc = next(p for p in pv["panels"] if p["id"] == 21)
            displayed = soc["description"].replace("${usable_battery_kwh}", variables["usable_battery_kwh"])
            self.assertIn("11.5 kWh", displayed)
            self.assertIn("not measured remaining energy", displayed)
            self.assertEqual(pv["timezone"], "Europe/Berlin")
            hp = json.loads((generated / "heatpump.json").read_text())
            variables = {v["name"]: v["current"]["value"] for v in hp["templating"]["list"]}
            expected = int(datetime(2024, 2, 12, tzinfo=load_installation(EXAMPLE).timezone).timestamp())
            self.assertEqual(variables["inbetriebnahme_ts"], str(expected))
            self.assertEqual(variables["wohnflaeche"], "123")
            self.assertEqual(Path("data/history").read_text(), "fictional history sentinel")
            # No tariff prices/credits/fixed charges in the generated dashboard.
            self.assertNotIn("restricted_credits", json.dumps(hp))
            self.assertEqual(Path("private").stat().st_mode & 0o777, 0o700)
            self.assertEqual((generated / "heatpump.json").stat().st_mode & 0o777, 0o644)
        self.assertEqual(originals, {p.name: p.read_bytes() for p in templates.iterdir() if p.is_file()})

    def test_optional_values_remain_unavailable_not_zero(self):
        raw = json.loads(EXAMPLE.read_text())
        del raw["installation"]["area_m2"]
        del raw["installation"]["commissioning_date"]
        dashboard = render_dashboard({"panels": []}, parse_installation(raw))
        variables = {v["name"]: v["query"] for v in dashboard["templating"]["list"]}
        self.assertEqual(variables["wohnflaeche"], "NaN")
        self.assertEqual(variables["inbetriebnahme_ts"], "NaN")

    def test_output_cannot_target_tracked_templates_or_storage(self):
        with tempfile.TemporaryDirectory() as temp, working_directory(temp):
            for target in ("grafana/provisioning/dashboards", "data", "private/generated", "private/generated/../../data"):
                with self.assertRaises(ConfigurationError):
                    render(load_installation(EXAMPLE), target)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_private_directory_symlink_cannot_write_into_public_directory(self):
        with tempfile.TemporaryDirectory() as temp, working_directory(temp):
            Path("public").mkdir()
            Path("private").symlink_to(Path("public").resolve(), target_is_directory=True)
            with self.assertRaises(ConfigurationError):
                render(load_installation(EXAMPLE))
            self.assertEqual(list(Path("public").iterdir()), [])

    def test_cli_missing_invalid_config_is_safe_and_preserves_last_render(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "PYTHONPATH": str(ROOT)}
            command = [sys.executable, "-m", "scraper.provision", "render"]
            output = Path(temp) / "private/generated/dashboards"
            output.mkdir(parents=True)
            sentinel = output / "previous.json"
            sentinel.write_text("fictional previous rendering")
            for content in (None, '{"FICTIONAL-SECRET":"FICTIONAL-SECRET"}'):
                if content:
                    (Path(temp) / "private/installation.json").write_text(content)
                result = subprocess.run(command, cwd=temp, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("FICTIONAL-SECRET", result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(sentinel.read_text(), "fictional previous rendering")
            result = subprocess.run(command + ["--config", str(EXAMPLE)], cwd=temp, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "Private dashboards rendered")
            self.assertNotIn("11.5", result.stdout + result.stderr)
            self.assertFalse(sentinel.exists())  # obsolete generated JSON is removed

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI unavailable")
    def test_opt_in_compose_changes_only_dashboard_mount_offline(self):
        # `config` parses files only; it never contacts the daemon or deploys.
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            for name in ("docker-compose.yml", "docker-compose.private.yml"):
                shutil.copyfile(ROOT / name, temp / name)
            (temp / ".env").write_text("FRONIUS_ENABLED=false\nISG_BASE_URL=http://192.0.2.10\n")
            env = {"PATH": os.environ["PATH"], "HOME": str(temp),
                   "GF_SECURITY_ADMIN_PASSWORD": "fictional-test-only", "DATA_PATH": str(temp / "data")}
            models = []
            for files in (("docker-compose.yml",), ("docker-compose.yml", "docker-compose.private.yml")):
                command = ["docker", "compose", "--project-name", "fictional-config-test"]
                for name in files:
                    command.extend(["-f", name])
                result = subprocess.run(command + ["config", "--format", "json"], cwd=temp, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, "fictional Compose model failed validation")
                models.append(json.loads(result.stdout))
            base, private = models
            target = "/otel-lgtm/grafana/conf/provisioning/dashboards"
            mounts = private["services"]["lgtm"]["volumes"]
            dashboard = next(m for m in mounts if m["target"] == target)
            self.assertEqual(dashboard["source"], str(temp / "private/generated/dashboards"))
            self.assertTrue(dashboard["read_only"])
            self.assertFalse(dashboard["bind"]["create_host_path"])
            # Restore the one changed mount and require whole-model equality:
            # /data, networks, ports, history and all other services are unchanged.
            original = next(m for m in base["services"]["lgtm"]["volumes"] if m["target"] == target)
            mounts[mounts.index(dashboard)] = original
            self.assertEqual(private, base)

    def test_private_paths_are_ignored_and_not_tracked(self):
        paths = ["private/installation.json", "private/generated/dashboards/heatpump.json", "private/backups/config.json", ".env.local"]
        for path in paths:
            result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
            self.assertEqual(result.returncode, 0)
        tracked = subprocess.check_output(["git", "ls-files", "private", ".env", ".env.local"], cwd=ROOT, text=True)
        self.assertEqual(tracked, "")
        for name in ("heatpump", "photovoltaik"):
            dashboard = json.loads((ROOT / f"grafana/provisioning/dashboards/{name}.json").read_text())
            self.assertEqual(dashboard["timezone"], "browser")
            for variable in dashboard.get("templating", {}).get("list", []):
                self.assertEqual(variable["current"]["value"], "NaN")


if __name__ == "__main__":
    unittest.main()
