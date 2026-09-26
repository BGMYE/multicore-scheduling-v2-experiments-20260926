"""由 results_p2/summary.csv、summary_by_k.csv 生成加速比折线图（SVG）与 Markdown 表格。

    python solution_p2/make_report_p2.py
输出：results_p2/speedup_p2.svg、results_p2/report_tables.md
"""

import csv
from collections import defaultdict

from common_b import RESULTS_DIR


def read_csv(path):
    with open(path, encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def svg_chart(agg, path):
    series = [
        ('本文方法（场景 B：超图分核 + 顺序/驻留局部规划）', 'mean_speedup', '#1f5fbf'),
        ('问题一最终方案直接用于场景 B', 'mean_p1plan_speedup', '#2e9e5b'),
        ('简单贪心（DFS 拓扑序 K 等分）', 'mean_greedy_speedup', '#d98c1f'),
        ('官方 stub 随机方案', 'mean_stub_speedup', '#8a8a8a'),
    ]
    W, H, L, R, T, B = 660, 440, 60, 20, 30, 130
    ks = [int(a['cores']) for a in agg]
    ymax = max(max(float(a[key]) for a in agg for _, key, _ in series), max(ks)) * 1.1
    def X(k): return L + (k - 1) / (max(ks) - 1) * (W - L - R)
    def Y(v): return T + (1 - v / ymax) * (H - T - B)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="{}" font-family="Noto Sans CJK SC, sans-serif" font-size="12">'.format(W, H),
           '<rect width="100%" height="100%" fill="white"/>']
    for i in range(0, int(ymax) + 1):
        out.append('<line x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}" stroke="#e5e5e5"/>'.format(L, Y(i), W - R, Y(i)))
        out.append('<text x="{}" y="{:.1f}" text-anchor="end">{}</text>'.format(L - 6, Y(i) + 4, i))
    for k in ks:
        out.append('<text x="{:.1f}" y="{}" text-anchor="middle">{}</text>'.format(X(k), H - B + 18, k))
    out.append('<text x="{}" y="{}" text-anchor="middle">核数 K</text>'.format((L + W - R) / 2, H - B + 36))
    out.append('<text x="16" y="{}" transform="rotate(-90 16 {})" text-anchor="middle">平均加速比（逐用例加速比的算术平均）</text>'.format((T + H - B) / 2, (T + H - B) / 2))
    out.append('<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" stroke="#bbbbbb" stroke-dasharray="4 4"/>'.format(X(1), Y(1), X(max(ks)), Y(max(ks))))
    for idx, (name, key, color) in enumerate(series):
        pts = ' '.join('{:.1f},{:.1f}'.format(X(int(a['cores'])), Y(float(a[key]))) for a in agg)
        out.append('<polyline points="{}" fill="none" stroke="{}" stroke-width="2.2"/>'.format(pts, color))
        for a in agg:
            out.append('<circle cx="{:.1f}" cy="{:.1f}" r="3.5" fill="{}"/>'.format(X(int(a['cores'])), Y(float(a[key])), color))
            if key == 'mean_speedup':
                out.append('<text x="{:.1f}" y="{:.1f}" text-anchor="middle" fill="{}">{:.2f}</text>'.format(
                    X(int(a['cores'])), Y(float(a[key])) - 8, color, float(a[key])))
        ly = H - B + 56 + idx * 17
        out.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="2.2"/>'.format(L, ly - 4, L + 24, ly - 4, color))
        out.append('<text x="{}" y="{}">{}</text>'.format(L + 30, ly, name))
    out.append('<text x="{}" y="{}" fill="#888">虚线：理想线性加速 y=K</text>'.format(W - 190, T + 12))
    out.append('</svg>')
    path.write_text('\n'.join(out), encoding='utf-8')


def fmt(v):
    return '{:,}'.format(int(float(v))) if v not in ('', None) else '—'


def main():
    rows = read_csv(RESULTS_DIR / 'summary.csv')
    agg = read_csv(RESULTS_DIR / 'summary_by_k.csv')
    svg_chart(agg, RESULTS_DIR / 'speedup_p2.svg')
    md = ['| 核数 K | 用例数 | 本文平均加速比 | 问题一方案@场景B | 简单贪心 | stub | 平均新增搬运 (B) | 全部官方合法 |',
          '|---|---|---|---|---|---|---|---|']
    for a in agg:
        md.append('| {} | {} | {:.3f} | {:.3f} | {:.3f} | {:.3f} | {} | {} |'.format(
            a['cores'], a['n_cases'], float(a['mean_speedup']), float(a['mean_p1plan_speedup']),
            float(a['mean_greedy_speedup']), float(a['mean_stub_speedup']),
            fmt(a['mean_added_bytes']), a['all_valid']))
    md.append('')
    md.append('| 核数 K | 优于问题一方案（严格/持平） | 优于简单贪心 | 优于 stub | 优于单核 | 加速比中位数 | 最小（用例） | 最大（用例） |')
    md.append('|---|---|---|---|---|---|---|---|')
    byk = defaultdict(list)
    for r in rows:
        byk[int(r['cores'])].append(r)
    for k in sorted(byk):
        rk = [r for r in byk[k] if r['speedup']]
        sp = sorted(float(r['speedup']) for r in rk)
        lo = min(rk, key=lambda r: float(r['speedup']))
        hi = max(rk, key=lambda r: float(r['speedup']))
        mk = lambda r: int(r['makespan'])
        md.append('| {} | {}/{} | {}/{} | {}/{} | {}/{} | {:.3f} | {:.3f} ({}) | {:.3f} ({}) |'.format(
            k, sum(mk(r) < int(r['p1plan_makespan']) for r in rk),
            sum(mk(r) == int(r['p1plan_makespan']) for r in rk),
            sum(mk(r) < int(r['greedy_makespan']) for r in rk), len(rk),
            sum(mk(r) < int(r['stub_makespan']) for r in rk), len(rk),
            sum(mk(r) < int(r['singlecore_makespan']) for r in rk), len(rk),
            sp[len(sp) // 2], float(lo['speedup']), lo['case'], float(hi['speedup']), hi['case']))
    md.append('')
    md.append('| 用例 | 单核基准 | K=2 Makespan / 新增搬运 | K=3 | K=4 | K=5 | K=2..5 加速比 | 问题一方案@B K=4 | 贪心 K=4 | stub K=4 |')
    md.append('|---|---|---|---|---|---|---|---|---|---|')
    by_case = defaultdict(dict)
    for r in rows:
        by_case[r['case']][int(r['cores'])] = r
    for case in sorted(by_case):
        d = by_case[case]
        any_r = next(iter(d.values()))
        cells = ['{} / {}'.format(fmt(d[k]['makespan']), fmt(d[k]['added_copy_bytes'])) if k in d else '—'
                 for k in (2, 3, 4, 5)]
        sps = '/'.join('{:.2f}'.format(float(d[k]['speedup'])) if k in d and d[k]['speedup'] else '—'
                       for k in (2, 3, 4, 5))
        r4 = d.get(4, {})
        md.append('| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |'.format(
            case, fmt(any_r['singlecore_makespan']), *cells, sps, fmt(r4.get('p1plan_makespan', '')),
            fmt(r4.get('greedy_makespan', '')), fmt(r4.get('stub_makespan', ''))))
    (RESULTS_DIR / 'report_tables.md').write_text('\n'.join(md) + '\n', encoding='utf-8')
    print('\n'.join(md[:15]))


if __name__ == '__main__':
    main()
