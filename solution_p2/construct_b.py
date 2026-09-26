"""场景 B 的初始方案构造。

场景 B 中每核只有一个 Task，子图的作用是：(1) 决定 op 属于哪个核；(2) 子图顺序把
官方 Step1 的核内访问序列按子图“分桶”，而该序列直接决定每条 Pipe 的发射顺序
（Pipe 按序列先进先出）。因此方案可以统一表示为：

    op → 核 的分配 κ  +  一个全局优先序 π（拓扑序）

再把 π 切成“连续同核段”(run)，每段是一个子图，各核按段在 π 中的先后排列。
切一个拓扑序得到的段，其商图天然无环，且各核顺序与全局拓扑序一致，因而不会触发
官方的“子图依赖环”与“全局执行等待环”检查。段长上限 ``max_run`` 控制分桶粒度：
段越短，Pipe 顺序越贴近 π（利于跨核流水）；段越长，Step1 的反向 DFS 在段内起作用
（利于缓存驻留、减少 spill）。
"""

import heapq
from collections import defaultdict

from construct import dfs_order  # 复用问题一的拓扑序工具


# ----------------------------------------------------------------------
def edge_bytes(model):
    """(u, v) -> u 产出且 v 消费的张量总字节（跨核时需要 COPY_OUT + COPY_IN）。"""
    eb = defaultdict(int)
    for t, prods in model.producers.items():
        size = model.tensor_by_id[t]['size']
        for u in prods:
            if u not in model.comp_set:
                continue
            for v in model.consumers.get(t, ()):
                if v in model.comp_set and v != u:
                    eb[(u, v)] += size
    return eb


def list_schedule_ops(model, k, cfg, eb, fixed_core=None, rank=None, extra=0.0,
                      copy_factor=1.0):
    """op 级列表调度（HEFT 思想），近似场景 B 的执行规则。

    跨核依赖 u→v 的到达时刻 = fin(u) + bytes/bw（COPY_OUT）+ delay(500) + bytes/bw
    （COPY_IN），再乘 copy_factor 近似 DDR 争用，另加 ``extra`` 作为跨核惩罚；每核
    PIPE_M / PIPE_V 分别计占用。``fixed_core`` 给定时只排时间不选核。
    返回 (core_of, start)。
    """
    rank = rank or model.bottom_level
    bw, delay = model.bandwidth, cfg['delay']
    indeg = {v: len(model.pred[v]) for v in model.comp}
    heap = [(-rank[v], v) for v in model.comp if indeg[v] == 0]
    heapq.heapify(heap)
    free = [[0.0, 0.0] for _ in range(k)]
    fin, start, core_of = {}, {}, {}
    cores = range(k)
    while heap:
        _, v = heapq.heappop(heap)
        p = 0 if model.is_m[v] else 1
        d = model.cyc[v]
        cand = [fixed_core[v]] if fixed_core is not None else cores
        best = None
        for c in cand:
            ready = 0.0
            for u in model.pred[v]:
                r = fin[u]
                if core_of[u] != c:
                    r += (2.0 * eb.get((u, v), 0) / bw) * copy_factor + delay + extra
                if r > ready:
                    ready = r
            s = max(ready, free[c][p])
            key = (s + d, free[c][0] + free[c][1], c)
            if best is None or key < best[0]:
                best = (key, c, s)
        _, c, s = best
        core_of[v] = c
        start[v] = s
        fin[v] = s + d
        free[c][p] = s + d
        for w in model.succ[v]:
            indeg[w] -= 1
            if indeg[w] == 0:
                heapq.heappush(heap, (-rank[w], w))
    return core_of, start


def runs_plan(order, core_of, k, max_run=None):
    """全局拓扑序 π 切成连续同核段 → (groups, core_orders)。"""
    groups, orders = [], [[] for _ in range(k)]
    cur, cur_core = [], None
    for v in order:
        c = core_of[v]
        if cur and (c != cur_core or (max_run and len(cur) >= max_run)):
            orders[cur_core].append(len(groups))
            groups.append(cur)
            cur = []
        cur.append(v)
        cur_core = c
    if cur:
        orders[cur_core].append(len(groups))
        groups.append(cur)
    return groups, orders


def time_order(model, start, pos):
    """按预计开始时刻排序的拓扑序（同时刻按 dfs 位置）。"""
    order = sorted(model.comp, key=lambda v: (start[v], pos[v]))
    return order


# ----------------------------------------------------------------------
# 多资源超图分核（方案文档 2.2）：两类 Pipe 负载平衡 + 张量连通度通信
# ----------------------------------------------------------------------
def hypergraph_refine(model, k, core_of, eps=0.05, passes=4, input_weight=1.0):
    """FM 风格的贪心单点移动：在 M/V 双资源负载约束下降低跨核通信代理。

    通信代理与官方计数一致：内部张量 t 若在 r 个远端核被消费，官方插入 r 对
    COPY_OUT/COPY_IN，计 2·r·b_t；原始输入被 n 个核读取计 n·b_t（至少 1 次为原图
    已有）。负载上限 L_r = (1+eps)·W_r/K，单个 op 超过时放宽。
    """
    tb = model.tensor_by_id
    size = {t: tb[t]['size'] for t in tb}
    prod = {}
    for t, ps in model.producers.items():
        for p in ps:
            if p in model.comp_set:
                prod[t] = p
    cons = {t: [c for c in model.consumers.get(t, ()) if c in model.comp_set]
            for t in tb}
    touched = defaultdict(list)          # op -> tensors it touches
    for t in tb:
        if not cons[t]:
            continue
        if t in prod:
            touched[prod[t]].append(t)
        for c in cons[t]:
            touched[c].append(t)
    load = [[0.0, 0.0] for _ in range(k)]
    for v in model.comp:
        load[core_of[v]][0 if model.is_m[v] else 1] += model.cyc[v]
    cap = [(1 + eps) * model.work_m / k, (1 + eps) * model.work_v / k]
    core_of = dict(core_of)

    def tensor_cost(t):
        cores = {core_of[c] for c in cons[t]}
        if t in prod:
            return 2 * size[t] * len(cores - {core_of[prod[t]]})
        return input_weight * size[t] * len(cores)

    for _ in range(passes):
        moved = 0
        for v in model.comp:
            src = core_of[v]
            r = 0 if model.is_m[v] else 1
            ts = touched.get(v, ())
            if not ts:
                continue
            base = sum(tensor_cost(t) for t in ts)
            best = (0, None)
            for c in range(k):
                if c == src:
                    continue
                if load[c][r] + model.cyc[v] > max(cap[r], model.cyc[v]):
                    continue
                core_of[v] = c
                gain = base - sum(tensor_cost(t) for t in ts)
                core_of[v] = src
                if gain > best[0]:
                    best = (gain, c)
            if best[1] is not None:
                c = best[1]
                core_of[v] = c
                load[src][r] -= model.cyc[v]
                load[c][r] += model.cyc[v]
                moved += 1
        if not moved:
            break
    return core_of


def comm_bytes(model, core_of):
    """官方口径的新增边界搬运代理（不含 spill）。"""
    tb = model.tensor_by_id
    total = 0
    for t in tb:
        cons = [c for c in model.consumers.get(t, ()) if c in model.comp_set]
        if not cons:
            continue
        cores = {core_of[c] for c in cons}
        ps = [p for p in model.producers.get(t, ()) if p in model.comp_set]
        if ps:
            total += 2 * tb[t]['size'] * len(cores - {core_of[ps[0]]})
        else:
            total += tb[t]['size'] * (len(cores) - 1)
    return total


def groups_to_core_of(groups, orders):
    core_of = {}
    for c, order in enumerate(orders):
        for g in order:
            for v in groups[g]:
                core_of[v] = c
    return core_of
