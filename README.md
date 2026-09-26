# 2026 研究生数学建模竞赛 A 题（华为）第二版方案：NPU 多核切图与调度

本仓库公开 **第二版（V2，“超图划分 + 局部数学规划”，由 Claude Code 驱动完成）** 的全部求解代码、实验结果、官方评估器复核输出与设计文档，对应题目“通用神经网络处理器下的多核切图与调度”的问题一、二、三。

- 第一版（V1）仓库，供对比：<https://github.com/BGMYE/multicore-scheduling-v1-experiments-20260926>
- 两版逐例对比报告：[`对比_V1vsV2/README_两版对比.md`](对比_V1vsV2/README_两版对比.md)
- 完整归档（含解压后的数据包、两版对比的合并方案集与官方复评缓存）：见本仓库 Release `v2.0.0`

## 核心结果（官方评估器复核，100 个算例 × K=2/3/4/5，逐算例加速比的算术平均）

| 核数 K | 2 | 3 | 4 | 5 | 数据来源 |
|---|---|---|---|---|---|
| 问题一（场景 A） | 1.9388 | 2.6701 | 3.3244 | 3.8837 | `results_p1/summary_by_k.csv` 的 `mean_speedup` |
| 问题二（场景 B） | 2.0927 | 2.9181 | 3.6632 | 4.2965 | `results_p2/summary_by_k.csv` 的 `mean_speedup` |
| 问题三（场景 B + 只读 L2 FIFO Cache） | 2.0979 | 2.9492 | 3.7100 | 4.3736 | `results_p3/summary_by_k.csv` 的 `mean_speedup_cache` |

约为：问题一 1.94 / 2.67 / 3.32 / 3.88，问题二 2.09 / 2.92 / 3.66 / 4.30，问题三 2.10 / 2.95 / 3.71 / 4.37。
三问共 1,200 个最终方案全部通过官方评估器合法性检查（`all_valid = True`）。加速比分母统一为官方 `singlecore_evaluate.py` 的无 Cache 整图单核 Makespan。

与第一版对比（见 `对比_V1vsV2/README_两版对比.md`）：K=5 时第一版为 问题一 2.458、问题二 3.399、问题三 3.504，第二版分别领先 +58.0%、+26.4%、+24.8%。

## 目录结构

```
.
├── README.md                          本文件
├── requirements.txt                   Python 依赖（ortools；作图可选 matplotlib）
├── 2026研数模A题.docx                  官方题目
├── A题数据包.zip                       官方数据包（100 个算例 + 官方评估器 + 文档），需解压到 数据包/
├── 版本二_超图划分与局部数学规划方案.md   V2 设计方案
├── 三版本完整方案_文档对比.md            三个版本方案的文档对比
├── solution_p1/                       问题一求解代码（入口 run_all.py，单算例 solve_p1.py）
├── solution_p2/                       问题二求解代码（入口 run_all_p2.py，单算例 solve_p2.py）
├── solution_p3/                       问题三求解代码（入口 run_all_p3.py，单算例 solve_p3.py）
├── results_p1/ results_p2/ results_p3/
│   ├── k2..k5/                        每个算例的最终方案 <case>_multicore_res.json
│   ├── official_eval/                 官方评估器复核输出
│   ├── baselines/                     单核 / 官方 stub / 贪心 等基线
│   ├── solver_logs/ logs/ validation/ 求解日志与验证记录
│   ├── summary.csv, summary_by_k.csv  汇总表
│   └── README_问题*结果.md / report_tables.md / *.svg  结果说明与图表
├── 对比_V1vsV2/                        第一版 vs 第二版对比：报告、脚本、汇总表、图、完整性核验、合并集汇总
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
.venv/bin/python solution_p3/run_all_p3.py --workers 6
python3 solution_p3/make_report_p3.py
.venv/bin/python solution_p3/solve_p3.py 数据包/data/case_005.json -k 4 --budget 120 -o out.json
python3 solution_p3/verify_eval_c.py case_064 case_003
python3 solution_p3/run_baselines_p3.py --kinds single stub greedy pB p1plan --workers 4
```

8 核机器上问题一、问题二全量求解各约 2.5 小时。详细方法、验证与结果分析见 `results_p1/README_问题一结果.md`、`results_p2/README_问题二结果.md` 和 `版本二_超图划分与局部数学规划方案.md`。

### 两版对比
`对比_V1vsV2/` 中的脚本（`eval_official.py`、`compare.py`、`analysis.py`、`build_merged.py`、`make_figures.py`）需要第一版的重生成方案（`v1_regen/`，约 5.6 GB，未收录）和官方复评缓存（`official_eval/`）。本仓库只收录对比报告、汇总表（`tables/`）、图（`figures/`）、完整性核验（`integrity/`）和合并集汇总（`merged/*.csv`）；合并方案文件与 `official_eval/` 在 Release 归档中。

## 未收录内容
- `数据包/`（解压后的数据，约 242 MB）：请自行解压 `A题数据包.zip`；Release 归档中也包含解压版。
- `.venv/`、`__pycache__/`：运行环境产物。
- 第一版仓库：已在 [V1 仓库](https://github.com/BGMYE/multicore-scheduling-v1-experiments-20260926) 公开。
- `对比_V1vsV2/v1_regen/`（第一版方案重生成原始数据，约 5.6 GB）。
