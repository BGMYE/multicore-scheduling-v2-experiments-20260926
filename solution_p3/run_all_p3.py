"""一条命令复现问题三全部结果（含方案文档 3.5 节四组配对对照）。

    .venv/bin/python solution_p3/run_all_p3.py --workers 6

步骤：
  1. solve    ：每个 (算例, K∈{2..5}) 以问题二最终方案 P_B 为初值调用 solve_p3.solve，
                得问题三方案 P_C → results_p3/k{K}/<case>_multicore_res.json；
  2. monotone ：K-1 核方案（补空核心）若 T_C 更优则沿用；
  3. verify   ：官方问题三命令行复核 T_C(P_C)（results_p3/official_eval/k{K}）；
                官方问题二命令行评估 T_B(P_C)（results_p3/pc_under_b/k{K}）；
  4. baseline ：run_baselines_p3（单核@Cache、P_B@Cache=T_C(P_B)、stub、贪心、问题一方案）；
                T_B(P_B) 取 results_p2/official_eval（只读）；无 L2 单核基准取 results_p1/baselines/singlecore；
  5. summary  ：results_p3/summary.csv、summary_by_k.csv。
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from common_c import CONFIG_PATH, DATA_DIR, OFFICIAL_CODE, RESULTS_DIR, RESULTS_P1, RESULTS_P2
from run_baselines_p3 import BASE_DIR, job as baseline_job, official_p3

SOLVER_LOGS = RESULTS_DIR / 'solver_logs'
OFFICIAL_DIR = RESULTS_DIR / 'official_eval'
PC_B_DIR = RESULTS_DIR / 'pc_under_b'


def budget_for(case, scale=1.0):
    """问题三预算：min(180, 30 + 计算节点数/150) 秒（窗口候选收益有限，比问题二略小）。"""
    with open(DATA_DIR / (case + '.json'), encoding='utf-8') as handle:
        g = json.load(handle)
    n = sum(1 for op in g['ops'] if op['op'] not in ('COPY_IN', 'COPY_OUT'))
    return min(180.0, 30.0 + n / 150.0) * scale


def plan_path(case, k):
    return RESULTS_DIR / 'k{}'.format(k) / (case + '_multicore_res.json')


def load_json(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def is_complete_plan(case, k):
    """断点续跑判据：方案与求解日志都存在且是完整 JSON（写到一半的文件视为未完成）。"""
    plan = load_json(plan_path(case, k))
    log = load_json(SOLVER_LOGS / 'k{}'.format(k) / (case + '.json'))
    return (isinstance(plan, dict) and set(plan) == {'node_to_subgraph', 'core_schedules'}
            and len(plan['core_schedules']) == k and isinstance(log, dict)
            and 'makespan_eval' in log.get('summary', {}))


def job_solve(case, k, scale, force):
    out, log = plan_path(case, k), SOLVER_LOGS / 'k{}'.format(k) / (case + '.json')
    if not force and is_complete_plan(case, k):
        return case, k, 'cached'
    from solve_p3 import solve
    t0 = time.time()
    _, s = solve(DATA_DIR / (case + '.json'), k, budget_for(case, scale), out, log, verbose=False)
    return case, k, 'T_C={} (T_C(P_B)={}, {:.0f}s)'.format(
        s['makespan_eval'], s['init_makespan_TC_PB'], time.time() - t0)


def official_p2(case, plan):
    with tempfile.TemporaryDirectory() as tmp:
        res = os.path.join(tmp, 'res.json')
        proc = subprocess.run(
            [sys.executable, str(OFFICIAL_CODE / 'multicore_cut_evaluate_problem_2.py'),
             str(DATA_DIR / (case + '.json')), str(plan), '--config', str(CONFIG_PATH),
             '-o', res, '--trace-output', os.path.join(tmp, 't.json'),
             '--log-output', os.path.join(tmp, 'l.txt')],
            capture_output=True, text=True, cwd=str(OFFICIAL_CODE.parent))
        if proc.returncode != 0:
            raise RuntimeError((proc.stdout + proc.stderr)[-1500:])
        r = json.load(open(res, encoding='utf-8'))
    return {'makespan': r['makespan'], 'data_movement_bytes': r['data_movement_bytes']}


def job_verify(case, k, force):
    out = OFFICIAL_DIR / 'k{}'.format(k) / (case + '.json')
    out_b = PC_B_DIR / 'k{}'.format(k) / (case + '.json')
    msg = []
    if force or not isinstance(load_json(out), dict):
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            data, log_text = official_p3(case, plan_path(case, k))
            out.write_text(json.dumps(data, indent=1), encoding='utf-8')
            out.with_name(case + '_problem_3_log.txt').write_text(log_text, encoding='utf-8')
            msg.append('T_C={}'.format(data['makespan']))
        except RuntimeError as error:
            out.with_suffix('.error.txt').write_text(str(error), encoding='utf-8')
            msg.append('OFFICIAL P3 FAILED')
    if force or not isinstance(load_json(out_b), dict):
        out_b.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = official_p2(case, plan_path(case, k))
            out_b.write_text(json.dumps(data, indent=1), encoding='utf-8')
            msg.append('T_B(P_C)={}'.format(data['makespan']))
        except RuntimeError as error:
            out_b.with_suffix('.error.txt').write_text(str(error), encoding='utf-8')
            msg.append('OFFICIAL P2 FAILED')
    return case, k, ' '.join(msg) or 'cached'


def enforce_monotone(cases, cores):
    changed = 0
    for case in cases:
        prev = None
        for k in sorted(cores):
            log = SOLVER_LOGS / 'k{}'.format(k) / (case + '.json')
            data = load_json(log)
            if data is None:
                prev = None
                continue
            s = data['summary']
            val = (s['makespan_eval'], s['added_copy_bytes_eval'])
            if prev is not None and prev[0] < val and prev[1] == k - 1:
                plan = load_json(plan_path(case, k - 1))
                plan['core_schedules'] = plan['core_schedules'] + [[]] * (k - len(plan['core_schedules']))
                with open(plan_path(case, k), 'w', encoding='utf-8') as handle:
                    json.dump(plan, handle, indent=1)
                s['own_makespan_eval'], s['own_added_copy_bytes_eval'] = val
                s['makespan_eval'], s['added_copy_bytes_eval'] = prev[0]
                s['inherited_from_cores'] = s.get('inherited_from_cores') or k - 1
                log.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
                for stale in (OFFICIAL_DIR / 'k{}'.format(k) / (case + '.json'),
                              PC_B_DIR / 'k{}'.format(k) / (case + '.json')):
                    if stale.exists():
                        stale.unlink()
                changed += 1
                val = prev[0]
            prev = (val, k)
    print('[monotone] plans inherited from K-1:', changed)


def summarize(cases, cores):
    rows = []
    for case in cases:
        single_b = load_json(RESULTS_P1 / 'baselines' / 'singlecore' / (case + '.json'))
        single_c = load_json(BASE_DIR / 'single_k1' / (case + '.json'))
        sb = single_b['makespan'] if single_b else None
        for k in cores:
            pc = load_json(OFFICIAL_DIR / 'k{}'.format(k) / (case + '.json'))
            pcb = load_json(PC_B_DIR / 'k{}'.format(k) / (case + '.json'))
            pbb = load_json(RESULTS_P2 / 'official_eval' / 'k{}'.format(k) / (case + '.json'))
            pbc = load_json(BASE_DIR / 'pB_k{}'.format(k) / (case + '.json'))
            sol = (load_json(SOLVER_LOGS / 'k{}'.format(k) / (case + '.json')) or {}).get('summary', {})
            base = {kind: load_json(BASE_DIR / '{}_k{}'.format(kind, k) / (case + '.json'))
                    for kind in ('stub', 'greedy', 'p1plan')}

            def ms(x):
                return x['makespan'] if x else ''
            T_BB, T_CB, T_BC, T_CC = ms(pbb), ms(pbc), ms(pcb), ms(pc)
            row = {
                'case': case, 'cores': k,
                'T_B_PB': T_BB, 'T_C_PB': T_CB, 'T_B_PC': T_BC, 'T_C_PC': T_CC,
                'S_hardware': round(T_BB / T_CB, 4) if T_BB and T_CB else '',
                'S_schedule': round(T_CB / T_CC, 4) if T_CB and T_CC else '',
                'S_overall': round(T_BB / T_CC, 4) if T_BB and T_CC else '',
                'PC_regress_without_L2': round(T_BC / T_BB, 4) if T_BB and T_BC else '',
                'added_copy_bytes_PC': pc['data_movement_bytes']['added_copy_bytes'] if pc else '',
                'added_copy_bytes_PB': pbc['data_movement_bytes']['added_copy_bytes'] if pbc else '',
                'hit_rate_PC': round(pc['cache_stats']['hit_rate'], 4) if pc else '',
                'hit_rate_PB': round(pbc['cache_stats']['hit_rate'], 4) if pbc else '',
                'hits_PC': pc['cache_stats']['hits'] if pc else '',
                'accesses_PC': pc['cache_stats']['accesses'] if pc else '',
                'hit_bytes_PC': pc['cache_stats']['hit_bytes'] if pc else '',
                'miss_bytes_PC': pc['cache_stats']['miss_bytes'] if pc else '',
                'evictions_PC': pc['cache_evictions'] if pc else '',
                'official_valid': bool(pc) and bool(pcb),
                'eval_match': bool(pc) and sol.get('makespan_eval') == pc['makespan'] and
                sol.get('added_copy_bytes_eval') == pc['data_movement_bytes']['added_copy_bytes'],
                'singlecore_noL2': sb or '', 'singlecore_cache': ms(single_c),
                'speedup_noL2': round(sb / T_BB, 4) if sb and T_BB else '',
                'speedup_cache': round(sb / T_CC, 4) if sb and T_CC else '',
            }
            for kind in ('stub', 'greedy', 'p1plan'):
                b = base[kind]
                row[kind + '_T_C'] = ms(b)
                row[kind + '_hit_rate'] = round(b['cache_stats']['hit_rate'], 4) if b else ''
                row[kind + '_speedup_cache'] = round(sb / b['makespan'], 4) if sb and b else ''
            row.update({
                'solve_seconds': sol.get('total_seconds', ''),
                'improve_accepted': (sol.get('improve_stats') or {}).get('accepted', ''),
                'improve_evals': (sol.get('improve_stats') or {}).get('evals', ''),
                'inherited_from_cores': sol.get('inherited_from_cores', ''),
                'official_eval_seconds': pc['eval_seconds'] if pc else '',
            })
            rows.append(row)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_DIR / 'summary.csv', 'w', newline='', encoding='utf-8') as handle:
        w = csv.DictWriter(handle, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    def mean(rs, key):
        vals = [r[key] for r in rs if r[key] != '']
        return round(sum(vals) / max(1, len(vals)), 4) if vals else ''
    singles = [(load_json(RESULTS_P1 / 'baselines' / 'singlecore' / (c + '.json')),
                load_json(BASE_DIR / 'single_k1' / (c + '.json'))) for c in cases]
    s1 = [a['makespan'] / b['makespan'] for a, b in singles if a and b]
    agg = [{'cores': 1, 'n_cases': len(s1), 'mean_speedup_noL2': 1.0,
            'mean_speedup_cache': round(sum(s1) / max(1, len(s1)), 4),
            'mean_S_hardware': round(sum(s1) / max(1, len(s1)), 4), 'mean_S_schedule': 1.0,
            'mean_S_overall': round(sum(s1) / max(1, len(s1)), 4),
            'mean_hit_rate_PB': '', 'mean_hit_rate_PC': '',
            'mean_stub_speedup_cache': '', 'mean_greedy_speedup_cache': '',
            'mean_p1plan_speedup_cache': '', 'all_valid': True}]
    for k in cores:
        rk = [r for r in rows if r['cores'] == k]
        agg.append({'cores': k, 'n_cases': sum(1 for r in rk if r['speedup_cache'] != ''),
                    'mean_speedup_noL2': mean(rk, 'speedup_noL2'),
                    'mean_speedup_cache': mean(rk, 'speedup_cache'),
                    'mean_S_hardware': mean(rk, 'S_hardware'),
                    'mean_S_schedule': mean(rk, 'S_schedule'),
                    'mean_S_overall': mean(rk, 'S_overall'),
                    'mean_hit_rate_PB': mean(rk, 'hit_rate_PB'),
                    'mean_hit_rate_PC': mean(rk, 'hit_rate_PC'),
                    'mean_stub_speedup_cache': mean(rk, 'stub_speedup_cache'),
                    'mean_greedy_speedup_cache': mean(rk, 'greedy_speedup_cache'),
                    'mean_p1plan_speedup_cache': mean(rk, 'p1plan_speedup_cache'),
                    'all_valid': all(r['official_valid'] for r in rk)})
    with open(RESULTS_DIR / 'summary_by_k.csv', 'w', newline='', encoding='utf-8') as handle:
        w = csv.DictWriter(handle, fieldnames=list(agg[0]))
        w.writeheader()
        w.writerows(agg)
    for a in agg:
        print(a)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--cases', nargs='*')
    p.add_argument('--cores', type=int, nargs='*', default=[2, 3, 4, 5])
    p.add_argument('--workers', type=int, default=6)
    p.add_argument('--budget-scale', type=float, default=1.0)
    p.add_argument('--force', action='store_true')
    p.add_argument('--skip-solve', action='store_true')
    p.add_argument('--skip-verify', action='store_true')
    p.add_argument('--skip-baselines', action='store_true')
    a = p.parse_args()
    cases = a.cases or sorted(x.stem for x in DATA_DIR.glob('case_*.json'))
    by_size = sorted(cases, key=lambda c: -(DATA_DIR / (c + '.json')).stat().st_size)
    if not a.skip_solve:
        with ProcessPoolExecutor(a.workers) as pool:
            futs = [pool.submit(job_solve, c, k, a.budget_scale, a.force) for c in by_size for k in a.cores]
            for f in as_completed(futs):
                print('[solve]', *f.result(), flush=True)
    enforce_monotone(cases, a.cores)
    with ProcessPoolExecutor(a.workers) as pool:
        futs = []
        if not a.skip_verify:
            futs += [pool.submit(job_verify, c, k, a.force) for c in by_size for k in a.cores]
        if not a.skip_baselines:
            futs += [pool.submit(baseline_job, c, 1, 'single') for c in by_size]
            futs += [pool.submit(baseline_job, c, k, kind)
                     for kind in ('pB', 'greedy', 'stub', 'p1plan') for c in by_size for k in a.cores]
        for f in as_completed(futs):
            print('[verify/baseline]', *f.result(), flush=True)
    summarize(cases, a.cores)


if __name__ == '__main__':
    main()
