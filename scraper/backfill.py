"""Bounded legacy reconstruction. Read-only inventory; apply builds PRIVATE blocks only.

No device freshness is inferred, no live database is opened, and no prices are
persisted. See docs/backfill.md for the one-shot offline installation/rollback.
"""
import argparse
from bisect import bisect_right
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from scraper.periods import SOURCES

PREFIX = 'smarthome_legacy_v1_'
FIELDS = ('start_seconds', 'end_seconds', 'left', 'right', 'rate', 'zero_seconds',
          'shape', 'stale_after_seconds', 'valid_until_seconds')
RESTART_LABELS = {'instance', 'service_instance_id'}


class BackfillError(ValueError):
    """Sanitized public error; never include input values or paths."""


def canonical(rows):
    """One installation, restart labels only. Agreeing overlaps are deduplicated.

    Concurrent streams must agree on their interpolated overlap (not summed or
    selected arbitrarily). Disagreement excludes the overlap plus its adjacent
    pairs. Nonoverlapping restarts may join; a subsequent decrease is a reset.
    """
    identities, streams, values = set(), [], {}
    for row in rows:
        labels = row['metric']
        identities.add(tuple(sorted((k, v) for k, v in labels.items()
                                    if k not in RESTART_LABELS | {'__name__'})))
        points = sorted((float(t), float(v)) for t, v in row['values'])
        if any(not math.isfinite(t) for t, _ in points) or len({t for t, _ in points}) != len(points):
            raise BackfillError('backfill: invalid or duplicate export timestamps')
        streams.append(points)
        for t, v in points:
            if t in values and values[t] != v:
                values[t] = float('nan')
            else:
                values[t] = v
    if len(identities) > 1:
        raise BackfillError('backfill: ambiguous installation identity')
    conflicts = []
    for i, left in enumerate(streams):
        for right in streams[i + 1:]:
            if not left or not right:
                continue
            a, b = max(left[0][0], right[0][0]), min(left[-1][0], right[-1][0])
            if a > b:
                continue
            def value(points, at):
                j = bisect_right(points, at, key=lambda p: p[0]) - 1
                if points[j][0] == at:
                    return points[j][1]
                x, y = points[j], points[j + 1]
                return x[1] + (y[1] - x[1]) * (at - x[0]) / (y[0] - x[0])
            checks = sorted({a, b} | {t for t, _ in left + right if a <= t <= b})
            if any(not math.isclose(value(left, t), value(right, t), rel_tol=1e-9, abs_tol=1e-9) for t in checks):
                conflicts.append((a, b))
    return sorted(values.items()), conflicts


def _positive_integral(left, right, seconds):
    if left >= 0 and right >= 0:
        return (left + right) * seconds / 2
    if left <= 0 and right <= 0:
        return 0.
    return max(left, right) ** 2 * seconds / (2 * abs(right - left))


def reconstruct(rows, key, start, cutoff, *, power_gap=900, counter_gap=86400):
    """Resample accepted raw pairs to minute energy-equivalent facts.

    A bucket with ANY uncovered part is omitted, never filled with zero. The
    first/last bucket is clipped to the requested bounds. Partial-minute power
    allocation is uniform; complete minute integrals preserve sign crossings.
    """
    source = SOURCES[key]
    if source.kind == 'soc':
        raise BackfillError('backfill: SOC inventory is outside legacy reconstruction')
    if any(row['metric'].get('__name__') != source.prom_name for row in rows):
        raise BackfillError('backfill: source name/unit mismatch')
    points, conflicts = canonical(rows)
    factor = .001 if source.unit == 'Wh' else 1000 if source.unit == 'MWh' else -1 if source.kind in ('load', 'charge') else 1
    counter = source.kind == 'counter'
    maximum = counter_gap if counter else power_gap
    buckets, rejected = {}, {'reset': 0, 'gap': 0, 'conflict': 0, 'invalid': 0}
    accepted_delta, covered, largest, plateau = 0., 0., 0., 0.
    segments = []
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        a, b = max(start, t0), min(cutoff, t1)
        if b <= a:
            continue
        dt = t1 - t0
        largest = max(largest, dt)
        reason = ('invalid' if not all(math.isfinite(v) for v in (v0, v1)) or
                  (source.kind in ('counter', 'power') and min(v0, v1) < 0) else
                  'conflict' if any(t0 <= hi and t1 >= lo for lo, hi in conflicts) else
                  'reset' if counter and v1 < v0 else 'gap' if dt > maximum else None)
        if reason:
            rejected[reason] += 1
            continue
        if v0 == v1:
            plateau += b - a  # Cached or real constant: cannot retrospectively distinguish.
        slope = (v1 - v0) * factor / dt
        left = v0 * factor
        if counter:
            accepted_delta += slope * (b - a)
        covered += b - a
        if not segments or abs(segments[-1][1] - a) > 1e-6:
            segments.append([a, b, v0 + (v1-v0)*(a-t0)/dt, v0 + (v1-v0)*(b-t0)/dt])
        else:
            segments[-1][1], segments[-1][3] = b, v0 + (v1-v0)*(b-t0)/dt
        while a < b:
            tick = (math.floor(a / 60) + 1) * 60
            end = min(b, tick)
            duration = end - a
            energy = slope * duration if counter else _positive_integral(
                left + slope * (a-t0), left + slope * (end-t0), duration) / 3600000
            old_energy, old_duration = buckets.get(tick, (0., 0.))
            buckets[tick] = old_energy + energy, old_duration + duration
            a = end
    facts = []
    for tick, (energy, seconds) in sorted(buckets.items()):
        a, b = max(start, tick-60), min(cutoff, tick)
        if abs(seconds - (b-a)) > 1e-5:
            continue
        rate = energy / seconds
        watts = rate * 3600000
        facts.append((tick, {'start_seconds': a, 'end_seconds': b,
                            'left': 0. if counter else watts, 'right': energy if counter else watts,
                            'rate': rate if counter else 0., 'zero_seconds': a, 'shape': 1.,
                            'stale_after_seconds': 0., 'valid_until_seconds': b + .001}))
    total = sum((v['right'] if counter else v['left'] * (v['end_seconds']-v['start_seconds']) / 3600000) for _, v in facts)
    # Independent first/last readings per uninterrupted accepted segment.
    reconciled = sum((last-first)*factor for _, _, first, last in segments) if counter else None
    report = {'provenance': 'legacy_estimate', 'freshness': 'unknown', 'start': start, 'cutoff': cutoff,
              'raw_samples': len(points), 'overlap_conflicts': [list(span) for span in conflicts], 'rejected_pairs': rejected,
              'largest_export_gap_seconds': largest, 'equal_reading_seconds': plateau,
              'accepted_pair_seconds': covered, 'covered_seconds': sum(v['end_seconds']-v['start_seconds'] for _, v in facts),
              'intervals': len(facts), 'energy_kwh': total, 'accepted_counter_delta_kwh': accepted_delta if counter else None,
              'independent_segment_delta_kwh': reconciled,
              'counter_reconciliation_error_kwh': accepted_delta - reconciled if counter else None,
              'minute_boundary_discard_kwh': accepted_delta - total if counter else None,
              'segments': segments if counter else [], 'source_unit': source.unit, 'boundary': source.boundary}
    if counter and points:
        before = [p for p in points if p[0] <= start]
        after = [p for p in points if p[0] >= cutoff]
        if before and after and math.isfinite(before[-1][1]) and math.isfinite(after[0][1]):
            first, last = before[-1], after[0]
            report['raw_bracketing_readings'] = [list(first), list(last)]
            report['raw_bracketing_delta_kwh'] = (last[1]-first[1])*factor
            report['boundary_allocation_kwh'] = report['raw_bracketing_delta_kwh']-accepted_delta
            report['raw_bracketing_reconciles'] = not any(rejected.values())
            # The raw bracketing delta is NOT a repaired total across a reset,
            # conflict or long gap. boundary_allocation then includes exclusions.
    return facts, report


def plan(data, start, until, *, power_gap=900, counter_gap=86400):
    if not all(math.isfinite(v) for v in (start, until, power_gap, counter_gap)) or not 0 < until-start <= 370*86400:
        raise BackfillError('backfill: invalid bounded range')
    if not 0 < power_gap <= 900 or not 0 < counter_gap <= 86400:
        raise BackfillError('backfill: gap limits exceed reviewed policy')
    if data.get(PREFIX + 'end_seconds'):
        raise BackfillError('backfill: existing reconstruction found; do not overlay another plan')
    # Earliest accepted live interval START, not rule receipt time or v2 marker.
    # Per-source cutoff joins different collector cadences without overlap.
    cutoffs = {}
    for row in data.get('smarthome_period_v1_start_seconds', []):
        key = row['metric']['metric']
        cutoffs[key] = min(cutoffs.get(key, until), *(float(v) for _, v in row['values']))
    facts, report = {}, {'version': 1, 'start': start, 'until': until,
                          'power_gap_seconds': power_gap, 'counter_gap_seconds': counter_gap, 'sources': {}}
    for key, source in SOURCES.items():
        if source.kind == 'soc':
            continue
        cutoff = min(until, cutoffs.get(key, until))
        if cutoff <= start:
            continue
        facts[key], report['sources'][key] = reconstruct(data.get(source.prom_name, []), key, start, cutoff,
                                                         power_gap=power_gap, counter_gap=counter_gap)
    return facts, report


def openmetrics(facts):
    for field in FIELDS:
        yield '# TYPE ' + PREFIX + field + ' gauge\n'
        for key, intervals in sorted(facts.items()):
            for tick, values in intervals:
                yield f'{PREFIX}{field}{{metric="{key}",provenance="legacy_estimate"}} {values[field]:.17g} {tick:.3f}\n'
    yield '# EOF\n'


def stage(data, start, until, output, *, apply=False, promtool=None):
    """Deterministic one-plan directory. --apply ONLY builds blocks in this directory.

    No API writes, target TSDB argument, shell commands, or active storage access.
    Changed input/output is a collision, not an implicit overwrite/reapplication.
    """
    output = Path(output)
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise BackfillError('backfill: symlink output refused')
    facts, report = plan(data, start, until)
    content = ''.join(openmetrics(facts)).encode()
    fingerprint = hashlib.sha256(content).hexdigest()
    report['sha256'] = fingerprint
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    output.chmod(0o700)
    marker = output / 'report.json'
    if marker.exists():
        if json.loads(marker.read_text()) != report or (output/'intervals.om').read_bytes() != content:
            raise BackfillError('backfill: staging collision; use a new private directory')
    elif any(output.iterdir()):
        raise BackfillError('backfill: nonempty staging directory refused')
    else:
        (output/'intervals.om').write_bytes(content)
        marker.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    if apply:
        if not promtool:
            raise BackfillError('backfill: promtool required for private block creation')
        manifest = output/'blocks.json'
        blocks = output/'blocks'
        if manifest.exists():
            expected = json.loads(manifest.read_text())
            if expected != block_hashes(blocks):
                raise BackfillError('backfill: staged blocks changed')
        else:
            if blocks.exists():
                raise BackfillError('backfill: incomplete block staging; inspect privately')
            blocks.mkdir(mode=0o700)
            # promtool rescans its input for each two-hour block. Daily chunks
            # bound memory/IO without changing sample timestamps or identities.
            days = {}
            for key, intervals in facts.items():
                for tick, values in intervals:
                    days.setdefault(math.floor(tick/86400), {}).setdefault(key, []).append((tick, values))
            chunk = output/'chunk.om'
            for day in sorted(days):
                chunk.write_text(''.join(openmetrics(days[day])))
                result = subprocess.run([str(promtool), 'tsdb', 'create-blocks-from', 'openmetrics', '--quiet',
                                         str(chunk), str(blocks)], capture_output=True,
                                        env={**os.environ, 'TMPDIR': str(output.resolve())})
                if result.returncode:
                    (output/'promtool-error.log').write_bytes(result.stdout + result.stderr)
                    raise BackfillError('backfill: private block creation failed')
            chunk.unlink(missing_ok=True)
            manifest.write_text(json.dumps(block_hashes(blocks), indent=2, sort_keys=True)+'\n')
    return report


def block_hashes(blocks):
    return {str(p.relative_to(blocks)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(blocks.rglob('*')) if p.is_file()}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def inventory(endpoint, start, until):
    """Bounded raw matrix reads; labels preserved privately, restarts not filtered.

    The live-start and existing-backfill checks span the full requested window.
    Padding is for bracketing only. A future until is disallowed by the CLI.
    """
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    names = {s.prom_name for s in SOURCES.values()} | {'smarthome_period_v1_start_seconds', PREFIX+'end_seconds'}
    data = {}
    for name in sorted(names):
        rows = {}
        cursor = start-86400
        while cursor < until+86400:
            stop = min(until+86400, cursor+86400)
            parameters = urlencode({'query': f'{name}[{math.ceil(stop-cursor)+1}s]', 'time': stop})
            try:
                with opener.open(endpoint.rstrip('/')+'/api/v1/query?'+parameters, timeout=60) as response:
                    body = json.load(response)
                if body.get('status') != 'success' or body.get('warnings') or body['data']['resultType'] != 'matrix':
                    raise ValueError
                for row in body['data']['result']:
                    identity = json.dumps(row['metric'], sort_keys=True)
                    record = rows.setdefault(identity, {'metric': row['metric'], 'values': {}})
                    record['values'].update((float(t), v) for t, v in row['values'])
            except Exception:
                raise BackfillError('backfill: unable to read complete private inventory') from None
            cursor = stop
        data[name] = [{'metric': row['metric'], 'values': sorted(row['values'].items())} for row in rows.values()]
    if data[PREFIX+'end_seconds']:
        raise BackfillError('backfill: existing reconstruction found; do not overlay another plan')
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path, help='Protected JSON: start/until epoch seconds, output directory; optional inventory gzip path')
    parser.add_argument('--apply', action='store_true', help='Build PRIVATE blocks only; never installs them')
    parser.add_argument('--promtool', type=Path)
    args = parser.parse_args(argv)
    try:
        import time
        if args.request.is_symlink() or args.request.stat().st_mode & 0o077 or args.request.parent.stat().st_mode & 0o077:
            raise BackfillError('backfill: request and parent must be private')
        request = json.loads(args.request.read_text())
        if not Path(request['output']).resolve().is_relative_to(args.request.parent.resolve()):
            raise BackfillError('backfill: output must stay beneath the protected request directory')
        start, until = float(request['start']), float(request['until'])
        if not 0 < until-start <= 370*86400 or math.ceil(until/60)*60 > time.time()-10800:
            raise BackfillError('backfill: invalid bounded historical request')
        if 'inventory' in request:
            with gzip.open(request['inventory'], 'rt') as stream:
                data = json.load(stream)
        else:
            data = inventory(os.environ['PERIOD_PROMETHEUS_URL'], start, until)
        stage(data, start, until, request['output'], apply=args.apply, promtool=args.promtool)
        print('Private backfill staging complete; no live storage modified.')
        return 0
    except Exception:
        print('Backfill unavailable; verify private inputs/staging. Details suppressed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
