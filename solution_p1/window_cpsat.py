"""方案文档 2.1 的局部数学规划：候选子图集合划分 + 分核 + Task 区间规划（CP-SAT）。

每轮迭代（对应文档伪代码 V2_A 的循环体）：

1. ``choose_critical_window``：沿精确仿真得到的关键 Task 链选一个时间窗
   [t0, t1)，窗口 W = 开始时刻落在其中的全部 Task。可以证明：原图中不存在
   “离开 W 的 op 集合、经窗口外 Task、再回到 W”的路径（窗口外 Task 要么
   在 t0 前开始、要么在 t1 后开始），因此只要窗口内被选候选之间满足时间
   先行约束，新方案的子图商图必然无环。
2. ``generate_candidate_subgraphs``：把 W 内每个 Task 沿拓扑序切成至多 3 个
   微块；候选 = 每个 Task 的连续微块区间（含单微块与原 Task）+ 相邻/有依赖
   的 Task 对的合并（及“尾块+后继”“前驱+首块”边界平移），合并候选做凸性
   检查。
3. CP-SAT 模型（文档 2.1.1 三层）：
   * 精确覆盖：每个微块恰被一个被选候选覆盖 ``sum_{g∋b} a_g = 1``；
   * 分核：``sum_k x_gk = a_g``，每个 x_gk 控制一个可选区间
     [S_g, F_g + 100)，同核 ``NoOverlap`` 表达串行与 100 cycles 切换等待；
   * 时间代理：``F_g = S_g + d_g``，d_g = 官方 Step1-3 独占带宽下的局部
     makespan × 当前方案标定的带宽争用系数；依赖 g→h 同核时
     ``S_h ≥ F_g``（叠加 NoOverlap 即 ≥ F_g+100），跨核时 ``S_h ≥ F_g + 1000``；
   * 窗口外固定部分：各核在 t0 前最后一个 Task 的结束时刻、窗口外前驱的
     完成时刻给出释放时刻；窗口外后继的“剩余路径长度”给出尾部项；
   * 目标：min Z + μ·Σ a_g·bytes_g/bw，Z ≥ F_g + tail_g。
4. 解码为 node_to_subgraph / core_schedules（窗口前 Task、窗口内按 S_g、窗口后
   Task 依次排列），用精确评估器完整回放，按 (Makespan, 新增搬运) 字典序
   择优保留。规划时刻不写入输出。
"""

import math
import time
from collections import defaultdict

try:
    from ortools.sat.python import cp_model
    HAVE_ORTOOLS = True
except ImportError:          # 没有 OR-Tools 时整个窗口优化阶段被跳过
    HAVE_ORTOOLS = False

from fast_eval import simulate, subgraph_preds


# ----------------------------------------------------------------------
def critical_chain(sim, orders):
    """从最晚结束的 Task 反向追踪决定其开始时刻的约束链。"""
    start, end = sim['task_start'], sim['task_end']
    core_of, preds = sim['core_of'], sim['pred_tasks']
    prev_on_core = {}
    for order in orders:
        for a, b in zip(order, order[1:]):
            prev_on_core[b] = a
    t = max(end, key=lambda x: (end[x], x))
    chain = [t]
    seen = {t}
    while True:
        s = start[t]
        cand = None
        p = prev_on_core.get(t)
        if p is not None and end[p] + 100 >= s - 1:
            cand = p
        for q in preds[t]:
            if core_of[q] != core_of[t] and end[q] + 1000 >= s - 1:
                if cand is None or end[q] > end[cand]:
                    cand = q
        if cand is None or cand in seen:
            break
        chain.append(cand)
        seen.add(cand)
        t = cand
    return chain[::-1]


class WindowOptimizer:
    def __init__(self, model, cache, cfg, k, pos, log=print, mu=0.2,
                 max_pieces=3, min_piece_cycles=0, solver_time=3.0, workers=1):
        self.m = model
        self.cache = cache
        self.cfg = cfg
        self.k = k
        self.pos = pos               # 全局 dfs 拓扑位置，用于任务内排序与切微块
        self.log = log
        self.mu = mu
        self.max_pieces = max_pieces
        self.min_piece_cycles = min_piece_cycles
        self.solver_time = solver_time
        self.workers = workers
        self.stats = {'windows': 0, 'improved': 0, 'optimal': 0, 'feasible': 0,
                      'unknown': 0, 'infeasible': 0, 'candidates': 0, 'evals': 0}

    # ------------------------------------------------------------------
    def evaluate(self, groups, orders):
        self.stats['evals'] += 1
        return simulate(self.cache, groups, orders, self.cfg['cross_wait'],
                        self.cfg['same_wait'])

    def split_pieces(self, ops):
        m = self.m
        ops = sorted(ops, key=self.pos.__getitem__)
        total = sum(m.cyc[v] for v in ops)
        p = min(self.max_pieces, len(ops))
        if self.min_piece_cycles:
            p = max(1, min(p, int(total // self.min_piece_cycles)))
        if p <= 1:
            return [ops]
        pieces, cur, acc = [], [], 0
        target = total / p
        for v in ops:
            cur.append(v)
            acc += m.cyc[v]
            if acc >= target * (len(pieces) + 1) and len(pieces) < p - 1:
                pieces.append(cur)
                cur = []
        if cur:
            pieces.append(cur)
        return [q for q in pieces if q]

    # ------------------------------------------------------------------
    def optimize_window(self, groups, orders, sim, t0, t1, deadline):
        m, k = self.m, self.k
        cross, same = self.cfg['cross_wait'], self.cfg['same_wait']
        start, end, core_of = sim['task_start'], sim['task_end'], sim['core_of']
        makespan = sim['makespan']
        W = [t for t in range(len(groups)) if t0 <= start[t] < t1]
        if len(W) < 2:
            return None
        Wset = set(W)
        owner = {}
        for t, ops in enumerate(groups):
            for v in ops:
                owner[v] = t
        # --- 微块
        pieces, piece_task = [], []
        task_pieces = {}
        for t in W:
            ps = self.split_pieces(groups[t])
            task_pieces[t] = list(range(len(pieces), len(pieces) + len(ps)))
            for q in ps:
                pieces.append(q)
                piece_task.append(t)
        piece_of = {}
        for i, q in enumerate(pieces):
            for v in q:
                piece_of[v] = i
        nP = len(pieces)
        p_succ = [set() for _ in range(nP)]
        p_ext_pred = [set() for _ in range(nP)]
        p_ext_succ = [set() for _ in range(nP)]
        for i, q in enumerate(pieces):
            for v in q:
                for w in m.succ[v]:
                    j = piece_of.get(w)
                    if j is None:
                        p_ext_succ[i].add(owner[w])
                    elif j != i:
                        p_succ[i].add(j)
                for u in m.pred[v]:
                    if u not in piece_of:
                        p_ext_pred[i].add(owner[u])
        # 微块级可达性（窗口内路径不会经窗口外返回）
        indeg = [0] * nP
        for i in range(nP):
            for j in p_succ[i]:
                indeg[j] += 1
        order_p = [i for i in range(nP) if indeg[i] == 0]
        for i in order_p:                     # Kahn（列表边遍历边追加）
            for j in p_succ[i]:
                indeg[j] -= 1
                if indeg[j] == 0:
                    order_p.append(j)
        if len(order_p) != nP:
            return None                       # 理论上不可达：微块商图有环
        reach = [set() for _ in range(nP)]
        for i in reversed(order_p):
            r = set()
            for j in p_succ[i]:
                r.add(j)
                r |= reach[j]
            reach[i] = r

        # --- 候选族
        cands = []           # frozenset(pieces)
        seen = set()

        def add(ps):
            fs = frozenset(ps)
            if fs in seen:
                return
            # 凸性：不存在 i∈fs → j∉fs → l∈fs 的路径
            for i in fs:
                for j in p_succ[i]:
                    if j not in fs and reach[j] & fs:
                        return
            seen.add(fs)
            cands.append(fs)

        for t in W:
            tp = task_pieces[t]
            for a in range(len(tp)):
                for b in range(a, len(tp)):
                    add(tp[a:b + 1])
        # 同核相邻 / 有依赖的 Task 对合并与边界平移
        pairs = set()
        for order in orders:
            inw = [t for t in order if t in Wset]
            for a, b in zip(inw, inw[1:]):
                pairs.add((a, b))
        for t in W:
            for i in task_pieces[t]:
                for j in p_succ[i]:
                    u = piece_task[j]
                    if u != t:
                        pairs.add((t, u))
        for a, b in pairs:
            pa, pb = task_pieces[a], task_pieces[b]
            add(pa + pb)
            if len(pa) > 1:
                add(pa[-1:] + pb)
            if len(pb) > 1:
                add(pa + pb[:1])
        # 同核连续三元组合并（减少 Task 数与边界搬运）
        for order in orders:
            inw = [t for t in order if t in Wset]
            for a, b, c in zip(inw, inw[1:], inw[2:]):
                add(task_pieces[a] + task_pieces[b] + task_pieces[c])
        if time.time() > deadline:
            return None

        # --- 候选时长（官方 Step1-3 局部 makespan，缓存）与争用标定
        ratio_num = sum(end[t] - start[t] for t in W)
        ratio_den = sum(self.cache.get(groups[t]).local_makespan for t in W)
        ratio = max(1.0, ratio_num / max(1, ratio_den))
        c_ops, c_dur, c_bytes = [], [], []
        for fs in cands:
            ops = [v for i in fs for v in pieces[i]]
            info = self.cache.get(ops)
            c_ops.append(ops)
            c_dur.append(max(1, int(round(info.local_makespan * ratio))))
            c_bytes.append(info.copy_bytes + info.spill_bytes)
            if time.time() > deadline:
                return None
        nC = len(cands)
        self.stats['candidates'] += nC
        # 候选间依赖、外部释放与尾部
        c_succ_p = []
        for fs in cands:
            s = set()
            for i in fs:
                s |= p_succ[i]
            c_succ_p.append(s - fs)
        by_piece = defaultdict(list)
        for g, fs in enumerate(cands):
            for i in fs:
                by_piece[i].append(g)
        # 各核 t0 前最后一个 Task 的结束时刻、t1 后第一个 Task 的剩余长度
        core_ready = [0] * k
        core_tail = [None] * k
        for c, order in enumerate(orders):
            before = [t for t in order if start[t] < t0]
            if before:
                core_ready[c] = end[before[-1]] + same
            after = [t for t in order if start[t] >= t1]
            if after:
                core_tail[c] = makespan - start[after[0]]
        # 预先计算每个候选的释放时刻、尾部项与候选间依赖对
        rel = [[0] * k for _ in range(nC)]
        tail = [0] * nC
        for g in range(nC):
            ext_pred = set()
            ext_succ = set()
            for i in cands[g]:
                ext_pred |= p_ext_pred[i]
                ext_succ |= p_ext_succ[i]
            for c in range(k):
                r = core_ready[c]
                for X in ext_pred:
                    r = max(r, end[X] + (cross if core_of[X] != c else 0))
                rel[g][c] = r
            for X in ext_succ:
                tail[g] = max(tail[g], cross + makespan - start[X])
        dep_pairs = []
        for g in range(nC):
            if not c_succ_p[g]:
                continue
            hs = set()
            for j in c_succ_p[g]:
                hs.update(by_piece[j])
            for h in sorted(hs):
                if not (cands[h] & cands[g]):
                    dep_pairs.append((g, h))
        mu = self.mu / self.m.bandwidth
        c_cost = [int(round(mu * c_bytes[g])) for g in range(nC)]
        family = {'nP': nP, 'cands': cands, 'by_piece': by_piece, 'dur': c_dur,
                  'cost': c_cost, 'rel': rel, 'tail': tail, 'core_tail': core_tail,
                  'dep_pairs': dep_pairs, 'k': k, 'same': same, 'cross': cross,
                  'c_ops': c_ops, 'W': W, 't0': t0, 't1': t1}
        self.last_family = family
        if getattr(self, 'build_only', False):
            return family
        # --- CP-SAT
        md = cp_model.CpModel()
        H = int(makespan * 3 + sum(c_dur) + 10000)
        a = [md.NewBoolVar('a%d' % g) for g in range(nC)]
        x = [[md.NewBoolVar('x%d_%d' % (g, c)) for c in range(k)] for g in range(nC)]
        S = [md.NewIntVar(0, H, 'S%d' % g) for g in range(nC)]
        F = [md.NewIntVar(0, H, 'F%d' % g) for g in range(nC)]
        Z = md.NewIntVar(0, 2 * H, 'Z')
        for i in range(nP):
            md.AddExactlyOne(a[g] for g in by_piece[i])
        intervals = [[] for _ in range(k)]
        for g in range(nC):
            md.Add(F[g] == S[g] + c_dur[g])
            md.Add(sum(x[g]) == a[g])
            E = md.NewIntVar(0, H + same, 'E%d' % g)
            md.Add(E == F[g] + same)
            for c in range(k):
                iv = md.NewOptionalIntervalVar(S[g], c_dur[g] + same, E, x[g][c], 'iv%d_%d' % (g, c))
                intervals[c].append(iv)
                if rel[g][c] > 0:
                    md.Add(S[g] >= rel[g][c]).OnlyEnforceIf(x[g][c])
                if core_tail[c] is not None:
                    md.Add(Z >= F[g] + same + core_tail[c]).OnlyEnforceIf(x[g][c])
            md.Add(Z >= F[g] + tail[g]).OnlyEnforceIf(a[g])
        for c in range(k):
            md.AddNoOverlap(intervals[c])
        # 依赖：g → h（两者都被选）；h 在核 c：g 也在 c 则 S_h ≥ F_g
        # （NoOverlap 另加 100），否则 S_h ≥ F_g + 1000
        npairs = len(dep_pairs)
        for g, h in dep_pairs:
            for c in range(k):
                md.Add(S[h] >= F[g]).OnlyEnforceIf([x[h][c], x[g][c]])
                md.Add(S[h] >= F[g] + cross).OnlyEnforceIf([x[h][c], x[g][c].Not(), a[g]])
        md.Minimize(Z + sum(c_cost[g] * a[g] for g in range(nC)))
        # hint：当前方案（每个 Task 的完整微块区间）
        full = {frozenset(task_pieces[t]): t for t in W}
        for g, fs in enumerate(cands):
            t = full.get(fs)
            md.AddHint(a[g], 1 if t is not None else 0)
            for c in range(k):
                md.AddHint(x[g][c], 1 if (t is not None and core_of[t] == c) else 0)
            if t is not None:
                md.AddHint(S[g], int(start[t]))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(0.5, min(self.solver_time, deadline - time.time()))
        solver.parameters.num_search_workers = self.workers
        solver.parameters.random_seed = 0
        status = solver.Solve(md)
        name = solver.StatusName(status)
        self.stats[{'OPTIMAL': 'optimal', 'FEASIBLE': 'feasible', 'INFEASIBLE': 'infeasible'}.get(name, 'unknown')] += 1
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        # --- 解码
        choice = []
        for g in range(nC):
            if solver.Value(a[g]):
                c = next(c for c in range(k) if solver.Value(x[g][c]))
                choice.append((g, c, solver.Value(S[g])))
        new_groups, new_orders = self.decode(groups, orders, sim, family, choice)
        info = {'status': name, 'window_tasks': len(W), 'pieces': nP, 'candidates': nC,
                'dep_pairs': npairs, 'objective': solver.ObjectiveValue(),
                'bound': solver.BestObjectiveBound(), 'wall': solver.WallTime()}
        return new_groups, new_orders, info

    def decode(self, groups, orders, sim, family, choice):
        """choice: [(候选 g, 核 c, 规划开始时刻)] -> (groups, core_orders)。"""
        start = sim['task_start']
        Wset = set(family['W'])
        t0, t1 = family['t0'], family['t1']
        new_groups, remap = [], {}
        for t, ops in enumerate(groups):
            if t not in Wset:
                remap[t] = len(new_groups)
                new_groups.append(ops)
        win_tasks = []
        for g, c, s in choice:
            win_tasks.append((s, g, c, len(new_groups)))
            new_groups.append(sorted(family['c_ops'][g], key=self.pos.__getitem__))
        new_orders = []
        for c, order in enumerate(orders):
            before = [remap[t] for t in order if t not in Wset and start[t] < t0]
            after = [remap[t] for t in order if t not in Wset and start[t] >= t1]
            mid = [nid for s, g, cc, nid in sorted(win_tasks) if cc == c]
            new_orders.append(before + mid + after)
        return new_groups, new_orders

    # ------------------------------------------------------------------
    def run(self, groups, orders, sim, time_budget, window_tasks=16, max_rounds=10**9):
        """在时间预算内沿关键链滑动窗口，保留精确评估更优的方案。"""
        t_end = time.time() + time_budget
        best = (sim['makespan'], sim['added_copy_bytes'])
        fails = 0
        rounds = 0
        tried = set()
        while time.time() < t_end - 0.5 and rounds < max_rounds:
            rounds += 1
            chain = critical_chain(sim, orders)
            by_start = sorted(range(len(groups)), key=lambda t: (sim['task_start'][t], t))
            rank = {t: i for i, t in enumerate(by_start)}
            # 关键链上依次取锚点；已失败的 (锚点, 窗宽) 不再重复
            anchor = None
            wsize = window_tasks
            for t in chain:
                key = (hash(frozenset(groups[t])), wsize)
                if key not in tried:
                    anchor = t
                    tried.add(key)
                    break
            if anchor is None:
                if wsize >= len(groups) or fails > 6:
                    break
                tried.clear()
                window_tasks = int(window_tasks * 1.5) + 1
                fails += 1
                continue
            i = rank[anchor]
            lo = max(0, i - wsize // 3)
            hi = min(len(by_start), lo + wsize)
            t0 = sim['task_start'][by_start[lo]]
            t1 = sim['task_start'][by_start[hi]] if hi < len(by_start) else float('inf')
            self.stats['windows'] += 1
            res = self.optimize_window(groups, orders, sim, t0, t1, t_end)
            if res is None:
                continue
            ng, no, info = res
            new_sim = self.evaluate(ng, no)
            val = (new_sim['makespan'], new_sim['added_copy_bytes'])
            self.log('    window[{:.0f},{:.0f}) tasks={} cand={} {} obj={:.0f} -> exact {} (best {})'.format(
                t0, t1 if t1 != float('inf') else -1, info['window_tasks'], info['candidates'],
                info['status'], info['objective'], val[0], best[0]))
            if val < best:
                best = val
                groups, orders, sim = ng, no, new_sim
                self.stats['improved'] += 1
                tried.clear()
        return groups, orders, sim
