#!/usr/bin/env python3
"""Compare analyzed RTK test runs: XRCE (DDS corrections) against the MAVROS baseline.

    python3 rtk_test/compare_runs.py logs/rtk_runs/* --out logs/rtk_runs/comparison

Reads each run's analysis/summary.json (run analyze_run.py first) and writes
comparison.md (for the GitHub issue), comparison.html and a chart to --out.
Relative criteria come from criteria.json: XRCE's median time to RTK fixed and
its fixed share must be within the set margins of MAVROS's. Runs should be
interleaved (XRCE, MAVROS, XRCE, ...) at the same place and mountpoint, so sky
and time-of-day effects are shared.
"""
import argparse
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def first_ttff(r):
    xs = [x for x in r.get('time_to_rtk_fixed', []) if x.get('note') != 'already RTK fixed']
    return xs[0]['ttff_s'] if xs else None


def recovery_ttffs(r):
    xs = [x['ttff_s'] for x in r.get('time_to_rtk_fixed', [])[1:] if x.get('note') != 'already RTK fixed']
    return [x for x in xs if x is not None]


def row(summary):
    fix = summary.get('fix') or {}
    receivers = fix.get('receivers') or {}
    # The receiver that reached RTK fixed (the rover); else the first one
    rid = next((k for k, r in receivers.items() if r.get('fixed_fraction_after_first_fix')), next(iter(receivers), None))
    r = receivers.get(rid, {})
    lost = sum(c['missing'] for c in summary['comparisons'].values())
    return {
        'run': summary['run'], 'mode': summary['mode'], 'label': summary.get('label'),
        'start': summary.get('start_utc'), 'receiver': rid,
        'ttff_s': first_ttff(r), 'recovery_ttff_s': recovery_ttffs(r),
        'fixed_share': r.get('fixed_fraction_with_corrections_flowing'),
        'unexpected_drops': r.get('unexpected_drops'),
        'lost_items': lost,
        'latency_p99_ms': (summary.get('latency_ms') or {}).get('p99'),
        'fails': sum(1 for v in summary['verdicts'] if v['result'] == 'FAIL'),
        'incomplete': sum(1 for v in summary['verdicts'] if v['result'] == 'INCOMPLETE'),
        'px4': (summary.get('px4') or {}).get('ver_sw'),
    }


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def fmt(v, nd=1):
    if v is None:
        return '-'
    if isinstance(v, float):
        return f'{v:.{nd}f}'
    return str(v)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('runs', nargs='+')
    p.add_argument('--out', required=True)
    p.add_argument('--criteria', default=os.path.join(HERE, 'criteria.json'))
    a = p.parse_args()
    c = json.load(open(a.criteria))
    rows = []
    for run in a.runs:
        path = os.path.join(run, 'analysis', 'summary.json')
        if os.path.exists(path):
            rows.append(row(json.load(open(path))))
        elif os.path.isdir(run):
            print(f'skipping {run}: no analysis/summary.json (run analyze_run.py)', file=sys.stderr)
    if not rows:
        raise SystemExit('No analyzed runs')
    rows.sort(key=lambda r: r['start'] or '')
    os.makedirs(a.out, exist_ok=True)

    groups = {m: [r for r in rows if r['mode'] == m] for m in ('xrce', 'mavros')}
    agg = {m: {'runs': len(g), 'ttff_median_s': med([r['ttff_s'] for r in g]),
               'ttff_values': [r['ttff_s'] for r in g],
               'fixed_share_median': med([r['fixed_share'] for r in g]),
               'unexpected_drops_total': sum(r['unexpected_drops'] or 0 for r in g),
               'lost_items_total': sum(r['lost_items'] for r in g),
               'runs_with_fail': sum(1 for r in g if r['fails']),
               'runs_incomplete': sum(1 for r in g if r['incomplete'])} for m, g in groups.items()}

    checks = []
    x, b = agg['xrce'], agg['mavros']
    if x['runs'] and b['runs']:
        if x['ttff_median_s'] is not None and b['ttff_median_s'] is not None:
            d = x['ttff_median_s'] - b['ttff_median_s']
            checks.append(('XRCE median time to RTK fixed minus MAVROS (s)', d <= c['ttff_vs_mavros_median_max_diff_s'],
                           round(d, 1), f'<= {c["ttff_vs_mavros_median_max_diff_s"]}'))
        if x['fixed_share_median'] is not None and b['fixed_share_median'] is not None:
            d = x['fixed_share_median'] - b['fixed_share_median']
            checks.append(('XRCE median RTK fixed share minus MAVROS', d >= c['fixed_fraction_vs_mavros_min_diff'],
                           round(d, 4), f'>= {c["fixed_fraction_vs_mavros_min_diff"]}'))
    checks.append(('XRCE runs with any FAIL', x['runs_with_fail'] == 0, x['runs_with_fail'], '0'))
    checks.append(('XRCE items lost at any hop, all runs', x['lost_items_total'] == 0, x['lost_items_total'], '0'))
    if min(x['runs'], b['runs']) < 3:
        checks.append(('At least 3 runs of each mode', False, f'{x["runs"]} XRCE / {b["runs"]} MAVROS', '>= 3 each'))

    L = ['## RTK corrections: XRCE (uXRCE-DDS) vs MAVROS\n']
    pxs = sorted({r['px4'] for r in rows if r['px4']})
    L.append(f'PX4 builds: {", ".join(f"`{v}`" for v in pxs) or "unknown"}\n')
    L.append('### Verdicts\n\n| Check | Result | Value | Limit |\n|---|---|---|---|')
    for name, ok, v, lim in checks:
        L.append(f'| {name} | **{"PASS" if ok else "FAIL"}** | {v} | {lim} |')
    L.append('\n### By mode\n\n| Mode | Runs | Median time to RTK fixed (s) | Median fixed share | '
             'Unexpected drops | Items lost | Runs with a FAIL | Runs incomplete |\n|---|---|---|---|---|---|---|---|')
    for m in ('xrce', 'mavros'):
        g = agg[m]
        L.append(f'| {m.upper()} | {g["runs"]} | {fmt(g["ttff_median_s"])} | {fmt(g["fixed_share_median"], 4)} | '
                 f'{g["unexpected_drops_total"]} | {g["lost_items_total"]} | {g["runs_with_fail"]} | '
                 f'{g["runs_incomplete"]} |')
    L.append('\n### Runs, in order\n\n| Run | Mode | Time to RTK fixed (s) | Recovery after outages (s) | Fixed share | '
             'Unexpected drops | Items lost | p99 latency (ms) | FAIL / INCOMPLETE |\n|---|---|---|---|---|---|---|---|---|')
    for r in rows:
        L.append(f'| {r["run"]} | {r["mode"].upper()} | {fmt(r["ttff_s"])} | '
                 f'{", ".join(fmt(v) for v in r["recovery_ttff_s"]) or "-"} | {fmt(r["fixed_share"], 4)} | '
                 f'{fmt(r["unexpected_drops"])} | {r["lost_items"]} | {fmt(r["latency_p99_ms"])} | '
                 f'{r["fails"]} / {r["incomplete"]} |')

    chart = None
    vals = [(m, v) for m in ('xrce', 'mavros') for v in agg[m]['ttff_values'] if v is not None]
    if vals:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        surface, ink, ink2, grid = '#fcfcfb', '#0b0b0b', '#52514e', '#e4e3df'
        colors = {'xrce': '#2a78d6', 'mavros': '#eb6834'}
        plt.rcParams.update({'figure.facecolor': surface, 'axes.facecolor': surface, 'axes.edgecolor': grid,
                             'axes.labelcolor': ink2, 'xtick.color': ink2, 'ytick.color': ink2,
                             'axes.grid': True, 'grid.color': grid, 'axes.spines.top': False,
                             'axes.spines.right': False})
        fig, ax = plt.subplots(figsize=(7, 3))
        for i, m in enumerate(('xrce', 'mavros')):
            ys = [v for mm, v in vals if mm == m]
            ax.scatter(ys, [i] * len(ys), s=64, color=colors[m], edgecolors=surface, linewidths=2, zorder=3)
            if ys:
                ax.plot([statistics.median(ys)] * 2, [i - 0.25, i + 0.25], color=ink, lw=2)
        ax.set_yticks([0, 1])
        ax.set_yticklabels(['XRCE', 'MAVROS'])
        ax.set_ylim(-0.6, 1.6)
        ax.set_xlabel('time to RTK fixed after corrections start (s); bar = median')
        ax.set_title('Time to RTK fixed, each run', loc='left', color=ink, fontsize=11)
        fig.tight_layout()
        chart = os.path.join(a.out, 'ttff.png')
        fig.savefig(chart, dpi=130)
        plt.close(fig)
        L.append('\n![Time to RTK fixed, each run](ttff.png)')

    md = '\n'.join(L) + '\n'
    open(os.path.join(a.out, 'comparison.md'), 'w').write(md)
    json.dump({'aggregate': agg, 'runs': rows, 'checks': checks}, open(os.path.join(a.out, 'comparison.json'), 'w'),
              indent=2)
    sys.path.insert(0, HERE)
    from analyze_run import html_report
    files = [('ttff', chart)] if chart else []
    open(os.path.join(a.out, 'comparison.html'), 'w').write(html_report(md, files, 'RTK XRCE vs MAVROS'))
    print(md)
    return 0 if all(ok for _, ok, _, _ in checks) else 1


if __name__ == '__main__':
    sys.exit(main())
