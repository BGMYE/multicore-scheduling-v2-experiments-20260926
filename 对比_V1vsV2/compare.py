"""汇总两版官方复评结果：逐算例对比 csv、按核数汇总 csv、胜负统计、差距最大算例、第一版补充候选。

只读 official_eval/ 下由 eval_official.py 生成的官方复评记录，以及两版各自报告的数值（仅用于核对“复评 = 报告”）。
加速比分母统一取官方 singlecore_evaluate.py 的复评结果（official_eval/single）。

    .venv/bin/python 对比_V1vsV2/compare.py            # 产出 tables/*.csv 与 tables/summary.json
"""
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

from eval_official import CASES, CORES, EVAL_DIR, HERE, ROOT, V1

OUT = HERE / 'tables'


def rd_csv(path):
    with open(path, encoding='utf-8-sig') as h:
        return list(csv.DictReader(h))


def load_tag(tag):
    d = EVAL_DIR / tag
    out = {}
    if d.exists():
        for f in d.glob('*.json'):
            r = json.loads(f.read_text(encoding='utf-8'))
            out[(r['case'], r['cores'])] = r
    return out


def write_csv(path, rows, keys=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = keys or list(rows[0])
    with open(path, 'w', newline='', encoding='utf-8-sig') as h:
        w = csv.DictWriter(h, fieldnames=keys, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------ 各版“报告值”（只用于核对）
def v1_reported(problem):
    if problem == 1:
        rows = rd_csv(V1 / 'v1_problem1/results/summary.csv')
        return {(r['case'], int(r['cores'])): (int(r['makespan']), int(r['added_copy_bytes'])) for r in rows}
    if problem == 2:  # 第一版问题二最终 = 探索后方案
        rows = rd_csv(V1 / 'v1_problem2/exploration_20260925/comparison.csv')
        return {(r['case'], int(r['cores'])): (int(r['optimized_cycles']), int(r['optimized_added_copy_bytes']))
                for r in rows}
    rows = rd_csv(V1 / 'v1_problem3/results/comparison.csv')
    return {(r['case'], int(r['cores'])): (int(r['TC_PC']), int(r['final_added_copy_bytes']), r['selected'],
                                           int(r['final_hit_bytes']), int(r['final_copy_in_miss_bytes']))
            for r in rows}


def v2_reported(problem):
    p = ROOT / 'results_p{}'.format(problem) / 'summary.csv'
    if not p.exists():
        return {}
    out = {}
    tk, ak = ('T_C_PC', 'added_copy_bytes_PC') if problem == 3 else ('makespan', 'added_copy_bytes')
    for r in rd_csv(p):
        if int(r['cores']) >= 2 and r.get(tk):
            out[(r['case'], int(r['cores']))] = (int(r[tk]), int(r[ak]))
    return out


def v1_supplement(problem):
    """第一版补充实验（正式图部分）逐 (图, 核) 最好 makespan 及对应变体。"""
    rows = rd_csv(V1 / 'v1_problem{}/supplementary_20260925/comparison.csv'.format(problem))
    best = {}
    for r in rows:
        if r.get('kind') == 'synthetic':
            continue
        t = int(r['TC_PC'] if problem == 3 else r['makespan'])
        key = (r['case'], int(r['cores']))
        if key not in best or t < best[key][0]:
            best[key] = (t, r['variant'])
    return best, len(rows), sum(r.get('kind') != 'synthetic' for r in rows)


# ------------------------------------------------------------ 逐算例对比
def res_fields(rec):
    r = rec['result']
    d = {'makespan': r['makespan'], 'added': r['data_movement_bytes']['added_copy_bytes'],
         'spill': r['data_movement_bytes']['spill_added_copy_bytes'],
         'partition': r['data_movement_bytes']['partition_added_copy_bytes']}
    if 'cross_core_transfers' in r:
        d['xfers'] = r['cross_core_transfers']
    if 'cache_stats' in r:
        cs = r['cache_stats']
        d.update(hits=cs['hits'], accesses=cs['accesses'], hit_bytes=cs['hit_bytes'],
                 miss_bytes=cs['miss_bytes'], hit_rate=cs['hit_rate'])
    return d


def build(problem, single):
    v1_tag = {1: 'v1_p1', 2: 'v1_p2', 3: 'v1_p3'}[problem]
    v1 = load_tag(v1_tag)
    v1_pkg = load_tag({1: 'v1_p1_pkg', 2: 'v1_p2', 3: 'v1_p3_pkg'}[problem])
    v1_seed = load_tag('v1_p3_seedPB') if problem == 3 else {}
    v2 = load_tag('v2_p{}'.format(problem))
    supp_eval = load_tag('v1_supp_q{}'.format(problem))
    rep1, rep2 = v1_reported(problem), v2_reported(problem)
    supp_best, _, _ = v1_supplement(problem)
    rows = []
    for k in CORES:
        for c in CASES:
            T1 = single[c]
            row = {'case': c, 'cores': k, 'T1': T1}
            # ---- 第一版：优先官方复评（重生成方案 → 发布包方案 → P_B 种子[仅问题三且第一版最终选 P_B]），否则报告值
            rec, src = v1.get((c, k)), 'official_reeval(regen)'
            if not (rec and rec.get('valid')) and (c, k) in v1_pkg and v1_pkg[(c, k)].get('valid'):
                rec, src = v1_pkg[(c, k)], 'official_reeval(package)'
            if problem == 2 and rec:
                src = 'official_reeval(package seeds)'
            if not (rec and rec.get('valid')) and problem == 3 and rep1.get((c, k), (0, 0, ''))[2] == 'P_B' \
                    and v1_seed.get((c, k), {}).get('valid'):
                rec, src = v1_seed[(c, k)], 'official_reeval(P_B seed)'
            rp = rep1.get((c, k))
            row['v1_reported_makespan'] = rp[0] if rp else ''
            if rec and rec.get('valid'):
                f = res_fields(rec)
                row['v1_valid'] = True
                for key, val in f.items():
                    row['v1_' + key] = val
                row['v1_matches_report'] = (rp is not None and rp[0] == f['makespan'] and rp[1] == f['added'])
            elif rec:
                row['v1_valid'] = False
                src = 'official_reeval_FAILED'
            else:
                row['v1_valid'] = ''
                row['v1_makespan'], row['v1_added'] = rp[0], rp[1]
                if problem == 3:
                    row['v1_hit_bytes'], row['v1_miss_bytes'] = rp[3], rp[4]
                src = 'reported_only'
            row['v1_source'] = src
            # ---- 第二版
            rec2 = v2.get((c, k))
            rp2 = rep2.get((c, k))
            row['v2_reported_makespan'] = rp2[0] if rp2 else ''
            if rec2 and rec2.get('valid'):
                f = res_fields(rec2)
                row['v2_valid'] = True
                for key, val in f.items():
                    row['v2_' + key] = val
                row['v2_matches_report'] = (rp2 is not None and rp2[0] == f['makespan'] and rp2[1] == f['added'])
                row['v2_source'] = 'official_reeval'
            else:
                row['v2_valid'] = False if rec2 else ''
                row['v2_source'] = 'official_reeval_FAILED' if rec2 else 'missing'
            if row.get('v1_makespan') in (None, '') or row.get('v2_makespan') in (None, ''):
                rows.append(row)
                continue
            t1, t2 = row['v1_makespan'], row['v2_makespan']
            row['v1_speedup'] = T1 / t1
            row['v2_speedup'] = T1 / t2
            row['speedup_diff_v2_minus_v1'] = row['v2_speedup'] - row['v1_speedup']
            row['makespan_ratio_v1_over_v2'] = t1 / t2
            row['winner'] = 'V2' if t2 < t1 else ('V1' if t1 < t2 else 'tie')
            k1, k2 = (t1, row['v1_added']), (t2, row['v2_added'])
            row['winner_lex'] = 'V2' if k2 < k1 else ('V1' if k1 < k2 else 'tie')
            # ---- 第一版补充实验（57 个更优候选经官方复评；952 组按其自报表取最好）
            sb = supp_best.get((c, k))
            row['v1_supp_best_reported'] = sb[0] if sb else ''
            row['v1_supp_best_variant'] = sb[1] if sb else ''
            se = supp_eval.get((c, k))
            best_t, best_a, best_src = t1, row['v1_added'], 'V1_main'
            if se and se.get('valid'):
                f = res_fields(se)
                row['v1_supp57_makespan'] = f['makespan']
                row['v1_supp57_added'] = f['added']
                if (f['makespan'], f['added']) < (best_t, best_a):
                    best_t, best_a, best_src = f['makespan'], f['added'], 'V1_supp57'
            row['v1best_makespan'], row['v1best_added'], row['v1best_source'] = best_t, best_a, best_src
            row['v1best_speedup'] = T1 / best_t
            row['winner_vs_v1best'] = 'V2' if t2 < best_t else ('V1' if best_t < t2 else 'tie')
            # ---- 逐算例取优（字典序），候选 = V2、V1 主结果、V1 57 候选
            cand = [((t2, row['v2_added']), 'V2'), ((best_t, best_a), best_src)]
            (mt, ma), msrc = min(cand, key=lambda x: (x[0], x[1] != 'V2'))
            row['merged_makespan'], row['merged_added'], row['merged_source'] = mt, ma, msrc
            row['merged_speedup'] = T1 / mt
            rows.append(row)
    return rows


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def summarize(problem, rows):
    out = []
    for k in CORES:
        r = [x for x in rows if x['cores'] == k and 'winner' in x]
        if not r:
            continue
        s = {'problem': problem, 'cores': k, 'n_compared': len(r),
             'v1_official_reevaluated': sum(x['v1_source'].startswith('official_reeval(') for x in r),
             'v1_valid': sum(x.get('v1_valid') is True for x in r),
             'v2_valid': sum(x.get('v2_valid') is True for x in r),
             'v1_mean_speedup': mean([x['v1_speedup'] for x in r]),
             'v2_mean_speedup': mean([x['v2_speedup'] for x in r]),
             'v1best_mean_speedup': mean([x['v1best_speedup'] for x in r]),
             'merged_mean_speedup': mean([x['merged_speedup'] for x in r]),
             'v1_median_speedup': statistics.median(x['v1_speedup'] for x in r),
             'v2_median_speedup': statistics.median(x['v2_speedup'] for x in r),
             'v1_mean_makespan': mean([x['v1_makespan'] for x in r]),
             'v2_mean_makespan': mean([x['v2_makespan'] for x in r]),
             'v1_total_makespan': sum(x['v1_makespan'] for x in r),
             'v2_total_makespan': sum(x['v2_makespan'] for x in r),
             'v1_mean_added_bytes': mean([x['v1_added'] for x in r]),
             'v2_mean_added_bytes': mean([x['v2_added'] for x in r]),
             'v1_total_added_bytes': sum(x['v1_added'] for x in r),
             'v2_total_added_bytes': sum(x['v2_added'] for x in r),
             'v2_wins': sum(x['winner'] == 'V2' for x in r),
             'ties': sum(x['winner'] == 'tie' for x in r),
             'v1_wins': sum(x['winner'] == 'V1' for x in r),
             'v2_wins_lex': sum(x['winner_lex'] == 'V2' for x in r),
             'v1_wins_lex': sum(x['winner_lex'] == 'V1' for x in r),
             'v1best_wins_vs_v2': sum(x['winner_vs_v1best'] == 'V1' for x in r),
             'merged_from_v1': sum(x['merged_source'] != 'V2' for x in r),
             'geomean_ratio_v1_over_v2': statistics.geometric_mean(x['makespan_ratio_v1_over_v2'] for x in r)}
        s['v2_less_added_count'] = sum(x['v2_added'] < x['v1_added'] for x in r)
        if problem >= 2:
            s['v1_total_xfers'] = sum(x.get('v1_xfers') or 0 for x in r)
            s['v2_total_xfers'] = sum(x.get('v2_xfers') or 0 for x in r)
        if problem == 3:
            for v in ('v1', 'v2'):
                hb = sum(x.get(v + '_hit_bytes') or 0 for x in r)
                mb = sum(x.get(v + '_miss_bytes') or 0 for x in r)
                s[v + '_total_hit_bytes'] = hb
                s[v + '_total_miss_bytes'] = mb
                s[v + '_pooled_byte_hit_rate'] = hb / (hb + mb) if hb + mb else None
                s[v + '_total_hits'] = sum(x.get(v + '_hits') or 0 for x in r)
                s[v + '_total_accesses'] = sum(x.get(v + '_accesses') or 0 for x in r)
                s[v + '_mean_hit_rate'] = mean([x.get(v + '_hit_rate') for x in r])
        out.append(s)
    return out


def main():
    OUT.mkdir(exist_ok=True)
    single_eval = load_tag('single')
    single = {c: single_eval[(c, 1)]['result']['makespan'] for c in CASES if (c, 1) in single_eval}
    # 分母核对：官方单核 vs 第一版 K1 vs 第二版 baselines/singlecore
    v1k1 = {r['case']: int(r['makespan']) for r in rd_csv(V1 / 'v1_problem1/results/summary.csv') if r['cores'] == '1'}
    v2k1 = {c: json.load(open(ROOT / 'results_p1/baselines/singlecore' / (c + '.json')))['makespan'] for c in CASES}
    den = [{'case': c, 'official_singlecore': single.get(c), 'v1_K1': v1k1.get(c), 'v2_singlecore': v2k1.get(c),
            'all_equal': single.get(c) == v1k1.get(c) == v2k1.get(c)} for c in CASES]
    write_csv(OUT / 'denominator_check.csv', den)
    missing = [c for c in CASES if c not in single]
    if missing:
        raise SystemExit('单核基准尚未全部复评：{}'.format(missing[:5]))
    summary = {'denominator_all_equal': all(d['all_equal'] for d in den)}
    all_sum = []
    for p in (1, 2, 3):
        rows = build(p, single)
        write_csv(OUT / 'per_case_p{}.csv'.format(p), rows, keys=sorted({k for r in rows for k in r},
                  key=lambda k: (list(rows[0]).index(k) if k in rows[0] else 999, k)))
        s = summarize(p, rows)
        all_sum += s
        cmp_rows = [r for r in rows if 'winner' in r]
        info = {'rows': len(rows), 'compared': len(cmp_rows),
                'v1_sources': dict(sorted(_count(r['v1_source'] for r in rows).items())),
                'v2_sources': dict(sorted(_count(r['v2_source'] for r in rows).items())),
                'v1_report_mismatch': [(r['case'], r['cores'], r.get('v1_makespan'), r['v1_reported_makespan'])
                                       for r in rows if r.get('v1_matches_report') is False],
                'v2_report_mismatch': [(r['case'], r['cores'], r.get('v2_makespan'), r['v2_reported_makespan'])
                                       for r in rows if r.get('v2_matches_report') is False],
                'overall': {'v2_wins': sum(r['winner'] == 'V2' for r in cmp_rows),
                            'ties': sum(r['winner'] == 'tie' for r in cmp_rows),
                            'v1_wins': sum(r['winner'] == 'V1' for r in cmp_rows),
                            'v1best_wins': sum(r['winner_vs_v1best'] == 'V1' for r in cmp_rows)},
                'top_v2_adv': [_brief(r) for r in sorted(cmp_rows, key=lambda r: -r['makespan_ratio_v1_over_v2'])[:10]],
                'top_v1_adv': [_brief(r) for r in sorted(cmp_rows, key=lambda r: r['makespan_ratio_v1_over_v2'])[:10]
                               if r['winner'] == 'V1'],
                'v1best_beats_v2': [_brief(r) for r in cmp_rows if r['winner_vs_v1best'] == 'V1']}
        summary['p{}'.format(p)] = info
    write_csv(OUT / 'summary_by_k.csv', all_sum, keys=sorted({k for r in all_sum for k in r},
              key=lambda k: (list(all_sum[-1]).index(k) if k in all_sum[-1] else 999, k)))
    # 补充实验：57 候选复评核对 + 952 组规模
    supp = rd_csv(V1 / 'v1_supplement_20260925/better_observed_candidates.csv')
    srows = []
    for r in supp:
        p = int(r['question'])
        e = load_tag('v1_supp_q{}'.format(p)).get((r['case'], int(r['cores'])))
        v2e = load_tag('v2_p{}'.format(p)).get((r['case'], int(r['cores'])))
        row = {'question': p, 'case': r['case'], 'cores': int(r['cores']), 'variant': r['variant'],
               'v1_main_makespan': int(r['original_makespan']), 'reported_makespan': int(r['best_observed_makespan']),
               'official_valid': bool(e and e.get('valid')),
               'official_makespan': e['result']['makespan'] if e and e.get('valid') else '',
               'official_added': e['result']['data_movement_bytes']['added_copy_bytes'] if e and e.get('valid') else '',
               'v2_makespan': v2e['result']['makespan'] if v2e and v2e.get('valid') else ''}
        if row['official_makespan'] != '' and row['v2_makespan'] != '':
            row['beats_v2'] = row['official_makespan'] < row['v2_makespan']
            row['gap_vs_v2_pct'] = 100 * (row['official_makespan'] / row['v2_makespan'] - 1)
        srows.append(row)
    write_csv(OUT / 'v1_supplement57_vs_v2.csv', srows)
    summary['supplement'] = {p: dict(zip(('best', 'rows', 'formal_rows'), (None,) + v1_supplement(p)[1:]))
                             for p in (1, 2, 3)}
    summary['supplement57'] = {'n': len(srows), 'valid': sum(r['official_valid'] for r in srows),
                               'match_report': sum(r['official_makespan'] == r['reported_makespan'] for r in srows),
                               'beats_v2': [(r['question'], r['case'], r['cores'], r['official_makespan'], r['v2_makespan'])
                                            for r in srows if r.get('beats_v2')]}
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    for s in all_sum:
        print('P{problem} K{cores}: n={n_compared} V1={v1_mean_speedup:.4f} V2={v2_mean_speedup:.4f} '
              'merged={merged_mean_speedup:.4f} W/T/L(V2)={v2_wins}/{ties}/{v1_wins}'.format(**s))


def _count(it):
    d = defaultdict(int)
    for x in it:
        d[x] += 1
    return d


def _brief(r):
    return {'case': r['case'], 'cores': r['cores'], 'v1': r['v1_makespan'], 'v2': r['v2_makespan'],
            'ratio_v1_over_v2': round(r['makespan_ratio_v1_over_v2'], 4),
            'v1_speedup': round(r['v1_speedup'], 3), 'v2_speedup': round(r['v2_speedup'], 3)}


if __name__ == '__main__':
    main()
