# -*- coding: utf-8 -*-
"""单批次流水线：只给「recipe + log 两个 Excel」，就能算出 HTML 报告所需的全部数据。

为什么要单独封一层
------------------
需求3/4/5/6/10 的分析链原本是**按 6 个固定批次（run1–run6）**跑的，中间产物
（`_runX_all.csv`、需求3/4 的 xlsx、需求6 的 `_step_table.csv`）都是**跨批次合并**的。
换一个新批次时若直接重跑那套脚本，会整表重写、把历史批次的结果一起改掉。

本模块把这些口径**按批次**重算一遍：只读用户选的源文件、只写自己的抽取缓存，
产出与需求3/4/6 表结构完全相同的 DataFrame，再交给 `make_html_report.build_report()` 出图。
因此：

  1. **单一事实来源不破** —— 判型与分段拟合仍调用
     `analyze_process_v3.analyze()` / `analyze_vacuum_v4.analyze()`，
     步汇总仍调用 `analyze_v5.analyze_run()`，结论摘要仍调用 `analyze_v6.step_summary()`，
     本模块**不复制任何算法**，只是把它们的单批次入口串起来；
  2. **不碰既有产物** —— 不改 `_runX_all.csv`，不改需求3/4/6 的 xlsx；
  3. **口径一致** —— 温度仍走需求7/方案C 自适应（`temp_fix.apply_to_frame`，幂等），
     主工艺段切分与 pass 标注仍走 `analyze_v5.prepare_log()`。

对外 API
--------
    load_recipe_any(path, sheet=None)        -> (recipe_dict, info)
    ingest_log(path, cache=True)             -> (proc, info)
    analyze_batch(proc, recipe, tag, label)  -> (step_df, segT_df, segV_df)
    generate(recipe, log, name, note, outdir) -> html_path      # 端到端

产物结构对齐
------------
    step_df   : 同 需求6 `_step_table.csv`（步号/阶段/温度模式/温度_推荐速率/结论摘要/…）
    segT_df   : 同 需求3『分段速率明细』（起_min/止_min/起温/段时长/段速率）
    segV_df   : 同 需求4『分段速率明细』（起_min/止_min/起值/段时长/段速率，含突跳/平台行）
"""
from __future__ import annotations

import hashlib
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import analyze_v5 as v5                      # noqa: E402
import analyze_process_v3 as v3              # noqa: E402
import analyze_vacuum_v4 as v4               # noqa: E402
import analyze_v6 as v6                      # noqa: E402
import batch_notes                           # noqa: E402
import temp_fix                              # noqa: E402

CACHE_DIR = os.path.join(_HERE, "_cache")

# log 里本报告实际用到的列（154 列里只取这些，读盘量降到 1/10）
LOG_COLS = [
    "时间", "当前运行步号",
    "T1温度", "T2温度", "目标温度", "PID目标温度", "主控温度",
    "电源总功率反馈",
    "目标压力", "炉内真空", "高真空反馈", "低真空反馈", "数控蝶阀开度反馈",
]
_REQUIRED = ["时间", "当前运行步号", "T1温度", "T2温度", "主控温度",
             "电源总功率反馈", "炉内真空", "数控蝶阀开度反馈"]


# ------------------------------------------------------------------ 读盘
def _engine():
    try:
        import python_calamine  # noqa: F401
        return "calamine"           # Rust 实现，比 openpyxl 快约 8 倍
    except ImportError:
        return None


def _sheet_with(path, want0="Sheet1"):
    """log 的 sheet 名一般是 Sheet1；否则取第一个。"""
    xl = pd.ExcelFile(path, engine=_engine())
    return want0 if want0 in xl.sheet_names else xl.sheet_names[0]


def _cache_key(path) -> str:
    st = os.stat(path)
    raw = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def read_log(path, cache=True, cache_dir=CACHE_DIR, verbose=True) -> pd.DataFrame:
    """抽 log 所需列；按「路径+大小+修改时间」做缓存，重跑秒级。

    缓存只存抽取后的 13 列（十几 MB），源文件被替换（大小/mtime 变）会自动失效，
    不需要手工 FORCE。
    """
    path = os.path.abspath(path)
    key = _cache_key(path)
    cp = os.path.join(cache_dir, f"log_{key}.csv")
    if cache and os.path.exists(cp):
        if verbose:
            print(f"  · log 命中缓存 {os.path.basename(cp)}")
        return pd.read_csv(cp, dtype={"当前运行步号": "float64"})

    sheet = _sheet_with(path)
    head = pd.read_excel(path, sheet_name=sheet, nrows=0, engine=_engine())
    cols = [c for c in LOG_COLS if c in set(head.columns)]
    miss = [c for c in _REQUIRED if c not in set(head.columns)]
    if miss:
        raise SystemExit(f"log 缺少必需列：{miss}\n实际表头：{list(head.columns)[:40]}")
    if verbose:
        print(f"  · 读取 log {os.path.basename(path)}（sheet={sheet}，{len(cols)}/{len(head.columns)} 列）…")
    df = pd.read_excel(path, sheet_name=sheet, usecols=cols, engine=_engine())
    if cache:
        os.makedirs(cache_dir, exist_ok=True)
        try:
            df.to_csv(cp, index=False, encoding="utf-8-sig")
        except OSError:
            pass                     # 缓存写失败不影响主流程
    return df


def load_recipe_any(path, sheet=None, verbose=True):
    """读 recipe；返回 (recipe_dict, info)。

    recipe 文件名与 sheet 名经常对不上（29C261016.xlsx → sheet 29C260815），
    因此按「第一个含『步号』列的工作表」定位，并支持 --sheet 手工指定。
    """
    path = os.path.abspath(path)
    if sheet is None:
        xl = pd.ExcelFile(path, engine=_engine())
        sheet = xl.sheet_names[0]
        for s in xl.sheet_names:
            try:
                if "步号" in pd.read_excel(path, sheet_name=s, nrows=0,
                                           engine=_engine()).columns:
                    sheet = s
                    break
            except Exception:
                continue
    rec = v5.load_recipe(path, sheet=sheet)
    if not rec:
        raise SystemExit(f"recipe 未解析出任何步：{path}（sheet={sheet}）")
    info = dict(path=path, sheet=sheet, n_steps=len(rec),
                last_step=max(rec), end_mark=max([s for s, r in rec.items()
                                                  if r.get("结束位", 0) == 1] or [max(rec)]))
    if verbose:
        print(f"  · recipe {os.path.basename(path)}（sheet={sheet}，{len(rec)} 步，"
              f"结束标记=步{info['end_mark']}）")
    return rec, info


# ------------------------------------------------------------------ 抽取
def ingest_log(path, cache=True, verbose=True):
    """log Excel -> 主工艺段 proc（已含需求7 温度口径与 pass 标注）。"""
    df = read_log(path, cache=cache, verbose=verbose)
    if verbose:
        print(f"  · 原始 {len(df)} 行，切分主工艺段 + 温度口径修正…")
    proc, dropped = v5.prepare_log(df, key="__batch__")
    info = dict(path=os.path.abspath(path), rows_raw=int(len(df)),
                rows_proc=int(len(proc)), dropped=int(dropped),
                t0=proc["时间"].iloc[0], t1=proc["时间"].iloc[-1],
                n_steps=int(proc["step"].nunique()),
                temp=v5.TEMP_INFO.pop("__batch__", {}))
    if verbose:
        tm = info["temp"]
        mode = ("需求7 拼接（T1/T2 同测点双量程）" if tm.get("mode") == "rule7"
                else f"保持源主控（方案C，通道中位偏差 {tm.get('t12_median', float('nan')):.0f}℃）")
        print(f"  · 主工艺段 {len(proc)} 行 / {info['n_steps']} 个步次，"
              f"{info['t0']:%Y-%m-%d %H:%M} → {info['t1']:%Y-%m-%d %H:%M}"
              + (f"，剔除尾部残段 {dropped} 行" if dropped else ""))
        print(f"  · 温度口径：{mode}")
    if 25 not in set(proc["step"]):
        print("  ! 未找到恒温步25——主工艺段切分与阶段归属依赖该步，请核对 log 是否完整")
    return proc, info


# ------------------------------------------------------------------ 单步统计
def _seg_rows_T(tag, ph, lab, r):
    out = []
    for k, sg in enumerate(r.get("segs") or [], 1):
        out.append(dict(炉次=tag, 阶段=ph, 步号=lab, 曲线类型=r.get("type"), 分段=k,
                        起_min=round(sg["t0"], 2), 止_min=round(sg["t1"], 2),
                        起温=round(sg["icpt"], 2), 段时长=round(sg["t1"] - sg["t0"], 2),
                        段温变=round(sg["dT"], 2), 段速率=round(sg["slope"], 4),
                        段R2=round(sg["r2"], 4) if sg["r2"] == sg["r2"] else None,
                        点数=sg["n"], 方向="下降" if sg["slope"] < 0 else "上升"))
    return out


def _seg_rows_V(tag, ph, lab, q):
    """与 analyze_vacuum_v4.main 完全一致：拟合段 + 突跳段 + 平台段。"""
    out = []
    for k, sg in enumerate(q.get("segs") or [], 1):
        out.append(dict(炉次=tag, 阶段=ph, 步号=lab, 曲线类型=q.get("type"), 分段=k,
                        起_min=round(sg["t0"], 2), 止_min=round(sg["t1"], 2),
                        起值=round(sg["icpt"], 1), 段时长=round(sg["t1"] - sg["t0"], 2),
                        段变化=round(sg["dT"], 1), 段速率=round(sg["slope"], 3),
                        段R2=round(sg["r2"], 4) if sg["r2"] == sg["r2"] else None,
                        点数=sg["n"],
                        方向="下降(真空变好)" if sg["slope"] < 0 else "上升(真空变差)"))
    for k, j in enumerate(q.get("_jumps") or [], 1):
        out.append(dict(炉次=tag, 阶段=ph, 步号=lab, 曲线类型=q.get("type"), 分段=f"突{k}",
                        起_min=round(j["t0"], 2), 止_min=round(j["t1"], 2),
                        起值=round(j["icpt"], 1), 段时长=round(j["t1"] - j["t0"], 3),
                        段变化=round(j["dT"], 1), 段速率=round(j["slope"], 1),
                        段R2=None, 点数=2, 方向=j.get("kind")))
    plist = q.get("_plat") or []
    for k, pl in enumerate(plist, 1):
        out.append(dict(炉次=tag, 阶段=ph, 步号=lab, 曲线类型=q.get("type"),
                        分段=("平台" if len(plist) == 1 else f"平台{k}"),
                        起_min=round(pl["t0"], 2), 止_min=round(pl["t1"], 2),
                        起值=round(pl["icpt"], 1), 段时长=round(pl["t1"] - pl["t0"], 2),
                        段变化=round(pl["dT"], 1), 段速率=round(pl["slope"], 3),
                        段R2=round(pl["r2"], 4) if pl["r2"] == pl["r2"] else None,
                        点数=pl["n"], 方向=v4._P["platform"]))
    return out


def _tag_for_notes(tag):
    """人为操作记录按 run1–run6 登记；新批次若直接用炉次号，这里反查回 runX。"""
    if tag in batch_notes.MANUAL_OPS:
        return tag
    for t, label, *_ in v5.RUNS:
        if label == tag:
            return t
    return tag


def analyze_batch(proc: pd.DataFrame, recipe: dict, tag: str, label: str = "",
                  verbose: bool = True):
    """按批次算：判型/推荐速率 -> 步汇总表 + 温度/真空分段明细。

    与需求3→4→6 的链路同序同参：
      · 温度：v3.analyze(el, 主控温度, T1, T2, 目标温度, dt, 阶段)
      · 真空：v4.analyze(el, 炉内真空, 高真空, 低真空, 目标压力, dt, 阶段, S)
    判型/速率回填进 `meas` 后交给 v5.analyze_run()，得到与需求6『分步速率与结论』
    同字段的步表。
    """
    S = v4.global_scale(proc)
    meas, segT, segV = {}, [], []
    for (s, p), sub in proc.groupby(["step", "pass"], sort=False):
        lab = v5.step_label(s, p)
        el = ((sub["时间"] - sub["时间"].iloc[0]).dt.total_seconds() / 60).to_numpy()
        dt = float(np.median(np.diff(el))) if len(el) > 2 else 1.0
        ph = v5.phase_of(s)

        # ---- 温度（需求3 口径）
        temp = sub[v5.T_COL].astype(float).to_numpy()
        t1 = sub["T1温度"].astype(float).to_numpy()
        t2 = sub["T2温度"].astype(float).to_numpy()
        tsp = float(sub["目标温度"].iloc[0])
        r = v3.analyze(el, temp, t1, t2, tsp, dt, ph)
        segT += _seg_rows_T(tag, ph, lab, r)

        # ---- 真空（需求4 口径）
        vv = sub[v4.PV].astype(float).to_numpy()
        hi = sub["高真空反馈"].astype(float).to_numpy()
        lo = sub["低真空反馈"].astype(float).to_numpy()
        sp = float(sub[v4.SP].iloc[0]) if v4.SP in sub.columns else 0.0
        q = v4.analyze(el, vv, hi, lo, sp, dt, ph, S)
        segV += _seg_rows_V(tag, ph, lab, q)

        meas[lab] = {
            "T_判型": r.get("type"),
            "T_速率": round(float(r["rec"]), 4) if r.get("rec") == r.get("rec") else None,
            "V_判型": q.get("type"),
            "V_速率": round(float(q["rec"]), 2) if q.get("rec") == q.get("rec") else None,
        }

    st = v5.analyze_run(tag, label, recipe, proc, meas)
    step_df = _step_table(proc, st, tag, label)
    if verbose:
        print(f"  · {len(step_df)} 个步次；温度拟合段 {len(segT)} 条，真空拟合段 {len(segV)} 条")
    return step_df, pd.DataFrame(segT), pd.DataFrame(segV)


def _step_table(proc: pd.DataFrame, st: pd.DataFrame, tag: str, label: str) -> pd.DataFrame:
    """把 v5 的步汇总 + 实测判型 拼成需求6『分步速率与结论』的形状（含结论摘要）。"""
    lut = {str(r["步号"]): r for _, r in st.iterrows()}
    ops_tag = _tag_for_notes(tag)
    rows = []
    for (s, p), sub in proc.groupby(["step", "pass"], sort=False):
        lab = v5.step_label(s, p)
        el = ((sub["时间"] - sub["时间"].iloc[0]).dt.total_seconds() / 60).to_numpy()
        row = lut.get(lab)
        if row is None:
            continue
        row = row.to_dict()
        ops = batch_notes.ops_text_of_step(ops_tag, s)
        rows.append(dict(
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
            结论摘要=" ｜ ".join(v6.step_summary(row))
                     + (f" ｜ 人为操作——{ops}" if ops else ""),
        ))
    d = pd.DataFrame(rows)
    if d.empty:
        raise SystemExit(f"未能生成任何步次（tag={tag}）")
    d["步号"] = d["步号"].astype(str)
    d["_n"] = d["步号"].str.split("(").str[0].astype(int)
    d["_p"] = d["步号"].str.extract(r"\((\d+)\)").fillna(1).astype(int)
    return d.sort_values(["_n", "_p"]).reset_index(drop=True)


# ------------------------------------------------------------------ 端到端
def generate(recipe_path, log_path, name=None, note="", outdir=None,
             sheet=None, cache=True, open_file=False, verbose=True):
    """选 recipe + log → 出单文件 HTML。返回 HTML 绝对路径。"""
    import make_html_report as mhr

    if verbose:
        print(f"[1/4] recipe：{recipe_path}")
    recipe, rinfo = load_recipe_any(recipe_path, sheet=sheet, verbose=verbose)

    if verbose:
        print(f"[2/4] log：{log_path}")
    proc, linfo = ingest_log(log_path, cache=cache, verbose=verbose)

    tag = name or _guess_name(recipe_path, log_path, rinfo)
    if verbose:
        print(f"[3/4] 单步统计：炉次 {tag}" + (f"（{note}）" if note else ""))
    st, segT, segV = analyze_batch(proc, recipe, tag, label=note, verbose=verbose)

    if verbose:
        print("[4/4] 生成 HTML…")
    out = mhr.build_report(st, proc, segT, segV, batch=tag, note=note, tag=tag,
                           outdir=outdir, n_steps_recipe=rinfo["n_steps"])
    if open_file:
        try:
            os.startfile(out)        # noqa: S606  Windows 双击同效
        except Exception:
            pass
    return out


def _guess_name(recipe_path, log_path, rinfo) -> str:
    """批次名：优先从文件名里取 29C2610xx 这类炉次号，其次用 recipe 文件名去掉后缀。"""
    import re
    pat = re.compile(r"(\d{2}C\d{6})")
    for p in (recipe_path, log_path):
        m = pat.search(os.path.basename(str(p)))
        if m:
            return m.group(1)
    stem = os.path.splitext(os.path.basename(str(recipe_path)))[0]
    return re.sub(r"[-_ ]*(recipe|配方|实际)$", "", stem) or "批次"


if __name__ == "__main__":
    print(__doc__)
