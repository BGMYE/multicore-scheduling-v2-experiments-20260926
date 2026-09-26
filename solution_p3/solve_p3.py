"""问题三（场景 B + 只读 L2 FIFO Cache）求解主程序：单个算例、单个核数。

流程（方案文档 V2_C）：
  1. 读入问题二最终方案 P_B（results_p2/k{K}，只读），在官方问题三评估器中回放 → T_C(P_B)；
  2. improve_c.ImproverC.run_cache：高价值重复 miss 窗口候选（gather / stagger / colocate /
     cluster），每个候选完整回放；剩余预算用问题二通用邻域（以 T_C 评分）；
  3. 输出 <case>_multicore_res.json 与求解日志（含窗口候选数、已评价数、是否穷尽）。

    .venv/bin/python solution_p3/solve_p3.py 数据包/data/case_005.json -k 4 --budget 120 -o out.json
"""

import argparse
import json
import time
from pathlib import Path

from common_c import (RESULTS_P2, GraphModel, case_name, dump_plan, load_config_c,
                      load_graph, make_plan)
import eval_c
import improve_c
from stub_multicore_cut_and_schedule import derive_multicore_plan


def plan_to_groups(graph, plan):
    view = derive_multicore_plan(graph, plan)
    ids = sorted(view['subgraph_ids'])
    remap = {sg: i for i, sg in enumerate(ids)}
    groups = [view['nodes_by_subgraph'][sg] for sg in ids]
    orders = [[remap[sg] for sg in o] for o in plan['core_schedules']]
    return groups, orders


def cache_summary(res):
    cs = res['cache_stats']
    return {k: cs[k] for k in ('hits', 'accesses', 'hit_bytes', 'miss_bytes', 'hit_rate')}


def solve(graph_path, k, budget, out_path=None, log_path=None, verbose=True, solver_time=3.0,
          init_plan_path=None):
    t_start = time.time()
    cfg = load_config_c()
    graph = load_graph(graph_path)
    model = GraphModel(graph, cfg['bandwidth'])
    case = case_name(graph_path)
    lines = []

    def log(msg):
        lines.append(msg)
        if verbose:
            print(msg, flush=True)

    init = Path(init_plan_path) if init_plan_path else RESULTS_P2 / 'k{}'.format(k) / (case + '_multicore_res.json')
    if not init.exists():
        raise FileNotFoundError('问题二方案不存在：{}（请先运行 solution_p2/run_all_p2.py）'.format(init))
    plan_b = json.load(open(init, encoding='utf-8'))
    groups, orders = plan_to_groups(graph, plan_b)
    res = eval_c.evaluate_groups_c(graph, groups, orders, cfg)
    init_val = eval_c.score(res)
    init_cache = cache_summary(res)
    log('{} K={} P_B: T_C={} added={} hit_rate={:.3f}'.format(
        case, k, init_val[0], init_val[1], init_cache['hit_rate']))
    imp = improve_c.ImproverC(model, graph, cfg, k, log=log, solver_time=solver_time)
    remaining = budget - (time.time() - t_start)
    if remaining > 3:
        groups, orders, res = imp.run_cache(groups, orders, res, remaining)
    plan = make_plan(groups, orders)
    summary = {
        'case': case, 'cores': k,
        'makespan_eval': res['makespan'],
        'added_copy_bytes_eval': res['data_movement_bytes']['added_copy_bytes'],
        'data_movement_bytes': res['data_movement_bytes'],
        'cache': cache_summary(res),
        'init_makespan_TC_PB': init_val[0], 'init_added_bytes': init_val[1], 'init_cache': init_cache,
        'num_subgraphs': len(groups),
        'improve_stats': dict(imp.stats),
        'reuse_windows': imp.windows,
        'lower_bound_LB0': model.lower_bound(k),
        'total_seconds': round(time.time() - t_start, 2),
    }
    log('final: T_C={} added={} hit_rate={:.3f} subgraphs={} time={:.1f}s'.format(
        res['makespan'], summary['added_copy_bytes_eval'], summary['cache']['hit_rate'],
        len(groups), summary['total_seconds']))
    if out_path:
        dump_plan(plan, out_path)
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(json.dumps({'summary': summary, 'log': lines},
                                             ensure_ascii=False, indent=1), encoding='utf-8')
    return plan, summary


def main():
    ap = argparse.ArgumentParser(description='问题三：场景 B + 只读 L2 Cache')
    ap.add_argument('graph')
    ap.add_argument('-k', '--cores', type=int, required=True)
    ap.add_argument('--budget', type=float, default=120.0)
    ap.add_argument('-o', '--output')
    ap.add_argument('--log')
    ap.add_argument('--init-plan', help='初值方案（默认 results_p2/k{K}/<case>_multicore_res.json）')
    a = ap.parse_args()
    out = a.output or str(Path(a.graph).with_suffix('')) + '_multicore_res.json'
    solve(a.graph, a.cores, a.budget, out, a.log, init_plan_path=a.init_plan)


if __name__ == '__main__':
    main()
