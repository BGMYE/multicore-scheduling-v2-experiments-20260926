"""问题三基线：全部由官方 multicore_cut_evaluate_problem_3.py 命令行（子进程、未打补丁）评估。

kind:
  single  : 1 核、全图 1 个子图（题目单核口径）在只读 Cache 配置下的 Makespan（K=1 的 Cache 点）
  stub    : 官方 stub（-n K，seed 0）随机方案
  greedy  : 问题一简单贪心方案（results_p1/baselines/greedy_k{K}，只读）
  pB      : 问题二最终方案（results_p2/k{K}，只读）→ T_C(P_B)
  p1plan  : 问题一最终方案（results_p1/k{K}，只读）

    python solution_p3/run_baselines_p3.py --kinds single stub greedy pB p1plan --workers 4
结果写入 results_p3/baselines/{kind}_k{K}/<case>.json（已存在则跳过）。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from common_c import CONFIG_PATH, DATA_DIR, OFFICIAL_CODE, RESULTS_DIR, RESULTS_P1, RESULTS_P2

BASE_DIR = RESULTS_DIR / 'baselines'


def official_p3(case, plan_path):
    """官方问题三命令行；返回精简结果与日志文本。"""
    with tempfile.TemporaryDirectory() as tmp:
        res = os.path.join(tmp, 'res.json')
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, str(OFFICIAL_CODE / 'multicore_cut_evaluate_problem_3.py'),
             str(DATA_DIR / (case + '.json')), str(plan_path), '--config', str(CONFIG_PATH),
             '-o', res, '--trace-output', os.path.join(tmp, 't.json'),
             '--log-output', os.path.join(tmp, 'log.txt')],
            capture_output=True, text=True, cwd=str(OFFICIAL_CODE.parent))
        dt = time.time() - t0
        if proc.returncode != 0:
            raise RuntimeError((proc.stdout + proc.stderr)[-1500:])
        with open(res, encoding='utf-8') as handle:
            r = json.load(handle)
        log_text = open(os.path.join(tmp, 'log.txt'), encoding='utf-8').read()
    cs = r['cache_stats']
    return {'makespan': r['makespan'], 'num_cores': r['num_cores'],
            'data_movement_bytes': r['data_movement_bytes'],
            'cache_stats': {k: cs[k] for k in ('hits', 'accesses', 'hit_bytes', 'miss_bytes',
                                               'hit_rate', 'copy_in_hits', 'copy_in_misses')},
            'cache_inserts': sum(1 for e in r['cache_events'] if e['event'] == 'insert'),
            'cache_evictions': sum(len(e.get('evicted_tensor_ids', ()))
                                   for e in r['cache_events'] if e['event'] == 'insert'),
            'num_subgraphs': sum(len(c['subgraphs']) for c in r['per_core_timeline']),
            'cross_core_transfers': len(r['cross_core_transfers']),
            'stdout': proc.stdout.strip(), 'eval_seconds': round(dt, 2)}, log_text


def single_plan(case, tmp):
    with open(DATA_DIR / (case + '.json'), encoding='utf-8') as handle:
        g = json.load(handle)
    ops = [op['id'] for op in g['ops'] if op['op'] not in ('COPY_IN', 'COPY_OUT')]
    path = os.path.join(tmp, 'single.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'node_to_subgraph': {str(v): 0 for v in ops}, 'core_schedules': [[0]]}, handle)
    return path


def job(case, k, kind):
    out = BASE_DIR / '{}_k{}'.format(kind, k) / (case + '.json')
    try:
        json.load(open(out, encoding='utf-8'))
        return case, k, kind, 'cached'
    except (OSError, ValueError):
        pass
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        if kind == 'single':
            plan = single_plan(case, tmp)
        elif kind == 'stub':
            plan = os.path.join(tmp, 'stub.json')
            proc = subprocess.run(
                [sys.executable, str(OFFICIAL_CODE / 'stub_multicore_cut_and_schedule.py'),
                 str(DATA_DIR / (case + '.json')), '-n', str(k), '-o', plan],
                capture_output=True, text=True)
            if proc.returncode != 0:
                return case, k, kind, 'STUB FAILED'
        elif kind == 'greedy':
            plan = RESULTS_P1 / 'baselines' / 'greedy_k{}'.format(k) / (case + '_multicore_res.json')
        elif kind == 'pB':
            plan = RESULTS_P2 / 'k{}'.format(k) / (case + '_multicore_res.json')
        elif kind == 'p1plan':
            plan = RESULTS_P1 / 'k{}'.format(k) / (case + '_multicore_res.json')
        else:
            raise ValueError(kind)
        try:
            data, _ = official_p3(case, plan)
        except RuntimeError as error:
            out.with_suffix('.error.txt').write_text(str(error), encoding='utf-8')
            return case, k, kind, 'FAILED'
    out.write_text(json.dumps(data, indent=1), encoding='utf-8')
    return case, k, kind, data['makespan']


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--kinds', nargs='*', default=['single', 'pB', 'stub', 'greedy', 'p1plan'])
    p.add_argument('--cores', type=int, nargs='*', default=[2, 3, 4, 5])
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--cases', nargs='*')
    a = p.parse_args()
    cases = a.cases or sorted(x.stem for x in DATA_DIR.glob('case_*.json'))
    cases.sort(key=lambda c: -(DATA_DIR / (c + '.json')).stat().st_size)
    jobs = []
    with ProcessPoolExecutor(a.workers) as pool:
        for kind in a.kinds:
            ks = [1] if kind == 'single' else a.cores
            for c in cases:
                for k in ks:
                    jobs.append(pool.submit(job, c, k, kind))
        for f in as_completed(jobs):
            print(*f.result(), flush=True)


if __name__ == '__main__':
    main()
