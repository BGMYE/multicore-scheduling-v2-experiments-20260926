"""问题三图表与表格（全部从 results_p3/summary.csv 重新计算，并与 summary_by_k.csv 交叉核对）。

    .venv/bin/python solution_p3/make_report_p3.py

输出（SVG + 300 dpi PNG，中文字体 Noto Sans CJK）：
  results_p3/speedup_p3.{svg,png}  1~5 核平均加速比：无 L2（P_B）/ 只读 Cache（P_C）及基线
  results_p3/pairing_p3.{svg,png}  配对对照：P_C 相对 P_B 的逐算例提升 + S_hardware/S_schedule/S_overall
  results_p3/hitrate_p3.{svg,png}  Cache 命中率：P_B 与 P_C 按核数的均值，以及逐算例命中率与 S_hardware
  results_p3/report_tables.md       README 引用的全部表格
颜色与问题一、二图一致：本文方法蓝 #1f5fbf、问题一方案绿 #2e9e5b、贪心橙 #d98c1f、stub 灰 #8a8a8a。
"""

import csv
import json
import statistics as st
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from common_c import RESULTS_DIR, RESULTS_P1  # noqa: E402

FONT = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
try:
    font_manager.fontManager.addfont(FONT)
    plt.rcParams['font.family'] = font_manager.FontProperties(fname=FONT).get_name()
except (OSError, RuntimeError):
    pass
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['svg.fonttype'] = 'none'

BLUE, RED, GREEN, ORANGE, GRAY = '#1f5fbf', '#c0392b', '#2e9e5b', '#d98c1f', '#8a8a8a'
CORES = (2, 3, 4, 5)


def read_csv(path):
    with open(path, encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def mean(vals):
    vals = [float(v) for v in vals if v not in ('', None)]
    return sum(vals) / len(vals) if vals else float('nan')


def aggregate(rows, cases):
    """从逐例表重新计算按核数的均值（与 run_all_p3.summarize 同口径）。"""
    agg = {}
    s1 = []
    for c in cases:
        a = json.load(open(RESULTS_P1 / 'baselines' / 'singlecore' / (c + '.json'), encoding='utf-8'))
        b = json.load(open(RESULTS_DIR / 'baselines' / 'single_k1' / (c + '.json'), encoding='utf-8'))
        s1.append(a['makespan'] / b['makespan'])
    agg[1] = {'noL2': 1.0, 'cache': mean(s1), 'S_hardware': mean(s1), 'S_schedule': 1.0,
              'S_overall': mean(s1), 'hit_PB': float('nan'), 'hit_PC': float('nan'),
              'stub': float('nan'), 'greedy': float('nan'), 'p1plan': float('nan'), 'n': len(s1)}
    for k in CORES:
        rk = [r for r in rows if int(r['cores']) == k]
        agg[k] = {'noL2': mean(r['speedup_noL2'] for r in rk), 'cache': mean(r['speedup_cache'] for r in rk),
                  'S_hardware': mean(r['S_hardware'] for r in rk), 'S_schedule': mean(r['S_schedule'] for r in rk),
                  'S_overall': mean(r['S_overall'] for r in rk), 'hit_PB': mean(r['hit_rate_PB'] for r in rk),
                  'hit_PC': mean(r['hit_rate_PC'] for r in rk), 'stub': mean(r['stub_speedup_cache'] for r in rk),
                  'greedy': mean(r['greedy_speedup_cache'] for r in rk),
                  'p1plan': mean(r['p1plan_speedup_cache'] for r in rk), 'n': len(rk),
                  'valid': all(r['official_valid'] == 'True' for r in rk)}
    return agg


def cross_check(agg):
    by_k = {int(a['cores']): a for a in read_csv(RESULTS_DIR / 'summary_by_k.csv')}
    pairs = [('noL2', 'mean_speedup_noL2'), ('cache', 'mean_speedup_cache'), ('S_hardware', 'mean_S_hardware'),
             ('S_schedule', 'mean_S_schedule'), ('S_overall', 'mean_S_overall')]
    for k, a in agg.items():
        for mine, col in pairs:
            ref = float(by_k[k][col])
            assert abs(round(a[mine], 4) - ref) < 1.5e-4, (k, mine, a[mine], ref)
    return True


def save(fig, stem):
    fig.savefig(RESULTS_DIR / (stem + '.svg'), bbox_inches='tight')
    fig.savefig(RESULTS_DIR / (stem + '.png'), dpi=300, bbox_inches='tight')
    plt.close(fig)


def chart_speedup(agg):
    ks = sorted(agg)
    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    ax.plot(ks, ks, '--', color='#bbbbbb', lw=1.2, label='理想线性加速 y=K')
    series = [('只读 Cache：问题三方案 P_C', 'cache', BLUE, 'o'),
              ('无 L2：问题二方案 P_B', 'noL2', RED, 's'),
              ('只读 Cache：问题一方案', 'p1plan', GREEN, '^'),
              ('只读 Cache：简单贪心', 'greedy', ORANGE, 'D'),
              ('只读 Cache：官方 stub', 'stub', GRAY, 'v')]
    for name, key, color, mk in series:
        xs = [k for k in ks if agg[k][key] == agg[k][key]]
        ys = [agg[k][key] for k in xs]
        if key in ('stub', 'greedy', 'p1plan'):
            xs, ys = [1] + xs, [1.0] + ys          # K=1 统一为单核基准（加速比 1）
        ax.plot(xs, ys, marker=mk, color=color, lw=2.0, ms=5, label=name)
        if key in ('cache', 'noL2'):
            for x, y in zip(xs, ys):
                ax.annotate('{:.3f}'.format(y), (x, y), textcoords='offset points',
                            xytext=(0, 8 if key == 'cache' else -15), ha='center', fontsize=8, color=color)
    ax.set_xlabel('核数 K')
    ax.set_ylabel('平均加速比（逐算例加速比的算术平均；分母为无 L2 单核基准）')
    ax.set_title('问题三：无 L2 与只读 L2 Cache 的 1~5 核平均加速比')
    ax.set_xticks(ks)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc='upper left')
    save(fig, 'speedup_p3')


def chart_pairing(rows, agg):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.6), gridspec_kw={'width_ratios': [1.35, 1]})
    colors = {2: '#9ecae1', 3: '#6baed6', 4: '#3182bd', 5: '#08519c'}
    for k in CORES:
        vals = sorted(((float(r['S_schedule']) - 1) * 100 for r in rows if int(r['cores']) == k), reverse=True)
        ax1.plot(range(1, len(vals) + 1), vals, color=colors[k], lw=1.8, label='K={}'.format(k))
    ax1.axhline(0, color='#888888', lw=0.8)
    ax1.set_xlabel('算例（按提升幅度降序排列）')
    ax1.set_ylabel('T_C(P_B) / T_C(P_C) − 1（%）')
    ax1.set_title('同一 Cache 配置下 P_C 相对 P_B 的逐算例提升')
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)
    width = 0.26
    names = [('S_hardware', 'S_hardware = T_B(P_B)/T_C(P_B)', GREEN),
             ('S_schedule', 'S_schedule = T_C(P_B)/T_C(P_C)', ORANGE),
             ('S_overall', 'S_overall = T_B(P_B)/T_C(P_C)', BLUE)]
    for i, (key, label, color) in enumerate(names):
        xs = [k + (i - 1) * width for k in CORES]
        ys = [agg[k][key] for k in CORES]
        ax2.bar(xs, ys, width=width, color=color, label=label)
        for x, y in zip(xs, ys):
            ax2.text(x, y + 0.0008, '{:.4f}'.format(y), ha='center', va='bottom', fontsize=6.5, rotation=90)
    ax2.set_ylim(0.995, max(agg[k]['S_overall'] for k in CORES) + 0.012)
    ax2.set_xticks(CORES)
    ax2.set_xlabel('核数 K')
    ax2.set_ylabel('平均比值（逐算例算术平均）')
    ax2.set_title('四格配对分解（方案文档 3.5 节）')
    ax2.grid(axis='y', alpha=0.3)
    ax2.legend(fontsize=7.5, loc='upper left')
    save(fig, 'pairing_p3')


def chart_hitrate(rows, agg):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.4))
    width = 0.36
    for i, (key, label, color) in enumerate((('hit_PB', '问题二方案 P_B', RED), ('hit_PC', '问题三方案 P_C', BLUE))):
        xs = [k + (i - 0.5) * width for k in CORES]
        ys = [agg[k][key] * 100 for k in CORES]
        ax1.bar(xs, ys, width=width, color=color, label=label)
        for x, y in zip(xs, ys):
            ax1.text(x, y + 0.3, '{:.1f}'.format(y), ha='center', fontsize=8)
    ax1.set_xticks(CORES)
    ax1.set_xlabel('核数 K')
    ax1.set_ylabel('平均按字节命中率（%）')
    ax1.set_title('只读 L2 Cache 命中率（逐算例平均）')
    ax1.grid(axis='y', alpha=0.3)
    ax1.legend(fontsize=8)
    markers = {2: 'o', 3: 's', 4: '^', 5: 'D'}
    for k in CORES:
        rk = [r for r in rows if int(r['cores']) == k]
        ax2.scatter([float(r['hit_rate_PC']) * 100 for r in rk], [float(r['S_overall']) for r in rk],
                    s=14, marker=markers[k], alpha=0.7, label='K={}'.format(k))
    ax2.axhline(1.0, color='#888888', lw=0.8)
    ax2.set_xlabel('P_C 按字节命中率（%）')
    ax2.set_ylabel('S_overall = T_B(P_B)/T_C(P_C)')
    ax2.set_title('逐算例：命中率与只读 Cache 加速比')
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=8)
    save(fig, 'hitrate_p3')


def fmt(v, d=3):
    return '—' if v != v else '{:.{}f}'.format(v, d)


def n(v):
    return '{:,}'.format(int(float(v))) if v not in ('', None) else '—'


def tables(rows, agg):
    md = []
    md.append('### 表 1  按核数的平均加速比（分母：无 L2 单核基准；逐算例算术平均）\n')
    md.append('| 核数 K | 用例数 | 无 L2（P_B） | 只读 Cache（P_C） | 问题一方案@Cache | 简单贪心@Cache | stub@Cache | 全部官方合法 |')
    md.append('|---|---|---|---|---|---|---|---|')
    for k in sorted(agg):
        a = agg[k]
        md.append('| {} | {} | {} | {} | {} | {} | {} | {} |'.format(
            k, a['n'], fmt(a['noL2']), fmt(a['cache']), fmt(a['p1plan']), fmt(a['greedy']), fmt(a['stub']),
            a.get('valid', True)))
    md.append('\nK=1 行：无 L2 为单核基准本身（=1）；只读 Cache 为同一单核方案在问题三评估器下的结果。\n')
    md.append('### 表 2  四格配对分解与 Cache 命中率（方案文档 3.5 节）\n')
    md.append('| 核数 K | S_hardware | S_schedule | S_overall | 命中率 P_B | 命中率 P_C |')
    md.append('|---|---|---|---|---|---|')
    for k in CORES:
        a = agg[k]
        md.append('| {} | {} | {} | {} | {}% | {}% |'.format(
            k, fmt(a['S_hardware'], 4), fmt(a['S_schedule'], 4), fmt(a['S_overall'], 4),
            fmt(a['hit_PB'] * 100, 2), fmt(a['hit_PC'] * 100, 2)))
    md.append('\n### 表 3  四格配对的逐核计数与总和\n')
    md.append('| 核数 K | Cache 使 P_B 变快 / 不变 / 变慢 | P_C 优于 / 等于 / 劣于 P_B（Cache 下） | 无 L2 下 P_C 劣于 / 优于 P_B | 新增搬运 P_C > P_B | ΣT_B(P_B) | ΣT_C(P_B) | ΣT_B(P_C) | ΣT_C(P_C) |')
    md.append('|---|---|---|---|---|---|---|---|---|')
    I = lambda r, key: int(r[key])
    for k in CORES:
        rk = [r for r in rows if int(r['cores']) == k]
        md.append('| {} | {} / {} / {} | {} / {} / {} | {} / {} | {} | {} | {} | {} | {} |'.format(
            k, sum(I(r, 'T_C_PB') < I(r, 'T_B_PB') for r in rk), sum(I(r, 'T_C_PB') == I(r, 'T_B_PB') for r in rk),
            sum(I(r, 'T_C_PB') > I(r, 'T_B_PB') for r in rk),
            sum(I(r, 'T_C_PC') < I(r, 'T_C_PB') for r in rk), sum(I(r, 'T_C_PC') == I(r, 'T_C_PB') for r in rk),
            sum(I(r, 'T_C_PC') > I(r, 'T_C_PB') for r in rk),
            sum(I(r, 'T_B_PC') > I(r, 'T_B_PB') for r in rk), sum(I(r, 'T_B_PC') < I(r, 'T_B_PB') for r in rk),
            sum(I(r, 'added_copy_bytes_PC') > I(r, 'added_copy_bytes_PB') for r in rk),
            n(sum(I(r, 'T_B_PB') for r in rk)), n(sum(I(r, 'T_C_PB') for r in rk)),
            n(sum(I(r, 'T_B_PC') for r in rk)), n(sum(I(r, 'T_C_PC') for r in rk))))
    md.append('\n### 表 4  与三种基线的逐核胜负（均为只读 Cache 配置下的官方 Makespan）\n')
    md.append('| 核数 K | 优于 stub | 优于简单贪心 | 优于问题一方案（严格 / 持平） | 优于单核（Cache） |')
    md.append('|---|---|---|---|---|')
    for k in CORES:
        rk = [r for r in rows if int(r['cores']) == k]
        md.append('| {} | {}/{} | {}/{} | {} / {} | {}/{} |'.format(
            k, sum(I(r, 'T_C_PC') < I(r, 'stub_T_C') for r in rk), len(rk),
            sum(I(r, 'T_C_PC') < I(r, 'greedy_T_C') for r in rk), len(rk),
            sum(I(r, 'T_C_PC') < I(r, 'p1plan_T_C') for r in rk), sum(I(r, 'T_C_PC') == I(r, 'p1plan_T_C') for r in rk),
            sum(I(r, 'T_C_PC') < I(r, 'singlecore_cache') for r in rk), len(rk)))
    md.append('\n### 表 5  运行时间（秒）\n')
    md.append('| 核数 K | 求解平均 | 求解最长 | 官方问题三复核平均 | 官方复核最长 |')
    md.append('|---|---|---|---|---|')
    for k in CORES:
        rk = [r for r in rows if int(r['cores']) == k]
        md.append('| {} | {:.1f} | {:.1f} | {:.1f} | {:.1f} |'.format(
            k, st.mean(float(r['solve_seconds']) for r in rk), max(float(r['solve_seconds']) for r in rk),
            st.mean(float(r['official_eval_seconds']) for r in rk), max(float(r['official_eval_seconds']) for r in rk)))
    md.append('\n### 表 6  逐算例结果（官方问题三评估器；Makespan 单位 cycles）\n')
    md.append('| 用例 | 单核 无 L2 / Cache | K=2 T_C(P_C) / 命中率 | K=3 | K=4 | K=5 | K=2..5 S_overall | K=4 四格 T_B(P_B) / T_C(P_B) / T_B(P_C) / T_C(P_C) |')
    md.append('|---|---|---|---|---|---|---|---|')
    by_case = defaultdict(dict)
    for r in rows:
        by_case[r['case']][int(r['cores'])] = r
    for case in sorted(by_case):
        d = by_case[case]
        a = next(iter(d.values()))
        cells = ['{} / {:.3f}'.format(n(d[k]['T_C_PC']), float(d[k]['hit_rate_PC'])) for k in CORES]
        so = '/'.join('{:.3f}'.format(float(d[k]['S_overall'])) for k in CORES)
        r4 = d[4]
        md.append('| {} | {} / {} | {} | {} | {} | {} | {} | {} / {} / {} / {} |'.format(
            case, n(a['singlecore_noL2']), n(a['singlecore_cache']), *cells, so,
            n(r4['T_B_PB']), n(r4['T_C_PB']), n(r4['T_B_PC']), n(r4['T_C_PC'])))
    (RESULTS_DIR / 'report_tables.md').write_text('\n'.join(md) + '\n', encoding='utf-8')
    return md


def main():
    rows = read_csv(RESULTS_DIR / 'summary.csv')
    cases = sorted({r['case'] for r in rows})
    agg = aggregate(rows, cases)
    cross_check(agg)
    chart_speedup(agg)
    chart_pairing(rows, agg)
    chart_hitrate(rows, agg)
    md = tables(rows, agg)
    print('\n'.join(md[:22]))


if __name__ == '__main__':
    main()
