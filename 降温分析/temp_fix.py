# -*- coding: utf-8 -*-
"""
需求7：温度 log 取值口径修正（单一事实来源）
========================================================
《分析需求.md》需求7 原文：
  现有温度 log 读取的是"主控温度"列，其数据来源于"T1温度"(<1100℃)、"T2温度"(>=1100℃)。
  但在步号11 时温度刚好突破此阈值，而"主控温度"在同一步号中只能读取一个温度，
  因此在后期 >1100℃ 时均显示 1100℃，与实际不符。
  → 在 T1<1100℃ 时用 T1 作为 log 温度，>1100℃ 时用 T2 作为 log 温度。

实测确认的两个缺陷（本模块要修掉的问题）：
  1) **切换滞后**：T1 饱和冻结于 1100.89℃ 后，"主控温度"仍滞留该值一段时间才跳到 T2。
     - 步11：末值报 1100.9℃，真值应 1152.4℃（最大偏差 51.5℃，31 行）
     - 步12：步中途(07:34)才跳变（最大偏差 106.4℃，42 行）
  2) **回切错误**：降温段 T1 已回落 <1100℃ 时，"主控温度"仍沿用 T2（偏高约 6℃）。
     - 步34（最大 6.6℃）、步35（全程约 5.7℃，646 行）

修正口径（严格按需求7）：
  T1 <  T_SWITCH  → 用 T1
  T1 >= T_SWITCH  → 用 T2

保护：T2 无效（NaN 或低于 T2_MIN）时不采用 T2，回退 T1 并计数告警。
      —— 高量程热电偶在低温段读数恒为 0，若机械套用会把 1100℃ 的炉子算成 0℃。
      实测 run2 有 1 行满足「T1>=1100 且 T2<=0」，run1 为 0 行。

方案C（自适应口径，2026-10-15 多批次扩展时新增）：
  需求7 的前提是 T1/T2 为**同一测点的双量程热电偶**（T1 低温段、T2 高温段，读数应衔接）。
  多批次实测发现个别批次 T1/T2 中位偏差高达数百℃（run3：355.4℃，步11 T1=739 vs T2=1148），
  属**不同测点**而非双量程拼接，机械套用需求7 会把升温段温度改成偏低的 T1 读数（失真约 300℃）。
  → 依各批次 T1/T2 通道一致性自适应：
      中位偏差 |T1-T2| <  T_CONSIST  → 同一测点双量程，按需求7 拼接（原规则）
      中位偏差 |T1-T2| >= T_CONSIST  → 保持源系统『主控温度』原值，
                                        仅修 1100.89℃ 量程饱和冻结伪影（有则修，无则原值即终值）
  实测通道中位偏差：run1=5.7 / run2=2.0 / run3=355.4 / run4=2.9 / run5=19.7 ℃。
========================================================
"""
import numpy as np
import pandas as pd

# ---- 阈值与常量 ----
T_SWITCH = 1100.0      # 需求7 定义的温度切换阈值（℃）
T1_SAT = 1100.89       # T1 热电偶量程饱和值（℃），仅用于诊断计数
T2_MIN = 800.0         # T2 有效下限（℃）：低于此值视为无效读数
T_CONSIST = 30.0       # 方案C：T1/T2 通道一致性阈值（中位偏差 ≥ 此值 → 非同一测点双量程）


def channel_dev(t1, t2):
    """T1/T2 通道一致性：重叠测温窗（T1 未饱和 <T_SWITCH 且 T2 有效 >T2_MIN）内
    逐点偏差绝对值的中位数，单位 ℃。

    物理依据：T1 是低量程偶（>1100.89℃ 即饱和），饱和后 |T1-T2| 对任何批次都必然拉大，
    不能入窗；只有重叠窗内才能判别「同一测点双量程」（中位偏差个位数℃）
    与「不同测点」（run3 实测 355.7℃）。机械全量中位会被低温段 T2=0 与 T1 饱和段污染。
    """
    t1 = pd.to_numeric(pd.Series(t1), errors="coerce").to_numpy(dtype=float)
    t2 = pd.to_numeric(pd.Series(t2), errors="coerce").to_numpy(dtype=float)
    ok = (t1 < T_SWITCH) & (t2 > T2_MIN) & ~np.isnan(t1) & ~np.isnan(t2)
    if not ok.any():
        return float("nan")
    return float(np.median(np.abs(t1[ok] - t2[ok])))


def log_temp(t1, t2, master=None):
    """
    生成 log 温度。

    master=None            → 纯需求7 拼接（T1<T_SWITCH 取 T1，否则取 T2，T2 无效回退 T1）
    master 给定            → 方案C 自适应：先算 T1/T2 通道中位偏差，
                             一致 → 同上（需求7 拼接）；
                             不一致 → 保持源主控原值，仅修 1100.89℃ 冻结伪影
                             （冻结点改取 T2，T2 无效回退 T1）。

    参数
      t1, t2 : 等长序列，"T1温度" / "T2温度"（℃）
      master : 可选，源系统"主控温度"序列（方案C 自适应依据）

    返回
      T    : np.ndarray，修正后的 log 温度（℃）
      info : dict，诊断信息
             n_total  总点数
             n_use_t2 改用 T2 的点数
             n_t2_bad T1 已饱和但 T2 无效 → 回退 T1 的点数（应尽量为 0）
             n_use_t1 仍用 T1 的点数
             n_t1_sat T1 处于量程饱和(≈1100.89℃)的点数（用于说明修正规模）
             mode     "rule7"（需求7 拼接）| "passthrough"（保持源主控，方案C）
             t12_median T1/T2 通道中位偏差（℃）；n_frozen 源主控冻结伪影行数（passthrough）
    """
    t1 = np.asarray(t1, dtype=float).copy()
    t2 = np.asarray(t2, dtype=float).copy()

    t1_safe = np.nan_to_num(t1, nan=-np.inf)
    t2_safe = np.nan_to_num(t2, nan=-np.inf)

    med = channel_dev(t1, t2) if master is not None else float("nan")

    # ---- 方案C：通道不一致 → 保持源主控，仅修冻结伪影 ----
    if master is not None and med == med and med >= T_CONSIST:
        T = np.asarray(master, dtype=float).copy()
        frozen = np.abs(np.nan_to_num(T, nan=-np.inf) - T1_SAT) < 0.01
        ok2 = t2_safe > T2_MIN
        use_t2 = frozen & ok2
        T = np.where(use_t2, t2, np.where(frozen, t1, T))  # 冻结点 T2 优先、T1 兜底
        info = dict(
            mode="passthrough", t12_median=med, n_frozen=int(frozen.sum()),
            n_total=int(T.size), n_use_t2=int(use_t2.sum()),
            n_t2_bad=int((frozen & ~ok2).sum()),
            n_use_t1=int((frozen & ~ok2).sum()), n_t1_sat=int((t1_safe >= T1_SAT - 0.01).sum()),
        )
        return T, info

    # ---- 通道一致（或未给 master）→ 需求7 拼接 ----
    hot = t1_safe >= T_SWITCH          # T1 已到/越过阈值 → 该用 T2
    ok2 = t2_safe > T2_MIN             # T2 读数有效
    use_t2 = hot & ok2

    T = np.where(use_t2, t2, t1)       # 默认：未过热或 T2 不可用 → 用 T1

    info = dict(
        mode="rule7", t12_median=med, n_frozen=0,
        n_total=int(T.size),
        n_use_t2=int(use_t2.sum()),
        n_t2_bad=int((hot & ~ok2).sum()),
        n_use_t1=int((~use_t2).sum()),
        n_t1_sat=int((t1_safe >= T1_SAT - 0.01).sum()),
    )
    return T, info


def apply_to_frame(df, t1_col="T1温度", t2_col="T2温度", out_col="主控温度",
                   keep_raw="主控温度_原始", master_col="主控温度", adaptive=True):
    """
    原地/返回：把 df 的 log 温度列替换为修正值（方案C 自适应口径）。
      - adaptive=True 且 df 含 master_col → 依 T1/T2 通道一致性自适应：
        一致走需求7 拼接；不一致保持源主控原值（仅修 1100.89℃ 冻结伪影）
      - out_col  : 写入修正后的温度（默认沿用"主控温度"，保证下游零改动）
      - keep_raw : 若指定且该列尚不存在，先把原值另存一份以便追溯
    返回 info。
    """
    master = df[master_col] if (adaptive and master_col in df.columns) else None
    T, info = log_temp(df[t1_col], df[t2_col], master=master)
    if keep_raw and (keep_raw not in df.columns):
        df[keep_raw] = df[out_col] if out_col in df.columns else np.nan
    df[out_col] = T
    return info


# ---------------------------------------------------------------- 源系统偏差诊断（R4：文案按数据生成）
def diag(proc):
    """从装配后的 log 帧统计源系统『主控温度』的两类偏差（切换滞后 / 回切错误）。

    参数 proc：含 当前运行步号 / T1温度 / T2温度 / 主控温度(已修正) / 主控温度_原始 的 DataFrame。
    返回 dict（数值全部来自当期数据，供说明页/PPT 生成文案，禁止再写死步号与温度值）。
    """
    import pandas as pd
    t1 = pd.to_numeric(proc.get("T1温度"), errors="coerce")
    t2 = pd.to_numeric(proc.get("T2温度"), errors="coerce")
    raw = (pd.to_numeric(proc["主控温度_原始"], errors="coerce")
           if "主控温度_原始" in proc else pd.Series(np.nan, index=proc.index))
    step = pd.to_numeric(proc.get("当前运行步号"), errors="coerce")

    med = channel_dev(t1, t2)
    passthrough = med == med and med >= T_CONSIST   # 方案C：通道不一致批次

    sat = (raw - T1_SAT).abs() < 0.01                      # ① 源主控冻结在 T1 量程上限
    n_sat = int(sat.sum())

    s11 = proc[step == 11]
    v11_raw = float(pd.to_numeric(s11["主控温度_原始"], errors="coerce").iloc[-1]) if len(s11) else float("nan")
    v11_fix = float(pd.to_numeric(s11["主控温度"], errors="coerce").iloc[-1]) if len(s11) else float("nan")

    n12, dev12 = 0, float("nan")                           # 步12 步中途才跳变
    s12 = proc[step == 12]
    if len(s12):
        r12 = pd.to_numeric(s12["主控温度_原始"], errors="coerce")
        m12 = (r12 - T1_SAT).abs() < 0.01
        n12 = int(m12.sum())
        if n12:
            dev12 = float((pd.to_numeric(s12["T2温度"], errors="coerce")[m12] - r12[m12]).max())

    n_back, back_steps, max_dev = 0, [], float("nan")      # ② 降温段回切错误
    if not passthrough:   # passthrough 批次源主控合法地跟随 T2，回切检测会假阳性 → 跳过
        cool = step >= 26
        m = cool & (t1 < T_SWITCH) & (t2 > T2_MIN) & ((raw - t2).abs() < 0.5)
        n_back = int(m.sum())
        back_steps = sorted({int(x) for x in step[m].dropna()}) if n_back else []
        max_dev = float((raw[m] - t1[m]).max()) if n_back else float("nan")

    return dict(mode="passthrough" if passthrough else "rule7", t12_median=med,
                n_sat=n_sat, v11_raw=v11_raw, v11_fix=v11_fix, n12=n12, dev12=dev12,
                n_back=n_back, back_steps=back_steps, max_dev=max_dev)


def diag_text(di):
    """把 diag() 结果转成一句可上报告的说明（含方案C 口径判定）。"""
    if di.get("mode") == "passthrough":
        s = (f"T1/T2 双测温通道中位偏差 {di['t12_median']:.0f}℃，属不同测点而非同一测点双量程，"
             f"《需求7》拼接前提不成立——本批次保持源系统『主控温度』原值")
        s += (f"（检出 1100.89℃ 冻结伪影 {di['n_sat']} 行，已按 T2/T1 兜底修正）" if di["n_sat"]
              else "（未检出 1100.89℃ 冻结伪影，原值即最终口径）")
        return s
    pre = f"T1/T2 双测温通道中位偏差 {di['t12_median']:.1f}℃（同一测点双量程）。"
    if not di["n_sat"] and not di["n_back"]:
        return pre + "源系统『主控温度』未检出切换滞后/回切错误"
    seg = []
    if di["n_sat"]:
        s = f"① 切换滞后——步11 末值冻结在 T1 量程上限 {T1_SAT}℃（真值应取 T2 ≈{di['v11_fix']:.1f}℃）"
        if di["n12"] and di["dev12"] == di["dev12"]:
            s += f"、步12 步中途才跳变（冻结段最大偏低 {di['dev12']:.1f}℃）"
        seg.append(s)
    if di["n_back"]:
        st = "/".join(f"步{s}" for s in di["back_steps"])
        seg.append(f"② 回切错误——降温段 {st} 在 T1 已回落 <{T_SWITCH:.0f}℃ 时源主控仍沿用 T2"
                   f"（共 {di['n_back']} 行，相对 T1 最大偏差 {di['max_dev']:+.1f}℃）")
    return pre + "源系统原主控值存在两类偏差：" + "；".join(seg) + "。本版已按需求7 消除"
