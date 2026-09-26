"""问题二（场景 B）公共设施：复用 solution_p1 的图模型，读取场景 B 配置。

只读引用 solution_p1/common.py（GraphModel、路径、plan 读写），不修改问题一的任何文件。
"""

import sys
from pathlib import Path

P2_DIR = Path(__file__).resolve().parent
ROOT = P2_DIR.parent
P1_DIR = ROOT / 'solution_p1'
for p in (str(P2_DIR), str(P1_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from common import (CONFIG_PATH, DATA_DIR, OFFICIAL_CODE, PKG, GraphModel,  # noqa: E402,F401
                    case_name, dump_plan, load_graph, make_plan)

RESULTS_DIR = ROOT / 'results_p2'
RESULTS_P1 = ROOT / 'results_p1'


def load_config_b(config_path=CONFIG_PATH):
    """用官方读取函数取得容量、带宽和场景 B 的跨核 COPY 同步延迟。"""
    from evaluation_validation import read_evaluation_config
    from multicore_cut_evaluate_problem_2 import read_scene_b_config
    settings = read_evaluation_config(str(config_path))
    scene = read_scene_b_config(str(config_path))
    return {
        'capacity': dict(settings['capacity']),
        'bandwidth': settings['bandwidth'],
        'delay': scene['cross_core_copy_delay_cycles'],
    }
