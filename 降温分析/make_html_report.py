# -*- coding: utf-8 -*-
"""需求10：把分步分析报告做成单文件交互式 HTML（Plotly.js 离线内嵌）。

设计要点
--------
1. **单一事实来源**：数据取 `降温分析/_runX_all.csv`（与需求3/4/5/6 同源）；
   判型/推荐速率/结论取需求6『分步速率与结论』；拟合段取需求3/需求4『分段速率明细』
   —— 本脚本**不重新拟合**，避免出现第二套口径。
2. **自包含**：plotly.min.js 直接内联，双击即可离线打开，不依赖 CDN/网络。
3. **保极值抽稀**：单步最多保留 N 点，按「分桶取 左轴+右轴 各自的 min/max」抽稀，
   既压缩体积又不丢尖峰（步25 有 6.9 万点）。
4. **实时切换**：一次算好全部步的数据，切步只做 `Plotly.react`，无请求、无重排。
5. **总览页（R11-b）**：与需求6 PPT 的三张总览页对齐——全程温度-功率、全程真空-蝶阀、
   各步速率拟合；外加 KPI 卡与由数据生成的「总览要点」。
6. **汇报口径**：页面只放工艺内容，不出现实现说明；窄窗口自动改上下布局。
7. **步级判定标注（对齐需求6 PPT / xlsx『分步速率与结论』）**：`温度_达标`/`压力_达标`
   两列合成为单一「步级判定态」（达标 / 未达标 / 未达标(过渡) / 不适用），
   在左侧步号按钮上打红点/琥珀点并附底色与阶段计数，总览 KPI 给出未达标步数
   —— 判定值本身**不重算**，仍取自需求6 产物（单一事实来源）。
8. **顶部参数表（2026-10-23 用户选定 A′ 方案）**：分步页顶栏不再是一墙胶囊，改为
   「标题行（阶段·步 + 判定 + 导航）」+「设定 / 实测 两段键值表」，标签灰、值深、列对齐；
   缺项用 `—` 占位（表格里空着会错列）。值只在超容差时着色：温度终点按该步容差
   （恒温 ±10℃ / 线性 5%×行程），时长只对"明显没跑完"（log < recipe 一半）着色——
   log 的分段时长按步号时间戳切分、与 recipe 计划时长不同源，多数步的偏差是切分边界效应，
   按 ±3% 着色会让 17/47 步变红。
9. **过程合理性（逆向回撤，2026-10-23）**：终点达标 ≠ 过程合理——升温步中途大幅回落、之后爬回目标，
   终点照样"达标"。判据 = 回撤幅度 R（℃）与持续时长 D（min）**双门槛**（R≥15 且 D≥5min → 琥珀；
   R≥30 且 D≥10min，或 D/步长>15% → 红），并先清洗三类假信号：温度 0 值哨兵（缺测填充）、
   >30℃/min 的跳变（实测最大 6.7）、步首惯性窗 min(10% 步长,20min)。恒温步不判（与终点判定重叠）。
   左栏用**底边色条**表达，与"未达标"的淡底/左竖条不抢同一通道，故两者可同时显示；
   温度图上把该区间画成淡红竖带，判定可核对。
10. **不画「控制器 PID 设定」（2026-10-23）**：该列不是独立测量，实测是控制器斜坡插值的输出
   （与时间线性拟合 R²=1.0000）：恒温步 ≡ recipe 目标、功率步 ≡ 主控实测、线性步又与拟合段几乎重合，
   画出来只是叠一条线。去掉后图例少一项、单批体积小 0.2 MB。PPT/需求6 逐步图未同批改动。
11. **不画拟合段线（2026-10-23）**：品红"拟合段"只是把需求3/4 已算好的分段速率在图上再画一遍，
    数值（推荐速率 + R²）已完整出现在参数表「速率」与本步结论里，线条叠在实测曲线上只添噪声。
    两图（温度/真空）都不再画线；拟合数据本身仍保留在 JSON 与文字结论中。
12. **短步不纳入分析（2026-10-23）**：`recipe < 2min` 或 `log 记录 < 20 点` 的步，只保留步号与设定值
   （否则按 recipe 核对会跳号），判型/速率/达标/过程一律不产出，结论区只留一句"滞后、不分析"。
   依据：6 批 63 个这样的步全是 1365℃ 恒温段的 1min 微调或 0min 占位，温度变化 <1℃ 而噪声 ±0.5℃；
   时长分布本身也断开（<2min 63 步 / 2–5min 0 步 / ≥5min 206 步），定 2min 不会误伤正常步。

用法
----
    # A. 任意新批次：recipe + log 两个文件（判型/拟合/结论全部按该批次现算）
    python make_html_report.py --recipe 29C261017-recipe.xlsx --log 29C261017-log.xlsx

    # B. 老批次：按 tag 复用既有 需求3/4/6 产物（不重算，与历史报告逐位一致）
    python make_html_report.py run5
    python make_html_report.py --all                    # run1–run6 全量重建

    # 常用选项
    --name 29C261017     指定炉次号（默认从文件名里取）
    --note 中阻          工况备注（显示在副标题）
    --out  D:/临时       输出目录（默认 需求10_交互式HTML报告）
    --sheet 29C260815    手工指定 recipe 工作表（默认取第一个含『步号』的表）
    --open               生成后立即打开
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import numpy as np
import pandas as pd

import project_paths as pp

BASE = pp.ROOT
ANA = pp.code()
OUTDIR = pp.out("需求10_交互式HTML报告")

RUNS = {                    # tag -> (短名称, 工况备注——只写工艺相关，不写实现说明)
    "run1": ("29C260916", "不同转速"),
    "run2": ("29C260926", "中阻"),
    "run3": ("29C260821", ""),
    "run4": ("29C260903", ""),
    "run5": ("29C261011", ""),
    "run6": ("29C261016", ""),
}

N_MAX = 1600                # 每条曲线单步最多点数（保极值抽稀后）
OV_MAX = 3200               # 总览曲线点数上限（全程跨 167 h，太稀会锯齿轮廓）

# 配色与需求6 完全一致（analyze_v6.C）
COL = dict(
    T="#1864ab", PID="#7048e8", TSP="#0ca678", P="#e03131", PMAX="#f76707", PMIN="#846358",
    V="#1864ab", PSP="#0ca678", MV="#e03131", VMAX="#f76707", VMIN="#846358", BAND="#ffd8a8",
    FIT="#d6336c", T1="#15aabf", T2="#9c36b5",
    GRID="#dee2e6", INK="#1f2937", SUB="#6b7280", BG="#f3f4f6",
)
PHASE_COL = {"升温": "#f76707", "恒温": "#1864ab", "降温": "#2f9e44"}

# 判型配色（与 analyze_v6.plot_rate_summary 的 colmap 一致，单一来源）
TYPE_COL = {
    "单一线性": "#4dabf7", "多段线性式": "#748ffc",
    "前期线性+后期恒温": "#ffd43b", "前期线性+后期恒压": "#ffd43b",
    "全程恒温/停滞": "#69db7c", "全程恒压/停滞": "#69db7c",
    "波浪式": "#f783ac", "短步(数据不足)": "#ced4da",
}


def _type_color(t: str) -> str:
    for k, c in TYPE_COL.items():
        if k in (t or ""):
            return c
    return "#adb5bd"


# ------------------------------------------------------------------ 抽稀
def decimate(t: np.ndarray, series: list[np.ndarray], n_max: int = N_MAX):
    """保极值抽稀：按桶保留各右/左轴序列的 min、max 索引 + 首尾点。"""
    n = len(t)
    if n <= n_max:
        return np.arange(n)
    nb = max(60, n_max // 4)
    edges = np.linspace(0, n - 1, nb + 1).astype(int)
    keep = {0, n - 1}
    for i in range(nb):
        a, b = edges[i], edges[i + 1]
        if b <= a:
            continue
        for arr in series:
            seg = arr[a:b]
            m = np.isfinite(seg)
            if not m.any():
                continue
            idx = np.arange(a, b)[m]
            v = seg[m]
            keep.add(int(idx[np.argmin(v)]))
            keep.add(int(idx[np.argmax(v)]))
    return np.array(sorted(keep))


def smooth_series(t: np.ndarray, arrs: list[np.ndarray], n: int = 400):
    """桶均值平滑：仅用于长步（>n 点）的"看趋势"视图，原始极值视图仍保留。"""
    if len(t) <= n:
        return None
    edges = np.linspace(0, len(t), n + 1).astype(int)
    ts, outs = [], [[] for _ in arrs]
    for i in range(n):
        a, b = edges[i], edges[i + 1]
        if b <= a:
            continue
        ts.append(round(float(np.nanmean(t[a:b])), 2))
        for k, arr in enumerate(arrs):
            seg = arr[a:b]
            seg = seg[np.isfinite(seg)]
            outs[k].append(round(float(seg.mean()), 2) if seg.size else None)
    return [ts] + outs


def _clean(arr) -> list:
    """转成 JSON 友好：NaN/inf -> None，数值保留合理位数。"""
    a = pd.to_numeric(pd.Series(arr), errors="coerce").to_numpy(dtype=float)
    out = []
    for v in a:
        out.append(None if not np.isfinite(v) else round(float(v), 2))
    return out


def _sm(t, keys, *arrs):
    """keys 必须显式给出——曾把 2 个序列固定映射成 V/MV，导致温度侧取不到 T/P。"""
    r = smooth_series(t, list(arrs))
    if r is None or len(r) != len(keys) + 1:
        return None
    return {"t": r[0], **{k: r[i + 1] for i, k in enumerate(keys)}}


# ------------------------------------------------------------------ 取数
_LEGACY_HINT = (
    "\n  这是「老批次」模式，复用需求3/4/6 的历史产物；新克隆的仓库里没有这些文件。\n"
    "  单批次请改用现算路径（不依赖任何历史产物）：\n"
    "    python make_report.py --recipe <recipe.xlsx> --log <log.xlsx>\n"
    "  或先跑一遍 需求3→4→6 生成历史产物（会重写跨批次 xlsx，仅维护者使用）。")


def _need(path: str) -> str:
    """老批次模式的可选文件：缺失时给可操作的提示，而不是裸 FileNotFoundError。"""
    if not os.path.exists(path):
        raise SystemExit(f"[找不到] {path}" + _LEGACY_HINT)
    return path


def load_step_table() -> pd.DataFrame:
    """步号必须保留字符串形态：重复 pass 的步写作 '13(2)'，转成数字会被丢掉。"""
    p = _need(pp.out("需求6_分步高清图与汇报PPT", "_step_table.csv"))
    d = pd.read_csv(p, dtype={"步号": str})
    d["_n"] = d["步号"].str.split("(").str[0].astype(int)
    d["_p"] = d["步号"].str.extract(r"\((\d+)\)").fillna(1).astype(int)
    return d


def load_proc(tag: str) -> pd.DataFrame:
    """⚠️ 必须复用 analyze_v5.load_log，不要自己 groupby 步号。

    该函数做了两件本页面不能省略的事：
      1) 只保留「含恒温步25 的主工艺段」及其之前的段落（seg <= main），
         否则步0 会把 run 结束后几天的待机日志也算进来（实测 31265 行 vs 应有 12431 行）；
      2) 标 `pass` 轮次，把重复执行的步拆成 '0' / '0(2)'、'13' / '13(2)'。
    顺带复用需求7+方案C 的温度口径修正（幂等）。
    """
    sys.path.insert(0, ANA)
    import analyze_v5 as v5
    proc, _dropped = v5.load_log(tag)
    return proc


def load_fit_sheets():
    t3 = pd.read_excel(_need(pp.out("需求3_工艺全程分析", "需求3_工艺全程分析.xlsx")),
                       sheet_name="分段速率明细")
    t4 = pd.read_excel(_need(pp.out("需求4_真空度蝶阀分析", "需求4_真空度蝶阀分析.xlsx")),
                       sheet_name="分段速率明细")
    # ⚠️ 步号一律保持**字符串**比对：需求4 明细里重复 pass 写作 '13(2)'，且两表 dtype 不同。
    # 曾用 pd.to_numeric 统一 → '13(2)' 被转成 NaN 丢掉、整数又被写成 '13.0' 对不上，
    # 结果真空拟合段静默全空（fitV=0）。
    for d in (t3, t4):
        d["步号"] = d["步号"].astype(str).str.strip()
    return t3, t4


def _norm_lab(x) -> str:
    s = str(x).strip()
    m = re.fullmatch(r"(\d+)\.0", s)
    return m.group(1) if m else s


# ---- 步级判定态（左栏标注用） ------------------------------------------------
# 取值全部来自需求6『分步速率与结论』的 温度_达标 / 压力_达标 两列，
# 判定本身由 analyze_v5 完成（温度：功率步看功率末值偏差±5% / 恒温步看维持偏差±10℃ /
# 线性步看终点偏差；压力：开度模式看跟随率≥50% / 压力模式看步末是否到达目标），本模块只做合并与着色。
V_OK, V_BAD, V_WARN, V_NA = "ok", "bad", "warn", "na"
_NO_JUDGE = ("不适用", "无对照")     # 该侧不参与判定，不代表"没达标"


def _cell(v) -> str:
    """CSV/Excel 单元格 → 去空白字符串；NaN/None 归空串（否则会变成字面量 "nan"）。"""
    try:
        if v is None or pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _verdict(tok: str, pok: str) -> str:
    """两列判定 → 一个步级态。优先级 未达标 > 未达标(过渡) > 达标 > 不适用。

    「未达标(过渡)」是 recipe 时长内未走完的升/降压过渡步（压力模式②），
    属设计预期而非故障，故单列一档（琥珀），不能和真未达标同色，
    否则每批会多出 1–7 个"假故障"。
    """
    tok, pok = (tok or "").strip(), (pok or "").strip()
    # ⚠️ 不能用精确词白名单：「未达标(过渡)」不在 {达标,未达标} 里，会被整条丢掉，
    #    导致过渡步被错判成"达标"（6 批实测共 31 个过渡步全被吞）。
    vals = [t for t in (tok, pok) if t and t not in _NO_JUDGE]
    if any(t == "未达标" for t in vals):
        return V_BAD
    if any("过渡" in t for t in vals):
        return V_WARN
    if any(t == "达标" for t in vals):
        return V_OK
    return V_NA


def _align_bad(txt: str) -> bool:
    """recipe 与 log 的步号/设定值对齐异常（含 1100℃ 测温通道截断）。"""
    t = txt or ""
    return any(k in t for k in ("滞后", "不一致", "截断"))


def _verdict_tip(tok: str, pok: str, alg: str) -> str:
    """悬停提示：把两列原值与对齐异常拼成一句，便于不切页也知道"为什么未达标"。"""
    it = []
    if tok and tok not in ("不适用", "无对照"):
        it.append("温度 " + tok)
    if pok and pok not in ("不适用", "无对照"):
        it.append("压力 " + pok)
    s = "　".join(it) or "无需判定"
    if _align_bad(alg):
        s += "　｜　recipe 对齐：" + alg
    return s


# ---- 过程合理性：逆向回撤（drawdown） ----------------------------------------
# 终点达标 ≠ 过程合理：升温步中途大幅回落、之后再爬回目标，终点照样"达标"。
# 判据 = 逆向回撤幅度 R（℃）与持续时长 D（min）**双门槛**，缺一不报：
# 单点尖峰过不了 D 的门槛，缓慢漂移过不了 R 的门槛——正好对应"长时间 + 大幅度"。
# 三类假信号必须先清洗（6 批 98 个线性步实测）：
#   ① 温度 0/负数 = 无效哨兵。run3 步37 开头两点为 0，直接算出"回撤 1000.6℃/2min、
#      变率 10003℃/min"，纯属缺测填充值。
#   ② 物理变率上限。全批实测最大变率 6.7℃/min，>30℃/min 一律按跳变剔除。
#   ③ 步首惯性。上一步方向的延续 + 控制器接管滞后（run3 步38 前 22min 跌 10℃ 再回升），
#      在 min(10% 步长, 20min) 窗口内的回撤不计入主判据，单独记 head 供提示。
PM_RATE_MAX = 30.0                    # ℃/min，物理变率上限
PM_MIN_PTS = 20                       # 点数太少不判（与"短步不给速率"同口径）
PM_HEAD_FRAC, PM_HEAD_CAP = 0.10, 20.0
PM_OK, PM_WARN, PM_BAD, PM_NA = "ok", "warn", "bad", "na"

# ---- 短步：不纳入分析 --------------------------------------------------------
# 依据（6 批 269 步实测）：recipe < 2min 的共 63 步，只有两类——
#   ① 步0/0(2)：recipe 时长 0.0min 的占位步；
#   ② 步14–17 / 21–24 / 28–29：1.0min、目标 1365℃、**温度变化全部 <1℃**，
#      而系统噪声本身就有 ±0.5℃，判型"全程恒温/停滞"、判定"达标"等于在测噪声。
# 且设备热响应与数据回传都有滞后，1min 内根本来不及形成有意义的温度变化。
# 界限取 2min 而不是 1min：时长分布本身是断开的（<2min 63 步 / 2–5min **0 步** / ≥5min 206 步），
# 定在 2min 不会碰到任何一个正常步；log<20 点作为数据量兜底（1.5min 步也只有 15–18 点）。
SHORT_RDUR, SHORT_PTS = 2.0, 20


def _process_metrics(g, tm: int, tgt, T0, dur) -> dict:
    """单步过程合理性。返回 pst/pamp/pmin/pfrac/pt0/pt1/pnote。"""
    na = dict(pst=PM_NA, pamp=None, pdur=None, pfrac=None, pt0=None, pt1=None, pnote="")
    if tm == 1:                                   # 功率模式：温度不受控
        return dict(na, pnote="功率模式，温度不受控")

    T = pd.to_numeric(g["主控温度"], errors="coerce").to_numpy(float)
    ts = pd.to_datetime(g["时间"], errors="coerce")
    ok = np.isfinite(T) & (T > 0) & pd.notna(ts).to_numpy()
    if ok.sum() < PM_MIN_PTS or dur is None or dur <= 0:
        return dict(na, pnote="采样点不足")

    t = (ts - ts.iloc[0]).dt.total_seconds().to_numpy(float) / 60.0
    ser = pd.Series(T[ok], index=pd.to_timedelta(t[ok], unit="min")).resample("1min").median()
    ser = ser.interpolate(limit_direction="both")
    Ts = ser.to_numpy(float)
    tsx = ser.index.total_seconds() / 60.0

    if len(Ts) > 2:                               # ② 剔除超过物理变率上限的跳变点
        step = np.abs(np.diff(Ts))
        if step.max() > PM_RATE_MAX:
            Ts = pd.Series(Ts)
            Ts[np.r_[False, step > PM_RATE_MAX]] = np.nan
            Ts = Ts.interpolate(limit_direction="both").to_numpy()

    if tm == 2:
        # 恒温步不做过程判定：①它的"过程"就是"有没有稳在目标"，与终点判定（尾段均值 ±10℃）完全重叠，
        # 实测 6 批里恒温步被判偏离的 4 例，终点判定也全是"未达标"，没有增量；
        # ②降温段的恒温步其实是阶梯节点（目标 1350、实测 1379→1326），"偏离"是连续降温的正常表现，易误读。
        # 过程判定的定位是"补上终点看不见的问题"，恒温步交给终点判定即可。
        return dict(na, pnote="恒温步由终点判定覆盖")

    if tm == 0:
        return dict(na, pnote="无温度模式（占位步）")
    if tm != 3 or tgt is None or T0 is None:
        return na
    span = tgt - T0
    if abs(span) < 20:                            # 近似等温的"线性"步，方向无意义
        return dict(na, pnote="近似等温步，不做方向判定")

    up = span > 0
    head = min(PM_HEAD_FRAC * dur, PM_HEAD_CAP)
    ref, ref_t = Ts[0], tsx[0]
    amp = dm = 0.0
    pt0 = pt1 = None
    head_dd = 0.0
    for i in range(1, len(Ts)):
        if (Ts[i] > ref) if up else (Ts[i] < ref):
            ref, ref_t = Ts[i], tsx[i]
            continue
        dd = (ref - Ts[i]) if up else (Ts[i] - ref)
        if ref_t < head:                          # ③ 步首惯性窗内不计入主判据
            head_dd = max(head_dd, dd)
            continue
        if dd > amp:
            amp, dm, pt0, pt1 = dd, tsx[i] - ref_t, ref_t, tsx[i]

    frac = dm / dur * 100 if dur else 0.0
    bad = (amp >= 30 and dm >= 10) or (amp >= 15 and frac > 15)
    warn = amp >= 15 and dm >= 5
    st = PM_BAD if bad else (PM_WARN if warn else PM_OK)
    note = ""
    if st != PM_OK:
        head_txt = "升温" if up else "降温"
        rev = "回落" if up else "反弹"
        note = f"{head_txt}步中途{rev} {amp:.0f}℃ / {dm:.0f} min（占本步 {frac:.0f}%）"
    elif head_dd >= 30:
        note = f"步首惯性反向 {head_dd:.0f}℃（未计入）"
    return dict(pst=st, pamp=round(amp, 1), pdur=round(dm, 1), pfrac=round(frac, 1),
                pt0=round(pt0, 1) if pt0 is not None else None,
                pt1=round(pt1, 1) if pt1 is not None else None, pnote=note)


def _fit_segments(d: pd.DataFrame, tag: str, lab: str, mode: str):
    """从需求3/4 的分段明细还原拟合线段：y = 起值 + 段速率*(t-起_min)。

    lab 用字符串步号（'13' / '13(2)'）—— 需求4 明细里重复 pass 的步本身带 (2)，
    这样重复步不会误用第一轮的拟合段。
    """
    want = _norm_lab(lab)
    q = d[(d["炉次"].astype(str) == tag) & (d["步号"].map(_norm_lab) == want)]
    segs = []
    for _, r in q.iterrows():
        t0, t1 = r.get("起_min"), r.get("止_min")
        dur, rate = r.get("段时长"), r.get("段速率")
        base = r.get("起温") if mode == "T" else r.get("起值")
        if any(pd.isna(x) for x in (t0, t1, dur, rate, base)):
            continue
        if dur <= 0:
            continue
        y0 = float(base)
        y1 = y0 + float(rate) * float(dur)
        if mode == "V" and (y0 <= 0 or y1 <= 0):
            continue          # 对数轴下不能画 <=0 的拟合线
        segs.append([round(float(t0), 2), round(float(t1), 2),
                     round(y0, 2), round(y1, 2), round(float(rate), 4)])
    return segs[:14]


def build_step(proc: pd.DataFrame, st: pd.Series, t3, t4, tag: str) -> dict:
    n = int(st["_n"])
    p = int(st["_p"])
    lab = str(st["步号"])
    g = proc[(proc["step"] == n) & (proc["pass"] == p)].reset_index(drop=True)
    if g.empty:
        raise ValueError(f"proc 中找不到步次 {lab}")
    if pd.notna(st.get("记录数")) and int(st["记录数"]) != len(g):
        print(f"  ! 步{lab} 记录数不一致：表 {int(st['记录数'])} / 数据 {len(g)}")
    t = (pd.to_datetime(g["时间"], errors="coerce")
         - pd.to_datetime(g["时间"].iloc[0], errors="coerce")).dt.total_seconds() / 60
    t = t.to_numpy(dtype=float)
    t = np.nan_to_num(t, nan=0.0)

    T = pd.to_numeric(g["主控温度"], errors="coerce").to_numpy(float)
    T1 = pd.to_numeric(g["T1温度"], errors="coerce").to_numpy(float)
    T2 = pd.to_numeric(g["T2温度"], errors="coerce").to_numpy(float)
    T2 = np.where(T2 > 0, T2, np.nan)                      # 低温段 T2 读 0 -> 缺测
    # 不读 PID目标温度：实测它是控制器斜坡插值的输出（对时间线性 R²=1.0000），
    # 恒温步≡recipe 目标、功率步≡主控实测、线性步又与拟合段几乎重合，画出来只是叠一条线。
    P = pd.to_numeric(g["电源总功率反馈"], errors="coerce").to_numpy(float)
    V = pd.to_numeric(g["炉内真空"], errors="coerce").to_numpy(float)
    V = np.where(V > 0, V, np.nan)                          # 对数轴：<=0 视为缺测
    MV = pd.to_numeric(g["数控蝶阀开度反馈"], errors="coerce").to_numpy(float)

    i_tp = decimate(t, [T, P])
    i_vm = decimate(t, [V, MV])

    tgt_T = float(st["目标温度"]) if pd.notna(st["目标温度"]) else None
    tgt_P = float(st["目标压力"]) if pd.notna(st["目标压力"]) else None
    pmax = float(st["功率上限"]) if pd.notna(st["功率上限"]) else None
    pmin = float(st["功率下限"]) if pd.notna(st["功率下限"]) else None
    vmax = float(st["蝶阀上限"]) if pd.notna(st["蝶阀上限"]) else None
    vmin = float(st["蝶阀下限"]) if pd.notna(st["蝶阀下限"]) else None

    txt = str(st.get("结论摘要") or "")
    parts = [s.strip() for s in txt.split("｜") if s.strip()]

    # 判定列：CSV 空值读成 NaN，直接 str() 会得到 "nan"，必须先归一
    tok = _cell(st.get("温度_达标"))
    pok = _cell(st.get("压力_达标"))
    alg = _cell(st.get("对齐检测"))

    tm = int(st["温度模式"]) if pd.notna(st.get("温度模式")) else 0
    dur_ = float(st["时长_min"]) if pd.notna(st.get("时长_min")) else None
    rdur_ = float(st["recipe时长_min"]) if pd.notna(st.get("recipe时长_min")) else None
    npts_ = int(st["记录数"]) if pd.notna(st.get("记录数")) else len(g)
    T0_ = float(st["温度_起始"]) if pd.notna(st.get("温度_起始")) else None
    T1e_ = float(st["温度_结束"]) if pd.notna(st.get("温度_结束")) else None

    # 短步：不产出任何实测派生量与判定（口径见 SHORT_RDUR 注释），但步号与设定值保留可追溯
    short = (rdur_ is not None and rdur_ < SHORT_RDUR) or (npts_ < SHORT_PTS)
    short_note = ""
    if short:
        why = (f"recipe 时长 {rdur_:.1f} min（< {SHORT_RDUR:g} min）"
               if (rdur_ is not None and rdur_ < SHORT_RDUR)
               else f"log 仅 {npts_} 个记录点（< {SHORT_PTS}）")
        short_note = f"{why}，设备响应与数据回传滞后，本步不做分步分析"
        tok = pok = "不适用"                       # 判定列不参与，避免噪声被当成结论

    # ---- 顶部参数表的值着色（只影响观感，**不参与任何判定**；判定仍以两列达标值为准）
    # 温度终点容差与 analyze_v5 同口径：恒温模式 ±10℃ / 线性模式 max(10℃, 5%×行程)。
    # 那边改口径时这里必须跟着改 —— 故只在此处算偏差与容差，前端只做大小比较。
    tdev = ttol = None
    if tgt_T is not None and T1e_ is not None and tm in (2, 3):
        tdev = T1e_ - tgt_T
        if tm == 2:
            ttol = 10.0
        else:
            span = tgt_T - (T0_ if T0_ is not None else T1e_)
            ttol = max(10.0, 0.05 * abs(span))
    # 时长偏差（%）：recipe 时长是硬指标，偏差本身即可判断，不需要额外容差口径
    ddur = ((dur_ - rdur_) / rdur_ * 100.0) if (dur_ is not None and rdur_) else None

    # 过程合理性（逆向回撤）：与终点判定正交，终点达标也可能过程走反
    pmet = _process_metrics(g, tm, tgt_T, T0_, dur_)
    if short:
        pmet = dict(pst=PM_NA, pamp=None, pdur=None, pfrac=None,
                    pt0=None, pt1=None, pnote="短步，不做过程判定")

    return dict(
        id=lab,
        n=n,
        p=p,
        phase=str(st.get("阶段") or ""),
        tm=tm,
        tmode=str(st.get("温度模式含义") or ""),
        pm=int(st["压力模式"]) if pd.notna(st.get("压力模式")) else 0,
        pmode=str(st.get("压力模式含义") or ""),
        dur=round(dur_, 1) if dur_ is not None else None,
        rdur=round(rdur_, 1) if rdur_ is not None else None,
        npts=npts_,
        T0=round(T0_, 1) if T0_ is not None else None,
        T1e=round(T1e_, 1) if T1e_ is not None else None,
        tdev=round(tdev, 1) if tdev is not None else None,     # 温度终点偏差
        ttol=round(ttol, 1) if ttol is not None else None,     # 该步温度容差
        ddur=round(ddur, 2) if ddur is not None else None,     # 时长偏差 %
        rate=(None if short else
              (round(float(st["温度_推荐速率"]), 3) if pd.notna(st.get("温度_推荐速率")) else None)),
        ftype="" if short else str(st.get("温度_实测判型") or ""),
        tok=tok,
        vrate=(None if short else
               (round(float(st["真空_推荐速率"]), 3) if pd.notna(st.get("真空_推荐速率")) else None)),
        vtype="" if short else str(st.get("真空_实测判型") or ""),
        pok=pok,
        sh=short,                                    # 短步：不纳入分析（仅展示，不下结论）
        shnote=short_note,
        vd=_verdict(tok, pok),                       # 左栏标注用的步级判定态
        alg=alg if _align_bad(alg) else "",          # 仅保留异常，正常值不占提示位
        vtip=_verdict_tip(tok, pok, alg),
        **pmet,                                      # pst/pamp/pmin/pfrac/pt0/pt1/pnote
        tgtT=tgt_T, tgtP=tgt_P, pmax=pmax, pmin=pmin, vmax=vmax, vmin=vmin,
        ve=round(float(st["真空_结束"]), 1) if pd.notna(st.get("真空_结束")) else None,
        mvmean=round(float(st["蝶阀_均值"]), 2) if pd.notna(st.get("蝶阀_均值")) else None,
        txt=([short_note] if short else parts),
        tp=dict(t=_clean(t[i_tp]), T=_clean(T[i_tp]), T1=_clean(T1[i_tp]),
                T2=_clean(T2[i_tp]), P=_clean(P[i_tp]),
                fit=_fit_segments(t3, tag, lab, "T")),
        vm=dict(t=_clean(t[i_vm]), V=_clean(V[i_vm]), MV=_clean(MV[i_vm]),
                fit=_fit_segments(t4, tag, lab, "V")),
        # 长步（原始点多）额外给一份桶均值序列，供"平滑看趋势"开关使用
        tp_s=_sm(t, ["T", "P"], T, P),
        vm_s=_sm(t, ["V", "MV"], V, MV),
    )


# ------------------------------------------------------------------ 总览（对齐 PPT 三张总览页）
def build_overview(proc: pd.DataFrame, steps: list[dict]) -> dict:
    """全程三视图 + KPI + 要点。x 轴统一为「自工艺开始的累计小时」。"""
    import analyze_v5 as v5

    t0 = proc["时间"].min()
    tt = (proc["时间"] - t0).dt.total_seconds().to_numpy(float) / 3600.0
    T = pd.to_numeric(proc["主控温度"], errors="coerce").to_numpy(float)
    T1 = pd.to_numeric(proc["T1温度"], errors="coerce").to_numpy(float)
    T2 = pd.to_numeric(proc["T2温度"], errors="coerce").to_numpy(float)
    T2 = np.where(T2 > 0, T2, np.nan)
    P = pd.to_numeric(proc["电源总功率反馈"], errors="coerce").to_numpy(float)
    V = pd.to_numeric(proc["炉内真空"], errors="coerce").to_numpy(float)
    V = np.where(V > 0, V, np.nan)
    MV = pd.to_numeric(proc["数控蝶阀开度反馈"], errors="coerce").to_numpy(float)

    i_tp = decimate(tt, [T, P], n_max=OV_MAX)
    i_vm = decimate(tt, [V, MV], n_max=OV_MAX)

    lut = {s["id"]: s for s in steps}
    bands = []
    xt, yt, xp1, yp1, xp2, yp2, xv, yv, xmv, ymv = [], [], [], [], [], [], [], [], [], []
    for (st, ps), g in proc.groupby(["step", "pass"], sort=False):
        lab = f"{st}" if ps <= 1 else f"{st}({ps})"
        a = (g["时间"].min() - t0).total_seconds() / 3600.0
        b = (g["时间"].max() - t0).total_seconds() / 3600.0
        bands.append(dict(t0=round(a, 4), t1=round(b, 4), ph=v5.phase_of(st), lab=lab))
        row = lut.get(lab)
        if not row:
            continue
        if row.get("tgtT"):
            xt += [round(a, 4), round(b, 4), None]
            yt += [row["tgtT"], row["tgtT"], None]
        if row.get("pmax"):
            xp1 += [round(a, 4), round(b, 4), None]
            yp1 += [row["pmax"], row["pmax"], None]
        if row.get("pmax") and row.get("pmin"):
            xp2 += [round(a, 4), round(b, 4), None]
            yp2 += [row["pmin"], row["pmin"], None]
        if row.get("tgtP"):
            xv += [round(a, 4), round(b, 4), None]
            yv += [row["tgtP"], row["tgtP"], None]
        if row.get("vmax"):
            xmv += [round(a, 4), round(b, 4), None]
            ymv += [row["vmax"], row["vmax"], None]
    bands.sort(key=lambda d: d["t0"])

    # ---- KPI
    dur = float(np.nanmax(tt)) if len(tt) else 0.0
    imax = int(np.nanargmax(T))
    Tmax, t_at_max = float(T[imax]), float(tt[imax])
    by_ph = {p: [s for s in steps if s["phase"] == p] for p in PHASE_COL}
    hold_h = sum(s["dur"] for s in by_ph["恒温"] if s["dur"]) / 60.0
    cool = by_ph["降温"]
    cool_h = sum(s["dur"] for s in cool if s["dur"]) / 60.0
    cool_dT = None
    if cool and cool[0].get("T0") is not None and cool[-1].get("T1e") is not None:
        cool_dT = cool[0]["T0"] - cool[-1]["T1e"]
    # ⚠️ cool_h 的单位是**小时**：直接相除得到的是 ℃/h（1215℃/63h=19.3），
    # 换算成 ℃/min 必须再 /60 —— 曾把 0.32 写成 19.29。
    # 取负号以统一符号口径（正=升温 / 负=降温，与 R1/R3 的分步速率一致）；
    # 曾只在 KPI 里手工加「−」，要点里却是正数，同一页出现两个符号。
    cool_rate = (-cool_dT / (cool_h * 60.0)) if (cool_dT and cool_h) else None
    # 用排版减号（−U+2212）而非 ASCII '-'，与页内其它负号视觉一致
    cool_txt = (("−" if cool_rate < 0 else "+") + f"{abs(cool_rate):.2f} ℃/min") if cool_rate else "—"
    vv = V[np.isfinite(V)]
    mv = MV[np.isfinite(MV)]

    # 步级判定汇总（与左栏红点同一口径；「不适用」与「短步」都不计入分母）
    v_bad = [s for s in steps if s["vd"] == V_BAD]
    v_warn = [s for s in steps if s["vd"] == V_WARN]
    v_ok = [s for s in steps if s["vd"] == V_OK]
    n_short = sum(1 for s in steps if s.get("sh"))
    n_jd = len(v_ok) + len(v_warn) + len(v_bad)

    kpi = [
        ["步次", f"{len(steps)}",
         f"升温 {len(by_ph['升温'])} / 恒温 {len(by_ph['恒温'])} / 降温 {len(by_ph['降温'])}"],
        ["未达标步次", f"{len(v_bad)} 步" if v_bad else "全部达标",
         # 四档互斥且合计＝总步数；不做"温度 n / 压力 m"拆分——同一步两侧都未达标会重复计数
         (f"达标 {len(v_ok)} · 过渡 {len(v_warn)} · 短步 {n_short} · "
          f"不适用 {len(steps) - n_jd - n_short} 步" if n_jd else "—"),
         "#e03131" if v_bad else "#2f9e44"],
        ["全程时长", f"{dur:.1f} h", f"{dur / 24:.1f} 天"],
        ["最高主控温度", f"{Tmax:.1f} ℃", f"出现在 {t_at_max:.1f} h"],
        ["恒温时长", f"{hold_h:.1f} h", f"占全程 {hold_h / dur:.0%}" if dur else "—"],
        ["降温段", f"{cool_h:.1f} h", (f"平均 {cool_txt}" if cool_rate else "—")],
        ["真空范围", f"{vv.min():,.0f} ~ {vv.max():,.0f} Pa" if vv.size else "—",
         f"蝶阀 {int(round(mv.min()))}~{int(round(mv.max()))}°" if mv.size else "—"],
    ]

    # ---- 要点（全部由当期数据生成，不写死）
    type_cnt: dict[str, int] = {}
    for s in steps:
        if s["ftype"]:
            type_cnt[s["ftype"]] = type_cnt.get(s["ftype"], 0) + 1
    top = sorted(type_cnt.items(), key=lambda kv: -kv[1])[:3]
    hold_txt = "、".join(
        f"步{s['id']} {s['T0']:.1f}→{s['T1e']:.1f}℃" for s in by_ph["恒温"]
        if s.get("T0") is not None and s.get("T1e") is not None) or "—"
    txt = [
        f"全程 {len(steps)} 个步次、{dur:.1f} h（{dur / 24:.1f} 天）；升温 {len(by_ph['升温'])} 步、"
        f"恒温 {len(by_ph['恒温'])} 步、降温 {len(by_ph['降温'])} 步。",
        f"最高主控温度 {Tmax:.1f}℃（{t_at_max:.1f} h 处）；恒温段 {hold_txt}，累计 {hold_h:.1f} h。",
        (f"降温段 {len(cool)} 步、{cool_h:.1f} h，由 {cool[0]['T0']:.1f}℃ 降到 {cool[-1]['T1e']:.1f}℃，"
         f"平均 {cool_txt}。" if cool_rate else f"降温段 {len(cool)} 步、{cool_h:.1f} h。"),
        "判型分布：" + "、".join(f"{k} {v} 步" for k, v in top) +
        (f"；真空 {vv.min():,.0f}~{vv.max():,.0f} Pa 跨 "
         f"{np.log10(vv.max() / max(vv.min(), 1e-9)):.1f} 个数量级。" if vv.size else "。"),
    ]

    return dict(
        dur=round(dur, 2),
        t=_clean(tt[i_tp]), T=_clean(T[i_tp]), T1=_clean(T1[i_tp]), T2=_clean(T2[i_tp]),
        P=_clean(P[i_tp]),
        tv=_clean(tt[i_vm]), V=_clean(V[i_vm]), MV=_clean(MV[i_vm]),
        xt=xt, yt=yt, xp1=xp1, yp1=yp1, xp2=xp2, yp2=yp2,
        xv=xv, yv=yv, xmv=xmv, ymv=ymv,
        bands=bands,
        kpi=kpi,
        txt=[s for s in txt if s],
        types=[{"k": k, "c": _type_color(k)} for k in type_cnt],
    )


# ------------------------------------------------------------------ 主流程
def build_report(st: pd.DataFrame, proc: pd.DataFrame, segT: pd.DataFrame, segV: pd.DataFrame,
                 batch: str, note: str = "", tag: str = "", outdir: str | None = None,
                 n_steps_recipe: int | None = None) -> str:
    """把「步表 + 过程数据 + 拟合段」装配成单文件 HTML，返回路径。

    入参全部是 DataFrame，因此有两条来源完全兼容：
      · 既有产物（需求6 `_step_table.csv` + 需求3/4『分段速率明细』）—— `main()` 的 tag 模式；
      · 用户任选 recipe+log 时现算的结果（`batch_pipeline.analyze_batch`）—— `--recipe/--log` 模式。
    """
    if "_n" not in st.columns:
        st = st.copy()
        st["_n"] = st["步号"].astype(str).str.split("(").str[0].astype(int)
        st["_p"] = st["步号"].astype(str).str.extract(r"\((\d+)\)").fillna(1).astype(int)

    steps = []
    for _, row in st.sort_values(["_n", "_p"]).iterrows():
        try:
            steps.append(build_step(proc, row, segT, segV, tag or batch))
        except Exception as e:                     # 单步失败不影响整体
            print(f"  ! 步{row['步号']} 跳过：{e}")

    ov = build_overview(proc, steps)
    data = dict(batch=batch, note=note, tag=tag or batch, steps=steps, ov=ov)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    outdir = outdir or OUTDIR
    os.makedirs(outdir, exist_ok=True)
    html = (TEMPLATE
            .replace("/*__DATA__*/", payload)
            .replace("__TITLE__", f"{batch} 工艺分步分析")
            .replace("__BATCH__", batch)
            .replace("__NOTE__", note or "")
            .replace("__PLOTLY__", _plotly_js()))
    out = os.path.join(outdir, f"{batch}_工艺分步分析.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    mb = os.path.getsize(out) / 1048576
    pts = sum(len(s["tp"]["t"]) + len(s["vm"]["t"]) for s in steps)
    print(f"[HTML] {batch}: {len(steps)} 步 / 抽稀后 {pts} 点 + 总览 {len(ov['t'])} 点 / {mb:.2f} MB")
    print(f"[HTML] -> {out}")
    return out


def _legacy_build(tag: str, outdir: str | None = None) -> str:
    """老批次：复用既有 需求3/4/6 产物（不重算，保证与历史报告一致）。"""
    short, note = RUNS[tag]
    st = load_step_table()
    st = st[st["炉次"] == tag].copy()
    proc = load_proc(tag)
    t3, t4 = load_fit_sheets()
    return build_report(st, proc, t3, t4, batch=short, note=note, tag=tag, outdir=outdir)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="make_html_report.py",
        description="生成单文件交互式工艺分步分析 HTML（Plotly 离线内嵌）")
    ap.add_argument("tag", nargs="?", default=None,
                    help=f"老批次 tag（复用需求3/4/6 产物），可选：{', '.join(RUNS)}")
    ap.add_argument("--recipe", help="recipe xlsx（任意批次，绝对/相对路径）")
    ap.add_argument("--log", help="log xlsx（任意批次）")
    ap.add_argument("--name", help="炉次号/短名称（默认从文件名提取）")
    ap.add_argument("--note", default="", help="工况备注，写在副标题")
    ap.add_argument("--out", help="输出目录")
    ap.add_argument("--sheet", help="recipe 工作表名（默认自动定位含『步号』的表）")
    ap.add_argument("--open", action="store_true", help="生成后打开")
    ap.add_argument("--all", action="store_true", help="老批次全量重建 run1–run6")
    a = ap.parse_args(argv)

    if a.recipe or a.log:
        if not (a.recipe and a.log):
            raise SystemExit("--recipe 与 --log 必须同时给出")
        import batch_pipeline as bp
        return bp.generate(a.recipe, a.log, name=a.name, note=a.note, outdir=a.out,
                           sheet=a.sheet, open_file=a.open)

    tags = list(RUNS) if a.all else [a.tag or "run1"]
    if tags[0] not in RUNS:
        raise SystemExit(f"未知批次 {tags[0]}，可选：{', '.join(RUNS)}")
    out = None
    for t in tags:
        out = _legacy_build(t, outdir=a.out)
    return out


def _plotly_js() -> str:
    import plotly
    p = os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js")
    with open(p, encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------ 模板
TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{
    --bg:#f3f4f6; --card:#fff; --ink:#1f2937; --sub:#6b7280; --line:#e5e7eb;
    --blue:#1864ab; --red:#e03131; --green:#2f9e44; --orange:#f76707;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.6 "Microsoft YaHei","PingFang SC",system-ui,-apple-system,sans-serif}
  header{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid var(--line);
         padding:10px 18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  header h1{font-size:16px;margin:0;font-weight:700}
  header .tag{font-size:12px;color:var(--sub)}
  header .spacer{flex:1}

  /* ---------- 两栏骨架：窄窗口自动变一栏 ---------- */
  .wrap{display:grid;grid-template-columns:132px minmax(0,1fr);gap:14px;
        padding:14px 18px 24px;align-items:start}
  aside{position:sticky;top:62px;align-self:start;max-height:calc(100vh - 78px);overflow:auto}
  .phasegrp{margin-bottom:10px}
  .phasegrp h4{margin:0 0 4px;font-size:11.5px;color:var(--sub);font-weight:600;letter-spacing:.5px}
  .phasegrp h4 .nb{float:right;font-style:normal;font-weight:700;font-size:10.5px;line-height:1.5;
                   min-width:15px;text-align:center;padding:0 3px;border-radius:7px;color:#fff}
  .phasegrp h4 .nb.bad{background:#e03131}
  .phasegrp h4 .nb.warn{background:#f59f00}
  .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px}
  .sbtn{position:relative;border:1px solid var(--line);background:#fff;border-radius:6px;cursor:pointer;
        padding:6px 0;font-size:13px;color:var(--ink);transition:.12s;white-space:nowrap;
        overflow:hidden}
  .sbtn:hover{border-color:#adb5bd}
  .sbtn.on{color:#fff;border-color:transparent;font-weight:700}
  .sbtn.on[data-p="升温"]{background:var(--orange)}
  .sbtn.on[data-p="恒温"]{background:var(--blue)}
  .sbtn.on[data-p="降温"]{background:var(--green)}
  .sbtn.ov{width:100%;border-radius:8px;font-weight:600}
  .sbtn.ov.on{background:var(--blue)}
  /* ---------- 步级判定标注：未达标(红) / 未达标(过渡)(琥珀) ----------
     不做角标圆点：4 列网格单列仅 ~30px，'13(2)' 这类重复 pass 步会被圆点压字并撑宽列。
     改用「左侧 inset 竖条 + 淡色底 + 同色边框 + 加粗字」，不占内容盒尺寸，
     选中态底色虽被 .sbtn.on[data-p] 接管（特异性更高），竖条仍保留以示该步未达标。 */
  .sbtn.bad{background:#fff5f5;border-color:#ffc9c9;color:#c92a2a;font-weight:700;
            box-shadow:inset 2px 0 0 #e03131}
  .sbtn.warn{background:#fff9db;border-color:#ffe066;color:#a85800;
             box-shadow:inset 2px 0 0 #f59f00}
  .sbtn.bad.on,.sbtn.warn.on{color:#fff;border-color:transparent;box-shadow:none}
  .sbtn.bad.on{background:#c92a2a}
  .sbtn.warn.on{background:#b35c00}
  /* 长标签（重复 pass 的 '13(2)'）在 30px 列宽里要缩字号才不被裁 */
  .sbtn.tight{font-size:10px;letter-spacing:-.3px}
  /* 过程逆反（与终点判定正交）：画在底边，不与"未达标"的淡底/左竖条抢同一个视觉通道，
     因此"终点未达标 + 过程逆反"能同时显示。 */
  .sbtn.pw:before,.sbtn.pb:before{content:"";position:absolute;left:3px;right:3px;bottom:1px;
            height:2px;border-radius:1px}
  .sbtn.pw:before{background:#f59f00}
  .sbtn.pb:before{background:#e03131}
  .sbtn.pw.on:before,.sbtn.pb.on:before{background:#fff}
  /* 短步（recipe <2min 或 log<20 点）：不纳入分析。灰化但仍可点开看曲线——
     步号必须保留，否则按 recipe 核对时会跳号对不上。 */
  .sbtn.sh{color:#b1b8bf;background:#fafbfc;border-color:#eef0f2;font-weight:400}
  .sbtn.sh:hover{border-color:#adb5bd}
  .sbtn.sh.on{color:#fff}
  /* 图例 + 判定汇总（数量由当期数据生成，不写死） */
  .lgd{display:flex;flex-wrap:wrap;gap:3px 10px;margin:0 0 9px;padding:6px 9px;background:#fff;
       border:1px solid var(--line);border-radius:8px;font-size:11.5px;color:#495057;line-height:1.5}
  .lgd .li{display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
  .lgd .dot{width:7px;height:7px;border-radius:50%;display:inline-block;flex:none}
  .lgd .dot.bad{background:#e03131} .lgd .dot.warn{background:#f59f00} .lgd .dot.ok{background:#2f9e44}
  .lgd .dot.proc{background:#f59f00;width:9px;height:3px;border-radius:1px}
  .lgd .dot.sh{background:#ced4da}
  .lgd b{font-weight:700;color:var(--ink)}

  main{min-width:0;display:flex;flex-direction:column;gap:12px}
  section{display:flex;flex-direction:column;gap:12px;min-width:0}
  /* ⚠️ section 上写了 display:flex 会盖掉 hidden 属性的 UA 样式（display:none），
     导致「总览」与「分步」两页同时显示（深链 ?step= 时实测）。必须显式声明。 */
  section[hidden]{display:none}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px}

  /* ---------- KPI ---------- */
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:10px}
  .kpi{background:#fff;border:1px solid var(--line);border-radius:12px;padding:9px 13px}
  .kpi .k{font-size:12px;color:var(--sub)}
  .kpi .v{font-size:19px;font-weight:700;line-height:1.35;letter-spacing:.2px}
  .kpi .s{font-size:11.5px;color:var(--sub)}

  /* ---------- 分步页顶部参数表：设定 / 实测 两段键值表，判定收进标题行 ---------- */
  .meta{padding:0;overflow:hidden}
  .meta-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
             padding:7px 14px;border-bottom:1px solid #f1f3f5}
  .step-tag{font-size:13px;font-weight:700;letter-spacing:.2px}
  .vset{display:flex;align-items:center;gap:13px;flex-wrap:wrap}
  .vitem{font-size:12px;color:#495057;display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
  .vitem i{width:7px;height:7px;border-radius:50%;display:inline-block;flex:none}
  .vitem b{font-weight:700}
  .vitem.none{color:#adb5bd}
  .kvgrp{display:grid;grid-template-columns:46px minmax(0,1fr);align-items:start;
         padding:6px 14px 7px 0;border-bottom:1px solid #f1f3f5}
  .kvgrp:last-child{border-bottom:0}
  .kvgrp>.h{padding:2px 0 0 11px;font-size:11.5px;font-weight:600;letter-spacing:.4px;color:#6b7280}
  .kvgrp>.h.set{color:#1864ab}
  .kv-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:3px 20px}
  .kv{font-size:12.5px;color:#1f2937;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .kv .k{font-size:12px;color:#6b7280;margin-right:7px}
  .kv .v{font-weight:700}
  .kv .v.warn{color:#e8590c}
  .kv .v.bad{color:#c92a2a}
  .kv .v.na{color:#adb5bd;font-weight:400}
  .nav{display:flex;gap:6px;margin-left:auto}
  .nav button{border:1px solid var(--line);background:#fff;border-radius:8px;padding:4px 10px;
              cursor:pointer;font-size:13px}
  .nav button:hover{background:#f8f9fa}
  .nav button:disabled{opacity:.4;cursor:not-allowed}
  .toolbar{background:#fff;border:1px solid var(--line);border-radius:12px;padding:6px 14px;
           display:flex;align-items:center;gap:12px;font-size:12.5px;color:#343a40}
  .toolbar label{cursor:pointer;user-select:none}

  /* ---------- 图表 ---------- */
  .charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:12px}
  .chartbox{padding:6px 8px 2px;min-width:0}
  .chartbox h3{margin:4px 6px 0;font-size:13.5px;font-weight:700}
  .chartbox h3 span{font-weight:400;color:var(--sub);font-size:12px;margin-left:6px}
  .plot{width:100%;height:clamp(340px,calc(100vh - 455px),700px)}
  .plot.ovmain{height:clamp(300px,calc(100vh - 700px),430px)}
  .plot.ovsub{height:clamp(260px,calc(100vh - 780px),340px)}
  .hint{margin:0 8px 6px;font-size:11.5px;color:#868e96}

  .analysis{padding:10px 16px 14px}
  .analysis h3{margin:2px 0 6px;font-size:13.5px}
  .analysis ul{margin:0;padding-left:18px}
  .analysis li{margin:2px 0}

  /* ---------- 响应式：窄窗口 → 步号栏横置 + 图表上下排 ---------- */
  @media (max-width:1150px){
    /* ⚠️ 必须是 minmax(0,1fr)：步号带 nowrap 的 min-content 会把 1fr 列撑破视口，
       整页内容被推到右侧视野外（实测 980px 宽时全白） */
    .wrap{grid-template-columns:minmax(0,1fr);padding:12px 14px 20px}
    /* 步号栏改为顶部「一条横带 + 横向滚动」，不占竖向空间
       ⚠️ 用 inline-flex + nowrap：光靠 aside{display:flex} 各组会各占一行（实测 3 行 340px）
       ⚠️ 横向滚动只交给 #stepList——若让 aside 自己滚，滚动步号时图例会被一起推出视野 */
    aside{position:static;max-height:none;overflow:visible;display:block;
          padding:6px 2px 8px;border-bottom:1px solid var(--line)}
    aside #stepList{overflow-x:auto;white-space:nowrap}
    aside .phasegrp{display:inline-flex;align-items:center;gap:6px;margin:0 14px 0 0;
                    vertical-align:middle}
    aside .phasegrp h4{margin:0;white-space:nowrap}
    aside .grid{display:inline-flex;gap:4px}
    aside .sbtn{min-width:34px;padding:6px 8px}
    aside .sbtn.ov{width:auto;padding:6px 12px}
    /* 图例在滚动区之外，横向排一行 */
    aside .lgd{display:inline-flex;vertical-align:middle;margin:0 0 6px;padding:4px 9px}
    /* 图表改为上下排列，单图高度收一档，保证仍能一眼看全 */
    .charts{grid-template-columns:1fr}
    /* 参数表 3 列 → 2 列（每格仍装得下「标签 值」） */
    .kv-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:3px 14px}
    .plot,.plot.ovmain,.plot.ovsub{height:clamp(300px,42vh,400px)}
    /* KPI 用定列数，避免 5+1 那种参差不齐的换行 */
    .kpis{grid-template-columns:repeat(3,1fr)}
  }
  @media (max-width:640px){
    header h1{font-size:15px}
    .kpis{grid-template-columns:repeat(2,1fr)}
    .kpi .v{font-size:17px}
    .plot,.plot.ovmain,.plot.ovsub{height:clamp(260px,38vh,340px)}
    /* ⚠️ 这里仍保持两列：单列会把 12 项排成 12 行（620px 实测），两列每列 ~285px 装得下 */
    .kv-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:3px 12px}
    .kvgrp{grid-template-columns:40px minmax(0,1fr);padding-right:12px}
    .kv .k{margin-right:5px}
  }
  @media (max-width:430px){
    .kv-grid{grid-template-columns:minmax(0,1fr)}
  }
</style>
</head>
<body>
<header>
  <h1>__BATCH__ 工艺分步分析</h1>
  <span class="tag" id="bnNote">__NOTE__</span>
  <span class="spacer"></span>
  <span class="tag" id="hdrStep"></span>
</header>

<div class="wrap">
  <aside>
    <div class="phasegrp">
      <h4>总览</h4>
      <button class="sbtn ov" data-id="__ov__" id="ovBtn">总览</button>
    </div>
    <div class="lgd" id="lgd"></div>
    <div id="stepList"></div>
  </aside>

  <main>
    <!-- ================= 总览 ================= -->
    <section id="ovView">
      <div class="kpis" id="kpi"></div>
      <div class="card chartbox">
        <h3>全程 温度 — 功率<span>左轴 ℃ / 右轴 kW　底色＝工艺阶段</span></h3>
        <div id="ovTP" class="plot ovmain"></div>
        <p class="hint" id="ovTpHint" style="display:none"></p>
      </div>
      <div class="charts">
        <div class="card chartbox">
          <h3>全程 炉内真空 — 蝶阀开度<span>左轴 Pa / 右轴 °</span></h3>
          <div id="ovVM" class="plot ovsub"></div>
        </div>
        <div class="card chartbox">
          <h3>各步升降温速率<span>正＝升温 / 负＝降温　柱色＝实测曲线类型</span></h3>
          <div id="ovRate" class="plot ovsub"></div>
        </div>
      </div>
      <div class="card analysis">
        <h3>总览要点</h3>
        <ul id="ovTxt"></ul>
      </div>
    </section>

    <!-- ================= 分步 ================= -->
    <section id="stepView" hidden>
      <div class="card meta" id="meta"></div>
      <div class="toolbar" id="toolbar" style="display:none">
        <label><input type="checkbox" id="smCb"> 平滑看趋势</label>
      </div>
      <div class="charts">
        <div class="card chartbox">
          <h3>温度 — 功率<span>左轴 ℃ / 右轴 kW</span></h3>
          <div id="tp" class="plot"></div>
          <p class="hint" id="tpHint" style="display:none"></p>
        </div>
        <div class="card chartbox">
          <h3>炉内真空 — 蝶阀开度<span>左轴 Pa / 右轴 °</span></h3>
          <div id="vm" class="plot"></div>
        </div>
      </div>
      <div class="card analysis">
        <h3>本步结论</h3>
        <ul id="txt"></ul>
      </div>
    </section>
  </main>
</div>

<script>__PLOTLY__</script>
<script>const DATA = /*__DATA__*/;</script>
<script>
(function(){
  "use strict";
  const OV = "__ov__";
  const steps = DATA.steps, ov = DATA.ov, byId = {}, firstOf = {};
  // 步次 id 形如 '13' / '13(2)'：重复执行的步是两段独立数据，不能只用步号做 key
  steps.forEach(s => { byId[s.id] = s; if (firstOf[s.n] === undefined) firstOf[s.n] = s.id; });
  let cur = OV;                       // 落地即总览（与汇报 PPT 的浏览顺序一致）
  let smoothOn = false, ovDrawn = false;
  const QS = new URLSearchParams(location.search);
  if (QS.get("smooth") === "1") smoothOn = true;
  const qs = QS.get("step");
  if (qs !== null) {
    const want = byId[qs] ? qs : firstOf[parseInt(qs, 10)];
    if (want) cur = want;
  }

  const PH_ORDER = ["升温","恒温","降温"];
  const PHC = { "升温":"#f76707", "恒温":"#1864ab", "降温":"#2f9e44" };

  /* ---------- 左侧步号（含步级判定标注） ---------- */
  // 标注只表达"这一步有没有达到 recipe 设定"，判定值本身来自需求6 产物（不在此重算）
  const VCLS = { bad: " bad", warn: " warn" };
  function stepTitle(s){
    return "步 " + s.id + '　' + (s.tmode || '') +
      '　目标 ' + (s.tgtT == null ? '—' : s.tgtT + '℃') +
      '　时长 ' + (s.dur == null ? '—' : s.dur + ' min') +
      (s.sh ? '　短步不分析（' + (s.shnote || '') + '）'
            : '　判定 ' + (s.vtip || '—') + (s.pnote ? '　过程 ' + s.pnote : ''));
  }
  function buildNav(){
    const host = document.getElementById("stepList");
    const groups = {};
    steps.forEach(s => (groups[s.phase] = groups[s.phase] || []).push(s));
    const order = PH_ORDER.filter(p => groups[p])
      .concat(Object.keys(groups).filter(p => PH_ORDER.indexOf(p) < 0));
    host.innerHTML = order.map(p => {
      const bad = groups[p].filter(s => s.vd === "bad").length;
      const warn = groups[p].filter(s => s.vd === "warn").length;
      const pbad = groups[p].filter(s => s.pst === "bad" || s.pst === "warn").length;
      const btns = groups[p].map(s =>
        '<button class="sbtn' + (VCLS[s.vd] || '') + (s.id.length > 3 ? ' tight' : '') +
        (s.pst === "bad" ? ' pb' : (s.pst === "warn" ? ' pw' : '')) +
        (s.sh ? ' sh' : '') +
        '" data-id="' + s.id + '" data-p="' + p +
        '" title="' + stepTitle(s) + '">' + s.id + '</button>').join("");
      // 阶段标题只放"未达标"计数（过渡/过程逆反不占标题位，交给图例与悬停）
      const tag = bad ? '<i class="nb bad" title="本阶段未达标 ' + bad + ' 步">' + bad + '</i>'
                : (warn ? '<i class="nb warn" title="本阶段过渡步 ' + warn + ' 步">' + warn + '</i>' : '');
      return '<div class="phasegrp"><h4>' + p + '（' + groups[p].length + '）' + tag + '</h4>' +
             '<div class="grid">' + btns + '</div></div>';
    }).join("");
    host.querySelectorAll(".sbtn").forEach(b =>
      b.addEventListener("click", () => go(b.dataset.id)));
    document.getElementById("ovBtn").addEventListener("click", () => go(OV));
  }

  function buildLegend(){
    const nb = steps.filter(s => s.vd === "bad").length;
    const nw = steps.filter(s => s.vd === "warn").length;
    const np = steps.filter(s => s.pst === "bad" || s.pst === "warn").length;
    const ns = steps.filter(s => s.sh).length;
    const nj = steps.filter(s => s.vd !== "na").length;
    const it = ['<span class="li" title="未在 recipe 时长内达到设定值"><i class="dot bad"></i>未达标 <b>' +
                nb + '</b></span>'];
    if (nw) it.push('<span class="li" title="升/降压过渡步：recipe 时长内未走完目标，属设计预期">' +
                    '<i class="dot warn"></i>过渡 <b>' + nw + '</b></span>');
    if (np) it.push('<span class="li" title="按钮底部色条：过程走反（升温步大幅回落 / 降温步大幅反弹）">' +
                    '<i class="dot proc"></i>过程逆反 <b>' + np + '</b></span>');
    if (!nb) it.push('<span class="li"><i class="dot ok"></i>全部达标</span>');
    if (ns) it.push('<span class="li" title="recipe &lt;2min 或 log&lt;20 点：设备响应与数据回传滞后，不做分析">' +
                    '<i class="dot sh"></i>短步 <b>' + ns + '</b></span>');
    // 分母用总步数，与"短步 n"并列后自洽（有判定 + 短步 = 总步数）
    it.push('<span class="li" title="其余为不适用（功率/开度模式下无该侧判定或数据不足）">' +
            '有判定 ' + nj + '/' + steps.length + '</span>');
    document.getElementById("lgd").innerHTML = it.join("");
  }

  /* ---------- 工具 ---------- */
  const fin = a => a.filter(v => v !== null && v !== undefined && isFinite(v));
  const range = (arr, pad) => {
    const v = fin(arr);
    if (!v.length) return null;
    let lo = Math.min.apply(null, v), hi = Math.max.apply(null, v);
    if (hi === lo) { hi = lo + 1; }
    const p = (hi - lo) * (pad == null ? 0.08 : pad);
    return [lo - p, hi + p];
  };
  const median = arr => {
    const v = fin(arr).slice().sort((a, b) => a - b);
    if (!v.length) return null;
    return v[Math.floor(v.length / 2)];
  };
  const fmt = (v, d) => v == null ? "—" : (typeof v === "number" ? v.toFixed(d) : v);
  // automargin: 否则右轴标题会压在刻度数字上（双轴图右侧余量不足时必现）
  const AXIS = { gridcolor: "#e9ecef", zeroline: false, linecolor: "#ced4da", nticks: 6,
                 automargin: true };
  const BASE = {
    margin: { l: 62, r: 96, t: 16, b: 72 },
    hovermode: "x unified",
    showlegend: true,
    legend: { orientation: "h", y: -0.28, x: 0, font: { size: 10.5 } },
    paper_bgcolor: "#fff", plot_bgcolor: "#fff",
    font: { family: '"Microsoft YaHei",system-ui,sans-serif', size: 12.5, color: "#1f2937" },
    dragmode: "zoom"
  };
  const CONF = { responsive: true, displaylogo: false,
                 modeBarButtonsToRemove: ["select2d","lasso2d","autoScale2d"],
                 toImageButtonOptions: { format: "png", scale: 2 } };

  function phaseShapes(bands){
    return bands.map(b => ({ type: "rect", xref: "x", yref: "paper",
      x0: b.t0, x1: b.t1, y0: 0, y1: 1,
      fillcolor: PHC[b.ph] || "#adb5bd", opacity: 0.06, line: { width: 0 }, layer: "below" }));
  }
  function phaseAnns(bands, total){
    const out = [], seen = {};
    bands.forEach(b => {
      const w = b.t1 - b.t0;
      if (w < total * 0.05 || seen[b.ph]) return;
      seen[b.ph] = 1;
      out.push({ xref: "x", yref: "paper", x: (b.t0 + b.t1) / 2, y: 0.99, yanchor: "top",
                 text: b.ph, showarrow: false,
                 font: { size: 11.5, color: PHC[b.ph] } });
    });
    return out;
  }

  /* ================= 总览 ================= */
  function drawOV(){
    document.getElementById("kpi").innerHTML = ov.kpi.map(c =>
      '<div class="kpi"><div class="k">' + c[0] + '</div><div class="v"' +
      (c[3] ? ' style="color:' + c[3] + '"' : '') + '>' + c[1] +
      '</div><div class="s">' + (c[2] || "&nbsp;") + '</div></div>').join("");
    document.getElementById("ovTxt").innerHTML = ov.txt.map(t => "<li>" + t + "</li>").join("");

    const shapes = phaseShapes(ov.bands), anns = phaseAnns(ov.bands, ov.dur);
    const xr = [0, ov.dur];

    /* --- 温度 — 功率 --- */
    const trT = [
      { x: ov.t, y: ov.T, name: "主控温度", type: "scatter", mode: "lines",
        line: { color: "#1864ab", width: 1.9 }, yaxis: "y",
        hovertemplate: "主控 %{y:.1f} ℃<extra></extra>" },
      { x: ov.t, y: ov.T1, name: "T1温度", type: "scatter", mode: "lines",
        line: { color: "#15aabf", width: 0.9, dash: "dash" }, yaxis: "y",
        hovertemplate: "T1 %{y:.1f} ℃<extra></extra>" },
      { x: ov.t, y: ov.T2, name: "T2温度", type: "scatter", mode: "lines",
        line: { color: "#9c36b5", width: 0.9, dash: "dot" }, yaxis: "y",
        hovertemplate: "T2 %{y:.1f} ℃<extra></extra>" },
      { x: ov.xt, y: ov.yt, name: "recipe 目标温度", type: "scatter", mode: "lines",
        line: { color: "#0ca678", width: 1.2, dash: "dash" }, yaxis: "y", connectgaps: false,
        hovertemplate: "目标 %{y:.0f} ℃<extra></extra>" },
      { x: ov.t, y: ov.P, name: "电源总功率反馈", type: "scatter", mode: "lines",
        line: { color: "#e03131", width: 1.2 }, yaxis: "y2",
        hovertemplate: "功率 %{y:.1f} kW<extra></extra>" },
      { x: ov.xp1, y: ov.yp1, name: "recipe 功率上限", type: "scatter", mode: "lines",
        line: { color: "#f76707", width: 1.1, dash: "dash" }, yaxis: "y2", connectgaps: false,
        hovertemplate: "上限 %{y:.0f} kW<extra></extra>" },
      { x: ov.xp2, y: ov.yp2, name: "recipe 功率下限", type: "scatter", mode: "lines",
        line: { color: "#846358", width: 1.1, dash: "dot" }, yaxis: "y2", connectgaps: false,
        hovertemplate: "下限 %{y:.0f} kW<extra></extra>" }
    ];
    const loT = range(fin(ov.T).concat(fin(ov.yt)), 0.06);
    const loP = range(fin(ov.P).concat(fin(ov.yp1)), 0.06);
    Plotly.react("ovTP", trT, Object.assign({}, BASE, {
      shapes: shapes, annotations: anns,
      xaxis: Object.assign({}, AXIS, { title: { text: "自工艺开始 (h)", font: { size: 11 } },
                                       range: xr, nticks: 12 }),
      yaxis: Object.assign({}, AXIS, { title: { text: "温度 (℃)", font: { color: "#1864ab", size: 11 } },
                                       tickfont: { color: "#1864ab" }, range: loT }),
      yaxis2: Object.assign({}, AXIS, { title: { text: "功率 (kW)", font: { color: "#e03131", size: 11 },
                                                 standoff: 24 },
                                        tickfont: { color: "#e03131" }, overlaying: "y", side: "right",
                                        range: loP, showgrid: false })
    }), CONF);

    /* --- 真空 — 蝶阀 --- */
    const trV = [
      { x: ov.tv, y: ov.V, name: "炉内真空", type: "scatter", mode: "lines",
        line: { color: "#1864ab", width: 1.5 }, yaxis: "y",
        hovertemplate: "真空 %{y:.3g} Pa<extra></extra>" },
      { x: ov.xv, y: ov.yv, name: "recipe 目标压力", type: "scatter", mode: "lines",
        line: { color: "#0ca678", width: 1.1, dash: "dash" }, yaxis: "y", connectgaps: false,
        hovertemplate: "目标 %{y:.0f} Pa<extra></extra>" },
      { x: ov.tv, y: ov.MV, name: "数控蝶阀开度", type: "scatter", mode: "lines",
        line: { color: "#e03131", width: 1.2 }, yaxis: "y2",
        hovertemplate: "蝶阀 %{y:.1f} °<extra></extra>" },
      { x: ov.xmv, y: ov.ymv, name: "recipe 开度上限", type: "scatter", mode: "lines",
        line: { color: "#f76707", width: 1.1, dash: "dash" }, yaxis: "y2", connectgaps: false,
        hovertemplate: "上限 %{y:.0f} °<extra></extra>" }
    ];
    const vpos = fin(ov.V);
    const logy = vpos.length > 1 &&
      (Math.max.apply(null, vpos) / Math.max(Math.min.apply(null, vpos), 1e-9)) > 50;
    const mvAll = fin(ov.MV);
    const loM = Math.max(-6, Math.min.apply(null, mvAll) - 6);
    const hiM = Math.min(115, Math.max.apply(null, mvAll) + 6);
    Plotly.react("ovVM", trV, Object.assign({}, BASE, {
      shapes: shapes,
      xaxis: Object.assign({}, AXIS, { title: { text: "自工艺开始 (h)", font: { size: 11 } },
                                       range: xr, nticks: 12 }),
      yaxis: Object.assign({}, AXIS, { title: { text: "真空 (Pa)" + (logy ? "／对数" : ""),
                                                font: { color: "#1864ab", size: 11 } },
                                       tickfont: { color: "#1864ab" },
                                       type: logy ? "log" : "linear",
                                       dtick: logy ? 1 : undefined }),
      yaxis2: Object.assign({}, AXIS, { title: { text: "蝶阀开度 (°)", font: { color: "#e03131", size: 11 },
                                                 standoff: 24 },
                                        tickfont: { color: "#e03131" }, overlaying: "y", side: "right",
                                        range: [loM, hiM], showgrid: false })
    }), CONF);

    /* --- 各步速率（与 PPT 总览第3页一致：只画温度推荐速率，柱色＝实测判型） --- */
    const labs = steps.map(s => s.id);
    const colOf = t => { for (let i = 0; i < ov.types.length; i++) { if (ov.types[i].k === t) return ov.types[i].c; } return "#adb5bd"; };
    const trR = [
      { x: labs, y: steps.map(s => s.rate), type: "bar", name: "温度速率 (℃/min)",
        customdata: steps.map(s => (s.ftype || "—")),
        marker: { color: steps.map(s => colOf(s.ftype)) }, width: 0.74,
        hovertemplate: "步%{x}　%{y:.3f} ℃/min<br>%{customdata}<extra></extra>" }
    ];
    // 判型图例（用空 trace 只借图例色块）
    ov.types.forEach(t => trR.push({ x: [null], y: [null], type: "bar", name: t.k,
      marker: { color: t.c }, hoverinfo: "skip" }));
    const rr = fin(steps.map(s => s.rate));
    // 步次多时（>30）隔几步标一个刻度，否则 47 个标签会糊成一条黑边
    const kLab = Math.max(1, Math.ceil(labs.length / 24));
    const tickvals = labs.filter((v, i) => i % kLab === 0);
    Plotly.react("ovRate", trR, Object.assign({}, BASE, {
      margin: { l: 58, r: 26, t: 16, b: 96 },
      bargap: 0.25,
      showlegend: true,
      legend: { orientation: "h", y: -0.40, x: 0, font: { size: 10 } },
      xaxis: Object.assign({}, AXIS, { tickangle: -90, tickfont: { size: 9.5 },
                                       tickmode: "array", tickvals: tickvals,
                                       title: { text: "步号", font: { size: 11 } } }),
      yaxis: Object.assign({}, AXIS, {
        title: { text: "温度速率 (℃/min)", font: { size: 11 } },
        zeroline: true, zerolinecolor: "#868e96", zerolinewidth: 1,
        range: rr.length ? [Math.min.apply(null, rr) * 1.18, Math.max.apply(null, rr) * 1.18] : null })
    }), CONF);

    // 总览里的 T1/T2 若整条落在主控量程外，同样折叠（不遮挡主线）
    const cT = range(fin(ov.T), 0) || [0, 1];
    const hide = [];
    if (median(ov.T1) != null && (median(ov.T1) < cT[0] || median(ov.T1) > cT[1])) hide.push("T1");
    if (median(ov.T2) != null && (median(ov.T2) < cT[0] || median(ov.T2) > cT[1])) hide.push("T2");
    const hint = document.getElementById("ovTpHint");
    if (hide.length) {
      hint.style.display = "block";
      hint.textContent = hide.join("、") + "温度超出全程量程，已折叠";
    } else { hint.style.display = "none"; }
    ovDrawn = true;
  }

  /* ================= 分步 ================= */
  const posv = v => v != null && isFinite(v) && v > 0;      // recipe 占位值(0)按"无"处理
  const modeTxt = (m, t) => (t || "").replace(/^\s*\d+\s*/, "") || ("模式" + m);
  // 判定配色与需求6 PPT / xlsx 一致：达标绿 / 未达标红 / 未达标(过渡)琥珀 / 数据不足灰
  const vColor = v => v === "达标" ? "#2f9e44"
    : (v.indexOf("过渡") >= 0 ? "#f59f00" : (v === "数据不足" ? "#868e96" : "#e03131"));
  const num = (v, d) => v == null ? "—" : v.toFixed(d);

  // 参数表格：标签灰 + 值深；cls 只给"值"着色，标签恒灰
  function kv(k, v, cls, tip){
    return '<div class="kv"' + (tip ? ' title="' + tip + '"' : '') + '>' +
           '<span class="k">' + k + '</span>' +
           '<span class="v' + (cls ? ' ' + cls : '') + '">' + v + '</span></div>';
  }
  // 时长**不按容差着色**：log 采样 4h、单步常只 20 min，duration 由相邻采样点决定，
  // 实测 47 步里 17 步超 ±3%（+578% / +258% / 一律 -10%），全是分段边界效应，不是工艺偏差。
  // 只给"明显没跑完"着色：log 比 recipe 短一半以上（重复 pass 的 13(2) 只有 0.4/20 min 这种）。
  function lvlDur(p){
    if (p == null || p > -50) return "";
    return p < -90 ? "bad" : "warn";
  }
  // 温度终点：用该步容差倍数（>1 倍琥珀、>1.5 倍红），口径见 build_step 的 tdev/ttol
  function lvlTol(v, tol){
    if (v == null || tol == null) return "";
    const a = Math.abs(v);
    return a > tol * 1.5 ? "bad" : (a > tol ? "warn" : "");
  }
  // 判定段：结论优先，放标题行
  function vitems(s){
    const it = [];
    // 短步：不参与任何判定，只给一句话说明（避免"达标/未达标"把噪声当结论）
    if (s.sh) {
      it.push('<span class="vitem none" title="' + (s.shnote || '') + '">短步 <b>不分析</b></span>');
      return it.join("");
    }
    const one = (label, val, color) =>
      it.push('<span class="vitem" style="color:' + color + '">' +
              '<i style="background:' + color + '"></i>' + label +
              ' <b>' + val + '</b></span>');
    if (s.tok && s.tok !== "不适用") one("温度", s.tok, vColor(s.tok));
    if (s.pok && s.pok !== "不适用") one("压力", s.pok, vColor(s.pok));
    // 过程逆反与终点判定正交：终点达标也可能过程走反，故单独一项
    if (s.pst === "warn" || s.pst === "bad") {
      const c = s.pst === "bad" ? "#e03131" : "#f59f00";
      it.push('<span class="vitem" style="color:' + c + '" title="' + (s.pnote || '') + '">' +
              '<i style="background:' + c + '"></i>过程 <b>逆反 ' +
              Math.round(s.pamp) + '℃ / ' + Math.round(s.pdur) + 'min</b></span>');
    }
    // recipe 与 log 的对齐异常（含 1100℃ 测温通道截断）：单列提示，悬停看全文
    if (s.alg) it.push('<span class="vitem" style="color:#e8590c" title="' + s.alg +
      '"><i style="background:#f59f00"></i>recipe 对齐 <b>需核查</b></span>');
    if (!it.length) it.push('<span class="vitem none">无判定项</span>');
    return it.join("");
  }

  function renderMeta(s){
    const phc = PHC[s.phase] || "#495057";
    // 设定段：recipe 要求的东西；缺项用 — 占位保持列对齐（表格里空着会错列）
    const setHas = [s.tm > 0, posv(s.tgtT), posv(s.pmax), s.pm > 0, posv(s.tgtP), posv(s.vmax)].some(Boolean);
    const setRows = [
      kv("温度控制", s.tm > 0 ? modeTxt(s.tm, s.tmode) : "—", s.tm > 0 ? "" : "na"),
      kv("目标温度", posv(s.tgtT) ? s.tgtT.toFixed(0) + " ℃" : "—", posv(s.tgtT) ? "" : "na"),
      kv("功率区间", posv(s.pmax) ? s.pmin.toFixed(0) + " ~ " + s.pmax.toFixed(0) + " kW" : "—",
         posv(s.pmax) ? "" : "na"),
      kv("压力控制", s.pm > 0 ? modeTxt(s.pm, s.pmode) : "—", s.pm > 0 ? "" : "na"),
      kv("目标压力", posv(s.tgtP) ? s.tgtP.toFixed(0) + " Pa" : "—", posv(s.tgtP) ? "" : "na"),
      kv("蝶阀区间", posv(s.vmax) ? s.vmin.toFixed(0) + " ~ " + s.vmax.toFixed(0) + " °" : "—",
         posv(s.vmax) ? "" : "na")
    ].join("");
    // 实测段：温度/时长的偏差超容差才着色（正常时保持深灰，避免满屏颜色）
    const tTip = s.tdev == null ? "" :
      "终点偏差 " + (s.tdev > 0 ? "+" : "") + s.tdev.toFixed(1) + " ℃ / 容差 ±" + s.ttol.toFixed(1) + " ℃";
    const dTip = s.ddur == null ? "" : "偏差 " + (s.ddur > 0 ? "+" : "") + s.ddur.toFixed(1) + " %";
    const obsRows = [
      kv("时长", num(s.dur, 1) + " / " + (posv(s.rdur) ? s.rdur.toFixed(0) : "—") + " min",
         lvlDur(s.ddur), dTip),
      kv("温度", s.T0 == null ? "—" : s.T0.toFixed(1) + " → " + s.T1e.toFixed(1) + " ℃",
         lvlTol(s.tdev, s.ttol), tTip),
      kv("速率", s.rate == null ? "—" : (s.rate > 0 ? "+" : "") + s.rate.toFixed(3) + " ℃/min",
         s.rate == null ? "na" : ""),
      kv("判型", s.ftype || "—", s.ftype ? "" : "na"),
      kv("真空速率", s.vrate == null ? "—" : (s.vrate > 0 ? "+" : "") + s.vrate.toFixed(1) + " Pa/min",
         s.vrate == null ? "na" : ""),
      kv("蝶阀均值", s.mvmean == null ? "—" : s.mvmean.toFixed(1) + " °", s.mvmean == null ? "na" : "")
    ].join("");

    document.getElementById("meta").innerHTML =
      '<div class="meta-head">' +
        '<span class="step-tag" style="color:' + phc + '">' + s.phase + ' · 步 ' + s.id + '</span>' +
        '<span class="vset">' + vitems(s) + '</span>' +
        '<span class="nav"><button id="prev">← 上一步</button>' +
        '<button id="next">下一步 →</button></span>' +
      '</div>' +
      // 设定段整段无值时不出（步0 这类待机占位步会白占 6 行）
      (setHas ? '<div class="kvgrp"><div class="h set">设定</div><div class="kv-grid">' +
                setRows + '</div></div>' : '') +
      '<div class="kvgrp"><div class="h">实测</div><div class="kv-grid">' + obsRows + '</div></div>';

    document.getElementById("prev").onclick = () => stepBy(-1);
    document.getElementById("next").onclick = () => stepBy(+1);
    const i = steps.findIndex(x => x.id === s.id);
    const pv = document.getElementById("prev"), nx = document.getElementById("next");
    pv.disabled = i <= 0; nx.disabled = i >= steps.length - 1;
    document.getElementById("hdrStep").textContent = "步 " + s.id + " / " + steps.length;
  }

  function drawTP(s){
    const d = s.tp, tr = [];
    const src = (smoothOn && s.tp_s) ? s.tp_s : d;   // 平滑视图只换主控/功率两条
    tr.push({ x: src.t, y: src.T, name: "主控温度", type: "scatter", mode: "lines",
              line: { color: "#1864ab", width: 2.1 }, yaxis: "y",
              hovertemplate: "主控 %{y:.1f} ℃<extra></extra>" });
    const cT = range(d.T, 0) || [0, 1];
    const far = v => { const m = median(v); return m != null && (m < cT[0] || m > cT[1]); };
    tr.push({ x: d.t, y: d.T1, name: "T1温度", type: "scatter", mode: "lines",
              line: { color: "#15aabf", width: 1.1, dash: "dash" }, yaxis: "y",
              visible: far(d.T1) ? "legendonly" : true,
              hovertemplate: "T1 %{y:.1f} ℃<extra></extra>" });
    tr.push({ x: d.t, y: d.T2, name: "T2温度", type: "scatter", mode: "lines",
              line: { color: "#9c36b5", width: 1.1, dash: "dot" }, yaxis: "y",
              visible: far(d.T2) ? "legendonly" : true,
              hovertemplate: "T2 %{y:.1f} ℃<extra></extra>" });
    if (posv(s.tgtT)) tr.push({ x: d.t, y: d.t.map(() => s.tgtT), name: "recipe 目标温度",
              type: "scatter", mode: "lines",
              line: { color: "#0ca678", width: 1.4, dash: "dash" }, yaxis: "y",
              hovertemplate: "目标 %{y:.0f} ℃<extra></extra>" });

    tr.push({ x: src.t, y: src.P, name: "电源总功率反馈", type: "scatter", mode: "lines",
              line: { color: "#e03131", width: 1.6 }, yaxis: "y2",
              hovertemplate: "功率 %{y:.1f} kW<extra></extra>" });
    if (posv(s.pmax)) tr.push({ x: d.t, y: d.t.map(() => s.pmax), name: "recipe 功率上限",
              type: "scatter", mode: "lines", line: { color: "#f76707", width: 1.3, dash: "dash" },
              yaxis: "y2", hovertemplate: "上限 %{y:.0f} kW<extra></extra>" });
    if (posv(s.pmax) && posv(s.pmin)) tr.push({ x: d.t, y: d.t.map(() => s.pmin), name: "recipe 功率下限",
              type: "scatter", mode: "lines", line: { color: "#846358", width: 1.3, dash: "dot" },
              yaxis: "y2", hovertemplate: "下限 %{y:.0f} kW<extra></extra>" });

    const loT = range(d.T.concat(posv(s.tgtT) ? [s.tgtT] : []), 0.10);
    const loP = range(d.P.concat([s.pmax, s.pmin].filter(v => v != null && v > 0)), 0.10);
    const xMaxT = Math.max.apply(null, fin(d.t).concat([1]));
    // 被判为"过程逆反"的区间直接画在图上，判定有据可查（否则用户无法核对阈值）
    const pShapes = (s.pt0 != null && (s.pst === "bad" || s.pst === "warn")) ? [{
      type: "rect", xref: "x", yref: "paper", x0: s.pt0,
      x1: Math.min(s.pt1 == null ? xMaxT : s.pt1, xMaxT),
      y0: 0, y1: 1, fillcolor: "#e03131", opacity: 0.07, line: { width: 0 }, layer: "below"
    }] : [];
    Plotly.react("tp", tr, Object.assign({}, BASE, {
      shapes: pShapes,
      yaxis: Object.assign({}, AXIS, { title: { text: "温度 (℃)", font: { color: "#1864ab", size: 11 } },
                                      tickfont: { color: "#1864ab" }, range: loT,
                                      tickformat: (loT && loT[1] - loT[0] < 12) ? ".1f" : ".0f" }),
      yaxis2: Object.assign({}, AXIS, { title: { text: "功率 (kW)", font: { color: "#e03131", size: 11 },
                                                 standoff: 24 },
                                       tickfont: { color: "#e03131" }, overlaying: "y", side: "right",
                                       range: loP, showgrid: false }),
      xaxis: Object.assign({}, AXIS, { title: { text: "本步累计时间 (min)", font: { size: 11 } },
                                       tickformat: ".0f", nticks: 9,
                                       range: [0, xMaxT] })
    }), CONF);

    const hidden = [];
    if (far(d.T1)) hidden.push("T1");
    if (far(d.T2)) hidden.push("T2");
    const hint = document.getElementById("tpHint");
    if (hidden.length){
      // 只说明"为什么少了一条线"，一句话，不写操作手册
      const m1 = median(d.T1), m2 = median(d.T2);
      hint.style.display = "block";
      hint.textContent = hidden.join("、") + "温度超出本步量程，已折叠（" +
        (hidden.indexOf("T1") >= 0 ? "T1 中位 " + fmt(m1, 1) + "℃" : "") +
        (hidden.length === 2 ? "，" : "") +
        (hidden.indexOf("T2") >= 0 ? "T2 中位 " + fmt(m2, 1) + "℃" : "") + "）";
    } else {
      hint.style.display = "none";
    }
  }

  function drawVM(s){
    const d = s.vm, tr = [];
    const src = (smoothOn && s.vm_s) ? s.vm_s : d;
    const pos = fin(d.V);
    const logy = pos.length > 1 && (Math.max.apply(null, pos) / Math.max(Math.min.apply(null, pos), 1e-9)) > 50;
    tr.push({ x: src.t, y: src.V, name: "炉内真空", type: "scatter", mode: "lines",
              line: { color: "#1864ab", width: 1.9 }, yaxis: "y",
              hovertemplate: "真空 %{y:.3g} Pa<extra></extra>" });
    if (posv(s.tgtP)) tr.push({ x: d.t, y: d.t.map(() => s.tgtP), name: "recipe 目标压力",
              type: "scatter", mode: "lines", line: { color: "#0ca678", width: 1.4, dash: "dash" },
              yaxis: "y", hovertemplate: "目标 %{y:.0f} Pa<extra></extra>" });
    tr.push({ x: src.t, y: src.MV, name: "数控蝶阀开度", type: "scatter", mode: "lines",
              line: { color: "#e03131", width: 1.6 }, yaxis: "y2",
              hovertemplate: "蝶阀 %{y:.1f} °<extra></extra>" });
    if (posv(s.vmax)) tr.push({ x: d.t, y: d.t.map(() => s.vmax), name: "recipe 开度上限",
              type: "scatter", mode: "lines", line: { color: "#f76707", width: 1.3, dash: "dash" },
              yaxis: "y2", hovertemplate: "上限 %{y:.0f} °<extra></extra>" });
    if (posv(s.vmax) && s.vmin != null) tr.push({ x: d.t, y: d.t.map(() => s.vmin), name: "recipe 开度下限",
              type: "scatter", mode: "lines", line: { color: "#846358", width: 1.3, dash: "dot" },
              yaxis: "y2", hovertemplate: "下限 %{y:.0f} °<extra></extra>" });

    // 对数轴不传 range：线性算法加边距会让下界变负（步0 真空 101Pa~102kPa 时实测），
    // Plotly 会把刻度渲染成一整列 "Infinity"；交给 autorange 即可
    const loV = logy ? null
      : range(pos.concat(posv(s.tgtP) ? [s.tgtP] : []), 0.10);
    const mvAll = fin(d.MV).concat([s.vmax, s.vmin].filter(v => v != null));
    let loM = mvAll.length ? Math.min.apply(null, mvAll) : 0;
    let hiM = mvAll.length ? Math.max.apply(null, mvAll) : 100;
    const padM = Math.max(4, (hiM - loM) * 0.12);
    loM = Math.max(-6, loM - padM); hiM = Math.min(115, hiM + padM);

    Plotly.react("vm", tr, Object.assign({}, BASE, {
      yaxis: Object.assign({}, AXIS, {
        title: { text: "真空 (Pa)" + (logy ? "／对数" : ""), font: { color: "#1864ab", size: 11 } },
        tickfont: { color: "#1864ab" }, range: loV, type: logy ? "log" : "linear",
        // 对数轴只标十进制档：否则 Plotly 会把 2/5 的次刻度也写上，出现 "100k / 5 / 0k" 混排
        dtick: logy ? 1 : undefined }),
      yaxis2: Object.assign({}, AXIS, {
        title: { text: "蝶阀开度 (°)", font: { color: "#e03131", size: 11 }, standoff: 24 },
        tickfont: { color: "#e03131" }, overlaying: "y", side: "right",
        range: [loM, hiM], showgrid: false }),
      xaxis: Object.assign({}, AXIS, { title: { text: "本步累计时间 (min)", font: { size: 11 } },
                                       tickformat: ".0f", nticks: 9,
                                       range: [0, Math.max.apply(null, fin(d.t).concat([1]))] })
    }), CONF);
  }

  /* ---------- 切换 ---------- */
  function go(id){
    const isOv = (id === OV);
    document.getElementById("ovView").hidden = !isOv;
    document.getElementById("stepView").hidden = isOv;
    document.querySelectorAll(".sbtn").forEach(b =>
      b.classList.toggle("on", b.dataset.id === id));
    cur = id;
    if (isOv) {
      document.getElementById("hdrStep").textContent =
        steps.length + " 个步次 · " + ov.dur.toFixed(1) + " h";
      if (!ovDrawn) { drawOV(); }
      else { setTimeout(resizeAll, 0); }
    } else {
      const s = byId[id];
      if (!s) return;
      renderMeta(s);
      drawTP(s);
      drawVM(s);
      document.getElementById("txt").innerHTML = (s.txt && s.txt.length)
        ? s.txt.map(t => "<li>" + t + "</li>").join("") : "<li>—</li>";
      // 平滑开关只在"点数多到看不清"的步出现，短步直接隐藏整条工具行，避免占用版面
      const cb = document.getElementById("smCb");
      const bar = document.getElementById("toolbar");
      const can = !!(s.tp_s || s.vm_s) && s.npts > 1600;
      if (bar) bar.style.display = can ? "flex" : "none";
      if (cb) { cb.checked = smoothOn && can; cb.disabled = !can; }
    }
    const active = document.querySelector(".sbtn.on");
    // inline:/nearest/ 必须带：窄屏步号栏是横向滚动带，只给 block 不会把它滚进视野
    if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  function stepBy(k){
    if (cur === OV) { go(k > 0 ? steps[0].id : steps[steps.length - 1].id); return; }
    const i = steps.findIndex(x => x.id === cur);
    const j = Math.min(Math.max(i + k, 0), steps.length - 1);
    if (j !== i) go(steps[j].id);
  }
  function resizeAll(){
    document.querySelectorAll(".plot").forEach(el => {
      if (el.offsetParent && el._fullLayout) {
        try { Plotly.Plots.resize(el); } catch (err) { /* 忽略未初始化 */ }
      }
    });
  }

  document.addEventListener("change", e => {
    if (e.target && e.target.id === "smCb") {
      smoothOn = e.target.checked;
      const s = byId[cur];
      if (s) { drawTP(s); drawVM(s); }
    }
  });

  document.addEventListener("keydown", e => {
    if (e.key === "ArrowLeft") { e.preventDefault(); stepBy(-1); }
    if (e.key === "ArrowRight") { e.preventDefault(); stepBy(+1); }
  });

  // 窗口/容器尺寸变化 → 重排图表：两栏↔上下布局切换时容器尺寸会变，Plotly 需要显式 resize
  if (window.ResizeObserver) {
    let pend = false;
    const ro = new ResizeObserver(() => {
      if (pend) return;
      pend = true;
      requestAnimationFrame(() => { pend = false; resizeAll(); });
    });
    document.querySelectorAll(".plot").forEach(el => ro.observe(el));
  }
  window.addEventListener("resize", () => setTimeout(resizeAll, 120));

  buildNav();
  buildLegend();
  go(cur);
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
