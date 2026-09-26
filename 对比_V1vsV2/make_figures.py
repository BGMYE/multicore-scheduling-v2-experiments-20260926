"""由 tables/ 生成对比图（需要 matplotlib；本机用独立 venv 运行，不改动项目 .venv）。

    <带 matplotlib 的 python> 对比_V1vsV2/make_figures.py
"""
import csv
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
TAB, FIG = HERE / 'tables', HERE / 'figures'
C_V2, C_V1, C_MERGE = '#2a78d6', '#eb6834', '#1baf7a'
INK, INK2, GRID = '#0b0b0b', '#52514e', '#e4e3df'
plt.rcParams.update({'font.sans-serif': ['Noto Sans CJK SC', 'WenQuanYi Zen Hei', 'SimHei', 'DejaVu Sans'],
                     'axes.unicode_minus': False, 'axes.edgecolor': INK2, 'axes.labelcolor': INK,
                     'xtick.color': INK2, 'ytick.color': INK2, 'axes.spines.top': False,
                     'axes.spines.right': False, 'font.size': 10})
TITLES = {1: '问题一（场景 A）', 2: '问题二（场景 B）', 3: '问题三（场景 B + L2 Cache）'}


def rd(path):
    with open(path, encoding='utf-8-sig') as h:
        return list(csv.DictReader(h))


def problems():
    return [p for p in (1, 2, 3) if (TAB / 'per_case_p{}.csv'.format(p)).exists()]


def speedup_by_k():
    s = rd(TAB / 'summary_by_k.csv')
    ps = problems()
    fig, axes = plt.subplots(1, len(ps), figsize=(4.2 * len(ps), 3.8), sharey=True)
    for ax, p in zip(axes, ps):
        r = sorted([x for x in s if int(x['problem']) == p], key=lambda x: int(x['cores']))
        ks = [1] + [int(x['cores']) for x in r]
        for key, col, lab, ls in (('v2_mean_speedup', C_V2, '第二版', '-'), ('v1_mean_speedup', C_V1, '第一版', '-'),
                                  ('merged_mean_speedup', C_MERGE, '逐算例取优', '--')):
            ys = [1.0] + [float(x[key]) for x in r]
            ax.plot(ks, ys, ls, color=col, lw=2, marker='o', ms=5, label=lab, zorder=3 if key != 'merged_mean_speedup' else 2)
            if key != 'merged_mean_speedup':
                ax.annotate('{:.2f}'.format(ys[-1]), (ks[-1], ys[-1]), xytext=(6, 0), textcoords='offset points',
                            va='center', color=INK, fontsize=9)
        ax.plot(ks, ks, ':', color=INK2, lw=1, label='线性加速')
        ax.set_title(TITLES[p], color=INK, fontsize=11)
        ax.set_xticks(ks)
        ax.set_xlabel('核数 K')
        ax.grid(axis='y', color=GRID, lw=0.8)
        ax.set_xlim(0.8, 5.6)
    axes[0].set_ylabel('平均加速比（逐算例 T1/TK 的算术平均）')
    axes[0].legend(frameon=False, loc='upper left', fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG / 'speedup_by_k.png', dpi=160)
    fig.savefig(FIG / 'speedup_by_k.svg')
    plt.close(fig)


def scatter():
    ps = problems()
    fig, axes = plt.subplots(1, len(ps), figsize=(4.2 * len(ps), 4.0))
    for ax, p in zip(axes, ps):
        r = [x for x in rd(TAB / 'per_case_p{}.csv'.format(p)) if x.get('winner')]
        x = [float(v['v1_speedup']) for v in r]
        y = [float(v['v2_speedup']) for v in r]
        ax.scatter(x, y, s=16, color=C_V2, alpha=0.55, edgecolors='white', linewidths=0.4)
        hi = max(x + y) * 1.05
        ax.plot([0, hi], [0, hi], color=INK2, lw=1, ls=':')
        w2 = sum(v['winner'] == 'V2' for v in r)
        w1 = sum(v['winner'] == 'V1' for v in r)
        t = len(r) - w1 - w2
        ax.text(0.97, 0.04, '对角线上方 = 第二版更快\n第二版胜 {} / 平 {} / 负 {}'.format(w2, t, w1),
                transform=ax.transAxes, va='bottom', ha='right', fontsize=9, color=INK)
        ax.set_xlim(0, hi)
        ax.set_ylim(0, hi)
        ax.set_aspect('equal')
        ax.set_xlabel('第一版加速比')
        ax.set_ylabel('第二版加速比')
        ax.set_title('{}：{} 个 (算例, K)'.format(TITLES[p], len(r)), color=INK, fontsize=11)
        ax.grid(color=GRID, lw=0.8)
    fig.tight_layout()
    fig.savefig(FIG / 'per_case_scatter.png', dpi=160)
    plt.close(fig)


def ratio_curve():
    ps = problems()
    fig, ax = plt.subplots(figsize=(7, 3.8))
    cols = ['#4a3aa7', '#e87ba4', '#008300']  # 按问题区分，避免与“第一版/第二版”配色混淆
    for p, col in zip(ps, cols):
        r = [float(x['makespan_ratio_v1_over_v2']) for x in rd(TAB / 'per_case_p{}.csv'.format(p)) if x.get('winner')]
        r.sort()
        ax.plot([100 * (i + 0.5) / len(r) for i in range(len(r))], r, color=col, lw=2, label=TITLES[p])
    ax.axhline(1, color=INK2, lw=1, ls=':')
    ax.set_yscale('log')
    ticks = [0.6, 0.8, 1, 1.25, 1.5, 2, 3, 4]
    ax.set_yticks(ticks)
    ax.set_yticklabels([str(t) for t in ticks])
    ax.minorticks_off()
    ax.set_xlabel('(算例, K) 分位 / %')
    ax.set_ylabel('第一版 Makespan ÷ 第二版 Makespan（对数轴）')
    ax.set_title('逐配置 Makespan 比值：大于 1 表示第二版更快', color=INK, fontsize=11)
    ax.grid(color=GRID, lw=0.8)
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG / 'makespan_ratio_sorted.png', dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    FIG.mkdir(exist_ok=True)
    speedup_by_k()
    scatter()
    ratio_curve()
    print('figures ->', FIG)
