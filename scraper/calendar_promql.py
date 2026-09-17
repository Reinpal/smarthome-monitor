"""Native, time-picker-driven calendar targets over the existing interval facts.

Grafana query variables resolve IANA-zone boundaries to numeric UTC instants.
Prometheus @ modifiers can then pin each instant integral to its own historical
window. No report, service, price record, or calendar-day recording series.
"""
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from scraper.period_promql import PeriodQueries

DATASOURCE = {'type': 'prometheus', 'uid': 'prometheus'}
# Explicit finite timezone-data horizon, NOT an assertion of telemetry history.
FIRST_YEAR, UNTIL_YEAR = 2000, 2101
DAYS = 32  # One calendar month can touch 32 dates with partial-day endpoints.
MONTHS = 12  # Bounded completed-month trend; longer selections fail closed.


@lru_cache(maxsize=32)
def zone_spans(zone):
    """Find exact TZDB offset transitions, including half-hour DST changes."""
    start = datetime(FIRST_YEAR, 1, 1, tzinfo=timezone.utc)
    stop = datetime(UNTIL_YEAR, 1, 1, tzinfo=timezone.utc)
    def offset(at):
        return int(at.astimezone(zone).utcoffset().total_seconds())
    spans, left, previous = [], start, offset(start)
    probe = start
    while probe < stop:
        following = min(probe + timedelta(hours=12), stop)
        current = offset(following)
        if current != previous:
            low, high = int(probe.timestamp()), int(following.timestamp())
            while high - low > 1:
                middle = (low + high) // 2
                if offset(datetime.fromtimestamp(middle, timezone.utc)) == previous:
                    low = middle
                else:
                    high = middle
            boundary = datetime.fromtimestamp(high, timezone.utc)
            spans.append((int(left.timestamp()), high, previous))
            left, previous = boundary, current
        probe = following
    spans.append((int(left.timestamp()), int(stop.timestamp()), previous))
    return tuple(spans)


def zone_offset(epoch, zone, *, wall=False):
    """Scalar offset; ambiguous/nonexistent wall instants and horizon fail closed.

    Group disjoint spans with the same offset, retaining distinct offset labels
    at a fold. scalar(vector) returns NaN unless exactly one candidate exists:
    it is the same uniqueness gate without duplicating the entire TZDB expression
    in sum/count for every hidden calendar variable. Equal-offset wall spans
    cannot overlap (both are translated by the same amount).
    """
    offsets = {}
    for start, end, offset in zone_spans(zone):
        if wall:
            start, end = start + offset, end + offset
        offsets.setdefault(offset, []).append(f'(vector({epoch}) >= {start} < {end})')
    pieces = [f'(label_replace(vector({offset}), "zone_offset", "{offset}", "", "") '
              f'and on() ({" or ".join(spans)}))' for offset, spans in offsets.items()]
    return 'scalar(' + ' or '.join(pieces) + ')'


def _variable(name, expression):
    query = f'query_result(vector({expression}) < Inf > -Inf)'
    return {'name': name, 'type': 'query', 'hide': 2, 'refresh': 2,
            'datasource': DATASOURCE, 'query': {'query': query, 'refId': name},
            'regex': r'/\{\}\s+(-?[0-9.]+)/',
            'current': {'text': '', 'value': ''}, 'options': [], 'skipUrlSync': True, 'multi': False,
            'includeAll': False, 'sort': 0}


def calendar_variables(installation, comparison_keys=('household',), *, monthly=False):
    """Ordered native query-variable dependency graph; re-evaluated on time change."""
    zone = installation.timezone
    variables = []
    def add(name, expr):
        variables.append(_variable('cal_' + name, expr))
    def utc(name, wall):
        add(name, f'({wall}) - ({zone_offset(wall, zone, wall=True)})')
    add('from_offset', zone_offset('(${__from}/1000)', zone))
    add('to_offset', zone_offset('(${__to}/1000)', zone))
    add('day_wall', 'scalar(floor(vector((${__from}/1000 + ${cal_from_offset}) / 86400))) * 86400')
    add('month_wall', '${cal_day_wall} - (scalar(day_of_month(vector(${cal_day_wall}))) - 1) * 86400')
    add('previous_wall', '${cal_month_wall} - scalar(days_in_month(vector(${cal_month_wall} - 86400))) * 86400')
    utc('month_start', '${cal_month_wall}')
    utc('previous_start', '${cal_previous_wall}')
    utc('month_end', '${cal_month_wall} + scalar(days_in_month(vector(${cal_month_wall}))) * 86400')
    add('compare_seconds', 'scalar(floor(clamp_max(vector(${__range_s}), ${cal_month_start} - ${cal_previous_start})))')
    add('current_end', '${cal_month_start} + ${cal_compare_seconds}')
    add('previous_end', '${cal_previous_start} + ${cal_compare_seconds}')
    add('selection_end', '${__to}/1000')
    add('observation_window', 'scalar((vector(${__range_s} + 172800) and on() '
        '(vector(${__from}/1000) == ${cal_month_start}) and on() '
        '(vector(${__to}/1000) <= ${cal_month_end})) or vector(1))')
    observed = PeriodQueries(installation, window='${cal_observation_window}s',
                             at='${cal_selection_end}', lookahead='48h')
    for key in sorted(set(comparison_keys)):
        # Cap to the actual continuous observed prefix for a live MTD window.
        # Zero here is ONLY an unavailable-window sentinel, never a telemetry
        # fallback. Targets require >0 and both complete equal-duration windows.
        add(key + '_seconds', f'scalar(floor(clamp_max(({observed.coverage[key]}) and '
                              f'({observed.metrics[key]}), ${{cal_compare_seconds}})) or vector(0))')
        add(key + '_current_end', '${cal_month_start} + ${cal_' + key + '_seconds}')
        add(key + '_previous_end', '${cal_previous_start} + ${cal_' + key + '_seconds}')
    for day in range(DAYS + 1):
        utc(f'day_{day:02}', '${cal_day_wall} + ' + str(day * 86400))
    if monthly:
        add('trend_wall_00', '${cal_month_wall}')
        utc('trend_00', '${cal_trend_wall_00}')
        for month in range(1, MONTHS + 1):
            previous = '${cal_trend_wall_%02d}' % (month - 1)
            add(f'trend_wall_{month:02}', f'{previous} + scalar(days_in_month(vector({previous}))) * 86400')
            utc(f'trend_{month:02}', '${cal_trend_wall_%02d}' % month)
        for month in range(MONTHS):
            start, end = '${cal_trend_%02d}' % month, '${cal_trend_%02d}' % (month + 1)
            # A short retrieval window for excluded buckets avoids scanning 34
            # days twelve times during ordinary day/MTD use. The target still
            # independently gates selection and coverage; 1s is not a data zero.
            add(f'trend_window_{month:02}', f'scalar((vector(2937600) and on() '
                f'(vector({start}) >= ${{__from}}/1000) and on() '
                f'(vector({end}) <= ${{__to}}/1000) and on() '
                f'(vector(${{__to}}/1000) <= ${{cal_trend_{MONTHS:02}}})) or vector(1))')
    # Latest completed operational night: local 18:00 to 06:00, not sunset/sunrise.
    add('night_wall', 'scalar(floor(vector((${__to}/1000 + ${cal_to_offset} - 21600) / 86400))) * 86400 + 21600')
    utc('night_start', '${cal_night_wall} - 43200')
    utc('night_end', '${cal_night_wall}')
    return variables


def _gate(expression, condition):
    return f'({expression}) and on() ({condition})'


def _target(expression, marker, legend):
    return {'refId': marker.get('refId', 'A'), 'expr': expression, 'instant': True,
            'range': False, 'editorMode': 'code', 'format': 'time_series',
            'legendFormat': marker.get('legendFormat', legend), 'datasource': DATASOURCE}


class CalendarQueries:
    def __init__(self, installation):
        self.installation = installation
        self._windows = {}

    def window(self, start, end, duration):
        # A closing source observation may be recorded AFTER local midnight.
        # Looking only @ midnight would withhold essentially every jittered day.
        # Allow bracketing facts to arrive, but clip all energy to the exact window.
        # 48h covers the existing maximum 24h source threshold plus receipt delay;
        # it does not bridge a rejected source gap or extrapolate missing tails.
        identity = start, end, duration
        if identity not in self._windows:
            self._windows[identity] = PeriodQueries(self.installation, start=start, end=end,
                                                   window=duration, at=end, lookahead='48h')
        return self._windows[identity]

    def comparison(self, marker):
        key, side = marker['calendarMetric'], marker['calendarMode']
        seconds = '${cal_' + key + '_seconds}'
        current = self.window('${cal_month_start}', '${cal_' + key + '_current_end}', '48h' + seconds + 's')
        previous = self.window('${cal_previous_start}', '${cal_' + key + '_previous_end}', '48h' + seconds + 's')
        # Comparison values require BOTH complete equal-duration windows. The main
        # headline is independent and is never shortened to fit the prior month.
        condition = (f'(vector({seconds}) > 0) and (vector(${{__from}}/1000) == ${{cal_month_start}}) and '
                     f'(vector(${{__to}}/1000) <= ${{cal_month_end}}) and '
                     f'({current.complete[key]} == 1) and ({previous.complete[key]} == 1) and '
                     f'({current.metrics[key]}) and ({previous.metrics[key]})')
        if side == 'change':
            expr = f'(({current.metrics[key]}) - ({previous.metrics[key]})) / ({previous.metrics[key]}) * 100'
            condition += f' and (({previous.metrics[key]}) > 0)'
        else:
            queries = current if side == 'current' else previous
            expr = getattr(queries, marker.get('periodField', 'metrics'))[key]
            if marker.get('periodField') == 'observed_until':
                expr = f'({expr}) * 1000'
        return _target(_gate(expr, condition), marker, side + ' ' + key)

    def daily(self, marker):
        key = marker['calendarMetric']
        field = marker.get('periodField', 'metrics')
        if field not in ('metrics', 'missing_seconds'):
            raise ValueError('calendar: unsupported daily field')
        terms = []
        for day in range(DAYS):
            start, end = '${cal_day_%02d}' % day, '${cal_day_%02d}' % (day + 1)
            q = self.window(start, end, '74h')
            condition = (f'(vector({start}) >= ${{__from}}/1000) '
                         f'and (vector({end}) <= ${{__to}}/1000) and (vector({end}) <= time()) '
                         f'and (vector(${{__to}}/1000) <= ${{cal_day_32}})')
            if field == 'missing_seconds':
                # Diagnostic only: absent coverage means the entire date is
                # missing, NOT zero energy. Keep incomplete dates visible while
                # leaving all energy/ratio acceptance gates unchanged.
                expression = f'clamp_min(vector({end} - {start}) - (({q.coverage[key]}) or on() vector(0)), 0)'
            else:
                # Reuse the compact full-source gate where available, rather
                # than computing both prefix acceptance and full coverage.
                expression = q.completed_sources.get(key)
                if expression is None:
                    expression = q.metrics[key]
                    condition = f'({q.complete[key]} == 1) and ({condition})'
            value = _gate(expression, condition)
            # Day START is a categorical timestamp, not the instant query's time.
            terms.append(f'label_replace(label_replace(({value}), "day", "{start}", "", ""), '
                         f'"quantity", "{key}", "", "")')
        return _target(' or '.join(terms), marker, '{{quantity}}')

    def monthly(self, marker):
        """Full local calendar months only, with the same interval coverage gates.

        Month arithmetic is on wall dates; each boundary resolves independently
        through TZDB. A 34-day retrieval window includes the longest month plus
        the existing 48h closing-observation allowance, never gap interpolation.
        """
        key, month = marker['calendarMetric'], marker['calendarMonth']
        start, end = '${cal_trend_%02d}' % month, '${cal_trend_%02d}' % (month + 1)
        q = self.window(start, end, '${cal_trend_window_%02d}s' % month)
        condition = (f'(vector({start}) >= ${{__from}}/1000) '
                     f'and (vector({end}) <= ${{__to}}/1000) and (vector({end}) <= time()) '
                     f'and (vector(${{__to}}/1000) <= ${{cal_trend_{MONTHS:02}}})')
        value = _gate(q.completed_sources[key], condition)
        expression = (f'label_replace(label_replace(({value}), "month", "{start}", "", ""), '
                      f'"quantity", "{key}", "", "")')
        return _target(expression, marker, '{{quantity}}')

    def overnight(self, marker):
        q = self.window('${cal_night_start}', '${cal_night_end}', '74h')
        keys = ('household', 'grid_import', 'pv', 'battery_charge', 'battery_discharge', 'battery_inventory_change')
        condition = ' and '.join(f'({q.complete[k]} == 1) and ({q.metrics[k]})' for k in keys)
        condition += (f' and (({q.metrics["pv"]}) == 0) and (({q.metrics["battery_charge"]}) == 0) '
                      f'and (({q.metrics["battery_inventory_change"]}) <= 0) '
                      'and (vector(${cal_night_start}) >= ${__from}/1000) '
                      'and (vector(${cal_night_end}) <= ${__to}/1000)')
        key = marker['calendarMetric']
        if key in ('night_start', 'night_end'):
            expr = 'vector(${cal_' + key + '} * 1000)'
        else:
            expr = q.metrics['self_sufficiency' if key == 'overnight_coverage' else key]
        if self.installation.usable_battery_kwh == 0:
            condition += ' and (vector(0) == 1)'
        return _target(_gate(expr, condition), marker, key)

    def targets(self, marker):
        # Separate months keep individual native requests bounded. Grafana merges
        # their categorical frames; there is no giant all-year PromQL request.
        if marker['calendarMode'] == 'monthly':
            return [self.monthly({**marker, 'calendarMonth': month,
                                  'refId': marker.get('refId', 'A') + str(month)})
                    for month in range(MONTHS)]
        return [self.target(marker)]

    def target(self, marker):
        mode = marker['calendarMode']
        if mode == 'daily':
            return self.daily(marker)
        if mode == 'overnight':
            return self.overnight(marker)
        if mode in ('current', 'previous', 'change'):
            return self.comparison(marker)
        raise ValueError('calendar: unsupported target mode')
