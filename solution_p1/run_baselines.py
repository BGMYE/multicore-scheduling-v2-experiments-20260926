"""运行官方基线并保存精简结果。

* 单核基准：官方 ``singlecore_evaluate.py``（整图一个子图、核心 0），这是题目
  规定的加速比分母；
* stub 基线：官方 ``stub_multicore_cut_and_schedule.py -n K``（seed=0，默认
  50~100 个节点一个子图、随机分核）生成方案，再由官方问题一评估器评估。

全部通过子进程调用官方命令行，结果写入 ``results_p1/baselines/``：

    python solution_p1/run_baselines.py --cores 2 3 4 5 --workers 4
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from common import CONFIG_PATH, DATA_DIR, OFFICIAL_CODE, RESULTS_DIR

BASE_DIR = RESULTS_DIR / 'baselines'


def trim_result(path):
    with open(path, encoding='utf-8') as handle:
        res = json.load(handle)
    return {
        'makespan': res['makespan'],
        'num_cores': res['num_cores'],
        'data_movement_bytes': res['data_movement_bytes'],
        'num_subgraphs': len(res.get('step3_by_task', {})),
        'memory_peak_by_core': res.get('memory_peak_by_core'),
    }


def run_official(args, timeout):
    t0 = time.time()
    proc = subprocess.run([sys.executable] + args, capture_output=True, text=True,
                          timeout=timeout, cwd=str(OFFICIAL_CODE.parent))
    return proc, time.time() - t0


def job_singlecore(case):
    out = BASE_DIR / 'singlecore' / (case + '.json')
    if out.exists():
        return case, 'singlecore', 'cached'
    with tempfile.TemporaryDirectory() as tmp:
        res = os.path.join(tmp, 'res.json')
        proc, dt = run_official([str(OFFICIAL_CODE / 'singlecore_evaluate.py'),
                                 str(DATA_DIR / (case + '.json')), '--config', str(CONFIG_PATH),
                                 '-o', res, '--trace-output', os.path.join(tmp, 't.json'),
                                 '--log-output', os.path.join(tmp, 'l.txt')], 7200)
        if proc.returncode != 0:
            return case, 'singlecore', 'FAILED ' + proc.stderr[-500:]
        data = trim_result(res)
    data['eval_seconds'] = round(dt, 2)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1), encoding='utf-8')
    return case, 'singlecore', data['makespan']


def job_stub(case, cores):
    out = BASE_DIR / 'stub_k{}'.format(cores) / (case + '.json')
    if out.exists():
        return case, 'stub_k{}'.format(cores), 'cached'
    with tempfile.TemporaryDirectory() as tmp:
        plan = os.path.join(tmp, 'plan.json')
        proc, _ = run_official([str(OFFICIAL_CODE / 'stub_multicore_cut_and_schedule.py'),
                                str(DATA_DIR / (case + '.json')), '-n', str(cores), '-o', plan], 3600)
        if proc.returncode != 0:
            return case, 'stub', 'FAILED ' + proc.stderr[-500:]
        res = os.path.join(tmp, 'res.json')
        proc, dt = run_official([str(OFFICIAL_CODE / 'multicore_cut_evaluate_problem_1.py'),
                                 str(DATA_DIR / (case + '.json')), plan, '--config', str(CONFIG_PATH),
                                 '-o', res, '--trace-output', os.path.join(tmp, 't.json'),
                                 '--log-output', os.path.join(tmp, 'l.txt')], 7200)
        if proc.returncode != 0:
            return case, 'stub_k{}'.format(cores), 'FAILED ' + proc.stderr[-500:]
        data = trim_result(res)
    data['eval_seconds'] = round(dt, 2)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1), encoding='utf-8')
    return case, 'stub_k{}'.format(cores), data['makespan']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cores', type=int, nargs='*', default=[2, 3, 4, 5])
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--cases', nargs='*')
    parser.add_argument('--skip-singlecore', action='store_true')
    parser.add_argument('--skip-stub', action='store_true')
    args = parser.parse_args()
    cases = args.cases or sorted(p.stem for p in DATA_DIR.glob('case_*.json'))
    # 大图先跑，缩短尾部等待
    cases.sort(key=lambda c: -(DATA_DIR / (c + '.json')).stat().st_size)
    jobs = []
    with ProcessPoolExecutor(args.workers) as pool:
        for case in cases:
            if not args.skip_singlecore:
                jobs.append(pool.submit(job_singlecore, case))
            if not args.skip_stub:
                for k in args.cores:
                    jobs.append(pool.submit(job_stub, case, k))
        for fut in as_completed(jobs):
            print(*fut.result(), flush=True)


if __name__ == '__main__':
    main()
