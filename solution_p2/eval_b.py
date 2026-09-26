"""场景 B（问题二）评估包装。

搜索阶段直接调用官方 ``multicore_cut_evaluate_problem_2.evaluate_scene_b``——Task 构造、
跨核 COPY、Step1-3、事件仿真全部是官方代码，因此打分与官方完全一致。

唯一的加速：官方 ``schedule_step3.step3_simulation`` 主循环每轮用
``all(status == 'done' for ...)`` 扫描全部 op（对 n 个 op 的核是 O(n^2)）。这里在
**本进程内存中**把该函数的源码做两处文本替换后重新编译（完成时计数、用计数判断
是否全部完成），其余每一行与官方相同；官方文件本身不做任何修改。
``verify_eval_b.py`` 对照未打补丁的官方评估器验证两者输出逐项一致；最终提交结果
另由官方命令行（子进程、未打补丁）逐个复核。
"""

import inspect
import textwrap

import common_b  # noqa: F401  (设置 sys.path)
import schedule_step3
from common_b import make_plan

_ORIGINAL_STEP3 = schedule_step3.step3_simulation
_PATCHED = False


def install_fast_step3():
    """用“计数器”替换 step3 主循环中的 O(n) 全量扫描（语义不变）。"""
    global _PATCHED
    if _PATCHED:
        return
    src = textwrap.dedent(inspect.getsource(_ORIGINAL_STEP3))
    a = "    op_status = {op_id: 'pending' for op_id in op_by_id}\n"
    b = "                    op_status[op_id] = 'done'\n"
    c = "        if all(status == 'done' for status in op_status.values()):\n"
    for s in (a, b, c):
        assert src.count(s) == 1, 'official step3 source changed: ' + s
    src = src.replace(a, a + "    _done_count = [0]\n")
    src = src.replace(b, b + "                    _done_count[0] += 1\n")
    src = src.replace(c, "        if _done_count[0] == len(op_status):\n")
    ns = schedule_step3.__dict__
    exec(compile(src, '<fast step3_simulation>', 'exec'), ns)
    _PATCHED = True


def uninstall_fast_step3():
    global _PATCHED
    schedule_step3.step3_simulation = _ORIGINAL_STEP3
    _PATCHED = False


class EvalError(RuntimeError):
    pass


def evaluate_plan(graph, plan, cfg, fast=True):
    """返回官方 evaluate_scene_b 的结果字典；方案非法时抛 EvalError。"""
    from multicore_cut_evaluate_problem_2 import evaluate_scene_b
    if fast:
        install_fast_step3()
    try:
        return evaluate_scene_b(graph, plan, bandwidth=cfg['bandwidth'],
                                capacity=cfg['capacity'],
                                cross_core_copy_delay=cfg['delay'])
    except (ValueError, RuntimeError) as error:
        raise EvalError(str(error)[:500]) from error


def evaluate_groups(graph, groups, orders, cfg):
    """groups: list[list[op]]（下标即 sgid）；orders: 每核 sgid 顺序。"""
    return evaluate_plan(graph, make_plan(groups, orders), cfg)


def score(res):
    """字典序目标：(Makespan, 新增搬运)。"""
    return (res['makespan'], res['data_movement_bytes']['added_copy_bytes'])
