"""输入一致性与“未改动”核验。

1. 官方数据包：数据包/ 与 A题数据包.zip、第一版三份 official/ 逐文件 SHA-256 比对（评估器、config、100 个算例）。
2. 第一版发布包：按 MANIFEST.json 逐文件核对字节数与 SHA-256，并检查有无多出的文件。
3. 第二版 solution_p1/p2、results_p1/p2：与本次对比开始时的快照（integrity/protected_before.txt）比对。

    .venv/bin/python 对比_V1vsV2/check_integrity.py [--snapshot]
"""
import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PKG = ROOT / '数据包'
V1PKG = ROOT / '第一版_V1仓库' / '第一版解压' / 'V1_Q1_Q2_Q3_20260926'
SNAP = HERE / 'integrity' / 'protected_before.txt'
PROTECTED = ['solution_p1', 'solution_p2', 'results_p1', 'results_p2']


def h(b):
    return hashlib.sha256(b).hexdigest()


def tree(base, sub=('code', 'data', 'docs')):
    out = {}
    for s in sub:
        for p in sorted((base / s).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:
                out[p.relative_to(base).as_posix()] = h(p.read_bytes())
    return out


def protected_hashes():
    out = {}
    for d in PROTECTED:
        for p in sorted((ROOT / d).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:
                out[p.relative_to(ROOT).as_posix()] = h(p.read_bytes())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--snapshot', action='store_true', help='写入第二版受保护目录的快照（仅在对比开始时运行一次）')
    a = ap.parse_args()
    if a.snapshot:
        SNAP.parent.mkdir(exist_ok=True)
        SNAP.write_text('\n'.join('{}  {}'.format(v, k) for k, v in protected_hashes().items()) + '\n',
                        encoding='utf-8')
        print('snapshot written', SNAP)
        return
    report = {}
    off = tree(PKG)
    zf = zipfile.ZipFile(ROOT / 'A题数据包.zip')
    zip_files = {}
    for n in zf.namelist():
        if n.endswith('/') or '__pycache__' in n or '/' not in n:
            continue
        rel = n.split('/', 1)[1] if n.split('/', 1)[0] not in ('code', 'data', 'docs') else n
        if rel.split('/')[0] in ('code', 'data', 'docs'):
            zip_files[rel] = h(zf.read(n))
    report['official_files'] = len(off)
    report['zip_equals_数据包'] = zip_files == off
    for p in (1, 2, 3):
        report['v1_problem{}_official_equals_数据包'.format(p)] = tree(V1PKG / 'output' / 'v1_problem{}'.format(p) / 'official') == off
    key = {k: v for k, v in off.items() if k.startswith('code/') or k == 'data/config.txt'}
    report['evaluator_and_config_sha256'] = key
    cat = hashlib.sha256()
    for k in sorted(off):
        if k.startswith('data/case_'):
            cat.update((PKG / k).read_bytes())
    report['cases_concat_sha256'] = cat.hexdigest()
    # 第一版发布包
    m = json.loads((V1PKG / 'MANIFEST.json').read_text(encoding='utf-8'))
    listed = {f['path']: f for f in m['files']}
    bad = [p for p, f in listed.items()
           if not (V1PKG / p).is_file() or (V1PKG / p).stat().st_size != f['bytes']
           or h((V1PKG / p).read_bytes()) != f['sha256']]
    actual = {p.relative_to(V1PKG).as_posix() for p in V1PKG.rglob('*') if p.is_file()}
    extra = sorted(actual - set(listed) - {'MANIFEST.json'})
    report['v1_manifest_files'] = len(listed)
    report['v1_manifest_mismatch'] = bad[:20]
    report['v1_extra_files'] = extra[:20]
    report['v1_zip_sha256'] = h((ROOT / '第一版_V1仓库' / 'v1.zip').read_bytes())
    # 第二版受保护目录
    before = {}
    for line in SNAP.read_text(encoding='utf-8').splitlines():
        if line.strip():
            v, k = line.split('  ', 1)
            before[k] = v
    now = protected_hashes()
    report['v2_protected_files'] = len(now)
    report['v2_protected_unchanged'] = now == before
    report['v2_protected_diff'] = sorted(set(now.items()) ^ set(before.items()))[:20]
    out = HERE / 'integrity' / 'integrity_report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'evaluator_and_config_sha256'}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
