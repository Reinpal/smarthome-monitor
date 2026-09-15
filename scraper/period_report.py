"""Read-only history query to private native Grafana report. No deploy/restart."""
import argparse
from datetime import date, datetime, time
import json
import os
from pathlib import Path
import sys

from scraper.installation import DEFAULT_CONFIG, load_installation
from scraper.periods import Period
from scraper.period_query import PrometheusHistory, query_report
from scraper.provision import render


def main(argv=None):
    parser = argparse.ArgumentParser(description='Render an explicit fixed-period Grafana report; no telemetry writes or deployment')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--from', dest='start', required=True, help='Local ISO date or offset-aware ISO datetime; inclusive')
    parser.add_argument('--until', required=True, help='Local ISO date or offset-aware ISO datetime; exclusive; needs a fresh bracketing observation')
    parser.add_argument('--no-comparison', action='store_true')
    args = parser.parse_args(argv)
    try:
        installation = load_installation(args.config)
        def endpoint(value):
            if len(value) == 10:
                return datetime.combine(date.fromisoformat(value), time.min, installation.timezone)
            return datetime.fromisoformat(value)
        period = Period(endpoint(args.start), endpoint(args.until))
        # Environment only: URLs/selector values never appear in command arguments or output.
        reader = PrometheusHistory(os.environ['PERIOD_PROMETHEUS_URL'],
                                   labels=json.loads(os.environ.get('PERIOD_PROMETHEUS_LABELS', '{}')))
        report = query_report(reader, installation, period, compare=not args.no_comparison)
        render(installation, period_report=report)
    except Exception:
        # Raw HTTP/configuration/filesystem exceptions can contain private inputs.
        print('Period report unavailable: check configuration, aware dates, source selection and read-only query access privately', file=sys.stderr)
        return 1
    print('Private fixed-period dashboards rendered; no deployment performed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
