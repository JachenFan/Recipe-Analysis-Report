# -*- coding: utf-8 -*-
"""
CVD 工艺全程分析 v3（需求3）
========================================================
需求3：此前只有《需求2》点名的步（26/27/30/31/32-34/35）做了细致处理，
       其余步（run1 升温段、run2 全程）统一用"端点温差÷时长"，与曲线实际形态不符。
       现改为【自动判别曲线类型 → 选择对应分析方法】，对两个 recipe 全部重算。

三种基本类型（+ 两种退化类型）：
  W 波浪式      : 围绕某一水平反复起落，净变化远小于累计行程
                  → 按升降分段，分别给出 上升速率 / 下降速率 / 波动幅度
  M 多段线性式  : 同一大方向但斜率明显改变（如先快后慢）
                  → 分段最小二乘，每段独立给速率；推荐速率取"温变贡献最大段"
  L 线性+恒温   : 前期变化、后期进入恒温带并维持到步末
                  → 只取"初始温度→恒温起点"区间拟合；另报恒温温度与偏离设定值
  S 单一线性    : 全程近似一条直线 → 整体最小二乘
  F 全程恒温/停滞: 整段几乎无变化 → 速率≈0

判别流程：
  ① 清洗（主控温度≤0 剔除；T1 量程饱和冻结点剔除）
  ② ~1min 平滑 → 等时重采样至 ≤300 点（消除采样密度差异、加速）
  ③ 平台(plateau)检测：从 i 到步末 温度极差≤恒温带 且 漂移斜率≤0.02℃/min 且 剩余时长≥15%
  ④ 无平台时：贪心分段线性（RMSE 下降 ≥30%、段长 ≥8%、相邻斜率相对差 ≥50%）最多 5 段
  ⑤ 按分段数 / 方向反转次数 / |净变化|÷累计行程 判型

新增数据质量修复（需求7 已改口径）：
  T1 热电偶量程上限 1100.89℃。原做法是"主控温度取 T1 时若冻结在 1100.89 则剔除该点"，
  相当于绕过假平台；但这会丢信息，且降温回落段仍有偏差。
  《需求7》改为直接拼接：T1 < 1100℃ 取 T1，T1 ≥ 1100℃ 取 T2（见 temp_fix.log_temp），
  原主控值保留在『主控温度_原始』列。原冻结判据保留为防御性代码（修正后命中数应为 0）。
========================================================
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from temp_fix import apply_to_frame, T_SWITCH, T_CONSIST, diag as t_diag, diag_text as t_diag_text  # noqa: E402
from batch_notes import ops_text  # noqa: E402  人为操作记录（现场确认，单一事实来源）
from recipe_meta import missing_steps, missing_text  # noqa: E402  缺失步检测（不得静默丢失）
from seg_terms import terms  # noqa: E402  分段类型领域话术（单一事实来源）
import phases  # noqa: E402  阶段划分（涂层前/中/后，单一事实来源）

warnings.filterwarnings("ignore")
rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
rcParams["axes.unicode_minus"] = False

import project_paths as pp

BASE = pp.ROOT
SIMU = pp.code()
OUT = pp.out("需求3_工艺全程分析")
FIG = os.path.join(OUT, "图表")
os.makedirs(FIG, exist_ok=True)

TCOL, PCOL = "主控温度", "电源总功率反馈"
TEMP_INFO = {}           # 需求7 温度修正诊断信息（按 tag 记录，供说明页引用）
T1SAT = 1100.89          # T1 热电偶量程上限（饱和值）
SAT_GAP = 3.0            # T2 高于主控该值 → 判定为冻结失效点
SAT_TOL = 0.5            # 主控≈T1 且 T1≈量程上限 的容差(℃)
BAND_ABS = 1.0           # 恒温带绝对下限(℃)
BAND_REL = 0.12          # 恒温带 = max(BAND_ABS, BAND_REL×本步总极差)  ← 相对化，避免把波浪当恒温
TAIL_SLOPE = 0.02        # 平台尾部漂移斜率绝对上限(℃/min)
TAIL_REL = 0.10          # 平台尾部漂移斜率 ≤ 该比例 × 前段斜率 → 才算"停住"
HOLD_FRAC = 0.15         # 平台至少覆盖步长的比例
HOLD_PTS = 0.10          # 平台至少覆盖的采样比例
FLAT_FRAC = 0.10         # 平台起点早于该比例 → 判为"全程恒温"
FLAT_AMP = 1.0           # 整步极差低于此值 → 全程恒温
NET_MIN = 2.0            # 有效净温变阈值(℃)
WAVE_NET = 0.40          # 波浪判据：|净变化| / 累计行程
WAVE_AMP = 1.5           # 波浪判据：整步极差下限(℃)
TURN_C = 0.5             # 波浪转折点最小幅值(℃)
SLOPE_ZERO = 0.05        # 视为"平段"的斜率阈值(℃/min)
NDS = 300                # 等时重采样点数
MAXSEG = 5               # 最多分段数
SEG_FRAC = 0.08          # 分段最短长度(占步长)
RMSE_RATIO = 0.70        # 分段接受：新RMSE < 该比例 × 旧RMSE
SLP_ABS, SLP_REL = 0.15, 0.50   # 分段接受：相邻斜率差 ≥ max(0.15℃, 50%×较大者)
MIN_N, MIN_DUR = 8, 3.0         # 绘图最小样本/时长

RUNS = [
    # 文件命名已规范化为 {批次}-recipe.xlsx / {批次}-log.xlsx；run5 短名规范为 29C261011
    ("run1", "29C260916", "29C260916-log",
     "29C260916-recipe.xlsx", "29C260815"),
    ("run2", "29C260926", "29C260926-log",
     "29C260926-recipe.xlsx", "29C260926"),
    ("run3", "29C260821", "29C260821-log",
     "29C260821-recipe.xlsx", "29C260815"),
    ("run4", "29C260903", "29C260903-log",
     "29C260903-recipe.xlsx", "29C260815"),
    ("run5", "29C261011", "29C261011-log",
     "29C261011-recipe.xlsx", "29C261010"),
    ("run6", "29C261016", "29C261016-log",
     "29C261016-recipe.xlsx", "29C260815"),
]
C_TEMP, C_TGT, C_SET, C_PWR, C_FIT = "#d9480f", "#868e96", "#7048e8", "#1c7ed6", "#2f9e44"
# 需求7（2026-10-20 修订）：三个温度都要在图中 → T1/T2 原始双测温配色与 analyze_v5.COL 保持一致
C_T1, C_T2 = "#15aabf", "#9c36b5"


def _plot_t1t2(ax, el, sub, lw_sub=0.8):
    """需求7：把 T1温度 / T2温度 叠到「主控温度」曲线上（主控由调用方绘制）。

    与 analyze_v5.plot_three_temps 同一口径：T2 低温段读数为 0（高位热电偶未起测）时按 0 屏蔽，
    避免把纵轴压到 0；T1/T2 画在主线之上（zorder 更高），即使与主控等值也能看到虚线。
    """
    el = np.asarray(el, float)
    for col, c, ls, lw in (("T1温度", C_T1, "--", lw_sub), ("T2温度", C_T2, ":", lw_sub + 0.05)):
        if col not in sub.columns:
            continue
        y = pd.to_numeric(sub[col], errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(y) & (y > 0)
        if ok.any():
            ax.plot(el[ok], y[ok], color=c, lw=lw, ls=ls, alpha=0.92, label=col, zorder=6)

# 领域话术：温度域平台叫「恒温」，压力·真空域同位置叫「恒压」（同模型、不同话术）——见 seg_terms.py
_T = terms("T")
T_WAVE, T_MULTI, T_LINHOLD, T_LIN, T_FLAT, T_SHORT = (
    _T["wave"], _T["multi"], _T["lin_hold"], _T["lin"], _T["flat"], _T["short"])

RATE_MEAN = {
    T_LIN: "整体最小二乘斜率",
    T_MULTI: "温变最大段(主段)斜率",
    T_WAVE: "波浪：下降段/上升段加权速率",
    T_LINHOLD: "初始温度→恒温点 的弦速率",
    T_FLAT: "0（无有效温变）",
    T_SHORT: "数据不足，不计算",
}


# ---------------------------------------------------------------- 数据
def load_process(tag):
    df = pd.read_csv(os.path.join(SIMU, f"_{tag}_raw.csv"))
    df["时间"] = pd.to_datetime(df["时间"])
    df = df.dropna(subset=["当前运行步号"]).sort_values("时间").reset_index(drop=True)
    df["step"] = df["当前运行步号"].astype(int)
    st = df["step"].to_numpy()
    newseg = np.concatenate([[True], np.diff(st) < 0])
    df["seg"] = np.cumsum(newseg)
    seg_ids = df["seg"].unique()
    main_seg = seg_ids[0]
    for sid in seg_ids:
        if 25 in set(df.loc[df["seg"] == sid, "step"]):
            main_seg = sid
            break
    proc = df[df["seg"] <= main_seg].reset_index(drop=True)
    dropped = int((df["seg"] > main_seg).sum())
    passes = np.ones(len(proc), int)
    cur, prev_sid = {}, None
    for i, (sid, s) in enumerate(zip(proc["seg"].to_numpy(), proc["step"].to_numpy())):
        if s not in cur:
            cur[s] = 1
        elif prev_sid is not None and sid != prev_sid:
            cur[s] += 1
        passes[i] = cur[s]
        prev_sid = sid
    proc["pass"] = passes

    # ---- 需求7：log 温度口径修正 ----
    # T1 < 1100℃ 用 T1；T1 >= 1100℃（含饱和冻结）用 T2；T2 无效时回退 T1。
    # 原"主控温度"另存为"主控温度_原始"以便追溯。修正后 TCOL 列即新口径温度，
    # 因此 analyze() 里基于「主控≈T1 饱和」的冻结判据自然失效为 no-op（不再误剔有效点）。
    TEMP_INFO[tag] = apply_to_frame(proc)
    return proc, dropped


def recipe_setpoint(fname, sheet):
    p = os.path.join(BASE, fname)
    r = pd.read_excel(p, sheet_name=sheet, usecols=["步号", "目标温度"])
    return dict(zip(r["步号"].astype(int), r["目标温度"].astype(float)))


# ---------------------------------------------------------------- 算法
def smooth(y, dt):
    """约1min滑动平均；归一化卷积避免边缘零填充失真"""
    y = np.asarray(y, dtype=float)
    w = max(3, int(round(1.0 / max(dt, 1e-6))))
    w = min(w, len(y) if len(y) % 2 else len(y) - 1)
    if w < 3 or len(y) < 3:
        return y.copy()
    k = np.ones(w) / w
    out = np.convolve(y, k, mode="same")
    norm = np.convolve(np.ones(len(y)), k, mode="same")
    return out / norm


def segstats(x, y):
    """一元线性拟合：斜率、截距、RSS、R²"""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    m = len(x)
    if m < 2:
        return dict(slope=0.0, icpt=float(y[0]) if m else 0.0, rss=0.0, r2=float("nan"), n=m)
    sx, sy = x.sum(), y.sum()
    sxx, sxy, syy = (x * x).sum(), (x * y).sum(), (y * y).sum()
    den = m * sxx - sx * sx
    b = (m * sxy - sx * sy) / den if abs(den) > 1e-12 else 0.0
    a = (sy - b * sx) / m
    rss = float(max(syy - a * sy - b * sxy, 0.0))
    sst = syy - sy * sy / m
    r2 = float(1 - rss / sst) if sst > 1e-12 else float("nan")
    return dict(slope=float(b), icpt=float(a), rss=rss, r2=r2, n=m)


def zigzag(y, thr):
    """转折点检测：返回单调段端点下标序列"""
    piv, pv, pi, trend = [0], y[0], 0, 0
    for i in range(1, len(y)):
        if trend >= 0:
            if y[i] > pv:
                pv, pi = y[i], i
            elif pv - y[i] >= thr:
                piv.append(pi); trend = -1; pv, pi = y[i], i
        else:
            if y[i] < pv:
                pv, pi = y[i], i
            elif y[i] - pv >= thr:
                piv.append(pi); trend = 1; pv, pi = y[i], i
    piv.append(len(y) - 1)
    out = []
    for p in piv:
        if not out or p > out[-1]:
            out.append(p)
    return out


def find_plateau(x, y, band, min_frac=HOLD_FRAC, min_pts=HOLD_PTS):
    """平台起点 = 满足『从该点到步末 极差≤band 且 漂移斜率≤TAIL_SLOPE』的最早下标
    后缀极差单调递减，配合后缀前缀和可 O(n) 向量化"""
    n = len(y)
    if n < 5:
        return None
    suf_max = np.maximum.accumulate(y[::-1])[::-1]
    suf_min = np.minimum.accumulate(y[::-1])[::-1]
    pp = suf_max - suf_min
    dur = x[-1] - x[0]
    ok = (pp <= band)
    if dur > 0:
        ok &= ((x[-1] - x) >= min_frac * dur)
    ok &= ((n - np.arange(n)) >= max(5, min_pts * n))
    # 尾部漂移斜率：用后缀前缀和 O(n)
    csx = np.concatenate([[0.0], np.cumsum(x)])
    csy = np.concatenate([[0.0], np.cumsum(y)])
    csxx = np.concatenate([[0.0], np.cumsum(x * x)])
    csxy = np.concatenate([[0.0], np.cumsum(x * y)])
    m = (n - np.arange(n)).astype(float)
    sx, sy = csx[n] - csx[:n], csy[n] - csy[:n]
    sxx, sxy = csxx[n] - csxx[:n], csxy[n] - csxy[:n]
    den = m * sxx - sx * sx
    d1 = np.where(np.abs(den) > 1e-12, den, 1.0)
    s_tail = np.where(np.abs(den) > 1e-12, (m * sxy - sx * sy) / d1, 0.0)
    # 前段 [0,i) 的斜率：恒温 = 尾部速率降到前段的 TAIL_REL 以下
    mp = np.arange(n).astype(float)
    px, py = csx[:n], csy[:n]
    pxx, pxy = csxx[:n], csxy[:n]
    dp = mp * pxx - px * px
    d2 = np.where(np.abs(dp) > 1e-12, dp, 1.0)
    s_pre = np.where(np.abs(dp) > 1e-12, (mp * pxy - px * py) / d2, 0.0)
    lim = np.minimum(TAIL_SLOPE, TAIL_REL * np.abs(s_pre))
    ok &= (np.abs(s_tail) <= lim) & (mp >= 5)
    cand = np.where(ok)[0]
    cand = cand[cand >= 1]
    return int(cand[0]) if len(cand) else None


def greedy_split(x, y, maxseg=MAXSEG):
    """贪心分段线性：每轮选使 RSS 下降最多且通过显著性门槛的切点"""
    n = len(x)
    dur = x[-1] - x[0]
    min_pts = max(5, int(SEG_FRAC * n))
    min_dur = SEG_FRAC * dur if dur > 0 else 0.0
    bounds = [0, n]
    while len(bounds) - 1 < maxseg:
        best = None
        for k in range(len(bounds) - 1):
            a, b = bounds[k], bounds[k + 1]
            if b - a < 2 * min_pts:
                continue
            s0 = segstats(x[a:b], y[a:b])
            rmse0 = np.sqrt(s0["rss"] / (b - a))
            if rmse0 <= 1e-9:
                continue
            for m in range(a + min_pts, b - min_pts + 1):
                if dur > 0 and ((x[m] - x[a]) < min_dur or (x[b - 1] - x[m]) < min_dur):
                    continue
                s1 = segstats(x[a:m], y[a:m])
                s2 = segstats(x[m:b], y[m:b])
                rmse1 = np.sqrt((s1["rss"] + s2["rss"]) / (b - a))
                if rmse1 >= RMSE_RATIO * rmse0:
                    continue
                if abs(s1["slope"] - s2["slope"]) < max(
                        SLP_ABS, SLP_REL * max(abs(s1["slope"]), abs(s2["slope"]))):
                    continue
                rss = s1["rss"] + s2["rss"]
                if best is None or rss < best[0]:
                    best = (rss, k, m)
        if best is None:
            break
        bounds.insert(best[1] + 1, best[2])
    return bounds


def refit_raw(el, temp, t_edges):
    """在原始数据上按时间边界重拟合各段（提高速率精度）"""
    out = []
    for a, b in zip(t_edges[:-1], t_edges[1:]):
        m = (el >= a - 1e-9) & (el <= b + 1e-9)
        if m.sum() < 3:
            m = (el >= a - 1e-9) & (el <= b + 1e-9)
            if m.sum() < 2:
                continue
        xa, ya = el[m], temp[m]
        s = segstats(xa - xa[0], ya)
        out.append(dict(t0=float(xa[0]), t1=float(xa[-1]), slope=s["slope"], icpt=s["icpt"],
                        r2=s["r2"], rss=s["rss"], n=int(m.sum()),
                        dT=float(s["slope"] * (xa[-1] - xa[0]))))
    return out


def piecewise_r2(segs, el, temp):
    """分段整体拟合 R²（比各段 R² 的平均更能反映分段效果）"""
    rss = sum(s["rss"] for s in segs)
    yy = np.asarray(temp, float)
    sst = float(np.sum((yy - yy.mean()) ** 2))
    return float(1 - rss / sst) if sst > 1e-12 else float("nan")


def analyze(el_raw, temp_raw, t1_raw, t2_raw, tsp, dt, phase):
    """返回类型 + 分段明细 + 推荐速率"""
    temp_raw = np.asarray(temp_raw, float)
    t1_raw = np.asarray(t1_raw, float)
    t2_raw = np.asarray(t2_raw, float)
    el_raw = np.asarray(el_raw, float)
    # --- 清洗 1：传感器跳零 ---
    ok = temp_raw > 0
    n_zero = int((~ok).sum())
    # --- 清洗 2：T1 量程饱和冻结（主控取 T1、T1 卡在量程上限，而 T2 仍在上升）---
    frozen = (ok & (np.abs(temp_raw - t1_raw) < SAT_TOL)
              & (np.abs(t1_raw - T1SAT) < SAT_TOL) & ((t2_raw - temp_raw) > SAT_GAP))
    n_frozen = int(frozen.sum())
    ok = ok & ~frozen
    el, temp = el_raw[ok], temp_raw[ok]
    n = len(el)
    res = dict(type=T_SHORT, segs=[], n_seg=0, rec=float("nan"), endpoint=float("nan"),
               rise=float("nan"), fall=float("nan"), amp=float("nan"), hold_t=None,
               hold_T=float("nan"), hold_dev=float("nan"), r2=float("nan"),
               n_zero=n_zero, n_frozen=n_frozen, n=0, dur=0.0, net=0.0,
               T0=float("nan"), T1=float("nan"))
    if n < 5:
        return res
    dur = float(el[-1] - el[0])
    res["n"], res["dur"] = n, dur
    res["T0"], res["T1"] = float(temp[0]), float(temp[-1])
    net = float(temp[-1] - temp[0])
    res["net"] = net
    res["endpoint"] = net / dur if dur > 0 else float("nan")
    res["amp"] = float(np.ptp(temp))
    if dur <= 0:
        res["type"] = T_FLAT
        res["rec"] = 0.0
        return res

    tsm = smooth(temp, dt)
    # --- 等时重采样 ---
    N = min(NDS, n)
    if dur > 0 and n > N:
        xg = np.linspace(el[0], el[-1], N)
        yg = np.interp(xg, el, tsm)
    else:
        xg, yg = el.copy(), tsm.copy()

    amp = res["amp"]
    band = max(BAND_ABS, BAND_REL * amp)   # 恒温带相对化：本步波动越大，允许的平台极差越大

    # --- ① 全程恒温（整步极差极小）---
    if amp <= FLAT_AMP:
        res["type"] = T_FLAT
        res["hold_t"] = 0.0
        res["hold_T"] = float(np.mean(temp))
        res["hold_dev"] = res["hold_T"] - tsp if tsp else float("nan")
        res["rec"] = 0.0
        res["n_seg"] = 1
        return res

    # --- ② 平台检测（前期变化 + 后期恒温）---
    pi = find_plateau(xg, yg, band)
    if pi is not None:
        hold_t = float(xg[pi] - xg[0])
        hold_T = float(np.mean(yg[pi:]))
        if hold_t <= FLAT_FRAC * dur or abs(hold_T - temp[0]) < NET_MIN:
            res["type"] = T_FLAT
            res["hold_t"] = hold_t
            res["hold_T"] = hold_T
            res["hold_dev"] = hold_T - tsp if tsp else float("nan")
            res["rec"] = 0.0
            res["n_seg"] = 1
            return res
        res["hold_t"] = hold_t
        res["hold_T"] = hold_T
        res["hold_dev"] = hold_T - tsp if tsp else float("nan")
        # 前期再做一次贪心分段（先快后慢再恒温之类的情形可拆出子段）
        sb = greedy_split(xg[:pi + 1], yg[:pi + 1])
        if len(sb) - 1 >= 2:
            edges = [float(xg[min(i, pi)]) for i in sb]
        else:
            edges = [float(xg[0]), float(xg[pi])]
        segs = refit_raw(el, temp, edges)
        res["type"] = T_LINHOLD
        res["segs"] = segs
        res["n_seg"] = len(segs)
        if segs:
            # 需求2 明确要求：降温速率取「初始温度 → 恒温点」的弦速率
            res["rec"] = (hold_T - float(temp[0])) / hold_t if hold_t > 0 else float("nan")
            res["r2"] = piecewise_r2(segs, el, temp)
            dn = [s for s in segs if s["slope"] < 0]
            up = [s for s in segs if s["slope"] > 0]
            res["fall"] = (sum(s["dT"] for s in dn) / sum(s["t1"] - s["t0"] for s in dn)) if dn else float("nan")
            res["rise"] = (sum(s["dT"] for s in up) / sum(s["t1"] - s["t0"] for s in up)) if up else float("nan")
        return res

    # --- ③ 波浪式（按转向点分段）---
    if amp >= WAVE_AMP:
        piv = zigzag(yg, TURN_C)
        if len(piv) - 1 >= 3:
            last_i = len(xg) - 1
            edges = [float(xg[min(p, last_i)]) for p in piv]
            wz = refit_raw(el, temp, edges)
            if len(wz) >= 3:
                path = sum(abs(s["dT"]) for s in wz)
                if path > 0 and abs(net) <= WAVE_NET * path:
                    dn = [s for s in wz if s["slope"] < 0]
                    up = [s for s in wz if s["slope"] > 0]
                    res["type"] = T_WAVE
                    res["segs"], res["n_seg"] = wz, len(wz)
                    if dn:
                        res["fall"] = sum(s["dT"] for s in dn) / sum(s["t1"] - s["t0"] for s in dn)
                    if up:
                        res["rise"] = sum(s["dT"] for s in up) / sum(s["t1"] - s["t0"] for s in up)
                    res["rec"] = res["fall"] if _cool_phase(phase) else res["rise"]
                    res["r2"] = piecewise_r2(wz, el, temp)
                    return res

    # --- ④ 贪心分段线性 ---
    bounds = greedy_split(xg, yg)
    last_i = len(xg) - 1
    edges = [float(xg[min(i, last_i)]) for i in bounds]
    segs = refit_raw(el, temp, edges)
    if len(segs) <= 1:
        s = segstats(el - el[0], temp)
        res["type"] = T_LIN
        res["segs"] = [dict(t0=float(el[0]), t1=float(el[-1]), slope=s["slope"], icpt=s["icpt"],
                            r2=s["r2"], rss=s["rss"], n=n, dT=float(net))]
        res["n_seg"] = 1
        res["rec"], res["r2"] = s["slope"], s["r2"]
        res["rise"] = s["slope"] if s["slope"] > 0 else float("nan")
        res["fall"] = s["slope"] if s["slope"] < 0 else float("nan")
        return res

    # 末段近水平 → 归为 线性+恒温
    last = segs[-1]
    if abs(last["slope"]) < SLOPE_ZERO and (last["t1"] - last["t0"]) >= 0.20 * dur:
        res["type"] = T_LINHOLD
        res["hold_t"] = float(last["t0"] - el[0])
        res["hold_T"] = float(np.mean(temp[el >= last["t0"]]))
        res["hold_dev"] = res["hold_T"] - tsp if tsp else float("nan")
        segs = segs[:-1]
        res["segs"] = segs
        res["n_seg"] = len(segs)
        if segs:
            tot = sum(s["dT"] for s in segs)
            tt = sum(s["t1"] - s["t0"] for s in segs)
            res["rec"] = tot / tt if tt > 0 else float("nan")
            res["r2"] = piecewise_r2(segs, el, temp)
            dn = [s for s in segs if s["slope"] < 0]
            up = [s for s in segs if s["slope"] > 0]
            res["fall"] = (sum(s["dT"] for s in dn) / sum(s["t1"] - s["t0"] for s in dn)) if dn else float("nan")
            res["rise"] = (sum(s["dT"] for s in up) / sum(s["t1"] - s["t0"] for s in up)) if up else float("nan")
        return res

    path = sum(abs(s["dT"]) for s in segs)
    dirs = [0 if abs(s["slope"]) < SLOPE_ZERO else (1 if s["slope"] > 0 else -1) for s in segs]
    nz = [d for d in dirs if d != 0]
    rev = sum(1 for i in range(1, len(nz)) if nz[i] != nz[i - 1])
    dn = [s for s in segs if s["slope"] < 0]
    up = [s for s in segs if s["slope"] > 0]
    if dn:
        res["fall"] = sum(s["dT"] for s in dn) / sum(s["t1"] - s["t0"] for s in dn)
    if up:
        res["rise"] = sum(s["dT"] for s in up) / sum(s["t1"] - s["t0"] for s in up)
    res["segs"], res["n_seg"] = segs, len(segs)

    if len(segs) >= 3 and rev >= 2 and path > 0 and abs(net) <= WAVE_NET * path:
        res["type"] = T_WAVE
        res["rec"] = res["fall"] if _cool_phase(phase) else res["rise"]
    else:
        res["type"] = T_MULTI
        dom = max(segs, key=lambda s: abs(s["dT"]))
        res["rec"] = dom["slope"]
        res["r2"] = piecewise_r2(segs, el, temp)
    return res


# ---------------------------------------------------------------- 绘图
# 阶段底色（阶段名 -> 颜色）；具体步号区间由实际步次生成，见 phase_bands()
PHASE_BG = {phases.PRE: "#ffe8cc", phases.HOLD: "#d0ebff", phases.POST: "#d3f9d8"}


def phase_of(s, hold=None):
    """步号 -> 阶段（涂层前/涂层中/涂层后）。口径见 `phases.py`。"""
    return phases.phase_of(s, hold)


def phase_bands(bounds):
    """[(阶段名, t0, t1, 底色)] —— 按实际步次起止时间生成，不写死步号区间。"""
    out = []
    for ph in phases.BASE:
        ts = [bounds[k] for k in bounds if phases.phase_of(k[0]) == ph]
        if ts:
            out.append((ph, min(a for a, _ in ts), max(b for _, b in ts), PHASE_BG[ph]))
    return out


def _cool_phase(phase):
    """该步所属阶段是否为"温度下降段"。

    兼容两种入参：新阶段基名（`涂层后`）与旧字面量（`降温`）——
    波浪式曲线的推荐速率要取"与本阶段方向一致"的那一段（fall/rise）。
    """
    return str(phase) in ("降温", phases.POST)


def plot_overview(tag, label, proc):
    fig, ax = plt.subplots(2, 1, figsize=(16, 9), sharex=True)
    fig.patch.set_facecolor("white")
    off, X, T, P, T1, T2 = 0.0, [], [], [], [], []
    bounds = {}
    for (s, p), sub in proc.groupby(["step", "pass"], sort=False):
        el = ((sub["时间"] - sub["时间"].iloc[0]).dt.total_seconds() / 60).to_numpy()
        k = max(1, len(el) // 4000)
        X.append(off + el[::k]); T.append(sub[TCOL].to_numpy()[::k]); P.append(sub[PCOL].to_numpy()[::k])
        T1.append(sub["T1温度"].to_numpy()[::k] if "T1温度" in sub else np.full(len(el[::k]), np.nan))
        T2.append(sub["T2温度"].to_numpy()[::k] if "T2温度" in sub else np.full(len(el[::k]), np.nan))
        bounds[(s, p)] = (off, off + (el[-1] if len(el) else 0))
        off += (el[-1] if len(el) else 0)
    X, T, P = np.concatenate(X), np.concatenate(T), np.concatenate(P)
    ax[0].plot(X, T, color=C_TEMP, lw=0.9, label="主控温度")
    # 需求7：T1/T2 原始双测温同时入图（0 值=高位偶未起测 → 屏蔽，避免压扁纵轴）
    for arr, c, ls, nm in ((np.concatenate(T1), C_T1, "--", "T1温度"),
                           (np.concatenate(T2), C_T2, ":", "T2温度")):
        y = pd.to_numeric(pd.Series(arr), errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(y) & (y > 0)
        if ok.any():
            ax[0].plot(X[ok], y[ok], color=c, lw=0.8, ls=ls, alpha=0.9, label=nm, zorder=5)
    ax[0].set_ylabel("温度 (℃)"); ax[0].legend(loc="upper right"); ax[0].grid(alpha=0.25)
    ax[1].plot(X, P, color=C_PWR, lw=0.7, label="电源总功率反馈")
    ax[1].set_ylabel("电源总功率 (kW)"); ax[1].legend(loc="upper right"); ax[1].grid(alpha=0.25)
    ax[1].set_xlabel("累计时间 (min，自首步起)")
    for name, x0, x1, c in phase_bands(bounds):
        for axx in ax:
            axx.axvspan(x0, x1, color=c, alpha=0.45, zorder=0)
        ax[0].text((x0 + x1) / 2, ax[0].get_ylim()[1], name,
                   ha="center", va="top", fontsize=12, color="#343a40", weight="bold")
    fig.suptitle(f"工艺全程总览  {label}（涂层前 / 涂层中 / 涂层后 三段）", fontsize=15)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    p = os.path.join(FIG, f"{tag}_全程总览.png")
    fig.savefig(p, dpi=140, facecolor="white"); plt.close(fig)
    return p


def plot_step_grid(tag, label, proc, res, sets, step_list, kind, fname, title):
    ncol = 4
    nrow = int(np.ceil(len(step_list) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.3 * ncol, 2.9 * nrow), constrained_layout=True)
    fig.patch.set_facecolor("white")
    axes = np.atleast_1d(axes).ravel()
    for i, key in enumerate(step_list):
        ax = axes[i]
        s, p = key
        sub = proc[(proc["step"] == s) & (proc["pass"] == p)].reset_index(drop=True)
        r = res[key]
        el = ((sub["时间"] - sub["时间"].iloc[0]).dt.total_seconds() / 60).to_numpy()
        lbl = f"{s}" + (f"({p})" if p > 1 else "")
        if len(sub) < MIN_N or r["dur"] < MIN_DUR:
            ax.axis("off")
            ax.text(0.5, 0.5, f"步号 {lbl}\n时长 {r['dur']:.1f} min\n记录 {len(sub)} 条\n"
                              f"→ 采样点过少，跳过绘图\n（详见 Excel 文字说明）",
                    ha="center", va="center", fontsize=9, color="#868e96",
                    bbox=dict(boxstyle="round", fc="#f8f9fa", ec="#ced4da"))
            continue
        if kind == "temp":
            tempv = sub[TCOL].astype(float).to_numpy()
            okt = tempv > 0
            ax.plot(el[okt], tempv[okt], color=C_TEMP, lw=1.0, label="主控温度")
            _plot_t1t2(ax, el, sub, lw_sub=0.65)   # 需求7：T1/T2 同图
            ax.plot(el, sub["目标温度"], color=C_TGT, lw=0.8, ls="--", label="目标温度(log)")
            logt = float(sub["目标温度"].iloc[0])
            rs = sets.get(s)
            show_rs = rs is not None and rs > 0 and abs(rs - logt) > 3
            ty = r["type"]
            # ---- 类型化叠加 ----
            if ty in (T_WAVE, T_MULTI):
                for k, sg in enumerate(r["segs"], 1):
                    xs = np.array([sg["t0"], sg["t1"]])
                    ys = sg["icpt"] + sg["slope"] * (xs - sg["t0"])
                    ax.plot(xs, ys, color=C_FIT, lw=1.8, zorder=5)
                    ax.annotate(f"#{k} {sg['slope']:+.3f}",
                                ((sg["t0"] + sg["t1"]) / 2, ys.mean()),
                                fontsize=6.5, color="#1864ab", ha="center",
                                bbox=dict(boxstyle="round,pad=0.12", fc="#ffffffcc", ec="none"))
                if ty == T_WAVE:
                    for sg in r["segs"]:
                        if sg["slope"] < 0:
                            ax.axvspan(sg["t0"], sg["t1"], color="#ffe3e3", alpha=0.55, zorder=0)
            elif ty == T_LINHOLD:
                if r["segs"]:
                    sg = r["segs"][0]
                    xs = np.array([sg["t0"], sg["t1"]])
                    ys = sg["icpt"] + sg["slope"] * (xs - sg["t0"])
                    ax.plot(xs, ys, color=C_FIT, lw=1.8, zorder=5)
                    ax.annotate(f"{sg['slope']:+.3f}℃/min", (xs.mean(), ys.mean()),
                                fontsize=6.5, color="#1864ab", ha="center",
                                bbox=dict(boxstyle="round,pad=0.12", fc="#ffffffcc", ec="none"))
                if r["hold_t"] is not None:
                    ax.axvspan(r["hold_t"], el[-1], color="#fff3bf", alpha=0.55, zorder=0)
                    ax.axvline(r["hold_t"], color="#7048e8", ls="--", lw=1.0)
                    ax.text(r["hold_t"], ax.get_ylim()[0], f" {r['hold_t']:.0f}min 恒温",
                            color="#7048e8", fontsize=7, va="bottom", rotation=90)
            elif ty == T_FLAT:
                ax.axhline(np.nanmean(tempv[okt]), color=C_FIT, lw=1.4, ls="-.")
            elif ty == T_LIN and r["segs"]:
                sg = r["segs"][0]
                xs = np.array([sg["t0"], sg["t1"]])
                ax.plot(xs, sg["icpt"] + sg["slope"] * (xs - sg["t0"]),
                        color=C_FIT, lw=1.6, ls="--", zorder=5)
            if show_rs:
                ax.axhline(rs, color=C_SET, ls=":", lw=1.4, label=f"设定值(recipe) {rs:.0f}℃")
            vals = [float(np.min(tempv[okt])), float(np.max(tempv[okt]))]
            if show_rs:
                vals.append(float(rs))
            lo, hi = min(vals), max(vals)
            if hi - lo < 1.0:
                lo, hi = lo - 0.5, hi + 0.5
            pad = max(0.8, (hi - lo) * 0.10)
            ax.set_ylim(lo - pad, hi + pad)
            ax.legend(fontsize=6, loc="best")
            if ty == T_WAVE:
                tt = (f"上升{r['rise']:+.2f} / 下降{r['fall']:+.2f} ℃/min\n"
                      f"幅度{r['amp']:.2f}℃  净{r['net']:+.2f}℃")
            elif ty == T_MULTI:
                tt = f"{r['n_seg']}段  主段{r['rec']:+.3f} ℃/min"
            elif ty == T_LINHOLD:
                tt = f"前期{r['rec']:+.3f} ℃/min  恒温{r['hold_T']:.1f}℃"
            elif ty == T_FLAT:
                tt = f"净变{r['net']:+.2f}℃  幅度{r['amp']:.2f}℃"
            else:
                tt = f"{r['rec']:+.3f} ℃/min  R²={r['r2']:.3f}" if r["r2"] == r["r2"] else f"{r['rec']:+.3f} ℃/min"
            ax.set_title(f"步号 {lbl} | {ty}\n{tt}", fontsize=8)
            ax.set_ylabel("温度 (℃)", fontsize=8)
        else:
            ax.plot(el, sub[PCOL], color=C_PWR, lw=0.9)
            ax.axhline(r["Pmin"], color="#2f9e44", ls=":", lw=0.9)
            ax.axhline(r["Pmax"], color="#e03131", ls=":", lw=0.9)
            ax.set_title(f"步号 {lbl} | 功率 min {r['Pmin']:.2f} / max {r['Pmax']:.2f} kW", fontsize=9)
            ax.set_ylabel("总功率 (kW)", fontsize=8)
        ax.set_xlabel("时间 (min)", fontsize=8)
        ax.tick_params(labelsize=7); ax.grid(alpha=0.25)
    for j in range(len(step_list), len(axes)):
        axes[j].set_visible(False)
    fig.suptitle(f"{title}  {label}", fontsize=14)
    p = os.path.join(FIG, fname)
    fig.savefig(p, dpi=140, facecolor="white"); plt.close(fig)
    return p


def plot_hold(tag, label, proc, key):
    s, p = key
    sub = proc[(proc["step"] == s) & (proc["pass"] == p)].reset_index(drop=True)
    el = ((sub["时间"] - sub["时间"].iloc[0]).dt.total_seconds() / 3600).to_numpy()
    temp = sub[TCOL].astype(float).to_numpy()
    ok = temp > 0
    el_all = el                      # 需求7：T1/T2 需与原始行对齐（下面 el 会被 ok 掩码）
    el, temp = el[ok], temp[ok]
    tsp = float(sub["目标温度"].iloc[0])
    pp = float(np.ptp(temp))
    fig, ax = plt.subplots(2, 1, figsize=(15, 7), sharex=True)
    fig.patch.set_facecolor("white")
    k = max(1, len(temp) // 8000)
    ax[0].plot(el[::k], temp[::k], color=C_TEMP, lw=0.7, label="主控温度")
    _plot_t1t2(ax[0], el_all, sub, lw_sub=0.6)   # 需求7：T1/T2 同图
    ax[0].axhline(tsp, color=C_TGT, ls="--", lw=1.0, label=f"目标温度 {tsp:.0f}℃")
    ax[0].axhspan(tsp - BAND_ABS, tsp + BAND_ABS, color="#ffd43b", alpha=0.15,
                  label=f"±{BAND_ABS}℃ 参考恒温带")
    ax[0].set_ylabel("主控温度 (℃)"); ax[0].legend(fontsize=9, loc="best"); ax[0].grid(alpha=0.25)
    ax[0].set_title(f"涂层中（步号 {s}）{label}   时长 {el[-1]:.1f} h   "
                    f"均值 {np.mean(temp):.2f}℃   极差 {pp:.2f}℃", fontsize=12)
    ax[1].plot(el[::k], sub[PCOL].to_numpy()[ok][::k], color=C_PWR, lw=0.7, label="电源总功率反馈")
    ax[1].set_ylabel("总功率 (kW)"); ax[1].set_xlabel("时间 (h)"); ax[1].legend(fontsize=9); ax[1].grid(alpha=0.25)
    fig.tight_layout()
    p = os.path.join(FIG, f"{tag}_恒温段_步{s}.png")
    fig.savefig(p, dpi=140, facecolor="white"); plt.close(fig)
    return p


def plot_rate_summary(tag, label, rows, phase):
    rows = [r for r in rows if r["rec"] == r["rec"]]
    if not rows:
        return None
    rows.sort(key=lambda r: r["order"])
    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(max(11, 0.55 * len(rows)), 5))
    fig.patch.set_facecolor("white")
    vals = [r["rec"] for r in rows]
    ax.bar(x, vals, color=[C_FIT if v < 0 else "#e8590c" for v in vals])
    ax.axhline(0, color="#495057", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{r['lbl']}\n{r['type']}" for r in rows], fontsize=7, rotation=45)
    ax.set_ylabel("速率 (℃/min)")
    ax.set_title(f"{phase} 各步速率（{label}，按曲线类型选用方法）  负=降温 / 正=升温", fontsize=13)
    for xi, v in zip(x, vals):
        ax.text(xi, v, f"{v:+.2f}", ha="center", va="bottom" if v >= 0 else "top", fontsize=7)
    ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    p = os.path.join(FIG, f"{tag}_{phase}速率.png")
    fig.savefig(p, dpi=140, facecolor="white"); plt.close(fig)
    return p


def plot_compare(stats, fname):
    """旧方法(端点差) vs 新方法(分型) 对比"""
    d = stats[(stats["曲线类型"] != T_SHORT) & stats["推荐速率"].notna() &
              stats["端点差速率"].notna() & (stats["时长_min"] >= MIN_DUR)].copy()
    if d.empty:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(max(15, 0.75 * len(d)), 5.5))
    fig.patch.set_facecolor("white")
    for ax, tag in zip(axes, ["run1", "run2"]):
        g = d[d["炉次"] == tag]
        if g.empty:
            ax.axis("off"); continue
        x = np.arange(len(g))
        w = 0.38
        ax.bar(x - w / 2, g["端点差速率"], w, color="#adb5bd", label="旧：端点温差÷时长")
        ax.bar(x + w / 2, g["推荐速率"], w, color="#1c7ed6", label="新：按曲线类型")
        ax.set_xticks(x)
        ax.set_xticklabels([f"{a}{b}" for a, b in zip(g["步号"], g["阶段"].str[0])], fontsize=7, rotation=60)
        ax.axhline(0, color="#495057", lw=0.8)
        ax.set_ylabel("速率 (℃/min)")
        ax.set_title(f"{tag}  两种算法速率对比", fontsize=12)
        ax.legend(fontsize=8); ax.grid(alpha=0.25, axis="y")
    fig.suptitle("需求3：自动分型方法 vs 原端点差方法", fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    p = os.path.join(FIG, fname)
    fig.savefig(p, dpi=140, facecolor="white"); plt.close(fig)
    return p


def plot_type_dist(stats, fname):
    order = [T_LIN, T_MULTI, T_WAVE, T_LINHOLD, T_FLAT, T_SHORT]
    types = [t for t in order if (stats["曲线类型"] == t).any()]
    runs = ["run1", "run2"]
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("white")
    w = 0.38
    x = np.arange(len(types))
    for i, tag in enumerate(runs):
        c = [((stats["炉次"] == tag) & (stats["曲线类型"] == t)).sum() for t in types]
        ax.bar(x + (i - 0.5) * w, c, w, label=tag)
        for xi, v in zip(x + (i - 0.5) * w, c):
            ax.text(xi, v, str(v), ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels(types, fontsize=9)
    ax.set_ylabel("步数")
    ax.set_title("自动判别结果：曲线类型分布", fontsize=13)
    ax.legend(); ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    p = os.path.join(FIG, fname)
    fig.savefig(p, dpi=140, facecolor="white"); plt.close(fig)
    return p


def fmt_xlsx(path, widths):
    from openpyxl import load_workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    wb = load_workbook(path)
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="495057")
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for i, wd in enumerate(widths.get(ws.title, []), start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = wd
    wb.save(path)


# ---------------------------------------------------------------- 主流程
def main():
    stats, segrows, power_rows, figures, notes = [], [], [], [], []
    run_meta = {}      # tag -> dict(label, log_last, recipe_last)（说明页『缺失情况』按实际数据生成）
    diags = {}         # tag -> 需求7 源系统偏差诊断（说明页按实际数据生成）
    for tag, label, src, rfile, rsheet in RUNS:
        proc, dropped = load_process(tag)
        sets = recipe_setpoint(rfile, rsheet)
        _log_last = int(proc["step"].max())
        run_meta[tag] = dict(label=label, log_last=_log_last,
                             recipe_last=max(sets.keys()),
                             missing=missing_steps(os.path.join(BASE, rfile), rsheet,
                                                   set(int(x) for x in proc["step"].unique()),
                                                   _log_last))
        diags[tag] = t_diag(proc)
        notes.append(f"{tag} [{label}]  数据源 {src}：主工艺 {len(proc)} 行，"
                     f"{proc['时间'].iloc[0]} → {proc['时间'].iloc[-1]}"
                     + (f"；剔除文件尾部属于下一炉次的残段 {dropped} 行" if dropped else
                        "；无文件尾部残段（步号回退仅为工艺循环，属同一炉次）"))

        res, order = {}, []
        for (s, p), sub in proc.groupby(["step", "pass"], sort=False):
            el = ((sub["时间"] - sub["时间"].iloc[0]).dt.total_seconds() / 60).to_numpy()
            temp = sub[TCOL].astype(float).to_numpy()
            t1 = sub["T1温度"].astype(float).to_numpy()
            t2 = sub["T2温度"].astype(float).to_numpy()
            dt = float(np.median(np.diff(el))) if len(el) > 2 else 1.0
            tsp = float(sub["目标温度"].iloc[0])
            ph = phase_of(s)
            r = analyze(el, temp, t1, t2, tsp, dt, ph)
            pwr = sub[PCOL].astype(float).to_numpy()
            if _cool_phase(ph):
                nz = pwr[pwr > 1.0]
                heater_on = (len(nz) / len(pwr) >= 0.05) and (np.median(nz) >= 5 if len(nz) else False)
                okp = np.ones(len(pwr), bool) if not heater_on else pwr > 1.0
            else:
                okp = np.ones(len(pwr), bool)
            r["Pmin"], r["Pmax"] = float(pwr[okp].min()), float(pwr[okp].max())
            r["n_pwr_drop"] = int((~okp).sum())
            r["_okp"] = okp
            r["n_rec"] = int(len(sub))
            r["t0"], r["t1"] = sub["时间"].iloc[0], sub["时间"].iloc[-1]
            r["tsp"] = tsp
            res[(s, p)] = r
            order.append((s, p))

            lbl = f"{s}" + (f"({p})" if p > 1 else "")
            note = ""
            if r["n_rec"] < MIN_N or r["dur"] < MIN_DUR:
                note = f"采样点过少（{r['n_rec']}条/{r['dur']:.1f}min），跳过绘图"
            if r["n_frozen"] > 0:
                add = (f"主控温度曾冻结于 T1 量程上限 {T1SAT}℃（失效点 {r['n_frozen']} 个已剔除）"
                       f"，随后测温通道切至 T2；本步含通道切换，速率仅供参考")
                note = (note + "；" if note else "") + add
            if r["type"] == T_FLAT and _cool_phase(ph):
                rs = sets.get(s)
                if rs and abs(rs - r["tsp"]) > 3:
                    note += f"；停滞于 {r['T1']:.0f}℃，未达设定值 {rs:.0f}℃"
                else:
                    note += "；温度已稳定于设定值附近，速率≈0"
            stats.append(dict(
                炉次=tag, 阶段=ph, 步号=lbl, 记录数=r["n_rec"], 有效点数=r["n"],
                剔除跳零=r["n_zero"], 剔除冻结=r["n_frozen"], 剔除功率=r["n_pwr_drop"],
                起始时间=r["t0"], 结束时间=r["t1"], 时长_min=round(r["dur"], 1),
                目标温度_log=round(r["tsp"], 1), 设定温度_recipe=sets.get(s),
                起始温度=round(r["T0"], 2) if r["T0"] == r["T0"] else None,
                结束温度=round(r["T1"], 2) if r["T1"] == r["T1"] else None,
                净变化=round(r["net"], 2), 波动幅度=round(r["amp"], 2) if r["amp"] == r["amp"] else None,
                曲线类型=r["type"], 分段数=r["n_seg"],
                恒温起点_min=round(r["hold_t"], 1) if r["hold_t"] is not None else None,
                恒温温度=round(r["hold_T"], 2) if r["hold_T"] == r["hold_T"] else None,
                恒温偏离设定=round(r["hold_dev"], 2) if r["hold_dev"] == r["hold_dev"] else None,
                端点差速率=round(r["endpoint"], 4) if r["endpoint"] == r["endpoint"] else None,
                推荐速率=round(r["rec"], 4) if r["rec"] == r["rec"] else None,
                速率含义=RATE_MEAN.get(r["type"], "-"),
                数据可信度=("存疑(测温通道切换)" if r["n_frozen"] > 0 else "正常"),
                速率差异=round(r["rec"] - r["endpoint"], 4)
                if (r["rec"] == r["rec"] and r["endpoint"] == r["endpoint"]) else None,
                拟合R2=round(r["r2"], 4) if r["r2"] == r["r2"] else None,
                上升段速率=round(r["rise"], 4) if r["rise"] == r["rise"] else None,
                下降段速率=round(r["fall"], 4) if r["fall"] == r["fall"] else None,
                功率最小值=round(r["Pmin"], 2), 功率最大值=round(r["Pmax"], 2), 备注=note))
            for k, sg in enumerate(r["segs"], 1):
                segrows.append(dict(炉次=tag, 阶段=ph, 步号=lbl, 曲线类型=r["type"], 分段=k,
                                    起_min=round(sg["t0"], 2), 止_min=round(sg["t1"], 2),
                                    起温=round(sg["icpt"], 2), 段时长=round(sg["t1"] - sg["t0"], 2),
                                    段温变=round(sg["dT"], 2), 段速率=round(sg["slope"], 4),
                                    段R2=round(sg["r2"], 4) if sg["r2"] == sg["r2"] else None,
                                    点数=sg["n"], 方向="下降" if sg["slope"] < 0 else "上升"))
            if _cool_phase(ph):
                for e, v in zip(el[r["_okp"]], pwr[r["_okp"]]):
                    power_rows.append(dict(炉次=tag, 步号=lbl, 相对时间_min=round(float(e), 3),
                                           电源总功率=round(float(v), 3)))
        # ---- 图 ----
        figures.append(plot_overview(tag, label, proc))
        heat = [k for k in order if k[0] < hold]
        cool = [k for k in order if k[0] > hold]
        holdk = [k for k in order if k[0] == hold]
        _span = lambda ks: (f"步{min(k[0] for k in ks)}–{max(k[0] for k in ks)}" if ks else "—")
        figures.append(plot_step_grid(tag, label, proc, res, sets, heat, "temp",
                                      f"{tag}_涂层前分步曲线.png",
                                      f"涂层前 分步曲线（{_span(heat)}，按类型拟合）"))
        figures.append(plot_step_grid(tag, label, proc, res, sets, cool, "temp",
                                      f"{tag}_涂层后分步曲线.png",
                                      f"涂层后 分步曲线（{_span(cool)}，按类型拟合）"))
        figures.append(plot_step_grid(tag, label, proc, res, sets, cool, "power",
                                      f"{tag}_涂层后分步功率曲线.png",
                                      f"涂层后 分步功率曲线（{_span(cool)}）"))
        if holdk:
            figures.append(plot_hold(tag, label, proc, holdk[0]))
        for ph in (phases.PRE, phases.POST):
            rr = [dict(phase=ph, order=i, lbl=(f"{s}" + (f"({p})" if p > 1 else "")),
                       rec=res[(s, p)]["rec"], type=res[(s, p)]["type"])
                  for i, (s, p) in enumerate(order) if phase_of(s) == ph]
            pf = plot_rate_summary(tag, label, rr, ph)
            if pf:
                figures.append(pf)

    st = pd.DataFrame(stats)
    sg = pd.DataFrame(segrows)
    pw = pd.DataFrame(power_rows)
    f = plot_compare(st, "新旧方法速率对比.png")
    if f:
        figures.append(f)
    f = plot_type_dist(st, "曲线类型分布.png")
    if f:
        figures.append(f)

    note = pd.DataFrame([
        ["需求3目标", "此前仅《需求2》点名的步做了细致处理，其余步统一用『结束温度-初始温度÷时长』。"
                   "现改为自动判别曲线类型并选择对应算法，对各批次全部重算。"],
        ["温度变量", f"『主控温度』按《需求7》结合各批次 T1/T2 通道一致性自适应确定："
                  f"重叠测温窗（T1<{T_SWITCH:.0f}℃ 且 T2 有效）中位偏差 <{T_CONSIST:.0f}℃ 判为同一测点双量程，"
                  f"按 T1/T2 拼接；否则保持源系统主控口径（源值保留于『主控温度_原始』列）。" +
                  "　".join(f"{t}：{t_diag_text(diags[t])}" for t in sorted(diags))],
        ["清洗调整", f"原『T1 量程饱和冻结剔除』判据（|主控-T1|<{SAT_TOL}℃ 且 |T1-{T1SAT}|<{SAT_TOL}℃ 且 T2-主控>{SAT_GAP}℃）"
                  f"是为绕过『主控冻结』打的补丁。温度口径修正后（拼接批次高温段取自 T2，保持源主控批次主控本就跟随 T2），"
                  f"该判据自动不再命中、不再误剔有效点（代码保留作防御，命中数应为 0）。"],
        ["功率变量", "电源总功率反馈"],
        ["类型① 波浪式", f"围绕某水平反复起落。判据：整步极差≥{WAVE_AMP}℃；按转折点(幅值≥{TURN_C}℃)切出≥3 个单调段；"
                     f"且 |净变化|≤{WAVE_NET:.0%}×累计行程（即走了一圈又回到原处，端点差无意义）。"
                     f"算法：逐段求斜率，分别给出 上升速率/下降速率/波动幅度"],
        ["类型② 多段线性式", "同一大方向但斜率明显改变（先快后慢等）。判据：不满足波浪式但被切成≥2 段。"
                       "算法：每段独立最小二乘；推荐速率取『温变贡献最大段』的斜率"],
        ["类型③ 前期线性+后期恒温", f"后期进入平台并维持到步末。判据：从某点到步末 极差≤max({BAND_ABS}℃, {BAND_REL:.0%}×本步极差)"
                            f"且 尾部漂移斜率 ≤ min({TAIL_SLOPE}℃/min, {TAIL_REL:.0%}×前段斜率)"
                            f"（即真正『停住』而非缓慢漂移）且 剩余时长≥{HOLD_FRAC:.0%}。"
                            f"算法：速率取『初始温度→恒温点』的弦速率（需求2 明确要求）"],
        ["类型④ 单一线性", "全程近似一条直线（贪心分段未通过显著性门槛）。算法：整体最小二乘，报斜率与 R²"],
        ["类型⑤ 全程恒温/停滞", f"平台起点早于步长 {FLAT_FRAC:.0%}，或净变化可忽略。速率记 0"],
        ["分段显著性门槛", f"最多 {MAXSEG} 段；新分段须使 RMSE 下降至 <{RMSE_RATIO:.0%}，段长≥{SEG_FRAC:.0%}，"
                     f"且相邻斜率差 ≥ max({SLP_ABS}℃/min, {SLP_REL:.0%}×较大斜率)——避免统计显著但物理无意义的分段"],
        ["预处理", "主控温度≤0 剔除；~1min 归一化滑动平均；等时重采样至 ≤300 点（消除采样密度差异）"],
        ["绘图跳过", f"记录数<{MIN_N} 或 时长<{MIN_DUR}min 的短步（如步28/29）不绘图，仅文字说明"],
        ["数据清洗(功率)", "加热开启的步中 功率≤1kW 视为丢数剔除；加热已关闭的步保留真实 0"],
        ["炉次切分", "按步号回退点切分；剔除文件尾部属于下一炉次的残段（剔除行数见上方『炉次说明』）"],
        ["缺失情况", "；".join(
            f"{t}({run_meta[t]['label']}) log 止于步{run_meta[t]['log_last']}"
            f"（recipe 最后一步=步{run_meta[t]['recipe_last']}，其后的结束标记步无 log 记录）"
            for t in sorted(run_meta))
            + ("。" + missing_text([(t, run_meta[t]['label'], run_meta[t]['missing'])
                                   for t in sorted(run_meta)])
               if any(run_meta[t]['missing'] for t in run_meta) else "")],
        ["速率符号", "℃/min；负=降温，正=升温"],
        ["三温度同图（需求7）", "《需求7》要求三个温度都在图中：全程总览、分步网格、恒温步图均同时绘制"
                        " 主控温度（粗实线，最终口径）、T1温度（青虚线）、T2温度（紫点线）。"
                        "T1/T2 为 log 原始双测温，叠画用于核对修正结果与通道差异；"
                        "T2 在低温段读数为 0（高位热电偶未起测），绘图中按 0 值屏蔽以免压扁纵轴。"],
        ["对比列", "『端点差速率』为需求3 之前的旧算法，『推荐速率』为按类型选取的新算法，『速率差异』=新-旧"],
    ] + ([["人为操作记录（现场确认，非系统异常）",
           "　".join(f"{t}({lb})：{ops_text(t)}" for t, lb, *_ in RUNS if ops_text(t))]]
         if any(ops_text(t) for t, *_ in RUNS) else []), columns=["项目", "说明"])
    for nt in notes:
        note.loc[len(note)] = ["炉次说明", nt]
    xls = os.path.join(OUT, "需求3_工艺全程分析.xlsx")
    with pd.ExcelWriter(xls, engine="openpyxl") as w:
        note.to_excel(w, sheet_name="说明", index=False)
        st.to_excel(w, sheet_name="全程分步统计", index=False)
        sg.to_excel(w, sheet_name="分段速率明细", index=False)
        pw.to_excel(w, sheet_name="降温功率数据", index=False)
    fmt_xlsx(xls, {"说明": [16, 110],
                   "全程分步统计": [7, 7, 8, 8, 9, 9, 9, 9, 20, 20, 9, 13, 14, 10, 10, 9, 9, 18, 8, 12, 11, 12,
                                    11, 11, 22, 18, 11, 9, 11, 11, 11, 11, 52],
                   "分段速率明细": [7, 7, 8, 14, 6, 10, 10, 10, 10, 10, 10, 8, 8, 8],
                   "降温功率数据": [8, 8, 14, 12]})
    st.to_csv(os.path.join(OUT, "_stats.csv"), index=False, encoding="utf-8-sig")
    print("excel:", xls)
    for f in figures:
        print("fig:", f)
    print()
    for nt in notes:
        print(nt)
    pd.set_option("display.width", 300); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 300)
    print()
    print(st[["炉次", "阶段", "步号", "时长_min", "净变化", "波动幅度", "曲线类型", "分段数",
              "恒温起点_min", "恒温温度", "端点差速率", "推荐速率", "拟合R2", "备注"]].to_string(index=False))


if __name__ == "__main__":
    main()
