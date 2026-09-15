"""Read-only Prometheus adapter and public query_report interface (v1).

Raw range vectors retain stored export timestamps; query_range/lookback and
extrapolating counter functions are deliberately not used. All errors are safe.
"""
from bisect import bisect_left
from datetime import datetime, timezone
import json
import math
import urllib.parse
import urllib.request

from scraper.periods import History, Observation, SOURCES, calculate_period, month_comparison


class QueryUnavailable(RuntimeError):
    """Safe diagnostic: never contains addresses, labels, or response bodies."""


class PrometheusHistory:
    def __init__(self, base_url, *, labels=None, timeout=30):
        self._base = base_url.rstrip('/')
        self._labels = dict(labels or {})
        if any(not k.replace('_', 'a').isalnum() or k[0].isdigit() for k in self._labels):
            raise QueryUnavailable("query: invalid selector label")
        if set(self._labels) & {"__name__", "metric", "collector", "source"}:
            raise QueryUnavailable("query: reserved selector label")
        self._timeout = timeout

    def _selector(self, name, attributes):
        labels = {**self._labels, **attributes}
        return name + ('{' + ','.join(k + '=' + json.dumps(str(v)) for k, v in sorted(labels.items())) + '}' if labels else '')

    def _fetch(self, selector, start, end):
        values, identity = {}, None
        cursor = start
        while cursor < end:
            stop = min(end, cursor + 86400)
            params = urllib.parse.urlencode({'query': f'{selector}[{math.ceil(stop - cursor) + 1}s]', 'time': stop})
            try:
                with urllib.request.urlopen(self._base + '/api/v1/query?' + params, timeout=self._timeout) as response:
                    payload = json.load(response)
                if payload.get('status') != 'success' or payload['data']['resultType'] != 'matrix' or payload.get('warnings'):
                    raise ValueError
                series = payload['data']['result']
                if len(series) > 1:
                    raise QueryUnavailable("query: ambiguous series; select one installation/exporter")
                if series:
                    labels = {k: v for k, v in series[0]['metric'].items() if k not in
                              {'__name__', 'metric', 'collector', 'source', 'otel_scope_name', 'otel_scope_version'}}
                    if identity is not None and identity != labels:
                        raise QueryUnavailable("query: series identity changed within period")
                    identity = labels
                    for at, value in series[0]['values']:
                        values[float(at)] = float(value)
            except QueryUnavailable:
                raise
            except Exception:
                raise QueryUnavailable("query: unable to read complete Prometheus history") from None
            cursor = stop
        return values, identity

    def read(self, period):
        """Return keyed History; no cross-call cache (tariff/history corrections visible).

        Allow one day of boundary padding; thresholds >1 day are rejected rather
        than silently truncating a configured collection interval.
        """
        if period.seconds > 370 * 86400:
            raise QueryUnavailable("query: maximum period is 370 elapsed days")
        start, end = period.start.timestamp() - 86400, period.end.timestamp() + 86400
        cache = {}
        def fetch(name, **attributes):
            selector = self._selector(name, attributes)
            if selector not in cache:
                cache[selector] = self._fetch(selector, start, end)
            return cache[selector]
        sorted_times = {}
        def aligned(data, at):
            # SDK scopes can differ by milliseconds. Never use Prometheus's 5m lookback.
            if id(data) not in sorted_times:
                sorted_times[id(data)] = sorted(data)
            times = sorted_times[id(data)]
            i = bisect_left(times, at)
            nearby = times[max(0, i - 1):i + 1]
            if not nearby:
                return None
            nearest = min(nearby, key=lambda t: abs(t - at))
            return data[nearest] if abs(nearest - at) <= 2 else None
        output = {}
        for key, source in SOURCES.items():
            raw, identity = fetch(source.prom_name)
            companions = [fetch('smarthome_measurement_last_success_seconds', metric=source.name),
                          fetch('smarthome_measurement_present', metric=source.name),
                          fetch('smarthome_measurement_contract_version'),
                          fetch('smarthome_collection_last_success_seconds', collector=source.collector, source=source.endpoint),
                          fetch('smarthome_collection_stale_after_seconds', collector=source.collector, source=source.endpoint)]
            if any(other is not None and identity is not None and other != identity for _, other in companions):
                raise QueryUnavailable("query: measurement and health identity mismatch")
            observations, invalid, reasons = {}, [], set()
            # Include explicit missing exports, not only times the raw metric exists.
            times = sorted(set(raw) | set(companions[1][0]))
            for at in times:
                value = aligned(raw, at)
                observed, present, version, health, threshold = [aligned(data, at) for data, _ in companions]
                metadata = (observed, present, version, health, threshold)
                if any(v is None or not math.isfinite(v) for v in metadata):
                    invalid.append(at)
                    reasons.add("legacy freshness unknown or missing per-field/endpoint metadata")
                    continue
                if not (version == 2 and present == 1 and 0 < threshold <= 86400
                        and 0 <= at - observed < threshold and 0 <= at - health < threshold
                        and value is not None and math.isfinite(value)):
                    invalid.append(at)
                    reasons.add("missing/stale field or endpoint; cached exports are not reads")
                    continue
                if (source.kind in ('counter', 'power') and value < 0) or (source.kind == 'soc' and not 0 <= value <= 100):
                    invalid.append(at)
                    reasons.add("invalid source value/unit semantics")
                    continue
                old = observations.get(observed)
                if old is not None and old.value != value:
                    raise QueryUnavailable("query: conflicting values for one source observation")
                observations[observed] = Observation(observed, value, threshold)
            output[key] = History(tuple(observations[t] for t in sorted(observations)), tuple(invalid), tuple(sorted(reasons)))
        return output


def query_report(reader, installation, period, *, compare=True):
    """Public read -> calculate -> JSON-safe report, accepted by render_panel/dashboard.

    reader.read(Period) returns histories. Production adapter is PrometheusHistory;
    tests can supply fictional observations without mocking calculation internals.
    """
    report = calculate_period(reader.read(period), installation, period)
    report['generated_at'] = datetime.now(timezone.utc).isoformat()
    report['comparison'] = {'status': 'unavailable', 'reason': 'comparison requires a local month-to-date period'}
    windows = month_comparison(period, installation) if compare else None
    if windows:
        current, prior = windows
        report['comparison'] = {
            'status': 'equal_elapsed_windows',
            'reason': 'Both comparison windows capped to shorter elapsed month; headline remains requested period',
            'current': calculate_period(reader.read(current), installation, current),
            'previous': calculate_period(reader.read(prior), installation, prior),
        }
    report['year_over_year'] = {'status': 'unavailable', 'reason': 'No validated equivalent prior-year history; no annualization'}
    return report
