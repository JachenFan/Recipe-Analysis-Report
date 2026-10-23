# -*- coding: utf-8 -*-
"""CVD 工艺分析 HTML 报告生成器（选中 recipe + log 即出报告）

两种用法
--------
1) 双击 `make_report.bat`：依次弹出两个文件选择框（recipe、log），
   确认炉次号后自动生成报告并打开。

2) 命令行（可脚本化）：
       python make_report.py --recipe 29C261017-recipe.xlsx --log 29C261017-log.xlsx
       python make_report.py --recipe ... --log ... --name 29C261017 --note 中阻
       python make_report.py --recipe ... --log ... --out D:/临时 --no-open

判定/拟合/结论口径与需求3/4/6 完全一致（直接复用同一批算法函数），
所以同一份数据无论走「选文件」还是走历史批次，速率与判型逐位相同。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ANA = os.path.join(BASE, "降温分析")
if ANA not in sys.path:
    sys.path.insert(0, ANA)

import project_paths as pp            # noqa: E402  路径唯一事实来源

try:                                    # Windows 控制台默认 GBK，中文/℃ 会炸
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ------------------------------------------------------------------ 交互
def _ps(script: str, timeout: int = 600) -> str:
    """跑一段 PowerShell 取标准输出（仅用于系统对话框，不做任何文件操作）。"""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("  ! 系统对话框超时（未操作），改用默认值继续")
        return ""
    except Exception as e:
        print(f"  ! 无法调用系统对话框（{e}）")
        return ""
    if r.returncode != 0 and r.stderr:
        print("  ! 对话框出错：", r.stderr.strip().splitlines()[0])
    return (r.stdout or "").strip()


def pick_file(title: str, start: str = "") -> str:
    ps = f"""
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = '{title}'
$d.Filter = 'Excel 工作簿 (*.xlsx;*.xls)|*.xlsx;*.xls|所有文件 (*.*)|*.*'
$d.InitialDirectory = '{start or pp.IN_ROOT}'
if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{ $d.FileName }}
"""
    return _ps(ps) or ""


def input_box(prompt: str, default: str) -> str:
    ps = f"""
Add-Type -AssemblyName Microsoft.VisualBasic
[Microsoft.VisualBasic.Interaction]::InputBox('{prompt}', 'CVD 工艺分析 HTML 报告', '{default}')
"""
    return _ps(ps, timeout=300) or default


# ------------------------------------------------------------------ 主流程
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="选 recipe + log → 生成单文件交互式工艺分步分析 HTML")
    ap.add_argument("--recipe", help="recipe xlsx 路径")
    ap.add_argument("--log", help="log xlsx 路径")
    ap.add_argument("--name", help="炉次号（报告标题；默认从文件名提取）")
    ap.add_argument("--note", default="", help="工况备注（副标题），如 中阻 / 不同转速")
    ap.add_argument("--out", help="输出目录（默认 需求10_交互式HTML报告）")
    ap.add_argument("--sheet", help="recipe 工作表名（默认取第一个含『步号』的表）")
    ap.add_argument("--no-open", action="store_true", help="生成后不自动打开")
    ap.add_argument("--no-cache", action="store_true", help="不复用 log 抽取缓存")
    a = ap.parse_args(argv)

    recipe = a.recipe or ""
    log = a.log or ""
    if not recipe:
        print("请选择 recipe 文件…")
        recipe = pick_file("第 1 步：选择 recipe 文件（工艺配方 xlsx）", pp.RECIPE_DIR)
        if not recipe:
            print("已取消。")
            return 2
    if not log:
        print("请选择 log 文件…")
        log = pick_file("第 2 步：选择 log 文件（数据报表 xlsx）", pp.LOG_DIR)
        if not log:
            print("已取消。")
            return 2

    # 允许只给文件名：自动到 输入/recipe、输入/log 下找，省得每次敲全路径
    recipe, log = pp.resolve(recipe), pp.resolve(log)
    for p in (recipe, log):
        if not os.path.exists(p):
            print(f"文件不存在：{p}")
            print(f"  已在 {pp.RECIPE_DIR} 与 {pp.LOG_DIR} 下查找过")
            return 2

    import batch_pipeline as bp
    name = a.name
    if not name:
        guess = bp._guess_name(recipe, log, {})
        # 只有「双击 / 一个参数都不给」才弹输入框。
        # 命令行给了路径就静默取默认名——脚本化调用时 stdin 也可能是 tty，
        # 靠 isatty 判断会把批量任务卡在隐藏的模态框上（实测踩过）。
        if a.recipe or a.log or not sys.stdin.isatty():
            name = guess
        else:
            name = input_box("炉次号（报告标题用，可改）：", guess)

    try:
        out = bp.generate(recipe, log, name=(name or None), note=a.note, outdir=a.out,
                          sheet=a.sheet, cache=not a.no_cache,
                          open_file=not a.no_open)
    except SystemExit as e:
        print(e)
        return 1
    print("\n完成：", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
