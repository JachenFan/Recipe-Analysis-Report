# -*- coding: utf-8 -*-
"""
CVD 工艺『真空度—蝶阀开度』分析 v4（需求4）
========================================================
把需求3（自动判别曲线类型 → 选对应算法）的同一套模式，从
   温度(主控温度) — 功率(电源总功率反馈)
平移到
   真空度(炉内真空) — 蝶阀开度(数控蝶阀开度反馈)

变量角色对应：
   主控温度   → 炉内真空        （被控量 PV；设定值列 目标温度 → 目标压力）
   电源总功率 → 数控蝶阀开度反馈（操纵量 MV；统计每步 max/min）

相对需求3 的两处改造：
 1) 判据全部『尺度无关化』：温度是 ℃（量程千级、波动个位数），真空是 Pa
    （量程 68 Pa ~ 100 kPa，跨 3 个数量级，波动从 1.4 Pa 到 6 万 Pa）。
    若沿用绝对阈值会全盘失效，故改为：
       恒压/平坦   : 极差 ≤ FLAT_REL × 全局跨度S
       恒压带      : max(FLAT_REL×S, BAND_REL×本步极差)
       转折点幅值  : TURN_REL × 本步极差
       平台尾部漂移: ≤ TAIL_REL × 前段斜率
       分段斜率差  : ≥ max(0.02×本步极差/步长, SLP_REL×较大斜率)
    其余（RMSE 降幅、段长、净变化/行程比）本就无量纲。
 2) 新增『真空计量程切换』处理：
    炉内真空由 低真空反馈 / 高真空反馈 两个真空计拼接而成（22%~33% 的点取自高真空计）。
    高真空计量程上限 13649.1 Pa；切换处两表读数相差中位数约 1.3 万 Pa，
    即 炉内真空 会出现『单采样点突变上万 Pa』的假跳变（与温度 T1/T2 切换同类）。
    处理：以切换点为硬断点把该步切成若干 piece，各 piece 独立分析，
    拟合时不跨越断点（跳变不计入任何速率），并在备注标注。

三种基本类型（+ 退化类型）与需求3 **算法完全一致，仅话术按领域改名**：
  波浪式 / 多段线性式 / 前期线性+后期恒压 / 单一线性 / 全程恒压停滞 / 短步
  （温度域同位置叫「恒温」，压力·真空域叫「恒压」；见 seg_terms.py 单一事实来源）
速率单位：Pa/min（正=压力升高=真空变差，负=压力降低=真空变好）
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
from seg_terms import terms as _terms  # noqa: E402  分段类型领域话术（单一事实来源）
from batch_notes import ops_text  # noqa: E402  人为操作记录（现场确认，单一事实来源）
from recipe_meta import missing_steps, missing_text  # noqa: E402  缺失步检测（不得静默丢失）
import phases  # noqa: E402  阶段划分（涂层前/中/后，单一事实来源）

warnings.filterwarnings("ignore")
rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
rcParams["axes.unicode_minus"] = False

import project_paths as pp

BASE = pp.ROOT
SIMU = pp.code()
OUT = pp.out("需求4_真空度蝶阀分析")
FIG = os.path.join(OUT, "图表")
os.makedirs(FIG, exist_ok=True)

# ---------------------------------------------------------------- 变量配置
PV, MV, SP = "炉内真空", "数控蝶阀开度反馈", "目标压力"
HI_SAT = 13649.1        # 高真空计量程上限(Pa)
JUMP_REL = 0.02         # 单采样点跳变 > JUMP_REL×全局跨度 且 真空计来源改变 → 判为量程切换
U_PV, U_MV = "Pa", "%"

# 判据：真空度跨 3 个数量级（68 Pa ~ 100 kPa），绝对阈值无意义，
#       故一律按【本步压力水平的百分比】或【本步极差的比例】取值
EPS_REL = 0.01          # 噪声地板 = 该比例 × 本步压力水平
FLAT_LVL = 0.05         # 全程恒定：本步极差 ≤ 该比例 × 本步压力水平
BAND_REL = 0.12         # 恒压带 = max(噪声地板, BAND_REL×本步极差)
TURN_REL = 0.05         # 转折点幅值 = max(噪声地板, TURN_REL×本步极差)
MIN_PIECE = 5           # 真空计切换去抖：相邻断点间隔小于该采样数只保留首个
DISC_REL = 0.15         # 瞬时突变：单采样点跳变 > max(DISC_REL×本步极差, 5×噪声地板)
R2_LOW = 0.90           # 分段拟合 R² 低于此值 → 备注提示"速率仅供参考"
TAIL_REL = 0.10         # 平台尾部漂移斜率 ≤ 该比例 × 前段斜率
WAVE_NET = 0.40         # 波浪：|净变化| ≤ 该比例 × 累计行程
SEG_FRAC = 0.08         # 分段最短长度(占步长)
RMSE_RATIO = 0.70       # 分段接受：新RMSE < 该比例 × 旧RMSE
SLP_REL = 0.50          # 分段接受：相邻斜率差 ≥ 该比例 × 较大斜率
SLP_FLOOR = 0.02        # 分段接受：相邻斜率差 ≥ 该比例 × 本步极差 / 步长
MAXSEG = 5
NDS = 300               # 等时重采样点数
MIN_N, MIN_DUR = 8, 3.0  # 绘图最小样本/时长

RUNS = [
    # 文件命名已规范化为 {批次}-log.xlsx；run5 短名规范为 29C261011
    ("run1", "29C260916", "29C260916-log"),
    ("run2", "29C260926", "29C260926-log"),
    ("run3", "29C260821", "29C260821-log"),
    ("run4", "29C260903", "29C260903-log"),
    ("run5", "29C261011", "29C261011-log"),
    ("run6", "29C261016", "29C261016-log"),
]
C_PV, C_SP, C_MV, C_FIT = "#1c7ed6", "#868e96", "#d9480f", "#2f9e44"

# 领域话术：压力·真空域的平台叫「恒压」（不是温度的「恒温」）——见 seg_terms.py
_P = _terms("P")
T_WAVE, T_MULTI, T_LINHOLD, T_LIN, T_FLAT, T_SHORT = (
    _P["wave"], _P["multi"], _P["lin_hold"], _P["lin"], _P["flat"], _P["short"])
T_SWITCH = "多段线性式(含真空计切换)"
T_JUMP = "多段线性式(含瞬时突变)"

RATE_MEAN = {
    T_LIN: "整体最小二乘斜率",
    T_MULTI: "变化量最大段(主段)斜率",
    T_SWITCH: "真空计切换分段后的主段斜率",
    T_JUMP: "突变分段后的主段斜率（突变本身另列）",
    T_WAVE: "波浪：下降段/上升段加权速率",
    T_LINHOLD: "初始值→平台值 的弦速率",
    T_FLAT: "0（无有效变化）",
    T_SHORT: "数据不足，不计算",
}


# ---------------------------------------------------------------- 数据
def load_process(tag, hold=None):
    """按步号回退点切分炉次：只保留到『含「涂层中」步』的主工艺段。

    `hold` 缺省由 `phases.hold_of(tag)` 从该批 recipe 现算——
    原实现写死"含步25 的段"，只对 29C 机台成立（25C261007 的涂层段在步32）。
    """
    df = pd.read_csv(os.path.join(SIMU, f"_{tag}_vac.csv"))
    df["时间"] = pd.to_datetime(df["时间"])
    df = df.dropna(subset=["当前运行步号"]).sort_values("时间").reset_index(drop=True)
    df["step"] = df["当前运行步号"].astype(int)
    st = df["step"].to_numpy()
    df["seg"] = np.cumsum(np.concatenate([[True], np.diff(st) < 0]))
    seg_ids = list(df["seg"].unique())
    if hold is None:
        hold = phases.hold_of(tag)
    if (df["step"] == hold).any():
        main_seg = seg_ids[0]
        for sid in seg_ids:
            if hold in set(df.loc[df["seg"] == sid, "step"]):
                main_seg = sid
                break
    else:
        # log 被截断/该步未执行：取记录最多的段（主工艺段必然最长）
        main_seg = max(seg_ids, key=lambda sid: int((df["seg"] == sid).sum()))
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
    return proc, dropped


def global_scale(proc):
    v = proc[PV].astype(float).to_numpy()
    v = v[np.isfinite(v) & (v > 0)]
    return float(np.percentile(v, 95) - np.percentile(v, 5))


# ---------------------------------------------------------------- 算法
def smooth(y, dt):
    y = np.asarray(y, dtype=float)
    w = max(3, int(round(1.0 / max(dt, 1e-6))))
    w = min(w, len(y) if len(y) % 2 else len(y) - 1)
    if w < 3 or len(y) < 3:
        return y.copy()
    k = np.ones(w) / w
    return np.convolve(y, k, mode="same") / np.convolve(np.ones(len(y)), k, mode="same")


def segstats(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
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
    return dict(slope=float(b), icpt=float(a), rss=rss,
                r2=float(1 - rss / sst) if sst > 1e-12 else float("nan"), n=m)


def zigzag(y, thr):
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


def find_plateau(x, y, band, tail_rel=TAIL_REL):
    """平台起点：从该点到步末 极差≤band，且 尾部漂移斜率 ≤ tail_rel×前段斜率"""
    n = len(y)
    if n < 5:
        return None
    suf_max = np.maximum.accumulate(y[::-1])[::-1]
    suf_min = np.minimum.accumulate(y[::-1])[::-1]
    pp = suf_max - suf_min
    dur = x[-1] - x[0]
    ok = (pp <= band)
    if dur > 0:
        ok &= ((x[-1] - x) >= 0.15 * dur)
    ok &= ((n - np.arange(n)) >= max(5, 0.10 * n))
    csx = np.concatenate([[0.0], np.cumsum(x)])
    csy = np.concatenate([[0.0], np.cumsum(y)])
    csxx = np.concatenate([[0.0], np.cumsum(x * x)])
    csxy = np.concatenate([[0.0], np.cumsum(x * y)])
    m = (n - np.arange(n)).astype(float)
    sx, sy = csx[n] - csx[:n], csy[n] - csy[:n]
    sxx, sxy = csxx[n] - csxx[:n], csxy[n] - csxy[:n]
    den = m * sxx - sx * sx
    s_tail = np.where(np.abs(den) > 1e-12, (m * sxy - sx * sy) / np.where(np.abs(den) > 1e-12, den, 1.0), 0.0)
    mp = np.arange(n).astype(float)
    px, py = csx[:n], csy[:n]
    pxx, pxy = csxx[:n], csxy[:n]
    dp = mp * pxx - px * px
    s_pre = np.where(np.abs(dp) > 1e-12, (mp * pxy - px * py) / np.where(np.abs(dp) > 1e-12, dp, 1.0), 0.0)
    ok &= (np.abs(s_tail) <= tail_rel * np.abs(s_pre)) & (mp >= 5)
    cand = np.where(ok)[0]
    cand = cand[cand >= 1]
    return int(cand[0]) if len(cand) else None


def greedy_split(x, y, slp_floor, maxseg=MAXSEG):
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
            if rmse0 <= 1e-12:
                continue
            for m in range(a + min_pts, b - min_pts + 1):
                if dur > 0 and ((x[m] - x[a]) < min_dur or (x[b - 1] - x[m]) < min_dur):
                    continue
                s1 = segstats(x[a:m], y[a:m])
                s2 = segstats(x[m:b], y[m:b])
                if np.sqrt((s1["rss"] + s2["rss"]) / (b - a)) >= RMSE_RATIO * rmse0:
                    continue
                if abs(s1["slope"] - s2["slope"]) < max(slp_floor, SLP_REL * max(abs(s1["slope"]), abs(s2["slope"]))):
                    continue
                rss = s1["rss"] + s2["rss"]
                if best is None or rss < best[0]:
                    best = (rss, k, m)
        if best is None:
            break
        bounds.insert(best[1] + 1, best[2])
    return bounds


def refit_raw(el, temp, t_edges):
    out = []
    for a, b in zip(t_edges[:-1], t_edges[1:]):
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
    rss = sum(s["rss"] for s in segs)
    yy = np.asarray(temp, float)
    sst = float(np.sum((yy - yy.mean()) ** 2))
    return float(1 - rss / sst) if sst > 1e-12 else float("nan")


def gauge_switch(el, temp, hi, lo, S):
    """真空计量程切换点：来源真空计改变 且 单采样点跳变 > JUMP_REL×S。
    返回 raw 下标列表 k（表示 k 与 k+1 之间为断点）"""
    if hi is None or lo is None or len(temp) < 5:
        return []
    src = np.where(np.abs(temp - hi) < 1e-6, 1, np.where(np.abs(temp - lo) < 1e-6, 2, 0))
    cut = []
    thr = JUMP_REL * S
    for k in np.where(np.diff(src) != 0)[0]:
        if src[k] == 0 or src[k + 1] == 0 or src[k] == src[k + 1]:
            continue
        if abs(temp[k + 1] - temp[k]) > thr:
            cut.append(int(k))
    # 去抖：迟滞带内会来回切换，间隔过近的断点只保留首个
    kept = []
    for k in cut:
        if not kept or k - kept[-1] >= MIN_PIECE:
            kept.append(k)
    return kept


def find_disc(el, temp, ptp, eps):
    """瞬时突变：单采样点跳变超过 max(DISC_REL×本步极差, 5×噪声地板)。
    返回 raw 下标 k（k 与 k+1 之间为断点）"""
    if len(temp) < 3:
        return []
    d = np.abs(np.diff(temp))
    thr = max(DISC_REL * ptp, 5 * eps)
    return [int(i) for i in np.where(d > thr)[0]]


# ------------------------------------------------- 单段(无切换)分析
def analyze_one(el, temp, sp, dt, S, phase):
    n = len(el)
    res = dict(type=T_SHORT, segs=[], n_seg=0, rec=float("nan"), endpoint=float("nan"),
               rise=float("nan"), fall=float("nan"), amp=float("nan"), hold_t=None,
               hold_T=float("nan"), hold_dev=float("nan"), r2=float("nan"),
               n=n, dur=0.0, net=0.0, P0=float("nan"), P1=float("nan"), _plat=[])
    if n < 5:
        return res
    dur = float(el[-1] - el[0])
    res["dur"] = dur
    res["P0"], res["P1"] = float(temp[0]), float(temp[-1])
    net = float(temp[-1] - temp[0])
    res["net"] = net
    res["endpoint"] = net / dur if dur > 0 else float("nan")
    amp = float(np.ptp(temp))
    res["amp"] = amp
    if dur <= 0:
        res["type"], res["rec"] = T_FLAT, 0.0
        return res
    tsm = smooth(temp, dt)
    N = min(NDS, n)
    xg = np.linspace(el[0], el[-1], N) if n > N else el.copy()
    yg = np.interp(xg, el, tsm) if n > N else tsm.copy()
    ptp = float(np.ptp(yg)) if len(yg) else 0.0
    lvl = max(abs(float(np.median(temp))), 1e-9)   # 本步压力水平
    eps = EPS_REL * lvl                            # 幅度噪声地板
    flat_thr = FLAT_LVL * lvl                      # 判定"恒定"的极差上限
    slp_floor = SLP_FLOOR * (ptp / dur) if dur > 0 else 0.0
    slope_zero = max(slp_floor, 0.10 * abs(res["endpoint"])) if res["endpoint"] == res["endpoint"] else slp_floor

    # ① 全程恒压
    if amp <= flat_thr:
        res.update(type=T_FLAT, hold_t=0.0, hold_T=float(np.mean(temp)), rec=0.0, n_seg=1)
        res["hold_dev"] = res["hold_T"] - sp if sp else float("nan")
        res["_plat"] = refit_raw(el, temp, [el[0], el[-1]])
        return res

    # ② 平台（前期变化 + 后期恒定）
    band = max(eps, BAND_REL * ptp)
    pi = find_plateau(xg, yg, band)
    if pi is not None:
        hold_t = float(xg[pi] - xg[0])
        hold_T = float(np.mean(yg[pi:]))
        # 只有"平台之前的変化也可忽略"才算全程恒定；若前期确有大幅变化，则归为 前期变化+后期恒定
        if abs(hold_T - temp[0]) <= flat_thr:
            res.update(type=T_FLAT, hold_t=hold_t, hold_T=hold_T, rec=0.0, n_seg=1)
            res["hold_dev"] = hold_T - sp if sp else float("nan")
            res["_plat"] = refit_raw(el, temp, [el[0] + hold_t, el[-1]])
            return res
        sb = greedy_split(xg[:pi + 1], yg[:pi + 1], slp_floor)
        edges = [float(xg[min(i, pi)]) for i in sb] if len(sb) - 1 >= 2 else [float(xg[0]), float(xg[pi])]
        segs = refit_raw(el, temp, edges)
        res.update(type=T_LINHOLD, hold_t=hold_t, hold_T=hold_T, segs=segs, n_seg=len(segs))
        res["hold_dev"] = hold_T - sp if sp else float("nan")
        res["_plat"] = refit_raw(el, temp, [el[0] + hold_t, el[-1]])
        if segs:
            res["rec"] = (hold_T - float(temp[0])) / hold_t if hold_t > 0 else float("nan")
            res["r2"] = piecewise_r2(segs, el, temp)
            _rates(res, segs)
        return res

    # ③ 波浪式
    if ptp > 0:
        piv = zigzag(yg, max(eps, TURN_REL * ptp))
        if len(piv) - 1 >= 3:
            last_i = len(xg) - 1
            wz = refit_raw(el, temp, [float(xg[min(p, last_i)]) for p in piv])
            if 3 <= len(wz) <= MAXSEG:      # 段数过多 = 噪声被当成波浪，不判为波浪式
                path = sum(abs(s["dT"]) for s in wz)
                if path > 0 and abs(net) <= WAVE_NET * path:
                    res.update(type=T_WAVE, segs=wz, n_seg=len(wz))
                    _rates(res, wz)
                    res["rec"] = res["fall"] if _cool_phase(phase) else res["rise"]
                    res["r2"] = piecewise_r2(wz, el, temp)
                    return res

    # ④ 贪心分段线性
    bounds = greedy_split(xg, yg, slp_floor)
    last_i = len(xg) - 1
    segs = refit_raw(el, temp, [float(xg[min(i, last_i)]) for i in bounds])
    if len(segs) <= 1:
        s = segstats(el - el[0], temp)
        res.update(type=T_LIN, n_seg=1, rec=s["slope"], r2=s["r2"])
        res["segs"] = [dict(t0=float(el[0]), t1=float(el[-1]), slope=s["slope"], icpt=s["icpt"],
                            r2=s["r2"], rss=s["rss"], n=n, dT=float(net))]
        res["rise"] = s["slope"] if s["slope"] > 0 else float("nan")
        res["fall"] = s["slope"] if s["slope"] < 0 else float("nan")
        return res
    # 末段近水平 → 线性+恒压
    last = segs[-1]
    if abs(last["slope"]) < slope_zero and (last["t1"] - last["t0"]) >= 0.20 * dur:
        res.update(type=T_LINHOLD, hold_t=float(last["t0"] - el[0]),
                   hold_T=float(np.mean(temp[el >= last["t0"]])))
        res["hold_dev"] = res["hold_T"] - sp if sp else float("nan")
        res["_plat"] = refit_raw(el, temp, [float(last["t0"]), el[-1]])
        segs = segs[:-1]
        res["segs"], res["n_seg"] = segs, len(segs)
        if segs:
            tt = sum(s["t1"] - s["t0"] for s in segs)
            res["rec"] = sum(s["dT"] for s in segs) / tt if tt > 0 else float("nan")
            res["r2"] = piecewise_r2(segs, el, temp)
            _rates(res, segs)
        return res
    res["segs"], res["n_seg"] = segs, len(segs)
    _rates(res, segs)
    rev = _reversals(segs, slope_zero)
    path = sum(abs(s["dT"]) for s in segs)
    if len(segs) >= 3 and rev >= 2 and path > 0 and abs(net) <= WAVE_NET * path:
        res.update(type=T_WAVE)
        res["rec"] = res["fall"] if _cool_phase(phase) else res["rise"]
    else:
        res.update(type=T_MULTI, rec=max(segs, key=lambda s: abs(s["dT"]))["slope"])
    res["r2"] = piecewise_r2(segs, el, temp)
    return res


def _rates(res, segs):
    dn = [s for s in segs if s["slope"] < 0]
    up = [s for s in segs if s["slope"] > 0]
    if dn:
        res["fall"] = sum(s["dT"] for s in dn) / sum(s["t1"] - s["t0"] for s in dn)
    if up:
        res["rise"] = sum(s["dT"] for s in up) / sum(s["t1"] - s["t0"] for s in up)


def _reversals(segs, zero):
    d = [0 if abs(s["slope"]) < zero else (1 if s["slope"] > 0 else -1) for s in segs]
    nz = [x for x in d if x != 0]
    return sum(1 for i in range(1, len(nz)) if nz[i] != nz[i - 1])


def analyze(el_raw, temp_raw, hi_raw, lo_raw, sp, dt, phase, S):
    """含断点（真空计量程切换 / 瞬时突变）：按断点切 piece 独立分析后合并，
    断点处的跳变单独列为『突变』段，不计入任何拟合速率"""
    ok = np.isfinite(temp_raw) & (temp_raw > 0)
    el, temp = np.asarray(el_raw, float)[ok], np.asarray(temp_raw, float)[ok]
    hi = np.asarray(hi_raw, float)[ok] if hi_raw is not None else None
    lo = np.asarray(lo_raw, float)[ok] if lo_raw is not None else None
    ptp = float(np.ptp(temp)) if len(temp) else 0.0
    lvl = max(abs(float(np.median(temp))) if len(temp) else 0.0, 1e-9)
    eps = EPS_REL * lvl
    gcuts = set(gauge_switch(el, temp, hi, lo, S))
    dcuts = set(find_disc(el, temp, ptp, eps))
    cuts = sorted(gcuts | dcuts)
    # 去抖
    kept = []
    for k in cuts:
        if not kept or k - kept[-1] >= MIN_PIECE:
            kept.append(k)
    cuts = kept
    base = analyze_one(el, temp, sp, dt, S, phase)
    base["n_switch"] = len([k for k in cuts if k in gcuts])
    base["n_jump"] = len(cuts)
    base["jump_max"] = float(max([abs(temp[k + 1] - temp[k]) for k in cuts], default=0.0))
    if not cuts:
        return base
    # 切 piece
    bnd, st = [], 0
    for k in cuts:
        bnd.append((st, k))
        st = k + 1
    bnd.append((st, len(el) - 1))
    segs = []
    plats = []
    jump_rows = []
    for i, (a, b) in enumerate(bnd):
        if b - a + 1 >= MIN_PIECE:
            sub = analyze_one(el[a:b + 1], temp[a:b + 1], sp, dt, S, phase)
            segs.extend(sub["segs"])
            plats.extend(sub.get("_plat", []))
        elif b - a + 1 >= 2:
            # 过短碎片：整段作为一个水平段保留，保证明细覆盖完整时间轴
            plats.extend(refit_raw(el, temp, [el[a], el[b]]))
        if i < len(cuts):
            k = cuts[i]
            dtm = float(el[k + 1] - el[k])
            dj = float(temp[k + 1] - temp[k])
            jump_rows.append(dict(t0=float(el[k]), t1=float(el[k + 1]), slope=dj / dtm if dtm > 0 else float("nan"),
                                  icpt=float(temp[k]), r2=float("nan"), rss=0.0, n=2, dT=dj,
                                  jump=True, kind="真空计切换" if k in gcuts else "瞬时突变"))
    if not segs and not plats:
        return base
    merged = dict(base)
    merged["segs"] = segs
    merged["n_seg"] = len(segs)
    merged["type"] = (T_SWITCH if any(k in gcuts for k in cuts) else T_JUMP) if len(segs) >= 1 else base["type"]
    if segs:
        merged["rec"] = max(segs, key=lambda s: abs(s["dT"]))["slope"]
        merged["r2"] = piecewise_r2(segs, el, temp)
        _rates(merged, segs)
    else:
        merged["rec"] = float("nan")
        merged["r2"] = float("nan")
    if plats:
        last = plats[-1]
        m = (el >= last["t0"] - 1e-9) & (el <= last["t1"] + 1e-9)
        merged["hold_t"] = float(last["t0"] - el[0])
        merged["hold_T"] = float(np.mean(temp[m])) if m.sum() else float("nan")
        merged["hold_dev"] = merged["hold_T"] - sp if sp else float("nan")
    else:
        merged["hold_t"] = None
        merged["hold_T"] = float("nan")
    merged["_plat"] = plats
    merged["_jumps"] = jump_rows
    return merged


# ---------------------------------------------------------------- 绘图
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
    """阶段是否为"下降段"（兼容新阶段基名与旧字面量『降温』）。"""
    return str(phase) in ("降温", phases.POST)


def _step_series(proc, key):
    s, p = key
    sub = proc[(proc["step"] == s) & (proc["pass"] == p)].reset_index(drop=True)
    return sub, ((sub["时间"] - sub["时间"].iloc[0]).dt.total_seconds() / 60).to_numpy()


def plot_overview(tag, label, proc):
    fig, ax = plt.subplots(2, 1, figsize=(16, 9), sharex=True)
    fig.patch.set_facecolor("white")
    off, X, V, B = 0.0, [], [], []
    bounds = {}
    for (s, p), sub in proc.groupby(["step", "pass"], sort=False):
        el = ((sub["时间"] - sub["时间"].iloc[0]).dt.total_seconds() / 60).to_numpy()
        k = max(1, len(el) // 4000)
        X.append(off + el[::k]); V.append(sub[PV].to_numpy()[::k]); B.append(sub[MV].to_numpy()[::k])
        bounds[(s, p)] = (off, off + (el[-1] if len(el) else 0))
        off += (el[-1] if len(el) else 0)
    X, V, B = np.concatenate(X), np.concatenate(V), np.concatenate(B)
    ax[0].plot(X, V, color=C_PV, lw=0.8, label=f"{PV} ({U_PV})")
    ax[0].set_ylabel(f"{PV} ({U_PV})"); ax[0].legend(loc="upper right"); ax[0].grid(alpha=0.25)
    ax[0].set_yscale("log")
    ax[1].plot(X, B, color=C_MV, lw=0.7, label=f"{MV} ({U_MV})")
    ax[1].set_ylabel(f"{MV} ({U_MV})"); ax[1].legend(loc="upper right"); ax[1].grid(alpha=0.25)
    ax[1].set_xlabel("累计时间 (min，自首步起)")
    for name, x0, x1, c in phase_bands(bounds):
        for axx in ax:
            axx.axvspan(x0, x1, color=c, alpha=0.45, zorder=0)
        ax[0].text((x0 + x1) / 2, ax[0].get_ylim()[1], name,
                   ha="center", va="top", fontsize=12, color="#343a40", weight="bold")
    fig.suptitle(f"工艺全程总览（真空度 / 蝶阀开度）  {label}", fontsize=15)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    p = os.path.join(FIG, f"{tag}_全程总览.png")
    fig.savefig(p, dpi=140, facecolor="white"); plt.close(fig)
    return p


def plot_step_grid(tag, label, proc, res, step_list, kind, fname, title):
    ncol = 4
    nrow = int(np.ceil(len(step_list) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.3 * ncol, 2.9 * nrow), constrained_layout=True)
    fig.patch.set_facecolor("white")
    axes = np.atleast_1d(axes).ravel()
    for i, key in enumerate(step_list):
        ax = axes[i]
        s, p = key
        sub, el = _step_series(proc, key)
        r = res[key]
        lbl = f"{s}" + (f"({p})" if p > 1 else "")
        if len(sub) < MIN_N or r["dur"] < MIN_DUR:
            ax.axis("off")
            ax.text(0.5, 0.5, f"步号 {lbl}\n时长 {r['dur']:.1f} min\n记录 {len(sub)} 条\n"
                              f"→ 采样点过少，跳过绘图\n（详见 Excel 文字说明）",
                    ha="center", va="center", fontsize=9, color="#868e96",
                    bbox=dict(boxstyle="round", fc="#f8f9fa", ec="#ced4da"))
            continue
        if kind == "pv":
            v = sub[PV].astype(float).to_numpy()
            ax.plot(el, v, color=C_PV, lw=1.0, label=f"{PV}")
            sp = float(sub[SP].iloc[0]) if SP in sub.columns else 0.0
            if sp and sp > 0:
                ax.axhline(sp, color=C_SP, ls="--", lw=0.9, label=f"目标压力 {sp:.0f} {U_PV}")
            ty = r["type"]
            for k, sg in enumerate(r["segs"], 1):
                xs = np.array([sg["t0"], sg["t1"]])
                ax.plot(xs, sg["icpt"] + sg["slope"] * (xs - sg["t0"]), color=C_FIT, lw=1.8, zorder=5)
                if ty in (T_WAVE, T_MULTI, T_SWITCH):
                    ax.annotate(f"#{k} {sg['slope']:+.1f}", ((sg["t0"] + sg["t1"]) / 2,
                                (sg["icpt"] + sg["slope"] * (sg["t1"] - sg["t0"]) / 2)),
                                fontsize=6, color="#1864ab", ha="center")
            if ty == T_LINHOLD and r["hold_t"] is not None:
                ax.axvspan(r["hold_t"], el[-1], color="#fff3bf", alpha=0.55, zorder=0)
                ax.axvline(r["hold_t"], color="#7048e8", ls="--", lw=1.0)
            if np.nanmin(v) > 0:
                ax.set_yscale("log")
            ax.legend(fontsize=6, loc="best")
            if ty == T_WAVE:
                tt = f"上升{r['rise']:+.0f} / 下降{r['fall']:+.0f} {U_PV}/min"
            elif ty == T_LINHOLD:
                tt = f"前期{r['rec']:+.1f} {U_PV}/min，平台{r['hold_T']:.0f} {U_PV}"
            elif ty == T_FLAT:
                tt = f"净变{r['net']:+.0f} {U_PV}"
            elif ty in (T_MULTI, T_SWITCH):
                tt = f"{r['n_seg']}段 主段{r['rec']:+.1f} {U_PV}/min"
            else:
                tt = f"{r['rec']:+.1f} {U_PV}/min  R²={r['r2']:.3f}" if r["r2"] == r["r2"] else f"{r['rec']:+.1f}"
            ax.set_title(f"步号 {lbl} | {ty}\n{tt}", fontsize=8)
            ax.set_ylabel(f"{PV} ({U_PV})", fontsize=8)
        else:
            b = sub[MV].astype(float).to_numpy()
            ax.plot(el, b, color=C_MV, lw=0.9)
            ax.axhline(r["MVmin"], color="#2f9e44", ls=":", lw=0.9)
            ax.axhline(r["MVmax"], color="#e03131", ls=":", lw=0.9)
            ax.set_title(f"步号 {lbl} | 蝶阀 min {r['MVmin']:.2f} / max {r['MVmax']:.2f} {U_MV}", fontsize=9)
            ax.set_ylabel(f"蝶阀开度 ({U_MV})", fontsize=8)
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
    sub, el = _step_series(proc, key)
    el = el / 60.0
    v = sub[PV].astype(float).to_numpy()
    b = sub[MV].astype(float).to_numpy()
    sp = float(sub[SP].iloc[0])
    fig, ax = plt.subplots(2, 1, figsize=(15, 7), sharex=True)
    fig.patch.set_facecolor("white")
    k = max(1, len(v) // 8000)
    ax[0].plot(el[::k], v[::k], color=C_PV, lw=0.7, label=PV)
    ax[0].axhline(sp, color=C_SP, ls="--", lw=1.0, label=f"目标压力 {sp:.0f} {U_PV}")
    ax[0].set_ylabel(f"{PV} ({U_PV})"); ax[0].legend(fontsize=9, loc="best"); ax[0].grid(alpha=0.25)
    ax[0].set_title(f"涂层中（步号 {s}）{label}   时长 {el[-1]:.1f} h   均值 {np.mean(v):.0f} {U_PV}   "
                    f"极差 {np.ptp(v):.0f} {U_PV}   相对波动 {np.ptp(v)/max(np.mean(v),1e-9):.2%}", fontsize=12)
    ax[1].plot(el[::k], b[::k], color=C_MV, lw=0.7, label=MV)
    ax[1].set_ylabel(f"蝶阀开度 ({U_MV})"); ax[1].set_xlabel("时间 (h)")
    ax[1].legend(fontsize=9); ax[1].grid(alpha=0.25)
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
    ax.set_ylabel(f"速率 ({U_PV}/min)")
    ax.set_title(f"{phase} 各步真空度速率（{label}，按曲线类型选用方法）  负=压力下降(真空变好)", fontsize=13)
    for xi, v in zip(x, vals):
        ax.text(xi, v, f"{v:+.0f}", ha="center", va="bottom" if v >= 0 else "top", fontsize=7)
    ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    p = os.path.join(FIG, f"{tag}_{phase}速率.png")
    fig.savefig(p, dpi=140, facecolor="white"); plt.close(fig)
    return p


def plot_compare(stats, fname):
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
        x = np.arange(len(g)); w = 0.38
        ax.bar(x - w / 2, g["端点差速率"], w, color="#adb5bd", label="旧：端点差÷时长")
        ax.bar(x + w / 2, g["推荐速率"], w, color="#1c7ed6", label="新：按曲线类型")
        ax.set_xticks(x)
        ax.set_xticklabels([f"{a}{b}" for a, b in zip(g["步号"], g["阶段"].str[0])], fontsize=7, rotation=60)
        ax.axhline(0, color="#495057", lw=0.8)
        ax.set_ylabel(f"速率 ({U_PV}/min)")
        ax.set_title(f"{tag}  两种算法速率对比", fontsize=12)
        ax.legend(fontsize=8); ax.grid(alpha=0.25, axis="y")
    fig.suptitle("真空度：自动分型方法 vs 端点差方法", fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    p = os.path.join(FIG, fname)
    fig.savefig(p, dpi=140, facecolor="white"); plt.close(fig)
    return p


def plot_type_dist(stats, fname):
    order = [T_LIN, T_MULTI, T_SWITCH, T_JUMP, T_WAVE, T_LINHOLD, T_FLAT, T_SHORT]
    types = [t for t in order if (stats["曲线类型"] == t).any()]
    fig, ax = plt.subplots(figsize=(11, 5))
    fig.patch.set_facecolor("white")
    w = 0.38; x = np.arange(len(types))
    for i, tag in enumerate(["run1", "run2"]):
        c = [((stats["炉次"] == tag) & (stats["曲线类型"] == t)).sum() for t in types]
        ax.bar(x + (i - 0.5) * w, c, w, label=tag)
        for xi, v in zip(x + (i - 0.5) * w, c):
            ax.text(xi, v, str(v), ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels(types, fontsize=9)
    ax.set_ylabel("步数"); ax.set_title("自动判别结果：真空度曲线类型分布", fontsize=13)
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
def load_recipe_last(tag):
    """recipe 最后一步（含结束标记步）。recipe 文件名单一来源 = analyze_v5.RUNS，避免再维护一份映射。"""
    import analyze_v5 as _v5
    rfile = {t: r for t, _, r, *_ in _v5.RUNS}[tag]
    d = pd.read_excel(pp.recipe(rfile), usecols=["步号"])
    return int(d["步号"].astype(int).max())


def load_recipe_meta(tag):
    """recipe 文件路径与首个 sheet 名（用于缺失步检测；文件名映射同 load_recipe_last）。"""
    import analyze_v5 as _v5
    rfile = {t: r for t, _, r, *_ in _v5.RUNS}[tag]
    p = pp.recipe(rfile)
    return p, pd.ExcelFile(p).sheet_names[0]


def main():
    stats, segrows, mv_rows, figures, notes = [], [], [], [], []
    run_meta = {}      # tag -> dict(label, log_last, recipe_last, missing)（说明页按实际数据生成）
    for tag, label, src in RUNS:
        hold = phases.hold_of(tag)               # 涂层中 = 本批 recipe 时长最长的一步
        phases.set_active(hold, tag)
        proc, dropped = load_process(tag, hold=hold)
        S = global_scale(proc)
        _rp, _rs = load_recipe_meta(tag)
        _log_last = int(proc["step"].max())
        run_meta[tag] = dict(label=label, log_last=_log_last,
                             recipe_last=load_recipe_last(tag),
                             missing=missing_steps(_rp, _rs,
                                                   set(int(x) for x in proc["step"].unique()),
                                                   _log_last))
        notes.append(f"{tag} [{label}]  数据源 {src}：主工艺 {len(proc)} 行，"
                     f"{proc['时间'].iloc[0]} → {proc['时间'].iloc[-1]}；"
                     f"真空度量程跨度 S(P95-P5)={S:.0f} {U_PV}"
                     + (f"；剔除文件尾部属于下一炉次的残段 {dropped} 行" if dropped else ""))
        res, order = {}, []
        for (s, p), sub in proc.groupby(["step", "pass"], sort=False):
            el = ((sub["时间"] - sub["时间"].iloc[0]).dt.total_seconds() / 60).to_numpy()
            v = sub[PV].astype(float).to_numpy()
            hi = sub["高真空反馈"].astype(float).to_numpy()
            lo = sub["低真空反馈"].astype(float).to_numpy()
            dt = float(np.median(np.diff(el))) if len(el) > 2 else 1.0
            sp = float(sub[SP].iloc[0]) if SP in sub.columns else 0.0
            ph = phase_of(s)
            r = analyze(el, v, hi, lo, sp, dt, ph, S)
            mv = sub[MV].astype(float).to_numpy()
            r["MVmin"], r["MVmax"] = float(np.nanmin(mv)), float(np.nanmax(mv))
            r["MVmean"] = float(np.nanmean(mv))
            r["n_rec"] = int(len(sub))
            r["t0"], r["t1"] = sub["时间"].iloc[0], sub["时间"].iloc[-1]
            r["sp"] = sp
            r["_el"], r["_mv"] = el, mv
            res[(s, p)] = r
            order.append((s, p))

            lbl = f"{s}" + (f"({p})" if p > 1 else "")
            note = ""
            if r["n_rec"] < MIN_N or r["dur"] < MIN_DUR:
                note = f"采样点过少（{r['n_rec']}条/{r['dur']:.1f}min），跳过绘图"
            if r.get("n_switch", 0):
                add = (f"含真空计量程切换 {r['n_switch']} 次（高/低真空计读数差可达上万 {U_PV}），"
                       f"已按切换点分段拟合，跳变不计入速率")
                note = (note + "；" if note else "") + add
            if r["r2"] == r["r2"] and r["r2"] < R2_LOW and not (r["n_rec"] < MIN_N or r["dur"] < MIN_DUR):
                add = f"分段拟合 R²={r['r2']:.2f} 偏低，速率仅供参考"
                note = (note + "；" if note else "") + add
            if (r["segs"] and r["rec"] == r["rec"] and r["endpoint"] == r["endpoint"]
                    and abs(r["rec"]) > 5 * abs(r["endpoint"])):
                dom = max(r["segs"], key=lambda s: abs(s["dT"]))
                dd = dom["t1"] - dom["t0"]
                if r["dur"] > 0 and dd < 0.10 * r["dur"]:
                    add = (f"推荐速率取自短时瞬变段（{dd:.1f}min，占本步 {dd / r['dur'] * 100:.1f}%），"
                           f"看全程趋势请用『端点差速率』")
                    note = (note + "；" if note else "") + add
            stats.append(dict(
                炉次=tag, 阶段=ph, 步号=lbl, 记录数=r["n_rec"], 时长_min=round(r["dur"], 1),
                目标压力=round(r["sp"], 1), 起始真空度=round(r["P0"], 1), 结束真空度=round(r["P1"], 1),
                净变化=round(r["net"], 1), 波动幅度=round(r["amp"], 1) if r["amp"] == r["amp"] else None,
                相对波动=round(r["amp"] / max(abs(r["P0"]), 1e-9), 4) if r["amp"] == r["amp"] else None,
                曲线类型=r["type"], 分段数=r["n_seg"], 真空计切换次数=int(r.get("n_switch", 0)),
                平台起点_min=round(r["hold_t"], 1) if r["hold_t"] is not None else None,
                平台真空度=round(r["hold_T"], 1) if r["hold_T"] == r["hold_T"] else None,
                平台偏离设定=round(r["hold_dev"], 1) if r["hold_dev"] == r["hold_dev"] else None,
                端点差速率=round(r["endpoint"], 2) if r["endpoint"] == r["endpoint"] else None,
                推荐速率=round(r["rec"], 2) if r["rec"] == r["rec"] else None,
                速率含义=RATE_MEAN.get(r["type"], "-"),
                数据可信度=("存疑(含真空计切换)" if r.get("n_switch", 0) else "正常"),
                断点数=int(r.get("n_jump", 0)),
                最大跳变=round(r.get("jump_max", 0.0), 1),
                拟合R2=round(r["r2"], 4) if r["r2"] == r["r2"] else None,
                上升段速率=round(r["rise"], 2) if r["rise"] == r["rise"] else None,
                下降段速率=round(r["fall"], 2) if r["fall"] == r["fall"] else None,
                蝶阀最小=round(r["MVmin"], 2), 蝶阀最大=round(r["MVmax"], 2),
                蝶阀均值=round(r["MVmean"], 2), 备注=note))
            jr = r.get("_jumps", [])
            if jr:
                add = (f"含 {'/'.join(sorted(set(j['kind'] for j in jr)))} "
                       f"{len(jr)} 处（最大跳变 {r['jump_max']:.0f} {U_PV}），已单独列出，不计入拟合速率")
                note = (note + "；" if note else "") + add
            for k, sg in enumerate(r["segs"], 1):
                segrows.append(dict(炉次=tag, 阶段=ph, 步号=lbl, 曲线类型=r["type"], 分段=k,
                                    起_min=round(sg["t0"], 2), 止_min=round(sg["t1"], 2),
                                    起值=round(sg["icpt"], 1), 段时长=round(sg["t1"] - sg["t0"], 2),
                                    段变化=round(sg["dT"], 1), 段速率=round(sg["slope"], 3),
                                    段R2=round(sg["r2"], 4) if sg["r2"] == sg["r2"] else None,
                                    点数=sg["n"], 方向="下降(真空变好)" if sg["slope"] < 0 else "上升(真空变差)"))
            for k, j in enumerate(jr, 1):
                segrows.append(dict(炉次=tag, 阶段=ph, 步号=lbl, 曲线类型=r["type"], 分段=f"突{k}",
                                    起_min=round(j["t0"], 2), 止_min=round(j["t1"], 2),
                                    起值=round(j["icpt"], 1), 段时长=round(j["t1"] - j["t0"], 3),
                                    段变化=round(j["dT"], 1), 段速率=round(j["slope"], 1),
                                    段R2=None, 点数=2, 方向=j["kind"]))
            plist = r.get("_plat", [])
            for k, pl in enumerate(plist, 1):
                segrows.append(dict(炉次=tag, 阶段=ph, 步号=lbl, 曲线类型=r["type"],
                                    分段=("平台" if len(plist) == 1 else f"平台{k}"),
                                    起_min=round(pl["t0"], 2), 止_min=round(pl["t1"], 2),
                                    起值=round(pl["icpt"], 1), 段时长=round(pl["t1"] - pl["t0"], 2),
                                    段变化=round(pl["dT"], 1), 段速率=round(pl["slope"], 3),
                                    段R2=round(pl["r2"], 4) if pl["r2"] == pl["r2"] else None,
                                    点数=pl["n"], 方向=_P["platform"]))
            if _cool_phase(ph):
                for e, val in zip(r["_el"], r["_mv"]):
                    mv_rows.append(dict(炉次=tag, 步号=lbl, 相对时间_min=round(float(e), 3),
                                        数控蝶阀开度反馈=round(float(val), 4)))
        figures.append(plot_overview(tag, label, proc))
        heat = [k for k in order if k[0] < hold]
        cool = [k for k in order if k[0] > hold]
        holdk = [k for k in order if k[0] == hold]
        _span = lambda ks: (f"步{min(k[0] for k in ks)}–{max(k[0] for k in ks)}" if ks else "—")
        figures.append(plot_step_grid(tag, label, proc, res, heat, "pv",
                                      f"{tag}_涂层前分步真空曲线.png", f"涂层前 分步真空度曲线（{_span(heat)}）"))
        figures.append(plot_step_grid(tag, label, proc, res, cool, "pv",
                                      f"{tag}_涂层后分步真空曲线.png", f"涂层后 分步真空度曲线（{_span(cool)}）"))
        figures.append(plot_step_grid(tag, label, proc, res, heat, "mv",
                                      f"{tag}_涂层前分步蝶阀曲线.png", f"涂层前 分步蝶阀开度曲线（{_span(heat)}）"))
        figures.append(plot_step_grid(tag, label, proc, res, cool, "mv",
                                      f"{tag}_涂层后分步蝶阀曲线.png", f"涂层后 分步蝶阀开度曲线（{_span(cool)}）"))
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
    mvdf = pd.DataFrame(mv_rows)
    for f, fn in [(plot_compare(st, "新旧方法速率对比.png"), "新旧"),
                  (plot_type_dist(st, "曲线类型分布.png"), "类型")]:
        if f:
            figures.append(f)

    note = pd.DataFrame([
        ["需求4目标", "把需求3『自动判别曲线类型→选对应算法』的同一套模式，从 温度—功率 平移到 "
                   "真空度(炉内真空) — 蝶阀开度(数控蝶阀开度反馈)"],
        ["变量角色", f"被控量 PV = {PV}（设定值列 {SP}）；操纵量 MV = {MV}（对应需求1.2 的功率曲线）"],
        ["速率单位", f"{U_PV}/min。正=压力升高=真空变差；负=压力降低=真空变好（与温度的升降方向语义相反，读图注意）"],
        ["判据按压力水平取百分比", f"真空量程跨 3 个数量级（约 68 {U_PV} ~ 100k {U_PV}），"
                     f"温度分析中『绝对 ℃ 阈值』的做法不能照搬：在 100 {U_PV} 段与在 42k {U_PV} 段，"
                     f"同等量级的波动含义完全不同。故幅度类判据统一取【本步压力水平的百分比】，"
                     f"斜率类判据取【本步极差/步长】或【相邻斜率比】，全部尺度无关"],
        ["-- 噪声地板", f"eps = {EPS_REL:.0%} × 本步压力水平（用于{_P['band']}下限与转折点最小幅值）"],
        ["-- 平坦判据", f"本步极差 ≤ {FLAT_LVL:.0%} × 本步压力水平 → 全程恒定/停滞"],
        ["-- " + _P["band"], f"max(eps, {BAND_REL:.0%} × 本步极差)"],
        ["-- 转折点幅值", f"max(eps, {TURN_REL:.0%} × 本步极差)"],
        ["-- 平台尾部漂移", f"≤ {TAIL_REL:.0%} × 前段斜率（即真正『停住』而非缓慢漂移）"],
        ["-- 分段门槛", f"新段 RMSE < {RMSE_RATIO:.0%}×旧段；段长≥{SEG_FRAC:.0%}步长；"
                   f"相邻斜率差 ≥ max({SLP_FLOOR:.0%}×本步极差/步长, {SLP_REL:.0%}×较大斜率)；最多 {MAXSEG} 段"],
        ["真空计量程切换", f"炉内真空 由 低真空反馈 / 高真空反馈 拼接，高真空计量程上限 {HI_SAT} {U_PV}。"
                     f"切换处两表读数相差中位数约 1.3 万 {U_PV}，即单采样点假跳变。"
                     f"处理：以切换点为硬断点切 piece，各 piece 独立分析，跳变不计入任何速率；"
                     f"该步类型记为『{T_SWITCH}』并在备注标注切换次数"],
        ["瞬时突变", f"真空会出现『单采样点内压力骤变』（如开阀/泵动作）：判据 单采样点跳变 > "
                 f"max({DISC_REL:.0%}×本步极差, 5×噪声地板)。处理同真空计切换：以跳变点为硬断点分段，"
                 f"跳变单独列为『突变』行（含跳变量与瞬时速率），不计入任何拟合速率；"
                 f"否则它会落在两个拟合段之间被完全漏掉"],
        ["拟合质量提示", f"分段拟合 R² < {R2_LOW} 时，备注提示『速率仅供参考』"],
        ["明细表完整性", f"『分段速率明细』覆盖步内完整时间轴：线性段 + 突变行(突N) + 平台行({_P['platform']})。"
                     "平台行是步末维持恒定的那段（可能占本步绝大部分时长），单独列出以免被漏读"],
        ["推荐速率的读法", "推荐速率默认取『变化量最大段』斜率，用于刻画本步最剧烈的变化；"
                      "若该段时长不足本步 10%，备注会提示改用『端点差速率』看全程趋势"],
        ["类型① 波浪式", f"反复起落。判据：按转折点(幅值≥{TURN_REL:.0%}×本步极差)切出≥3 个单调段，"
                     f"且 |净变化| ≤ {WAVE_NET:.0%}×累计行程，且段数 ≤ {MAXSEG}（段数过多说明是噪声而非波浪）。"
                     f"给 上升/下降速率 + 波动幅度"],
        ["类型② 多段线性式", "同一大方向但斜率明显改变。每段独立最小二乘；推荐速率取『变化量最大段』"],
        ["类型③ 前期线性+后期恒定", f"后期进入平台并维持到步末（本表记为『{T_LINHOLD}』）。速率取『初始值→平台值』的弦速率"],
        ["类型④ 单一线性", "全程近似一条直线。整体最小二乘 + R²"],
        ["类型⑤ 全程恒压/停滞", "极差可忽略或平台起点极早。速率记 0"],
        ["蝶阀开度说明", f"量程 0~100 {U_MV} 左右；实测下限≈0.16、上限≈91.1（执行器机械限位）。"
                     f"阀门全开(≈91)时泵抽速最大→压力最低；关小则压力升高"],
        ["预处理", f"{PV}≤0 或非有限值剔除；~1min 归一化滑动平均；等时重采样至 ≤{NDS} 点"],
        ["绘图跳过", f"记录数<{MIN_N} 或 时长<{MIN_DUR}min 的短步不绘图，仅文字说明"],
        ["炉次切分", "按步号回退点切分；剔除文件尾部属于下一炉次的残段"],
        ["缺失情况", "；".join(
            f"{t}({run_meta[t]['label']}) log 止于步{run_meta[t]['log_last']}"
            f"（recipe 最后一步=步{run_meta[t]['recipe_last']}，其后的结束标记步无 log 记录）"
            for t in sorted(run_meta))
            + ("。" + missing_text([(t, run_meta[t]['label'], run_meta[t]['missing'])
                                   for t in sorted(run_meta)])
               if any(run_meta[t]['missing'] for t in run_meta) else "")],
    ] + ([["人为操作记录（现场确认，非系统异常）",
           "　".join(f"{t}({lb})：{ops_text(t)}" for t, lb, _ in RUNS if ops_text(t))]]
         if any(ops_text(t) for t, *_ in RUNS) else []), columns=["项目", "说明"])
    for nt in notes:
        note.loc[len(note)] = ["炉次说明", nt]
    xls = os.path.join(OUT, "需求4_真空度蝶阀分析.xlsx")
    with pd.ExcelWriter(xls, engine="openpyxl") as w:
        note.to_excel(w, sheet_name="说明", index=False)
        st.to_excel(w, sheet_name="全程分步统计", index=False)
        sg.to_excel(w, sheet_name="分段速率明细", index=False)
        mvdf.to_excel(w, sheet_name="降温段蝶阀开度数据", index=False)
    fmt_xlsx(xls, {"说明": [20, 110],
                   "全程分步统计": [7, 7, 8, 8, 9, 12, 12, 12, 11, 11, 10, 20, 8, 12, 10, 11, 11,
                                    11, 11, 22, 20, 8, 11, 9, 11, 11, 11, 11, 11, 52],
                   "分段速率明细": [7, 7, 8, 20, 6, 10, 10, 10, 10, 10, 10, 8, 16],
                   "降温段蝶阀开度数据": [8, 8, 14, 18]})
    st.to_csv(os.path.join(OUT, "_stats.csv"), index=False, encoding="utf-8-sig")
    print("excel:", xls)
    for f in figures:
        print("fig:", f)
    print()
    for nt in notes:
        print(nt)
    pd.set_option("display.width", 320); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 300)
    print()
    print(st[["炉次", "阶段", "步号", "时长_min", "目标压力", "起始真空度", "结束真空度", "净变化",
              "波动幅度", "曲线类型", "分段数", "真空计切换次数", "端点差速率", "推荐速率",
              "拟合R2", "蝶阀最小", "蝶阀最大", "备注"]].to_string(index=False))


if __name__ == "__main__":
    main()
