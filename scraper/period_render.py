"""Native Grafana text panels for fixed-period calculation reports (interface v1).

No plugin, datasource, JavaScript, or additional service. The time picker is
hidden: these are explicit query results, not dynamically recalculated targets.
"""
import html

GROUPS = {
    "Site energy": ("household", "pv", "grid_import", "grid_export", "self_sufficiency", "solar_self_consumption", "specific_yield"),
    "Battery boundary": ("battery_charge", "battery_discharge", "battery_inventory_change"),
    "Financial estimates": ("import_cost", "export_revenue", "avoided_cost", "solar_battery_benefit", "net_grid_cost"),
    "Heating and hot water": ("heating_electricity", "water_electricity", "heatpump_electricity", "heating_heat", "water_heat", "heatpump_heat", "heating_ratio", "water_ratio", "heatpump_ratio", "heating_aux_heat", "water_aux_heat", "heatpump_aux_heat", "heating_reference_cost", "water_reference_cost", "heatpump_reference_cost"),
}


def _safe(value):
    return html.escape(str(value)).replace('|', '&#124;').replace('\n', ' ')


def _value(result):
    return "Unavailable" if result['value'] is None else f"{result['value']:.4f} {_safe(result['unit'])}"


def render_panel(report, keys, *, panel_id, title, grid_pos=None):
    """Reusable fixed-period panel. Keys are stable report.metrics identifiers.

    Keep the period/confidence text when embedding in #5/#6 dashboards. Do not
    present this panel as responding to a dashboard time picker or auto-refresh.
    """
    if report['version'] != 1:
        raise ValueError('report: unsupported version')
    content = [f"**Fixed calculation period:** {_safe(report['start'])} → {_safe(report['end'])} (end exclusive)",
               f"Elapsed: {report['seconds']:g} s · Zone: {_safe(report['timezone'])}",
               "**Not live.** Time-picker changes do not recalculate. Rerun the period-report command after history/tariff corrections.",
               "| Metric | Result | Data status | Covered / requested seconds | Largest gap (s) | Price confidence |",
               "|---|---:|---|---:|---:|---|"]
    for key in keys:
        r = report['metrics'][key]
        content.append(f"| {_safe(key)} | {_value(r)} | {_safe(r['status'])} | {r['covered_seconds']:g} / {report['seconds']:g} | {r['largest_gap_seconds']:g} | {_safe(r['tariff_status'])} |")
    for key in keys:
        r = report['metrics'][key]
        content.append(f"\n**{_safe(key)}** — {_safe(r['boundary'])}. " + '; '.join(_safe(n) for n in r['reasons']))
        if r['value'] is None and r['observed_value'] is not None:
            content.append(f"Observed subtotal only: {r['observed_value']:.4f} {_safe(r['unit'])}; not a full-period total.")
    return _text(panel_id, title, '\n\n'.join(content[:3]) + '\n\n' + '\n'.join(content[3:]), grid_pos)


def _text(panel_id, title, content, grid_pos):
    return dict(id=panel_id, type='text', title=title,
                gridPos=grid_pos or dict(x=0, y=0, w=24, h=16),
                options=dict(mode='markdown', content=content))


def render_dashboard(report):
    panels = []
    y = 0
    for index, (title, keys) in enumerate(GROUPS.items(), 1):
        height = 12 + 2 * len(keys)
        panels.append(render_panel(report, keys, panel_id=index, title=title,
                                   grid_pos=dict(x=0, y=y, w=24, h=height)))
        y += height
    daily = ['Local day (partial endpoints respected) | Elapsed seconds | Household kWh | PV kWh | Import kWh | Export kWh | Import cost | Export revenue | Avoided cost',
             '---|---:|---:|---:|---:|---:|---:|---:|---:']
    from datetime import datetime
    from zoneinfo import ZoneInfo
    for day in report['daily']:
        local = datetime.fromisoformat(day['start']).astimezone(ZoneInfo(report['timezone'])).date()
        daily.append(f"{local} | {day['seconds']:g} | " + ' | '.join(_value(day['metrics'][key]) for key in (
            'household', 'pv', 'grid_import', 'grid_export', 'import_cost', 'export_revenue', 'avoided_cost')))
    daily.append('\nMoney is estimated; headline price confidence applies to this table. Missing is never zero.')
    panels.append(_text(5, 'Daily calendar / DST allocation', '\n'.join(daily), dict(x=0, y=y, w=24, h=18)))
    y += 18
    comparison = report['comparison']
    content = [comparison['reason'], '', report['year_over_year']['reason']]
    if comparison['status'] == 'equal_elapsed_windows':
        for name in ('current', 'previous'):
            window = comparison[name]
            content.append(f"\n**{name}**: {window['start']} → {window['end']} · {window['seconds']:g} elapsed seconds")
            for key in ('household', 'grid_import', 'self_sufficiency', 'solar_battery_benefit', 'heatpump_electricity', 'heatpump_ratio'):
                r = window['metrics'][key]
                content.append(f"- {key}: {_value(r)}; {r['status']}; {r['tariff_status']}; coverage {r['covered_seconds']:g}/{window['seconds']:g} s")
    content.extend(['', 'Heating comparisons are not weather-normalized or causal evidence.',
                    'Generated: ' + report['generated_at'], *report['limitations']])
    panels.append(_text(6, 'Equivalent elapsed comparison / limitations', '\n'.join(content), dict(x=0, y=y, w=24, h=22)))
    return dict(uid='period-verification', title='Period calculations — fixed report', schemaVersion=39,
                version=1, editable=False, tags=['energy', 'verification', 'fixed-period'],
                timezone=report['timezone'], refresh='', timepicker={'hidden': True},
                time={'from': report['start'], 'to': report['end']}, panels=panels,
                description='Read-only Prometheus history → calculation v1 → native Grafana. Explicit rerender required; not live.')
