"""简单贪心基线：反向 DFS 拓扑序切成 K 段负载相等的连续区间 + Task 级列表调度
（即构造器 ``interval-dfs/1``）。方案保存到 results_p1/baselines/greedy_k{K}/，
用快速精确评估器打分（与官方评估器逐项一致，见 verify_fast_eval.py 与 summary.csv 的
fast_eval_match 列）。

    python solution_p1/run_greedy_baseline.py --workers 6
"""
import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed

from common import DATA_DIR, RESULTS_DIR, GraphModel, dump_plan, load_config, load_graph, make_plan
from construct import dfs_order, list_schedule, microblocks, segment_by_load
from fast_eval import TaskCache, simulate
from solve_p1 import renumber


def job(case, cores):
    cfg = load_config()
    model = GraphModel(load_graph(DATA_DIR / (case + '.json')), cfg['bandwidth'])
    cache = TaskCache(model, cfg)
    order = dfs_order(model)
    total = model.work_m + model.work_v
    big = max(model.work_m, model.work_v)
    out = []
    for k in cores:
        blocks = microblocks(model, order, 64, max(1, total // (k * 64)))
        segs = segment_by_load(model, blocks, big / k)
        g, o = renumber(segs, list_schedule(model, cache, segs, k, cfg))
        sim = simulate(cache, g, o, cfg['cross_wait'], cfg['same_wait'])
        d = RESULTS_DIR / 'baselines' / 'greedy_k{}'.format(k)
        dump_plan(make_plan(g, o), d / (case + '_multicore_res.json'))
        (d / (case + '.json')).write_text(json.dumps({
            'makespan': sim['makespan'], 'added_copy_bytes': sim['added_copy_bytes'],
            'num_subgraphs': len(g), 'evaluator': 'fast_eval (identical to official)'}, indent=1))
        out.append((k, sim['makespan']))
    return case, out


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--cores', type=int, nargs='*', default=[2, 3, 4, 5])
    p.add_argument('--workers', type=int, default=6)
    a = p.parse_args()
    cases = sorted(x.stem for x in DATA_DIR.glob('case_*.json'))
    with ProcessPoolExecutor(a.workers) as pool:
        for f in as_completed([pool.submit(job, c, a.cores) for c in cases]):
            print(*f.result(), flush=True)


if __name__ == '__main__':
    main()
