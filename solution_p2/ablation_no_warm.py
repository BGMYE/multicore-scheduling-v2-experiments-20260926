"""消融：不读取问题一最终方案（--no-p1-warm）时的问题二求解结果。

对每隔 5 个取 1 个的 20 个算例、K=4，用与主实验相同的时间预算求解，结果写入
results_p2/ablation_no_p1_warm/（不影响主结果）。评分为 eval_b（与官方逐项一致）。

    .venv/bin/python solution_p2/ablation_no_warm.py --workers 6
"""
import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor, as_completed

from common_b import DATA_DIR, RESULTS_DIR
from run_all_p2 import budget_for

OUT = RESULTS_DIR / 'ablation_no_p1_warm'


def job(case, k):
    from solve_p2 import solve
    _, s = solve(DATA_DIR / (case + '.json'), k, budget_for(case), OUT / (case + '_k{}.json'.format(k)),
                 OUT / 'logs' / (case + '_k{}.json'.format(k)), verbose=False, p1_warm=False)
    return case, k, s['makespan_eval'], s['added_copy_bytes_eval'], s['initial_constructor']


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--k', type=int, default=4)
    p.add_argument('--workers', type=int, default=6)
    a = p.parse_args()
    cases = sorted(x.stem for x in DATA_DIR.glob('case_*.json'))[::5]
    rows = []
    with ProcessPoolExecutor(a.workers) as pool:
        for f in as_completed([pool.submit(job, c, a.k) for c in cases]):
            rows.append(f.result())
            print(*f.result(), flush=True)
    main_rows = {(r['case'], int(r['cores'])): r for r in csv.DictReader(open(RESULTS_DIR / 'summary.csv'))}
    with open(OUT / 'ablation.csv', 'w', newline='', encoding='utf-8') as handle:
        w = csv.writer(handle)
        w.writerow(['case', 'cores', 'no_warm_makespan', 'no_warm_added', 'no_warm_initial',
                    'main_makespan', 'p1plan_makespan', 'singlecore_makespan'])
        for case, k, ms, add, init in sorted(rows):
            m = main_rows[(case, k)]
            w.writerow([case, k, ms, add, init, m['makespan'], m['p1plan_makespan'], m['singlecore_makespan']])


if __name__ == '__main__':
    main()
