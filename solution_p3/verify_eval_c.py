"""验证 step3 计数器补丁不改变官方问题三评估结果（含 Cache 统计与事件轨迹）。

    python solution_p3/verify_eval_c.py [case_064 case_003 ...]
"""
import json
import sys
import time

from common_c import DATA_DIR, RESULTS_P2, load_config_c, load_graph
import eval_c
from stub_multicore_cut_and_schedule import generate_multicore_plan


def digest(r):
    return (r['makespan'], json.dumps(r['data_movement_bytes'], sort_keys=True),
            json.dumps(r['cache_stats'], sort_keys=True), json.dumps(r['cache_events']),
            json.dumps(r['cache_final_entries']),
            tuple(core['tasks'][0]['end'] for core in r['per_core_timeline']))


def main(cases):
    cfg = load_config_c()
    total = bad = 0
    for case in cases:
        g = load_graph(DATA_DIR / (case + '.json'))
        plans = [('stub s{} k{}'.format(s, k), generate_multicore_plan(g, num_cores=k, seed=s,
                  min_subgraph_size=lo, max_subgraph_size=hi))
                 for s, k, lo, hi in ((0, 2, 20, 40), (1, 3, 50, 100), (2, 5, 5, 15))]
        for k in (2, 5):
            p = RESULTS_P2 / 'k{}'.format(k) / (case + '_multicore_res.json')
            if p.exists():
                plans.append(('p2 k{}'.format(k), json.load(open(p, encoding='utf-8'))))
        for tag, plan in plans:
            t0 = time.time()
            ref = eval_c.evaluate_plan_c(g, plan, cfg, fast=False)
            t1 = time.time()
            fast = eval_c.evaluate_plan_c(g, plan, cfg, fast=True)
            t2 = time.time()
            ok = digest(ref) == digest(fast)
            total += 1
            bad += not ok
            print('{} {}: official={} patched={} hit_rate={:.3f} {} [{:.2f}s vs {:.2f}s]'.format(
                case, tag, ref['makespan'], fast['makespan'], ref['cache_stats']['hit_rate'],
                'OK' if ok else 'MISMATCH', t1 - t0, t2 - t1), flush=True)
    print('checked {} plans, mismatches {}'.format(total, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:] or ['case_064', 'case_001', 'case_005', 'case_003', 'case_044', 'case_016']))
