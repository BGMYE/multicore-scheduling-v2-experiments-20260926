"""方案文档 2.1.3 的验证：小窗口上 CP-SAT 与完整枚举对照。

对若干小窗口（3~4 个 Task、每 Task 至多 2 个微块、K=2）：
  (1) 模型编码验证：完整枚举同一候选族允许的全部（精确覆盖 × 分核 × 每核
      顺序），按与 CP-SAT 完全相同的代理目标求最优值，与 CP-SAT 的 OPTIMAL
      目标值比较；
  (2) 建模误差测量：把枚举得到的全部离散方案逐一用精确评估器（与官方一致）
      回放，比较“真实最优”与“代理选中方案”的真实 Makespan，并给出代理值
      与真实值的 Spearman 秩相关。

    .venv/bin/python solution_p1/verify_cpsat_enum.py [case_064] [--windows 4]
"""

import argparse
import itertools
import json
import time

from common import DATA_DIR, RESULTS_DIR, GraphModel, load_config, load_graph
from construct import all_orders, tals
from fast_eval import TaskCache, simulate
from solve_p1 import renumber
import window_cpsat


def schedule_proxy(fam, sel, assign, orders_per_core):
    """固定选择/分核/核内顺序时的最早开始调度（半主动调度即最优）。"""
    k, same, cross = fam['k'], fam['same'], fam['cross']
    selset = set(sel)
    preds = {g: [] for g in sel}
    for g, h in fam['dep_pairs']:
        if g in selset and h in selset:
            preds[h].append(g)
    prev = {}
    for c in range(k):
        for a, b in zip(orders_per_core[c], orders_per_core[c][1:]):
            prev[b] = a
    S, F = {}, {}
    remaining = list(sel)
    guard = 0
    while remaining:
        guard += 1
        if guard > 1000:
            return None                      # 顺序与依赖冲突（有环）
        progressed = False
        for g in list(remaining):
            ps = preds[g] + ([prev[g]] if g in prev else [])
            if any(p not in F for p in ps):
                continue
            c = assign[g]
            s = fam['rel'][g][c]
            if g in prev:
                s = max(s, F[prev[g]] + same)
            for p in preds[g]:
                s = max(s, F[p] + (0 if assign[p] == c else cross))
            S[g], F[g] = s, s + fam['dur'][g]
            remaining.remove(g)
            progressed = True
        if not progressed:
            return None
    z = 0
    for g in sel:
        z = max(z, F[g] + fam['tail'][g])
        ct = fam['core_tail'][assign[g]]
        if ct is not None:
            z = max(z, F[g] + same + ct)
    return z + sum(fam['cost'][g] for g in sel), S


def exact_covers(fam):
    nP, cands = fam['nP'], fam['cands']
    out = []

    def rec(covered, chosen):
        if len(covered) == nP:
            out.append(list(chosen))
            return
        i = min(set(range(nP)) - covered)
        for g in fam['by_piece'][i]:
            if not (cands[g] & covered):
                chosen.append(g)
                rec(covered | cands[g], chosen)
                chosen.pop()
    rec(frozenset(), [])
    return out


def enumerate_plans(fam):
    k = fam['k']
    plans = []
    for sel in exact_covers(fam):
        for assign_t in itertools.product(range(k), repeat=len(sel)):
            assign = dict(zip(sel, assign_t))
            per_core = [[g for g in sel if assign[g] == c] for c in range(k)]
            for perms in itertools.product(*[itertools.permutations(p) for p in per_core]):
                res = schedule_proxy(fam, sel, assign, [list(p) for p in perms])
                if res is None:
                    continue
                val, S = res
                plans.append((val, sel, assign, S))
    return plans


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('case', nargs='?', default='case_064')
    parser.add_argument('--k', type=int, default=2)
    parser.add_argument('--windows', type=int, default=4)
    parser.add_argument('--window-tasks', type=int, default=3)
    parser.add_argument('--max-plans', type=int, default=4000)
    args = parser.parse_args()
    cfg = load_config()
    model = GraphModel(load_graph(DATA_DIR / (args.case + '.json')), cfg['bandwidth'])
    cache = TaskCache(model, cfg)
    k = args.k
    orders = all_orders(model)
    groups, corders = tals(model, k, cfg, orders['dfs'], copy_factor=1.0, open_bias=1000)
    groups, corders = renumber(groups, corders)
    sim = simulate(cache, groups, corders, cfg['cross_wait'], cfg['same_wait'])
    pos = {v: i for i, v in enumerate(orders['dfs'])}
    opt = window_cpsat.WindowOptimizer(model, cache, cfg, k, pos, log=lambda *_: None,
                                       max_pieces=2, solver_time=60.0)
    by_start = sorted(range(len(groups)), key=lambda t: (sim['task_start'][t], t))
    report = []
    step = max(1, len(by_start) // (args.windows + 1))
    for w in range(args.windows):
        lo = (w + 1) * step
        hi = lo + args.window_tasks
        if hi >= len(by_start):
            break
        t0 = sim['task_start'][by_start[lo]]
        t1 = sim['task_start'][by_start[hi]]
        opt.build_only = True
        fam = opt.optimize_window(groups, corders, sim, t0, t1, time.time() + 600)
        if fam is None or isinstance(fam, tuple):
            continue
        t_enum = time.time()
        plans = enumerate_plans(fam)
        t_enum = time.time() - t_enum
        best_enum = min(p[0] for p in plans)
        opt.build_only = False
        res = opt.optimize_window(groups, corders, sim, t0, t1, time.time() + 600)
        cp_status, cp_obj = res[2]['status'], res[2]['objective']
        # 真实评估：去重后的离散方案（选择+分核+核内顺序）
        seen, vals_proxy, vals_true = set(), [], []
        chosen_true = None
        for val, sel, assign, S in sorted(plans, key=lambda p: p[0])[:args.max_plans]:
            choice = [(g, assign[g], S[g]) for g in sel]
            ng, no = opt.decode(groups, corders, sim, fam, choice)
            key = tuple(tuple(tuple(sorted(ng[t])) for t in row) for row in no)
            if key in seen:
                continue
            seen.add(key)
            ms = simulate(cache, ng, no, cfg['cross_wait'], cfg['same_wait'])['makespan']
            vals_proxy.append(val)
            vals_true.append(ms)
            if chosen_true is None:
                chosen_true = ms          # 代理最优（排序后第一个）的真实值
        exhausted = len(plans) <= args.max_plans
        cp_true = simulate(cache, res[0], res[1], cfg['cross_wait'], cfg['same_wait'])['makespan']
        item = {
            'window': [t0, t1], 'window_tasks': len(fam['W']), 'pieces': fam['nP'],
            'candidates': len(fam['cands']), 'enumerated_plans': len(plans),
            'enumeration_seconds': round(t_enum, 2),
            'proxy_opt_enumeration': best_enum, 'cpsat_status': cp_status,
            'cpsat_objective': cp_obj, 'encoding_match': abs(best_enum - cp_obj) < 0.5,
            'distinct_plans_simulated': len(vals_true), 'all_plans_simulated': exhausted,
            'true_best_makespan': min(vals_true), 'proxy_selected_true_makespan': chosen_true,
            'cpsat_plan_true_makespan': cp_true, 'current_plan_makespan': sim['makespan'],
            'spearman_proxy_vs_true': spearman(vals_proxy, vals_true),
        }
        report.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    out = RESULTS_DIR / 'validation' / 'cpsat_enumeration_{}_k{}.json'.format(args.case, k)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print('written', out)


if __name__ == '__main__':
    main()
