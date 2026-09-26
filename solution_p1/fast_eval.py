"""场景 A（问题一）快速精确评估器。

与官方 ``multicore_cut_evaluate_problem_1.evaluate_scene_a`` 的关系：

1. **Task 构造**：对每个子图，按官方 ``_build_scene_a_tasks`` 完全相同的
   规则（触及张量的排序、边界 COPY_IN/COPY_OUT 的插入、DDR→UB 改写、边的
   追加顺序）构造局部 Task 图，然后直接调用官方 ``step1_schedule``、
   ``step2_spill_insertion``、``_build_extended_graph``、
   ``prepare_step3_execution``。Task 只由子图的节点集合决定，与核号和顺序
   无关，因此按 ``frozenset(op_ids)`` 缓存，局部搜索时重复利用。
   边界节点的新 id 与官方全局分配不同，但两者只差一个保持大小关系的单调
   重编号（全部大于原图 id、Task 内递增），官方三步算法只依赖 id 的相对
   大小，故结果不变——该结论由 ``verify_fast_eval.py`` 对照官方评估器实测。
2. **多核事件模拟**：逐行复刻官方事件循环（退休→激活→发射→推进），DDR
   公平共享的浮点结算与取整完全照搬；仅把官方每个事件都遍历整个 Task 的
   ``all(...)`` 检查改成计数器，复杂度从 O(n^2) 降到 O(n log n)。

该模块只用于搜索阶段的快速精确打分；最终结果一律再由官方评估器复核。
"""

import heapq
import math
from collections import defaultdict

from common import GraphModel  # noqa: F401  (类型提示)

from schedule_step1 import step1_schedule
from schedule_step2 import _build_extended_graph, step2_spill_insertion
from schedule_step3 import _op_duration, _uses_ddr_bandwidth, prepare_step3_execution

PIPE_INDEX = {'PIPE_MTE2': 0, 'PIPE_MTE3': 1, 'PIPE_M': 2, 'PIPE_V': 3}


class TaskInfo:
    """一个子图（Task）经官方 Step1-3 后的紧凑执行描述。"""
    __slots__ = ('n', 'op_ids', 'pipe', 'dur', 'ddr', 'npred', 'succs',
                 'pipe_order', 'pipe_next', 'local_makespan', 'copy_bytes',
                 'spill_bytes', 'in_bytes', 'out_bytes', 'work_m', 'work_v',
                 'peak')


class TaskCache:
    """按子图节点集合缓存官方 Step1-3 的结果。"""

    def __init__(self, model, config):
        self.model = model
        self.capacity = dict(config['capacity'])
        self.bandwidth = config['bandwidth']
        self.cache = {}
        g = model.graph
        self.base_op_id = max([op['id'] for op in g['ops']] + [0]) + 1
        self.base_tensor_id = max([t['id'] for t in g['tensors']] + [10000]) + 1
        self.used_ids = set(model.op_by_id) | set(model.tensor_by_id)
        # 官方 output_boundary 判定需要的张量属性
        self.has_copy_out = {}
        self.eligible_consumers = {}
        for tid in model.tensor_by_id:
            cons = model.consumers.get(tid, set())
            self.has_copy_out[tid] = any(
                model.op_by_id[c].get('op') == 'COPY_OUT' for c in cons)
            self.eligible_consumers[tid] = {c for c in cons if c in model.comp_set}
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------------
    def build_task_graph(self, op_ids):
        """复刻官方 _build_scene_a_tasks 中单个 Task 的局部图构造。"""
        m = self.model
        bw = self.bandwidth
        task_op_ids = set(op_ids)
        ops = [dict(m.op_by_id[v]) for v in sorted(task_op_ids)]
        tensors, edges = [], []
        touched = sorted(m.tensors_of(task_op_ids))
        next_op_id, next_tensor_id = self.base_op_id, self.base_tensor_id
        used = self.used_ids
        local_new = set()

        def new_ids():
            nonlocal next_op_id, next_tensor_id
            while next_tensor_id in used or next_tensor_id in local_new:
                next_tensor_id += 1
            ddr_id = next_tensor_id
            local_new.add(ddr_id)
            next_tensor_id += 1
            while next_op_id in used or next_op_id in local_new:
                next_op_id += 1
            copy_id = next_op_id
            local_new.add(copy_id)
            next_op_id += 1
            return ddr_id, copy_id

        in_bytes = out_bytes = 0
        for tid in touched:
            tensor = dict(m.tensor_by_id[tid])
            local_producers = m.producers.get(tid, set()) & task_op_ids
            local_consumers = m.consumers.get(tid, set()) & task_op_ids
            eligible = self.eligible_consumers[tid]
            input_boundary = bool(local_consumers) and not bool(local_producers)
            output_boundary = bool(local_producers) and (
                self.has_copy_out[tid] or not eligible
                or bool(eligible - task_op_ids))
            if tensor.get('pos') == 'DDR':
                tensor['pos'] = 'UB'
            tensors.append(tensor)
            for p in sorted(local_producers):
                edges.append({'source': p, 'target': tid})
            for c in sorted(local_consumers):
                edges.append({'source': tid, 'target': c})
            if input_boundary:
                ddr_id, copy_id = new_ids()
                tensors.append({'id': ddr_id, 'pos': 'DDR', 'size': tensor['size']})
                ops.append({'id': copy_id, 'op': 'COPY_IN', 'pipe': 'PIPE_MTE2',
                            'cycles': max(1, math.ceil(tensor['size'] / bw))})
                edges.extend([{'source': ddr_id, 'target': copy_id},
                              {'source': copy_id, 'target': tid}])
                in_bytes += tensor['size']
            if output_boundary:
                ddr_id, copy_id = new_ids()
                tensors.append({'id': ddr_id, 'pos': 'DDR', 'size': tensor['size']})
                ops.append({'id': copy_id, 'op': 'COPY_OUT', 'pipe': 'PIPE_MTE3',
                            'cycles': max(1, math.ceil(tensor['size'] / bw))})
                edges.extend([{'source': tid, 'target': copy_id},
                              {'source': copy_id, 'target': ddr_id}])
                out_bytes += tensor['size']
        for edge in m.direct_edges:
            if edge['source'] in task_op_ids and edge['target'] in task_op_ids:
                edges.append(dict(edge))
        return {'ops': ops, 'tensors': tensors, 'edges': edges}, in_bytes, out_bytes

    def get(self, op_ids):
        key = op_ids if isinstance(op_ids, frozenset) else frozenset(op_ids)
        info = self.cache.get(key)
        if info is not None:
            self.hits += 1
            return info
        self.misses += 1
        info = self._prepare(key)
        self.cache[key] = info
        return info

    def _prepare(self, key):
        graph, in_bytes, out_bytes = self.build_task_graph(key)
        seq = step1_schedule(graph)
        result2 = step2_spill_insertion(graph, seq, capacity=self.capacity)
        spill = sum(sp['size'] * (1 + int(sp['spill_out_copies_data']))
                    for sp in result2['spill_records'])
        ext = _build_extended_graph(graph, result2)
        prepared = prepare_step3_execution(ext, capacity=self.capacity,
                                           bandwidth=self.bandwidth)
        seq_ext = prepared['seq']
        index = {op_id: i for i, op_id in enumerate(seq_ext)}
        info = TaskInfo()
        info.n = len(seq_ext)
        info.op_ids = seq_ext
        op_by_id = prepared['op_by_id']
        info.pipe = [PIPE_INDEX[op_by_id[o]['pipe']] for o in seq_ext]
        info.dur = [_op_duration(op_by_id[o], prepared['in_tids'], prepared['out_tids'],
                                 prepared['tensor_by_id'], self.bandwidth) for o in seq_ext]
        info.ddr = [_uses_ddr_bandwidth(op_by_id[o], prepared['in_tids'],
                                        prepared['out_tids'], prepared['tensor_by_id'])
                    for o in seq_ext]
        info.npred = [len(prepared['op_preds'][o]) for o in seq_ext]
        info.succs = [[index[s] for s in prepared['op_succs'][o]] for o in seq_ext]
        info.pipe_order = [[index[o] for o in prepared['pipe_ops'][p]]
                           for p in ('PIPE_MTE2', 'PIPE_MTE3', 'PIPE_M', 'PIPE_V')]
        # pipe_next[i] = 同一 Pipe 顺序中的下一个 op（-1 表示没有）
        nxt = [-1] * info.n
        for order in info.pipe_order:
            for a, b in zip(order, order[1:]):
                nxt[a] = b
        info.pipe_next = nxt
        info.local_makespan = prepared['step3']['makespan']
        info.spill_bytes = spill
        info.in_bytes, info.out_bytes = in_bytes, out_bytes
        info.copy_bytes = in_bytes + out_bytes
        m = self.model
        info.work_m = sum(m.cyc[v] for v in key if m.is_m[v])
        info.work_v = sum(m.cyc[v] for v in key if not m.is_m[v])
        info.peak = dict(prepared['step3']['memory_peak'])
        return info


class SimulationError(RuntimeError):
    pass


def simulate(cache, groups, core_orders, cross_wait, same_wait, want_trace=False):
    """精确复刻官方场景 A 事件循环。

    groups: dict/list sgid -> iterable(op_ids)；core_orders: list[list[sgid]]。
    返回 dict(makespan, added_copy_bytes, task_start, task_end, ...)。
    """
    if isinstance(groups, list):
        groups = dict(enumerate(groups))
    num_cores = len(core_orders)
    tasks = {}
    core_of = {}
    for core, order in enumerate(core_orders):
        for sg in order:
            core_of[sg] = core
    for sg, ops in groups.items():
        tasks[sg] = cache.get(ops)
    # 子图依赖（用于 Task 激活）
    pred_tasks = subgraph_preds(cache.model, groups)

    task_status = {sg: 0 for sg in tasks}          # 0 waiting, 1 active, 2 done
    task_start, task_end = {}, {}
    core_index = [0] * num_cores
    core_active = [None] * num_cores
    core_prev_end = [None] * num_cores
    executors = [[None] * 4 for _ in range(num_cores)]   # (item, end) 或 None
    queues = [[[] for _ in range(4)] for _ in range(num_cores)]
    # 活跃 Task 的逐 op 状态
    st = {}      # sg -> [status list, pred_remaining list, pipe cursor list, remaining count]
    ddr_work = {}
    ddr_last = [0]
    op_end = {}
    counter = [0]                                  # 已完成 Task 数

    def advance_ddr(now):
        elapsed = now - ddr_last[0]
        while elapsed > 1e-9:
            active = [item for item, work in ddr_work.items() if work > 1e-9]
            if not active:
                break
            min_work = min(ddr_work[item] for item in active)
            finish_delta = min_work * len(active)
            if finish_delta >= elapsed - 1e-9:
                share = elapsed / len(active)
                for item in active:
                    ddr_work[item] = max(0.0, ddr_work[item] - share)
                break
            for item in active:
                ddr_work[item] = max(0.0, ddr_work[item] - min_work)
            elapsed -= finish_delta
        ddr_last[0] = now

    def reschedule(now):
        if not ddr_work:
            return
        ordered = sorted((max(0.0, work), item) for item, work in ddr_work.items())
        projected = {}
        cursor, previous, active_count = float(now), 0.0, len(ordered)
        i = 0
        while i < len(ordered):
            work = ordered[i][0]
            cursor += (work - previous) * active_count
            j = i
            while j < len(ordered) and abs(ordered[j][0] - work) <= 1e-9:
                projected[ordered[j][1]] = int(math.ceil(cursor - 1e-9))
                j += 1
            active_count -= j - i
            previous = work
            i = j
        for item, end in projected.items():
            op_end[item] = end
        for c in range(num_cores):
            ex = executors[c]
            for p in range(4):
                run = ex[p]
                if run is not None and run[0] in projected:
                    ex[p] = (run[0], projected[run[0]])

    def queue_if_ready(sg, i):
        s = st[sg]
        if s[0][i] != 0 or s[1][i] != 0:
            return
        t = tasks[sg]
        p = t.pipe[i]
        order = t.pipe_order[p]
        cur = s[2][p]
        if cur >= len(order) or order[cur] != i:
            return
        s[0][i] = 1
        heapq.heappush(queues[core_of[sg]][p], (i, (sg, i)))

    def activate(sg, now):
        t = tasks[sg]
        task_status[sg] = 1
        task_start[sg] = now
        core_active[core_of[sg]] = sg
        st[sg] = [[0] * t.n, list(t.npred), [0, 0, 0, 0], t.n]
        for i in range(t.n):
            queue_if_ready(sg, i)

    def release_time(sg):
        core = core_of[sg]
        for pr in pred_tasks[sg]:
            if task_status[pr] != 2:
                return None
        release = 0
        if core_prev_end[core] is not None:
            release = core_prev_end[core] + same_wait
        for pr in pred_tasks[sg]:
            if core_of[pr] != core:
                release = max(release, task_end[pr] + cross_wait)
        return release

    def activate_ready(now):
        for core in range(num_cores):
            if core_active[core] is not None:
                continue
            order = core_orders[core]
            if core_index[core] >= len(order):
                continue
            sg = order[core_index[core]]
            rel = release_time(sg)
            if rel is not None and rel <= now:
                activate(sg, now)

    def retire(now):
        advance_ddr(now)
        retired_ddr = False
        finished_tasks = []
        for core in range(num_cores):
            ex = executors[core]
            for p in range(4):
                run = ex[p]
                if run is None or run[1] > now:
                    continue
                ex[p] = None
                item = run[0]
                sg, i = item
                s = st[sg]
                t = tasks[sg]
                s[0][i] = 3
                s[3] -= 1
                # advance pipe
                s[2][p] += 1
                nx = t.pipe_next[i]
                if nx >= 0:
                    queue_if_ready(sg, nx)
                if item in ddr_work:
                    del ddr_work[item]
                    retired_ddr = True
                for j in t.succs[i]:
                    s[1][j] -= 1
                    queue_if_ready(sg, j)
                if s[3] == 0:
                    finished_tasks.append(sg)
        if retired_ddr:
            reschedule(now)
        for sg in finished_tasks:
            core = core_of[sg]
            task_status[sg] = 2
            task_end[sg] = now
            core_active[core] = None
            core_prev_end[core] = now
            core_index[core] += 1
            del st[sg]
            counter[0] += 1

    def issue(now):
        for core in range(num_cores):
            ex = executors[core]
            qs = queues[core]
            for p in range(4):
                if ex[p] is None and qs[p]:
                    _, item = heapq.heappop(qs[p])
                    sg, i = item
                    t = tasks[sg]
                    st[sg][0][i] = 2
                    dur = t.dur[i]
                    end = now + dur
                    op_end[item] = end
                    ex[p] = (item, end)
                    if t.ddr[i]:
                        advance_ddr(now)
                        ddr_work[item] = float(dur)
                        reschedule(now)

    now = 0
    total_tasks = len(tasks)
    while True:
        retire(now)
        activate_ready(now)
        issue(now)
        if counter[0] == total_tasks:
            break
        next_times = [run[1] for c in range(num_cores) for run in executors[c] if run is not None]
        for core in range(num_cores):
            if core_active[core] is not None:
                continue
            order = core_orders[core]
            if core_index[core] < len(order):
                rel = release_time(order[core_index[core]])
                if rel is not None and rel > now:
                    next_times.append(rel)
        if not next_times:
            raise SimulationError('deadlock at t={}'.format(now))
        nxt = min(next_times)
        if nxt <= now:
            raise SimulationError('no progress at t={}'.format(now))
        now = nxt

    makespan = max(task_end.values(), default=0)
    m = cache.model
    copy_total = sum(t.copy_bytes for t in tasks.values())
    spill_total = sum(t.spill_bytes for t in tasks.values())
    return {
        'makespan': makespan,
        'added_copy_bytes': copy_total - m.original_copy_bytes + spill_total,
        'partition_added_copy_bytes': copy_total - m.original_copy_bytes,
        'spill_added_copy_bytes': spill_total,
        'task_start': task_start,
        'task_end': task_end,
        'core_of': core_of,
        'pred_tasks': pred_tasks,
    }


def subgraph_preds(model, groups):
    """子图依赖：原计算 DAG 中跨子图的边（官方 dependency_pairs 语义）。"""
    owner = {}
    for sg, ops in groups.items():
        for v in ops:
            owner[v] = sg
    preds = {sg: set() for sg in groups}
    for v, sg in owner.items():
        for w in model.succ[v]:
            tw = owner[w]
            if tw != sg:
                preds[tw].add(sg)
    return preds
