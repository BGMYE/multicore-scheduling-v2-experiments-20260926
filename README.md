# 2026 研究生数学建模竞赛 A 题（华为）第二版方案：NPU 多核切图与调度

本仓库公开 **第二版（V2，“超图划分 + 局部数学规划”，由 Claude Code 驱动完成）** 的全部求解代码、实验结果、官方评估器复核输出与设计文档，对应题目“通用神经网络处理器下的多核切图与调度”的问题一、二、三。

- 完整归档（含解压后的官方数据包及第二版代码、结果与文档）：见本仓库 Release [`v2.1.0`](https://github.com/BGMYE/multicore-scheduling-v2-experiments-20260926/releases/tag/v2.1.0)（最新，补齐问题三结果说明、图与建模/实验文档）；旧版 `v2.0.0` 保留
- 建模文档：[`数学模型与公式.md`](数学模型与公式.md)（符号、决策变量、目标、约束、Cache 模型、下界、求解流程）；实验文档：[`实验方案设计.md`](实验方案设计.md)（设置、基线、指标、验证、时间与随机性、结果总表）
- 各问结果说明：[`问题一`](results_p1/README_问题一结果.md) · [`问题二`](results_p2/README_问题二结果.md) · [`问题三`](results_p3/README_问题三结果.md)

## 核心结果（官方评估器复核，100 个算例 × K=2/3/4/5，逐算例加速比的算术平均）

| 核数 K | 2 | 3 | 4 | 5 | 数据来源 |
|---|---|---|---|---|---|
| 问题一（场景 A） | 1.9388 | 2.6701 | 3.3244 | 3.8837 | `results_p1/summary_by_k.csv` 的 `mean_speedup` |
| 问题二（场景 B） | 2.0927 | 2.9181 | 3.6632 | 4.2965 | `results_p2/summary_by_k.csv` 的 `mean_speedup` |
| 问题三（场景 B + 只读 L2 FIFO Cache） | 2.0979 | 2.9492 | 3.7100 | 4.3736 | `results_p3/summary_by_k.csv` 的 `mean_speedup_cache` |

约为：问题一 1.94 / 2.67 / 3.32 / 3.88，问题二 2.09 / 2.92 / 3.66 / 4.30，问题三 2.10 / 2.95 / 3.71 / 4.37。
三问共 1,200 个最终方案全部通过官方评估器合法性检查（`all_valid = True`）。加速比分母统一为官方 `singlecore_evaluate.py` 的无 Cache 整图单核 Makespan。


## 问题三结果（场景 B + 只读 L2 FIFO Cache）

以问题二最终方案 P_B 为初值，在官方问题三评估器中定位高价值重复 miss 窗口，生成合法候选并完整回放；400 个方案全部官方合法。
表格由 `solution_p3/make_report_p3.py` 从 `results_p3/summary.csv` 重新计算（并与 `summary_by_k.csv` 交叉核对），详见 [`results_p3/README_问题三结果.md`](results_p3/README_问题三结果.md)。

**平均加速比**（分母：无 L2 官方单核基准；K=1 行为单核方案在只读 Cache 下的比值）

| 核数 K | 用例数 | 无 L2（P_B） | 只读 Cache（P_C） | 问题一方案@Cache | 简单贪心@Cache | stub@Cache | 全部官方合法 |
|---|---|---|---|---|---|---|---|
| 1 | 100 | 1.000 | 1.009 | — | — | — | True |
| 2 | 100 | 2.093 | 2.098 | 1.992 | 1.094 | 1.117 | True |
| 3 | 100 | 2.918 | 2.949 | 2.792 | 1.183 | 1.183 | True |
| 4 | 100 | 3.663 | 3.710 | 3.490 | 1.342 | 1.230 | True |
| 5 | 100 | 4.297 | 4.374 | 4.114 | 1.369 | 1.264 | True |

K=1 行：无 L2 为单核基准本身（=1）；只读 Cache 为同一单核方案在问题三评估器下的结果。

**方案文档 3.5 节四格配对与命中率**（S_hardware = T_B(P_B)/T_C(P_B)，S_schedule = T_C(P_B)/T_C(P_C)，S_overall = T_B(P_B)/T_C(P_C)）

| 核数 K | S_hardware | S_schedule | S_overall | 命中率 P_B | 命中率 P_C |
|---|---|---|---|---|---|
| 2 | 1.0013 | 1.0011 | 1.0024 | 12.50% | 12.56% |
| 3 | 1.0033 | 1.0064 | 1.0098 | 17.98% | 18.64% |
| 4 | 1.0079 | 1.0063 | 1.0144 | 21.18% | 21.84% |
| 5 | 1.0137 | 1.0082 | 1.0222 | 23.67% | 24.79% |

| 加速比曲线 | 配对对照 | 命中率 |
|---|---|---|
| ![](results_p3/speedup_p3.png) | ![](results_p3/pairing_p3.png) | ![](results_p3/hitrate_p3.png) |

## 三问总表

由 [`tools/make_overview_tables.py`](tools/make_overview_tables.py) 从三个 `summary.csv` 逐行计算（逐算例加速比的算术平均；全部为官方评估器输出）。

| 问题 / 方法 | K=2 | K=3 | K=4 | K=5 | 方案数 | 官方合法 |
|---|---|---|---|---|---|---|
| 问题一（场景 A）本文 | 1.939 | 2.670 | 3.324 | 3.884 | 400 | 400/400 |
| 问题一 简单贪心 | 1.085 | 1.178 | 1.326 | 1.355 | — | — |
| 问题一 官方 stub | 0.955 | 0.960 | 0.965 | 0.964 | — | — |
| 问题二（场景 B）本文 | 2.093 | 2.918 | 3.663 | 4.297 | 400 | 400/400 |
| 问题二 问题一方案@B | 1.990 | 2.781 | 3.476 | 4.075 | — | — |
| 问题二 简单贪心 | 1.083 | 1.175 | 1.331 | 1.361 | — | — |
| 问题二 官方 stub | 1.110 | 1.172 | 1.217 | 1.248 | — | — |
| 问题三（B + 只读 Cache）本文 P_C | 2.098 | 2.949 | 3.710 | 4.374 | 400 | 400/400 |
| 问题三 无 L2 的 P_B（对照） | 2.093 | 2.918 | 3.663 | 4.297 | — | — |
| 问题三 问题一方案@Cache | 1.992 | 2.792 | 3.490 | 4.114 | — | — |
| 问题三 简单贪心@Cache | 1.094 | 1.183 | 1.342 | 1.369 | — | — |
| 问题三 官方 stub@Cache | 1.117 | 1.183 | 1.230 | 1.264 | — | — |

| 问题 | 平均 Makespan/LB0 | 单个 (算例,K) 求解平均/最长 (s) | 官方复核单个方案最长 (s) | 求解评分=官方复核 | K−1 沿用 |
|---|---|---|---|---|---|
| 问题一 | 1.553 | 74.4 / 301.6 | 123.2 | 400/400 | 19 |
| 问题二 | 1.356 | 88.1 / 311.3 | 17.3 | 400/400 | 12 |
| 问题三 | 1.335 | 58.7 / 189.7 | 15.9 | 400/400 | 4 |


## 目录结构

```
.
├── README.md                          本文件
├── requirements.txt                   Python 依赖（ortools；作图可选 matplotlib）
├── 2026研数模A题.docx                  官方题目
├── A题数据包.zip                       官方数据包（100 个算例 + 官方评估器 + 文档），需解压到 数据包/
├── 版本二_超图划分与局部数学规划方案.md   V2 设计方案
├── 数学模型与公式.md                    三问统一的数学模型（符号、变量、目标、约束、下界、算法流程）
├── 实验方案设计.md                      实验设置、基线、指标、验证、时间与随机性、结果总表
├── tools/                             三问总表生成脚本 make_overview_tables.py
├── solution_p1/                       问题一求解代码（入口 run_all.py，单算例 solve_p1.py）
├── solution_p2/                       问题二求解代码（入口 run_all_p2.py，单算例 solve_p2.py）
├── solution_p3/                       问题三求解代码（入口 run_all_p3.py，单算例 solve_p3.py）
├── results_p1/ results_p2/ results_p3/
│   ├── k2..k5/                        每个算例的最终方案 <case>_multicore_res.json
│   ├── official_eval/                 官方评估器复核输出
│   ├── baselines/                     单核 / 官方 stub / 贪心 等基线
│   ├── solver_logs/ logs/ validation/ 求解日志与验证记录
│   ├── summary.csv, summary_by_k.csv  汇总表
│   └── README_问题*结果.md / report_tables.md / *.svg（问题三另有 300 dpi *.png）  结果说明与图表
└── prompts/                           驱动 Claude Code 完成本工作所用的提示词
```

## 环境与数据准备

```bash
git clone https://github.com/BGMYE/multicore-scheduling-v2-experiments-20260926.git
cd multicore-scheduling-v2-experiments-20260926
# 数据包解压到 数据包/（zip 内顶层即 code/ data/ docs/ README.md）
python3 -c "import zipfile; zipfile.ZipFile('A题数据包.zip').extractall('数据包')"
# 依赖：Python ≥ 3.10（原实验使用 Python 3.13.5）
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

代码通过 `solution_p*/common*.py` 中的相对路径定位 `数据包/data`、`数据包/code`（官方评估器）和 `results_p*`，请在仓库根目录运行以下命令。

## 复现

### 问题一
```bash
.venv/bin/python solution_p1/run_baselines.py --workers 4        # 单核基准 + 官方 stub 基线
.venv/bin/python solution_p1/run_greedy_baseline.py --workers 6  # 简单贪心基线
.venv/bin/python solution_p1/run_all.py --workers 6              # 求解全部算例 × K=2..5 → 官方复核 → summary.csv
python3 solution_p1/make_report.py                               # 图表与表格
# 单个算例 + 官方评估
.venv/bin/python solution_p1/solve_p1.py 数据包/data/case_003.json -k 4 --budget 120 -o case_003_multicore_res.json
python3 数据包/code/multicore_cut_evaluate_problem_1.py 数据包/data/case_003.json case_003_multicore_res.json --config 数据包/data/config.txt
# 验证
python3 solution_p1/verify_fast_eval.py case_064 case_001 case_006 case_019 case_008
.venv/bin/python solution_p1/verify_cpsat_enum.py case_064 --windows 4
```

### 问题二（依赖问题一结果作 warm start / 基线）
```bash
.venv/bin/python solution_p2/run_all_p2.py --workers 6
python3 solution_p2/make_report_p2.py
.venv/bin/python solution_p2/solve_p2.py 数据包/data/case_044.json -k 4 --budget 120 -o case_044_multicore_res.json
python3 数据包/code/multicore_cut_evaluate_problem_2.py 数据包/data/case_044.json case_044_multicore_res.json --config 数据包/data/config.txt
python3 solution_p2/verify_eval_b.py case_064 case_001 case_006 case_003 case_016 case_044 case_005
.venv/bin/python solution_p2/ablation_no_warm.py --workers 6
```

### 问题三（以问题二最终方案为初值）
```bash
.venv/bin/python solution_p3/run_baselines_p3.py --workers 4      # 单核@Cache、P_B@Cache、stub、贪心、问题一方案（官方问题三评估）
.venv/bin/python solution_p3/run_all_p3.py --workers 6            # 求解（断点续跑）→单调性→官方问题三/问题二复核→基线→summary
.venv/bin/python solution_p3/make_report_p3.py                    # 三张图（SVG + 300 dpi PNG）与 report_tables.md
.venv/bin/python solution_p3/write_readme_p3.py                   # 生成 README_问题三结果.md（数字全部现场计算）
.venv/bin/python solution_p3/solve_p3.py 数据包/data/case_046.json -k 5 --budget 120 -o out.json
python3 数据包/code/multicore_cut_evaluate_problem_3.py 数据包/data/case_046.json out.json --config 数据包/data/config.txt
python3 solution_p3/verify_eval_c.py case_064 case_001 case_005 case_003 case_044 case_016
python3 tools/make_overview_tables.py                             # 三问总表
```

8 核机器上问题一、问题二全量求解各约 2.5 小时，问题三约 2 小时。详细方法、验证与结果分析见 `results_p1/README_问题一结果.md`、`results_p2/README_问题二结果.md`、`results_p3/README_问题三结果.md`、`数学模型与公式.md`、`实验方案设计.md` 和 `版本二_超图划分与局部数学规划方案.md`。


## 未收录内容
- `数据包/`（解压后的数据，约 242 MB）：请自行解压 `A题数据包.zip`；Release 归档中也包含解压版。
- `.venv/`、`__pycache__/`：运行环境产物。
