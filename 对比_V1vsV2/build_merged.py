"""逐算例取优合并集：对每个 (问题, 算例, K) 在 {第二版, 第一版主结果, 第一版 57 个补充候选} 中按
(Makespan, 新增搬运) 字典序取最好，再做核数单调性修补（K 核若慢于 K−1 核，沿用 K−1 核方案并补一个空核）。

输出 merged/p{P}/k{K}/<case>_multicore_res.json（提交格式）与 merged/merged_selection.csv。
每个输出方案都有官方评估记录：与已评估方案字节相同的直接复用其官方记录（按 SHA-256 匹配），
补空核产生的新方案调用官方评估器重新评估（标签 merged_pad_p{P}）。

    .venv/bin/python 对比_V1vsV2/build_merged.py
"""
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path

from compare import OUT, load_tag
from eval_official import CASES, CORES, EVAL_DIR, HERE, ROOT, run_one, sha256

MERGED = HERE / 'merged'


def key(rec):
    r = rec['result']
    return r['makespan'], r['data_movement_bytes']['added_copy_bytes']


def main():
    by_sha = {}
    for f in EVAL_DIR.glob('*/*.json'):
        rec = json.loads(f.read_text(encoding='utf-8'))
        if rec.get('valid') and rec.get('plan_sha256'):
            by_sha.setdefault((rec['problem'], rec['plan_sha256']), rec)
    rows = []
    for p in (1, 2, 3):
        cands = [('V2', load_tag('v2_p{}'.format(p))), ('V1_main', load_tag('v1_p{}'.format(p) if p != 2 else 'v1_p2')),
                 ('V1_supp57', load_tag('v1_supp_q{}'.format(p)))]
        single = {c: load_tag('single')[(c, 1)]['result']['makespan'] for c in CASES}
        chosen = defaultdict(dict)
        for c in CASES:
            for k in CORES:
                opts = [(key(t[(c, k)]), i, name, t[(c, k)]) for i, (name, t) in enumerate(cands)
                        if (c, k) in t and t[(c, k)].get('valid')]
                (kk, _, name, rec) = min(opts, key=lambda o: (o[0], o[1]))
                chosen[c][k] = (name, ROOT / rec['plan'], kk)
        for c in CASES:
            prev = None
            for k in CORES:
                name, plan_path, kk = chosen[c][k]
                plan = json.loads(plan_path.read_text(encoding='utf-8'))
                source = name
                if prev and prev[2][0] < kk[0]:  # K 核比 K−1 核慢：沿用 K−1 核方案，补空核
                    plan = json.loads(json.dumps(prev[3]))
                    plan['core_schedules'] = plan['core_schedules'] + [[]] * (k - len(plan['core_schedules']))
                    source = prev[0] + '+pad_from_K{}'.format(k - 1)
                out = MERGED / 'p{}'.format(p) / 'k{}'.format(k) / (c + '_multicore_res.json')
                out.parent.mkdir(parents=True, exist_ok=True)
                if source == name:
                    shutil.copyfile(plan_path, out)
                else:
                    out.write_text(json.dumps(plan, separators=(',', ':')), encoding='utf-8')
                rec = by_sha.get((p, sha256(out)))
                if rec is None:
                    rec = run_one('merged_pad_p{}'.format(p), p, c, k, out)
                assert rec.get('valid'), (p, c, k, rec.get('error'))
                kk2 = key(rec)
                rows.append({'problem': p, 'case': c, 'cores': k, 'source': source, 'makespan': kk2[0],
                             'added_copy_bytes': kk2[1], 'speedup': single[c] / kk2[0],
                             'official_record': rec['tag'], 'plan_sha256': sha256(out)})
                prev = (source.split('+')[0], plan, kk2, plan)
    with open(MERGED / 'merged_selection.csv', 'w', newline='', encoding='utf-8-sig') as h:
        w = csv.DictWriter(h, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    summ = []
    for p in (1, 2, 3):
        for k in CORES:
            r = [x for x in rows if x['problem'] == p and x['cores'] == k]
            src = defaultdict(int)
            for x in r:
                src[x['source']] += 1
            summ.append({'problem': p, 'cores': k, 'mean_speedup': sum(x['speedup'] for x in r) / len(r),
                         'mean_added_bytes': sum(x['added_copy_bytes'] for x in r) / len(r),
                         'from_V2': src.get('V2', 0), 'from_V1_main': src.get('V1_main', 0),
                         'from_V1_supp57': src.get('V1_supp57', 0),
                         'padded': sum(v for s, v in src.items() if '+pad' in s)})
    with open(MERGED / 'merged_summary_by_k.csv', 'w', newline='', encoding='utf-8-sig') as h:
        w = csv.DictWriter(h, fieldnames=list(summ[0]))
        w.writeheader()
        w.writerows(summ)
    for s in summ:
        print(s)


if __name__ == '__main__':
    main()
