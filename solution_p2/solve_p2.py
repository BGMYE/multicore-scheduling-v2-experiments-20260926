"""问题二（场景 B）求解主程序：单个算例、单个核数。

流程（方案文档 V2_B）：
  1. 读图、建立计算 DAG 与张量超边（复用 solution_p1.common.GraphModel）；
  2. 初始方案组合，全部用官方问题二评估器（eval_b）打分：
       * 场景 B 专用：op 级列表调度（跨核到达 = COPY_OUT + 500 + COPY_IN）→ 全局时间序
         → 连续同核段；以及在其分核结果上做双资源超图 FM 细化（hyper）后重排；
       * 复用问题一的构造器（分量打包 / 层带 / TALS / HEFT 同步点切分）：其 Task 序列
         在场景 B 中成为各核的子图顺序；
       * 可选：问题一最终方案（results_p1，只读）作为 warm start；
  3. 局部规划（improve_b.Improver）：分核窗口 CP-SAT（v/z/w 通信 + 双资源负载）、
     关键核顺序/驻留窗口 CP-SAT、时间反馈重排、粒度合并，交替进行，逐个精确回放；
  4. 输出 <case>_multicore_res.json 与求解日志。

用法：
    .venv/bin/python solution_p2/solve_p2.py 数据包/data/case_003.json -k 4 --budget 120 -o out.json
"""

import argparse
import json
import time
from pathlib import Path

from common_b import (RESULTS_P1, GraphModel, case_name, dump_plan, load_config_b,
                      load_graph, make_plan)
import eval_b
import improve_b
from construct import (all_orders, band_segments, component_pack, dfs_order, heft_assign,
                       list_schedule, sync_cut_tasks, tals, weak_components)
from construct_b import (comm_bytes, edge_bytes, hypergraph_refine, list_schedule_ops,
                         runs_plan, time_order)


def compact(groups, orders):
    new_groups, new_orders = [], []
    for order in orders:
        row = []
        for g in order:
            if groups[g]:
                row.append(len(new_groups))
                new_groups.append(list(groups[g]))
        new_orders.append(row)
    return new_groups, new_orders


def portfolio(model, graph, cfg_b, k, deadline, log, p1_warm=True, case=None):
    from common import load_config
    from fast_eval import TaskCache
    cfg_a = load_config()                    # 问题一构造器需要场景 A 参数（仅用于构造）
    cache_a = TaskCache(model, cfg_a)
    results = []
    orders_by_name = all_orders(model)
    pos = {v: i for i, v in enumerate(orders_by_name['dfs'])}
    eb = edge_bytes(model)
    big = max(model.work_m, model.work_v)

    def consider(tag, groups, orders):
        if time.time() > deadline:
            return
        groups, orders = compact(groups, orders)
        t0 = time.time()
        try:
            res = eval_b.evaluate_groups(graph, groups, orders, cfg_b)
        except eval_b.EvalError as error:
            log('  [{}] invalid: {}'.format(tag, str(error)[:100]))
            return
        results.append((eval_b.score(res), tag, groups, orders, res))
        log('  [{}] makespan={} added={} subgraphs={} ({:.1f}s)'.format(
            tag, res['makespan'], res['data_movement_bytes']['added_copy_bytes'],
            len(groups), time.time() - t0))

    # (a) 问题一最终方案（只读 warm start）
    if p1_warm and case:
        p = RESULTS_P1 / 'k{}'.format(k) / (case + '_multicore_res.json')
        if p.exists():
            plan = json.load(open(p, encoding='utf-8'))
            if time.time() <= deadline:
                try:
                    res = eval_b.evaluate_plan(graph, plan, cfg_b)
                    from stub_multicore_cut_and_schedule import derive_multicore_plan
                    view = derive_multicore_plan(graph, plan)
                    ids = sorted(view['subgraph_ids'])
                    remap = {sg: i for i, sg in enumerate(ids)}
                    groups = [view['nodes_by_subgraph'][sg] for sg in ids]
                    orders = [[remap[sg] for sg in o] for o in plan['core_schedules']]
                    results.append((eval_b.score(res), 'p1-final', groups, orders, res))
                    log('  [p1-final] makespan={} added={}'.format(
                        res['makespan'], res['data_movement_bytes']['added_copy_bytes']))
                except eval_b.EvalError as error:
                    log('  [p1-final] invalid: ' + str(error)[:100])
    # (b) 场景 B 专用：op 级列表调度 → 时间序 → 同核段；超图细化
    for extra in (0, 1000):
        if time.time() > deadline:
            break
        core_of, start = list_schedule_ops(model, k, cfg_b, eb, extra=extra)
        order = time_order(model, start, pos)
        for mr in (None, 8):
            g, o = runs_plan(order, core_of, k, mr)
            consider('lsB/x{}/run{}'.format(extra, mr or 'max'), g, o)
        if time.time() > deadline:
            break
        co2 = hypergraph_refine(model, k, core_of)
        log('   hyper: comm {:.2f}MB -> {:.2f}MB'.format(comm_bytes(model, core_of) / 1e6,
                                                         comm_bytes(model, co2) / 1e6))
        _, st2 = list_schedule_ops(model, k, cfg_b, eb, fixed_core=co2)
        g, o = runs_plan(time_order(model, st2, pos), co2, k, None)
        consider('hyper/x{}'.format(extra), g, o)
    # (c) 问题一构造器（Task 序列 → 子图顺序）
    comps = weak_components(model, orders_by_name['dfs'])
    if len(comps) >= 2:
        for div in (1, 4, 16):
            if time.time() > deadline or (div > 1 and len(comps) < k * div / 2):
                break
            g, o = component_pack(model, cache_a, k, cfg_a, comps, big / (k * div))
            consider('comp-pack/{}'.format(div), g, o)
    for name, cf, ob in (('dfs', 1.0, 1000), ('dfs', 4.0, 1000), ('dfs', 4.0, 3000),
                         ('dfs', 1.0, 6000), ('bl', 4.0, 1000), ('bl', 1.0, 0)):
        if time.time() > deadline:
            break
        g, o = tals(model, k, cfg_a, orders_by_name[name], copy_factor=cf, open_bias=ob)
        consider('tals-{}/cf{:g}/ob{}'.format(name, cf, ob), g, o)
    for nb, pc in ((4, 1), (4, k), (16, k)):
        if time.time() > deadline:
            break
        segs = band_segments(model, orders_by_name['dfs'], nb, pc)
        consider('band/{}x{}'.format(nb, pc), segs, list_schedule(model, cache_a, segs, k, cfg_a))
    if time.time() <= deadline:
        core_of, start = heft_assign(model, k, 1500)
        order = sorted(model.comp, key=lambda v: (start[v], -model.bottom_level[v]))
        g, o = sync_cut_tasks(model, core_of, order, k, 4000, float('inf'))
        consider('heft-cut/P1500', g, o)
    results.sort(key=lambda r: r[0])
    return results


def solve(graph_path, k, budget, out_path=None, log_path=None, verbose=True, p1_warm=True,
          solver_time=3.0):
    t_start = time.time()
    cfg = load_config_b()
    graph = load_graph(graph_path)
    model = GraphModel(graph, cfg['bandwidth'])
    case = case_name(graph_path)
    lines = []

    def log(msg):
        lines.append(msg)
        if verbose:
            print(msg, flush=True)

    log('{} K={} compute={} LB0={:.0f} CP={} W_M={} W_V={}'.format(
        case, k, len(model.comp), model.lower_bound(k), model.critical_path,
        model.work_m, model.work_v))
    results = portfolio(model, graph, cfg, k, t_start + budget * 0.5, log, p1_warm, case)
    best_val, best_tag, groups, orders, res = results[0]
    log('initial best: {} {}'.format(best_tag, best_val))
    t_cons = time.time() - t_start
    imp = improve_b.Improver(model, graph, cfg, k, log=log, solver_time=solver_time)
    remaining = budget - (time.time() - t_start)
    if remaining > 3:
        groups, orders, res = imp.run(groups, orders, res, remaining)
    plan = make_plan(groups, orders)
    summary = {
        'case': case, 'cores': k,
        'makespan_eval': res['makespan'],
        'added_copy_bytes_eval': res['data_movement_bytes']['added_copy_bytes'],
        'data_movement_bytes': res['data_movement_bytes'],
        'num_subgraphs': len(groups),
        'initial_constructor': best_tag, 'initial_makespan': best_val[0],
        'initial_added_bytes': best_val[1],
        'portfolio': [{'tag': r[1], 'makespan': r[0][0], 'added': r[0][1]} for r in results],
        'lower_bound_LB0': model.lower_bound(k), 'critical_path': model.critical_path,
        'improve_stats': dict(imp.stats),
        'construct_seconds': round(t_cons, 2),
        'total_seconds': round(time.time() - t_start, 2),
    }
    log('final: makespan={} added={} subgraphs={} time={:.1f}s'.format(
        res['makespan'], res['data_movement_bytes']['added_copy_bytes'], len(groups),
        summary['total_seconds']))
    if out_path:
        dump_plan(plan, out_path)
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(json.dumps({'summary': summary, 'log': lines},
                                             ensure_ascii=False, indent=1), encoding='utf-8')
    return plan, summary


def main():
    parser = argparse.ArgumentParser(description='问题二：场景 B 多核切图与调度')
    parser.add_argument('graph')
    parser.add_argument('-k', '--cores', type=int, required=True)
    parser.add_argument('--budget', type=float, default=120.0)
    parser.add_argument('-o', '--output')
    parser.add_argument('--log')
    parser.add_argument('--no-p1-warm', action='store_true',
                        help='不读取 results_p1 的问题一最终方案作为初始候选')
    parser.add_argument('--solver-time', type=float, default=3.0)
    args = parser.parse_args()
    out = args.output or str(Path(args.graph).with_suffix('')) + '_multicore_res.json'
    solve(args.graph, args.cores, args.budget, out, args.log, p1_warm=not args.no_p1_warm,
          solver_time=args.solver_time)


if __name__ == '__main__':
    main()
