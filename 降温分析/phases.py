# -*- coding: utf-8 -*-
"""阶段划分的单一事实来源：以 recipe 中「时长最长的步」为分隔节点。

为什么要有这个模块
------------------
原实现把阶段写死在步号上（`analyze_v5.phase_of`）：

    return "升温" if s <= 24 else ("恒温" if s == 25 else "降温")

这隐含了一个**只对 29C 机台成立**的假设——「前 24 步升温、步25 恒温、步26 起降温」。
实测反例：25C261007（recipe sheet 25C260728，78 步）最长的步是 **步32（6000 min）**，
步25 只有 20 min。套用写死口径的后果不是标签难看，而是**实质错判**：
真正的涂层段（步26–33）被整段标成"降温"，而"恒温"落在一个 20 min 的过渡步上；
主工艺段切分（`prepare_log` 找"含步25 的段"）也会切错。

正确口径（用户 2026-10-23 定基准，2026-10-27 修正为区间）
--------------------------------------------------------
    涂层中末点 = recipe 时长为全表最长的那一步      （占位步/0 min 步排除）
    涂层中起点 = 从该步**向前回溯**，氢A1 由 0 开始进气的那一步
                 （以最长步为基准向前延伸，其间步骤全部算「涂层中」）
    涂层中     = [起点, 末点] 闭区间
    涂层前     = 起点之前的所有步
    涂层后     = 末点之后的所有步

为什么起点要按氢A1 反推（用户 2026-10-27）：最长步只是"主涂层平台"，
真正的涂层过程从**进气爬坡**就开始了——29C 系步21–24 把氢A1 从 0 拉到 250–310 L/min、
硅A2 0→450，25C261007 步28–31 同样如此，进气到位后才进最长步。
若只把最长步算作涂层中，这些爬坡步会被归到「涂层前」，涂层分段的时长/温度口径整体偏窄。

实测取值（回归基线）：29C 六批 → 步21–25；25C261007 → 步28–32。

阶段标签再按**实测温度净变化**附方向后缀（升温/恒温/降温），方向由数据判定、不写死：

    涂层前·升温 / 涂层中·恒温 / 涂层后·降温

用法
----
    import phases

    h0, h1 = phases.span_of("29C260916")   # 读该批 recipe，结果缓存 -> (21, 25)
    phases.set_active(h1, "29C260916", h0) # 老代码的 phase_of(s) 走这个上下文
    phases.phase_of(25)                    # -> "涂层中"
    phases.label("涂层中", "恒温")          # -> "涂层中·恒温"

    # 已知 recipe dict 时最省事（不读盘）：
    h0, h1 = phases.hold_span(recipe)
"""
from __future__ import annotations

import os

PRE, HOLD, POST = "涂层前", "涂层中", "涂层后"
BASE = (PRE, HOLD, POST)                 # 时序顺序，也是展示顺序
SEP = "·"                                # 阶段名与方向后缀的分隔符

DIR_UP, DIR_FLAT, DIR_DOWN = "升温", "恒温", "降温"
DIR_FLAT_T = 10.0                        # ℃；阶段首末温差 ≤ 此值视作"恒温"（系统噪声 ±0.5℃）

GAS_START = "氢A1"                       # 「涂层中」起点判据通道（与 analyze_v5.R_GAS_START 一致）
GAS_EPS = 0.05                           # L/min；与断流阈值同量级，浮点噪声不算「已进气」

_ACTIVE = {"hold": None, "start": None, "tag": None}   # 当前批次上下文（供老签名的 phase_of(s) 使用）
_CACHE: dict[str, int] = {}              # tag -> 涂层中末步（最长步）
_CACHE_SPAN: dict[str, tuple[int, int]] = {}   # tag -> (起点, 末点)


# ------------------------------------------------------------------ 分隔节点
def hold_step(recipe: dict) -> int:
    """recipe dict -> 「涂层中」**末步** = 时长最长的一步。

    只考虑**有正时长**的步：占位步（0 min）与结束标记步再长也不能当分隔节点。
    时长并列时取**步号较小**者（工艺流程上更早出现的那一步是起点）。
    """
    cand = [(float(r.get("时长_min") or 0.0), int(s))
            for s, r in (recipe or {}).items() if float(r.get("时长_min") or 0.0) > 0]
    if not cand:
        # 极端情况：recipe 全是 0 min（未填时长）。退化为最小步号，避免抛异常打断流水线
        return min(recipe) if recipe else 1
    return max(cand, key=lambda t: (t[0], -t[1]))[1]


def _gas(row) -> float:
    """取该步的氢A1 进气量（L/min）；缺列/非数值一律按 0（= 未进气）。"""
    try:
        v = float((row or {}).get(GAS_START) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v else 0.0          # NaN -> 0


def hold_start(recipe: dict, end: int | None = None) -> int:
    """recipe dict -> 「涂层中」**起步** = 从末步向前回溯到氢A1 开始进气的那一步。

    判据：末步（含）往前连续满足 `氢A1 > 0` 的最左一步——即「进气没断过的最长尾段」的起点。
      · 中间若关过气（氢A1 回落 0 再开），起点取**最近一次开启**，不跨越断气段；
      · 末步本身氢A1 = 0（该步不进气 / 老 recipe 无气体列）→ 退化为末步单步，
        与 2026-10-23 的单步口径等价，不会把整段工艺误吞进「涂层中」。
    """
    rec = recipe or {}
    if not rec:
        return 0
    h = int(end if end is not None else hold_step(rec))
    start = h
    for s in sorted((int(x) for x in rec if int(x) <= h), reverse=True):
        if _gas(rec[s]) > GAS_EPS:
            start = s
        else:
            break
    return start


def hold_span(recipe: dict) -> tuple[int, int]:
    """recipe dict -> (涂层中起步, 末步)。不读盘，最省事。"""
    h1 = hold_step(recipe)
    return hold_start(recipe, h1), h1


def hold_of(tag: str, verbose: bool = False) -> int:
    """按批次号取「涂层中」**末步**（最长步）：读该批 recipe 现算，结果缓存。

    recipe 来源按 R6 的唯一登记处 `analyze_v5.RUNS`；未登记的批次（如 25C261007）
    按 `{批次}-recipe.xlsx` 命名规范在 `输入/recipe/` 下找。
    """
    return span_of(tag, verbose=verbose)[1]


def span_of(tag: str, verbose: bool = False) -> tuple[int, int]:
    """按批次号取 (涂层中起步, 末步)：读该批 recipe 现算，结果缓存。"""
    if tag in _CACHE_SPAN:
        return _CACHE_SPAN[tag]

    import analyze_v5 as v5          # 延迟导入：避免 phases <- v5 的循环导入
    import project_paths as pp

    path = None
    for t, label, rfile, *_ in v5.RUNS:
        if t == tag or label == tag:
            path = pp.recipe(rfile)
            break
    if path is None or not os.path.exists(path):
        guess = pp.recipe(f"{tag}-recipe.xlsx")
        path = guess if os.path.exists(guess) else None
    if path is None:
        raise FileNotFoundError(
            f"[phases] 找不到批次 {tag} 的 recipe，无法确定「涂层中」步号。\n"
            f"  已登记批次：{[l for _t, l, *_ in v5.RUNS]}；"
            f"其它批次请按 {{批次}}-recipe.xlsx 命名放入 输入/recipe/")

    rec = v5.load_recipe(path)
    h0, h1 = hold_span(rec)
    _CACHE[tag] = h1
    _CACHE_SPAN[tag] = (h0, h1)
    if verbose:
        dur = rec.get(h1, {}).get("时长_min", 0)
        gas = rec.get(h0, {}).get(GAS_START, 0)
        print(f"  · 阶段划分：{tag} 的「涂层中」= 步{h0}–{h1}"
              f"（步{h1} recipe 时长 {dur:.0f} min 为全表最长；"
              f"步{h0} 起 {GAS_START}={gas:g} L/min 进气）")
    return h0, h1


# ------------------------------------------------------------------ 阶段
def set_active(hold, tag: str | None = None, start=None) -> None:
    """设置当前批次的「涂层中」区间（供老签名 phase_of(s) 使用）。

    `start` 缺省 = `hold`（单步口径）——只传末步的老调用不会把区间放大。
    """
    _ACTIVE["hold"] = int(hold) if hold is not None else None
    _ACTIVE["start"] = (int(start) if start is not None
                        else (_ACTIVE["hold"] if hold is not None else None))
    _ACTIVE["tag"] = tag


def active() -> int | None:
    return _ACTIVE["hold"]


def active_start() -> int | None:
    return _ACTIVE["start"]


def active_tag() -> str | None:
    return _ACTIVE["tag"]


def phase_of(s, hold=None, start=None) -> str:
    """步号 -> 阶段基名（涂层前/涂层中/涂层后）。

    「涂层中」是**闭区间** [start, hold]；start 缺省取上下文，仍缺省则等于 hold（单步）。
    """
    h = hold if hold is not None else _ACTIVE["hold"]
    if h is None:
        raise RuntimeError(
            "[phases] 阶段划分未初始化：请先 phases.set_active(*phases.span_of(tag))，"
            "或显式传入 hold=/start=。写死步号（步≤24 升温/步25 恒温）的口径已废弃。")
    h = int(h)
    b = start if start is not None else _ACTIVE["start"]
    b = h if b is None else min(int(b), h)
    s = int(str(s).split("(")[0])
    return PRE if s < b else (HOLD if s <= h else POST)


def dir_of(t0, t1, flat: float = DIR_FLAT_T) -> str:
    """一段的首末温度 -> 方向词（升温/恒温/降温）。缺值时返回空串。"""
    try:
        d = float(t1) - float(t0)
    except (TypeError, ValueError):
        return ""
    if d != d:            # NaN
        return ""
    if abs(d) <= flat:
        return DIR_FLAT
    return DIR_UP if d > 0 else DIR_DOWN


def label(base: str, direction: str = "") -> str:
    """基名 + 方向 → 展示用全名（无方向时只给基名）。"""
    return f"{base}{SEP}{direction}" if direction else base


def dirs_from_steps(steps) -> dict:
    """按步表算各阶段方向：{涂层前: '升温', ...}。

    steps 需为**时间顺序**的 dict 序列，含 phase / T0 / T1e 三个键
    （`make_html_report.build_step` 产出的形状）。
    """
    out = {}
    for b in BASE:
        grp = [s for s in steps if s.get("phase") == b]
        if not grp:
            continue
        t0 = next((s["T0"] for s in grp if s.get("T0") is not None), None)
        t1 = next((s["T1e"] for s in reversed(grp) if s.get("T1e") is not None), None)
        out[b] = dir_of(t0, t1)
    return out


def labels_from_steps(steps) -> dict:
    """{涂层前: '涂层前·升温', ...}——展示层的唯一入口。"""
    d = dirs_from_steps(steps)
    return {b: label(b, d.get(b, "")) for b in BASE if b in d}


def span_text(steps) -> dict:
    """各阶段实际覆盖的步号区间：{涂层前: '步1–24', ...}（用于图标题/说明文案）。"""
    out = {}
    for b in BASE:
        ids = [int(str(s.get("id", "")).split("(")[0]) for s in steps
               if s.get("phase") == b and str(s.get("id", "")).split("(")[0].isdigit()]
        if not ids:
            continue
        lo, hi = min(ids), max(ids)
        out[b] = f"步{lo}" if lo == hi else f"步{lo}–{hi}"
    return out
