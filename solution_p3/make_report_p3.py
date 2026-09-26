"""问题三图表与表格：results_p3/speedup_p3.svg、pairing_p3.svg、report_tables.md。

    python solution_p3/make_report_p3.py
"""

import csv
from collections import defaultdict

from common_c import RESULTS_DIR


def read_csv(path):
    with open(path, encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def line_chart(path, xs, series, ylabel, ideal=False, ymin=0.0, label_key=None):
    W, H, L, R, T, B = 660, 440, 64, 20, 30, 120
    vals = [v for _, pts, _ in series for v in pts if v is not None]
    ymax = max(vals + ([max(xs)] if ideal else [])) * 1.08
    ymin = min([ymin] + vals) if ymin is not None else min(vals) * 0.98
    def X(k): return L + (k - min(xs)) / (max(xs) - min(xs)) * (W - L - R)
    def Y(v): return T + (1 - (v - ymin) / (ymax - ymin)) * (H - T - B)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="{}" font-family="Noto Sans CJK SC, sans-serif" font-size="12">'.format(W, H),
           '<rect width="100%" height="100%" fill="white"/>']
    steps = 6
    for i in range(steps + 1):
        v = ymin + (ymax - ymin) * i / steps
        out.append('<line x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}" stroke="#e5e5e5"/>'.format(L, Y(v), W - R, Y(v)))
        out.append('<text x="{}" y="{:.1f}" text-anchor="end">{:.2f}</text>'.format(L - 6, Y(v) + 4, v))
    for k in xs:
        out.append('<text x="{:.1f}" y="{}" text-anchor="middle">{}</text>'.format(X(k), H - B + 18, k))
    out.append('<text x="{}" y="{}" text-anchor="middle">核数 K</text>'.format((L + W - R) / 2, H - B + 36))
    out.append('<text x="16" y="{0}" transform="rotate(-90 16 {0})" text-anchor="middle">{1}</text>'.format((T + H - B) / 2, ylabel))
    if ideal:
        out.append('<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" stroke="#bbbbbb" stroke-dasharray="4 4"/>'.format(
            X(min(xs)), Y(min(xs)), X(max(xs)), Y(max(xs))))
    for idx, (name, pts, color) in enumerate(series):
        xy = [(X(k), Y(v)) for k, v in zip(xs, pts) if v is not None]
        out.append('<polyline points="{}" fill="none" stroke="{}" stroke-width="2.2"/>'.format(
            ' '.join('{:.1f},{:.1f}'.format(*p) for p in xy), color))
        for (x, y), v in zip(xy, [v for v in pts if v is not None]):
            out.append('<circle cx="{:.1f}" cy="{:.1f}" r="3.5" fill="{}"/>'.format(x, y, color))
            if idx < 2:
                out.append('<text x="{:.1f}" y="{:.1f}" text-anchor="middle" fill="{}">{:.3f}</text>'.format(
                    x, y - 8 - 12 * idx, color, v))
        ly = H - B + 56 + idx * 16
        out.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="2.2"/>'.format(L, ly - 4, L + 24, ly - 4, color))
        out.append('<text x="{}" y="{}">{}</text>'.format(L + 30, ly, name))
    out.append('</svg>')
    path.write_text('\n'.join(out), encoding='utf-8')


def f(v, d=3):
    return '{:.{}f}'.format(float(v), d) if v not in ('', None) else '—'


def n(v):
    return '{:,}'.format(int(float(v))) if v not in ('', None) else '—'


def main():
    rows = read_csv(RESULTS_DIR / 'summary.csv')
    agg = read_csv(RESULTS_DIR / 'summary_by_k.csv')
    xs = [int(a['cores']) for a in agg]
    g = lambda key: [float(a[key]) if a[key] not in ('', None) else None for a in agg]
    line_chart(RESULTS_DIR / 'speedup_p3.svg', xs, [
        ('只读 Cache：问题三方案 P_C（T_single / T_C(P_C)）', g('mean_speedup_cache'), '#1f5fbf'),
        ('无 L2：问题二方案 P_B（T_single / T_B(P_B)）', g('mean_speedup_noL2'), '#c0392b'),
        ('只读 Cache：简单贪心', g('mean_greedy_speedup_cache'), '#d98c1f'),
        ('只读 Cache：官方 stub', g('mean_stub_speedup_cache'), '#8a8a8a'),
    ], '平均加速比（分母：无 L2 单核基准）', ideal=True)
    line_chart(RESULTS_DIR / 'pairing_p3.svg', xs, [
        ('S_overall = T_B(P_B)/T_C(P_C)', g('mean_S_overall'), '#1f5fbf'),
        ('S_hardware = T_B(P_B)/T_C(P_B)', g('mean_S_hardware'), '#2e9e5b'),
        ('S_schedule = T_C(P_B)/T_C(P_C)', g('mean_S_schedule'), '#d98c1f'),
    ], '只读 Cache 相对无 L2 的平均加速比', ymin=None)
    md = ['| 核数 K | 用例数 | 无 L2 平均加速比（P_B） | 只读 Cache 平均加速比（P_C） | S_hardware | S_schedule | S_overall | 命中率 P_B | 命中率 P_C | 贪心@Cache | stub@Cache | 问题一方案@Cache | 全部合法 |',
          '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for a in agg:
        md.append('| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |'.format(
            a['cores'], a['n_cases'], f(a['mean_speedup_noL2']), f(a['mean_speedup_cache']),
            f(a['mean_S_hardware'], 4), f(a['mean_S_schedule'], 4), f(a['mean_S_overall'], 4),
            f(a['mean_hit_rate_PB']), f(a['mean_hit_rate_PC']), f(a['mean_greedy_speedup_cache']),
            f(a['mean_stub_speedup_cache']), f(a['mean_p1plan_speedup_cache']), a['all_valid']))
    md.append('')
    # 四格配对：按 K 汇总计数
    md.append('| 核数 K | T_C(P_B) < T_B(P_B)（Cache 有益） | T_C(P_C) < T_C(P_B)（问题三计划更优） | T_C(P_C) = T_C(P_B) | T_B(P_C) > T_B(P_B)（缓存专用计划在无 L2 下退化） | ΣT_B(P_B) | ΣT_C(P_B) | ΣT_B(P_C) | ΣT_C(P_C) |')
    md.append('|---|---|---|---|---|---|---|---|---|')
    byk = defaultdict(list)
    for r in rows:
        byk[int(r['cores'])].append(r)
    for k in sorted(byk):
        rk = byk[k]
        I = lambda r, key: int(r[key])
        md.append('| {} | {}/{} | {}/{} | {} | {} | {} | {} | {} | {} |'.format(
            k, sum(I(r, 'T_C_PB') < I(r, 'T_B_PB') for r in rk), len(rk),
            sum(I(r, 'T_C_PC') < I(r, 'T_C_PB') for r in rk), len(rk),
            sum(I(r, 'T_C_PC') == I(r, 'T_C_PB') for r in rk),
            sum(I(r, 'T_B_PC') > I(r, 'T_B_PB') for r in rk),
            n(sum(I(r, 'T_B_PB') for r in rk)), n(sum(I(r, 'T_C_PB') for r in rk)),
            n(sum(I(r, 'T_B_PC') for r in rk)), n(sum(I(r, 'T_C_PC') for r in rk))))
    md.append('')
    md.append('| 用例 | 单核（无 L2 / Cache） | K=2 T_C(P_C) / 命中率 | K=3 | K=4 | K=5 | K=2..5 S_overall | K=4 四格 T_B(P_B) / T_C(P_B) / T_B(P_C) / T_C(P_C) |')
    md.append('|---|---|---|---|---|---|---|---|')
    by_case = defaultdict(dict)
    for r in rows:
        by_case[r['case']][int(r['cores'])] = r
    for case in sorted(by_case):
        d = by_case[case]
        a = next(iter(d.values()))
        cells = ['{} / {}'.format(n(d[k]['T_C_PC']), f(d[k]['hit_rate_PC'])) if k in d else '—' for k in (2, 3, 4, 5)]
        so = '/'.join(f(d[k]['S_overall']) if k in d else '—' for k in (2, 3, 4, 5))
        r4 = d.get(4)
        four = '{} / {} / {} / {}'.format(n(r4['T_B_PB']), n(r4['T_C_PB']), n(r4['T_B_PC']), n(r4['T_C_PC'])) if r4 else '—'
        md.append('| {} | {} / {} | {} | {} | {} | {} | {} | {} |'.format(
            case, n(a['singlecore_noL2']), n(a['singlecore_cache']), *cells, so, four))
    (RESULTS_DIR / 'report_tables.md').write_text('\n'.join(md) + '\n', encoding='utf-8')
    print('\n'.join(md[:14]))


if __name__ == '__main__':
    main()
