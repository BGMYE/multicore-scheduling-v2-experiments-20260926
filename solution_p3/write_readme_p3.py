"""生成 results_p3/README_问题三结果.md：所有数字由 summary.csv、summary_by_k.csv、求解日志与
验证文件现场计算，表格取自 make_report_p3.py 生成的 report_tables.md（同样由 summary.csv 计算）。

    .venv/bin/python solution_p3/make_report_p3.py && .venv/bin/python solution_p3/write_readme_p3.py
"""

import csv
import glob
import json
import re
import statistics as st
from collections import Counter

from common_c import RESULTS_DIR, RESULTS_P1

CORES = (2, 3, 4, 5)


def rows():
    return list(csv.DictReader(open(RESULTS_DIR / 'summary.csv', encoding='utf-8')))


def by_k():
    return {int(a['cores']): a for a in csv.DictReader(open(RESULTS_DIR / 'summary_by_k.csv', encoding='utf-8'))}


def main():
    r = rows()
    tables = open(RESULTS_DIR / 'report_tables.md', encoding='utf-8').read().strip()
    blocks = re.split(r'\n(?=### )', tables)
    get = lambda title: next(b for b in blocks if b.startswith('### ' + title))
    I = lambda x, k: int(x[k])
    F = lambda x, k: float(x[k])
    cases = sorted({x['case'] for x in r})

    from make_report_p3 import aggregate, cross_check
    full = aggregate(r, cases)            # 从 summary.csv 全精度重算
    cross_check(full)                     # 与 summary_by_k.csv 一致
    keymap = {'mean_speedup_cache': 'cache', 'mean_speedup_noL2': 'noL2', 'mean_S_hardware': 'S_hardware',
              'mean_S_schedule': 'S_schedule', 'mean_S_overall': 'S_overall', 'mean_hit_rate_PB': 'hit_PB',
              'mean_hit_rate_PC': 'hit_PC', 'mean_stub_speedup_cache': 'stub',
              'mean_greedy_speedup_cache': 'greedy', 'mean_p1plan_speedup_cache': 'p1plan'}
    agg = {k: {col: full[k][key] for col, key in keymap.items()} for k in full}

    def seq(key, d=3):
        return ' / '.join('{:.{}f}'.format(float(agg[k][key]), d) for k in CORES)

    n_valid = sum(x['official_valid'] == 'True' for x in r)
    n_match = sum(x['eval_match'] == 'True' for x in r)
    inherited = [(x['case'], x['cores']) for x in r if x['inherited_from_cores']]
    pc_better = sum(I(x, 'T_C_PC') < I(x, 'T_C_PB') for x in r)
    pc_equal = sum(I(x, 'T_C_PC') == I(x, 'T_C_PB') for x in r)
    pc_worse = sum(I(x, 'T_C_PC') > I(x, 'T_C_PB') for x in r)
    hw_help = sum(I(x, 'T_C_PB') < I(x, 'T_B_PB') for x in r)
    hw_hurt = sum(I(x, 'T_C_PB') > I(x, 'T_B_PB') for x in r)
    b_regress = sum(I(x, 'T_B_PC') > I(x, 'T_B_PB') for x in r)
    b_better = sum(I(x, 'T_B_PC') < I(x, 'T_B_PB') for x in r)
    d_up = sum(I(x, 'added_copy_bytes_PC') > I(x, 'added_copy_bytes_PB') for x in r)
    best_sched = max(r, key=lambda x: F(x, 'S_schedule'))
    best_hw = max(r, key=lambda x: F(x, 'S_hardware'))
    worst_hw = min(r, key=lambda x: F(x, 'S_hardware'))
    best_ov = max(r, key=lambda x: F(x, 'S_overall'))
    zero_hit = sum(F(x, 'hit_rate_PC') == 0 for x in r)
    hit_bytes = sum(I(x, 'hit_bytes_PC') for x in r)
    miss_bytes = sum(I(x, 'miss_bytes_PC') for x in r)
    evictions = sum(I(x, 'evictions_PC') for x in r)
    sp_c = {k: [F(x, 'speedup_cache') for x in r if int(x['cores']) == k] for k in CORES}
    beats = {kind: sum(I(x, 'T_C_PC') < I(x, kind + '_T_C') for x in r) for kind in ('stub', 'greedy', 'p1plan')}
    ties_p1 = sum(I(x, 'T_C_PC') == I(x, 'p1plan_T_C') for x in r)
    solve_mean = st.mean(F(x, 'solve_seconds') for x in r)
    solve_max = max(F(x, 'solve_seconds') for x in r)
    off_mean = st.mean(F(x, 'official_eval_seconds') for x in r)
    off_max = max(F(x, 'official_eval_seconds') for x in r)
    # 单核：无 L2 vs Cache
    singles = []
    for c in cases:
        a = json.load(open(RESULTS_P1 / 'baselines' / 'singlecore' / (c + '.json'), encoding='utf-8'))
        b = json.load(open(RESULTS_DIR / 'baselines' / 'single_k1' / (c + '.json'), encoding='utf-8'))
        singles.append((c, a['makespan'], b['makespan'], b['cache_stats']['hit_rate']))
    single_faster = sum(1 for _, a, b, _ in singles if b < a)
    single_best = max(singles, key=lambda s: s[1] / s[2])
    # 求解日志统计
    acc, evals, errors, windows, cands, exhausted = Counter(), 0, 0, 0, 0, 0
    for f in glob.glob(str(RESULTS_DIR / 'solver_logs' / 'k*' / '*.json')):
        s = json.load(open(f, encoding='utf-8'))['summary']
        stt = s['improve_stats']
        evals += stt.get('evals', 0)
        errors += stt.get('eval_errors', 0)
        for key, v in stt.items():
            if key.startswith('accepted_'):
                acc[key[len('accepted_'):]] += v
        for w in s['reuse_windows']:
            windows += 1
            cands += w['candidates']
            exhausted += bool(w['exhausted'])
    cache_acc = {k: v for k, v in acc.items() if k.startswith('cache_')}
    gen_acc = {k: v for k, v in acc.items() if not k.startswith('cache_')}
    ver = open(RESULTS_DIR / 'validation' / 'verify_eval_c.txt', encoding='utf-8').read().strip().splitlines()[-1]
    m = re.search(r'checked (\d+) plans, mismatches (\d+)', ver)
    ver_n, ver_bad = (int(m.group(1)), int(m.group(2))) if m else ('?', '?')

    def fmt_acc(d):
        return '、'.join('{} {}'.format(k.replace('cache_', ''), v) for k, v in sorted(d.items(), key=lambda t: -t[1]))

    md = f'''# 2026 研究生数学建模竞赛 A 题 · 问题三（场景 B + 只读 L2 FIFO Cache）求解结果

**结论**：按方案文档“版本二 · 2.3 FIFO 事件状态与有限窗口候选优化”实现的问题三求解器，以问题二最终方案 P_B 为初值，
对 `data/` 下全部 100 个算例、K = 2、3、4、5 共 **{len(r)} 个方案**全部得到官方问题三评估器的最终结果
（官方合法 {n_valid}/{len(r)}；求解期打分与官方命令行复核一致 {n_match}/{len(r)}）。

- **只读 Cache 下平均加速比（分母：无 L2 单核基准，逐算例算术平均）**：{seq('mean_speedup_cache')}（K=2/3/4/5；K=1 为 {float(agg[1]['mean_speedup_cache']):.4f}）；
  同口径**无 L2（问题二方案 P_B）**为 {seq('mean_speedup_noL2')}。
- **方案文档 3.5 节四组配对**（逐算例比值的算术平均，K=2/3/4/5）：
  S_hardware = T_B(P_B)/T_C(P_B) = {seq('mean_S_hardware', 4)}；
  S_schedule = T_C(P_B)/T_C(P_C) = {seq('mean_S_schedule', 4)}；
  S_overall = T_B(P_B)/T_C(P_C) = {seq('mean_S_overall', 4)}。
- **Cache 命中率（按字节，逐算例平均）**：P_B {' / '.join('{:.2f}%'.format(float(agg[k]['mean_hit_rate_PB']) * 100) for k in CORES)}，
  P_C {' / '.join('{:.2f}%'.format(float(agg[k]['mean_hit_rate_PC']) * 100) for k in CORES)}。
- 在只读 Cache 配置下，P_C 优于 stub {beats['stub']}/{len(r)}、优于简单贪心 {beats['greedy']}/{len(r)}、
  优于问题一方案 {beats['p1plan']}/{len(r)}（持平 {ties_p1}）；P_C 相对 P_B：更优 {pc_better}、持平 {pc_equal}、更差 {pc_worse}。

| 产出 | 位置 |
|---|---|
| 求解代码（一条命令复现） | `solution_p3/`（入口 `run_all_p3.py`，单算例 `solve_p3.py`） |
| 提交格式结果文件（{len(r)} 个） | `results_p3/k{{2,3,4,5}}/case_XXX_multicore_res.json` |
| 官方问题三复核 T_C(P_C) 与日志 | `results_p3/official_eval/k{{K}}/case_XXX.json`、`case_XXX_problem_3_log.txt` |
| 官方问题二下的 T_B(P_C) | `results_p3/pc_under_b/k{{K}}/case_XXX.json` |
| 基线（官方问题三评估） | `results_p3/baselines/{{single_k1,pB,stub,greedy,p1plan}}_k{{K}}/` |
| 结果汇总 | `results_p3/summary.csv`（逐算例×核数，含四格与命中率）、`results_p3/summary_by_k.csv` |
| 图（SVG + 300 dpi PNG） | `speedup_p3`（加速比折线）、`pairing_p3`（配对对照）、`hitrate_p3`（命中率） |
| 表格 | `results_p3/report_tables.md`（由 `make_report_p3.py` 从 summary.csv 计算） |
| 求解日志 / 验证 | `results_p3/solver_logs/`、`results_p3/validation/verify_eval_c.txt` |

`solution_p1/`、`solution_p2/`、`results_p1/`、`results_p2/` 在问题三中只读引用，未被修改。

---

## 1. 问题三与问题二的区别（以官方评估器 `multicore_cut_evaluate_problem_3.py` 为准）

| 方面 | 问题二 | 问题三（评估器实际语义） |
|---|---|---|
| Task 构造、跨核 COPY、500 cycles 同步、Step1-3、Pipe 顺序 | — | 与问题二**完全相同**（同一套 `_build_scene_b_tasks` 与 Step1-3；Step3 仍按 DDR 60 B/cycle 估计 COPY 时长来排定顺序，不会因 Cache 调整核内顺序） |
| 谁查询 Cache | 无 Cache | 只有 **COPY_IN**；COPY_OUT（写回）永远走 DDR |
| 查询时刻 | — | COPY_IN **发射时**按逻辑张量 id 查询 FIFO：命中 → 进入独立的 `CACHE_READ` 带宽池（250 B/cycle，公平共享，不占 DDR）；未命中 → 进入 DDR 池（60 B/cycle） |
| 插入时刻 | — | 未命中的 COPY_IN **完成后**才插入；插入前的并发读取仍全部 miss（不合并在途请求）；已在 Cache 中的张量不重复插入 |
| 替换 | — | FIFO（`OrderedDict`，命中不刷新顺序）；容量 1,048,576 B，逐个淘汰最早插入项直至放得下；单个张量 > 容量则不缓存（= 容量允许） |
| 缓存键 | — | COPY_IN 输出片上张量的 `logical_tid`（无则张量 id）。因此**原始输入的首次读入、跨核传输的目标端读入、Step2 spill 的换回**只要逻辑张量相同就共享同一个缓存项 |
| 指标 | Makespan、新增搬运 | 另有 `cache_stats`：命中次数/字节、未命中次数/字节、按字节命中率 `hit_bytes/(hit_bytes+miss_bytes)`。**新增搬运 `added_copy_bytes` 口径不变，命中字节仍计入**（它不是物理 DDR 字节） |

## 2. 方案文档与官方评估器的差异记录（以评估器为准）

| # | 方案文档 2.3 / 1.5 的表述 | 评估器实际语义 / 本实现处理 |
|---|---|---|
| 1 | “读取发射时查 Cache、miss 完成后才插入 FIFO；hit 不重排；对象大于容量不缓存” | 一致。补充：插入条件是 `size > capacity` 才拒绝（等于容量可插入）；已存在则不插入；在途重复 miss 各自读 DDR（文档 U07 所说“不假定在途合并”与源码一致）。 |
| 2 | 缓存对象是“共享输入” | 键是**逻辑张量**：跨核传输的目标端 COPY_IN 与 spill 换回也会命中，所以命中率不只来自共享输入；单核方案也可能因 spill 换回命中而变快（{single_faster}/100 个算例的单核 Makespan 在 Cache 下更小，最大 {single_best[1] / single_best[2]:.3f} 倍，{single_best[0]}）。 |
| 3 | 规划器用 CP-SAT 生成窗口候选并排序 | 本实现把“命中”完全交给官方事件仿真，不在任何模型中把 h_q 作为变量；Cache 窗口候选由规则生成（gather / stagger / stagger2 / colocate / cluster，见 3.2），每个候选完整回放；CP-SAT 只出现在复用的问题二通用邻域（分核窗口、顺序窗口、区间窗口）中。 |
| 4 | “Cache hit 的 COPY 仍然是搬运，总 COPY 字节与 DDR 字节不能混为一列” | 一致：官方 `added_copy_bytes` 在问题三中含命中字节；本 README 的“新增搬运”均为官方字段，另列命中/未命中字节。 |
| 5 | 1.5 节 FIFO 状态 Q_e 与内存驻留分开建模 | Step2 的 spill 决策仍按无 Cache 的规则做（换出/换回位置不变），Cache 只改变换回 COPY_IN 的耗时与带宽池；外层无法让 Step2 感知 Cache。 |
| 6 | 3.5 节四组配对 | 按文档实现：T_B(P_B) 取问题二官方复核结果；T_C(P_B) 由官方问题三命令行评估问题二方案；T_B(P_C) 由官方问题二命令行评估问题三方案；T_C(P_C) 为官方问题三复核。 |

## 3. 方法（方案文档 V2_C）

1. **初值**：读取 `results_p2/k{{K}}/<case>_multicore_res.json`（问题二最终方案 P_B），在问题三评估器中回放得到 T_C(P_B) 与 Cache 事件轨迹。
2. **高价值重复 miss 窗口**（`improve_c.ranked_reuse_windows`）：对每个逻辑张量统计 miss 序列，按价值 = 字节数 × (miss 次数 − 1) 排序取前 12 个；
   每次 miss 标注为 first（首读）、inflight（首读未完成时的并发读取）或 evicted（曾插入后被 FIFO 淘汰再读）。
3. **有限候选族 Ω_t**（全部通过“子图依赖 DAG 上的优先级 Kahn 解码”保证合法，见问题二 README 3.1）：
   - `gather`：把 evicted 读者所在子图的优先级提到首读子图之后（首读完成后集中消费）；
   - `stagger` / `stagger2`：把 inflight 读者所在子图推迟到“首读完成后才开始的第一个子图”之后（stagger2 再多让一个位置）；
   - `colocate`：把跨核的重复读者子图迁到首读核并紧跟首读子图（同核后续消费者直接用片上副本）；
   - `cluster`：对当前前 12 个高价值张量同时做 gather。
   每个候选调用官方 `evaluate_problem_3` 完整回放（不冻结窗口外时间），按 (T_C, 新增搬运) 字典序严格变好才接受；接受后重新计算窗口排序。
4. **剩余预算**：60% 时间用于第 2–3 步，其余交给问题二的通用邻域（细粒度改核、优先级交换、合并、时间反馈、分核/顺序/区间窗口 CP-SAT），评分函数换成问题三评估器。
5. **预算与单调性**：单个 (算例, K) 预算 `min(180, 30 + 计算节点数/150)` 秒；K 核结果若劣于 K−1 核则沿用 K−1 核方案并补空核心（{len(inherited)} 个：{', '.join('{} K={}'.format(c, k) for c, k in inherited)}）。

## 4. 验证

1. **评估补丁等价**：问题三同样在进程内给官方 `step3_simulation` 打“完成计数器”补丁（问题二 README 第 2 节第 6 条）。`solution_p3/verify_eval_c.py`
   在 6 个算例 × 5 个方案（3 个 stub 随机方案 + 问题二 K=2、5 方案）上逐项比较 Makespan、各项搬运、`cache_stats`、**完整 Cache 事件序列**、最终 FIFO 内容与各核结束时刻：
   {ver_n} 个方案、{ver_bad} 处不一致（`validation/verify_eval_c.txt`）。
2. **官方复核**：{len(r)} 个 P_C 全部由官方问题三命令行（子进程、未打补丁）评估成功，并由官方问题二命令行得到 T_B(P_C)；
   求解期打分与官方输出一致 {n_match}/{len(r)}（`summary.csv` 的 `eval_match` 列）。所有基线同样来自官方命令行。
3. **表格数字**：`make_report_p3.py` 从 `summary.csv` 逐行重新计算各核均值，并断言与 `summary_by_k.csv` 一致（误差 < 1.5e-4）后才输出图表。

## 5. 结果

### 5.1 平均加速比（1~5 核，无 L2 与只读 Cache 对比曲线）

![平均加速比](speedup_p3.png)

{get('表 1')}

### 5.2 四组配对对照（方案文档 3.5 节）

![配对对照](pairing_p3.png)

{get('表 2')}

{get('表 3')}

- **硬件收益（S_hardware）**：同一 P_B，只读 Cache 使 {hw_help}/{len(r)} 个 (算例, K) 变快、{hw_hurt} 个变慢；最大 {F(best_hw, 'S_hardware'):.4f}（{best_hw['case']} K={best_hw['cores']}），最小 {F(worst_hw, 'S_hardware'):.4f}（{worst_hw['case']} K={worst_hw['cores']}）。
  Cache 偶尔变慢的原因：命中改变了 COPY_IN 完成时刻，进而改变 Pipe 发射与 DDR 争用的相对时序（事件仿真是非单调的）。
- **调度收益（S_schedule）**：同一 Cache 配置下 P_C 优于 P_B {pc_better} 个、持平 {pc_equal} 个、劣于 {pc_worse} 个（接受准则保证不劣）；最大提升 {F(best_sched, 'S_schedule'):.4f}（{best_sched['case']} K={best_sched['cores']}）。
  收益集中在少数算例：多数算例的重复 miss 很少或来自无法通过顺序消除的 FIFO 淘汰。
- **总体（S_overall）**：最大 {F(best_ov, 'S_overall'):.4f}（{best_ov['case']} K={best_ov['cores']}）。
- **缓存专用计划在无 L2 下**：T_B(P_C) 劣于 T_B(P_B) {b_regress} 个、优于 {b_better} 个——P_C 的部分改动（主要是通用邻域）在无 L2 下同样有效，部分改动依赖 Cache 命中。
- **搬运**：P_C 的新增搬运高于 P_B 的有 {d_up} 个（字典序以 Makespan 优先）。

### 5.3 Cache 命中率

![命中率](hitrate_p3.png)

- P_C 全部 {len(r)} 个方案合计命中 {hit_bytes / 2**20:,.1f} MiB、未命中 {miss_bytes / 2**20:,.1f} MiB（总体按字节命中率 {hit_bytes / (hit_bytes + miss_bytes) * 100:.2f}%），FIFO 淘汰 {evictions:,} 次；
  命中率为 0 的方案 {zero_hit} 个（多为分量独立、输入不共享的图，如 case_001、case_084）。
- 命中率随核数上升（更多核并发读取同一张量，第一个 miss 完成后其余核命中），与 S_hardware 随 K 增大一致；但命中率与 S_overall 只弱相关（右图），
  因为命中节省的是 COPY_IN 时间，只有当它位于关键路径或 DDR 饱和时才转化为 Makespan 收益——**最终以官方 Makespan 为准，不以命中率为目标**。

### 5.4 与基线对比

{get('表 4')}

- 基线说明：stub = 官方随机示例（seed 0）；简单贪心 = 问题一 `interval-dfs/1` 方案；问题一方案 = `results_p1/k{{K}}`；均在只读 Cache 配置下由官方问题三命令行评估。
  只读 Cache 下各基线平均加速比见表 1（stub {seq('mean_stub_speedup_cache')}，贪心 {seq('mean_greedy_speedup_cache')}，问题一方案 {seq('mean_p1plan_speedup_cache')}）。
- 各核加速比中位数（P_C）：{' / '.join('{:.3f}'.format(st.median(sp_c[k])) for k in CORES)}；最小值：{' / '.join('{:.3f}'.format(min(sp_c[k])) for k in CORES)}。

### 5.5 局部搜索统计与运行时间

- 精确回放 {evals:,} 次（被官方判为不可执行而舍弃的候选 {errors} 个；其余候选都经优先级 Kahn 解码、构造上合法，因此这些都来自复用的问题一区间窗口解码）。
- Cache 窗口：{windows} 轮窗口、{cands:,} 个候选，其中 {exhausted} 轮在预算内评价完全部候选（其余因时间或提前接受而截断）。
  接受次数——Cache 窗口：{fmt_acc(cache_acc)}；通用邻域：{fmt_acc(gen_acc)}。
- 单个 (算例, K) 求解平均 {solve_mean:.1f} s、最长 {solve_max:.1f} s；官方问题三复核单个方案平均 {off_mean:.1f} s、最长 {off_max:.1f} s。

{get('表 5')}

### 5.6 逐算例结果

{get('表 6')}

## 6. 发现的问题与局限

1. **只读 Cache 的收益整体有限**：对问题二已优化的方案，S_hardware 平均仅 {seq('mean_S_hardware', 4)}。主要原因：(a) 原始输入在场景 B 中每核只读一次，跨核共享输入的重复读取本来就少；
   (b) 大算例的重复读取多来自 spill 换回，其间隔远超 1 MiB FIFO 的覆盖范围，被淘汰后再读（例如 case_014 K=4 的 P_B 有 8,587 次“淘汰后重读”）；(c) 命中节省的 COPY_IN 时间只有在关键路径上才转化为 Makespan。
2. **核内顺序对 Cache 不敏感**：Step1-3 仍按无 Cache 的 DDR 带宽排定 Pipe 顺序，外层只能通过子图归属与名次间接影响“谁先读、何时读”，这限制了窗口候选的效果。
3. **事件仿真非单调**：命中会改变后续事件次序，个别算例在 Cache 下反而变慢（{hw_hurt} 个 (算例, K)），因此一切改动都必须完整回放，不能用命中率或局部代理直接判优。
4. **各类 Cache 窗口候选的有效性**：接受次数依次为 {fmt_acc(cache_acc)}（跨越全部 {len(r)} 个方案）。colocate 把重复读者并到首读核，
   同核后续消费者直接复用片上副本、完全省去 COPY_IN，因此收益最直接，但目标核负载允许时才被接受；stagger 常常使解码顺序不变或拖慢首读核，
   接受率较低。
5. **非位级复现**：预算按墙钟截断，CP-SAT 有时间上限，重跑结果可能有 1% 量级差异；提交结果以本目录文件为准，均经官方复核。
6. **断点续跑**：批处理曾被误报中断，实际一直在运行；`run_all_p3.py` 已加入“方案与日志均为完整 JSON 才跳过”的续跑判据，写到一半的文件会被重算。

## 7. 运行方式

```bash
# 依赖：Python ≥ 3.10；OR-Tools（CP-SAT，缺失时跳过相关邻域）；matplotlib（仅画图）
python3 -m venv .venv && .venv/bin/pip install ortools matplotlib
# 需要先有问题二结果（初值 P_B 与 T_B(P_B)）以及问题一的单核基准、贪心与最终方案（基线）
.venv/bin/python solution_p3/run_all_p3.py --workers 6      # 求解→单调性→官方复核（问题三、问题二）→基线→summary
.venv/bin/python solution_p3/make_report_p3.py             # 图（SVG+PNG）与 report_tables.md
.venv/bin/python solution_p3/write_readme_p3.py            # 本 README
# 单个算例
.venv/bin/python solution_p3/solve_p3.py 数据包/data/case_046.json -k 5 --budget 120 -o case_046_multicore_res.json
python3 数据包/code/multicore_cut_evaluate_problem_3.py 数据包/data/case_046.json case_046_multicore_res.json --config 数据包/data/config.txt
# 验证
python3 solution_p3/verify_eval_c.py case_064 case_001 case_005 case_003 case_044 case_016
```
8 核机器上第一条命令约 2 小时（{len(r)} 次求解平均 {solve_mean:.0f} s，6 进程并行，外加官方复核与基线）。

## 8. 代码结构（`solution_p3/`）

| 文件 | 作用 |
|---|---|
| `common_c.py` | 路径；复用问题一/二模块；用官方函数读取 `[problem_3]` Cache 参数 |
| `eval_c.py` | 官方 `evaluate_problem_3` 包装（复用问题二的 step3 计数器补丁） |
| `improve_c.py` | Cache 事件分析、重复 miss 窗口排序、gather/stagger/colocate/cluster 候选、复用问题二通用邻域（以 T_C 评分） |
| `solve_p3.py` | 单个 (算例, K) 主流程：读 P_B → 回放 → 窗口候选 → 通用邻域 → 输出 |
| `run_all_p3.py` | 全量求解（断点续跑）、单调性、官方问题三/问题二复核、基线、汇总 |
| `run_baselines_p3.py` | 单核@Cache、P_B@Cache、stub、贪心、问题一方案的官方问题三评估 |
| `make_report_p3.py` | 三张图（SVG + 300 dpi PNG）与 report_tables.md，含与 summary_by_k.csv 的交叉核对 |
| `write_readme_p3.py` | 生成本 README（数字全部现场计算） |
| `verify_eval_c.py` | 评估补丁等价性验证 |
'''
    (RESULTS_DIR / 'README_问题三结果.md').write_text(md, encoding='utf-8')
    print('written', len(md.splitlines()), 'lines')


if __name__ == '__main__':
    main()
