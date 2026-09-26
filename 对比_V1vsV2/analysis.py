"""胜负原因的辅助统计（依赖 compare.py 生成的 tables/per_case_p*.csv）。输出 tables/analysis.json 及若干小表。

    .venv/bin/python 对比_V1vsV2/analysis.py
"""
import csv
import json
import statistics as st
from collections import Counter, defaultdict

from compare import OUT, V1, rd_csv, v1_supplement, write_csv
from eval_official import CASES, CORES, DATA, ROOT


def ops_count():
    out = {}
    for c in CASES:
        g = json.loads((DATA / (c + '.json')).read_text(encoding='utf-8'))
        out[c] = sum(1 for o in g['ops'] if o['op'] not in ('COPY_IN', 'COPY_OUT'))
    return out


def fnum(x):
    return float(x) if x not in ('', None) else None


def main():
    ops = ops_count()
    order = sorted(CASES, key=lambda c: ops[c])
    quart = {c: min(3, i * 4 // len(order)) for i, c in enumerate(order)}
    qbounds = [(ops[order[q * 25]], ops[order[q * 25 + 24]]) for q in range(4)]
    res = {'ops_quartile_bounds': qbounds}
    per = {p: [r for r in rd_csv(OUT / 'per_case_p{}.csv'.format(p)) if r.get('winner')] for p in (1, 2, 3)}

    # 1. 按图规模四分位的胜负与平均加速比
    rows = []
    for p in (1, 2, 3):
        for q in range(4):
            r = [x for x in per[p] if quart[x['case']] == q]
            rows.append({'problem': p, 'ops_quartile': 'Q{} ({}–{} 计算节点)'.format(q + 1, *qbounds[q]),
                         'configs': len(r), 'v2_wins': sum(x['winner'] == 'V2' for x in r),
                         'ties': sum(x['winner'] == 'tie' for x in r), 'v1_wins': sum(x['winner'] == 'V1' for x in r),
                         'v1_mean_speedup': round(st.mean(float(x['v1_speedup']) for x in r), 4),
                         'v2_mean_speedup': round(st.mean(float(x['v2_speedup']) for x in r), 4),
                         'geomean_T_v1_over_v2': round(st.geometric_mean(float(x['makespan_ratio_v1_over_v2'])
                                                                         for x in r), 4)})
    write_csv(OUT / 'by_size_quartile.csv', rows)
    res['by_size_quartile'] = rows

    # 2. 问题一：第一版退回整图（加速比=1）与子图数量、spill
    p1 = per[1]
    res['p1'] = {
        'v1_speedup_eq_1': sorted((x['case'], int(x['cores'])) for x in p1 if abs(float(x['v1_speedup']) - 1) < 1e-12),
        'v2_speedup_eq_1': sorted((x['case'], int(x['cores'])) for x in p1 if abs(float(x['v2_speedup']) - 1) < 1e-12),
        'v1_superlinear': sum(float(x['v1_speedup']) > int(x['cores']) for x in p1),
        'v2_superlinear': sum(float(x['v2_speedup']) > int(x['cores']) for x in p1),
        'total_spill_bytes': {v: sum(int(x[v + '_spill']) for x in p1) for v in ('v1', 'v2')},
        'total_partition_bytes': {v: sum(int(x[v + '_partition']) for x in p1) for v in ('v1', 'v2')},
    }
    v1sel = Counter()
    for c in CASES:
        rows_c = json.loads((ROOT / '对比_V1vsV2/v1_regen/p1/results' / c / 'rows.json').read_text(encoding='utf-8'))
        for r in rows_c:
            if r['cores'] >= 2:
                v1sel[r['selected'].split('_')[0] if not r['selected'].startswith('safe_growth')
                      else 'safe_growth_' + r['selected'].split('_')[-1]] += 1
    res['p1']['v1_selected_candidate'] = dict(v1sel.most_common())
    v2p1 = rd_csv(ROOT / 'results_p1/summary.csv')
    res['p1']['v2_construct_best'] = dict(Counter(r['construct_best'].split('/')[0] for r in v2p1 if int(r['cores']) >= 2).most_common())
    res['p1']['v2_cpsat_improved'] = sum(r['cpsat_improved'] not in ('', '0') for r in v2p1 if int(r['cores']) >= 2)
    for v in ('v1', 'v2'):  # 问题一每个子图是一个 Task；子图数直接从提交方案统计
        counts = []
        for x in p1:
            f = ROOT / '对比_V1vsV2/official_eval' / '{}_p1'.format(v) / '{}_K{}.json'.format(x['case'], x['cores'])
            plan = json.loads((ROOT / json.loads(f.read_text(encoding='utf-8'))['plan']).read_text(encoding='utf-8'))
            counts.append(len(set(plan['node_to_subgraph'].values())))
        res['p1'][v + '_median_subgraphs'] = st.median(counts)
        res['p1'][v + '_mean_subgraphs'] = round(st.mean(counts), 1)

    # 3. 问题二/三：第一版获胜配置的特征
    v2p2 = {(r['case'], r['cores']): r for r in rd_csv(ROOT / 'results_p2/summary.csv')}
    v1exp = {(r['case'], r['cores']): r for r in rd_csv(V1 / 'v1_problem2/exploration_20260925/comparison.csv')}
    w = [x for x in per[2] if x['winner'] == 'V1']
    l = [x for x in per[2] if x['winner'] == 'V2']
    res['p2'] = {
        'v1_win_median_ops': st.median(ops[x['case']] for x in w),
        'v2_win_median_ops': st.median(ops[x['case']] for x in l),
        'v1_win_cases': sorted({x['case'] for x in w}),
        'v2_construct_best_when_v1_wins': dict(Counter(v2p2[(x['case'], x['cores'])]['construct_best'].split('/')[0]
                                                      for x in w).most_common()),
        'v1_selected_when_v1_wins': dict(Counter(_v1src(v1exp[(x['case'], x['cores'])]['selected']) for x in w).most_common()),
        'v1_selected_all': dict(Counter(_v1src(r['selected']) for r in v1exp.values()).most_common()),
        'v2_construct_best_all': dict(Counter(r['construct_best'].split('/')[0] for r in v2p2.values()
                                              if int(r['cores']) >= 2).most_common()),
        'v2_mean_solve_seconds_when_v1_wins': st.mean(float(v2p2[(x['case'], x['cores'])]['solve_seconds']) for x in w),
        'v1_mean_search_seconds_when_v1_wins': st.mean(float(v1exp[(x['case'], x['cores'])]['search_seconds']) for x in w),
        'total_xfers': {v: sum(int(x[v + '_xfers']) for x in per[2]) for v in ('v1', 'v2')},
        'total_spill_bytes': {v: sum(int(x[v + '_spill']) for x in per[2]) for v in ('v1', 'v2')},
    }
    both = {(x['case'], x['cores']) for x in per[2] if x['winner'] == 'V1'} & \
           {(x['case'], x['cores']) for x in per[3] if x['winner'] == 'V1'}
    res['p2_p3_v1_win_overlap'] = len(both)

    # 4. 问题三相对问题二的增量（各版自己的 P3 − P2），以及 Cache 命中
    s = {(int(r['problem']), int(r['cores'])): r for r in rd_csv(OUT / 'summary_by_k.csv')}
    res['p3_minus_p2'] = {k: {v: round(float(s[(3, k)][v + '_mean_speedup']) - float(s[(2, k)][v + '_mean_speedup']), 4)
                              for v in ('v1', 'v2')} for k in CORES}
    # 同一方案（第一版 P_B 种子）在问题三下的 T_C，用来分解“起点差”和“问题三优化增量”
    seed = {}
    for f in (ROOT / '对比_V1vsV2/official_eval/v1_p3_seedPB').glob('*.json'):
        r = json.loads(f.read_text(encoding='utf-8'))
        seed[(r['case'], str(r['cores']))] = r['result']['makespan']
    p3 = per[3]
    res['p3_v1_improved_over_seed'] = sum(int(x['v1_makespan']) < seed[(x['case'], x['cores'])] for x in p3)
    v2p3 = {(r['case'], r['cores']): r for r in rd_csv(ROOT / 'results_p3/summary.csv')}
    res['p3_v2_improved_over_PB'] = sum(int(v2p3[(x['case'], x['cores'])]['T_C_PC']) <
                                        int(v2p3[(x['case'], x['cores'])]['T_C_PB']) for x in p3)

    # 5. 逐算例取优后的核数单调性（K 核不应慢于 K−1 核；若慢可沿用 K−1 核方案补空核）
    mono = []
    for p in (1, 2, 3):
        byc = defaultdict(dict)
        for x in per[p]:
            byc[x['case']][int(x['cores'])] = int(x['merged_makespan'])
        viol = [(c, k) for c, d in byc.items() for k in CORES[1:] if d[k] > d[k - 1]]
        mono.append({'problem': p, 'merged_monotone_violations': len(viol), 'examples': viol[:10]})
    res['merged_monotonicity'] = mono

    # 6. 第一版 952 组补充配置：按其自报 makespan 取每个 (图, 核) 最好，与第二版比较
    supp_rows = []
    for p in (1, 2, 3):
        best, n_all, n_formal = v1_supplement(p)
        v2 = {(x['case'], int(x['cores'])): int(x['v2_makespan']) for x in per[p]}
        v1m = {(x['case'], int(x['cores'])): int(x['v1_makespan']) for x in per[p]}
        for key, (t, var) in sorted(best.items()):
            supp_rows.append({'problem': p, 'case': key[0], 'cores': key[1], 'supp_best_makespan_reported': t,
                              'supp_best_variant': var, 'v1_main_makespan': v1m[key], 'v2_makespan': v2[key],
                              'supp_beats_v1_main': t < v1m[key], 'supp_beats_v2': t < v2[key],
                              'gap_vs_v2_pct': round(100 * (t / v2[key] - 1), 3)})
        res['supplement_p{}'.format(p)] = {'configs_total': n_all, 'configs_on_official_graphs': n_formal,
                                           'graph_core_cells': len(best),
                                           'cells_beating_v1_main': sum(r['supp_beats_v1_main'] for r in supp_rows if r['problem'] == p),
                                           'cells_beating_v2': sum(r['supp_beats_v2'] for r in supp_rows if r['problem'] == p)}
    write_csv(OUT / 'v1_supplement952_cells_vs_v2.csv', supp_rows)

    (OUT / 'analysis.json').write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str)[:6000])


def _v1src(sel):
    if sel.startswith('round'):
        return 'neighborhood:' + sel.split(':')[2]
    return sel.split(':')[0]


if __name__ == '__main__':
    main()
