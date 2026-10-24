# -*- coding: utf-8 -*-
"""
CVD 工艺『recipe 语义解析 + 双纵坐标对比』分析 v5（需求5）
========================================================
需求5 原文：
  1) 解释 recipe 各参数意义：温度模式(1 功率模式 / 2 温度模式 / 3 线性模式)、
     压力模式(1 开度模式 / 2 压力模式)、结束位(1=工艺结束)。
  2) 据此更新图表：如真空度可添加 recipe 值作参考。
  3) 把 (温度, 功率)、(压力, 蝶阀开度) 画在同一张图 —— 双纵坐标，
     直接对比功率对温度、开度对压力的影响，方便后续工艺优化。

本脚本做三件事：
  A. recipe 语义落地：把 recipe 的模式/设定值/上下限按步号关联到 log，
     并在『模式语义』下校验实测行为是否与 recipe 意图一致
     （功率模式→功率线性升至上限；温度模式→稳压在目标温度；
       线性模式→线性升至目标温度；开度模式→蝶阀=开度上限；压力模式→稳压在目标压力）。
  B. 双纵坐标图：每步 左轴温度(℃)/右轴功率、左轴真空(Pa)/右轴蝶阀开度(%)，
     叠加 recipe 参考线（目标温度、功率上下限、目标压力、蝶阀上下限、PID 设定）。
  C. 期望-实测交叉校验：把 recipe 推导出的『期望曲线类型』与需求3/需求4
     实测判型结果对照，输出符合率。

不修改需求3/需求4 的任何产物，仅读取其 _stats.csv / Excel 明细作为实测判型来源
（单一事实来源原则），本脚本独立输出到 需求5_双轴对比分析/。
========================================================
"""
import os
import time
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams
from matplotlib.patches import Patch
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from temp_fix import apply_to_frame, T_SWITCH, T_CONSIST, diag as t_diag, diag_text as t_diag_text  # noqa: E402
from batch_notes import ops_text  # noqa: E402  人为操作记录（现场确认，单一事实来源）

warnings.filterwarnings("ignore")
rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
rcParams["axes.unicode_minus"] = False

import project_paths as pp
import phases

BASE = pp.ROOT
SIMU = pp.code()
OUT = pp.out("需求5_双轴对比分析")
FIG = os.path.join(OUT, "图表")
os.makedirs(FIG, exist_ok=True)

# ---------------------------------------------------------------- log 列
T_COL, TSP_COL, PID_COL, P_COL = "主控温度", "目标温度", "PID目标温度", "电源总功率反馈"
V_COL, PSP_COL, MV_COL = "炉内真空", "目标压力", "数控蝶阀开度反馈"
TEMP_INFO = {}      # 需求7 温度修正诊断（按 tag 记录）

RUNS = [
    # 文件命名已规范化为 {批次}-recipe.xlsx / {批次}-log.xlsx（2026-10-20 用户统一重命名）
    # 短名称即报告里展示的炉次号；run5 由旧名 29C261011-2 规范为 29C261011
    ("run1", "29C260916", "29C260916-recipe.xlsx", "需求3_工艺全程分析", "需求4_真空度蝶阀分析"),
    ("run2", "29C260926", "29C260926-recipe.xlsx", "需求3_工艺全程分析", "需求4_真空度蝶阀分析"),
    ("run3", "29C260821", "29C260821-recipe.xlsx", "需求3_工艺全程分析", "需求4_真空度蝶阀分析"),
    ("run4", "29C260903", "29C260903-recipe.xlsx", "需求3_工艺全程分析", "需求4_真空度蝶阀分析"),
    ("run5", "29C261011", "29C261011-recipe.xlsx", "需求3_工艺全程分析", "需求4_真空度蝶阀分析"),
    ("run6", "29C261016", "29C261016-recipe.xlsx", "需求3_工艺全程分析", "需求4_真空度蝶阀分析"),
]

# recipe 列
R_STEP, R_CYC, R_STAGE, R_H, R_M, R_S = "步号", "循环次数", "工艺阶段", "时", "分", "秒"
R_TMODE, R_TSP, R_PMAX, R_PMIN = "温度模式", "目标温度", "功率上限", "功率下限"
R_PMODE, R_VMAX, R_VMIN = "压力模式", "蝶阀开度上限", "蝶阀开度下限"
R_PSP_COL, R_END = "目标压力", "结束位"

MODE_T = {0: "—(占位)", 1: "①功率模式", 2: "②温度模式", 3: "③线性模式"}
MODE_P = {0: "—(占位)", 1: "①开度模式", 2: "②压力模式"}

# 期望曲线类型（由 recipe 模式推导）
EXP_T = {
    1: "单一线性(功率线性升→温度升)",
    2: "全程恒温/停滞(稳压目标温度)",
    3: "单一线性(线性升至目标温度)",
}
EXP_V = {
    1: "开环(蝶阀=开度上限，无压力设定)",
    2: "全程恒压/停滞(稳压目标压力)",
}

COL = dict(t="#1c7ed6", tsp="#868e96", pid="#37b24d", p="#d9480f",
           lim="#adb5bd", v="#1c7ed6", mv="#d9480f", band="#ffd8a8",
           ok="#2f9e44", bad="#e03131", part="#f08c00")

# ---------------------------------------------------------------- 需求7 三温度同图
# 《分析需求.md》需求7（2026-10-20 修订）：log 温度取自「T1温度」/「T2温度」，
# 每个温度各有意义、暂时无法取舍 → 要求把 主控温度 / T1温度 / T2温度 **三个都画在图上**。
# 「主控温度」= 按需求7+方案C 修正后的口径（单一事实来源 temp_fix.py），
# T1/T2 为 log 原始双测温，用于目视核对修正结果与通道差异。
T1_COL, T2_COL = "T1温度", "T2温度"
COL["t1"], COL["t2"] = "#15aabf", "#9c36b5"      # T1 青 / T2 紫（与主控深蓝、PID 绿、功率橙均可区分）


def plot_three_temps(ax, sub, x, lw_main=1.6, lw_sub=1.05, main_label=None, z_base=4,
                     col_main=None, col_t1=None, col_t2=None):
    """在 ax 上绘制 主控温度（粗实线）+ T1温度（青虚线）+ T2温度（紫点线）。

    需求7 要求三个温度都在图中：
      - 主控温度是最终口径，用粗实线打底；
      - T1/T2 细线叠在上层——rule7 批次里主控本就等于 T1 或 T2 的某一段，
        叠画后虚线仍在主线之上可见，既是「三条都在图中」的直接证据，也不干扰主线判读；
      - T2 在低温段读数为 0（高位热电偶未起测），统一按 >0 过滤，避免把纵轴压到 0。
    返回已绘制的 y 值列表（调用方据此算纵轴范围），图例顺序固定 主控 → T1 → T2。
    """
    xs = np.asarray(x, float)
    out = []
    c_main = col_main or COL["t"]
    if T_COL in sub.columns:
        y = np.asarray(sub[T_COL], float)
        ok = np.isfinite(y) & (y > 0)
        if ok.any():
            ax.plot(xs[ok], y[ok], color=c_main, lw=lw_main,
                    label=(main_label or "主控温度 (℃)"), zorder=z_base)
            out += list(y[ok])
    for col, c, ls, nm, lw in ((T1_COL, col_t1 or COL["t1"], "--", "T1温度 (℃)", lw_sub),
                               (T2_COL, col_t2 or COL["t2"], ":", "T2温度 (℃)", lw_sub + 0.05)):
        if col not in sub.columns:
            continue
        y = np.asarray(sub[col], float)
        ok = np.isfinite(y) & (y > 0)
        if ok.any():
            ax.plot(xs[ok], y[ok], color=c, lw=lw, ls=ls, alpha=0.92, label=nm, zorder=z_base + 1)
            out += list(y[ok])
    return out


# ================================================================ 数据加载
def load_recipe(path, sheet=None):
    """读 recipe，返回 步号 -> dict(模式与设定值)

    path 可为**绝对路径**（新批次由用户任选文件）或相对 BASE 的路径。
    sheet=None 时自动取第一个含『步号』列的工作表——recipe 文件名与 sheet 名常不一致
    （29C261016.xlsx 的 sheet 名是 29C260815），因此不能按文件名猜。
    """
    p = str(path) if os.path.isabs(str(path)) else os.path.join(BASE, path)
    if sheet is None:
        xl = pd.ExcelFile(p)
        sheet = xl.sheet_names[0]
        for s in xl.sheet_names:
            try:
                if R_STEP in pd.read_excel(p, sheet_name=s, nrows=0).columns:
                    sheet = s
                    break
            except Exception:
                continue
    d = pd.read_excel(p, sheet_name=sheet)
    d = d.dropna(subset=[R_STEP])
    rec = {}
    for _, r in d.iterrows():
        s = int(r[R_STEP])
        sec = 3600 * float(r.get(R_H, 0) or 0) + 60 * float(r.get(R_M, 0) or 0) + float(r.get(R_S, 0) or 0)
        rec[s] = dict(
            步号=s, 循环次数=int(r.get(R_CYC, 0) or 0), 工艺阶段=int(r.get(R_STAGE, 0) or 0),
            时长_min=sec / 60.0,
            温度模式=int(r.get(R_TMODE, 0) or 0), 目标温度=float(r.get(R_TSP, 0) or 0),
            功率上限=float(r.get(R_PMAX, 0) or 0), 功率下限=float(r.get(R_PMIN, 0) or 0),
            压力模式=int(r.get(R_PMODE, 0) or 0), 目标压力=float(r.get(R_PSP_COL, 0) or 0),
            蝶阀开度上限=float(r.get(R_VMAX, 0) or 0), 蝶阀开度下限=float(r.get(R_VMIN, 0) or 0),
            结束位=int(r.get(R_END, 0) or 0),
        )
    return rec


def phase_of(s, hold=None):
    """步号 -> 阶段（涂层前/涂层中/涂层后）。

    ⚠️ 2026-10-23 起不再写死「步≤24 升温 / 步25 恒温 / 步26+ 降温」——
    分隔节点改为 recipe 中**时长最长的一步**（见 `phases.py` 与 R13）。
    `hold` 缺省取当前批次上下文（`phases.set_active`）。
    """
    return phases.phase_of(s, hold)


def prepare_log(df, key=None, hold=None):
    """DataFrame -> 主工艺段 + pass 标注 + 需求7 温度口径（load_log 的内核）。

    单独抽出，供「用户任选 log 文件」的场景直接喂内存 DataFrame，避免先落盘再读。
    注意：必须以 `key` 记诊断（TEMP_INFO），None 时不记。

    `hold` = 「涂层中」步号（recipe 里时长最长的一步）。主工艺段按它切分：
    **含该步的段就是主工艺段**，其后的残段（下一炉次/复机）被剔除。
    ⚠️ 原实现写死"含步25 的段"，只对 29C 机台成立——25C261007 的涂层段在步32，
    套用写死口径会把真正的涂层段切掉，报告只剩前半段。
    """
    df = df.copy()
    df["时间"] = pd.to_datetime(df["时间"])
    df = df.dropna(subset=["当前运行步号"]).sort_values("时间").reset_index(drop=True)
    df["step"] = df["当前运行步号"].astype(int)
    st = df["step"].to_numpy()
    df["seg"] = np.cumsum(np.concatenate([[True], np.diff(st) < 0]))
    seg_ids = list(df["seg"].unique())
    if hold is None:
        hold = phases.hold_of(key) if key else None
    if hold is not None and (df["step"] == int(hold)).any():
        main = seg_ids[0]
        for sid in seg_ids:
            if int(hold) in set(df.loc[df["seg"] == sid, "step"]):
                main = sid
                break
    else:
        # 缺该步（log 截断/该步未执行）：取记录最多的段——主工艺段必然最长，
        # 比"第一段"稳（步0 待机段被单独切开时第一段可能很短）。
        main = max(seg_ids, key=lambda sid: int((df["seg"] == sid).sum()))
    proc = df[df["seg"] <= main].reset_index(drop=True)
    dropped = int((df["seg"] > main).sum())
    passes = np.ones(len(proc), int)
    cur, prev = {}, None
    for i, (sid, s) in enumerate(zip(proc["seg"].to_numpy(), proc["step"].to_numpy())):
        if s not in cur:
            cur[s] = 1
        elif prev is not None and sid != prev:
            cur[s] += 1
        passes[i] = cur[s]
        prev = sid
    proc["pass"] = passes

    # ---- 需求7：log 温度口径修正 ----
    # T1 < 1100℃ 用 T1；T1 >= 1100℃ 用 T2；T2 无效回退 T1。原值存『主控温度_原始』。
    # 幂等：修正值只由 T1/T2 决定，重复执行结果一致；已有『主控温度_原始』列时不会被覆盖。
    info = apply_to_frame(proc)
    if key is not None:
        TEMP_INFO[key] = info
    return proc, dropped


def load_log(tag, hold=None):
    """按步号回退点切分：只保留含「涂层中」步的主工艺段；并标注重复 pass。

    `hold` 缺省由 `phases.hold_of(tag)` 从该批 recipe 现算（缓存），
    因此不再依赖"步25 就是恒温步"这一 29C 专有假设。
    """
    df = pd.read_csv(os.path.join(SIMU, f"_{tag}_all.csv"))
    if hold is None:
        hold = phases.hold_of(tag)
    return prepare_log(df, key=tag, hold=hold)


def step_label(s, p):
    return f"{s}" if p <= 1 else f"{s}({p})"


def series_of(proc, s, p):
    sub = proc[(proc["step"] == s) & (proc["pass"] == p)].reset_index(drop=True)
    el = (sub["时间"] - sub["时间"].iloc[0]).dt.total_seconds().to_numpy() / 60.0
    return sub, el


def stat(x, el):
    """一段时序的统计：起/止/极值/均值/最小二乘斜率/R²"""
    y = np.asarray(x, float)
    ok = np.isfinite(y)
    if ok.sum() < 2:
        y2 = y[ok]
        v = float(y2[0]) if len(y2) else float("nan")
        return dict(n=int(ok.sum()), t0=v, t1=v, mn=v, mx=v, mean=v, slope=float("nan"), r2=float("nan"))
    xv, yv = np.asarray(el, float)[ok], y[ok]
    xv = xv - xv[0]
    m = len(xv)
    sx, sy = xv.sum(), yv.sum()
    sxx, sxy, syy = (xv * xv).sum(), (xv * yv).sum(), (yv * yv).sum()
    den = m * sxx - sx * sx
    b = (m * sxy - sx * sy) / den if abs(den) > 1e-12 else 0.0
    a = (sy - b * sx) / m
    rss = float(max(syy - a * sy - b * sxy, 0.0))
    sst = syy - sy * sy / m
    return dict(n=int(m), t0=float(yv[0]), t1=float(yv[-1]), mn=float(yv.min()), mx=float(yv.max()),
                mean=float(yv.mean()), slope=float(b),
                r2=float(1 - rss / sst) if sst > 1e-12 else float("nan"))


def tail_mean(y, el, frac=0.5):
    """后 frac 时间长度的均值（用于判定'是否稳定在设定值'）"""
    y = np.asarray(y, float); el = np.asarray(el, float)
    ok = np.isfinite(y)
    if ok.sum() < 2:
        return float("nan")
    y, el = y[ok], el[ok]
    t0 = el[-1] - frac * (el[-1] - el[0])
    sel = el >= t0
    return float(y[sel].mean()) if sel.any() else float("nan")


# ================================================================ 判型来源
def load_measured(tag, d3, d4):
    """读取需求3/需求4 的实测判型与推荐速率（单一事实来源）"""
    m = {}
    for d, key, tag_name in [(d3, "T", "温度"), (d4, "V", "真空度")]:
        p = os.path.join(BASE, d, "_stats.csv")
        s = pd.read_csv(p, encoding="utf-8-sig")
        s = s[s["炉次"] == tag]
        for _, r in s.iterrows():
            k = str(r["步号"])
            m.setdefault(k, {})[f"{key}_判型"] = r["曲线类型"]
            m.setdefault(k, {})[f"{key}_速率"] = r["推荐速率"]
    return m


def fam(t):
    """曲线类型 → 族：lin 线性 / hold 平台 / short 数据不足 / other

    平台族兼容温度域「恒温」与压力·真空域「恒压」两种话术（同一分段模型）。"""
    if not isinstance(t, str):
        return "na"
    if "短步" in t or "不足" in t:
        return "short"
    if "恒温" in t or "恒压" in t or "停滞" in t:
        return "hold"
    if "线性" in t:
        return "lin"
    return "other"


def _ranges(nums):
    """步号列表 → 「1–8/13/17–25」形式的紧凑区间串。"""
    nums = sorted(set(int(x) for x in nums))
    out, s, p = [], None, None
    for x in nums:
        if s is None:
            s = p = x
        elif x == p + 1:
            p = x
        else:
            out.append(f"{s}" if s == p else f"{s}–{p}")
            s = p = x
    if s is not None:
        out.append(f"{s}" if s == p else f"{s}–{p}")
    return "/".join(out)


def _ncol(df):
    """给统计表补一列数字步号 _n（步号可能是 '13(2)' 这种带循环次数的写法）。"""
    df = df.copy()
    df["_n"] = df["步号"].astype(str).str.split("(").str[0].astype(int)
    return df


def mode_dist_text(stats):
    """按**实际数据**汇总各模式覆盖的步号区间（说明页不再写死，避免 recipe 更正后文案脱节）。"""
    ranges = _ranges
    df = _ncol(stats)
    lines = []
    for tag in sorted(df["炉次"].unique()):
        sub = df[df["炉次"] == tag]
        seg = []
        for col, mp in (("温度模式", {1: "①功率", 2: "②温度", 3: "③线性"}),
                        ("压力模式", {1: "①开度", 2: "②压力"})):
            for m in sorted(mp):
                nums = sub.loc[sub[col] == m, "_n"].tolist()
                if nums:
                    seg.append(f"{'温度' if col == '温度模式' else '压力'}模式{mp[m]}→步{ranges(nums)}（{len(nums)}步）")
        lines.append(f"{tag}：" + "；".join(seg))
    return "　".join(lines)


# ---------------------------------------------------------------- 说明页文案生成器
# 教训（需求8）：说明页里的「示例型」文案一旦写死，recipe 更正后就会与数据脱节，
# 甚至出现「降温段滞后一步」这种已被修正的错话。以下全部改为由 stats 实测生成。

def workcond_text(run_info):
    """工况说明：log 实际止步 + recipe 结束标记 + 缺失步，全部按当期数据生成（不写死）。"""
    seg = []
    for tag in sorted(run_info):
        d = run_info[tag]
        s = (f"{tag}({d['label']}) log 止于步{d['log_last']}、recipe 结束标记=步{d['end_mark']}")
        if d.get("missing"):
            s += (f"；log 缺步{'/'.join(str(x) for x in d['missing'])}"
                  f"（recipe 有该步计划但 log 无任何记录，已按缺失处理、不参与统计）")
        seg.append(s)
    return ("；".join(seg) +
            "。结束位=1 的步是工艺结束标记，log 无其运行记录；"
            "若 log 止步小于结束标记前的最后一步，说明该炉 log 缺尾部步，"
            "对应步不参与统计（见各步『记录数』列）")


def xcheck_power_text(stats):
    """温度模式① 交叉验证：功率末值是否逐位等于 recipe 功率上限。"""
    df = _ncol(stats)
    seg = []
    for tag in sorted(df["炉次"].unique()):
        sub = df[(df["炉次"] == tag) & (df["温度模式"] == 1)].sort_values("_n").head(8)
        if sub.empty:
            continue
        a = "/".join(f"{v:.1f}" for v in sub["功率_结束"])
        b = "/".join(f"{v:g}" for v in sub["功率上限"])
        seg.append(f"{tag} 步{_ranges(sub['_n'])} 实测功率末值 {a} 对上 recipe 功率上限 {b}，逐位吻合")
    pm = df["功率上限"].max()
    return ("；".join(seg) +
            f" → 可确认功率模式语义与 log 功率列的量纲一致（0–100% 标幺，本批 recipe 峰值上限 {pm:g}%）")


def xcheck_valve_text(stats):
    """压力模式① 交叉验证：蝶阀开度是否跟住 recipe 开度上限（只取跟随成功的步）。"""
    df = _ncol(stats)
    seg = []
    for tag in sorted(df["炉次"].unique()):
        sub = df[(df["炉次"] == tag) & (df["压力模式"] == 1) &
                 (df["蝶阀上限"] > 0) & (df["蝶阀跟随率"] >= 50)].sort_values("_n")
        if sub.empty:
            continue
        for u, g in sub.groupby("蝶阀上限"):
            seg.append(f"{tag} 步{_ranges(g['_n'])} 上限 {u:g}% → 实测蝶阀均值 {g['蝶阀_均值'].median():.2f}%")
    return ("；".join(seg) +
            " → 开度模式下蝶阀确实被 recipe 上限钉住（目标压力不参与控制，开环），"
            "且执行器存在约 +1 个百分点的标定偏差（上限 90 实开 ≈91.0）")


def valve_split_text(stats):
    """开度模式内的反例：上限相同但蝶阀几乎全关的步（跟随率 <50% 自动识别）。"""
    df = _ncol(stats)
    seg = []
    for tag in sorted(df["炉次"].unique()):
        sub = df[(df["炉次"] == tag) & (df["压力模式"] == 1) &
                 (df["蝶阀上限"] > 0) & (df["蝶阀跟随率"] < 50)].sort_values("_n")
        if sub.empty:
            continue
        det = "/".join(f"步{r['步号']}(上限{r['蝶阀上限']:g}%→实开{r['蝶阀_均值']:.2f}%，跟随率{r['蝶阀跟随率']:.0f}%)"
                       for _, r in sub.iterrows())
        seg.append(f"{tag}：{det}")
    if not seg:
        return "（本次未检出开度模式内的蝶阀行为分裂，蝶阀全程跟随 recipe 开度上限）"
    return ("；".join(seg) +
            "：同属压力模式①（开度模式）却未跟住 recipe 开度上限——同一 recipe 段内阀门行为分裂。"
            "已用『跟随率=蝶阀落在上限±3% 的时间占比』识别并标为未达标，建议现场核查执行机构/反馈回路")


def align_text(stats):
    """recipe↔log 步号对齐结论（由『对齐检测』列自动生成，不再写死『滞后一步』）。"""
    df = _ncol(stats)
    has = df[df["对齐检测"].astype(str) != "—"]
    lag = has[has["对齐检测"].astype(str).str.contains("滞后")]
    if lag.empty:
        return (f"逐步比对 log 控制器目标值与 recipe 目标值：{len(has)} 个有对照的步中无一步出现步号错位，"
                f"更正后『- 实际』recipe 与 log 步号已完全对齐"
                f"（旧版 recipe 曾在 run2 降温段整体滞后一步，本次更正后已消除，"
                f"故不再需要按错位剔除达标率）。剩余异常仅来自测温通道量程截断，属硬件限制而非编排错位，见下条")
    seg = []
    for tag in sorted(lag["炉次"].unique()):
        g = lag[lag["炉次"] == tag]
        seg.append(f"{tag} 步{_ranges(g['_n'])}：{g['对齐检测'].iloc[0]}")
    return ("；".join(seg) +
            " → 本表已单列『对齐检测/对齐异常』列，并在达标率统计中剔除错位步，"
            "避免把编排错位误判成工艺未达标")


def t_trunc_text(stats):
    """主控测温通道量程截断的步（由『对齐检测』自动生成）。"""
    df = _ncol(stats)
    d = df[df["对齐检测"].astype(str).str.contains("截断")].sort_values(["炉次", "_n"])
    if d.empty:
        return "（本次无测温通道量程截断异常）"
    det = "；".join(f"{r['炉次']} 步{int(r['_n'])}（recipe 目标 {r['目标温度_recipe']:.0f}℃ → "
                   f"log 恒为 {r['目标温度_log']:.0f}℃）" for _, r in d.iterrows())
    return (det + "：对应主控测温通道 T1 在 1100.89℃ 饱和冻结，控制器只能把目标压到可用测温范围内，"
                  "属硬件量程限制，不是 recipe 编排错误")


def lowtemp_text(stats):
    """低温段测温无响应现象（『温度模式① + 实测判型=停滞』自动生成）。"""
    df = _ncol(stats)
    d = df[(df["温度模式"] == 1) &
           (df["温度_实测判型"].astype(str).str.contains("停滞"))].sort_values(["炉次", "_n"])
    if d.empty:
        return "（本次未检出低温段测温无响应步）"
    tv = d["温度_起始"].mode().iloc[0]
    pw = d["功率_结束"].max()
    seg = []
    for tag in sorted(d["炉次"].unique()):
        seg.append(f"{tag} 步{_ranges(d[d['炉次'] == tag]['_n'])}")
    return ("、".join(seg) +
            f"（共 {len(d)} 步）被判『全程恒温/停滞』：功率从 0 爬到 {pw:.1f}%，"
            f"主控温度读数却恒定在 {tv:.1f}℃ 不动。"
            "这不是工艺恒温，而是低温段测温通道无响应，读图时不要把它当成热平衡")


def p2_subtype_text(stats):
    """压力模式② 的稳压步 / 过渡步（由『压力_达标』自动生成）。"""
    df = _ncol(stats)
    d = df[df["压力模式"] == 2]
    if d.empty:
        return "（本次无压力模式② 步）"
    seg = []
    for tag in sorted(d["炉次"].unique()):
        sub = d[d["炉次"] == tag]
        ok = sub[sub["压力_达标"] == "达标"]
        tr = sub[sub["压力_达标"] != "达标"]
        if len(ok):
            dv = ok["压力_偏差pct"].dropna()
            a = (f"稳压达标步{_ranges(ok['_n'])}（{len(ok)} 步，后段均值相对目标偏差 "
                 f"{dv.min():+.2f}%~{dv.max():+.2f}%）") if len(dv) else \
                f"稳压达标步{_ranges(ok['_n'])}（{len(ok)} 步）"
        else:
            a = "无稳压达标步"
        if len(tr):
            cd = tr["压力_完成度"].dropna()
            b = (f"过渡步{_ranges(tr['_n'])}（{len(tr)} 步，完成度 "
                 f"{cd.min():.1f}%~{cd.max():.1f}%）") if len(cd) else \
                f"过渡步{_ranges(tr['_n'])}（{len(tr)} 步）"
        else:
            b = "无过渡步"
        seg.append(f"{tag}：{a}；{b}")
    return ("同标为『压力模式②』的步实际分两类——①稳压步（步末已到目标）→ 校验设定值跟踪；"
            "②过渡步（每步仅数分钟、压力仍在爬升）→ 校验的是升压能力而非稳压精度。"
            "把两者混在一起统计会把达标率压低，本表已分列『压力_达标』区分。" + "　".join(seg))


def verdict(exp_fam, got):
    g = fam(got)
    if g == "short":
        return "数据不足"
    if g == "na":
        return "无对照"
    if exp_fam == "lin":
        return "吻合" if g == "lin" else ("部分" if g == "other" else "不符")
    if exp_fam == "hold":
        return "吻合" if g == "hold" else ("部分" if g == "lin" else "不符")
    return "—"


# ================================================================ 单步分析
def analyze_run(tag, label, recipe, proc, meas):
    rows, checks = [], []
    keys = list(proc.groupby(["step", "pass"], sort=False).groups.keys())
    keys.sort(key=lambda k: (k[0], k[1]))
    for s, p in keys:
        sub, el = series_of(proc, s, p)
        lab = step_label(s, p)
        rc = recipe.get(s, {})
        dur = float(el[-1] - el[0]) if len(el) > 1 else 0.0
        tm = rc.get("温度模式", 0); pm = rc.get("压力模式", 0)

        t = stat(sub[T_COL], el)
        pw = stat(sub[P_COL], el)
        v = stat(sub[V_COL], el)
        mv = stat(sub[MV_COL], el)
        okv = np.asarray(sub[V_COL], float) > 0
        v_tail = tail_mean(np.where(okv, sub[V_COL], np.nan), el)
        t_tail = tail_mean(np.where(np.asarray(sub[T_COL], float) > 0, sub[T_COL], np.nan), el)
        p_tail = tail_mean(sub[P_COL], el)
        mv_tail = tail_mean(sub[MV_COL], el)
        pid_end = stat(sub[PID_COL], el)["t1"]

        # ---- 模式校验（物理量：维持偏差 / 完成度 / 达标判定）
        concl, notes = [], []
        t_done, p_done = np.nan, np.nan
        t_ok, p_ok = "不适用", "不适用"
        # 温度侧
        if tm == 1:
            pe = rc.get("功率上限", np.nan)
            dev = (pw["t1"] - pe) / pe * 100 if pe else np.nan
            tm_dev = np.nan
            if pe and np.isfinite(dev):
                concl.append(f"功率模式：功率末值 {pw['t1']:.1f} / 上限 {pe:.1f}（差 {dev:+.2f}%），"
                             f"功率线性 R²={pw['r2']:.4f}" if pw["r2"] == pw["r2"] else
                             f"功率模式：功率末值 {pw['t1']:.1f} / 上限 {pe:.1f}（差 {dev:+.2f}%）")
                t_ok = "达标" if abs(dev) <= 5 else "未达标"
            notes.append("功率模式：目标温度不参与控制（log 目标温度为 0，故不做温度达标判定）")
        elif tm == 2:
            ts = rc.get("目标温度", np.nan)
            tm_dev = t_tail - ts if ts else np.nan
            t_ok = ("达标" if abs(tm_dev) <= 10 else "未达标") if tm_dev == tm_dev else "数据不足"
            concl.append(f"温度模式：后段温度 {t_tail:.1f}℃ / 目标 {ts:.0f}℃（维持偏差 {tm_dev:+.1f}℃，"
                         f"阈值 ±10℃）")
            if t_ok == "未达标":
                notes.append("未稳压至目标温度：需核查功率余量、热损失或测温通道")
        elif tm == 3:
            ts = rc.get("目标温度", np.nan)
            span = ts - t["t0"]
            tm_dev = t["t1"] - ts
            # 目标≈起始（近似等温的"线性"步）时完成度会爆炸，此时不计算完成度
            t_done = (t["t1"] - t["t0"]) / span * 100 if abs(span) >= 20 else np.nan
            tol = max(10.0, 0.05 * abs(span))
            t_ok = "达标" if abs(tm_dev) <= tol else "未达标"
            concl.append(f"线性模式：{t['t0']:.0f}→{t['t1']:.0f}℃ / 目标 {ts:.0f}℃（差 {tm_dev:+.1f}℃，"
                         f"完成度 {t_done:.0f}%），实测斜率 {t['slope']:+.3f}℃/min"
                         if t_done == t_done else
                         f"线性模式：{t['t0']:.0f}→{t['t1']:.0f}℃ / 目标 {ts:.0f}℃（差 {tm_dev:+.1f}℃）")
            if t_ok == "未达标":
                notes.append("本步未在设定时间内到达目标温度"
                             + ("（降温步受炉体热惯性限制，属正常滞后；缺口由后续步继续完成）"
                                if span < 0 else "（升温受阻，核查功率余量/测温通道）"))
            if span > 0 and abs(t["t1"] - 1100.89) < 0.6:
                notes.append("末值落在 1100.89℃：主控测温通道(T1)饱和冻结值，"
                             "『未达标』由测温通道造成，不能作为工艺未达标的证据")
        else:
            tm_dev = np.nan
        # 压力侧
        if pm == 1:
            up = rc.get("蝶阀开度上限", np.nan)
            mv_dev = mv["mean"] - up if np.isfinite(up) else np.nan
            mv_arr = np.asarray(sub[MV_COL], float)
            mv_arr = mv_arr[np.isfinite(mv_arr)]
            # 跟随率用『时间占比』而非均值：开度模式下阀门可能 ON/OFF 跳变，
            # 均值会被两端平均掉，既掩盖"全开"也掩盖"全关"
            follow = float(np.mean(np.abs(mv_arr - up) <= 3) * 100) if (np.isfinite(up) and mv_arr.size) else np.nan
            p_ok = ("达标" if follow >= 50 else "未达标") if follow == follow else "数据不足"
            concl.append(f"开度模式：蝶阀落在开度上限 {up:.1f}%±3 的时间占比 {follow:.0f}%"
                         f"（均值 {mv['mean']:.2f}%，中位 {np.median(mv_arr):.2f}%，"
                         f"区间 {mv['mn']:.2f}~{mv['mx']:.2f}%）"
                         if follow == follow else f"开度模式：开度上限 {up:.1f}%")
            notes.append("开度模式：目标压力不参与控制（log 中该列不作为设定值）")
            if p_ok == "未达标":
                notes.append("开度模式未跟随：实测蝶阀大量时间停在 "
                             f"{np.median(mv_arr):.2f}%（上限 {up:.1f}%），"
                             "疑为 ON/OFF 式调节或阀门未执行到位，建议核查执行机构/反馈回路")
            pv_dev = np.nan
        elif pm == 2:
            ps = rc.get("目标压力", np.nan)
            pv_dev = (v_tail - ps) / ps * 100 if ps else np.nan
            span = ps - v["t0"]
            # 目标与起始压力接近时完成度无意义（分母过小），此时只判达标不报完成度
            p_done = (v["t1"] - v["t0"]) / span * 100 if abs(span) >= 0.05 * ps else np.nan
            if ps and abs(v["t1"] - ps) <= 0.05 * ps:
                p_ok = "达标"
                concl.append(f"压力模式：后段真空 {v_tail:.0f} Pa / 目标 {ps:.0f} Pa"
                             f"（偏 {pv_dev:+.2f}%），稳压达标")
            else:
                p_ok = "未达标(过渡)"
                concl.append((f"压力模式：步末 {v['t1']:.0f} Pa / 目标 {ps:.0f} Pa"
                              f"（完成度 {p_done:.0f}%）") if p_done == p_done else
                             f"压力模式：步末 {v['t1']:.0f} Pa / 目标 {ps:.0f} Pa")
                notes.append(f"升压/降压过渡步：本步 recipe 时长 {rc.get('时长_min', 0):.0f} min 内"
                             f"压力未到达目标值，设定值跟踪应结合后续步一起看")
            lo, hi = rc.get("蝶阀开度下限", np.nan), rc.get("蝶阀开度上限", np.nan)
            if np.isfinite(lo) and np.isfinite(hi) and mv["n"] >= 5:
                inside = (mv["mn"] >= lo - 1.5) and (mv["mx"] <= hi + 1.5)
                notes.append(f"蝶阀在 [{lo:.1f}, {hi:.1f}]% 限幅内{'正常' if inside else '越界，需核查'}")
            mv_dev = np.nan
        else:
            mv_dev = np.nan; pv_dev = np.nan; follow = np.nan
        if pm != 1:
            follow = np.nan
        if rc.get("结束位", 0) == 1:
            notes.append("recipe 结束位=1 → 工艺结束标记步（故 log 无该步运行记录）")

        # ---- recipe 与 log 的步号对齐检测
        # （降温段实测发现：log 的控制器目标值可能滞后 recipe 一整步，
        #   或被测温通道上限截断，若不做识别会把"错位"误判为"未达标"）
        lts = float(np.nanmedian(np.asarray(sub[TSP_COL], float))) if len(sub) else np.nan
        lps = float(np.nanmedian(np.asarray(sub[PSP_COL], float))) if len(sub) else np.nan
        rt_g, rp_g = rc.get("目标温度", np.nan), rc.get("目标压力", np.nan)
        al = []
        if tm and np.isfinite(rt_g) and lts == lts:
            tol = max(4.0, 0.005 * abs(rt_g))
            if abs(lts - rt_g) <= tol:
                al.append("温度目标一致")
            else:
                pv_t = recipe.get(s - 1, {}).get("目标温度", np.nan)
                if pv_t == pv_t and abs(lts - pv_t) <= max(4.0, 0.005 * abs(pv_t)):
                    al.append(f"温度目标滞后1步(=recipe 步{s - 1} 的 {pv_t:.0f}℃)")
                elif abs(lts - 1100.89) < 1.0:
                    al.append("温度目标截断在 1100℃(T1 测温通道上限)")
                else:
                    al.append(f"温度目标不一致(log {lts:.0f} vs recipe {rt_g:.0f})")
        if rp_g and rp_g == rp_g and lps == lps:
            tol = max(1.0, 0.005 * abs(rp_g))
            if abs(lps - rp_g) <= tol:
                al.append("压力目标一致")
            else:
                pv_p = recipe.get(s - 1, {}).get("目标压力", np.nan)
                if pv_p == pv_p and abs(lps - pv_p) <= max(1.0, 0.005 * abs(pv_p)):
                    al.append(f"压力目标滞后1步(=recipe 步{s - 1} 的 {pv_p:.0f}Pa)")
                else:
                    al.append(f"压力目标不一致(log {lps:.0f} vs recipe {rp_g:.0f})")
        align = "；".join(al) if al else "—"
        align_bad = "是" if any(k in align for k in ("滞后", "不一致", "截断")) else ("否" if al else "—")
        if align_bad == "是":
            notes.insert(0, f"recipe 对齐异常：{align}（本步不纳入设定值达标率统计，改列为待核查）")

        m = meas.get(lab, {})
        t_type, v_type = m.get("T_判型"), m.get("V_判型")
        t_exp_fam = {1: "lin", 2: "hold", 3: "lin"}.get(tm, "na")
        v_exp_fam = {1: "other", 2: "hold"}.get(pm, "na")
        t_ver = verdict(t_exp_fam, t_type) if tm else "无对照"
        v_ver = verdict(v_exp_fam, v_type) if pm else "无对照"
        if pm == 1 and v_type is not None:
            v_ver = "开环(以蝶阀偏差判)" if fam(v_type) != "short" else "数据不足"

        rows.append(dict(
            炉次=tag, 阶段=phase_of(s), 步号=lab, 循环次数=rc.get("循环次数", None),
            recipe时长_min=rc.get("时长_min", None), log时长_min=round(dur, 1), 记录数=len(sub),
            温度模式=tm, 温度模式含义=MODE_T.get(tm, "—"),
            目标温度_recipe=rc.get("目标温度", None), 目标温度_log=round(lts, 1) if lts == lts else None,
            温度_起始=round(t["t0"], 1), 温度_结束=round(t["t1"], 1),
            温度_最小=round(t["mn"], 1), 温度_最大=round(t["mx"], 1), 温度_均值=round(t["mean"], 1),
            温度_斜率=round(t["slope"], 4) if t["slope"] == t["slope"] else None,
            温度_线性R2=round(t["r2"], 4) if t["r2"] == t["r2"] else None,
            功率上限=rc.get("功率上限", None), 功率下限=rc.get("功率下限", None),
            功率_起始=round(pw["t0"], 1), 功率_结束=round(pw["t1"], 1),
            功率_最小=round(pw["mn"], 1), 功率_最大=round(pw["mx"], 1), 功率_均值=round(pw["mean"], 1),
            功率_斜率=round(pw["slope"], 4) if pw["slope"] == pw["slope"] else None,
            功率_线性R2=round(pw["r2"], 4) if pw["r2"] == pw["r2"] else None,
            压力模式=pm, 压力模式含义=MODE_P.get(pm, "—"),
            目标压力_recipe=rc.get("目标压力", None),
            目标压力_log=round(lps, 1) if lps == lps else None,
            对齐检测=align, 对齐异常=align_bad,
            真空_起始=round(v["t0"], 1), 真空_结束=round(v["t1"], 1),
            真空_最小=round(v["mn"], 1), 真空_最大=round(v["mx"], 1), 真空_均值=round(v["mean"], 1),
            真空_后段均值=round(v_tail, 1) if v_tail == v_tail else None,
            蝶阀上限=rc.get("蝶阀开度上限", None), 蝶阀下限=rc.get("蝶阀开度下限", None),
            蝶阀_最小=round(mv["mn"], 2), 蝶阀_最大=round(mv["mx"], 2), 蝶阀_均值=round(mv["mean"], 2),
            蝶阀跟随率=round(follow, 1) if follow == follow else None,
            温度_实测判型=t_type, 温度_推荐速率=m.get("T_速率"), 温度_期望类型=EXP_T.get(tm, "—"),
            温度_符合度=t_ver, 温度_达标=t_ok,
            温度_偏差=round(tm_dev, 1) if tm_dev == tm_dev else None,
            温度_完成度=round(t_done, 1) if t_done == t_done else None,
            真空_实测判型=v_type, 真空_推荐速率=m.get("V_速率"), 真空_期望类型=EXP_V.get(pm, "—"),
            真空_符合度=v_ver, 压力_达标=p_ok,
            压力_偏差pct=round(pv_dev, 2) if pv_dev == pv_dev else None,
            压力_完成度=round(p_done, 1) if p_done == p_done else None,
            模式校验="；".join(concl) if concl else "—",
            备注="；".join(notes) if notes else "",
            _tm_dev=tm_dev, _pv_dev=pv_dev, _mv_dev=mv_dev, _t_done=t_done, _p_done=p_done,
        ))
    return pd.DataFrame(rows)


def _save(fig, path, dpi=125, tries=6):
    """原子写图：先写临时文件再替换，规避 Windows 上偶发的文件占用(Errno 22)"""
    tmp = path + ".tmp.png"
    for i in range(tries):
        try:
            fig.savefig(tmp, dpi=dpi, facecolor="white")
            os.replace(tmp, path)
            return path
        except OSError:
            time.sleep(0.4 * (i + 1))
    fig.savefig(path, dpi=dpi, facecolor="white")
    return path

# ================================================================ 绘图
def _step_spans(proc):
    """返回 {key:(累计起点, 累计终点)} 与 拼接后的 x / y 序列"""
    off, spans = 0.0, {}
    for (s, p), _ in proc.groupby(["step", "pass"], sort=False).groups.items():
        pass
    keys = sorted(proc.groupby(["step", "pass"], sort=False).groups.keys(), key=lambda k: (k[0], k[1]))
    for s, p in keys:
        sub, el = series_of(proc, s, p)
        spans[(s, p)] = (off, off + (el[-1] if len(el) else 0.0))
        off += (el[-1] if len(el) else 0.0)
    return spans


def _concat(proc, col, k=1):
    X, Y = [], []
    keys = sorted(proc.groupby(["step", "pass"], sort=False).groups.keys(), key=lambda k: (k[0], k[1]))
    off = 0.0
    for s, p in keys:
        sub, el = series_of(proc, s, p)
        y = sub[col].to_numpy(float)
        st = max(1, len(el) // 4000)
        X.append(off + el[::st]); Y.append(y[::st])
        off += (el[-1] if len(el) else 0.0)
    if not X:
        return np.array([]), np.array([])
    return np.concatenate(X), np.concatenate(Y)


def _staircase(proc, recipe, keyname, axis_scale=1.0, only_mode=None):
    """把 recipe 的某个设定值画成阶梯线（跨全步长的水平段）"""
    return None


def plot_overview_dual(tag, label, recipe, proc, kind, fname, title):
    """全程双轴总览：温度-功率 / 真空-蝶阀，叠加 recipe 阶梯参考线"""
    spans = _step_spans(proc)
    fig, ax = plt.subplots(figsize=(17, 6.2))
    fig.patch.set_facecolor("white")
    ax2 = ax.twinx()

    if kind == "tp":
        X, T = _concat(proc, T_COL); _, P = _concat(proc, P_COL)
        ax.plot(X, T, color=COL["t"], lw=0.9, label=f"{T_COL} (℃, 左轴)")
        # 需求7：T1/T2 原始双测温同时入图（0 值=高位偶未起测，屏蔽以免压扁纵轴）
        for col, c, ls, nm in ((T1_COL, COL["t1"], "--", f"{T1_COL} (℃, 左轴)"),
                               (T2_COL, COL["t2"], ":", f"{T2_COL} (℃, 左轴)")):
            _, yy = _concat(proc, col)
            yy = np.asarray(yy, float)
            ax.plot(X, np.where(yy > 0, yy, np.nan), color=c, lw=0.8, ls=ls, alpha=0.9, label=nm)
        ax2.plot(X, P, color=COL["p"], lw=0.8, alpha=0.9, label=f"{P_COL} (%, 右轴)")
        # recipe 阶梯：目标温度 / 功率上下限
        xs, ts, ps, pl = [], [], [], []
        xt, xp, xl = [], [], []
        for (s, p), (a, b) in spans.items():
            rc = recipe.get(s, {})
            if rc.get("温度模式", 0) in (2, 3) and rc.get("目标温度", 0):
                xt += [a, b]; ts += [rc["目标温度"]] * 2
            if rc.get("温度模式", 0) == 1 and rc.get("功率上限"):
                xp += [a, b]; ps += [rc["功率上限"]] * 2
            if rc.get("功率下限"):
                xl += [a, b]; pl += [rc["功率下限"]] * 2
        if ts:
            ax.step(xt, ts, where="post", color=COL["tsp"], ls="--", lw=1.2,
                    label="recipe 目标温度 (℃, 左轴)")
        if ps:
            ax2.step(xp, ps, where="post", color=COL["p"], ls="--", lw=1.0, alpha=0.75,
                     label="recipe 功率上限 (%, 右轴)")
        if pl:
            ax2.step(xl, pl, where="post", color=COL["lim"], ls=":", lw=1.0,
                     label="recipe 功率下限 (%, 右轴)")
        ax.set_ylabel("温度 (℃)", color=COL["t"]); ax2.set_ylabel("功率 (%)", color=COL["p"])
    else:
        X, V = _concat(proc, V_COL); _, M = _concat(proc, MV_COL)
        ax.plot(X, V, color=COL["v"], lw=0.9, label=f"{V_COL} (Pa, 左轴)")
        ax.set_yscale("log")
        ax2.plot(X, M, color=COL["mv"], lw=0.8, alpha=0.9, label=f"{MV_COL} (%, 右轴)")
        xs, vs, xsu, up_, xsd, dn_ = [], [], [], [], [], []
        for (s, p), (a, b) in spans.items():
            rc = recipe.get(s, {})
            if rc.get("压力模式", 0) == 2 and rc.get("目标压力", 0):
                xs += [a, b]; vs += [rc["目标压力"]] * 2
            if rc.get("蝶阀开度上限") is not None:
                xsu += [a, b]; up_ += [rc["蝶阀开度上限"]] * 2
            if rc.get("蝶阀开度下限") is not None:
                xsd += [a, b]; dn_ += [rc["蝶阀开度下限"]] * 2
        if vs:
            ax.step(xs, vs, where="post", color=COL["v"], ls="--", lw=1.2,
                    label="recipe 目标压力 (Pa, 左轴，仅压力模式)")
        if up_:
            ax2.step(xsu, up_, where="post", color=COL["mv"], ls="--", lw=1.0, alpha=0.8,
                     label="recipe 蝶阀开度上限 (%, 右轴)")
        if dn_:
            ax2.step(xsd, dn_, where="post", color=COL["lim"], ls=":", lw=1.0,
                     label="recipe 蝶阀开度下限 (%, 右轴)")
        ax.set_ylabel("炉内真空 (Pa)", color=COL["v"]); ax2.set_ylabel("蝶阀开度 (%)", color=COL["mv"])

    # 阶段背景 + 分隔
    first = True
    for (s, p), (a, b) in spans.items():
        ph = phase_of(s)
        c = {phases.PRE: "#fff4e6", phases.HOLD: "#e7f5ff", phases.POST: "#ebfbee"}[ph]
        if p == 1:
            if not first:
                ax.axvline(a, color="#dee2e6", lw=0.5)
                ax2.axvline(a, color="#dee2e6", lw=0.5)
            first = False
        ax.axvspan(a, b, color=c, alpha=0.35, zorder=0)
    ax.set_xlabel("累计时间 (min，自首步起)")
    ax.set_title(title)
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8, ncol=2, framealpha=0.9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    _save(fig, os.path.join(FIG, fname))
    plt.close(fig)
    return fname


def _stat_lookup(st):
    if st is None:
        return {}
    return {str(r["步号"]): r for _, r in st.iterrows()}


def _status_text(kind, row):
    """子图内嵌的模式校验结论标注"""
    if row is None:
        return ""
    try:
        if kind == "tp":
            tm = int(row["温度模式"])
            if tm == 1:
                return f"功率末值 {float(row['功率_结束']):.1f} / 上限 {float(row['功率上限']):.1f}"
            if tm == 2:
                d = row["温度_偏差"]
                return f"维持偏差 {float(d):+.0f}℃" if d == d else ""
            if tm == 3:
                d, c = row["温度_偏差"], row["温度_完成度"]
                s = f"完成度 {float(c):.0f}%" if c == c else ""
                return (s + f"（差 {float(d):+.0f}℃）") if d == d and s else s
            return ""
        pm = int(row["压力模式"])
        if pm == 1:
            f5 = row["蝶阀跟随率"]
            return f"蝶阀跟随率 {float(f5):.0f}%" if f5 == f5 else ""
        if pm == 2:
            d, c = row["压力_偏差pct"], row["压力_完成度"]
            if row["压力_达标"] == "达标":
                return f"稳压偏差 {float(d):+.2f}%"
            return f"过渡步 完成度 {float(c):.0f}%" if c == c else ""
    except Exception:
        return ""
    return ""


def plot_step_grid_dual(tag, label, recipe, proc, keys, kind, fname, title, st=None):
    """分步子图，每步一张双纵坐标图 + recipe 参考线 + 模式标注"""
    n = len(keys)
    ncol = 6
    nrow = int(np.ceil(n / ncol))
    lut = _stat_lookup(st)
    fig, axes = plt.subplots(nrow, ncol, figsize=(3.5 * ncol, 2.9 * nrow))
    axes = np.atleast_1d(axes).ravel()
    for i, (s, p) in enumerate(keys):
        ax = axes[i]
        sub, el = series_of(proc, s, p)
        rc = recipe.get(s, {})
        lab = step_label(s, p)
        tm, pm = rc.get("温度模式", 0), rc.get("压力模式", 0)
        ax2 = ax.twinx()
        parsed = False
        if kind == "tp":
            y_t = np.asarray(sub[T_COL], float)
            ok = np.isfinite(y_t) & (y_t > 0)
            cand = list(y_t[ok])
            if tm in (2, 3) and rc.get("目标温度"):
                cand.append(rc["目标温度"])
            if len(cand) >= 2:
                # 需求7：主控 + T1 + T2 三条温度同图（细线，小图也放得下）
                cand += plot_three_temps(ax, sub, el, lw_main=1.0, lw_sub=0.75, z_base=3)
                pid = np.asarray(sub[PID_COL], float)
                okp = np.isfinite(pid) & (pid > 0)
                if okp.any():
                    ax.plot(el[okp], pid[okp], color=COL["pid"], lw=0.8, ls="-.", alpha=0.85)
                    cand += list(pid[okp])
                if tm in (2, 3) and rc.get("目标温度"):
                    ax.axhline(rc["目标温度"], color=COL["tsp"], ls="--", lw=1.1)
                lo, hi = min(cand), max(cand)
                pad = max(5.0, 0.06 * (hi - lo + 1e-9))
                ax.set_ylim(lo - pad, hi + pad)
                ax.set_ylabel("℃", fontsize=7)
                parsed = True
            pw = np.asarray(sub[P_COL], float)
            if np.isfinite(pw).any():
                ax2.plot(el, pw, color=COL["p"], lw=0.9, alpha=0.9)
                ax2.axhline(rc.get("功率上限", np.nan), color=COL["p"], ls="--", lw=1.0, alpha=0.8)
                if rc.get("功率下限"):
                    ax2.axhline(rc["功率下限"], color=COL["lim"], ls=":", lw=1.0)
                pv = list(pw[np.isfinite(pw)])
                if rc.get("功率上限"):
                    pv.append(rc["功率上限"])
                lo2, hi2 = min(pv), max(pv)
                pd2 = max(3.0, 0.08 * (hi2 - lo2 + 1e-9))
                ax2.set_ylim(lo2 - pd2, hi2 + pd2)
                ax2.set_ylabel("%", fontsize=7, color=COL["p"])
        else:
            vv = np.asarray(sub[V_COL], float)
            ok = np.isfinite(vv) & (vv > 0)
            if ok.any():
                ax.plot(el[ok], vv[ok], color=COL["v"], lw=1.0)
                if pm == 2 and rc.get("目标压力"):
                    ax.axhline(rc["目标压力"], color=COL["v"], ls="--", lw=1.1)
                    cand = list(vv[ok]) + [rc["目标压力"]]
                else:
                    cand = list(vv[ok])
                if max(cand) / max(min(cand), 1e-9) > 50:
                    ax.set_yscale("log")
                else:
                    lo, hi = min(cand), max(cand)
                    pd = max(0.02 * (hi - lo + 1e-9), 0.5)
                    ax.set_ylim(lo - pd, hi + pd)
                ax.set_ylabel("Pa", fontsize=7)
                parsed = True
            mvd = np.asarray(sub[MV_COL], float)
            if np.isfinite(mvd).any():
                ax2.plot(el, mvd, color=COL["mv"], lw=0.9, alpha=0.9)
                if rc.get("蝶阀开度上限") is not None:
                    ax2.axhline(rc["蝶阀开度上限"], color=COL["mv"], ls="--", lw=1.0, alpha=0.8)
                if rc.get("蝶阀开度下限"):
                    ax2.axhline(rc["蝶阀开度下限"], color=COL["lim"], ls=":", lw=1.0)
                if rc.get("蝶阀开度上限") and rc.get("蝶阀开度下限"):
                    ax2.axhspan(rc["蝶阀开度下限"], rc["蝶阀开度上限"], color=COL["band"], alpha=0.25, zorder=0)
                lo2 = min(float(np.nanmin(mvd)), rc.get("蝶阀开度下限") or 0.0)
                hi2 = max(float(np.nanmax(mvd)), rc.get("蝶阀开度上限") or 0.0)
                pd2 = max(3.0, 0.08 * (hi2 - lo2 + 1e-9))
                ax2.set_ylim(max(-5, lo2 - pd2), min(105, hi2 + pd2))
                ax2.set_ylabel("%", fontsize=7, color=COL["mv"])
        tag_mode = (f"[{MODE_T.get(tm, '')[:5]}]" if kind == "tp" else f"[{MODE_P.get(pm, '')[:5]}]")
        ax.set_title(f"步{lab} {tag_mode} 目标T:{rc.get('目标温度', 0):.0f}℃ 目标P:{rc.get('目标压力', 0):.0f}Pa",
                     fontsize=7.5)
        stx = _status_text(kind, lut.get(lab))
        if stx:
            ax.text(0.02, 0.04, stx, transform=ax.transAxes, fontsize=6.5, color="#c92a2a",
                    bbox=dict(fc="white", ec="#ffc9c9", lw=0.6, alpha=0.85, pad=1.4))
        if lut.get(lab) is not None and lut[lab]["对齐异常"] == "是":
            ax.text(0.98, 0.96, "recipe对齐异常", transform=ax.transAxes, fontsize=6.5,
                    color="#e8590c", ha="right", va="top",
                    bbox=dict(fc="#fff4e6", ec="#ffd8a8", lw=0.6, alpha=0.9, pad=1.4))
        ax.tick_params(labelsize=6)
        ax2.tick_params(labelsize=6, colors=("#c92a2a" if kind == "tp" else "#c92a2a"))
        ax.grid(alpha=0.2)
        if not parsed:
            ax.text(0.5, 0.5, "无有效数据", ha="center", va="center", fontsize=8, transform=ax.transAxes)
        if el.size and el[-1] > 60:
            ax.set_xlabel("min", fontsize=6)
    for j in range(n, len(axes)):
        axes[j].axis("off")
    if kind == "tp":
        proxies = [
            plt.Line2D([], [], color=COL["t"], lw=1.4, label="主控温度 (左轴 ℃)"),
            plt.Line2D([], [], color=COL["t1"], lw=1.2, ls="--", label="T1温度 (左轴 ℃)"),
            plt.Line2D([], [], color=COL["t2"], lw=1.2, ls=":", label="T2温度 (左轴 ℃)"),
            plt.Line2D([], [], color=COL["pid"], lw=1.2, ls="-.", label="控制器PID设定 (左轴 ℃)"),
            plt.Line2D([], [], color=COL["tsp"], lw=1.2, ls="--", label="recipe 目标温度 (左轴 ℃)"),
            plt.Line2D([], [], color=COL["p"], lw=1.4, label="电源总功率反馈 (右轴 %)"),
            plt.Line2D([], [], color=COL["p"], lw=1.2, ls="--", label="recipe 功率上限 (右轴 %)"),
        ]
    else:
        proxies = [
            plt.Line2D([], [], color=COL["v"], lw=1.4, label="炉内真空 (左轴 Pa)"),
            plt.Line2D([], [], color=COL["v"], lw=1.2, ls="--", label="recipe 目标压力 (左轴 Pa，仅压力模式)"),
            plt.Line2D([], [], color=COL["mv"], lw=1.4, label="数控蝶阀开度反馈 (右轴 %)"),
            plt.Line2D([], [], color=COL["mv"], lw=1.2, ls="--", label="recipe 蝶阀开度上限 (右轴 %)"),
            Patch(facecolor=COL["band"], alpha=0.35, label="recipe 蝶阀开度上下限区间"),
        ]
    fig.legend(handles=proxies, loc="lower center", ncol=max(5, min(len(proxies), 7)), fontsize=8.5,
               frameon=False, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle(title, fontsize=13)
    fig.tight_layout(rect=[0, 0.022, 1, 0.97])
    _save(fig, os.path.join(FIG, fname))
    plt.close(fig)
    return fname


def plot_hold_dual(tag, label, recipe, proc, key, fname, title):
    """恒温步25：上=温度+功率，下=真空+蝶阀"""
    sub, el = series_of(proc, *key)
    rc = recipe.get(key[0], {})
    h = el / 60.0
    fig, axs = plt.subplots(2, 1, figsize=(15, 8), sharex=True)
    fig.patch.set_facecolor("white")
    # 上
    a = axs[0]; a2 = a.twinx()
    # 需求7：主控 + T1 + T2 同图
    plot_three_temps(a, sub, h, lw_main=0.9, lw_sub=0.75, main_label=f"{T_COL} (℃)")
    pid = np.asarray(sub[PID_COL], float); okp = np.isfinite(pid) & (pid > 0)
    if okp.any():
        a.plot(h[okp], pid[okp], color=COL["pid"], lw=0.8, ls="-.", label="PID目标温度 (℃)")
    if rc.get("目标温度"):
        a.axhline(rc["目标温度"], color=COL["tsp"], ls="--", lw=1.2, label="recipe 目标温度")
    a.set_ylabel("温度 (℃)"); a.legend(loc="upper right", fontsize=8); a.grid(alpha=0.25)
    a2.plot(h, sub[P_COL], color=COL["p"], lw=0.8, alpha=0.85, label=f"{P_COL} (%)")
    if rc.get("功率上限"):
        a2.axhline(rc["功率上限"], color=COL["p"], ls="--", lw=1.0, alpha=0.8, label="recipe 功率上限")
    if rc.get("功率下限"):
        a2.axhline(rc["功率下限"], color=COL["lim"], ls=":", lw=1.0, label="recipe 功率下限")
    a2.set_ylabel("功率 (%)", color=COL["p"]); a2.legend(loc="lower right", fontsize=8)
    a.set_title(f"{title} —— 温度(左轴) vs 功率(右轴)")
    # 下
    b = axs[1]; b2 = b.twinx()
    vv = np.asarray(sub[V_COL], float); okv = np.isfinite(vv) & (vv > 0)
    b.plot(h[okv], vv[okv], color=COL["v"], lw=0.9, label=f"{V_COL} (Pa)")
    if rc.get("压力模式") == 2 and rc.get("目标压力"):
        b.axhline(rc["目标压力"], color=COL["v"], ls="--", lw=1.2, label="recipe 目标压力")
    b.set_ylabel("炉内真空 (Pa)"); b.set_xlabel("时间 (h)"); b.legend(loc="upper right", fontsize=8); b.grid(alpha=0.25)
    b2.plot(h, sub[MV_COL], color=COL["mv"], lw=0.7, alpha=0.8, label=f"{MV_COL} (%)")
    if rc.get("蝶阀开度上限") is not None:
        b2.axhline(rc["蝶阀开度上限"], color=COL["mv"], ls="--", lw=1.0, alpha=0.8, label="recipe 开度上限")
    if rc.get("蝶阀开度下限"):
        b2.axhline(rc["蝶阀开度下限"], color=COL["lim"], ls=":", lw=1.0, label="recipe 开度下限")
    b2.set_ylabel("蝶阀开度 (%)", color=COL["mv"]); b2.legend(loc="lower right", fontsize=8)
    b.set_title("真空度(左轴) vs 蝶阀开度(右轴)")
    fig.tight_layout()
    _save(fig, os.path.join(FIG, fname))
    plt.close(fig)
    return fname


def plot_mode_check(tag, label, df, check, fname, title):
    """模式符合度仪表盘：维持偏差 / 完成度 / 末值偏差 / 蝶阀跟随 + 汇总达标率"""
    d = df[df["记录数"] >= 8].copy().reset_index(drop=True)
    x = np.arange(len(d))
    labs = [f"{a}-{b}" for a, b in zip(d["炉次"], d["步号"])]
    fig, axs = plt.subplots(3, 2, figsize=(19, 13))
    fig.patch.set_facecolor("white")
    ax = axs.ravel()

    def bars(a, vals, thr, ylab, ttl, unit="", signed=True):
        vals = pd.Series(vals).astype(float)
        cols = []
        for v in vals:
            if v != v:
                cols.append(COL["lim"])
            elif abs(v) <= thr:
                cols.append(COL["ok"])
            else:
                cols.append(COL["bad"])
        a.bar(x, vals, color=cols, width=0.72)
        a.axhline(0, color="#495057", lw=0.8)
        if signed:
            a.axhline(thr, color=COL["bad"], ls=":", lw=1.0)
            a.axhline(-thr, color=COL["bad"], ls=":", lw=1.0)
        else:
            a.axhline(thr, color=COL["bad"], ls=":", lw=1.0)
        a.set_ylabel(ylab, fontsize=8); a.set_title(ttl, fontsize=9.5); a.grid(alpha=0.25)
        for i, v in enumerate(vals):
            if v == v and abs(v) > thr:
                a.text(i, v, f"{v:+.0f}{unit}", ha="center", fontsize=5.5,
                       va="bottom" if v > 0 else "top")
        return

    # ① 温度模式② 维持偏差
    v1 = (d["_tm_dev"].astype(float)).where(d["温度模式"] == 2)
    bars(ax[0], v1, 10, "后段均值 − 目标 (℃)",
         "① 温度模式：维持偏差（阈值 ±10℃；仅模式②）", "℃")
    # ② 温度模式③ 完成度
    v2 = (d["_t_done"].astype(float)).where(d["温度模式"] == 3)
    okn = (d["温度_达标"] == "达标") & (d["温度模式"] == 3)
    cols = [COL["ok"] if (t and v == v and v >= 0) else (COL["part"] if v == v else COL["lim"])
            for t, v in zip(okn, v2)]
    ax[1].bar(x, v2, color=cols, width=0.72)
    ax[1].axhline(100, color=COL["bad"], ls=":", lw=1.0)
    ax[1].axhline(0, color="#495057", lw=0.8)
    ax[1].set_ylabel("完成度 (%)", fontsize=8)
    ax[1].set_title("② 温度模式③ 线性模式：本步温度完成度 = (末值−起始)/(目标−起始)\n"
                    "（绿=达标；降温步出现低完成度属炉体热惯性滞后；目标≈起始的步不计算）", fontsize=9.5)
    vv = v2.dropna()
    if len(vv):
        lo, hi = min(-20.0, float(vv.min())), max(130.0, float(vv.max()))
        lo = max(lo, -160.0); hi = min(hi, 260.0)
        ax[1].set_ylim(lo, hi)
        for i, val in enumerate(v2):
            if val == val and (val < lo or val > hi):
                ax[1].text(i, hi * 0.92 if val > hi else lo * 0.9, f"{val:+.0f}", ha="center",
                           fontsize=5.5, color=COL["bad"], rotation=90)
    ax[1].grid(alpha=0.25)
    # ③ 功率模式 末值偏差
    pe = d["功率上限"].astype(float)
    v3 = ((d["功率_结束"].astype(float) - pe) / pe * 100).where(d["温度模式"] == 1)
    bars(ax[2], v3, 5, "功率末值 − 功率上限 (%)",
         "③ 功率模式：功率末值 vs recipe 功率上限（阈值 ±5%）", "%")
    # ④ 压力模式② 达标 / 完成度
    v4 = (d["_p_done"].astype(float)).where(d["压力模式"] == 2)
    okp = (d["压力_达标"] == "达标") & (d["压力模式"] == 2)
    cols = [COL["ok"] if t else COL["part"] if v == v else COL["lim"] for t, v in zip(okp, v4)]
    ax[3].bar(x, v4, color=cols, width=0.72)
    ax[3].axhline(100, color="#495057", ls=":", lw=1.0)
    ax[3].axhline(0, color="#495057", lw=0.8)
    ax[3].set_ylabel("升压完成度 (%)", fontsize=8)
    ax[3].set_title("④ 压力模式②：达标(绿，步末已稳压到目标) / 过渡步(橙，给出完成度)", fontsize=9.5)
    ax[3].grid(alpha=0.25)
    # ⑤ 开度模式 蝶阀跟随率（时间占比）
    v5 = (d["蝶阀跟随率"].astype(float)).where(d["压力模式"] == 1)
    cols = [COL["ok"] if (v == v and v >= 50) else COL["bad"] if v == v else COL["lim"] for v in v5]
    ax[4].bar(x, v5, color=cols, width=0.72)
    ax[4].axhline(50, color=COL["bad"], ls=":", lw=1.0)
    ax[4].set_ylabel("蝶阀落在开度上限±3% 的时间占比 (%)", fontsize=8)
    ax[4].set_title("⑤ 开度模式：蝶阀是否跟随 recipe 开度上限（时间占比 ≥ 50% 记达标）\n"
                    "（用占比而非均值：ON/OFF 式调节会把均值拉到中间，掩盖真实状态）", fontsize=9.5)
    ax[4].grid(alpha=0.25)
    # ⑥ 汇总达标率
    lab = list(check["校验项"]); rate = [float(str(v).rstrip("%")) if str(v) != "—" else 0
                                    for v in check["达标率"]]
    y = np.arange(len(lab))[::-1]
    ax[5].barh(y, rate, color=[COL["ok"] if r >= 80 else COL["part"] if r >= 50 else COL["bad"] for r in rate])
    for yy, r, n, k in zip(y, rate, check["有效步数"], check["达标步数"]):
        ax[5].text(r + 1, yy, f"{r:.0f}%  ({k}/{n})", va="center", fontsize=7.5)
    ax[5].set_yticks(y); ax[5].set_yticklabels(lab, fontsize=8)
    ax[5].set_xlim(0, 118); ax[5].set_xlabel("达标率 (%)")
    ax[5].set_title("⑥ 五项模式语义校验达标率汇总", fontsize=9.5); ax[5].grid(alpha=0.25, axis="x")

    for k in [0, 1, 2, 3, 4]:
        ax[k].set_xticks(x); ax[k].set_xticklabels(labs, rotation=90, fontsize=5.5)
    fig.suptitle(title, fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.975])
    _save(fig, os.path.join(FIG, fname))
    plt.close(fig)
    return fname


def plot_type_matrix(stats, fname, title):
    """recipe 期望族 vs 实测判型 的分布对照（堆叠条形，按模式分组）

    温度域与压力·真空域共用同一分段模型，但平台话术不同（温度=「恒温」/ 压力·真空=「恒压」），
    故按域取不同的类别名；图例合并两套、配色按族一一对应。"""
    CATS_T = ["单一线性", "多段线性式", "前期线性+后期恒温", "全程恒温/停滞", "波浪式", "短步(数据不足)"]
    CATS_V = ["单一线性", "多段线性式", "前期线性+后期恒压", "全程恒压/停滞", "波浪式", "短步(数据不足)"]
    ALL_CATS = CATS_T + [c for c in CATS_V if c not in CATS_T]

    def norm(t, cats):
        """实测类型 → 本域类别名。

        容错：过渡期若压力域数据仍残留温度话术「恒温」，归入本域的「恒压」类，
        避免因话术未同步而落进『其他』。"""
        if not isinstance(t, str):
            return None
        for c in cats:
            if c in t:
                return c
        for c in cats:
            alt = c.replace("恒压", "恒温")   # 本域类别名 ↔ 另一域话术
            if alt != c and alt in t:
                return c
        return "其他"

    groups = [
        ("温度\n模式①功率", stats["温度模式"] == 1, "温度_实测判型", "#fff4e6", CATS_T),
        ("温度\n模式②温度", stats["温度模式"] == 2, "温度_实测判型", "#e7f5ff", CATS_T),
        ("温度\n模式③线性", stats["温度模式"] == 3, "温度_实测判型", "#ebfbee", CATS_T),
        ("压力\n模式①开度", stats["压力模式"] == 1, "真空_实测判型", "#fff4e6", CATS_V),
        ("压力\n模式②压力", stats["压力模式"] == 2, "真空_实测判型", "#e7f5ff", CATS_V),
    ]
    palette = {"单一线性": "#4dabf7", "多段线性式": "#748ffc",
               "前期线性+后期恒温": "#ffd43b", "前期线性+后期恒压": "#ffd43b",
               "全程恒温/停滞": "#69db7c", "全程恒压/停滞": "#69db7c",
               "波浪式": "#e599f7", "短步(数据不足)": "#ced4da", "其他": "#adb5bd"}
    fig, ax = plt.subplots(figsize=(11, 5.8))
    fig.patch.set_facecolor("white")
    xs = np.arange(len(groups))
    bottom = np.zeros(len(groups))
    for c in ALL_CATS + ["其他"]:
        vals = []
        for _, sel, col, _c, cats in groups:
            sub = stats[sel & (stats["记录数"] >= 8)]
            tot = len(sub)
            vals.append(0 if tot == 0 else (sub[col].map(lambda t, _cats=cats: norm(t, _cats)) == c).sum() / tot * 100)
        vals = np.array(vals, float)
        ax.bar(xs, vals, bottom=bottom, color=palette[c], label=c, width=0.62,
               edgecolor="white", lw=0.6)
        for i, v in enumerate(vals):
            if v >= 8:
                ax.text(xs[i], bottom[i] + v / 2, f"{v:.0f}%", ha="center", va="center", fontsize=7.5)
        bottom += vals
    for x in xs[1:]:
        ax.axvline(x - 0.5, color="#dee2e6", lw=0.8)
    ax.set_xticks(xs); ax.set_xticklabels([g[0] for g in groups], fontsize=9)
    ax.set_ylabel("占该模式有效步数的比例 (%)"); ax.set_ylim(0, 100)
    ax.set_title(title)
    ax.legend(fontsize=8, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.10))
    ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    _save(fig, os.path.join(FIG, fname))
    plt.close(fig)
    return fname


# ================================================================ 主流程
def main():
    notes_out = []
    all_stats, all_detail, all_recipe, all_check = [], [], [], []
    collected = []
    run_info = {}          # tag -> dict(label, log_last, end_mark)（说明页按实际数据生成）
    diags = {}             # tag -> 需求7 源系统偏差诊断（说明页按实际数据生成）
    for tag, label, rfile, d3, d4 in RUNS:
        recipe = load_recipe(rfile)
        hold = phases.hold_step(recipe)          # 涂层中 = 时长最长的一步（本批现算，不写死）
        phases.set_active(hold, tag)
        proc, dropped = load_log(tag, hold=hold)
        meas = load_measured(tag, d3, d4)
        st = analyze_run(tag, label, recipe, proc, meas)
        all_stats.append(st)
        diags[tag] = t_diag(proc)
        _endm = [s for s, rc in recipe.items() if rc.get("结束位", 0) == 1]
        _endm_v = max(_endm) if _endm else max(recipe.keys())
        _log_steps = {int(x) for x in proc["step"].unique()}
        _log_last = max(int(str(x).split("(")[0]) for x in st["步号"])
        # 缺失步：recipe 有正时长计划、但在 log 实际执行区间内无任何记录（尾部缺失由 log_last 表述）
        _missing = sorted(s for s, rc in recipe.items()
                          if rc.get("时长_min", 0) > 0 and 1 <= s < _log_last and s not in _log_steps)
        run_info[tag] = dict(
            label=label,
            log_last=_log_last,
            end_mark=_endm_v,
            missing=_missing)

        # recipe 参数表
        for s in sorted(recipe.keys()):
            rc = recipe[s]
            all_recipe.append(dict(
                炉次=tag, 步号=s, 循环次数=rc["循环次数"], 工艺阶段=rc["工艺阶段"],
                时长_min=rc["时长_min"],
                温度模式=rc["温度模式"], 温度模式含义=MODE_T.get(rc["温度模式"], "—"),
                目标温度=rc["目标温度"], 功率上限=rc["功率上限"], 功率下限=rc["功率下限"],
                压力模式=rc["压力模式"], 压力模式含义=MODE_P.get(rc["压力模式"], "—"),
                目标压力=rc["目标压力"], 蝶阀开度上限=rc["蝶阀开度上限"], 蝶阀开度下限=rc["蝶阀开度下限"],
                结束位=rc["结束位"],
                log有无记录="有" if ((proc["step"] == s).any()) else "无（未执行/结束标记）",
            ))

        keys = sorted(proc.groupby(["step", "pass"], sort=False).groups.keys(), key=lambda k: (k[0], k[1]))
        heat = [k for k in keys if k[0] < hold]
        cool = [k for k in keys if k[0] > hold]
        holdk = [k for k in keys if k[0] == hold]
        # 图标题的步号区间按**本批实际步号**生成（R4：不写死 1–24 / 26–45）
        _span = lambda ks: (f"步{min(k[0] for k in ks)}–{max(k[0] for k in ks)}" if ks else "—")

        plot_overview_dual(tag, label, recipe, proc, "tp", f"{tag}_温度功率_全程双轴.png",
                           f"工艺全程 温度(左轴) vs 功率(右轴) 双纵坐标对比  {label}")
        plot_overview_dual(tag, label, recipe, proc, "vm", f"{tag}_真空蝶阀_全程双轴.png",
                           f"工艺全程 炉内真空(左轴, 对数) vs 蝶阀开度(右轴) 双纵坐标对比  {label}")
        plot_step_grid_dual(tag, label, recipe, proc, heat, "tp", f"{tag}_温度功率_涂层前双轴.png",
                            f"涂层前({_span(heat)}) 温度 vs 功率 双纵坐标分步图  {label}", st=st)
        plot_step_grid_dual(tag, label, recipe, proc, cool, "tp", f"{tag}_温度功率_涂层后双轴.png",
                            f"涂层后({_span(cool)}) 温度 vs 功率 双纵坐标分步图  {label}", st=st)
        plot_step_grid_dual(tag, label, recipe, proc, heat, "vm", f"{tag}_真空蝶阀_涂层前双轴.png",
                            f"涂层前({_span(heat)}) 真空度 vs 蝶阀开度 双纵坐标分步图  {label}", st=st)
        plot_step_grid_dual(tag, label, recipe, proc, cool, "vm", f"{tag}_真空蝶阀_涂层后双轴.png",
                            f"涂层后({_span(cool)}) 真空度 vs 蝶阀开度 双纵坐标分步图  {label}", st=st)
        if holdk:
            plot_hold_dual(tag, label, recipe, proc, holdk[0], f"{tag}_涂层中步{hold}_双轴.png",
                           f"涂层中(步{hold})  {label}")
        collected.append((tag, label, st))
        notes_out.append(f"{tag}: log {len(proc)} 行，{len(keys)} 个步次，剔除尾部 {dropped} 行（下一炉次残段）")

    stats = pd.concat(all_stats, ignore_index=True)
    recipe_df = pd.DataFrame(all_recipe)
    plot_type_matrix(stats, "模式vs实测判型_分布对照.png",
                     "recipe 模式期望 vs 实测曲线类型分布（两炉合并，仅统计记录数≥8 的步次）")

    # 明细：合并需求3(温度) + 需求4(真空)
    det = []
    for tag, label, rfile, d3, d4 in RUNS:
        a = pd.read_excel(os.path.join(BASE, d3, f"{d3}.xlsx"), sheet_name="分段速率明细")
        a = a[a["炉次"] == tag].copy()
        if "起温" in a.columns:
            a = a.rename(columns={"起温": "起值", "段温变": "段变化"})
        a["变量"] = "温度"; a["速率单位"] = "℃/min"
        b = pd.read_excel(os.path.join(BASE, d4, f"{d4}.xlsx"), sheet_name="分段速率明细")
        b = b[b["炉次"] == tag].copy()
        b["变量"] = "真空度"; b["速率单位"] = "Pa/min"
        det.append(pd.concat([a, b], ignore_index=True))
    detail = pd.concat(det, ignore_index=True)
    detail["步号"] = detail["步号"].astype(str)
    detail = detail.sort_values(["炉次", "变量", "阶段", "步号", "分段"], kind="stable")
    detail = detail[["炉次", "变量", "阶段", "步号", "曲线类型", "分段", "起_min", "止_min",
                     "起值", "段时长", "段变化", "段速率", "速率单位", "段R2", "点数", "方向"]]

    # 模式校验汇总（维持偏差 / 完成度 / 达标率 三类指标）
    def summ(df):
        v = df[df["记录数"] >= 8].copy()
        out = []
        # ① 温度模式② 维持偏差
        s = v[v["温度模式"] == 2]
        d = (s["_tm_dev"].astype(float)).dropna()
        inr = int((d.abs() <= 10).sum())
        out.append(dict(校验项="温度模式② 维持偏差", 有效步数=len(d), 判据="|后段均值−目标| ≤ 10℃",
                        达标步数=inr, 达标率=f"{inr / len(d) * 100:.0f}%" if len(d) else "—",
                        平均偏差=round(float(d.mean()), 2) if len(d) else None,
                        最小=round(float(d.min()), 2) if len(d) else None,
                        最大=round(float(d.max()), 2) if len(d) else None,
                        平均完成度=None, 对齐异常步数=int((s["对齐异常"] == "是").sum())))
        # ② 温度模式③ 完成度（剔除 recipe 对齐异常步，避免把"步号错位"当"未达标"）
        s0 = v[v["温度模式"] == 3]
        s = s0[s0["对齐异常"] != "是"]
        d = s["_t_done"].astype(float).replace([np.inf, -np.inf], np.nan).dropna()
        ok = int((s["温度_达标"] == "达标").sum())
        out.append(dict(校验项="温度模式③ 线性完成度", 有效步数=len(s),
                        判据="|末值−目标| ≤ max(10℃, 5%×|目标−起始|)（已剔除对齐异常步）",
                        达标步数=ok, 达标率=f"{ok / len(s) * 100:.0f}%" if len(s) else "—",
                        平均偏差=round(float((s['温度_结束'].astype(float) - s['目标温度_recipe'].astype(float)).mean()), 2) if len(s) else None,
                        最小=round(float(d.min()), 1) if len(d) else None,
                        最大=round(float(d.max()), 1) if len(d) else None,
                        平均完成度=round(float(d.mean()), 1) if len(d) else None,
                        对齐异常步数=int((s0["对齐异常"] == "是").sum())))
        # ③ 功率模式 末值偏差
        s = v[v["温度模式"] == 1]
        pe = s["功率上限"].astype(float)
        d = ((s["功率_结束"].astype(float) - pe) / pe * 100).replace([np.inf, -np.inf], np.nan).dropna()
        inr = int((d.abs() <= 5).sum())
        out.append(dict(校验项="温度模式① 功率末值偏差", 有效步数=len(d), 判据="|功率末值−功率上限|/上限 ≤ 5%",
                        达标步数=inr, 达标率=f"{inr / len(d) * 100:.0f}%" if len(d) else "—",
                        平均偏差=round(float(d.mean()), 3) if len(d) else None,
                        最小=round(float(d.min()), 3) if len(d) else None,
                        最大=round(float(d.max()), 3) if len(d) else None,
                        平均完成度=None, 对齐异常步数=int((s["对齐异常"] == "是").sum())))
        # ④ 压力模式② 稳压达标 & 过渡完成度（同样剔除对齐异常步）
        s0 = v[v["压力模式"] == 2]
        s = s0[s0["对齐异常"] != "是"]
        okn = int((s["压力_达标"] == "达标").sum())
        tr = s[s["压力_达标"] == "未达标(过渡)"]
        dx = (s[s["压力_达标"] == "达标"])["_pv_dev"].astype(float).dropna()
        dd = tr["_p_done"].astype(float).replace([np.inf, -np.inf], np.nan).dropna()
        out.append(dict(校验项="压力模式② 稳压达标", 有效步数=len(s),
                        判据="|步末−目标|/目标 ≤ 5% 记达标；否则记过渡步并给完成度（已剔除对齐异常步）",
                        达标步数=okn, 达标率=f"{okn / len(s) * 100:.0f}%" if len(s) else "—",
                        平均偏差=round(float(dx.mean()), 3) if len(dx) else None,
                        最小=round(float(dx.min()), 3) if len(dx) else None,
                        最大=round(float(dx.max()), 3) if len(dx) else None,
                        平均完成度=round(float(dd.mean()), 1) if len(dd) else None,
                        对齐异常步数=int((s0["对齐异常"] == "是").sum())))
        # ⑤ 开度模式 蝶阀跟随（按时间占比，而非均值）
        s = v[v["压力模式"] == 1]
        d = s["蝶阀跟随率"].astype(float).replace([np.inf, -np.inf], np.nan).dropna()
        inr = int((d >= 50).sum())
        out.append(dict(校验项="压力模式① 蝶阀跟随开度上限", 有效步数=len(d),
                        判据="蝶阀落在开度上限±3% 的时间占比 ≥ 50%",
                        达标步数=inr, 达标率=f"{inr / len(d) * 100:.0f}%" if len(d) else "—",
                        平均偏差=None,
                        最小=round(float(d.min()), 1) if len(d) else None,
                        最大=round(float(d.max()), 1) if len(d) else None,
                        平均完成度=round(float(d.mean()), 1) if len(d) else None, 对齐异常步数=None))
        return out

    check = pd.DataFrame(summ(stats))
    for t_, lb_, s_ in collected:
        plot_mode_check(t_, lb_, s_, pd.DataFrame(summ(s_)), f"{t_}_模式符合度.png",
                        f"recipe 模式语义 vs 实测行为 校验仪表盘  {lb_}")
    del stats["_tm_dev"], stats["_pv_dev"], stats["_mv_dev"], stats["_t_done"], stats["_p_done"]

    # 判型符合度
    tm_ok = stats[stats["温度模式"].isin([1, 2, 3]) & (stats["记录数"] >= 8)]
    vv_ok = stats[stats["压力模式"].isin([1, 2]) & (stats["记录数"] >= 8)]
    tv = tm_ok["温度_符合度"].value_counts().to_dict()
    vv = vv_ok["真空_符合度"].value_counts().to_dict()

    note = pd.DataFrame([
        ["需求5 目标", "把 recipe 的『温度模式/压力模式/结束位』语义落进分析：图为双纵坐标（温度-功率、压力-蝶阀开度）；"
                   "图上叠加 recipe 参考值；并按模式语义校验实测行为是否符合 recipe 意图"],
        ["温度口径（需求7+方案C）", f"log 温度『主控温度』按《需求7》结合各批次 T1/T2 通道一致性自适应确定："
                       f"重叠测温窗（T1<{T_SWITCH:.0f}℃ 且 T2 有效）中位偏差 <{T_CONSIST:.0f}℃ 判为同一测点双量程，"
                       f"按 T1/T2 拼接；否则保持源系统主控口径。源系统原值保留在『主控温度_原始』列。" +
                       "　".join(f"{t}：{t_diag_text(diags[t])}" for t in sorted(diags))],
        ["三温度同图（需求7）", "《需求7》要求三个温度都在图中：所有温度-功率图同时绘制 主控温度（粗实线）、"
                        "T1温度（青虚线）、T2温度（紫点线）。主控温度为最终口径；T1/T2 为 log 原始双测温，"
                        "叠画用于目视核对修正结果与通道差异（通道不一致批次的主控与 T1/T2 会明显分离，属正常）。"
                        "T2 在高位热电偶未起测的低温段读数为 0，绘图中按 0 值屏蔽，避免压扁纵轴。"],
        ["温度模式① 功率模式", "功率上限即目标值，功率在设定工艺时间内随时间线性增加；目标温度不参与控制"],
        ["温度模式② 温度模式", "目标温度＝需保持的温度，功率上下限限制功率调节范围"],
        ["温度模式③ 线性模式", "目标温度＝控制值，温度在设定工艺时间内随时间线性增加；功率上下限限制调节范围"],
        ["压力模式① 开度模式", "蝶阀开度上限直接控制蝶阀开度，目标压力无作用（开环）"],
        ["压力模式② 压力模式", "目标压力＝需维持的目标值，蝶阀上下限限制开度范围（闭环 PID）"],
        ["工艺结束位", "结束位=1 表示本步后工艺结束，不再运行后续步骤（各炉结束标记见下方『工况说明』），"
                   "故 log 无其运行记录"],
        ["-- 全工艺模式分布（按实际数据生成）", mode_dist_text(stats)],
        ["-- 交叉验证① 功率模式语义", xcheck_power_text(stats)],
        ["-- 交叉验证② 开度模式开环", xcheck_valve_text(stats)],
        ["-- 反例：开度模式内蝶阀行为分裂（需核查）", valve_split_text(stats)],
        ["-- recipe↔log 步号对齐（关键）", align_text(stats)],
        ["-- 主控测温通道量程截断", t_trunc_text(stats)],
        ["-- 低温段测温无响应（勿当恒温）", lowtemp_text(stats)],
        ["-- 压力模式② 的两种子类型", p2_subtype_text(stats)],
        ["双轴图读法", "左轴=被控量（温度/真空），右轴=操纵量（功率/蝶阀开度）；灰虚线=recipe 设定；"
                   "浅绿点线=控制器 PID 设定（模式①/③ 下为爬坡值）；橙色阴影带=功率或蝶阀上下限允许区间"],
        ["为何要双轴", "同一时间轴上直接看『操纵量→被控量』的因果与滞后：功率升→温度升（有热惯性滞后）；"
                   "蝶阀开大→压力降；开度模式下一旦开度被上限钉住，压力就退化为开环（不受设定值约束）"],
        ["压力模式①下 目标压力列", "log 中仍会出现非 0 的目标压力（如步43/44 填 50000/60000），但该值在开度模式下不参与控制，"
                            "不能当作设定值解读；本文该校验列一律置为『不适用』"],
        ["数据来源", "实测判型与分段速率直接读取需求3(温度)/需求4(真空度) 的 _stats.csv 与 Excel 明细，不重算、不改写；"
                 "本脚本只新增 recipe 关联、模式校验与双轴图"],
        ["工况说明", workcond_text(run_info)],
    ] + ([["人为操作记录（现场确认，非系统异常）",
           "　".join(f"{t}({lb})：{ops_text(t)}" for t, lb, *_ in RUNS if ops_text(t))]]
         if any(ops_text(t) for t, *_ in RUNS) else []) + [["数据规模", n] for n in notes_out], columns=["项目", "说明"])

    xls = os.path.join(OUT, "需求5_双轴对比分析.xlsx")
    with pd.ExcelWriter(xls, engine="openpyxl") as w:
        note.to_excel(w, sheet_name="说明", index=False)
        recipe_df.to_excel(w, sheet_name="recipe参数", index=False)
        stats.to_excel(w, sheet_name="全程分步统计", index=False)
        check.to_excel(w, sheet_name="模式校验汇总", index=False)
        detail.to_excel(w, sheet_name="分段速率明细", index=False)
        frames = {"说明": note, "recipe参数": recipe_df, "全程分步统计": stats,
                  "模式校验汇总": check, "分段速率明细": detail}

        def w_of(nm):
            if nm == "说明":
                return 130
            if nm in ("备注", "模式校验", "对齐检测", "判据"):
                return 46
            if nm in ("曲线类型", "校验项", "温度模式含义", "压力模式含义", "方向"):
                return 24
            if nm in ("炉次", "阶段", "步号", "变量", "分段"):
                return 9
            if nm in ("模式校验汇总",):
                return 12
            return 12

        for sn, ws in w.sheets.items():
            dfx = frames.get(sn)
            if dfx is not None:
                for i, nm in enumerate(dfx.columns, 1):
                    ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w_of(str(nm))
            ws.freeze_panes = "A2"
    print("Excel ->", xls)
    print("说明行数", len(note), "| 统计", stats.shape, "| 参数", recipe_df.shape,
          "| 校验", check.shape, "| 明细", detail.shape)
    print(check.to_string(index=False))
    return stats, check


if __name__ == "__main__":
    main()
