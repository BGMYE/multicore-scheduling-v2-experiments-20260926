"""问题一求解程序的公共设施：路径、官方代码导入、配置读取、图预处理。

所有硬件参数都通过官方 ``evaluation_validation`` 从 ``数据包/data/config.txt``
读取，本程序不内置任何数值默认值，以保证与官方评估器口径一致。
"""

import json
import math
import os
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent            # 工作目录 A题-问题1/
PKG = ROOT / '数据包'
OFFICIAL_CODE = PKG / 'code'
DATA_DIR = PKG / 'data'
CONFIG_PATH = DATA_DIR / 'config.txt'
RESULTS_DIR = ROOT / 'results_p1'

if str(OFFICIAL_CODE) not in sys.path:
    sys.path.insert(0, str(OFFICIAL_CODE))
sys.setrecursionlimit(1000000)

COPY_TYPES = ('COPY_IN', 'COPY_OUT')
PIPES = ('PIPE_MTE2', 'PIPE_MTE3', 'PIPE_M', 'PIPE_V')


def load_config(config_path=CONFIG_PATH):
    """用官方读取函数取得容量、带宽和场景 A 的两类等待周期。"""
    from evaluation_validation import read_evaluation_config
    from multicore_cut_evaluate_problem_1 import read_scene_a_config
    settings = read_evaluation_config(str(config_path))
    scene = read_scene_a_config(str(config_path))
    return {
        'capacity': dict(settings['capacity']),
        'bandwidth': settings['bandwidth'],
        'cross_wait': scene['task_cross_core_wait_cycles'],
        'same_wait': scene['task_same_core_wait_cycles'],
    }


def load_graph(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def case_name(path):
    return Path(path).stem


class GraphModel:
    """原始 Op-Tensor 二部图的只读视图。

    * ``comp``: 全部非 COPY 计算操作（方案需要为其分配 sgid 的节点）；
    * ``succ/pred``: 跨过 COPY 节点收缩后的计算操作 DAG（与官方
      ``_contract_excluded_copy_nodes`` 语义一致，用于子图依赖）；
    * 张量生产者/消费者集合，用于通信与重复读取代理。
    """

    def __init__(self, graph_json, bandwidth):
        self.graph = graph_json
        self.bandwidth = bandwidth
        self.op_by_id = {op['id']: op for op in graph_json['ops']}
        self.tensor_by_id = {t['id']: t for t in graph_json['tensors']}
        op_ids = set(self.op_by_id)
        self.producers = defaultdict(set)
        self.consumers = defaultdict(set)
        self.direct_edges = []
        self.in_tensors = defaultdict(list)     # op -> 输入张量
        self.out_tensors = defaultdict(list)    # op -> 输出张量
        for edge in graph_json['edges']:
            src, dst = edge['source'], edge['target']
            if src in op_ids and dst not in op_ids:
                self.producers[dst].add(src)
                self.out_tensors[src].append(dst)
            elif src not in op_ids and dst in op_ids:
                self.consumers[src].add(dst)
                self.in_tensors[dst].append(src)
            elif src in op_ids and dst in op_ids and src != dst:
                self.direct_edges.append(edge)
        self.comp = sorted(o for o, op in self.op_by_id.items()
                           if op['op'] not in COPY_TYPES)
        self.comp_set = set(self.comp)
        self._build_contracted_dag()
        self._build_costs()

    # ------------------------------------------------------------------
    def _build_contracted_dag(self):
        """完整 op 图 -> 跳过 COPY 节点的计算 op DAG。"""
        full_succ = defaultdict(set)
        for tid, prods in self.producers.items():
            for p in prods:
                for c in self.consumers.get(tid, ()):
                    if p != c:
                        full_succ[p].add(c)
        for edge in self.direct_edges:
            full_succ[edge['source']].add(edge['target'])
        succ = {v: set() for v in self.comp}
        for v in self.comp:
            stack = list(full_succ.get(v, ()))
            seen = set()
            while stack:
                w = stack.pop()
                if w in self.comp_set:
                    if w != v:
                        succ[v].add(w)
                    continue
                if w in seen:
                    continue
                seen.add(w)
                stack.extend(full_succ.get(w, ()))
        pred = {v: set() for v in self.comp}
        for v, ws in succ.items():
            for w in ws:
                pred[w].add(v)
        self.succ, self.pred = succ, pred
        indeg = {v: len(pred[v]) for v in self.comp}
        queue = deque(v for v in self.comp if indeg[v] == 0)
        order = []
        while queue:
            v = queue.popleft()
            order.append(v)
            for w in sorted(succ[v]):
                indeg[w] -= 1
                if indeg[w] == 0:
                    queue.append(w)
        if len(order) != len(self.comp):
            raise RuntimeError('compute DAG has a cycle')
        self.topo = order

    def _build_costs(self):
        """每个计算操作的 Pipe 工作量、输入输出字节，以及关键路径长度。"""
        bw = self.bandwidth
        self.cyc = {}
        self.is_m = {}
        for v in self.comp:
            op = self.op_by_id[v]
            self.cyc[v] = max(1, op.get('cycles', 1))
            self.is_m[v] = op['pipe'] == 'PIPE_M'
        # 自上而下 / 自下而上的最长路径（仅计算周期，用作优先级）
        top = {}
        for v in self.topo:
            top[v] = max((top[p] + self.cyc[p] for p in self.pred[v]), default=0)
        bot = {}
        for v in reversed(self.topo):
            bot[v] = self.cyc[v] + max((bot[s] for s in self.succ[v]), default=0)
        self.top_level, self.bottom_level = top, bot
        self.critical_path = max((top[v] + bot[v] for v in self.comp), default=0)
        self.work_m = sum(c for v, c in self.cyc.items() if self.is_m[v])
        self.work_v = sum(c for v, c in self.cyc.items() if not self.is_m[v])
        # 原始搬运字节（官方 _copy_traffic_bytes 口径）
        total = 0
        for op in self.graph['ops']:
            if op['op'] == 'COPY_IN':
                total += sum(self.tensor_by_id[t]['size'] for t in self.out_tensors[op['id']])
            elif op['op'] == 'COPY_OUT':
                total += sum(self.tensor_by_id[t]['size'] for t in self.in_tensors[op['id']])
        self.original_copy_bytes = total
        self.ddr_time_lb = total / bw

    def lower_bound(self, k):
        """LB0 = max(W_M/K, W_V/K, CP_compute)，不依赖搬运细节的共同下界。"""
        return max(self.work_m / k, self.work_v / k, self.critical_path)

    def tensors_of(self, op_ids):
        """一组操作触及的全部张量（生产或消费）。"""
        out = set()
        for v in op_ids:
            out.update(self.in_tensors.get(v, ()))
            out.update(self.out_tensors.get(v, ()))
        return out


def dump_plan(plan, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=1)
        handle.write('\n')


def make_plan(groups, core_orders):
    """groups: list[list[op_id]]（下标即 sgid）; core_orders: list[list[sgid]]。"""
    node_to_subgraph = {}
    for sgid, ops in enumerate(groups):
        for v in ops:
            node_to_subgraph[str(v)] = sgid
    return {'node_to_subgraph': node_to_subgraph,
            'core_schedules': [list(order) for order in core_orders]}
