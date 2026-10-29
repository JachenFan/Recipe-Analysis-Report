# -*- coding: utf-8 -*-
"""
回归守卫：压力·真空域不得出现温度话术「恒温」。

背景（教训）：分段模型在 温度 与 压力/真空 两域**完全一致**，但平台的中文话术不同——
温度域叫「恒温」，压力·真空域叫「恒压」。曾出现压力侧沿用「恒温」的错误
（PPT 里「真空拟合：−16.1 Pa/min（前期线性+后期恒温）」），与事实不符。

本脚本做三类检查（任一处失败 → 退出码 1）：
  A. 源码口径：需求3(温度) 必须用「恒温」；需求4(压力/真空) 必须用「恒压」。
  B. 产物口径：若已生成需求4/5/6 的 xlsx，其**压力相关列/文段**不得含「恒温」。
  C. 阶段口径（R13）：阶段必须以「recipe 时长最长的一步」为「涂层中」，
     源码里不得再出现写死「步≤24 升温 / 步25 恒温 / 步26+ 降温」的判定；
     若已生成需求10 的 HTML，其 hold 必须等于该批 recipe 的最长步。

用法：  python check_terms.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_paths as pp

BASE = pp.ROOT
problems = []


def _chk(cond, msg):
    if not cond:
        problems.append(msg)
    print(("  [OK]  " if cond else "  [FAIL] ") + msg)


# ------------------------------------------------------------------ A. 源码口径
def check_source():
    print("A. 源码口径")
    import matplotlib
    matplotlib.use("Agg")
    import analyze_process_v3 as v3
    import analyze_vacuum_v4 as v4

    for name in ("T_LINHOLD", "T_FLAT"):
        t = getattr(v3, name)
        _chk("恒温" in t and "恒压" not in t,
             f"需求3(温度) {name} 应用「恒温」话术：当前 {t!r}")
    for name in ("T_LINHOLD", "T_FLAT"):
        p = getattr(v4, name)
        _chk("恒压" in p and "恒温" not in p,
             f"需求4(压力/真空) {name} 应用「恒压」话术：当前 {p!r}")

    # v5 压力域期望类型
    import analyze_v5 as v5
    _chk("恒压" in v5.EXP_V[2] and "恒温" not in v5.EXP_V[2],
         f"需求5 EXP_V[2](压力模式期望) 应用「恒压」话术：当前 {v5.EXP_V[2]!r}")
    _chk("恒温" in v5.EXP_T[2] and "恒压" not in v5.EXP_T[2],
         f"需求5 EXP_T[2](温度模式期望) 应用「恒温」话术：当前 {v5.EXP_T[2]!r}")
    _chk(v5.fam("前期线性+后期恒压") == "hold" and v5.fam("全程恒压/停滞") == "hold",
         "需求5 fam() 应把「恒压」/「全程恒压」识别为 hold 族")


# ------------------------------------------------------------------ C. 阶段口径
# 写死口径的历史形态（只要再出现就是回归）：步号与阶段名的直接绑定
_HARDCODED = [
    r'"升温"\s*if\s*\w+\s*<=\s*24',
    r'"恒温"\s*if\s*\w+\s*==\s*25',
    r'1\s*<=\s*k\[0\]\s*<=\s*24',
    r'k\[0\]\s*>=\s*26',
    r'k\[0\]\s*==\s*25',
    r'if\s+step\s*==\s*25',
    r'if\s+s\s*==\s*25',
]
_SKIP_FILES = {"phases.py", "check_terms.py"}      # phases.py 的文档里刻意引用了旧写法
_SKIP_DIRS = ("__pycache__",)


def check_phase_rule():
    import re
    import phases

    print("C. 阶段口径（R13：涂层中 = [氢A1 开始进气, recipe 时长最长的一步]）")
    # C1 分隔节点取值（回归基线）：末步 = 最长步；起步 = 氢A1 开始进气的那一步
    _chk(phases.hold_of("29C260916") == 25, "29C260916 的「涂层中」末步应为步25")
    _chk(phases.hold_of("25C261007") == 32,
         "25C261007 的「涂层中」末步应为步32（步25 仅 20 min，真正的涂层段在步32）")
    _chk(phases.span_of("29C260916") == (21, 25),
         f"29C260916 的「涂层中」应为步21–25（步21 起氢A1 进气），当前 {phases.span_of('29C260916')}")
    _chk(phases.span_of("25C261007") == (28, 32),
         f"25C261007 的「涂层中」应为步28–32（步28 起氢A1 进气），当前 {phases.span_of('25C261007')}")
    _chk([phases.phase_of(s, 32, 28) for s in (27, 28, 32, 33)] ==
         [phases.PRE, phases.HOLD, phases.HOLD, phases.POST],
         "phase_of 的区间切分应随「进气起步 / 最长步」移动")
    # 只传末步时退化为单步（老调用兼容，不会把区间悄悄放大）
    _chk(phases.phase_of(31, 32) == phases.PRE,
         "phase_of(s, hold) 不传 start 时应退化为单步口径")

    # C2 源码不得再写死
    here = os.path.dirname(os.path.abspath(__file__))
    hits = []
    for fn in sorted(os.listdir(here)):
        if not fn.endswith(".py") or fn in _SKIP_FILES:
            continue
        txt = open(os.path.join(here, fn), encoding="utf-8", errors="ignore").read()
        for pat in _HARDCODED:
            for m in re.finditer(pat, txt):
                hits.append(f"{fn}: {txt[max(0, m.start() - 24):m.end() + 12].strip()}")
    _chk(not hits, "源码中不应再有写死步号的阶段判定"
                   + (f"（命中 {len(hits)} 处：{hits[:3]}）" if hits else ""))

    # C3 需求10 产物：hold 与 recipe 一致，且三阶段覆盖全部步次
    d10 = pp.out("需求10_交互式HTML报告")
    if not os.path.isdir(d10):
        print("  [skip] 未找到 需求10 产物目录")
        return
    import json
    for fn in sorted(os.listdir(d10)):
        if not fn.endswith(".html"):
            continue
        batch = fn.replace("_工艺分步分析.html", "")
        try:
            txt = open(os.path.join(d10, fn), encoding="utf-8").read()
            i = txt.index("const DATA = ") + len("const DATA = ")
            d, _ = json.JSONDecoder().raw_decode(txt, i)
        except Exception as e:
            problems.append(f"{fn} 解析失败: {e}")
            print(f"  [FAIL] {fn} 解析失败: {e}")
            continue
        exp = phases.hold_of(batch)
        exp0, _ = phases.span_of(batch)
        n = len(d["steps"])
        cnt = {p: sum(1 for x in d["steps"] if x["phase"] == p) for p in phases.BASE}
        _chk(d.get("hold") == exp, f"{fn}：hold={d.get('hold')}，应为 recipe 最长步 {exp}")
        _chk(d.get("span") == [exp0, exp],
             f"{fn}：span={d.get('span')}，应为 [进气起步, 最长步] = {[exp0, exp]}")
        _chk(cnt[phases.HOLD] ==
             sum(1 for x in d["steps"] if exp0 <= int(str(x["id"]).split("(")[0]) <= exp),
             f"{fn}：涂层中 {cnt[phases.HOLD]} 步，应等于步{exp0}–{exp} 的步次数")
        _chk(sum(cnt.values()) == n, f"{fn}：三阶段合计应等于步次数（{sum(cnt.values())} / {n}）")
        # 顶部参数表：进气参数清单必须落到产物（硅A2 无用，不得出现）
        _chk(d.get("gas") == ["氢A1", "氢B", "氢F", "氮C", "氮G"],
             f"{fn}：进气参数清单应为 氢A1/氢B/氢F/氮C/氮G，当前 {d.get('gas')}")


# ------------------------------------------------------------------ B. 产物口径
def check_outputs():
    print("B. 产物口径（若产物存在；未重跑时会按旧数据报 FAIL，属预期）")
    import pandas as pd

    def scan(path, sheet, col, what):
        if not os.path.exists(path):
            print(f"  [skip] 未找到 {what}: {os.path.basename(path)}")
            return
        try:
            df = pd.read_excel(path, sheet_name=sheet)
        except Exception as e:
            problems.append(f"{what} 读取失败: {e}")
            print(f"  [FAIL] {what} 读取失败: {e}")
            return
        if col not in df.columns:
            print(f"  [skip] {what} 无列 {col}")
            return
        bad = df[df[col].astype(str).str.contains("恒温", na=False)]
        _chk(len(bad) == 0, f"{what}『{col}』不应含「恒温」（命中 {len(bad)} 行）")

    scan(pp.out("需求4_真空度蝶阀分析", "需求4_真空度蝶阀分析.xlsx"),
         "全程分步统计", "曲线类型", "需求4 压力/真空类型")
    scan(pp.out("需求5_双轴对比分析", "需求5_双轴对比分析.xlsx"),
         "全程分步统计", "真空_实测判型", "需求5 真空实测判型")
    scan(pp.out("需求6_分步高清图与汇报PPT", "需求6_分步高清图与汇报PPT.xlsx"),
         "全程分步统计", "真空_实测判型", "需求6 真空实测判型")

    # 需求6 结论摘要里的『真空拟合』文段
    p6 = pp.out("需求6_分步高清图与汇报PPT", "需求6_分步高清图与汇报PPT.xlsx")
    if os.path.exists(p6):
        try:
            df = pd.read_excel(p6, sheet_name="分步速率与结论")
            hit = 0
            for s in df.get("结论摘要", pd.Series(dtype=str)).astype(str):
                if "真空拟合" in s and "恒温" in s.split("真空拟合", 1)[1]:
                    hit += 1
            _chk(hit == 0, f"需求6『结论摘要·真空拟合』文段不应含「恒温」（命中 {hit} 行）")
        except Exception as e:
            problems.append(f"需求6 分步速率与结论 读取失败: {e}")
            print(f"  [FAIL] 需求6 分步速率与结论 读取失败: {e}")


if __name__ == "__main__":
    print("=" * 68)
    print("回归守卫：领域话术（恒温/恒压）+ 阶段口径（涂层前/中/后）")
    print("=" * 68)
    check_source()
    check_outputs()
    check_phase_rule()
    print("-" * 68)
    if problems:
        print(f"结论：发现 {len(problems)} 处问题 ↓")
        for p in problems:
            print("   -", p)
        sys.exit(1)
    print("结论：全部通过")
