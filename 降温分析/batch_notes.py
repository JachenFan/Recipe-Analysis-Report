# -*- coding: utf-8 -*-
"""
人为操作记录（单一事实来源）
========================================================
某些批次在指定步内存在**现场人为手动调整**（不是系统控制出错）。这类事实无法从
recipe/log 自动推出（log 只记录"目标值被改成多少"，不记录"是谁改的、为什么"），
必须由现场确认后登记在此，再由报告与 PPT 统一引用，避免被自动判型误报成"控制异常"。

登记格式
  tag -> [ dict(step=步号, stage="工艺阶段名", text="说明", basis="数值依据（可选）") ]

引用方
  - analyze_process_v3 / analyze_v5 / analyze_v6 的说明页「人为操作记录」行
  - make_ppt_v6 的逐步页：以【人为操作】追加到『简要分析』末尾（另见 _user_notes_<tag>.json
    的「【现场备注】」通道，二者互补：此处登记与步号绑定的既成事实，JSON 存现场追加批注）

2026-10-20 登记 run6（29C261016）：
  步25 涂层前期为人为手动操作——压力 42000 Pa、氢A1 240、氢B 250、氢F 40；
  运行约 35 min 后才调至 recipe 参数（32000 Pa / 氢A1 70 / 氢B 250 / 氢F 80）。
"""
MANUAL_OPS = {
    "run6": [
        dict(
            step=25,
            stage="涂层",
            text=("前期为现场手动设定：压力 42000 Pa、氢A1 240、氢B 250、氢F 40，"
                  "运行一段时间后才调至 recipe 参数（32000 Pa / 氢A1 70 / 氢B 250 / 氢F 80）；"
                  "属人为调整，不是系统控制出错"),
            basis=("log『目标压力』在步25 前 35 min 稳定显示 42000 Pa（346 行），"
                   "至 35.4 min 才切到 32000 Pa；炉内真空随之由 22656 Pa 爬升至 31949 Pa，"
                   "蝶阀开度 6.6% → 10.6%（压力模式②闭环跟随，无失控迹象）"),
        )
    ],
}


def ops_of(tag):
    """返回该批次登记的人为操作列表（无登记则为空表）。"""
    return MANUAL_OPS.get(tag, [])


def ops_text(tag):
    """说明页用：把该批次人为操作压成一行文本（无登记返回空串）。"""
    ops = ops_of(tag)
    if not ops:
        return ""
    seg = []
    for o in ops:
        s = f"步{o['step']}（{o['stage']}）：{o['text']}"
        if o.get("basis"):
            s += f"。实测依据：{o['basis']}"
        seg.append(s)
    return "；".join(seg) + "。"


def ops_text_of_step(tag, step):
    """PPT 逐步页用：返回该步的人为操作说明（无则空串）。"""
    for o in ops_of(tag):
        if int(o["step"]) == int(step):
            return f"{o['text']}。实测依据：{o['basis']}。" if o.get("basis") else o["text"] + "。"
    return ""
