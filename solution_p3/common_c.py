"""问题三（场景 B + 只读 L2 FIFO Cache）公共设施。

只读复用 solution_p1 / solution_p2 的通用模块（GraphModel、方案表示、问题二评估包装等），
不修改其中任何文件；结果目录为 results_p3/。
"""

import sys
from pathlib import Path

P3_DIR = Path(__file__).resolve().parent
ROOT = P3_DIR.parent
for p in (str(P3_DIR), str(ROOT / 'solution_p2'), str(ROOT / 'solution_p1')):
    if p not in sys.path:
        sys.path.insert(0, p)

from common_b import (CONFIG_PATH, DATA_DIR, OFFICIAL_CODE, RESULTS_P1, GraphModel,  # noqa: E402,F401
                      case_name, dump_plan, load_graph, make_plan)

RESULTS_DIR = ROOT / 'results_p3'
RESULTS_P2 = ROOT / 'results_p2'


def load_config_c(config_path=CONFIG_PATH):
    """容量/带宽/500 cycles 同步延迟 + [problem_3] 的 Cache 容量与命中带宽（官方读取函数）。"""
    from evaluation_validation import read_evaluation_config
    from multicore_cut_evaluate_problem_3 import read_cache_config, read_scene_b_config
    settings = read_evaluation_config(str(config_path))
    scene = read_scene_b_config(str(config_path))
    cache = read_cache_config(str(config_path))
    return {
        'capacity': dict(settings['capacity']),
        'bandwidth': settings['bandwidth'],
        'delay': scene['cross_core_copy_delay_cycles'],
        'cache_capacity_bytes': cache['cache_capacity_bytes'],
        'cache_bandwidth_bytes_per_cycle': cache['cache_bandwidth_bytes_per_cycle'],
    }
