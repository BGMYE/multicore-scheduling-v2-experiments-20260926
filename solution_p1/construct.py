"""初始可行方案构造：拓扑序 → 微块 → 连续区间子图 → 列表调度分核排序。

方案文档 2.1.1 的候选族取“同一拓扑序的连续区间”。任意拓扑序切成的
连续区间，其子图商图天然无环（依赖只会指向后面的区间），因此本模块
生成的每个方案都满足官方的子图 DAG 与同核顺序检查。

提供几种保持局部性的拓扑序：
* ``dfs``   ：从汇点出发的反向 DFS（与官方 Step1 同思路），同一输出的祖先锥连续；
* ``level`` ：按 ASAP 层次、层内按 dfs 位置，得到“水平带”；
* ``bl``    ：Kahn 算法 + 底层长度（bottom level）优先，偏向关键路径。
"""

import heapq
from collections import defaultdict

from fast_eval import simulate


# ----------------------------------------------------------------------
# 拓扑序
# ----------------------------------------------------------------------
def dfs_order(model):
    """反向 DFS：汇点按 id 升序，节点在全部前驱输出后才输出。"""
    pred = model.pred
    sinks = sorted(v for v in model.comp if not model.succ[v])
    seen = set()
    order = []
    for s in sinks:
        if s in seen:
            continue
        stack = [(s, iter(sorted(pred[s])))]
        seen.add(s)
        while stack:
            v, it = stack[-1]
            nxt = None
            for p in it:
                if p not in seen:
                    nxt = p
                    break
            if nxt is None:
                order.append(v)
                stack.pop()
            else:
                seen.add(nxt)
                stack.append((nxt, iter(sorted(pred[nxt]))))
    return order


def level_order(model, base):
    pos = {v: i for i, v in enumerate(base)}
    lvl = {}
    for v in model.topo:
        lvl[v] = max((lvl[p] + 1 for p in model.pred[v]), default=0)
    return sorted(model.comp, key=lambda v: (lvl[v], pos[v]))


def bottom_level_order(model, base):
    """Kahn：就绪节点按 (−bottom_level, dfs 位置) 取出。"""
    pos = {v: i for i, v in enumerate(base)}
    bl = model.bottom_level
    indeg = {v: len(model.pred[v]) for v in model.comp}
    heap = [(-bl[v], pos[v], v) for v in model.comp if indeg[v] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        _, _, v = heapq.heappop(heap)
        order.append(v)
        for w in model.succ[v]:
            indeg[w] -= 1
            if indeg[w] == 0:
                heapq.heappush(heap, (-bl[w], pos[w], w))
    return order


def all_orders(model):
    base = dfs_order(model)
    return {'dfs': base, 'level': level_order(model, base),
            'bl': bottom_level_order(model, base)}


def check_topological(model, order):
    pos = {v: i for i, v in enumerate(order)}
    return all(pos[v] < pos[w] for v in model.comp for w in model.succ[v])


# ----------------------------------------------------------------------
# 微块：拓扑序上的短区间（build_safe_microblocks）
# ----------------------------------------------------------------------
def microblocks(model, order, max_ops, max_cycles):
    """把拓扑序切成不超过 (max_ops, max_cycles) 的连续短区间。

    连续区间保证商图无环；优先在“数据不相连”的位置断开（即相邻两个
    op 之间没有依赖、也不共享输入张量），以免把强通信边切开。
    """
    blocks, cur, cyc = [], [], 0
    cyc_of = model.cyc
    for v in order:
        c = cyc_of[v]
        if cur and (len(cur) >= max_ops or cyc + c > max_cycles):
            blocks.append(cur)
            cur, cyc = [], 0
        cur.append(v)
        cyc += c
    if cur:
        blocks.append(cur)
    return blocks


# ----------------------------------------------------------------------
# 区间切分：按目标负载把微块序列切成 Task
# ----------------------------------------------------------------------
def segment_by_load(model, blocks, target):
    """顺序累积微块，负载 max(W_M, W_V) 达到 target 即切一刀。"""
    segs, cur, wm, wv = [], [], 0, 0
    for b in blocks:
        bm = sum(model.cyc[v] for v in b if model.is_m[v])
        bv = sum(model.cyc[v] for v in b if not model.is_m[v])
        if cur and max(wm + bm, wv + bv) > target and max(wm, wv) >= 0.5 * target:
            segs.append(cur)
            cur, wm, wv = [], 0, 0
        cur = cur + b
        wm += bm
        wv += bv
    if cur:
        segs.append(cur)
    return segs


# ----------------------------------------------------------------------
# 列表调度：任务按区间顺序依次放到“预计完成最早”的核心
# ----------------------------------------------------------------------
def list_schedule(model, cache, segs, k, cfg, dur_scale=1.0):
    """返回 core_orders。任务顺序 = 区间顺序（一个合法拓扑序）。"""
    from fast_eval import subgraph_preds
    groups = dict(enumerate(segs))
    preds = subgraph_preds(model, groups)
    cross, same = cfg['cross_wait'], cfg['same_wait']
    core_free = [0.0] * k
    core_used = [False] * k
    end = {}
    core_of = {}
    orders = [[] for _ in range(k)]
    for sg in range(len(segs)):
        d = cache.get(segs[sg]).local_makespan * dur_scale
        best = None
        for c in range(k):
            s = core_free[c] + (same if core_used[c] else 0)
            for p in preds[sg]:
                s = max(s, end[p] + (cross if core_of[p] != c else 0))
            f = s + d
            key = (f, c)
            if best is None or key < best[0]:
                best = (key, c, f)
        _, c, f = best
        orders[c].append(sg)
        core_of[sg] = c
        core_used[c] = True
        core_free[c] = f
        end[sg] = f
    return orders


def evaluate_segments(model, cache, segs, k, cfg):
    orders = list_schedule(model, cache, segs, k, cfg)
    res = simulate(cache, segs, orders, cfg['cross_wait'], cfg['same_wait'])
    return res, orders


# ----------------------------------------------------------------------
# 构造二：op 级列表调度（HEFT 思想）+ 同步点切分 Task
# ----------------------------------------------------------------------
def heft_assign(model, k, penalty, rank=None):
    """把计算操作按优先级逐个放到“预计完成最早”的核心。

    跨核前驱附加 ``penalty``（近似 1000 cycles 等待 + 边界 COPY 往返），
    每核 PIPE_M / PIPE_V 分别计占用，模拟 Cube/Vector 并行。
    返回 (core_of, start)，start 用于生成全局处理顺序。
    """
    rank = rank or model.bottom_level
    indeg = {v: len(model.pred[v]) for v in model.comp}
    heap = [(-rank[v], v) for v in model.comp if indeg[v] == 0]
    heapq.heapify(heap)
    pipe_free = [[0.0, 0.0] for _ in range(k)]
    fin, start, core_of = {}, {}, {}
    while heap:
        _, v = heapq.heappop(heap)
        p = 0 if model.is_m[v] else 1
        d = model.cyc[v]
        best = None
        for c in range(k):
            ready = 0.0
            for u in model.pred[v]:
                r = fin[u] + (0.0 if core_of[u] == c else penalty)
                if r > ready:
                    ready = r
            s = max(ready, pipe_free[c][p])
            key = (s + d, pipe_free[c][0] + pipe_free[c][1], c)
            if best is None or key < best[0]:
                best = (key, c, s)
        _, c, s = best
        core_of[v] = c
        start[v] = s
        fin[v] = s + d
        pipe_free[c][p] = s + d
        for w in model.succ[v]:
            indeg[w] -= 1
            if indeg[w] == 0:
                heapq.heappush(heap, (-rank[w], w))
    return core_of, start


def sync_cut_tasks(model, core_of, proc_order, k, max_ops, max_load):
    """按全局处理顺序把每核的 op 串切成 Task。

    规则（保证商图无环，证明见 README）：处理 op v（核 c）时，若其某个跨核
    前驱位于另一核仍“打开”的 Task X，则关闭 X（生产端切分）；若 v 所在核
    当前 Task 尚未依赖 X，则也关闭当前 Task、为 v 新开 Task（消费端切分），
    使每个 Task 的跨核依赖都在其开始时刻即可满足。按关闭时刻排序即为商图
    拓扑序，同核 Task 顺序 = 打开顺序。另外按 max_ops / max_load 限制规模。
    """
    open_task = [None] * k
    task_ops = []
    task_core = []
    task_deps = []          # 每个 Task 已依赖的其他 Task 集合
    task_load = []
    owner = {}
    closed = []
    orders = [[] for _ in range(k)]

    def close(c):
        if open_task[c] is not None:
            closed.append(open_task[c])
            open_task[c] = None

    def new_task(c):
        tid = len(task_ops)
        task_ops.append([])
        task_core.append(c)
        task_deps.append(set())
        task_load.append([0, 0])
        open_task[c] = tid
        orders[c].append(tid)
        return tid

    for v in proc_order:
        c = core_of[v]
        ext = set()
        for u in model.pred[v]:
            tu = owner[u]
            if task_core[tu] != c:
                ext.add(tu)
        for tu in ext:
            if open_task[task_core[tu]] == tu:
                close(task_core[tu])
        t = open_task[c]
        if t is not None:
            p = 0 if model.is_m[v] else 1
            if (ext - task_deps[t]) or len(task_ops[t]) >= max_ops or \
                    task_load[t][p] + model.cyc[v] > max_load:
                close(c)
                t = None
        if t is None:
            t = new_task(c)
        task_ops[t].append(v)
        task_deps[t] |= ext
        task_load[t][0 if model.is_m[v] else 1] += model.cyc[v]
        owner[v] = t
    return task_ops, orders


# ----------------------------------------------------------------------
# 构造三：场景 A 感知的 Task 级列表调度（TALS）
# ----------------------------------------------------------------------
class _Task:
    __slots__ = ('core', 'ops', 'start', 'end', 'open', 'free', 'loaded',
                 'opset', 'max_fin', 'load', 'inside', 'ext_bytes', 'last_ext')

    def __init__(self, core, start):
        self.core = core
        self.ops = []
        self.opset = set()
        self.start = start
        self.end = None
        self.open = True
        self.free = [start, start, start, start]      # MTE2, MTE3, M, V 可用时刻
        self.loaded = set()                            # 已经 COPY_IN 的张量
        self.max_fin = start
        self.load = [0, 0]
        self.inside = {}                               # 张量 -> Task 内消费者数
        self.ext_bytes = 0                             # 需 COPY_OUT 的字节（增量维护）
        self.last_ext = 0.0                            # 最后一个外送张量的搬出时长


def tals(model, k, cfg, order, copy_factor=1.0, max_ops=4000, max_load=None,
         open_bias=0.0, byte_weight=0.0):
    """按给定拓扑序逐个放置计算操作，近似模拟场景 A 的 Task 语义。

    对每个 op 在每个核上评估两种选择：并入该核当前打开的 Task，或关闭
    当前 Task 后新开一个 Task；取预计完成时刻最早者（``open_bias`` 为新开
    Task 的附加惩罚）。估计包含：同核 100 / 跨核 1000 cycles 激活等待、
    边界张量 COPY_IN（按 bytes/bw×copy_factor 在 MTE2 上串行）、Task 关闭时
    的 COPY_OUT（MTE3 串行）以及 PIPE_M/PIPE_V 占用。跨核前驱所在 Task
    若仍打开，则先关闭它。``byte_weight`` λ 把新增 DDR 搬运折算进选择键
    fin + λ·bytes/bw（DDR 为全部核心共享，搬运字节会拖慢所有核心）。
    返回 (groups, core_orders)。
    """
    bw = model.bandwidth
    bwt = byte_weight / bw
    cross, same = cfg['cross_wait'], cfg['same_wait']
    tb = model.tensor_by_id
    size = {t: tb[t]['size'] for t in tb}
    prod_of = {}
    for t, ps in model.producers.items():
        for p in ps:
            if p in model.comp_set:
                prod_of[t] = p
    n_elig = {}
    for t in tb:
        n_elig[t] = sum(1 for c in model.consumers.get(t, ()) if c in model.comp_set)
    final_out = {t for t in tb if any(model.op_by_id[c]['op'] == 'COPY_OUT'
                                      for c in model.consumers.get(t, ()))}
    in_t = {v: list(dict.fromkeys(model.in_tensors.get(v, ()))) for v in model.comp}
    out_t = {v: list(dict.fromkeys(model.out_tensors.get(v, ()))) for v in model.comp}
    is_m, cyc = model.is_m, model.cyc
    cf_bw = copy_factor / bw
    tasks = []
    core_open = [None] * k
    core_last_end = [None] * k
    op_task, op_fin = {}, {}
    orders = [[] for _ in range(k)]
    max_load = max_load or float('inf')

    def external(T, t):
        return t in final_out or n_elig[t] == 0 or T.inside.get(t, 0) < n_elig[t]

    def close(ti):
        T = tasks[ti]
        if not T.open:
            return
        outs = []
        for v in T.ops:
            for t in out_t[v]:
                if external(T, t):
                    outs.append((op_fin[v], size[t]))
        outs.sort()
        mte3 = T.free[1]
        for fin, sz in outs:
            mte3 = max(mte3, fin) + sz * cf_bw
        T.end = max(T.max_fin, mte3)
        T.open = False
        core_open[T.core] = None
        core_last_end[T.core] = T.end

    def est_end(T):
        if not T.open:
            return T.end
        return max(T.max_fin + T.last_ext, T.start + T.ext_bytes * cf_bw)

    def place_cost(v, T, start):
        mte2 = T.free[0] if T is not None else start
        ready = start
        bytes_in = 0
        for t in in_t[v]:
            p = prod_of.get(t)
            if T is not None:
                if p is not None and p in T.opset:
                    f = op_fin[p]
                    if f > ready:
                        ready = f
                    continue
                if t in T.loaded:
                    continue
            bytes_in += size[t]
            mte2 = max(mte2, start) + size[t] * cf_bw
            if mte2 > ready:
                ready = mte2
        pfree = T.free[2 if is_m[v] else 3] if T is not None else start
        s = ready if ready > pfree else pfree
        return s + cyc[v], mte2, bytes_in

    for v in order:
        src_tasks = {op_task[u] for u in model.pred[v]}
        src_end = {s: est_end(tasks[s]) for s in src_tasks}
        best = None
        for c in range(k):
            ti = core_open[c]
            if ti is not None:
                T = tasks[ti]
                ok = len(T.ops) < max_ops and T.load[0 if is_m[v] else 1] + cyc[v] <= max_load
                if ok:
                    for s in src_tasks:
                        if s == ti:
                            continue
                        S = tasks[s]
                        if S.open or S.end + (cross if S.core != c else same) > T.start:
                            ok = False
                            break
                if ok:
                    fin, mte2, b = place_cost(v, T, T.start)
                    key = (fin + bwt * b, b, 0, c)
                    if best is None or key < best[0]:
                        best = (key, 'a', c)
                prev_end = est_end(T)
            else:
                prev_end = core_last_end[c]
            start = 0 if prev_end is None else prev_end + same
            for s in src_tasks:
                e = src_end[s] + (cross if tasks[s].core != c else same)
                if e > start:
                    start = e
            fin, mte2, b = place_cost(v, None, start)
            # 新开 Task 会迫使仍打开的前驱 Task 提前关闭，每个再计一次碎片化惩罚
            n_forced = sum(1 for s in src_tasks if tasks[s].open)
            key = (fin + open_bias * (1 + n_forced) + bwt * b, b, 1, c)
            if best is None or key < best[0]:
                best = (key, 'b', c)
        _, kind, c = best
        if kind == 'b':
            for s in src_tasks:
                if tasks[s].open:
                    close(s)
            if core_open[c] is not None:
                close(core_open[c])
            prev_end = core_last_end[c]
            start = 0 if prev_end is None else prev_end + same
            for s in src_tasks:
                S = tasks[s]
                start = max(start, S.end + (cross if S.core != c else same))
            T = _Task(c, start)
            tasks.append(T)
            ti = len(tasks) - 1
            core_open[c] = ti
            orders[c].append(ti)
        else:
            ti = core_open[c]
            T = tasks[ti]
        fin, mte2, _ = place_cost(v, T, T.start)
        # 提交：更新 Task 状态与外送字节
        T.ops.append(v)
        T.opset.add(v)
        for t in in_t[v]:
            p = prod_of.get(t)
            if p is not None and p in T.opset:
                was_ext = external(T, t)
                T.inside[t] = T.inside.get(t, 0) + 1
                if was_ext and not external(T, t):
                    T.ext_bytes -= size[t]
            else:
                T.loaded.add(t)
        last = 0.0
        for t in out_t[v]:
            if external(T, t):
                T.ext_bytes += size[t]
                last = max(last, size[t] * cf_bw)
        if fin >= T.max_fin:
            T.max_fin = fin
            T.last_ext = last
        T.free[0] = max(T.free[0], mte2)
        T.free[2 if is_m[v] else 3] = fin
        T.load[0 if is_m[v] else 1] += cyc[v]
        op_task[v] = ti
        op_fin[v] = fin
    for ti in range(len(tasks)):
        close(ti)
    groups = [T.ops for T in tasks]
    return groups, orders


# ----------------------------------------------------------------------
# 构造四：弱连通分量打包（共享输入超边聚合 + LPT 分核）
# ----------------------------------------------------------------------
def weak_components(model, base_order):
    pos = {v: i for i, v in enumerate(base_order)}
    parent = {v: v for v in model.comp}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for v in model.comp:
        for w in model.succ[v]:
            a, b = find(v), find(w)
            if a != b:
                parent[a] = b
    comps = defaultdict(list)
    for v in model.comp:
        comps[find(v)].append(v)
    out = [sorted(c, key=pos.__getitem__) for c in comps.values()]
    out.sort(key=lambda c: pos[c[0]])
    return out


def component_pack(model, cache, k, cfg, comps, target):
    """分量之间没有数据依赖，Task 之间因而完全独立。

    贪心序列化：当前 Task 的已加载输入张量集合为 I，下一个并入的分量取
    与 I 共享输入字节最多者（共享输入 = 超边，合在一个 Task 只读一次）；
    负载达到 target 就关闭 Task。Task 用 LPT（按官方 Step1-3 局部 makespan）
    分给最空闲的核心。
    """
    import heapq as hq
    tb = model.tensor_by_id
    prod = {}
    for t, ps in model.producers.items():
        for p in ps:
            if p in model.comp_set:
                prod[t] = p
    comp_in = []
    users = defaultdict(list)
    for ci, ops in enumerate(comps):
        ins = set()
        for v in ops:
            for t in model.in_tensors.get(v, ()):
                if t not in prod:
                    ins.add(t)
        comp_in.append(ins)
        for t in ins:
            users[t].append(ci)
    load = [max(sum(model.cyc[v] for v in ops if model.is_m[v]),
                sum(model.cyc[v] for v in ops if not model.is_m[v])) for ops in comps]
    remaining = set(range(len(comps)))
    tasks = []
    order_next = 0
    while remaining:
        while order_next not in remaining:
            order_next += 1
        cur, cur_in, cur_load = [], set(), 0
        score = defaultdict(int)
        heap = []
        c = order_next
        while True:
            remaining.discard(c)
            cur.append(c)
            cur_load += load[c]
            for t in comp_in[c]:
                if t in cur_in:
                    continue
                cur_in.add(t)
                for d in users[t]:
                    if d in remaining:
                        score[d] += tb[t]['size']
                        hq.heappush(heap, (-score[d], d))
            if cur_load >= target or not remaining:
                break
            c = None
            while heap:
                s, d = hq.heappop(heap)
                if d in remaining and -s == score[d]:
                    c = d
                    break
            if c is None:
                while order_next not in remaining:
                    order_next += 1
                c = order_next
        tasks.append([v for ci in cur for v in comps[ci]])
    # LPT
    dur = [cache.get(t).local_makespan for t in tasks]
    idx = sorted(range(len(tasks)), key=lambda i: -dur[i])
    core_load = [0.0] * k
    orders = [[] for _ in range(k)]
    for i in idx:
        c = min(range(k), key=lambda j: (core_load[j], j))
        orders[c].append(i)
        core_load[c] += dur[i] + cfg['same_wait']
    return tasks, orders


# ----------------------------------------------------------------------
# 构造五：层带（band）区间 —— 按 ASAP 深度分带、带内按 dfs 连续切块
# ----------------------------------------------------------------------
def band_segments(model, base_order, n_bands, pieces):
    """把 ASAP 深度均分为 n_bands 个带；排序键 (带号, dfs 位置) 是拓扑序。

    每个带内再按负载连续切成 ``pieces`` 块（同一带的不同块多为不同分量/
    通道，可并行）。适合“多个相同分支共享逐层权重”的图：一个带只包含少数
    几层，其权重在 Task 内全程驻留，避免整分量打包时的反复换出换入。
    """
    pos = {v: i for i, v in enumerate(base_order)}
    lvl = {}
    for v in model.topo:
        lvl[v] = max((lvl[p] + 1 for p in model.pred[v]), default=0)
    depth = max(lvl.values()) + 1
    width = max(1, -(-depth // n_bands))
    order = sorted(model.comp, key=lambda v: (lvl[v] // width, pos[v]))
    segs = []
    i = 0
    while i < len(order):
        b = lvl[order[i]] // width
        j = i
        while j < len(order) and lvl[order[j]] // width == b:
            j += 1
        ops = order[i:j]
        load = sum(model.cyc[v] for v in ops)
        target = load / max(1, pieces)
        cur, acc = [], 0
        for v in ops:
            cur.append(v)
            acc += model.cyc[v]
            if acc >= target * 0.999 and len(cur) and (j - i) > 1:
                segs.append(cur)
                cur, acc = [], 0
        if cur:
            segs.append(cur)
        i = j
    return segs
