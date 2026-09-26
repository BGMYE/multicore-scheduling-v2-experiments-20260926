"""问题三评估包装：直接调用官方 ``multicore_cut_evaluate_problem_3.evaluate_problem_3``。

与问题二相同，仅在进程内给官方 ``schedule_step3.step3_simulation`` 打“完成计数器”补丁
（solution_p2/eval_b.install_fast_step3，语义不变、官方文件不改）；``verify_eval_c.py``
对照未打补丁的官方函数验证输出逐项一致，最终结果再由官方命令行（子进程）复核。
"""

import common_c  # noqa: F401
import eval_b
from common_b import make_plan


class EvalError(RuntimeError):
    pass


def evaluate_plan_c(graph, plan, cfg, fast=True):
    from multicore_cut_evaluate_problem_3 import evaluate_problem_3
    if fast:
        eval_b.install_fast_step3()
    else:
        eval_b.uninstall_fast_step3()
    try:
        return evaluate_problem_3(
            graph, plan, bandwidth=cfg['bandwidth'], capacity=cfg['capacity'],
            cross_core_copy_delay=cfg['delay'],
            cache_capacity_bytes=cfg['cache_capacity_bytes'],
            cache_bandwidth_bytes_per_cycle=cfg['cache_bandwidth_bytes_per_cycle'])
    except (ValueError, RuntimeError) as error:
        raise EvalError(str(error)[:500]) from error


def evaluate_groups_c(graph, groups, orders, cfg):
    return evaluate_plan_c(graph, make_plan(groups, orders), cfg)


def score(res):
    """字典序目标：(Makespan, 新增搬运)。命中率只作诊断，不参与接受。"""
    return (res['makespan'], res['data_movement_bytes']['added_copy_bytes'])
