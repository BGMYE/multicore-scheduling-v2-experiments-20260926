"""问题一（场景 A）求解主程序：单个算例、单个核数。

流程（方案文档 V2_A）：
    1. 读图、建立计算 DAG 与张量视图（common.GraphModel）；
    2. 构造一组已知可行的初始方案（construct.py 的五类构造器 × 参数），
       全部用快速精确评估器（与官方逐项一致）打分，取最优者；
    3. 在剩余时间预算内做关键窗口 CP-SAT 局部规划（window_cpsat.py），
       每个候选方案完整回放，按 (Makespan, 新增搬运) 字典序择优；
    4. 输出 <case>_multicore_res.json（官方提交格式）与求解日志 JSON。

用法：
    python solution_p1/solve_p1.py 数据包/data/case_003.json -k 4 --budget 120 -o out.json
"""

import argparse
import json
import random
import time
from pathlib import Path

from common import GraphModel, case_name, dump_plan, load_config, load_graph, make_plan
from construct import (all_orders, band_segments, component_pack, heft_assign, list_schedule,
                       microblocks, segment_by_load, sync_cut_tasks, tals,
                       weak_components)
from fast_eval import TaskCache, simulate
import window_cpsat


def renumber(groups, orders):
    """按 (核, 核内位置) 重新编号 sgid，删除空子图。"""
    new_groups, new_orders = [], []
    for order in orders:
        row = []
        for t in order:
            if groups[t]:
                row.append(len(new_groups))
                new_groups.append(list(groups[t]))
        new_orders.append(row)
    return new_groups, new_orders


def portfolio(model, cache, cfg, k, deadline, log):
    """五类构造器 × 参数网格；返回按精确 (makespan, added) 排序的候选列表。"""
    orders_by_name = all_orders(model)
    total = model.work_m + model.work_v
    big = max(model.work_m, model.work_v)
    n = len(model.comp)
    results = []

    def consider(tag, groups, orders):
        if time.time() > deadline:
            return
        groups, orders = renumber(groups, orders)
        t0 = time.time()
        sim = simulate(cache, groups, orders, cfg['cross_wait'], cfg['same_wait'])
        results.append(((sim['makespan'], sim['added_copy_bytes']), tag, groups, orders, sim))
        log('  [{}] makespan={} added={} tasks={} ({:.1f}s)'.format(
            tag, sim['makespan'], sim['added_copy_bytes'], len(groups), time.time() - t0))

    # (0) 弱连通分量打包（分量间无依赖；共享输入聚合，LPT 分核）
    comps = weak_components(model, orders_by_name['dfs'])
    if len(comps) >= 2:
        for div in (1, 2, 4, 8, 16, 32):
            if div > 1 and len(comps) < k * div / 2:
                break
            g, o = component_pack(model, cache, k, cfg, comps, big / (k * div))
            consider('comp-pack/{}'.format(div), g, o)
    # (0b) 层带区间：ASAP 深度分带 × 带内连续切块（逐层共享权重的多分支图）
    for nb in (2, 4, 8, 16, 32):
        for pc in (1, k, 2 * k):
            if time.time() > deadline:
                break
            segs = band_segments(model, orders_by_name['dfs'], nb, pc)
            if len(segs) > 4000:
                continue
            consider('band/{}x{}'.format(nb, pc), segs, list_schedule(model, cache, segs, k, cfg))
    # (1) 拓扑区间 + Task 级列表调度
    blocks = microblocks(model, orders_by_name['dfs'], 64, max(1, total // (k * 64)))
    for div in (1, 2, 4, 8):
        segs = segment_by_load(model, blocks, big / (k * div))
        if len(segs) > 4000:
            continue
        consider('interval-dfs/{}'.format(div), segs, list_schedule(model, cache, segs, k, cfg))
    # (2) 场景 A 感知的 Task 级列表调度 TALS
    max_ops = 4000
    grid = [('dfs', cf, ob) for ob in (1000, 3000, 6000, 0) for cf in (1.0, 4.0)]
    grid += [('bl', cf, ob) for ob in (0, 1000, 3000) for cf in (1.0, 4.0)]
    grid += [('level', cf, ob) for ob in (0, 3000) for cf in (1.0, 4.0)]
    for name, cf, ob in grid:
        if time.time() > deadline:
            break
        g, o = tals(model, k, cfg, orders_by_name[name], copy_factor=cf,
                    max_ops=max_ops, open_bias=ob)
        consider('tals-{}/cf{:g}/ob{}'.format(name, cf, ob), g, o)
    # (3) op 级 HEFT + 同步点切分
    for pen in (1500, 4000, 12000):
        if time.time() > deadline:
            break
        core_of, start = heft_assign(model, k, pen)
        order = sorted(model.comp, key=lambda v: (start[v], -model.bottom_level[v]))
        g, o = sync_cut_tasks(model, core_of, order, k, max_ops, float('inf'))
        consider('heft-cut/P{}'.format(pen), g, o)
    results.sort(key=lambda r: r[0])
    return results


def solve(graph_path, k, budget, out_path=None, log_path=None, seed=0, verbose=True,
          window_tasks=16, solver_time=3.0):
    t_start = time.time()
    random.seed(seed)
    cfg = load_config()
    graph = load_graph(graph_path)
    model = GraphModel(graph, cfg['bandwidth'])
    cache = TaskCache(model, cfg)
    lines = []

    def log(msg):
        lines.append(msg)
        if verbose:
            print(msg, flush=True)

    log('{} K={} ops={} compute={} LB0={:.0f} CP={} W_M={} W_V={}'.format(
        case_name(graph_path), k, len(graph['ops']), len(model.comp), model.lower_bound(k),
        model.critical_path, model.work_m, model.work_v))
    # 构造阶段最多用预算的 45%
    cons_deadline = t_start + budget * 0.45
    results = portfolio(model, cache, cfg, k, cons_deadline, log)
    best_val, best_tag, groups, orders, sim = results[0]
    log('initial best: {} {}'.format(best_tag, best_val))
    t_cons = time.time() - t_start
    stats = {}
    if window_cpsat.HAVE_ORTOOLS and len(groups) >= 2:
        from construct import dfs_order
        pos = {v: i for i, v in enumerate(dfs_order(model))}
        opt = window_cpsat.WindowOptimizer(model, cache, cfg, k, pos, log=log,
                                           solver_time=solver_time)
        remaining = budget - (time.time() - t_start)
        if remaining > 2:
            groups, orders, sim = opt.run(groups, orders, sim, remaining,
                                          window_tasks=window_tasks)
        stats = opt.stats
    groups, orders = renumber(groups, orders)
    final = simulate(cache, groups, orders, cfg['cross_wait'], cfg['same_wait'])
    plan = make_plan(groups, orders)
    summary = {
        'case': case_name(graph_path), 'cores': k,
        'makespan_fast_eval': final['makespan'],
        'added_copy_bytes_fast_eval': final['added_copy_bytes'],
        'num_subgraphs': len(groups),
        'initial_constructor': best_tag, 'initial_makespan': best_val[0],
        'initial_added_bytes': best_val[1],
        'portfolio': [{'tag': r[1], 'makespan': r[0][0], 'added': r[0][1]} for r in results],
        'lower_bound_LB0': model.lower_bound(k), 'critical_path': model.critical_path,
        'window_stats': stats,
        'construct_seconds': round(t_cons, 2),
        'total_seconds': round(time.time() - t_start, 2),
        'task_cache': {'entries': len(cache.cache), 'hits': cache.hits, 'misses': cache.misses},
    }
    log('final: makespan={} added={} subgraphs={} time={:.1f}s'.format(
        final['makespan'], final['added_copy_bytes'], len(groups), summary['total_seconds']))
    if out_path:
        dump_plan(plan, out_path)
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(json.dumps({'summary': summary, 'log': lines},
                                             ensure_ascii=False, indent=1), encoding='utf-8')
    return plan, summary


def main():
    parser = argparse.ArgumentParser(description='问题一：场景 A 多核切图与调度')
    parser.add_argument('graph')
    parser.add_argument('-k', '--cores', type=int, required=True)
    parser.add_argument('--budget', type=float, default=120.0, help='单算例时间预算（秒）')
    parser.add_argument('-o', '--output')
    parser.add_argument('--log')
    parser.add_argument('--window-tasks', type=int, default=16)
    parser.add_argument('--solver-time', type=float, default=3.0)
    args = parser.parse_args()
    out = args.output or str(Path(args.graph).with_suffix('')) + '_multicore_res.json'
    solve(args.graph, args.cores, args.budget, out, args.log,
          window_tasks=args.window_tasks, solver_time=args.solver_time)


if __name__ == '__main__':
    main()
