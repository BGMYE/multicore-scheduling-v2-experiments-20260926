"""验证 eval_b 的 step3 计数器补丁不改变官方问题二评估结果。

对若干算例，分别用官方 stub 随机方案（不同种子/核数/子图规模）和问题一的最终方案，
比较“打补丁”与“未打补丁”的官方 evaluate_scene_b 输出：Makespan、数据搬运各项、
各核内存峰值、每个跨核传输的 COPY_IN 开始/结束时刻，要求全部相等。

    python solution_p2/verify_eval_b.py [case_064 case_003 ...]
"""

import json
import sys
import time

from common_b import DATA_DIR, RESULTS_P1, load_config_b, load_graph
import eval_b
from stub_multicore_cut_and_schedule import generate_multicore_plan


def digest(res):
    return (res['makespan'], json.dumps(res['data_movement_bytes'], sort_keys=True),
            json.dumps(res['memory_peak_by_core'], sort_keys=True),
            tuple((t['copy_in_start'], t['copy_in_end']) for t in res['cross_core_transfers']),
            tuple(core['tasks'][0]['end'] for core in res['per_core_timeline']))


def main(cases):
    cfg = load_config_b()
    total = bad = 0
    for case in cases:
        graph = load_graph(DATA_DIR / (case + '.json'))
        plans = []
        for seed, k, lo, hi in ((0, 2, 20, 40), (1, 3, 50, 100), (2, 5, 5, 15), (3, 4, 100, 300)):
            plans.append(('stub s{} k{}'.format(seed, k),
                          generate_multicore_plan(graph, num_cores=k, seed=seed,
                                                  min_subgraph_size=lo, max_subgraph_size=hi)))
        for k in (2, 5):
            p = RESULTS_P1 / 'k{}'.format(k) / (case + '_multicore_res.json')
            if p.exists():
                plans.append(('p1 k{}'.format(k), json.load(open(p, encoding='utf-8'))))
        for tag, plan in plans:
            t0 = time.time()
            eval_b.uninstall_fast_step3()
            ref = eval_b.evaluate_plan(graph, plan, cfg, fast=False)
            t1 = time.time()
            fast = eval_b.evaluate_plan(graph, plan, cfg, fast=True)
            t2 = time.time()
            ok = digest(ref) == digest(fast)
            total += 1
            bad += not ok
            print('{} {}: official={} patched={} {} [{:.2f}s vs {:.2f}s]'.format(
                case, tag, ref['makespan'], fast['makespan'], 'OK' if ok else 'MISMATCH',
                t1 - t0, t2 - t1), flush=True)
    print('checked {} plans, mismatches {}'.format(total, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:] or ['case_064', 'case_001', 'case_006', 'case_003', 'case_016']))
