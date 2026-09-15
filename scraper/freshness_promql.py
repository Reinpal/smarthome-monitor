"""Shared single-exporter freshness policy for snapshots, diagnostics and rules."""
import json


def freshness_state(source, *, selector=None, shift=0, age_only=False):
    """Return summed fields and gates at this evaluation (or an offset rule step).

    Age-only diagnostics intentionally retain stale/missing-field evidence. Value
    consumers require presence, endpoint and field ages, finite values and aligned
    export timestamps as well as unique metadata and contract-v2 provenance.
    """
    def raw(name, **attrs):
        if selector is not None:
            expression = selector(name, attrs)
        else:
            labels = ','.join(f'{key}={json.dumps(value)}' for key, value in attrs.items())
            expression = name + ('{' + labels + '}' if labels else '')
        return expression + (f' offset {shift}s' if shift else '')
    fields = {
        'value': raw(source.prom_name),
        'observed': raw('smarthome_measurement_last_success_seconds', metric=source.name),
        'present': raw('smarthome_measurement_present', metric=source.name),
        'version': raw('smarthome_measurement_contract_version'),
        'health': raw('smarthome_collection_last_success_seconds', collector=source.collector, source=source.endpoint),
        'threshold': raw('smarthome_collection_stale_after_seconds', collector=source.collector, source=source.endpoint),
    }
    values = {key: f'sum({expr})' for key, expr in fields.items()}
    checks = [f'(count({expr}) == 1)' for expr in fields.values()]
    checks += [f'({values["version"]} == 2)', f'({values["threshold"]} > 0)', f'({values["threshold"]} <= 86400)']
    now = f'time() - {shift}' if shift else 'time()'
    age = f'({now} - {values["observed"]})'
    checks.append(f'({age} >= 0)')
    if not age_only:
        checks += [f'({values["present"]} == 1)', f'({age} < {values["threshold"]})',
                   f'({now} - {values["health"]} >= 0)', f'({now} - {values["health"]} < {values["threshold"]})',
                   f'(abs({values["value"]}) < Inf)']
        checks += [f'(abs(sum(timestamp({expr})) - sum(timestamp({fields["value"]}))) <= 2)' for expr in fields.values()]
        if source.kind in ('counter', 'power', 'soc'):
            checks.append(f'({values["value"]} >= 0)')
        if source.kind == 'soc':
            checks.append(f'({values["value"]} <= 100)')
    return values, checks


def diagnostic_target(marker):
    """Resolve compact price-free diagnostic markers, preserving query mode."""
    from types import SimpleNamespace
    query = marker['diagnosticQuery']
    expression = query['expression']
    for index, source in enumerate(query['sources']):
        _, checks = freshness_state(SimpleNamespace(**source))
        # Keep diagnostic series labels (unlike summed homeowner snapshots).
        guarded = f'({source["prom_name"]}) and on() ' + ' and on() '.join(checks)
        expression = expression.replace(f'__fresh_{index}__', guarded)
    if query.get('mode') == 'cycling':
        samples = f'(({expression}))[${{__range_s}}s:60s]'
        # Count the actual epoch-aligned subquery grid, not range/60. Using
        # the same window also handles Grafana's whole-second range rounding.
        expected = 'count_over_time((vector(1))[${__range_s}s:60s])'
        expression = (f'(last_over_time({samples}) - min_over_time({samples})) '
                      f'and (resets({samples}) == 0) '
                      f'and (count_over_time({samples}) == on() {expected}) '
                      f'and (count_over_time({samples}) >= 2)')
    return {**{k: v for k, v in marker.items() if k != 'diagnosticQuery'}, 'expr': expression}
