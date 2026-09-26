"""一条命令复现问题二全部结果。

    .venv/bin/python solution_p2/run_all_p2.py --workers 6

步骤：
  1. solve    ：每个 (算例, K∈{2,3,4,5}) 调用 solve_p2.solve（进程池），方案写入
                results_p2/k{K}/<case>_multicore_res.json（官方提交格式）；
  2. monotone ：若 K-1 核方案（补一个空核心）更优则 K 核沿用，保证随核数单调；
  3. verify   ：官方 multicore_cut_evaluate_problem_2.py 命令行（子进程、未打补丁）逐个复核，
                精简结果与日志写入 results_p2/official_eval/k{K}/；
  4. baseline ：官方 stub（-n K, seed 0）→ 官方问题二评估；问题一最终方案 → 官方问题二评估；
                简单贪心（问题一 interval-dfs/1 方案）→ 官方问题二评估；
                单核基准沿用 results_p1/baselines/singlecore（只读；场景 B 单核单子图与之逐项相同，
                见 README）；
  5. summary  ：results_p2/summary.csv、summary_by_k.csv。
已存在的结果会被跳过（--force 重算）。
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

from common_b import CONFIG_PATH, DATA_DIR, OFFICIAL_CODE, RESULTS_DIR, RESULTS_P1

SOLVER_LOGS = RESULTS_DIR / 'solver_logs'
OFFICIAL_DIR = RESULTS_DIR / 'official_eval'
BASE_DIR = RESULTS_DIR / 'baselines'


def budget_for(case, scale=1.0):
    with open(DATA_DIR / (case + '.json'), encoding='utf-8') as handle:
        g = json.load(handle)
    n = sum(1 for op in g['ops'] if op['op'] not in ('COPY_IN', 'COPY_OUT'))
    return min(300.0, 40.0 + n / 100.0) * scale


def plan_path(case, k):
    return RESULTS_DIR / 'k{}'.format(k) / (case + '_multicore_res.json')


def load_json(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def job_solve(case, k, scale, force, p1_warm):
    out, log = plan_path(case, k), SOLVER_LOGS / 'k{}'.format(k) / (case + '.json')
    if out.exists() and log.exists() and not force:
        return case, k, 'cached'
    from solve_p2 import solve
    t0 = time.time()
    _, s = solve(DATA_DIR / (case + '.json'), k, budget_for(case, scale), out, log,
                 verbose=False, p1_warm=p1_warm)
    return case, k, 'makespan={} ({:.0f}s)'.format(s['makespan_eval'], time.time() - t0)


def official_p2(case, plan):
    """官方问题二命令行评估（子进程）；返回精简结果或抛错。"""
    with tempfile.TemporaryDirectory() as tmp:
        res = os.path.join(tmp, 'res.json')
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, str(OFFICIAL_CODE / 'multicore_cut_evaluate_problem_2.py'),
             str(DATA_DIR / (case + '.json')), str(plan), '--config', str(CONFIG_PATH),
             '-o', res, '--trace-output', os.path.join(tmp, 't.json'),
             '--log-output', os.path.join(tmp, 'log.txt')],
            capture_output=True, text=True, cwd=str(OFFICIAL_CODE.parent))
        dt = time.time() - t0
        if proc.returncode != 0:
            raise RuntimeError((proc.stdout + proc.stderr)[-1500:])
        with open(res, encoding='utf-8') as handle:
            r = json.load(handle)
        log_text = Path(tmp, 'log.txt').read_text(encoding='utf-8')
    return {'makespan': r['makespan'], 'num_cores': r['num_cores'],
            'data_movement_bytes': r['data_movement_bytes'],
            'num_subgraphs': sum(len(c['subgraphs']) for c in r['per_core_timeline']),
            'cross_core_transfers': len(r['cross_core_transfers']),
            'memory_peak_by_core': r['memory_peak_by_core'],
            'stdout': proc.stdout.strip(), 'eval_seconds': round(dt, 2)}, log_text


def job_verify(case, k, force):
    out = OFFICIAL_DIR / 'k{}'.format(k) / (case + '.json')
    if out.exists() and not force:
        return case, k, 'cached'
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        data, log_text = official_p2(case, plan_path(case, k))
    except RuntimeError as error:
        out.with_suffix('.error.txt').write_text(str(error), encoding='utf-8')
        return case, k, 'OFFICIAL EVALUATION FAILED'
    out.write_text(json.dumps(data, indent=1), encoding='utf-8')
    out.with_name(case + '_problem_2_log.txt').write_text(log_text, encoding='utf-8')
    return case, k, 'official makespan={}'.format(data['makespan'])


def job_baseline(case, k, kind, force):
    out = BASE_DIR / '{}_k{}'.format(kind, k) / (case + '.json')
    if out.exists() and not force:
        return case, k, kind, 'cached'
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        if kind == 'stub':
            plan = os.path.join(tmp, 'plan.json')
            proc = subprocess.run(
                [sys.executable, str(OFFICIAL_CODE / 'stub_multicore_cut_and_schedule.py'),
                 str(DATA_DIR / (case + '.json')), '-n', str(k), '-o', plan],
                capture_output=True, text=True)
            if proc.returncode != 0:
                return case, k, kind, 'STUB FAILED'
        elif kind == 'p1plan':
            plan = RESULTS_P1 / 'k{}'.format(k) / (case + '_multicore_res.json')
        elif kind == 'greedy':
            plan = RESULTS_P1 / 'baselines' / 'greedy_k{}'.format(k) / (case + '_multicore_res.json')
        else:
            raise ValueError(kind)
        try:
            data, _ = official_p2(case, plan)
        except RuntimeError as error:
            out.with_suffix('.error.txt').write_text(str(error), encoding='utf-8')
            return case, k, kind, 'FAILED'
    out.write_text(json.dumps(data, indent=1), encoding='utf-8')
    return case, k, kind, data['makespan']


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
                stale = OFFICIAL_DIR / 'k{}'.format(k) / (case + '.json')
                if stale.exists():
                    stale.unlink()
                changed += 1
                val = prev[0]
            prev = (val, k)
    print('[monotone] plans inherited from K-1:', changed)


def summarize(cases, cores):
    rows = []
    for case in cases:
        single = load_json(RESULTS_P1 / 'baselines' / 'singlecore' / (case + '.json'))
        for k in cores:
            off = load_json(OFFICIAL_DIR / 'k{}'.format(k) / (case + '.json'))
            sol = load_json(SOLVER_LOGS / 'k{}'.format(k) / (case + '.json'))
            s = (sol or {}).get('summary', {})
            base = {kind: load_json(BASE_DIR / '{}_k{}'.format(kind, k) / (case + '.json'))
                    for kind in ('stub', 'p1plan', 'greedy')}
            sc = single['makespan'] if single else None

            def sp(x):
                return round(sc / x['makespan'], 4) if sc and x else ''
            row = {
                'case': case, 'cores': k,
                'makespan': off['makespan'] if off else '',
                'added_copy_bytes': off['data_movement_bytes']['added_copy_bytes'] if off else '',
                'partition_added_bytes': off['data_movement_bytes']['partition_added_copy_bytes'] if off else '',
                'spill_added_bytes': off['data_movement_bytes']['spill_added_copy_bytes'] if off else '',
                'num_subgraphs': off['num_subgraphs'] if off else '',
                'cross_core_transfers': off['cross_core_transfers'] if off else '',
                'official_valid': bool(off),
                'eval_match': bool(off) and s.get('makespan_eval') == off['makespan'] and
                s.get('added_copy_bytes_eval') == off['data_movement_bytes']['added_copy_bytes'],
                'singlecore_makespan': sc or '',
                'speedup': sp(off),
            }
            for kind in ('p1plan', 'stub', 'greedy'):
                b = base[kind]
                row[kind + '_makespan'] = b['makespan'] if b else ''
                row[kind + '_added_bytes'] = b['data_movement_bytes']['added_copy_bytes'] if b else ''
                row[kind + '_speedup'] = sp(b)
            row.update({
                'construct_best': s.get('initial_constructor', ''),
                'construct_makespan': s.get('initial_makespan', ''),
                'lower_bound_LB0': round(s['lower_bound_LB0']) if s.get('lower_bound_LB0') else '',
                'solve_seconds': s.get('total_seconds', ''),
                'improve_accepted': (s.get('improve_stats') or {}).get('accepted', ''),
                'improve_evals': (s.get('improve_stats') or {}).get('evals', ''),
                'inherited_from_cores': s.get('inherited_from_cores', ''),
                'official_eval_seconds': off['eval_seconds'] if off else '',
            })
            rows.append(row)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_DIR / 'summary.csv', 'w', newline='', encoding='utf-8') as handle:
        w = csv.DictWriter(handle, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    agg = [{'cores': 1, 'n_cases': len(cases), 'mean_speedup': 1.0, 'mean_p1plan_speedup': 1.0,
            'mean_stub_speedup': 1.0, 'mean_greedy_speedup': 1.0, 'all_valid': True,
            'mean_added_bytes': 0}]
    for k in cores:
        rk = [r for r in rows if r['cores'] == k]

        def mean(key):
            vals = [r[key] for r in rk if r[key] != '']
            return round(sum(vals) / max(1, len(vals)), 4)
        agg.append({'cores': k, 'n_cases': sum(1 for r in rk if r['speedup'] != ''),
                    'mean_speedup': mean('speedup'), 'mean_p1plan_speedup': mean('p1plan_speedup'),
                    'mean_stub_speedup': mean('stub_speedup'),
                    'mean_greedy_speedup': mean('greedy_speedup'),
                    'all_valid': all(r['official_valid'] for r in rk),
                    'mean_added_bytes': round(mean('added_copy_bytes'))})
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
    p.add_argument('--no-p1-warm', action='store_true')
    p.add_argument('--skip-solve', action='store_true')
    p.add_argument('--skip-verify', action='store_true')
    p.add_argument('--skip-baselines', action='store_true')
    a = p.parse_args()
    cases = a.cases or sorted(x.stem for x in DATA_DIR.glob('case_*.json'))
    by_size = sorted(cases, key=lambda c: -(DATA_DIR / (c + '.json')).stat().st_size)
    if not a.skip_solve:
        with ProcessPoolExecutor(a.workers) as pool:
            futs = [pool.submit(job_solve, c, k, a.budget_scale, a.force, not a.no_p1_warm)
                    for c in by_size for k in a.cores]
            for f in as_completed(futs):
                print('[solve]', *f.result(), flush=True)
    enforce_monotone(cases, a.cores)
    with ProcessPoolExecutor(a.workers) as pool:
        futs = []
        if not a.skip_verify:
            futs += [pool.submit(job_verify, c, k, a.force) for c in by_size for k in a.cores]
        if not a.skip_baselines:
            futs += [pool.submit(job_baseline, c, k, kind, a.force)
                     for kind in ('p1plan', 'greedy', 'stub') for c in by_size for k in a.cores]
        for f in as_completed(futs):
            print('[verify/baseline]', *f.result(), flush=True)
    summarize(cases, a.cores)


if __name__ == '__main__':
    main()
