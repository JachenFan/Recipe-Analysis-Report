# -*- coding: utf-8 -*-
"""项目路径的**唯一事实来源**。

以前每个脚本各自写 `BASE = r"D:\\个人文件\\报告\\工艺分析"` 再按 `BASE/需求N_...` 拼目录，
一搬目录就要改二十多处、漏一处就静默写到旧位置。现在统一从这里取。

目录约定（2026-10-23 起）
------------------------
    <ROOT>/
    ├── 输入/
    │   ├── recipe/   *-recipe.xlsx     工艺配方（步号/时长/温度/功率/真空/蝶阀/流量）
    │   └── log/      *-log.xlsx        设备数据报表（154 列原始记录）
    ├── 输出/                            分析产物，按需求编号分子目录
    │   ├── 需求2_工艺全程分析/ … 需求10_交互式HTML报告/
    │   └── 25C261007_打火分析/
    ├── 降温分析/                        代码 + 抽取缓存（_runN_all.csv / _cache/）
    └── README.md / 项目文件分类.md / 分析需求.md

ROOT 可用环境变量 `CVD_ROOT` 覆盖（换机器 / 换盘不改代码）。

兼容性
------
`out()` 会先找 `输出/<需求N>`，找不到而旧位置 `<ROOT>/<需求N>` 存在时回落到旧路径——
这样「代码先改、目录后搬」或「只搬了一半」都不会写错地方。
"""
from __future__ import annotations

import os

ROOT = os.environ.get("CVD_ROOT") or r"D:\个人文件\报告\工艺分析"

# ---- 输入 ----
IN_ROOT = os.path.join(ROOT, "输入")
RECIPE_DIR = os.path.join(IN_ROOT, "recipe")
LOG_DIR = os.path.join(IN_ROOT, "log")

# ---- 输出 ----
OUT_ROOT = os.path.join(ROOT, "输出")

# ---- 代码与运行缓存（本文件所在目录 = 降温分析/）----
CODE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = CODE_DIR                       # _runN_all.csv、_cache/ 都在这里
DIAG_DIR = os.path.join(CODE_DIR, "诊断")

# 常用目录的短别名
REQ2 = "需求2_工艺全程分析"
REQ3 = "需求3_工艺全程分析"
REQ4 = "需求4_真空度蝶阀分析"
REQ5 = "需求5_双轴对比分析"
REQ6 = "需求6_分步高清图与汇报PPT"
REQ9 = "需求9_横向对比"
REQ10 = "需求10_交互式HTML报告"
ARC = "25C261007_打火分析"


def out(name: str, *parts: str) -> str:
    """输出目录下的路径：`out("需求6_分步高清图与汇报PPT", "图表")`。

    name 也接受短别名常量（REQ3…）；若 `输出/name` 不存在但旧位置 `<ROOT>/name` 存在，
    回落旧位置（迁移期兼容）。
    """
    p = os.path.join(OUT_ROOT, name)
    if not os.path.isdir(p):
        legacy = os.path.join(ROOT, name)
        if os.path.isdir(legacy):
            p = legacy
    return os.path.join(p, *parts) if parts else p


def code(*parts: str) -> str:
    """代码/缓存目录（降温分析/）下的路径。"""
    return os.path.join(CODE_DIR, *parts) if parts else CODE_DIR


def _first_exist(cands: list[str]) -> str:
    for c in cands:
        if c and os.path.exists(c):
            return c
    return cands[0] if cands else ""


def recipe(name: str) -> str:
    """recipe 文件：绝对路径原样返回；否则依次找 输入/recipe/、ROOT、当前目录。"""
    if not name:
        return ""
    if os.path.isabs(name):
        return name
    return _first_exist([os.path.join(RECIPE_DIR, name),
                         os.path.join(ROOT, name),
                         os.path.abspath(name)])


def log(name: str) -> str:
    """log 文件：绝对路径原样返回；否则依次找 输入/log/、ROOT、当前目录。"""
    if not name:
        return ""
    if os.path.isabs(name):
        return name
    return _first_exist([os.path.join(LOG_DIR, name),
                         os.path.join(ROOT, name),
                         os.path.abspath(name)])


def any_in(name: str) -> str:
    """不确定是 recipe 还是 log 时用：recipe → log → 根目录。"""
    if not name:
        return ""
    if os.path.isabs(name):
        return name
    return _first_exist([os.path.join(RECIPE_DIR, name),
                         os.path.join(LOG_DIR, name),
                         os.path.join(ROOT, name),
                         os.path.abspath(name)])


def resolve(path: str) -> str:
    """用户从命令行/弹窗给的路径：绝对路径或当前目录存在就原样；否则按文件名去 输入/ 下找。"""
    if not path:
        return ""
    if os.path.isabs(path) or os.path.exists(path):
        return os.path.abspath(path)
    return any_in(os.path.basename(path))


def check(verbose: bool = True) -> dict[str, bool]:
    """自检：输出目录与关键输入是否齐全。返回 {路径: 是否存在}。"""
    items = {IN_ROOT: True, RECIPE_DIR: True, LOG_DIR: True, OUT_ROOT: True}
    for n in (REQ2, REQ3, REQ4, REQ5, REQ6, REQ9, REQ10, ARC):
        items[out(n)] = True
    res = {k: os.path.exists(k) for k in items}
    if verbose:
        print(f"ROOT = {ROOT}")
        for k, v in res.items():
            print(f"  {'OK ' if v else 'MISS'}  {k}")
        reqs = sorted(os.listdir(RECIPE_DIR)) if os.path.isdir(RECIPE_DIR) else []
        logs = sorted(os.listdir(LOG_DIR)) if os.path.isdir(LOG_DIR) else []
        print(f"  recipe {len(reqs)} 份 / log {len(logs)} 份")
    return res


if __name__ == "__main__":
    import sys
    ok = check(verbose=True)
    sys.exit(0 if all(ok.values()) else 1)
