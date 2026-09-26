"""一条命令复现问题一全部结果。

    .venv/bin/python solution_p1/run_all.py              # 全部算例 × K=2..5
    .venv/bin/python solution_p1/run_all.py --cases case_001 case_064 --cores 4

步骤：
  1. solve   ：对每个 (算例, K) 调用 solve_p1.solve（子进程池并行），方案写入
               results_p1/k{K}/<case>_multicore_res.json（官方提交格式）；
  1b. monotone：若 K-1 核方案更优则 K 核沿用（补空核心），保证结果随核数单调不增；
  2. verify  ：用官方 multicore_cut_evaluate_problem_1.py 命令行逐个评估，
               精简结果与日志写入 results_p1/official_eval/k{K}/；
  3. baseline：（可选）官方单核基准与 stub 基线，见 run_baselines.py；
  4. summary ：汇总为 results_p1/summary.csv 与 results_p1/summary_by_k.csv。
已存在的方案/评估结果会被跳过（--force 重算）。
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

from common import CONFIG_PATH, DATA_DIR, OFFICIAL_CODE, RESULTS_DIR

SOLVER_LOGS = RESULTS_DIR / 'solver_logs'
OFFICIAL_DIR = RESULTS_DIR / 'official_eval'
BASE_DIR = RESULTS_DIR / 'baselines'


def budget_for(case, scale=1.0):
    """按计算节点数给出单次求解时间预算（秒）：40 s 起，最大 300 s。"""
    with open(DATA_DIR / (case + '.json'), encoding='utf-8') as handle:
        g = json.load(handle)
    n = sum(1 for op in g['ops'] if op['op'] not in ('COPY_IN', 'COPY_OUT'))
    return min(300.0, 40.0 + n / 100.0) * scale


def plan_path(case, k):
    return RESULTS_DIR / 'k{}'.format(k) / (case + '_multicore_res.json')


def job_solve(case, k, scale, force):
    out = plan_path(case, k)
    log = SOLVER_LOGS / 'k{}'.format(k) / (case + '.json')
    if out.exists() and log.exists() and not force:
        return case, k, 'cached'
    from solve_p1 import solve
    t0 = time.time()
    _, summary = solve(DATA_DIR / (case + '.json'), k, budget_for(case, scale), out, log,
                       verbose=False)
    return case, k, 'makespan={} ({:.0f}s)'.format(summary['makespan_fast_eval'], time.time() - t0)


def job_verify(case, k, force):
    out = OFFICIAL_DIR / 'k{}'.format(k) / (case + '.json')
    if out.exists() and not force:
        return case, k, 'cached'
    plan = plan_path(case, k)
    with tempfile.TemporaryDirectory() as tmp:
        res = os.path.join(tmp, 'res.json')
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, str(OFFICIAL_CODE / 'multicore_cut_evaluate_problem_1.py'),
             str(DATA_DIR / (case + '.json')), str(plan), '--config', str(CONFIG_PATH),
             '-o', res, '--trace-output', os.path.join(tmp, 'trace.json'),
             '--log-output', os.path.join(tmp, 'log.txt')],
            capture_output=True, text=True, cwd=str(OFFICIAL_CODE.parent))
        dt = time.time() - t0
        out.parent.mkdir(parents=True, exist_ok=True)
        if proc.returncode != 0:
            out.with_suffix('.error.txt').write_text(proc.stdout + proc.stderr, encoding='utf-8')
            return case, k, 'OFFICIAL EVALUATION FAILED'
        with open(res, encoding='utf-8') as handle:
            r = json.load(handle)
        Path(out.with_name(case + '_problem_1_log.txt')).write_text(
            Path(tmp, 'log.txt').read_text(encoding='utf-8'), encoding='utf-8')
    data = {'makespan': r['makespan'], 'num_cores': r['num_cores'],
            'data_movement_bytes': r['data_movement_bytes'],
            'num_subgraphs': len(r['step3_by_task']),
            'memory_peak_by_core': r['memory_peak_by_core'],
            'stdout': proc.stdout.strip(), 'eval_seconds': round(dt, 2)}
    out.write_text(json.dumps(data, indent=1), encoding='utf-8')
    return case, k, 'official makespan={}'.format(r['makespan'])


def enforce_monotone(cases, cores):
    """核数单调性：若 (K-1) 核方案的 (Makespan, 新增搬运) 更好，则 K 核直接沿用它，
    并补一个空核心列表（官方格式允许空列表；多出的空核心不改变仿真结果）。"""
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
            val = (s['makespan_fast_eval'], s['added_copy_bytes_fast_eval'])
            if prev is not None and prev[0] < val and prev[1] == k - 1:
                plan = load_json(plan_path(case, k - 1))
                plan['core_schedules'] = plan['core_schedules'] + [[]] * (k - len(plan['core_schedules']))
                with open(plan_path(case, k), 'w', encoding='utf-8') as handle:
                    json.dump(plan, handle, indent=1)
                s['own_makespan_fast_eval'] = s['makespan_fast_eval']
                s['own_added_copy_bytes_fast_eval'] = s['added_copy_bytes_fast_eval']
                s['makespan_fast_eval'], s['added_copy_bytes_fast_eval'] = prev[0]
                s['inherited_from_cores'] = s.get('inherited_from_cores') or k - 1
                log.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
                stale = OFFICIAL_DIR / 'k{}'.format(k) / (case + '.json')
                if stale.exists():
                    stale.unlink()
                changed += 1
                val = prev[0]
            prev = (val, k)
    print('[monotone] plans inherited from K-1:', changed)


def load_json(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def summarize(cases, cores):
    rows = []
    for case in cases:
        single = load_json(BASE_DIR / 'singlecore' / (case + '.json'))
        for k in cores:
            off = load_json(OFFICIAL_DIR / 'k{}'.format(k) / (case + '.json'))
            sol = load_json(SOLVER_LOGS / 'k{}'.format(k) / (case + '.json'))
            stub = load_json(BASE_DIR / 'stub_k{}'.format(k) / (case + '.json'))
            s = (sol or {}).get('summary', {})
            port = {p['tag']: p for p in s.get('portfolio', [])}
            gb = load_json(BASE_DIR / 'greedy_k{}'.format(k) / (case + '.json'))
            greedy = ({'makespan': gb['makespan'], 'added': gb['added_copy_bytes']} if gb
                      else port.get('interval-dfs/1'))
            row = {
                'case': case, 'cores': k,
                'makespan': off['makespan'] if off else '',
                'added_copy_bytes': off['data_movement_bytes']['added_copy_bytes'] if off else '',
                'partition_added_bytes': off['data_movement_bytes']['partition_added_copy_bytes'] if off else '',
                'spill_added_bytes': off['data_movement_bytes']['spill_added_copy_bytes'] if off else '',
                'num_subgraphs': off['num_subgraphs'] if off else '',
                'official_valid': bool(off),
                'fast_eval_match': (bool(off) and s.get('makespan_fast_eval') == off['makespan']
                                    and s.get('added_copy_bytes_fast_eval') ==
                                    off['data_movement_bytes']['added_copy_bytes']),
                'singlecore_makespan': single['makespan'] if single else '',
                'speedup': round(single['makespan'] / off['makespan'], 4) if single and off else '',
                'stub_makespan': stub['makespan'] if stub else '',
                'stub_added_bytes': stub['data_movement_bytes']['added_copy_bytes'] if stub else '',
                'stub_speedup': round(single['makespan'] / stub['makespan'], 4) if single and stub else '',
                'greedy_makespan': greedy['makespan'] if greedy else '',
                'greedy_added_bytes': greedy['added'] if greedy else '',
                'construct_best': s.get('initial_constructor', ''),
                'construct_makespan': s.get('initial_makespan', ''),
                'lower_bound_LB0': round(s['lower_bound_LB0']) if s.get('lower_bound_LB0') else '',
                'solve_seconds': s.get('total_seconds', ''),
                'cpsat_windows': (s.get('window_stats') or {}).get('windows', ''),
                'cpsat_improved': (s.get('window_stats') or {}).get('improved', ''),
                'inherited_from_cores': s.get('inherited_from_cores', ''),
                'official_eval_seconds': off['eval_seconds'] if off else '',
            }
            rows.append(row)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_DIR / 'summary.csv', 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    # 按核数的平均加速比（逐图加速比的算术平均，题目口径）
    agg = []
    agg.append({'cores': 1, 'n_cases': len(cases), 'mean_speedup': 1.0, 'mean_stub_speedup': 1.0,
                'mean_greedy_speedup': 1.0, 'all_valid': True, 'mean_added_bytes': 0})
    for k in cores:
        rk = [r for r in rows if r['cores'] == k and r['speedup'] != '']
        rs = [r for r in rows if r['cores'] == k and r['stub_speedup'] != '']
        rg = [r for r in rows if r['cores'] == k and r['greedy_makespan'] != '' and r['singlecore_makespan'] != '']
        agg.append({
            'cores': k, 'n_cases': len(rk),
            'mean_speedup': round(sum(r['speedup'] for r in rk) / max(1, len(rk)), 4),
            'mean_stub_speedup': round(sum(r['stub_speedup'] for r in rs) / max(1, len(rs)), 4),
            'mean_greedy_speedup': round(sum(r['singlecore_makespan'] / r['greedy_makespan'] for r in rg)
                                         / max(1, len(rg)), 4),
            'all_valid': all(r['official_valid'] for r in rows if r['cores'] == k),
            'mean_added_bytes': round(sum(r['added_copy_bytes'] for r in rk) / max(1, len(rk))),
        })
    with open(RESULTS_DIR / 'summary_by_k.csv', 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(agg[0]))
        writer.writeheader()
        writer.writerows(agg)
    for a in agg:
        print(a)
    return rows, agg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', nargs='*')
    parser.add_argument('--cores', type=int, nargs='*', default=[2, 3, 4, 5])
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--budget-scale', type=float, default=1.0)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--skip-solve', action='store_true')
    parser.add_argument('--skip-verify', action='store_true')
    parser.add_argument('--baselines', action='store_true', help='同时运行官方单核与 stub 基线')
    parser.add_argument('--ascending', action='store_true', help='小算例优先（便于双进程分头跑）')
    args = parser.parse_args()
    cases = args.cases or sorted(p.stem for p in DATA_DIR.glob('case_*.json'))
    by_size = sorted(cases, key=lambda c: -(DATA_DIR / (c + '.json')).stat().st_size)
    if args.ascending:
        by_size.reverse()
    if not args.skip_solve:
        with ProcessPoolExecutor(args.workers) as pool:
            futs = [pool.submit(job_solve, c, k, args.budget_scale, args.force)
                    for c in by_size for k in args.cores]
            for f in as_completed(futs):
                print('[solve]', *f.result(), flush=True)
    enforce_monotone(cases, args.cores)
    if not args.skip_verify:
        with ProcessPoolExecutor(args.workers) as pool:
            futs = [pool.submit(job_verify, c, k, args.force) for c in by_size for k in args.cores]
            for f in as_completed(futs):
                print('[verify]', *f.result(), flush=True)
    if args.baselines:
        subprocess.run([sys.executable, str(Path(__file__).with_name('run_baselines.py')),
                        '--cores'] + [str(k) for k in args.cores] +
                       ['--workers', str(args.workers), '--cases'] + cases, check=False)
    summarize(cases, args.cores)


if __name__ == '__main__':
    main()
