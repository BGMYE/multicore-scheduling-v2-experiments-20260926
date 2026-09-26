"""对照验证：快速评估器 vs 官方 evaluate_scene_a。

对若干算例生成官方 stub 随机方案（不同种子、子图规模、核数），分别用
官方评估器与 fast_eval.simulate 计算 Makespan 与新增搬运量，要求逐项
完全相等。用法：

    python solution_p1/verify_fast_eval.py [case_001 case_064 ...]
"""

import sys
import time

from common import DATA_DIR, GraphModel, load_config, load_graph
from fast_eval import TaskCache, simulate

from multicore_cut_evaluate_problem_1 import evaluate_scene_a
from stub_multicore_cut_and_schedule import derive_multicore_plan, generate_multicore_plan


def plan_to_groups(graph, plan):
    view = derive_multicore_plan(graph, plan)
    groups = {sg: view['nodes_by_subgraph'][sg] for sg in view['subgraph_ids']}
    return groups, [list(o) for o in plan['core_schedules']]


def main(cases):
    cfg = load_config()
    total, bad = 0, 0
    for case in cases:
        graph = load_graph(DATA_DIR / (case + '.json'))
        model = GraphModel(graph, cfg['bandwidth'])
        cache = TaskCache(model, cfg)
        for seed, cores, lo, hi in ((0, 2, 20, 40), (1, 3, 50, 100), (2, 5, 5, 15),
                                    (3, 4, 100, 300), (4, 1, 10, 30)):
            plan = generate_multicore_plan(graph, num_cores=cores, seed=seed,
                                           min_subgraph_size=lo, max_subgraph_size=hi)
            t0 = time.time()
            ref = evaluate_scene_a(graph, plan, bandwidth=cfg['bandwidth'],
                                   capacity=cfg['capacity'],
                                   cross_core_wait=cfg['cross_wait'],
                                   same_core_wait=cfg['same_wait'])
            t1 = time.time()
            groups, orders = plan_to_groups(graph, plan)
            fast = simulate(cache, groups, orders, cfg['cross_wait'], cfg['same_wait'])
            t2 = time.time()
            ok = (ref['makespan'] == fast['makespan'] and
                  ref['data_movement_bytes']['added_copy_bytes'] == fast['added_copy_bytes'])
            ends_ok = all(
                t['end'] == fast['task_end'][t['task_id']]
                for core in ref['per_core_timeline'] for t in core['tasks'])
            total += 1
            bad += (not ok) or (not ends_ok)
            print('{} seed={} cores={} size={}-{}: official={} fast={} added {} / {} '
                  'task_ends_match={} [{} | official {:.2f}s fast {:.2f}s]'.format(
                      case, seed, cores, lo, hi, ref['makespan'], fast['makespan'],
                      ref['data_movement_bytes']['added_copy_bytes'],
                      fast['added_copy_bytes'], ends_ok, 'OK' if ok and ends_ok else 'MISMATCH',
                      t1 - t0, t2 - t1), flush=True)
    print('checked {} plans, mismatches {}'.format(total, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:] or ['case_064', 'case_001', 'case_006', 'case_019']))
