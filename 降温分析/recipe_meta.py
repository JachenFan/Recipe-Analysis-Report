# -*- coding: utf-8 -*-
"""recipe 侧的步次元信息（单一事实来源）：缺失步检测。

某些批次 recipe 里有计划、log 里却完全没有记录的步（如 run6=29C261016 的步8，90 min 功率模式）。
这类步既不会出现在统计表，也不会出现在 PPT 逐页里 —— 属"静默缺失"，必须在说明页显式声明，
否则读者会以为该步不存在（违反 R4：不允许示例型信息静默失效/缺失）。
"""
import pandas as pd


def steps_with_duration(path, sheet):
    """返回 {步号: 计划时长(min)}，只含时长 > 0 的步（排除占位步与结束标记步）。"""
    d = pd.read_excel(path, sheet_name=sheet, usecols=["步号", "时", "分", "秒"])
    dur = (pd.to_numeric(d["时"], errors="coerce").fillna(0) * 60
           + pd.to_numeric(d["分"], errors="coerce").fillna(0)
           + pd.to_numeric(d["秒"], errors="coerce").fillna(0) / 60.0)
    return {int(s): float(m) for s, m in zip(d["步号"], dur) if float(m) > 0}


def missing_steps(path, sheet, log_steps, upto):
    """执行区间 (0, upto) 内、recipe 有时长但 log 无记录的步号列表（升序）。"""
    plan = steps_with_duration(path, sheet)
    have = {int(x) for x in log_steps}
    return sorted(s for s in plan if 1 <= s < int(upto) and s not in have)


def missing_text(pairs):
    """pairs: [(tag, label, [缺失步...])] → 一行说明（全部缺失则为空串）。"""
    seg = [f"{t}({lb}) 缺步{'/'.join(str(x) for x in ms)}" for t, lb, ms in pairs if ms]
    if not seg:
        return ""
    return ("；".join(seg) +
            "。recipe 对这些步有计划时长，但 log 无任何记录（非结束标记、非占位步），"
            "已按缺失处理：不参与速率统计、无逐页高清图，判读时勿当作『该步未运行』")
