"""用同一份官方评估器（数据包/code 的命令行入口，未改动）逐方案重新评估。

每个评估任务 = (标签, 问题号, 算例, 核数, 方案路径)。问题号 0 表示官方 singlecore_evaluate.py。
结果只保留比较需要的精简字段，缓存在 对比_V1vsV2/official_eval/<标签>/<case>_K<k>.json，
缓存键含方案文件 SHA-256，方案变了会自动重评。评估失败（非零退出码）也会记录 stderr 尾部。

    nice -n 19 .venv/bin/python 对比_V1vsV2/eval_official.py --jobs p2 --workers 2
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PKG = ROOT / '数据包'
DATA = PKG / 'data'
CONFIG = DATA / 'config.txt'
EVAL_DIR = HERE / 'official_eval'
V1 = ROOT / '第一版_V1仓库' / '第一版解压' / 'V1_Q1_Q2_Q3_20260926' / 'output'
REGEN = HERE / 'v1_regen'
CASES = ['case_{:03d}'.format(i) for i in range(1, 101)]
CORES = [2, 3, 4, 5]
SCRIPTS = {0: 'singlecore_evaluate.py', 1: 'multicore_cut_evaluate_problem_1.py',
           2: 'multicore_cut_evaluate_problem_2.py', 3: 'multicore_cut_evaluate_problem_3.py'}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def slim(r):
    out = {'makespan': r['makespan'], 'data_movement_bytes': r['data_movement_bytes'],
           'num_subgraphs': r.get('num_subgraphs'), 'task_count': r.get('task_count'),
           'cross_task_traffic': r.get('cross_task_traffic')}
    if 'cross_core_transfers' in r:
        out['cross_core_transfers'] = len(r['cross_core_transfers'])
    if 'cache_stats' in r:
        out['cache_stats'] = r['cache_stats']
    return out


def cache_path(tag, case, k):
    return EVAL_DIR / tag / '{}_K{}.json'.format(case, k)


def run_one(tag, problem, case, k, plan):
    out = cache_path(tag, case, k)
    plan_sha = sha256(plan) if plan else None
    if out.exists():
        old = json.loads(out.read_text(encoding='utf-8'))
        if old.get('plan_sha256') == plan_sha:
            return old
    cmd = [sys.executable, str(PKG / 'code' / SCRIPTS[problem]), str(DATA / (case + '.json'))]
    if plan:
        cmd.append(str(plan))
    rec = {'tag': tag, 'problem': problem, 'case': case, 'cores': k,
           'plan': os.path.relpath(plan, ROOT) if plan else None, 'plan_sha256': plan_sha}
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        res = os.path.join(tmp, 'r.json')
        cmd += ['--config', str(CONFIG), '-o', res, '--trace-output', os.path.join(tmp, 't.json'),
                '--log-output', os.path.join(tmp, 'l.txt')]
        p = subprocess.run(cmd, capture_output=True, text=True)
        rec['seconds'] = round(time.time() - t0, 2)
        rec['returncode'] = p.returncode
        if p.returncode == 0:
            rec['valid'] = True
            rec['result'] = slim(json.loads(Path(res).read_text(encoding='utf-8')))
        else:
            rec['valid'] = False
            rec['error'] = (p.stdout + p.stderr)[-800:]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
    return rec


# ---------------------------------------------------------------- 任务清单
def jobs_single():
    return [('single', 0, c, 1, None) for c in CASES]


def jobs_v2(problem):
    d = ROOT / 'results_p{}'.format(problem)
    return [('v2_p{}'.format(problem), problem, c, k, d / 'k{}'.format(k) / (c + '_multicore_res.json'))
            for k in CORES for c in CASES]


def jobs_v1_p1():
    return [('v1_p1', 1, c, k, REGEN / 'p1' / 'results' / c / 'K{}'.format(k) / 'plan.json')
            for k in CORES for c in CASES]


def jobs_v1_p1_packaged():
    """发布包内原样保留的 8 图 × K2–5 方案（用于核对重生成方案与原交付一致）。"""
    base = V1 / 'v1_problem1' / 'results'
    return [('v1_p1_pkg', 1, d.name, k, d / 'K{}'.format(k) / 'plan.json')
            for d in sorted(base.glob('case_*')) for k in CORES]


def jobs_v1_p2():
    """第一版问题二最终方案 = 探索后最新 P_B，完整 400 份保存在 v1_problem3/seeds。"""
    base = V1 / 'v1_problem3' / 'seeds'
    return [('v1_p2', 2, c, k, base / c / 'K{}'.format(k) / 'plan.json') for k in CORES for c in CASES]


def jobs_v1_p2_main_pkg():
    """第一版问题二主实验（探索前）的 8 图方案，仅用于说明探索带来的变化。"""
    base = V1 / 'v1_problem2' / 'results'
    return [('v1_p2_main_pkg', 2, d.name, k, d / 'K{}'.format(k) / 'plan.json')
            for d in sorted(base.glob('case_*')) for k in CORES]


def jobs_v1_p3():
    return [('v1_p3', 3, c, k, REGEN / 'p3' / 'results' / c / 'K{}'.format(k) / 'plan.json')
            for k in CORES for c in CASES]


def jobs_v1_p3_packaged():
    base = V1 / 'v1_problem3' / 'results'
    return [('v1_p3_pkg', 3, d.name, k, d / 'K{}'.format(k) / 'plan.json')
            for d in sorted(base.glob('case_*')) for k in CORES]


def jobs_v1_p3_seed():
    """第一版问题二计划 P_B 直接放到问题三评估器（第一版问题三的起点，也是 115 组的最终答案）。"""
    base = V1 / 'v1_problem3' / 'seeds'
    return [('v1_p3_seedPB', 3, c, k, base / c / 'K{}'.format(k) / 'plan.json') for k in CORES for c in CASES]


def jobs_v1_supp():
    import csv
    rows = list(csv.DictReader(open(V1 / 'v1_supplement_20260925' / 'better_observed_candidates.csv',
                                    encoding='utf-8-sig')))
    out = []
    for r in rows:
        rel = r['plan_path'].replace('D:\\OFFICE\\CodexWorkspace\\output\\', '').replace('\\', '/')
        tag = 'v1_supp_q{}'.format(r['question'])
        # 同一 (问题, 图, 核) 只有一条候选，故以 case_K 作键即可唯一定位
        out.append((tag, int(r['question']), r['case'], int(r['cores']), V1 / rel))
    return out


JOBS = {'single': jobs_single, 'v2_p1': lambda: jobs_v2(1), 'v2_p2': lambda: jobs_v2(2),
        'v2_p3': lambda: jobs_v2(3), 'v1_p1': jobs_v1_p1, 'v1_p1_pkg': jobs_v1_p1_packaged,
        'v1_p2': jobs_v1_p2, 'v1_p2_main_pkg': jobs_v1_p2_main_pkg, 'v1_p3': jobs_v1_p3,
        'v1_p3_pkg': jobs_v1_p3_packaged, 'v1_p3_seed': jobs_v1_p3_seed, 'v1_supp': jobs_v1_supp}


def graph_size(case):
    return (DATA / (case + '.json')).stat().st_size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', nargs='+', required=True, choices=sorted(JOBS))
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--allow-missing', action='store_true', help='跳过尚未生成的方案文件')
    ap.add_argument('--reverse', action='store_true', help='小图先评（与另一进程从两端并行时使用）')
    a = ap.parse_args()
    todo = []
    for name in a.jobs:
        for j in JOBS[name]():
            if j[4] is not None and not Path(j[4]).exists():
                if a.allow_missing:
                    continue
                raise SystemExit('missing plan: {}'.format(j[4]))
            todo.append(j)
    todo.sort(key=lambda j: -graph_size(j[2]), reverse=a.reverse)  # 默认大图先评，尾部更均衡
    print('jobs:', len(todo), flush=True)
    bad = 0
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(run_one, *j): j for j in todo}
        for i, f in enumerate(as_completed(futs), 1):
            rec = f.result()
            if not rec.get('valid'):
                bad += 1
                print('INVALID', futs[f][:4], rec.get('error', '')[-200:], flush=True)
            if i % 50 == 0 or i == len(todo):
                print('{}/{} done, invalid={}'.format(i, len(todo), bad), flush=True)


if __name__ == '__main__':
    main()
