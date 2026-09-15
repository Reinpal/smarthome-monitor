"""Validate private inputs and render existing Grafana dashboards offline.

Run from the repository root: python -m scraper.provision validate|render
Only this module writes artifacts; installation.py is the calculation API.
"""
import argparse
from datetime import datetime, time
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from scraper.installation import ConfigurationError, DEFAULT_CONFIG, load_installation

TEMPLATES = Path(__file__).resolve().parents[1] / "grafana/provisioning/dashboards"
OUTPUT = Path("private/generated/dashboards")


def _constant(name, label, value):
    text = str(value)
    return {"name": name, "label": label, "type": "constant", "hide": 2,
            "query": text, "current": {"text": text, "value": text},
            "options": [{"text": text, "value": text, "selected": True}]}


def render_dashboard(dashboard, installation):
    """Return a copy with private parameters; never modify tracked templates."""
    dashboard = json.loads(json.dumps(dashboard))
    commissioning = installation.commissioning_date
    timestamp = int(datetime.combine(commissioning, time.min, installation.timezone).timestamp()) if commissioning else "NaN"
    parameters = {
        "wohnflaeche": ("Configured area (m²; not a certified heat boundary)", installation.area_m2 if installation.area_m2 is not None else "NaN"),
        "inbetriebnahme_ts": ("Configured commissioning (local midnight)", timestamp),
        "panel_kwp": ("Configured panel capacity (kWp)", installation.panel_kwp),
        "usable_battery_kwh": ("Configured usable battery capacity (kWh)", installation.usable_battery_kwh),
    }
    variables = dashboard.setdefault("templating", {}).setdefault("list", [])
    variables[:] = [v for v in variables if v["name"] not in parameters]
    variables.extend(_constant(name, label, value) for name, (label, value) in parameters.items())
    dashboard["timezone"] = str(installation.timezone)
    return dashboard


def render(installation, output=OUTPUT):
    """Stage a complete rendering before replacing output files.

    Output must stay below private/generated; inputs and data stores are never
    written. Fixed templates only; no arbitrary file paths from private inputs.
    """
    output = Path(output)
    if Path("private").is_symlink() or Path("private/generated").is_symlink():
        raise ConfigurationError("output: private directory symlinks not permitted")
    root = Path("private/generated").resolve()
    if not output.resolve().is_relative_to(root) or output.resolve() == root:
        raise ConfigurationError("output: must be a directory below private/generated")
    if output.is_symlink():
        raise ConfigurationError("output: symlink not permitted")
    # The parent protects artifacts on the host; bind only the child directory
    # into Grafana, whose unprivileged process needs readable files.
    Path("private").mkdir(mode=0o700, exist_ok=True)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.mkdir(mode=0o755, parents=True, exist_ok=True)
    output.chmod(0o755)  # Also works when private setup uses umask 077.
    with tempfile.TemporaryDirectory(dir=root) as staging:
        staging = Path(staging)
        shutil.copyfile(TEMPLATES / "dashboards.yml", staging / "dashboards.yml")
        for source in sorted(TEMPLATES.glob("*.json")):
            dashboard = render_dashboard(json.loads(source.read_text()), installation)
            (staging / source.name).write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n")
        names = {source.name for source in staging.iterdir()}
        for source in staging.iterdir():
            source.chmod(0o644)
            os.replace(source, output / source.name)
        # This dedicated directory is wholly generated; do not retain dashboards
        # removed from the template set (they can contain obsolete private data).
        for obsolete in output.glob("*.json"):
            if obsolete.name not in names:
                obsolete.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private configuration validation and offline Grafana rendering")
    parser.add_argument("command", choices=("validate", "render"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    try:
        installation = load_installation(args.config)
        if args.command == "render":
            render(installation)
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("provisioning: unable to render templates; check paths and permissions", file=sys.stderr)
        return 1
    print("Private configuration valid" if args.command == "validate" else "Private dashboards rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
