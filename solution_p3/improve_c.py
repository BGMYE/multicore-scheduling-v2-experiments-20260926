"""问题三局部优化（方案文档 2.3：FIFO 事件状态与有限窗口候选优化）。

不把命中当作决策变量：命中/缺失完全由官方评估器按事件状态判定（COPY_IN 发射时查
FIFO；miss 完成后才插入；命中不重排；超过容量不缓存）。本模块只生成**合法的离散方案候选**，
每个候选都做完整全图回放（官方 evaluate_problem_3），按 (T_C, 新增搬运) 字典序择优。

流程（对应文档伪代码 V2_C）：
  1. 初值 = 问题二最终方案 P_B，在问题三评估器中回放，得到 Cache 事件轨迹；
  2. ``ranked_reuse_windows``：按“重复 miss 价值” = 张量字节 × (miss 次数 − 1) 排序，
     把 miss 分成两类：在途重复（首个读取尚未完成、尚未插入时的并发读取）与淘汰后重读；
  3. 对每个高价值张量 t 生成有限候选族 Ω_t（均为合法方案，经子图依赖 DAG 上的优先级 Kahn 解码）：
       * gather  ：把淘汰后重读 t 的子图优先级提到“首次读取 t 的子图”之后（首读完成后集中消费）；
       * stagger ：把在途重复读取 t 的子图推迟到首读完成时刻之后开始的位置；
       * colocate：把重复读取 t 的子图迁到首读核（同核后续消费者直接用片上副本，不再 COPY_IN）；
       * cluster ：对同一时间窗内的全部高价值张量同时做 gather（成组移动）；
  4. 剩余预算交给问题二的通用邻域（improve_b.Improver 的细粒度改核、优先级交换、合并、
     区间窗口 CP-SAT 等），但评分函数换成问题三评估器；
  5. 记录每个窗口的候选数、已评价数与是否穷尽。
"""

import time
from collections import defaultdict

import eval_c
import improve_b


class ImproverC(improve_b.Improver):
    """复用问题二 Improver 的方案表示/解码/邻域，评估器换成问题三。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.windows = []

    # --- 评估器替换 ------------------------------------------------------
    def evaluate(self, st):
        orders = self.decode(st)
        if orders is None:
            self.stats['decode_cycle'] += 1
            return None, None
        groups, new_orders = [], []
        for order in orders:
            row = []
            for g in order:
                if st.groups[g]:
                    row.append(len(groups))
                    groups.append(st.groups[g])
            new_orders.append(row)
        return self.evaluate_direct(groups, new_orders)

    def evaluate_direct(self, groups, orders):
        groups, orders = [list(g) for g in groups], [list(o) for o in orders]
        self.stats['evals'] += 1
        try:
            res = eval_c.evaluate_groups_c(self.graph, groups, orders, self.cfg)
        except eval_c.EvalError as error:
            self.stats['eval_errors'] += 1
            self.log('    [invalid] ' + str(error)[:120])
            return None, None
        return res, (groups, orders)

    # --- Cache 事件分析 ---------------------------------------------------
    def miss_analysis(self, res):
        """返回 {tensor: dict(size, misses=[(time, core, op, kind)], first_insert)}。"""
        info = defaultdict(lambda: {'size': 0, 'misses': [], 'hits': 0, 'first_insert': None})
        inserted = set()
        for e in res['cache_events']:
            t = e['tensor_id']
            if e['event'] == 'insert':
                if info[t]['first_insert'] is None:
                    info[t]['first_insert'] = e['time']
                inserted.add(t)
            elif e['event'] == 'miss':
                d = info[t]
                d['size'] = e['size_bytes']
                if t in inserted:
                    kind = 'evicted'
                elif d['misses']:
                    kind = 'inflight'
                else:
                    kind = 'first'
                d['misses'].append((e['time'], e['core_id'], e['op_id'], kind))
            elif e['event'] == 'hit':
                info[t]['hits'] += 1
        return info

    def ranked_reuse_windows(self, res, top=20):
        info = self.miss_analysis(res)
        ranked = []
        for t, d in info.items():
            m = len(d['misses'])
            if m >= 2:
                ranked.append((d['size'] * (m - 1), t))
        ranked.sort(reverse=True)
        return [(t, info[t], v) for v, t in ranked[:top]]

    def op_subgraph_map(self, res):
        m = {}
        for core in res['per_core_timeline']:
            for o in core['ops']:
                m[(core['core_id'], o['op_id'])] = o['subgraph_id']
        return m

    # --- 候选生成 ---------------------------------------------------------
    def window_candidates(self, groups, orders, res, t, d):
        g_start, g_end, _ = self.timing(res, groups, orders)
        core, _ = self.canonical(groups, orders)
        prio = self.global_prio(groups, orders, g_start)
        sub = self.op_subgraph_map(res)
        misses = sorted(d['misses'])
        first_t, first_core, first_op, _ = misses[0]
        g_first = sub.get((first_core, first_op))
        if g_first is None:
            return []
        first_done = d['first_insert'] if d['first_insert'] is not None else first_t
        cands = []
        later = []
        for (tm, c, op, kind) in misses[1:]:
            g = sub.get((c, op))
            if g is None or g == g_first:
                continue
            later.append((kind, g, c))
        if not later:
            return []
        # gather：淘汰后重读 → 紧跟首读子图
        ev = [g for kind, g, c in later if kind == 'evicted']
        if ev:
            p = list(prio)
            for i, g in enumerate(ev):
                p[g] = prio[g_first] + 0.1 + 0.01 * i
            cands.append(('gather', improve_b.State(groups, core, p)))
        # stagger：在途重复 → 推迟到首读完成后才开始的子图之前
        inf = [(g, c) for kind, g, c in later if kind == 'inflight']
        if inf:
            for tag, delta in (('stagger', 0.05), ('stagger2', 1.05)):
                p = list(prio)
                for g, c in inf:
                    after = [h for h in orders[c] if g_start[h] >= first_done and h != g]
                    if after:
                        # 放到“首读完成后才开始的第一个子图”之后（stagger2 再多让一个位置）
                        p[g] = prio[after[0]] + delta
                    else:
                        p[g] = max(prio) + 1
                cands.append((tag, improve_b.State(groups, core, p)))
        # colocate：重复读取者迁到首读核，并紧跟首读子图
        movers = [g for kind, g, c in later if c != first_core]
        if movers:
            nc = list(core)
            p = list(prio)
            for i, g in enumerate(movers[:4]):
                nc[g] = first_core
                p[g] = prio[g_first] + 0.1 + 0.01 * i
            cands.append(('colocate', improve_b.State(groups, nc, p)))
        return cands

    def cluster_candidate(self, groups, orders, res, windows):
        """同时对前若干个高价值张量做 gather（成组集中消费）。"""
        g_start, _, _ = self.timing(res, groups, orders)
        core, _ = self.canonical(groups, orders)
        prio = self.global_prio(groups, orders, g_start)
        sub = self.op_subgraph_map(res)
        p = list(prio)
        moved = 0
        for t, d, _ in windows:
            misses = sorted(d['misses'])
            g_first = sub.get((misses[0][1], misses[0][2]))
            if g_first is None:
                continue
            for i, (tm, c, op, kind) in enumerate(misses[1:]):
                g = sub.get((c, op))
                if g is None or g == g_first:
                    continue
                target = prio[g_first] + 0.1 + 0.001 * i
                if target < p[g]:
                    p[g] = target
                    moved += 1
        if not moved:
            return None
        return improve_b.State(groups, core, p)

    # --- 主循环 -----------------------------------------------------------
    def run_cache(self, groups, orders, res, budget, windows_top=12):
        """阶段一：重复 miss 窗口候选；阶段二：通用邻域（问题三评分）。"""
        t_end = time.time() + budget
        best = eval_c.score(res)
        phase1_end = time.time() + budget * 0.6
        tried = set()
        stalls = 0
        while time.time() < phase1_end and stalls < 3:
            windows = self.ranked_reuse_windows(res, top=windows_top)
            if not windows:
                break
            improved = False
            cl = self.cluster_candidate(groups, orders, res, windows)
            items = [('cluster', None, cl)] if cl else []
            for t, d, value in windows:
                if t in tried:
                    continue
                tried.add(t)
                for tag, st in self.window_candidates(groups, orders, res, t, d):
                    items.append((tag, t, st))
            if not items:
                break
            n_eval = 0
            for tag, t, st in items:
                if time.time() > phase1_end:
                    break
                n_eval += 1
                new_res, plan = self.evaluate(st)
                if new_res is None:
                    continue
                val = eval_c.score(new_res)
                self.log('    cache-{} t={} -> {} hit={:.3f} (best {})'.format(
                    tag, t, val[0], new_res['cache_stats']['hit_rate'], best[0]))
                if val < best:
                    best = val
                    groups, orders = plan
                    res = new_res
                    self.stats['accepted'] += 1
                    self.stats['accepted_cache_' + tag] += 1
                    improved = True
                    break          # 轨迹已变，重新排序窗口
            self.windows.append({'candidates': len(items), 'evaluated': n_eval,
                                 'exhausted': n_eval == len(items), 'improved': improved})
            stalls = 0 if improved else stalls + 1
        remaining = t_end - time.time()
        if remaining > 3:
            groups, orders, res = self.run(groups, orders, res, remaining)
        return groups, orders, res

    def run(self, groups, orders, res, budget, window=24):
        """问题二通用邻域，评分改为问题三（eval_c.score 与 eval_b.score 同构）。"""
        import eval_b
        saved = eval_b.score
        eval_b.score = eval_c.score
        try:
            return super().run(groups, orders, res, budget, window)
        finally:
            eval_b.score = saved
