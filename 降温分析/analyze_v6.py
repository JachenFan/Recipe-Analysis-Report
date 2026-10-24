# -*- coding: utf-8 -*-
"""
CVD 工艺分析 v6（需求6）
========================================================
需求6 原文：
  1) 图表功率单位错误，单位应该为 kW。（注：脚本 v5 误标为 %，实测列 0~302.88 与
     recipe 功率上限 0~302.85 逐位吻合 → 量纲确为 kW，本次仅改轴标签/单位，不换算）
  2) 图表各系列曲线颜色区分度不够，请优化。
  3) 最新图表没有升降温速率拟合，参考需求2、3进行补充。
  4) 除总图外，为每一个步号都单独生成一张分辨率更高的图片，用于制作PPT。
  5) 针对每个 recipe，各生成一份工艺分析汇报PPT（由 make_ppt_v6.py 承担）。

本脚本负责 1~4 项：
  A. 全新高区分度配色（实测线 / 控制器设定 / recipe 设定 / 上下限 / 拟合线 各自独立颜色）
  B. 叠加需求3(温度) / 需求4(真空度) 的『分段最小二乘拟合』线与速率标注（单一事实来源，不重算）
  C. 输出：全程双轴总览、升温/降温分段双轴网格、恒温段、速率拟合总览、每步高清双轴图
  D. 输出 Excel：说明 / 分步速率与结论 / 图片索引

不改动需求3/4/5 的任何产物，只读取其 _stats.csv 与 Excel 明细。
========================================================
"""
import os
import sys
import csv
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from temp_fix import diag_text as t_diag_text  # noqa: E402  需求7 源系统偏差诊断文案（按数据生成）
from batch_notes import ops_text_of_step  # noqa: E402  人为操作记录（现场确认，进结论摘要）

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_v5 as v5  # noqa: E402  复用数据加载 / 统计 / 判型
import project_paths as pp

warnings_mod = __import__("warnings")
warnings_mod.filterwarnings("ignore")

BASE = v5.BASE
SIMU = v5.SIMU
OUT = pp.out("需求6_分步高清图与汇报PPT")
FIG = os.path.join(OUT, "图表")
HD = os.path.join(OUT, "高清图")
for d in (OUT, FIG, HD):
    os.makedirs(d, exist_ok=True)

# ---------------------------------------------------------------- 配色（需求6-2）
# 目标：实测线 / 控制器设定 / recipe 设定 / 上下限 / 拟合线 / 区间 六类颜色互不相近
C = dict(
    # 温度-功率图
    T="#1864ab",        # 主控温度（实测，深蓝）
    PID="#7048e8",      # 控制器 PID 设定（紫）
    TSP="#0ca678",      # recipe 目标温度（青绿）
    P="#e03131",        # 电源总功率反馈（实测，红）
    PMAX="#f76707",     # recipe 功率上限（橙）
    PMIN="#846358",     # recipe 功率下限（棕）
    # 真空-蝶阀图
    V="#1864ab",        # 炉内真空（实测，深蓝）
    PSP="#0ca678",      # recipe 目标压力（青绿）
    MV="#e03131",       # 蝶阀开度反馈（实测，红）
    VMAX="#f76707",     # recipe 开度上限（橙）
    VMIN="#846358",     # recipe 开度下限（棕）
    BAND="#ffd8a8",     # 开度允许区间
    # 拟合 / 判定
    FIT="#d6336c",      # 升降温速率拟合线（品红）
    OK="#2f9e44", BAD="#e03131", PART="#f08c00", NA="#adb5bd",
    GRID="#dee2e6",
)
PHASE_BG = {v5.phases.PRE: "#fff4e6", v5.phases.HOLD: "#e7f5ff", v5.phases.POST: "#ebfbee"}

# 功率单位（需求6-1）
P_UNIT = "kW"

RUNS = v5.RUNS

# ---- PPT 图片槽位(px) 与画布比例 -----------------------------------------
# slidep 转换器忽略 objectFit、恒定按 cover 处理：槽位比例 != 图片比例就会裁切。
# 因此把 matplotlib 画布比例做成与槽位比例完全一致 → 零裁剪、纯等比缩放。
STEP_SLOT = (588, 300)      # 逐步页卡片内图片槽位(604 - 8*2 = 588)
OV_SLOT = (1240, 468)       # 总览页整幅图片槽位
STEP_FIG = (12.6, 12.6 * STEP_SLOT[1] / STEP_SLOT[0])   # 12.6 x 6.4286  (r=1.96000)
OV_FIG = (12.4, 12.4 * OV_SLOT[1] / OV_SLOT[0])         # 12.4 x 4.6800  (r=2.64957)


# ================================================================ 速率拟合来源
def load_fit_segments():
    """读取需求3(温度) / 需求4(真空度) 的分段最小二乘明细 → {(tag,步号,kind):[seg,...]}"""
    src = [
        ("T", pp.out("需求3_工艺全程分析", "需求3_工艺全程分析.xlsx"), "起温", "段温变", "℃/min"),
        ("V", pp.out("需求4_真空度蝶阀分析", "需求4_真空度蝶阀分析.xlsx"), "起值", "段变化", "Pa/min"),
    ]
    out = {}
    for kind, path, col_y0, col_dy, unit in src:
        df = pd.read_excel(path, sheet_name="分段速率明细")
        for _, r in df.iterrows():
            key = (str(r["炉次"]), str(r["步号"]), kind)
            out.setdefault(key, []).append(dict(
                分段=r["分段"], t0=_f(r["起_min"]), t1=_f(r["止_min"]), y0=_f(r[col_y0]),
                速率=_f(r["段速率"]), R2=_f(r["段R2"]), 点数=_f(r["点数"]),
                方向=str(r.get("方向", "")), 单位=unit,
            ))
    return out


def load_fit_r2():
    """从需求3/4 的 _stats.csv 取『推荐速率』对应的拟合 R²（用于标注）"""
    out = {}
    for kind, d in [("T", "需求3_工艺全程分析"), ("V", "需求4_真空度蝶阀分析")]:
        p = os.path.join(BASE, d, "_stats.csv")
        s = pd.read_csv(p, encoding="utf-8-sig")
        for _, r in s.iterrows():
            out[(str(r["炉次"]), str(r["步号"]), kind)] = dict(
                速率=_f(r.get("推荐速率")), R2=_f(r.get("拟合R2")),
                含义=str(r.get("速率含义", "")), 类型=str(r.get("曲线类型", "")),
                可信=str(r.get("数据可信度", "")),
            )
    return out


def _f(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def fit_line(seg, el):
    """把某段拟合（斜率+起点）还原成 t-y 折线，用于叠加到实测曲线上"""
    t0, t1, y0, k = seg["t0"], seg["t1"], seg["y0"], seg["速率"]
    if not (np.isfinite(t0) and np.isfinite(t1) and np.isfinite(y0) and np.isfinite(k)):
        return None
    tt = np.array([t0, t1], float)
    yy = y0 + k * (tt - t0)
    return tt, yy


def main_seg(segs):
    """主段 = 段变化绝对值最大的一段（即『推荐速率』来源段）"""
    if not segs:
        return None
    return max(segs, key=lambda s: abs(s["速率"] * (s["t1"] - s["t0"]) if np.isfinite(s["速率"]) else 0.0))


# ================================================================ 单步高清图（需求6-4）
def _annot(ax, lines, loc="upper left", fs=8.0, color="#212529", fc="#ffffff", ec="#ced4da"):
    lines = [ln for ln in lines if ln]
    if not lines:
        return
    xy = {"upper left": (0.018, 0.965), "lower left": (0.018, 0.045),
          "upper right": (0.972, 0.965), "lower right": (0.972, 0.045)}[loc]
    ha = "right" if "right" in loc else "left"
    va = "top" if "upper" in loc else "bottom"
    ax.text(xy[0], xy[1], "\n".join(lines), transform=ax.transAxes, fontsize=fs, color=color,
            ha=ha, va=va, linespacing=1.45, zorder=9,
            bbox=dict(fc=fc, ec=ec, lw=0.8, alpha=1.0, pad=3.2, boxstyle="round,pad=0.38"))


def _num(v, nd=0, unit="", pre="", post=""):
    """安全数字格式化：NaN/None → 空串（避免图上出现 nan%）"""
    try:
        x = float(v)
        if x != x:
            return ""
        return f"{pre}{x:.{nd}f}{unit}{post}"
    except Exception:
        return ""


def _sgn(v, nd=0, unit=""):
    """带符号格式化：NaN/None → 空串"""
    try:
        x = float(v)
        if x != x:
            return ""
        return f"{x:+.{nd}f}{unit}"
    except Exception:
        return ""


def _dual_axes(ax):
    ax2 = ax.twinx()
    return ax, ax2


def _rate_text(ft, kind, unit):
    if not ft:
        return "拟合速率：数据不足"
    r = ft.get("速率", np.nan)
    if not np.isfinite(r):
        return "拟合速率：数据不足"
    r2 = ft.get("R2", np.nan)
    s = f"推荐速率 {r:+.3f} {unit}" if kind == "T" else f"推荐速率 {r:+.1f} {unit}"
    if np.isfinite(r2):
        s += f"（R²={r2:.4f}）"
    return s


def make_step_fig(tag, label, recipe, proc, key, kind, outpath, st_row, fitT, fitV, dpi=175):
    """单步高清双轴图：kind='tp' 温度-功率 / 'vm' 真空-蝶阀"""
    s, p = key
    lab = v5.step_label(s, p)
    sub, el = v5.series_of(proc, s, p)
    rc = recipe.get(s, {})
    drawn = False
    fig, ax = plt.subplots(figsize=STEP_FIG)
    fig.patch.set_facecolor("white")
    ax2 = ax.twinx()

    if kind == "tp":
        tm = int(rc.get("温度模式", 0) or 0)
        t = np.asarray(sub[v5.T_COL], float)
        ok = np.isfinite(t) & (t > 0)
        cand = []
        if ok.any():
            # 需求7：主控 + T1 + T2 三条温度同图（T1/T2 细虚线叠在主线之上）
            cand += v5.plot_three_temps(ax, sub, el, lw_main=1.7, lw_sub=1.05, z_base=4,
                                        col_main=C["T"], col_t1=v5.COL["t1"], col_t2=v5.COL["t2"])
            drawn = True
        pid = np.asarray(sub[v5.PID_COL], float)
        okp = np.isfinite(pid) & (pid > 0)
        if okp.any():
            ax.plot(el[okp], pid[okp], color=C["PID"], lw=1.1, ls="-.", alpha=0.9,
                    label="控制器 PID 设定 (℃)", zorder=3)
            cand += list(pid[okp])
        if tm in (2, 3) and rc.get("目标温度"):
            ax.axhline(rc["目标温度"], color=C["TSP"], ls="--", lw=1.5,
                       label="recipe 目标温度 (℃)", zorder=2)
            cand.append(rc["目标温度"])
        # 拟合线（温度）
        segs = fitT.get((tag, lab, "T"))
        fit_lab = True
        for sg in (segs or []):
            ln = fit_line(sg, el)
            if ln is not None:
                ax.plot(ln[0], ln[1], color=C["FIT"], lw=1.7, ls="--", alpha=0.9,
                        label=("升降温速率拟合线" if fit_lab else None), zorder=6)
                fit_lab = False
                cand += list(ln[1])
        if cand:
            lo, hi = min(cand), max(cand)
            pad = max(6.0, 0.07 * (hi - lo + 1e-9))
            ax.set_ylim(lo - pad, hi + pad)
        ax.set_ylabel("温度 (℃)", color=C["T"])
        ax.tick_params(axis="y", colors=C["T"], labelsize=9)

        pw = np.asarray(sub[v5.P_COL], float)
        pv = list(pw[np.isfinite(pw)])
        if pv:
            ax2.plot(el, pw, color=C["P"], lw=1.5, alpha=0.95, label=f"电源总功率反馈 ({P_UNIT})", zorder=4)
            drawn = True
        if rc.get("功率上限"):
            ax2.axhline(rc["功率上限"], color=C["PMAX"], ls="--", lw=1.4, label=f"recipe 功率上限 ({P_UNIT})")
            pv.append(rc["功率上限"])
        if rc.get("功率下限"):
            ax2.axhline(rc["功率下限"], color=C["PMIN"], ls=":", lw=1.4, label=f"recipe 功率下限 ({P_UNIT})")
            pv.append(rc["功率下限"])
        if pv:
            lo2, hi2 = min(pv), max(pv)
            pd2 = max(4.0, 0.10 * (hi2 - lo2 + 1e-9))
            ax2.set_ylim(lo2 - pd2, hi2 + pd2)
        ax2.set_ylabel(f"功率 ({P_UNIT})", color=C["P"])
        ax2.tick_params(axis="y", colors=C["P"], labelsize=9)

        # 左上角：速率拟合结论
        ft = fitT.get((tag, lab, "T")) and None
        # 主段速率（来自 _stats 推荐速率）
        mr = st_row.get("温度_推荐速率")
        mtype = st_row.get("温度_实测判型")
        r2v = fitT.get(("__r2__", tag, lab, "T"), np.nan)
        lines = []
        if mr is not None and mr == mr:
            s_ = f"温度拟合速率 {float(mr):+.3f} ℃/min"
            if r2v == r2v:
                s_ += f"（R²={float(r2v):.4f}）"
            lines.append(s_)
        if isinstance(mtype, str):
            lines.append(f"实测判型：{mtype}")
        _annot(ax, lines, "upper left", fs=9.0, color="#0b3d91", fc="#eef4ff", ec="#b6ccf0")
        # 右上角：模式与达标
        right = []
        t0s = _num(st_row.get("温度_起始"), 0, "℃")
        t1s = _num(st_row.get("温度_结束"), 0, "℃")
        jud = str(st_row.get("温度_达标", "—"))
        if tm == 1:
            right.append(f"[① 功率模式] 功率线性升至上限 {_num(rc.get('功率上限'), 1, ' ' + P_UNIT)}")
            dev = _sgn(float(st_row.get("功率_结束", np.nan)) - float(rc.get("功率上限", np.nan)), 1, " " + P_UNIT)
            right.append("功率末值 " + _num(st_row.get("功率_结束"), 1, " " + P_UNIT)
                         + (f"（{dev}）" if dev else ""))
            right.append(f"温度被动响应 {t0s}→{t1s}")
            right.append("判定：" + jud)
        elif tm == 2:
            right.append(f"[② 温度模式] 目标保持 {_num(rc.get('目标温度'), 0, '℃')}")
            dv = _sgn(st_row.get("温度_偏差"), 0, "℃")
            right.append(f"后段均值 {_num(st_row.get('温度_均值'), 0, '℃')}"
                         + (f" ｜ 维持偏差 {dv}" if dv else ""))
            right.append("判定：" + jud)
        else:
            right.append(f"[③ 线性模式] 目标 {_num(rc.get('目标温度'), 0, '℃')}")
            cc = _num(st_row.get("温度_完成度"), 0, "%")
            right.append(f"实测 {t0s}→{t1s}" + (f" ｜ 完成度 {cc}" if cc else ""))
            right.append("判定：" + jud)
        _annot(ax, right, "upper right", fs=9.0,
               color=C["OK"] if st_row.get("温度_达标") == "达标" else "#842029",
               fc="#f1fbf4" if st_row.get("温度_达标") == "达标" else "#fff5f5",
               ec="#b2e0c0" if st_row.get("温度_达标") == "达标" else "#f0b8b8")
        # 标题
        ax.set_title(f"步 {lab}   温度—功率 双纵坐标（{v5.MODE_T.get(tm, '—')}）   "
                     f"recipe 目标温度 {rc.get('目标温度', 0):.0f}℃ / 功率 {rc.get('功率下限', 0):.0f}~{rc.get('功率上限', 0):.0f} {P_UNIT}",
                     fontsize=12.5, pad=10)
    else:
        pm = int(rc.get("压力模式", 0) or 0)
        v = np.asarray(sub[v5.V_COL], float)
        okv = np.isfinite(v) & (v > 0)
        cand = []
        if okv.any():
            ax.plot(el[okv], v[okv], color=C["V"], lw=1.7, label="炉内真空 (Pa)", zorder=4)
            cand += list(v[okv])
            drawn = True
        if pm == 2 and rc.get("目标压力"):
            ax.axhline(rc["目标压力"], color=C["PSP"], ls="--", lw=1.5, label="recipe 目标压力 (Pa)")
            cand.append(rc["目标压力"])
        segs = fitV.get((tag, lab, "V"))
        fit_lab = True
        for sg in (segs or []):
            ln = fit_line(sg, el)
            if ln is not None and np.all(ln[1] > 0):
                ax.plot(ln[0], ln[1], color=C["FIT"], lw=1.7, ls="--", alpha=0.9,
                        label=("升压/降压速率拟合线" if fit_lab else None), zorder=6)
                fit_lab = False
                cand += list(ln[1])
        if cand and min(cand) > 0:
            if max(cand) / max(min(cand), 1e-9) > 50:
                ax.set_yscale("log")
            else:
                lo, hi = min(cand), max(cand)
                pd = max(0.02 * (hi - lo + 1e-9), 1.0)
                ax.set_ylim(lo - pd, hi + pd)
        ax.set_ylabel("炉内真空 (Pa)", color=C["V"])
        ax.tick_params(axis="y", colors=C["V"], labelsize=9)

        mv = np.asarray(sub[v5.MV_COL], float)
        mvf = mv[np.isfinite(mv)]
        if mvf.size:
            ax2.plot(el, mv, color=C["MV"], lw=1.5, alpha=0.95, label="数控蝶阀开度反馈 (%)", zorder=4)
            drawn = True
        lo_, hi_ = rc.get("蝶阀开度下限"), rc.get("蝶阀开度上限")
        if np.isfinite(lo_ if lo_ is not None else np.nan) and np.isfinite(hi_ if hi_ is not None else np.nan):
            ax2.axhspan(lo_, hi_, color=C["BAND"], alpha=0.30, zorder=0,
                        label="recipe 开度允许区间")
        if hi_ is not None:
            ax2.axhline(hi_, color=C["VMAX"], ls="--", lw=1.4, label="recipe 开度上限 (%)")
        if lo_:
            ax2.axhline(lo_, color=C["VMIN"], ls=":", lw=1.4, label="recipe 开度下限 (%)")
        vals = list(mvf) if mvf.size else []
        for z in (lo_, hi_):
            if z is not None:
                vals.append(float(z))
        lo2, hi2 = min(vals + [0.0]), max(vals + [10.0])
        pd2 = max(4.0, 0.10 * (hi2 - lo2 + 1e-9))
        ax2.set_ylim(max(-6, lo2 - pd2), min(106, hi2 + pd2))
        ax2.set_ylabel("蝶阀开度 (%)", color=C["MV"])
        ax2.tick_params(axis="y", colors=C["MV"], labelsize=9)

        left = []
        vr = st_row.get("真空_推荐速率")
        vtype = st_row.get("真空_实测判型")
        if vr is not None and vr == vr:
            try:
                left.append(f"真空拟合速率 {float(vr):+.1f} Pa/min")
            except Exception:
                left.append(f"真空拟合速率 {vr}")
        if isinstance(vtype, str):
            left.append(f"实测判型：{vtype}")
        _annot(ax, left, "upper left", fs=9.0, color="#0b3d91", fc="#eef4ff", ec="#b6ccf0")

        right = []
        pj = str(st_row.get("压力_达标", "—"))
        if pm == 1:
            right.append(f"[① 开度模式] 蝶阀开度上限 {_num(hi_, 1, '%')} 直接控制（开环）")
            fr = st_row.get("蝶阀跟随率")
            right.append(f"实测蝶阀均值 {_num(st_row.get('蝶阀_均值'), 2, '%')}"
                         + (f" ｜ 跟随率 {_num(fr, 0, '%')}" if _num(fr) else ""))
            right.append("判定：" + pj)
        else:
            right.append(f"[② 压力模式] 目标压力 {_num(rc.get('目标压力'), 0, ' Pa')}（闭环）")
            dd = _sgn(st_row.get("压力_偏差pct"), 2, "%")
            right.append(f"步末 {_num(st_row.get('真空_结束'), 0, ' Pa')}"
                         + (f" ｜ 偏差 {dd}" if dd else " ｜ 过渡步"))
            right.append("判定：" + pj)
        _annot(ax, right, "upper right", fs=9.0,
               color=C["OK"] if str(st_row.get("压力_达标", "")).startswith("达标") else "#842029",
               fc="#f1fbf4" if str(st_row.get("压力_达标", "")).startswith("达标") else "#fff5f5",
               ec="#b2e0c0" if str(st_row.get("压力_达标", "")).startswith("达标") else "#f0b8b8")
        ax.set_title(f"步 {lab}   真空—蝶阀开度 双纵坐标（{v5.MODE_P.get(pm, '—')}）   "
                     f"recipe 目标压力 {rc.get('目标压力', 0):.0f} Pa / 开度 "
                     f"{rc.get('蝶阀开度下限', 0):.0f}~{rc.get('蝶阀开度上限', 0):.0f}%",
                     fontsize=12.5, pad=10)

    ax.set_xlabel("本步累计时间 (min)", fontsize=10)
    ax.grid(alpha=0.25)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    fig.legend(h1 + h2, l1 + l2, loc="lower center", ncol=len(h1) + len(h2), fontsize=8.2,
               frameon=False, bbox_to_anchor=(0.5, 0.006))
    note = st_row.get("对齐检测")
    if isinstance(note, str) and note != "—" and st_row.get("对齐异常") == "是":
        ax.text(0.5, 0.02, f"⚠ recipe 对齐异常：{note}", transform=ax.transAxes, fontsize=8.5,
                color="#e8590c", ha="center", va="bottom",
                bbox=dict(fc="#fff4e6", ec="#ffd8a8", lw=0.7, alpha=0.95, pad=2.5))
    if not drawn:
        ax.text(0.5, 0.5, "有效采样点不足，无法绘图（见需求2 说明）", ha="center", va="center",
                fontsize=13, color="#868e96", transform=ax.transAxes)
    fig.tight_layout(rect=[0, 0.10, 1, 1])
    v5._save(fig, outpath, dpi=dpi)
    plt.close(fig)
    return drawn


# ================================================================ 总览 / 网格（需求6-1/2/3）
def _step_spans(proc):
    return v5._step_spans(proc)


def _concat(proc, col, k=1):
    return v5._concat(proc, col, k)


def plot_overview_dual(tag, label, recipe, proc, kind, fname, title):
    """全程双轴总览（kW 单位 + 新配色）"""
    spans = _step_spans(proc)
    fig, ax = plt.subplots(figsize=OV_FIG)
    fig.patch.set_facecolor("white")
    ax2 = ax.twinx()
    if kind == "tp":
        X, T = _concat(proc, v5.T_COL)
        _, P = _concat(proc, v5.P_COL)
        ax.plot(X, T, color=C["T"], lw=1.1, label="主控温度 (℃, 左轴)")
        # 需求7：T1/T2 原始双测温同时入图（0 值=高位偶未起测，屏蔽以免压扁纵轴）
        for col, c, ls, nm in ((v5.T1_COL, v5.COL["t1"], "--", "T1温度 (℃, 左轴)"),
                               (v5.T2_COL, v5.COL["t2"], ":", "T2温度 (℃, 左轴)")):
            _, yy = _concat(proc, col)
            yy = np.asarray(yy, float)
            ax.plot(X, np.where(yy > 0, yy, np.nan), color=c, lw=0.85, ls=ls, alpha=0.9, label=nm, zorder=5)
        ax2.plot(X, P, color=C["P"], lw=0.9, alpha=0.9, label=f"电源总功率反馈 ({P_UNIT}, 右轴)")
        xt, ts, xp, ps, xl, pl = [], [], [], [], [], []
        for (s, p), (a, b) in spans.items():
            rc = recipe.get(s, {})
            if rc.get("温度模式", 0) in (2, 3) and rc.get("目标温度", 0):
                xt += [a, b]; ts += [rc["目标温度"]] * 2
            if rc.get("温度模式", 0) == 1 and rc.get("功率上限"):
                xp += [a, b]; ps += [rc["功率上限"]] * 2
            if rc.get("功率下限"):
                xl += [a, b]; pl += [rc["功率下限"]] * 2
        if ts:
            ax.step(xt, ts, where="post", color=C["TSP"], ls="--", lw=1.3, label="recipe 目标温度 (℃, 左轴)")
        if ps:
            ax2.step(xp, ps, where="post", color=C["PMAX"], ls="--", lw=1.1, alpha=0.8,
                     label=f"recipe 功率上限 ({P_UNIT}, 右轴)")
        if pl:
            ax2.step(xl, pl, where="post", color=C["PMIN"], ls=":", lw=1.1,
                     label=f"recipe 功率下限 ({P_UNIT}, 右轴)")
        ax.set_ylabel("温度 (℃)", color=C["T"])
        ax2.set_ylabel(f"功率 ({P_UNIT})", color=C["P"])
        ax.tick_params(axis="y", colors=C["T"]); ax2.tick_params(axis="y", colors=C["P"])
    else:
        X, V = _concat(proc, v5.V_COL)
        _, M = _concat(proc, v5.MV_COL)
        ax.plot(X, V, color=C["V"], lw=1.0, label="炉内真空 (Pa, 左轴, 对数)")
        ax.set_yscale("log")
        ax2.plot(X, M, color=C["MV"], lw=0.9, alpha=0.9, label="数控蝶阀开度反馈 (%, 右轴)")
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
            ax.step(xs, vs, where="post", color=C["PSP"], ls="--", lw=1.3, label="recipe 目标压力 (Pa, 左轴)")
        if up_:
            ax2.step(xsu, up_, where="post", color=C["VMAX"], ls="--", lw=1.1, alpha=0.85,
                     label="recipe 蝶阀开度上限 (%, 右轴)")
        if dn_:
            ax2.step(xsd, dn_, where="post", color=C["VMIN"], ls=":", lw=1.1,
                     label="recipe 蝶阀开度下限 (%, 右轴)")
        ax.set_ylabel("炉内真空 (Pa)", color=C["V"])
        ax2.set_ylabel("蝶阀开度 (%)", color=C["MV"])
        ax.tick_params(axis="y", colors=C["V"]); ax2.tick_params(axis="y", colors=C["MV"])
    first = True
    for (s, p), (a, b) in spans.items():
        if p == 1:
            if not first:
                ax.axvline(a, color=C["GRID"], lw=0.5)
            first = False
        ax.axvspan(a, b, color=PHASE_BG[v5.phase_of(s)], alpha=0.4, zorder=0)
    # 阶段文字标注：锚点与步号区间都按本批实际步次生成（不写死 1–24 / 25 / 26–45）
    for ph, c in ((v5.phases.PRE, "#e8590c"), (v5.phases.HOLD, "#1971c2"), (v5.phases.POST, "#2f9e44")):
        ks = sorted(k for k in spans if v5.phase_of(k[0]) == ph)
        if ks:
            xx = spans[ks[0]][0]
            rng = (f"步{ks[0][0]}" if ks[0][0] == ks[-1][0] else f"步{ks[0][0]}–{ks[-1][0]}")
            ax.text(xx + 1, ax.get_ylim()[1], f" {ph}({rng})", fontsize=9, color=c,
                    va="top", ha="left")
    ax.set_xlabel("累计时间 (min，自首步起)")
    ax.set_title(title, fontsize=14)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=7.8, ncol=3, framealpha=0.92)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    v5._save(fig, os.path.join(FIG, fname), dpi=130)
    plt.close(fig)
    return fname


def plot_step_grid_dual(tag, label, recipe, proc, keys, kind, fname, title, st=None, fit=None):
    """分段网格（kW + 新配色 + 每子图叠加拟合线与速率标注）"""
    n = len(keys)
    ncol = 6
    nrow = int(np.ceil(n / ncol))
    lut = {str(r["步号"]): r for _, r in st.iterrows()} if st is not None else {}
    fit = fit or {}
    fig, axes = plt.subplots(nrow, ncol, figsize=(3.7 * ncol, 3.1 * nrow))
    axes = np.atleast_1d(axes).ravel()
    for i, (s, p) in enumerate(keys):
        ax = axes[i]
        sub, el = v5.series_of(proc, s, p)
        rc = recipe.get(s, {})
        lab = v5.step_label(s, p)
        tm, pm = int(rc.get("温度模式", 0) or 0), int(rc.get("压力模式", 0) or 0)
        ax2 = ax.twinx()
        parsed = False
        row = lut.get(lab)
        if kind == "tp":
            y_t = np.asarray(sub[v5.T_COL], float)
            ok = np.isfinite(y_t) & (y_t > 0)
            cand = []
            if ok.any():
                ax.plot(el[ok], y_t[ok], color=C["T"], lw=1.1)
                cand += list(y_t[ok]); parsed = True
            pid = np.asarray(sub[v5.PID_COL], float)
            okp = np.isfinite(pid) & (pid > 0)
            if okp.any():
                ax.plot(el[okp], pid[okp], color=C["PID"], lw=0.8, ls="-.", alpha=0.85)
                cand += list(pid[okp])
            if tm in (2, 3) and rc.get("目标温度"):
                ax.axhline(rc["目标温度"], color=C["TSP"], ls="--", lw=1.1)
                cand.append(rc["目标温度"])
            for sg in (fit.get((tag, lab, "T")) or []):
                ln = fit_line(sg, el)
                if ln is not None:
                    ax.plot(ln[0], ln[1], color=C["FIT"], lw=1.3, ls="--", alpha=0.8)
                    cand += list(ln[1])
            if cand:
                lo, hi = min(cand), max(cand)
                pad = max(5.0, 0.06 * (hi - lo + 1e-9))
                ax.set_ylim(lo - pad, hi + pad)
            ax.set_ylabel("℃", fontsize=7, color=C["T"])
            ax.tick_params(axis="y", colors=C["T"], labelsize=6)
            pw = np.asarray(sub[v5.P_COL], float)
            if np.isfinite(pw).any():
                ax2.plot(el, pw, color=C["P"], lw=0.9, alpha=0.9)
            pv = list(pw[np.isfinite(pw)])
            if rc.get("功率上限"):
                ax2.axhline(rc["功率上限"], color=C["PMAX"], ls="--", lw=1.0); pv.append(rc["功率上限"])
            if rc.get("功率下限"):
                ax2.axhline(rc["功率下限"], color=C["PMIN"], ls=":", lw=1.0); pv.append(rc["功率下限"])
            if pv:
                lo2, hi2 = min(pv), max(pv)
                pd2 = max(3.0, 0.08 * (hi2 - lo2 + 1e-9))
                ax2.set_ylim(lo2 - pd2, hi2 + pd2)
            ax2.set_ylabel(P_UNIT, fontsize=7, color=C["P"])
            ax2.tick_params(axis="y", colors=C["P"], labelsize=6)
        else:
            vv = np.asarray(sub[v5.V_COL], float)
            ok = np.isfinite(vv) & (vv > 0)
            cand = []
            if ok.any():
                ax.plot(el[ok], vv[ok], color=C["V"], lw=1.1); cand += list(vv[ok]); parsed = True
            if pm == 2 and rc.get("目标压力"):
                ax.axhline(rc["目标压力"], color=C["PSP"], ls="--", lw=1.1); cand.append(rc["目标压力"])
            for sg in (fit.get((tag, lab, "V")) or []):
                ln = fit_line(sg, el)
                if ln is not None and np.all(ln[1] > 0):
                    ax.plot(ln[0], ln[1], color=C["FIT"], lw=1.3, ls="--", alpha=0.8); cand += list(ln[1])
            if cand:
                if max(cand) / max(min(cand), 1e-9) > 50:
                    ax.set_yscale("log")
                else:
                    lo, hi = min(cand), max(cand)
                    pd = max(0.02 * (hi - lo + 1e-9), 0.5)
                    ax.set_ylim(lo - pd, hi + pd)
            ax.set_ylabel("Pa", fontsize=7, color=C["V"])
            ax.tick_params(axis="y", colors=C["V"], labelsize=6)
            mvd = np.asarray(sub[v5.MV_COL], float)
            if np.isfinite(mvd).any():
                ax2.plot(el, mvd, color=C["MV"], lw=0.9, alpha=0.9)
            if rc.get("蝶阀开度上限") is not None:
                ax2.axhline(rc["蝶阀开度上限"], color=C["VMAX"], ls="--", lw=1.0)
            if rc.get("蝶阀开度下限"):
                ax2.axhline(rc["蝶阀开度下限"], color=C["VMIN"], ls=":", lw=1.0)
            if rc.get("蝶阀开度上限") and rc.get("蝶阀开度下限"):
                ax2.axhspan(rc["蝶阀开度下限"], rc["蝶阀开度上限"], color=C["BAND"], alpha=0.25, zorder=0)
            vals = [float(np.nanmin(mvd)), float(np.nanmax(mvd))]
            for z in (rc.get("蝶阀开度下限"), rc.get("蝶阀开度上限")):
                if z is not None:
                    vals.append(float(z))
            lo2, hi2 = min(vals + [0.0]), max(vals + [10.0])
            pd2 = max(3.0, 0.08 * (hi2 - lo2 + 1e-9))
            ax2.set_ylim(max(-6, lo2 - pd2), min(106, hi2 + pd2))
            ax2.set_ylabel("%", fontsize=7, color=C["MV"])
            ax2.tick_params(axis="y", colors=C["MV"], labelsize=6)
        tag_mode = v5.MODE_T.get(tm, "")[:5] if kind == "tp" else v5.MODE_P.get(pm, "")[:5]
        ax.set_title(f"步{lab} [{tag_mode}] T*:{rc.get('目标温度', 0):.0f}℃ P*:{rc.get('目标压力', 0):.0f}Pa",
                     fontsize=7.5)
        # 速率拟合标注
        fkey = (tag, lab, "T" if kind == "tp" else "V")
        segs = fit.get(fkey)
        mseg = main_seg(segs)
        if mseg and np.isfinite(mseg["速率"]):
            u = "℃/min" if kind == "tp" else "Pa/min"
            txt = (f"拟合 {mseg['速率']:+.3f} {u}" if kind == "tp" else f"拟合 {mseg['速率']:+.0f} {u}")
            if np.isfinite(mseg["R2"]):
                txt += f" (R²={mseg['R2']:.3f})"
            ax.text(0.02, 0.04, txt, transform=ax.transAxes, fontsize=6.2, color="#a61e4d",
                    bbox=dict(fc="#fff0f6", ec="#fcc2d7", lw=0.6, alpha=0.9, pad=1.2))
        if row is not None:
            ok_t = row.get("温度_达标") if kind == "tp" else row.get("压力_达标")
            if isinstance(ok_t, str) and ok_t not in ("—", "不适用"):
                col = C["OK"] if ok_t == "达标" else (C["PART"] if "过渡" in ok_t else C["BAD"])
                ax.text(0.98, 0.95, ok_t, transform=ax.transAxes, fontsize=6.2, color="white",
                        ha="right", va="top", bbox=dict(fc=col, ec="none", alpha=0.9, pad=1.2))
            if row.get("对齐异常") == "是":
                ax.text(0.98, 0.80, "对齐异常", transform=ax.transAxes, fontsize=6.2, color="#e8590c",
                        ha="right", va="top", bbox=dict(fc="#fff4e6", ec="#ffd8a8", lw=0.6, alpha=0.92, pad=1.2))
        ax.grid(alpha=0.2)
        if not parsed:
            ax.text(0.5, 0.5, "无有效数据", ha="center", va="center", fontsize=8, transform=ax.transAxes)
        if el.size and el[-1] > 60:
            ax.set_xlabel("min", fontsize=6)
    for j in range(n, len(axes)):
        axes[j].axis("off")
    if kind == "tp":
        proxies = [
            plt.Line2D([], [], color=C["T"], lw=1.6, label="主控温度 (左轴 ℃)"),
            plt.Line2D([], [], color=v5.COL["t1"], lw=1.3, ls="--", label="T1温度 (左轴 ℃)"),
            plt.Line2D([], [], color=v5.COL["t2"], lw=1.3, ls=":", label="T2温度 (左轴 ℃)"),
            plt.Line2D([], [], color=C["PID"], lw=1.3, ls="-.", label="控制器 PID 设定 (左轴 ℃)"),
            plt.Line2D([], [], color=C["TSP"], lw=1.3, ls="--", label="recipe 目标温度 (左轴 ℃)"),
            plt.Line2D([], [], color=C["P"], lw=1.6, label=f"电源总功率反馈 (右轴 {P_UNIT})"),
            plt.Line2D([], [], color=C["PMAX"], lw=1.3, ls="--", label=f"recipe 功率上限 (右轴 {P_UNIT})"),
            plt.Line2D([], [], color=C["PMIN"], lw=1.3, ls=":", label=f"recipe 功率下限 (右轴 {P_UNIT})"),
            plt.Line2D([], [], color=C["FIT"], lw=1.5, ls="--", label="升降温速率拟合线"),
        ]
    else:
        proxies = [
            plt.Line2D([], [], color=C["V"], lw=1.6, label="炉内真空 (左轴 Pa)"),
            plt.Line2D([], [], color=C["PSP"], lw=1.3, ls="--", label="recipe 目标压力 (左轴 Pa)"),
            plt.Line2D([], [], color=C["MV"], lw=1.6, label="数控蝶阀开度反馈 (右轴 %)"),
            plt.Line2D([], [], color=C["VMAX"], lw=1.3, ls="--", label="recipe 开度上限 (右轴 %)"),
            plt.Line2D([], [], color=C["VMIN"], lw=1.3, ls=":", label="recipe 开度下限 (右轴 %)"),
            Patch(facecolor=C["BAND"], alpha=0.35, label="开度允许区间"),
        ]
    fig.legend(handles=proxies, loc="lower center", ncol=len(proxies),
               fontsize=(7.8 if len(proxies) > 7 else 8.5), frameon=False,
               bbox_to_anchor=(0.5, 0.0))
    fig.suptitle(title, fontsize=14)
    fig.tight_layout(rect=[0, 0.025, 1, 0.972])
    v5._save(fig, os.path.join(FIG, fname), dpi=130)
    plt.close(fig)
    return fname


def plot_hold_dual(tag, label, recipe, proc, key, fname, title):
    """恒温步25：上=温度+功率，下=真空+蝶阀（kW + 新配色）"""
    sub, el = v5.series_of(proc, *key)
    rc = recipe.get(key[0], {})
    h = el / 60.0
    fig, axs = plt.subplots(2, 1, figsize=(15.5, 8.4))
    fig.patch.set_facecolor("white")
    a = axs[0]; a2 = a.twinx()
    # 需求7：主控 + T1 + T2 同图
    v5.plot_three_temps(a, sub, h, lw_main=1.0, lw_sub=0.8,
                        col_main=C["T"], col_t1=v5.COL["t1"], col_t2=v5.COL["t2"])
    pid = np.asarray(sub[v5.PID_COL], float); okp = np.isfinite(pid) & (pid > 0)
    if okp.any():
        a.plot(h[okp], pid[okp], color=C["PID"], lw=0.9, ls="-.", label="控制器 PID 设定 (℃)")
    if rc.get("目标温度"):
        a.axhline(rc["目标温度"], color=C["TSP"], ls="--", lw=1.3, label="recipe 目标温度 (℃)")
    a.set_ylabel("温度 (℃)", color=C["T"]); a.legend(loc="upper right", fontsize=8); a.grid(alpha=0.25)
    a2.plot(h, sub[v5.P_COL], color=C["P"], lw=0.9, alpha=0.9, label=f"电源总功率反馈 ({P_UNIT})")
    if rc.get("功率上限"):
        a2.axhline(rc["功率上限"], color=C["PMAX"], ls="--", lw=1.1, label=f"recipe 功率上限 ({P_UNIT})")
    if rc.get("功率下限"):
        a2.axhline(rc["功率下限"], color=C["PMIN"], ls=":", lw=1.1, label=f"recipe 功率下限 ({P_UNIT})")
    a2.set_ylabel(f"功率 ({P_UNIT})", color=C["P"]); a2.legend(loc="lower right", fontsize=8)
    a2.tick_params(axis="y", colors=C["P"])
    a.set_title(f"{title} —— 温度(左轴) vs 功率(右轴)")
    b = axs[1]; b2 = b.twinx()
    vv = np.asarray(sub[v5.V_COL], float); okv = np.isfinite(vv) & (vv > 0)
    b.plot(h[okv], vv[okv], color=C["V"], lw=1.0, label="炉内真空 (Pa)")
    if rc.get("压力模式") == 2 and rc.get("目标压力"):
        b.axhline(rc["目标压力"], color=C["PSP"], ls="--", lw=1.3, label="recipe 目标压力 (Pa)")
    b.set_ylabel("炉内真空 (Pa)", color=C["V"]); b.set_xlabel("时间 (h)"); b.legend(loc="upper right", fontsize=8); b.grid(alpha=0.25)
    b2.plot(h, sub[v5.MV_COL], color=C["MV"], lw=0.8, alpha=0.85, label="数控蝶阀开度反馈 (%)")
    if rc.get("蝶阀开度上限") is not None:
        b2.axhline(rc["蝶阀开度上限"], color=C["VMAX"], ls="--", lw=1.1, label="recipe 开度上限 (%)")
    if rc.get("蝶阀开度下限"):
        b2.axhline(rc["蝶阀开度下限"], color=C["VMIN"], ls=":", lw=1.1, label="recipe 开度下限 (%)")
    b2.set_ylabel("蝶阀开度 (%)", color=C["MV"]); b2.legend(loc="lower right", fontsize=8)
    b2.tick_params(axis="y", colors=C["MV"])
    b.set_title("真空度(左轴) vs 蝶阀开度(右轴)")
    fig.tight_layout()
    v5._save(fig, os.path.join(FIG, fname), dpi=130)
    plt.close(fig)
    return fname


def plot_rate_summary(tag, label, stats, fname, title):
    """升降温速率拟合总览（需求6-3）：每步推荐速率条形 + 类型 + R²"""
    d = stats[stats["记录数"] >= 8].copy().reset_index(drop=True)
    d = d[d["温度_推荐速率"].notna()].reset_index(drop=True)
    labs = [f"{r['步号']}" for _, r in d.iterrows()]
    rate = d["温度_推荐速率"].astype(float)
    typ = d["温度_实测判型"].astype(str)
    # 类型配色：温度域用「恒温」；同时登记压力·真空域「恒压」同族配色，避免跨域复用时漏色
    colmap = {"单一线性": "#4dabf7", "多段线性式": "#748ffc",
              "前期线性+后期恒温": "#ffd43b", "前期线性+后期恒压": "#ffd43b",
              "全程恒温/停滞": "#69db7c", "全程恒压/停滞": "#69db7c",
              "波浪式": "#f783ac", "短步(数据不足)": "#ced4da"}
    cols = [colmap.get(next((k for k in colmap if k in t), "其他"), "#adb5bd") for t in typ]
    fig, ax = plt.subplots(figsize=OV_FIG)
    fig.patch.set_facecolor("white")
    x = np.arange(len(d))
    ax.bar(x, rate, color=cols, width=0.72, edgecolor="white", lw=0.6)
    ax.axhline(0, color="#495057", lw=0.9)
    for i, (v, t) in enumerate(zip(rate, typ)):
        ax.text(i, v + (0.5 if v >= 0 else -0.5), f"{v:+.2f}", ha="center",
                va="bottom" if v >= 0 else "top", fontsize=6.4, rotation=90)
    ax.set_xticks(x); ax.set_xticklabels(labs, rotation=90, fontsize=7)
    ax.set_ylabel("推荐升降温速率 (℃/min)　正=升温 / 负=降温")
    ax.set_title(title, fontsize=13)
    ax.grid(alpha=0.25, axis="y")
    hs = [Patch(facecolor=c, label=k) for k, c in colmap.items()]
    fig.legend(handles=hs, fontsize=8.5, ncol=6, loc="lower center", frameon=False,
               bbox_to_anchor=(0.5, 0.012))
    fig.tight_layout(rect=[0, 0.135, 1, 0.975])
    v5._save(fig, os.path.join(FIG, fname), dpi=130)
    plt.close(fig)
    return fname


# ================================================================ 单步结论摘要
def step_summary(row):
    """为一页 PPT 生成 2~4 句简要分析"""
    out = []
    tm = int(row.get("温度模式", 0) or 0)
    if tm == 1:
        pe = row.get("功率上限")
        p1 = row.get("功率_结束")
        dev = (p1 - pe) if (pe == pe and p1 == p1) else np.nan
        out.append(f"温度侧：功率模式，功率 {row.get('功率_起始', 0):.0f}→{row.get('功率_结束', 0):.0f} {P_UNIT}"
                   f"（recipe 上限 {pe:.0f}，末值差 {dev:+.1f}），"
                   f"温度被动由 {row.get('温度_起始', 0):.0f}℃ 变到 {row.get('温度_结束', 0):.0f}℃。"
                   + ("功率末值已贴合上限，符合『功率线性升至上限』的设定。"
                      if dev == dev and abs(dev) <= 5 else "功率末值偏离上限，需核查。"))
    elif tm == 2:
        d = row.get("温度_偏差")
        out.append(f"温度侧：温度模式，目标保持 {row.get('目标温度_recipe', 0):.0f}℃，"
                   f"后段实测 {row.get('温度_均值', 0):.0f}℃"
                   + (f"（维持偏差 {d:+.0f}℃，判定 {row.get('温度_达标')}）。" if d == d else "。"))
    elif tm == 3:
        c = row.get("温度_完成度")
        out.append(f"温度侧：线性模式，目标 {row.get('目标温度_recipe', 0):.0f}℃，"
                   f"实测 {row.get('温度_起始', 0):.0f}→{row.get('温度_结束', 0):.0f}℃"
                   + (f"（完成度 {c:.0f}%，判定 {row.get('温度_达标')}）。" if c == c else "。"))
    else:
        out.append("温度侧：无 recipe 模式定义。")
    r = row.get("温度_推荐速率")
    if r is not None and r == r:
        out.append(f"拟合：推荐速率 {float(r):+.3f} ℃/min（{row.get('温度_实测判型')}）。")
    pm = int(row.get("压力模式", 0) or 0)
    if pm == 1:
        fr = row.get("蝶阀跟随率")
        out.append(f"压力侧：开度模式，蝶阀开度上限 {row.get('蝶阀上限', 0):.0f}% 直接控制，"
                   f"实测蝶阀均值 {row.get('蝶阀_均值', 0):.2f}%"
                   + (f"，跟随率 {float(fr):.0f}%，判定 {row.get('压力_达标')}。" if fr == fr else "。"))
    elif pm == 2:
        d = row.get("压力_偏差pct")
        out.append(f"压力侧：压力模式，目标 {row.get('目标压力_recipe', 0):.0f} Pa，"
                   f"步末 {row.get('真空_结束', 0):.0f} Pa"
                   + (f"（偏差 {float(d):+.2f}%），判定 {row.get('压力_达标')}。" if d == d else "。"))
    vr = row.get("真空_推荐速率")
    if vr is not None and vr == vr:
        out.append(f"真空拟合：{float(vr):+.1f} Pa/min（{row.get('真空_实测判型')}）。")
    al = row.get("对齐检测")
    if isinstance(al, str) and row.get("对齐异常") == "是":
        out.append(f"⚠ 对齐异常：{al}")
    return out


# ================================================================ 主流程
def main():
    fitseg = load_fit_segments()
    fitr2 = load_fit_r2()
    # 把推荐速率的 R² 塞回 fitseg 供绘图标注（key: ('__r2__',tag,lab,kind) → 标量 R²）
    for (tg, lb, kd), d in fitr2.items():
        fitseg[("__r2__", tg, lb, kd)] = d.get("R2", np.nan)

    all_stats, notes_out, img_index, step_rows = [], [], [], []
    run_info = {}          # tag -> dict(label, log_last, end_mark)（说明页按实际数据生成）
    diags = {}             # tag -> 需求7 源系统偏差诊断
    for tag, label, rfile, d3, d4 in RUNS:
        recipe = v5.load_recipe(rfile)
        hold = v5.phases.hold_step(recipe)       # 涂层中 = 本批 recipe 时长最长的一步
        v5.phases.set_active(hold, tag)
        proc, dropped = v5.load_log(tag, hold=hold)
        meas = v5.load_measured(tag, d3, d4)
        st = v5.analyze_run(tag, label, recipe, proc, meas)
        all_stats.append(st)
        from temp_fix import diag as _tdiag
        diags[tag] = _tdiag(proc)
        _endm = [s for s, rc in recipe.items() if rc.get("结束位", 0) == 1]
        _log_last = max(int(str(x).split("(")[0]) for x in st["步号"])
        _log_steps = {int(x) for x in proc["step"].unique()}
        # 缺失步（中间断档）：recipe 有正时长但 log 在执行区间内无记录
        _missing = sorted(s for s, rc in recipe.items()
                          if rc.get("时长_min", 0) > 0 and 1 <= s < _log_last and s not in _log_steps)
        run_info[tag] = dict(
            label=label,
            log_last=_log_last,
            end_mark=max(_endm) if _endm else max(recipe.keys()),
            missing=_missing)
        lut = {str(r["步号"]): r for _, r in st.iterrows()}

        keys = sorted(proc.groupby(["step", "pass"], sort=False).groups.keys(), key=lambda k: (k[0], k[1]))
        heat = [k for k in keys if k[0] < hold]
        cool = [k for k in keys if k[0] > hold]
        holdk = [k for k in keys if k[0] == hold]

        def _span(ks):
            """按实际存在的步生成标题区间（不写死，避免 recipe 更正后标题与实际步号脱节）。"""
            ns = sorted({int(k[0]) for k in ks})
            if not ns:
                return "无步"
            return f"步{ns[0]}" if ns[0] == ns[-1] else f"步{ns[0]}–{ns[-1]}"

        sp_h, sp_c = _span(heat), _span(cool)

        # ---- 需求6-1/2/3：更新版总览与分段图
        plot_overview_dual(tag, label, recipe, proc, "tp", f"{tag}_温度功率_全程双轴_v6.png",
                           f"工艺全程 温度(左轴,℃) vs 功率(右轴,kW) 双纵坐标  {label}")
        plot_overview_dual(tag, label, recipe, proc, "vm", f"{tag}_真空蝶阀_全程双轴_v6.png",
                           f"工艺全程 炉内真空(左轴,Pa 对数) vs 蝶阀开度(右轴,%) 双纵坐标  {label}")
        plot_step_grid_dual(tag, label, recipe, proc, heat, "tp", f"{tag}_温度功率_涂层前分步_v6.png",
                            f"涂层前({sp_h}) 温度 vs 功率(kW) 分步双轴 + 速率拟合  {label}",
                            st=st, fit=fitseg)
        plot_step_grid_dual(tag, label, recipe, proc, cool, "tp", f"{tag}_温度功率_涂层后分步_v6.png",
                            f"涂层后({sp_c}) 温度 vs 功率(kW) 分步双轴 + 速率拟合  {label}",
                            st=st, fit=fitseg)
        plot_step_grid_dual(tag, label, recipe, proc, heat, "vm", f"{tag}_真空蝶阀_涂层前分步_v6.png",
                            f"涂层前({sp_h}) 真空度 vs 蝶阀开度 分步双轴 + 速率拟合  {label}",
                            st=st, fit=fitseg)
        plot_step_grid_dual(tag, label, recipe, proc, cool, "vm", f"{tag}_真空蝶阀_涂层后分步_v6.png",
                            f"涂层后({sp_c}) 真空度 vs 蝶阀开度 分步双轴 + 速率拟合  {label}",
                            st=st, fit=fitseg)
        if holdk:
            sp_o = _span(holdk)
            plot_hold_dual(tag, label, recipe, proc, holdk[0], f"{tag}_涂层中步{hold}_双轴_v6.png",
                           f"涂层中({sp_o})  {label}")
        plot_rate_summary(tag, label, st, f"{tag}_升降温速率拟合总览.png",
                          f"各步升降温速率拟合（{label}）按曲线类型选用方法　正=升温 / 负=降温")

        # ---- 需求6-4：每步高清图
        for (s, p) in keys:
            lab = v5.step_label(s, p)
            sub, el = v5.series_of(proc, s, p)
            row = lut.get(lab, {})
            tag_row = row.to_dict() if hasattr(row, "to_dict") else {}
            enough = (len(sub) >= 2) and (
                (np.isfinite(np.asarray(sub[v5.T_COL], float)) & (np.asarray(sub[v5.T_COL], float) > 0)).sum() >= 2
                or np.isfinite(np.asarray(sub[v5.MV_COL], float)).sum() >= 2
            )
            fn_tp = os.path.join(HD, tag, f"步{lab.replace('(','_').replace(')','')}_温度功率.png")
            fn_vm = os.path.join(HD, tag, f"步{lab.replace('(','_').replace(')','')}_压力开度.png")
            os.makedirs(os.path.join(HD, tag), exist_ok=True)
            ok_tp = ok_vm = False
            if enough:
                ok_tp = make_step_fig(tag, label, recipe, proc, (s, p), "tp", fn_tp,
                                      tag_row, fitseg, fitseg, dpi=175)
                ok_vm = make_step_fig(tag, label, recipe, proc, (s, p), "vm", fn_vm,
                                      tag_row, fitseg, fitseg, dpi=175)
            for knd, fn, okc in [("温度功率", fn_tp, ok_tp), ("压力开度", fn_vm, ok_vm)]:
                img_index.append(dict(
                    炉次=tag, 工况=label, 步号=lab, 阶段=v5.phase_of(s), 图片类型=knd,
                    记录数=len(sub), 时长_min=round(float(el[-1] - el[0]), 1) if len(el) > 1 else 0.0,
                    已生成="是" if okc else "否（采样点不足）",
                    文件=os.path.relpath(fn, OUT).replace("\\", "/"),
                ))
            step_rows.append(dict(
                炉次=tag, 工况=label, 阶段=v5.phase_of(s), 步号=lab, 记录数=len(sub),
                时长_min=round(float(el[-1] - el[0]), 1) if len(el) > 1 else 0.0,
                recipe时长_min=row.get("recipe时长_min"),
                温度模式=row.get("温度模式"), 温度模式含义=row.get("温度模式含义"),
                目标温度=row.get("目标温度_recipe"),
                功率下限=row.get("功率下限"), 功率上限=row.get("功率上限"),
                压力模式=row.get("压力模式"), 压力模式含义=row.get("压力模式含义"),
                目标压力=row.get("目标压力_recipe"),
                蝶阀下限=row.get("蝶阀下限"), 蝶阀上限=row.get("蝶阀上限"),
                温度_起始=row.get("温度_起始"), 温度_结束=row.get("温度_结束"),
                功率_结束=row.get("功率_结束"),
                真空_结束=row.get("真空_结束"), 蝶阀_均值=row.get("蝶阀_均值"),
                温度_实测判型=row.get("温度_实测判型"), 温度_推荐速率=row.get("温度_推荐速率"),
                温度_达标=row.get("温度_达标"),
                真空_实测判型=row.get("真空_实测判型"), 真空_推荐速率=row.get("真空_推荐速率"),
                压力_达标=row.get("压力_达标"),
                对齐检测=row.get("对齐检测"),
                结论摘要=(" ｜ ".join(step_summary(tag_row)) if tag_row else "无记录")
                         + (f" ｜ 人为操作——{ops_text_of_step(tag, s)}"
                            if ops_text_of_step(tag, s) else ""),
            ))
        notes_out.append(f"{tag}({label})：log {len(proc)} 行，{len(keys)} 个步次，"
                         f"高清图 {sum(1 for r in img_index if r['炉次'] == tag and r['已生成'] == '是')} 张")

    stats = pd.concat(all_stats, ignore_index=True)
    img = pd.DataFrame(img_index)
    stepdf = pd.DataFrame(step_rows)

    note = pd.DataFrame([
        ["需求6 目标", "① 功率单位改为 kW；② 优化系列配色区分度；③ 补充升降温速率拟合；"
                   "④ 每步单独生成高清图；⑤ 每 recipe 一份分步汇报PPT（另见 make_ppt_v6.py）"],
        ["温度口径（需求7+方案C）", f"log 温度『主控温度』按《需求7》结合各批次 T1/T2 通道一致性自适应确定："
                       f"重叠测温窗（T1<{v5.T_SWITCH:.0f}℃ 且 T2 有效）中位偏差 <{v5.T_CONSIST:.0f}℃ 判为同一测点双量程，"
                       f"按 T1/T2 拼接，否则保持源系统主控口径；源系统原值保留在『主控温度_原始』列"
                       f"（源自 temp_fix.log_temp）。" +
                       "　".join(f"{t}：{t_diag_text(diags[t])}" for t in sorted(diags)) +
                       "。本版全部图表、速率与高清图均使用统一口径温度"],
        ["三温度同图（需求7）", "《需求7》要求三个温度都在图中：逐步高清图与全程总览同时绘制 主控温度（粗实线，"
                        "配色 C['T']）、T1温度（青虚线）、T2温度（紫点线）。T1/T2 为 log 原始双测温，"
                        "叠画用于核对修正结果与通道差异；T2 低温段读数为 0（高位热电偶未起测），绘图中按 0 值屏蔽。"],
        ["① 功率单位", f"v5 曾把右轴误标为 %。核对：log『电源总功率反馈』量程 0~302.88，"
                   f"recipe『功率上限』峰值 303.0（更正版『- 实际』recipe），二者逐位吻合，"
                   f"且温度模式①下功率末值精确等于 recipe 功率上限 "
                   f"（run1 步1–7：26.82/53.76/67.25/79.96/99.98/121.10/121.20 对上 26.92/53.84/67.30/80/100/121.14/121.14）"
                   f"→ 量纲为 kW，本版仅改标签不做换算"],
        ["② 配色优化", "六类曲线各自独立配色，不再复用：主控温度=深蓝 #1864ab；控制器 PID 设定=紫 #7048e8；"
                   "recipe 目标温度/目标压力=青绿 #0ca678；电源总功率/蝶阀开度实测=红 #e03131；"
                   "recipe 上限=橙 #f76707；recipe 下限=棕 #846358；速率拟合线=品红 #d6336c；"
                   "允许区间=浅橙底 #ffd8a8。实测线与同色参考线的『同色混淆』问题已消除"],
        ["③ 速率拟合", "拟合线直接复用需求3(温度)/需求4(真空度) 的『分段最小二乘』结果（单一事实来源，不重算）："
                   "在总览/分段/分步图上叠加品红虚线拟合段，并标注推荐速率与 R²；"
                   "另出『各步升降温速率拟合总览』条形图（按曲线类型着色）"],
        ["④ 高清分步图", "每步两张，175 dpi（2205×1125 px，比例与PPT槽位一致→零裁剪），可直接放入PPT；"
                    "温度-功率图含实测温度/PID设定/recipe目标温度 + 实测功率/recipe功率上下限 + 拟合线；"
                    "压力-开度图含实测真空/recipe目标压力 + 实测蝶阀/recipe开度上下限"],
        ["数据来源", "实测判型、推荐速率、分段拟合明细直接读取需求3/需求4 产物；本脚本只做配色/单位/叠加/高清输出"],
        ["工况说明", v5.workcond_text(run_info)],
    ] + ([["人为操作记录（现场确认，非系统异常）",
           "　".join(f"{t}({lb})：{v5.ops_text(t)}" for t, lb, *_ in v5.RUNS if v5.ops_text(t))]]
         if any(v5.ops_text(t) for t, *_ in v5.RUNS) else []) + [["数据规模", n] for n in notes_out], columns=["项目", "说明"])

    xls = os.path.join(OUT, "需求6_分步高清图与汇报PPT.xlsx")
    with pd.ExcelWriter(xls, engine="openpyxl") as w:
        note.to_excel(w, sheet_name="说明", index=False)
        stepdf.to_excel(w, sheet_name="分步速率与结论", index=False)
        img.to_excel(w, sheet_name="图片索引", index=False)
        stats.to_excel(w, sheet_name="全程分步统计", index=False)
        for sn, ws in w.sheets.items():
            for i in range(1, ws.max_column + 1):
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 14
            ws.freeze_panes = "A2"
    print("Excel ->", xls)
    print("高清图:", int((img["已生成"] == "是").sum()), "/", len(img))
    print("步次:", len(stepdf))
    # 供 make_ppt_v6 复用
    stepdf.to_csv(os.path.join(OUT, "_step_table.csv"), index=False, encoding="utf-8-sig")
    return stats, stepdf, img


if __name__ == "__main__":
    main()
