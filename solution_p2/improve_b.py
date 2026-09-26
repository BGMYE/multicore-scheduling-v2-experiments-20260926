"""场景 B 局部规划（方案文档 2.2）：多资源超图分核窗口 + 核内顺序/驻留窗口，交替进行。

方案表示：State = (子图列表, 子图→核, 子图优先级)。``decode`` 在子图依赖 DAG 上按优先级做
Kahn 拓扑排序，得到一个全局拓扑序，每核顺序取其子序列——因此任意优先级都给出
官方合法、且不会形成“本地依赖 + Pipe FIFO + 跨核 COPY”等待环的方案。

每轮（对应文档 V2_B 伪代码）：
  1. ``assign_window``（2.2.1 前半）：在一段全局顺序窗口内，对微块（子图）求解
     CP-SAT：x_bk 分核；张量超边的 u/v/z/w 线性化计算官方口径的跨核搬运
     （内部张量每个远端消费核 2·b_t：一次 COPY_OUT + 一次 COPY_IN；原始输入每个读取核
     b_t）；窗口时间段内两类 Pipe 负载 L_{k,M}, L_{k,V}（含窗口外同时间段的固定负载）；
     目标 min Z + λ·D/bw（Z ≥ L_{k,r}），并加微小的“改动惩罚”保持稳定。
  2. ``order_window``（2.2.1 后半）：在关键核上取连续若干子图，单核排序 CP-SAT：
     区间 NoOverlap、释放时刻 = 实测跨核输入到达（COPY_OUT 结束 + 500），核内依赖/可达
     先行，目标 min 最大(完成+尾部) + μ·Σ(b_t/C)·驻留跨度（缩短长寿命张量驻留）。
  3. ``time_feedback``：按精确仿真的子图实际开始时刻重排优先级。
每个候选都用官方评估器（eval_b）完整回放，按 (Makespan, 新增搬运) 字典序择优。
"""

import heapq
import time
from collections import defaultdict

try:
    from ortools.sat.python import cp_model
    HAVE_ORTOOLS = True
except ImportError:
    HAVE_ORTOOLS = False

import eval_b


def _one(v):
    return isinstance(v, int) and v == 1


def _zero(v):
    return isinstance(v, int) and v == 0


class State:
    def __init__(self, groups, core, prio):
        self.groups = [list(g) for g in groups]
        self.core = list(core)
        self.prio = list(prio)


def state_from_plan(groups, orders):
    """(groups, core_orders) → State；优先级取一个与各核顺序一致的全局拓扑位置。"""
    core = [0] * len(groups)
    rank = [0] * len(groups)
    for c, order in enumerate(orders):
        for i, g in enumerate(order):
            core[g] = c
            rank[g] = i
    return State(groups, core, [float(r) for r in rank]), orders


class Improver:
    def __init__(self, model, graph, cfg, k, log=print, solver_time=3.0):
        self.m = model
        self.graph = graph
        self.cfg = cfg
        self.k = k
        self.log = log
        self.solver_time = solver_time
        self.stats = defaultdict(int)
        tb = model.tensor_by_id
        self.size = {t: tb[t]['size'] for t in tb}
        self.prod = {}
        for t, ps in model.producers.items():
            for p in ps:
                if p in model.comp_set:
                    self.prod[t] = p
        self.cons = {t: [c for c in model.consumers.get(t, ()) if c in model.comp_set]
                     for t in tb}
        self.tpos = {t: tb[t]['pos'] for t in tb}
        self._win = None

    def window_optimizer(self):
        """复用问题一的候选族/CP-SAT 区间模型，参数换成场景 B 的时序规则：
        同核子图之间无切换等待（same=0），跨核依赖 = 500 cycles 同步 + 一次 COPY_OUT
        与一次 COPY_IN（按中位边界张量大小估计）。时长代理 d_g 仍取官方 Step1-3 的
        局部 makespan（含边界搬运），由窗口内实测时长标定。"""
        if self._win is None:
            import statistics
            from construct import dfs_order
            from fast_eval import TaskCache
            import window_cpsat
            sizes = [self.size[t] for t in self.prod if self.cons.get(t)] or [0]
            copy = 2 * statistics.median(sizes) / self.m.bandwidth
            cfg = {'capacity': self.cfg['capacity'], 'bandwidth': self.cfg['bandwidth'],
                   'cross_wait': int(self.cfg['delay'] + copy), 'same_wait': 0}
            cache = TaskCache(self.m, cfg)
            pos = {v: i for i, v in enumerate(dfs_order(self.m))}
            self._win = window_cpsat.WindowOptimizer(
                self.m, cache, cfg, self.k, pos, log=lambda *_: None,
                solver_time=self.solver_time)
        return self._win

    def sim_view(self, res, groups, orders):
        g_start, g_end, _ = self.timing(res, groups, orders)
        core, _ = self.canonical(groups, orders)
        return {'makespan': res['makespan'],
                'added_copy_bytes': res['data_movement_bytes']['added_copy_bytes'],
                'task_start': g_start, 'task_end': g_end,
                'core_of': dict(enumerate(core))}

    def interval_window(self, groups, orders, res, lo, span):
        """文档 2.1 的区间模型在场景 B 下的窗口：按实测开始时刻取第 [lo, lo+span) 个子图。"""
        opt = self.window_optimizer()
        sim = self.sim_view(res, groups, orders)
        by_time = sorted(range(len(groups)), key=lambda g: (sim['task_start'][g], g))
        if len(by_time) < 2:
            return None
        lo = min(lo, max(0, len(by_time) - span))
        t0 = sim['task_start'][by_time[lo]]
        hi = lo + span
        t1 = sim['task_start'][by_time[hi]] if hi < len(by_time) else float('inf')
        out = opt.optimize_window(groups, orders, sim, t0, t1, time.time() + 4 * self.solver_time)
        if out is None:
            return None
        ng, no, info = out
        self.stats['interval_' + info['status']] += 1
        return ng, no

    # ------------------------------------------------------------------
    def quotient(self, groups):
        owner = {}
        for g, ops in enumerate(groups):
            for v in ops:
                owner[v] = g
        succ = [set() for _ in groups]
        for g, ops in enumerate(groups):
            for v in ops:
                for w in self.m.succ[v]:
                    h = owner[w]
                    if h != g:
                        succ[g].add(h)
        return owner, succ

    def decode(self, st):
        """优先级 Kahn → 每核顺序；商图有环时返回 None。"""
        groups = [g for g in st.groups]
        _, succ = self.quotient(groups)
        n = len(groups)
        indeg = [0] * n
        for g in range(n):
            for h in succ[g]:
                indeg[h] += 1
        heap = [(st.prio[g], g) for g in range(n) if indeg[g] == 0]
        heapq.heapify(heap)
        orders = [[] for _ in range(self.k)]
        seen = 0
        while heap:
            _, g = heapq.heappop(heap)
            orders[st.core[g]].append(g)
            seen += 1
            for h in succ[g]:
                indeg[h] -= 1
                if indeg[h] == 0:
                    heapq.heappush(heap, (st.prio[h], h))
        if seen != n:
            return None
        return orders

    def evaluate(self, st):
        orders = self.decode(st)
        if orders is None:
            return None, None
        # 去掉空子图、重新编号
        groups, new_orders = [], []
        for order in orders:
            row = []
            for g in order:
                if st.groups[g]:
                    row.append(len(groups))
                    groups.append(st.groups[g])
            new_orders.append(row)
        self.stats['evals'] += 1
        try:
            res = eval_b.evaluate_groups(self.graph, groups, new_orders, self.cfg)
        except eval_b.EvalError as error:
            self.stats['eval_errors'] += 1
            self.log('    [invalid] ' + str(error)[:120])
            return None, None
        return res, (groups, new_orders)

    def evaluate_direct(self, groups, orders):
        groups, orders = [list(g) for g in groups], [list(o) for o in orders]
        self.stats['evals'] += 1
        try:
            res = eval_b.evaluate_groups(self.graph, groups, orders, self.cfg)
        except eval_b.EvalError as error:
            self.stats['eval_errors'] += 1
            self.log('    [invalid] ' + str(error)[:120])
            return None, None
        return res, (groups, orders)

    def global_prio(self, groups, orders, g_start=None):
        """与当前各核顺序完全一致的全局拓扑位置（依赖 + 核内顺序边上的 Kahn）。
        以它为优先级 decode 会原样复现当前方案；各邻域只在此基础上做局部改动。"""
        _, succ = self.quotient(groups)
        n = len(groups)
        nxt = [set(s) for s in succ]
        for order in orders:
            for a, b in zip(order, order[1:]):
                nxt[a].add(b)
        indeg = [0] * n
        for g in range(n):
            for h in nxt[g]:
                indeg[h] += 1
        key = (lambda g: (g_start.get(g, 0), g)) if g_start else (lambda g: (0, g))
        heap = [(key(g), g) for g in range(n) if indeg[g] == 0]
        heapq.heapify(heap)
        prio = [0.0] * n
        i = 0
        while heap:
            _, g = heapq.heappop(heap)
            prio[g] = float(i)
            i += 1
            for h in nxt[g]:
                indeg[h] -= 1
                if indeg[h] == 0:
                    heapq.heappush(heap, (key(h), h))
        return prio

    @staticmethod
    def canonical(groups, orders):
        core = [0] * len(groups)
        prio = [0.0] * len(groups)
        for c, order in enumerate(orders):
            for i, g in enumerate(order):
                core[g] = c
                prio[g] = float(i)
        return core, prio

    def timing(self, res, groups, orders):
        """从官方结果提取子图实测起止与跨核到达。"""
        g_start, g_end = {}, {}
        op_sub = {}
        for core in res['per_core_timeline']:
            for s in core['subgraphs']:
                g_start[s['subgraph_id']] = s['start']
                g_end[s['subgraph_id']] = s['end']
            for o in core['ops']:
                op_sub[(core['core_id'], o['op_id'])] = o['subgraph_id']
        arrive = defaultdict(int)
        for t in res['cross_core_transfers']:
            g = op_sub.get((t['target_core'], t['target_copy_in_id']))
            if g is not None:
                arrive[g] = max(arrive[g], t['copy_in_release'])
        return g_start, g_end, arrive

    # ------------------------------------------------------------------
    def time_feedback(self, groups, orders, res):
        g_start, _, _ = self.timing(res, groups, orders)
        core, _ = self.canonical(groups, orders)
        pos = {g: i for order in orders for i, g in enumerate(order)}
        prio = [g_start[g] + 1e-6 * pos[g] for g in range(len(groups))]
        return State(groups, core, prio)

    # ------------------------------------------------------------------
    def assign_window(self, groups, orders, res, lo, hi, lam=None, move_pen=0.02,
                      max_blocks=40):
        """窗口：按实测开始时刻排序后第 [lo, hi) 个子图。"""
        m, k = self.m, self.k
        g_start, g_end, _ = self.timing(res, groups, orders)
        core, prio0 = self.canonical(groups, orders)
        by_time = sorted(range(len(groups)), key=lambda g: (g_start[g], g))
        W = by_time[lo:hi][:max_blocks]
        if len(W) < 2:
            return None
        Wset = set(W)
        t0 = min(g_start[g] for g in W)
        t1 = max(g_end[g] for g in W)
        owner = {}
        for g, ops in enumerate(groups):
            for v in ops:
                owner[v] = g
        # 负载：窗口子图各自的 M/V 周期；窗口外、时间段 [t0,t1] 内的固定负载按重叠比例计
        wl = {g: [sum(m.cyc[v] for v in groups[g] if m.is_m[v]),
                  sum(m.cyc[v] for v in groups[g] if not m.is_m[v])] for g in W}
        fixed = [[0.0, 0.0] for _ in range(k)]
        for g in range(len(groups)):
            if g in Wset:
                continue
            s, e = g_start[g], g_end[g]
            ov = max(0, min(e, t1) - max(s, t0))
            if ov <= 0 or e <= s:
                continue
            f = ov / (e - s)
            for r, is_m in ((0, True), (1, False)):
                fixed[core[g]][r] += f * sum(m.cyc[v] for v in groups[g] if m.is_m[v] == is_m)
        # 相关张量：被窗口子图生产或消费的
        tens = set()
        for g in W:
            for v in groups[g]:
                tens.update(m.in_tensors.get(v, ()))
                tens.update(m.out_tensors.get(v, ()))
        tens = [t for t in tens if self.cons.get(t)]
        md = cp_model.CpModel()
        x = {(g, c): md.NewBoolVar('x%d_%d' % (g, c)) for g in W for c in range(k)}
        for g in W:
            md.AddExactlyOne(x[(g, c)] for c in range(k))

        def on_core(v, c):
            """op v 在核 c 的布尔表达：窗口内为变量，窗口外为常数。"""
            g = owner[v]
            if g in Wset:
                return x[(g, c)]
            return 1 if core[g] == c else 0

        comm_terms = []
        base_comm = 0
        for t in tens:
            b = self.size[t]
            cons = self.cons[t]
            vv = []
            for c in range(k):
                lits = [on_core(v, c) for v in cons]
                if any(_one(l) for l in lits):
                    vv.append(1)
                    continue
                lits = [l for l in lits if not _zero(l)]
                if not lits:
                    vv.append(0)
                    continue
                y = md.NewBoolVar('')
                md.AddMaxEquality(y, lits)           # v_tc = OR(消费者在 c)
                vv.append(y)
            p = self.prod.get(t)
            if p is None:                              # 原始输入：每个读取核一次
                for c in range(k):
                    if _one(vv[c]):
                        base_comm += b
                    elif not _zero(vv[c]):
                        comm_terms.append((b, vv[c]))
                continue
            for c in range(k):                         # z_tc = v_tc ∧ ¬u_tc
                u = on_core(p, c)
                if _zero(vv[c]) or _one(u):
                    continue
                if _one(vv[c]) and _zero(u):
                    base_comm += 2 * b
                    continue
                z = md.NewBoolVar('')
                vl = vv[c]
                if _one(vl):
                    md.Add(z == 1 - u)
                elif _zero(u):
                    md.Add(z == vl)
                else:
                    md.Add(z <= vl)
                    md.Add(z <= 1 - u)
                    md.Add(z >= vl - u)
                comm_terms.append((2 * b, z))
        Z = md.NewIntVar(0, 10 ** 12, 'Z')
        for c in range(k):
            for r in range(2):
                md.Add(Z >= int(fixed[c][r]) + sum(wl[g][r] * x[(g, c)] for g in W))
        bw = m.bandwidth
        lam = (1.0 / k) if lam is None else lam
        span = max(1.0, t1 - t0)
        obj = [Z]
        obj += [int(round(lam * w / bw)) * z for w, z in comm_terms]
        obj += [int(round(move_pen * span / len(W))) * (1 - x[(g, core[g])]) for g in W]
        md.Minimize(sum(obj))
        for g in W:
            for c in range(k):
                md.AddHint(x[(g, c)], 1 if core[g] == c else 0)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.solver_time
        solver.parameters.num_search_workers = 1
        status = solver.Solve(md)
        self.stats['assign_' + solver.StatusName(status)] += 1
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        new_core = list(core)
        changed = 0
        for g in W:
            c = next(c for c in range(k) if solver.Value(x[(g, c)]))
            if c != core[g]:
                changed += 1
            new_core[g] = c
        if not changed:
            return None
        prio = self.global_prio(groups, orders, g_start)
        return State(groups, new_core, prio), changed

    # ------------------------------------------------------------------
    def order_window(self, groups, orders, res, c, i0, n, mu=0.05):
        """关键核 c 上第 [i0, i0+n) 个子图的单核排序（含驻留跨度惩罚）。"""
        m = self.m
        g_start, g_end, arrive = self.timing(res, groups, orders)
        order = orders[c]
        W = order[i0:i0 + n]
        if len(W) < 3:
            return None
        Wset = set(W)
        owner, succ = self.quotient(groups)
        makespan = res['makespan']
        # 窗口内可达关系（经任意核的路径）→ 先行
        idx = {g: i for i, g in enumerate(W)}
        reach_pairs = []
        for g in W:
            stack, seen = list(succ[g]), set()
            while stack:
                h = stack.pop()
                if h in seen:
                    continue
                seen.add(h)
                if h in Wset:
                    reach_pairs.append((g, h))
                if g_start.get(h, 0) <= g_end[W[-1]] + 1:
                    stack.extend(succ[h])
        dur = {g: max(1, g_end[g] - g_start[g]) for g in W}
        t_base = g_start[W[0]]
        tail = {}
        for g in W:
            tl = 0
            for h in succ[g]:
                if h not in Wset:
                    tl = max(tl, makespan - g_start.get(h, makespan))
            tail[g] = tl
        md = cp_model.CpModel()
        H = int(t_base + sum(dur.values()) * 3 + makespan)
        S = {g: md.NewIntVar(int(t_base), H, 'S%d' % g) for g in W}
        F = {g: md.NewIntVar(0, H, 'F%d' % g) for g in W}
        iv = []
        for g in W:
            md.Add(F[g] == S[g] + dur[g])
            iv.append(md.NewIntervalVar(S[g], dur[g], F[g], 'iv%d' % g))
            if arrive.get(g, 0) > t_base:
                md.Add(S[g] >= int(arrive[g]))
        md.AddNoOverlap(iv)
        for g, h in reach_pairs:
            md.Add(S[h] >= S[g] + 1)
        Z = md.NewIntVar(0, 4 * H, 'Z')
        for g in W:
            md.Add(Z >= F[g] + tail[g])
        # 驻留跨度：窗口内生产、窗口内消费的片上张量
        cap = self.cfg['capacity']
        terms = []
        for g in W:
            for v in groups[g]:
                for t in m.out_tensors.get(v, ()):
                    last = [owner[u] for u in self.cons.get(t, ()) if owner[u] in Wset and owner[u] != g]
                    if not last or self.tpos.get(t) not in cap:
                        continue
                    for h in set(last):
                        sp = md.NewIntVar(0, H, '')
                        md.Add(sp >= F[h] - S[g])
                        w = self.size[t] / cap[self.tpos[t]]
                        terms.append((w, sp))
        md.Minimize(Z * 100 + sum(int(round(100 * mu * w)) * sp for w, sp in terms if w > 0))
        for g in W:
            md.AddHint(S[g], int(g_start[g]))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.solver_time
        solver.parameters.num_search_workers = 1
        status = solver.Solve(md)
        self.stats['order_' + solver.StatusName(status)] += 1
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        new_seq = sorted(W, key=lambda g: solver.Value(S[g]))
        if new_seq == W:
            return None
        core, _ = self.canonical(groups, orders)
        prio = self.global_prio(groups, orders, g_start)
        # 窗口内按新顺序重新分配优先级（落在原时间段内）
        slots = sorted(prio[g] for g in W)
        for g, p in zip(new_seq, slots):
            prio[g] = p
        return State(groups, core, prio)

    # ------------------------------------------------------------------
    def merge_split(self, groups, orders, res, max_ops=512):
        """粒度调整：同核、核内顺序相邻的小子图 a,b 在“b 不能经其他子图从 a 到达”时合并
        （合并后仍凸、商图无环），让 Step1 在更大的桶内做反向 DFS，改善驻留。"""
        g_start, g_end, _ = self.timing(res, groups, orders)
        _, succ = self.quotient(groups)
        core, _ = self.canonical(groups, orders)
        merged_into = {}
        new_groups = [list(x) for x in groups]

        def reach_other(a, b):
            limit = g_start[b]
            stack = [h for h in succ[a] if h != b]
            seen = set()
            while stack:
                h = stack.pop()
                if h == b:
                    return True
                if h in seen or g_start.get(h, 0) > limit:
                    continue
                seen.add(h)
                stack.extend(succ[h])
            return False

        count = 0
        for order in orders:
            i = 0
            while i + 1 < len(order):
                a, b = order[i], order[i + 1]
                a_root = merged_into.get(a, a)
                if len(new_groups[a_root]) + len(new_groups[b]) <= max_ops and \
                        not reach_other(a, b):
                    new_groups[a_root] += new_groups[b]
                    new_groups[b] = []
                    merged_into[b] = a_root
                    succ[a] = (succ[a] | succ[b]) - {a, b}
                    count += 1
                i += 1
        if not count:
            return None
        prio = self.global_prio(groups, orders, g_start)
        return State(new_groups, core, prio)

    def single_moves(self, groups, orders, res, cursor, n_moves=6):
        """细粒度邻域（精确回放判优）：对最晚结束核上结束最晚的子图，
        (a) 改分到当前结束最早的核；(b) 在本核顺序中与前一个子图交换优先级。"""
        g_start, g_end, _ = self.timing(res, groups, orders)
        core, _ = self.canonical(groups, orders)
        ends = [res['per_core_timeline'][c]['tasks'][0]['end'] for c in range(self.k)]
        crit = max(range(self.k), key=lambda c: ends[c])
        cand = sorted(orders[crit], key=lambda g: -g_end[g])
        out = []
        if not cand:
            return out
        base_prio = self.global_prio(groups, orders, g_start)
        for j in range(n_moves):
            g = cand[(cursor + j) % len(cand)]
            targets = sorted((c for c in range(self.k) if c != crit), key=lambda c: ends[c])
            if targets:
                new_core = list(core)
                new_core[g] = targets[0]
                out.append(('move sg{}->c{}'.format(g, targets[0]),
                            State(groups, new_core, base_prio)))
            order = orders[crit]
            i = order.index(g)
            if i > 0:
                prio = list(base_prio)
                a = order[i - 1]
                prio[g], prio[a] = prio[a], prio[g]
                out.append(('swap sg{}<->sg{}'.format(a, g), State(groups, core, prio)))
        return out

    # ------------------------------------------------------------------
    def run(self, groups, orders, res, budget, window=24):
        t_end = time.time() + budget
        best = eval_b.score(res)
        rounds = 0
        stalls = 0
        n_groups_start = len(groups)
        while time.time() < t_end - 1 and stalls < 15:
            rounds += 1
            cands = []
            step = rounds % 6
            if step == 1:
                cands.append(('time-feedback', self.time_feedback(groups, orders, res)))
                if len(groups) > 2 * self.k:
                    st = self.merge_split(groups, orders, res)
                    if st:
                        cands.append(('merge', st))
            elif step in (2, 0) and HAVE_ORTOOLS:
                n = len(groups)
                span = min(16, n)
                lo = ((rounds // 6) * span // 2 + (span // 4 if step == 0 else 0)) % max(1, n)
                out = self.interval_window(groups, orders, res, lo, span)
                if out:
                    cands.append(('interval[{}:{}]'.format(lo, lo + span), out))
            elif step == 3 and HAVE_ORTOOLS:
                n = len(groups)
                span = min(window, n)
                lo = ((rounds // 6) * span // 2) % max(1, n - span + 1)
                out = self.assign_window(groups, orders, res, lo, lo + span)
                if out:
                    cands.append(('assign[{}:{}] moved={}'.format(lo, lo + span, out[1]), out[0]))
            elif step == 5:
                cands.extend(self.single_moves(groups, orders, res, rounds // 6 * 6))
            elif HAVE_ORTOOLS:
                ends = [res['per_core_timeline'][c]['tasks'][0]['end'] for c in range(self.k)]
                c = max(range(self.k), key=lambda i: ends[i])
                n = len(orders[c])
                if n >= 3:
                    w = min(12, n)
                    i0 = ((rounds // 6) * w // 2) % max(1, n - w + 1)
                    st = self.order_window(groups, orders, res, c, i0, w)
                    if st:
                        cands.append(('order core{}[{}:{}]'.format(c, i0, i0 + w), st))
            improved = False
            for tag, st in cands:
                if time.time() > t_end:
                    break
                if isinstance(st, tuple):
                    new_res, plan = self.evaluate_direct(*st)
                else:
                    new_res, plan = self.evaluate(st)
                if new_res is None:
                    continue
                val = eval_b.score(new_res)
                self.log('    {} -> {} (best {})'.format(tag, val[0], best[0]))
                if val < best:
                    best = val
                    groups, orders = plan
                    res = new_res
                    self.stats['accepted'] += 1
                    self.stats['accepted_' + tag.split('[')[0].split(' ')[0]] += 1
                    improved = True
            stalls = 0 if improved else stalls + 1
        self.stats['rounds'] = rounds
        self.stats['subgraphs_start'] = n_groups_start
        return groups, orders, res
