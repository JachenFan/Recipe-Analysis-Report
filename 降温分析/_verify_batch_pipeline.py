# -*- coding: utf-8 -*-
"""6 批次交叉验证：`--recipe/--log` 现算 与 既有需求3/4/6 产物 是否逐字段一致。

这是封装的验收脚本——两条路径的数据源不同（一个现算、一个读历史 xlsx），
但口径必须完全相同；任一步的速率/判型/拟合段数/KPI/要点不一致都算失败。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import analyze_v5 as v5              # noqa: E402
import batch_pipeline as bp          # noqa: E402
import make_html_report as mhr       # noqa: E402
import project_paths as pp           # noqa: E402

LEG = os.path.join(HERE, "_tmp_htmlcheck", "legacy")
NEW = os.path.join(HERE, "_tmp_htmlcheck", "new")


def load(p):
    s = open(p, encoding="utf-8").read()
    i = s.index("const DATA = ") + len("const DATA = ")
    return json.JSONDecoder().raw_decode(s, i)[0]


def diff(A, B):
    """返回 (差异步列表, KPI差异数, 要点差异数)"""
    ia = {s["id"]: s for s in A["steps"]}
    ib = {s["id"]: s for s in B["steps"]}
    bad = []
    for k in sorted(set(ia) & set(ib)):
        x, y = ia[k], ib[k]
        d = []
        for f in ("rate", "vrate", "T0", "T1e", "ve", "mvmean"):
            a = None if x[f] is None else round(float(x[f]), 3)
            b = None if y[f] is None else round(float(y[f]), 3)
            if a != b:
                d.append(f"{f} {a}≠{b}")
        for f in ("ftype", "vtype", "tmode", "pmode"):
            if (x[f] or "") != (y[f] or ""):
                d.append(f"{f} {x[f]}≠{y[f]}")
        for f, k2 in (("tp", "fit"), ("vm", "fit")):
            if len(x[f][k2]) != len(y[f][k2]):
                d.append(f"{f}.fit {len(x[f][k2])}≠{len(y[f][k2])}")
        if d:
            bad.append((k, d))
    extra = sorted(set(ia) ^ set(ib))
    nk = sum(1 for a, b in zip(A["ov"]["kpi"], B["ov"]["kpi"]) if a != b)
    nt = sum(1 for a, b in zip(A["ov"]["txt"], B["ov"]["txt"]) if a != b)
    return bad, nk, nt, extra


def main():
    rows = []
    for tag, label, recipe, *_ in v5.RUNS:
        lp = pp.log(f"{label}-log.xlsx")
        rp = pp.recipe(recipe)
        print(f"\n=== {tag} / {label} ===", flush=True)
        a = mhr._legacy_build(tag, outdir=LEG)
        b = bp.generate(rp, lp, name=label, note=mhr.RUNS[tag][1], outdir=NEW, verbose=True)
        A, B = load(a), load(b)
        bad, nk, nt, extra = diff(A, B)
        rows.append((label, len(A["steps"]), len(bad), nk, nt, extra))
        print(f"  -> 步数 {len(A['steps'])}，差异步 {len(bad)}，KPI差异 {nk}，要点差异 {nt}"
              + (f"，步号集合差异 {extra}" if extra else ""))
        for k, d in bad[:6]:
            print(f"     步{k}: " + "；".join(d))

    print("\n批次      步数  差异步  KPI  要点  步号差异")
    ok = True
    for label, n, nb, nk, nt, ex in rows:
        flag = "OK" if (nb == 0 and nk == 0 and nt == 0 and not ex) else "FAIL"
        ok &= flag == "OK"
        print(f"{label:<9} {n:>4}  {nb:>5}  {nk:>3}  {nt:>3}  {ex or '-'}  {flag}")
    print("\n结论：", "两条路径完全一致 ✔" if ok else "存在差异，需排查 ✘")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
