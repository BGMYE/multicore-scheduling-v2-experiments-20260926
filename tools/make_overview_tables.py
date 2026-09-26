"""三问总表：从 results_p1/2/3 的 summary.csv 逐行重新计算（逐算例加速比的算术平均），输出 Markdown。

    python3 tools/make_overview_tables.py > tools/overview_tables.md
"""
import csv
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORES = (2, 3, 4, 5)


def rows(p):
    return list(csv.DictReader(open(ROOT / p / 'summary.csv', encoding='utf-8')))


def mean(rs, key):
    return st.mean(float(r[key]) for r in rs)


def by_k(rs, k):
    return [r for r in rs if int(r['cores']) == k]


def main():
    p1, p2, p3 = rows('results_p1'), rows('results_p2'), rows('results_p3')
    out = []
    out.append('| 问题 / 方法 | K=2 | K=3 | K=4 | K=5 | 方案数 | 官方合法 |')
    out.append('|---|---|---|---|---|---|---|')
    lines = [
        ('问题一（场景 A）本文', p1, 'speedup'),
        ('问题一 简单贪心', p1, None),
        ('问题一 官方 stub', p1, 'stub_speedup'),
        ('问题二（场景 B）本文', p2, 'speedup'),
        ('问题二 问题一方案@B', p2, 'p1plan_speedup'),
        ('问题二 简单贪心', p2, 'greedy_speedup'),
        ('问题二 官方 stub', p2, 'stub_speedup'),
        ('问题三（B + 只读 Cache）本文 P_C', p3, 'speedup_cache'),
        ('问题三 无 L2 的 P_B（对照）', p3, 'speedup_noL2'),
        ('问题三 问题一方案@Cache', p3, 'p1plan_speedup_cache'),
        ('问题三 简单贪心@Cache', p3, 'greedy_speedup_cache'),
        ('问题三 官方 stub@Cache', p3, 'stub_speedup_cache'),
    ]
    for name, rs, key in lines:
        if key is None:     # 问题一 summary 中贪心只有 makespan
            vals = [st.mean(float(r['singlecore_makespan']) / float(r['greedy_makespan']) for r in by_k(rs, k)) for k in CORES]
        else:
            vals = [mean(by_k(rs, k), key) for k in CORES]
        valid = '{}/{}'.format(sum(r['official_valid'] == 'True' for r in rs), len(rs)) if '本文' in name else '—'
        out.append('| {} | {} | {} | {} | {} | {} | {} |'.format(
            name, *('{:.3f}'.format(v) for v in vals), len(rs) if '本文' in name else '—', valid))
    out.append('')
    out.append('| 问题 | 平均 Makespan/LB0 | 单个 (算例,K) 求解平均/最长 (s) | 官方复核单个方案最长 (s) | 求解评分=官方复核 | K−1 沿用 |')
    out.append('|---|---|---|---|---|---|')
    lb = {r['case'] + r['cores']: float(r['lower_bound_LB0']) for r in p2}
    for name, rs, mk, match in (('问题一', p1, 'makespan', 'fast_eval_match'), ('问题二', p2, 'makespan', 'eval_match'),
                                ('问题三', p3, 'T_C_PC', 'eval_match')):
        if 'lower_bound_LB0' in rs[0]:
            gap = st.mean(float(r[mk]) / float(r['lower_bound_LB0']) for r in rs)
        else:
            gap = st.mean(float(r[mk]) / lb[r['case'] + r['cores']] for r in rs)
        out.append('| {} | {:.3f} | {:.1f} / {:.1f} | {:.1f} | {}/{} | {} |'.format(
            name, gap, st.mean(float(r['solve_seconds']) for r in rs), max(float(r['solve_seconds']) for r in rs),
            max(float(r['official_eval_seconds']) for r in rs), sum(r[match] == 'True' for r in rs), len(rs),
            sum(1 for r in rs if r.get('inherited_from_cores'))))
    print('\n'.join(out))


if __name__ == '__main__':
    main()
