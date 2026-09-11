# -*- coding: utf-8 -*-
"""页签②：硬性条件清单 → 电价确认/手填曲线 → 综合评分。作者: jiali.qiu"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from storage_scoring import (
    CABINET_KWH,
    CABINET_KW,
    HARD_CHOICES,
    HARD_ITEMS,
    ScoreInput,
    evaluate_hard_gate,
    score_and_invest,
)
from tariff_lib import cities_by_province, load_library, match_tariff, tariff_row_fields, tariff_schedule_rows


def _score_step_bar(step: int) -> None:
    labels = ["1 硬性条件", "2 电价与曲线", "3 综合评分"]
    cols = st.columns(3)
    for i, (col, lab) in enumerate(zip(cols, labels), start=1):
        with col:
            if i == step:
                st.markdown(f"**● {lab}**")
            elif i < step:
                st.caption(f"✓ {lab}")
            else:
                st.caption(f"○ {lab}")


def _hard_answers_from_state() -> dict[str, str]:
    edited = st.session_state.get("hard_editor")
    if isinstance(edited, pd.DataFrame) and "id" in edited.columns and "判定" in edited.columns:
        out = {it["id"]: "待确认" for it in HARD_ITEMS}
        for _, row in edited.iterrows():
            iid = str(row.get("id") or "")
            if iid:
                out[iid] = str(row.get("判定") or "待确认")
        return out
    return {it["id"]: str(st.session_state.get(f"hard_{it['id']}", "待确认")) for it in HARD_ITEMS}


def _render_hard_extras(pre: dict) -> None:
    hint = str(pre.get("场地能否放储能柜") or "")
    if hint:
        st.caption(f"摸底表「场地能否放储能柜」= {hint}，现场尺寸请再核一次。")
    a, b, c = st.columns(3)
    with a:
        st.number_input("场地长 m", min_value=0.0, step=0.5, key="hard_space_l")
        st.number_input("场地宽 m", min_value=0.0, step=0.5, key="hard_space_w")
        st.selectbox("地面", ["不确定", "硬化地面", "回填土未处理", "地下室/管廊顶板"], key="hard_ground_type")
    with b:
        st.number_input("地基高出地面 mm", min_value=0.0, step=50.0, key="hard_drain_mm")
        st.number_input(
            "变压器还能再接 kVA",
            min_value=0.0,
            value=float(pre.get("变压器还能再接（千伏安）") or pre.get("剩余可用kVA") or 0) or 0.0,
            key="hard_tf_headroom",
        )
        st.number_input("并网点距离 m", min_value=0.0, step=5.0, key="hard_tf_dist")
    with c:
        st.selectbox("场地权属", ["不确定", "自有", "租赁", "权属不清"], key="hard_title_kind")


def _render_hard_step(pre: dict) -> None:
    st.markdown("### 1. 硬性条件")
    st.caption("八项都选「适合」才能定方案。「不适合」直接不配储。「待确认」= 没填完。")
    table = pd.DataFrame(
        [
            {
                "id": it["id"],
                "问": it.get("ask") or it["name"],
                "适合才过": it.get("short") or it["fit"],
                "判定": "待确认",
            }
            for it in HARD_ITEMS
        ]
    )
    edited = st.data_editor(
        table,
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        height=332,
        key="hard_editor",
        column_config={
            "id": None,
            "问": st.column_config.TextColumn("问", width="medium", disabled=True),
            "适合才过": st.column_config.TextColumn("适合才过", width="medium", disabled=True),
            "判定": st.column_config.SelectboxColumn(
                "判定",
                options=list(HARD_CHOICES),
                required=True,
                width="small",
            ),
        },
    )
    answers = {str(r["id"]): str(r["判定"]) for _, r in edited.iterrows()}

    with st.expander("补充现场数字（选填）", expanded=False):
        _render_hard_extras(pre)

    gate = evaluate_hard_gate(answers)
    m1, m2, m3 = st.columns(3)
    m1.metric("硬性得分", gate.score)
    m2.metric("未填", len(gate.pending))
    m3.metric("不适合", len(gate.fails))

    c1, c2 = st.columns(2)
    with c1:
        go = st.button("硬性已确认，进入电价与曲线", type="primary", disabled=not gate.passed)
    with c2:
        fail_go = st.button("硬门槛未过，直接出不配储结论", disabled=not gate.complete or gate.passed)

    if go and gate.passed:
        st.session_state["score_hard"] = gate
        st.session_state["score_wizard"] = 2
        st.rerun()
    if fail_go and gate.complete and not gate.passed:
        st.session_state["score_hard"] = gate
        dummy = ScoreInput(
            scene="厂区",
            tf_kva=float(pre.get("变压器额定kVA") or 0) or 0.0,
            peak_kw=0.0,
            overload_hours=0.0,
            valley_idle_hours=0.0,
            valley_headroom_kw=0.0,
            peak_yuan=0.0,
            valley_yuan=0.0,
            two_part=True,
            demand_waiver=False,
            demand_yuan_per_kw_month=0.0,
            daily_kwh=0.0,
            service_fee=0.0,
            purchase_market=False,
            site_ok=False,
            chg_controllable=False,
            hard_passed=False,
            site_score_pts=gate.score,
        )
        st.session_state["score_last"] = score_and_invest(dummy)
        st.session_state["score_wizard"] = 3
        st.rerun()
    if not gate.complete:
        st.info("还有未判定项：" + "、".join(gate.pending))
    elif gate.fails:
        st.error("硬门槛未过：" + "、".join(gate.fails) + "。不能定配储方案。")


def _scene_index(pre: dict) -> int:
    raw = str(pre.get("资源类型") or pre.get("场景类型") or "")
    if "厂区" in raw and "充电" in raw:
        return 2
    if "厂区" in raw or "可调负荷" in raw:
        return 1
    return 0


def _render_tariff_curve_step(pre: dict) -> None:
    st.markdown("### 2. 电价确认（可改）与手填曲线")
    if st.button("← 返回硬性条件"):
        st.session_state["score_wizard"] = 1
        st.rerun()

    lib = load_library()
    geo = cities_by_province(lib)
    provinces = list(geo.keys())
    pre_prov = str(pre.get("省") or "").replace("省", "").replace("市", "")
    pre_city = str(pre.get("市") or "").replace("市", "")
    if pre_prov not in provinces and provinces:
        pre_prov = provinces[0]
    p_i = provinces.index(pre_prov) if pre_prov in provinces else 0
    g1, g2 = st.columns(2)
    with g1:
        prov = st.selectbox("省", provinces, index=p_i, key="score_prov")
    cities = geo.get(prov) or ["（请选市）"]
    if pre_city in cities:
        c_i = cities.index(pre_city)
    else:
        c_i = 0
    with g2:
        city = st.selectbox("市", cities, index=c_i, key="score_city")
    t0 = match_tariff(str(prov or ""), str(city or ""), lib)
    if pre:
        st.caption(
            f"带入筛站：{pre.get('场站名称') or '（未带入，请自选省市）'} · {pre.get('结论') or ''}"
        )

    market = st.checkbox(
        "已直接入市（政府固定峰谷取消，须手填零售/现货价）",
        value=str(pre.get("购电方式") or "").startswith("已直接"),
        key="score_market",
    )
    tf = tariff_row_fields(t0, purchase_market=market)
    regime = (t0 or {}).get("market") if market else (t0 or {}).get("agent")
    mark = (regime or {}).get("marker") or tf.get("电价标记") or ""
    form = (regime or {}).get("form_label") or tf.get("电价形态") or ""
    usable = bool((regime or {}).get("usable_for_spread")) if regime else False

    if mark.startswith("⭐") or (regime and not usable):
        st.warning(
            "⭐ 该用户已执行动态/市场化分时。下面时段仅供对照，库内政府峰谷**不能当保底套利**。"
            "请把「本次测算」峰/谷改成零售合同或现货加权价。"
        )
    elif "即将变动" in str(mark):
        st.warning("【政策即将变动需复核】库内价可暂用，上会前必须对当月官方表。")
    elif not t0:
        st.info("该省市未入库。请手填峰谷，不可套默认。")

    meta = pd.DataFrame(
        [
            {"项目": "电价库", "内容": tf.get("电价库") or "未入库"},
            {"项目": "适用用户", "内容": (regime or {}).get("user_label") or "—"},
            {"项目": "电价形态", "内容": f"{form} {mark}".strip()},
            {"项目": "库内价可否测算套利", "内容": tf.get("库内价可否测算套利") or "不可以"},
            {"项目": "单价锚", "内容": (t0 or {}).get("price_anchor") or "—"},
            {"项目": "电价置信", "内容": tf.get("电价置信") or "—"},
            {
                "项目": "需量 / 容量",
                "内容": (
                    f"{tf.get('需量元/kW月') or '—'} 元/kW·月；"
                    f"{tf.get('容量元/kVA月') or '—'} 元/kVA·月"
                ),
            },
        ]
    )
    st.markdown("#### 库内匹配")
    st.dataframe(meta, hide_index=True, use_container_width=True)

    sched = pd.DataFrame(tariff_schedule_rows(t0, purchase_market=market))
    st.markdown("#### 分时时段与库内电价")
    st.caption("每一行是一段钟点。同一类型多段共用一个库内单价。尖峰/深谷若未单列单价，见说明。")
    st.dataframe(sched, hide_index=True, use_container_width=True)

    lib_peak = float(tf.get("峰电价") or 0.0) if usable else 0.0
    lib_valley = float(tf.get("谷电价") or 0.0) if usable else 0.0
    lib_flat = float(tf.get("平电价") or 0.0) if usable and tf.get("平电价") else 0.0
    lib_demand = float(tf.get("需量元/kW月") or 36.0)

    st.markdown("#### 本次测算用电价（可改）")
    st.caption("默认带入库内值（入市或不可用则为 0）。改了以你填的为准。")

    p1, p2, p3, p4 = st.columns(4)
    with p1:
        peak_p = st.number_input(
            "峰电价 元/kWh",
            min_value=0.0,
            value=lib_peak,
            step=0.01,
            key=f"score_peak_p_{int(market)}_{tf.get('电价库')}",
        )
    with p2:
        valley_p = st.number_input(
            "谷电价 元/kWh",
            min_value=0.0,
            value=lib_valley,
            step=0.01,
            key=f"score_valley_p_{int(market)}_{tf.get('电价库')}",
        )
    with p3:
        st.number_input("平电价 元/kWh（备查）", min_value=0.0, value=lib_flat, step=0.01, key="score_flat_p")
    with p4:
        demand_p = st.number_input("需量 元/kW·月", min_value=0.0, value=lib_demand, step=0.1, key="score_demand_p")
    st.metric("本次价差 元/kWh", f"{max(0.0, peak_p - valley_p):.3f}")

    st.markdown("#### 手填曲线结论（不上传文件）")
    st.caption("从关口或充电曲线读：峰有多高、能顶多久、谷段有没有空窗。")
    s1, s2, s3 = st.columns(3)
    with s1:
        scene = st.selectbox("场景", ["充电", "厂区", "混合"], index=_scene_index(pre), key="score_scene")
        tf_kva = st.number_input(
            "变压器额定 kVA",
            min_value=0.0,
            value=float(pre.get("变压器额定kVA") or 500) or 500.0,
            key="score_tf_kva",
        )
        two_part = st.checkbox("两部制（有需量/容量电费）", value=True, key="score_two_part")
        waiver = st.checkbox(
            "适用42号免需量（集中式充换电）",
            value=(str(pre.get("用电性质") or "") == "充电设施" or _scene_index(pre) == 0),
            key="score_waiver",
        )
    with s2:
        peak_kw = st.number_input("峰时负荷 kW", min_value=0.0, value=400.0, key="score_peak_kw")
        overload_h = st.number_input(
            "峰值超过变压器/需量的时长（小时/天）",
            min_value=0.0,
            max_value=24.0,
            value=1.0,
            key="score_overload_h",
        )
        daily = st.number_input(
            "日均用电或充电量 kWh",
            min_value=0.0,
            value=float(pre.get("日均用电或充电量kWh") or 800) or 800.0,
            key="score_daily",
        )
    with s3:
        idle_h = st.number_input("谷段空窗（小时/天）", min_value=0.0, max_value=24.0, value=3.0, key="score_idle_h")
        headroom = st.number_input("谷段还可充电功率 kW", min_value=0.0, value=200.0, key="score_headroom")
        service = st.number_input(
            "充电服务费 元/kWh（厂区填0）",
            min_value=0.0,
            value=0.4 if scene != "厂区" else 0.0,
            key="score_service",
        )
        chg_ctrl = st.checkbox(
            "充电可调",
            value=str(pre.get("充电可调") or pre.get("能调控?") or pre.get("能调控") or "")
            in ("是", "能", "部分能", "需改造"),
            key="score_chg_ctrl",
        )

    if st.button("计算综合评分", type="primary"):
        if not usable and (peak_p <= 0 or valley_p <= 0):
            st.error("该用户类型库内政府峰谷不能当保底。请手填零售/现货峰谷后再算。")
            return
        gate = st.session_state.get("score_hard") or evaluate_hard_gate(_hard_answers_from_state())
        inp = ScoreInput(
            scene=scene,
            tf_kva=tf_kva,
            peak_kw=peak_kw,
            overload_hours=overload_h,
            valley_idle_hours=idle_h,
            valley_headroom_kw=headroom,
            peak_yuan=peak_p,
            valley_yuan=valley_p,
            two_part=two_part,
            demand_waiver=waiver,
            demand_yuan_per_kw_month=demand_p,
            daily_kwh=daily,
            service_fee=service,
            purchase_market=market,
            site_ok=gate.passed,
            chg_controllable=chg_ctrl,
            hard_passed=gate.passed,
            site_score_pts=gate.score,
        )
        st.session_state["score_last"] = score_and_invest(inp)
        st.session_state["score_wizard"] = 3
        st.rerun()


def _render_score_output() -> None:
    st.markdown("### 3. 综合评分")
    b1, b2 = st.columns(2)
    with b1:
        if st.button("← 返回改电价与曲线"):
            st.session_state["score_wizard"] = 2
            st.rerun()
    with b2:
        if st.button("从硬性条件重填"):
            st.session_state["score_wizard"] = 1
            st.rerun()

    r = st.session_state.get("score_last")
    gate = st.session_state.get("score_hard")
    if r is None:
        st.warning("还没有评分结果。")
        return
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("综合分", r.score_total)
    k2.metric("分型", f"{r.type_code} {r.type_name}")
    k3.metric("建议柜数", f"{r.n_cabinets} × {int(CABINET_KWH)}kWh")
    k4.metric("粗回本（年）", "—" if r.payback_years is None else r.payback_years)
    if r.type_code in ("D", "E"):
        st.error(r.verdict)
    else:
        st.success(r.verdict)
    extra = f"（打分表 {gate.score}/100）" if gate is not None else ""
    st.write(
        f"硬性落地分 {r.score_site}{extra}"
        f" · 容量分 {r.score_cap} · 价差分 {r.score_spread} · 空窗分 {r.score_idle}"
    )
    st.caption("综合分 = 容量 30% ＋ 价差 30% ＋ 空窗 25% ＋ 硬性落地 15%。柜型 125kW/262kWh。V2G/补贴不进保底。")
    st.write(f"增量 CAPEX 约 **{r.capex_wan} 万**（{int(CABINET_KW)}kW/{int(CABINET_KWH)}kWh × {r.n_cabinets}）")
    st.write(f"年增量约 **{r.annual_wan} 万**")
    st.write(f"落地 EGC 预制：**{r.egc_preset}**")
    st.write(f"建议勾选：{r.egc_ticks}")
    if gate is not None and gate.fails:
        st.warning("未过项：" + "、".join(gate.fails))
    for n in r.notes:
        st.caption("· " + n)


def reset_score_wizard() -> None:
    """带入新站时清掉上一次硬性判定和评分，保留 score_prefill。"""
    keep = {"score_prefill"}
    for k in list(st.session_state.keys()):
        if str(k).startswith(("hard_", "score_")) and k not in keep:
            del st.session_state[k]
    st.session_state.pop("hard_editor", None)
    st.session_state["score_wizard"] = 1


def render_scoring_page() -> None:
    """页签②主流程。"""
    st.subheader("② 配储评分与粗投资")
    st.caption("先过硬性条件清单，再确认电价、手填曲线，最后出综合评分。不上传曲线文件。")
    pre = st.session_state.get("score_prefill") or {}
    step = int(st.session_state.get("score_wizard") or 1)
    _score_step_bar(step)
    if step <= 1:
        _render_hard_step(pre)
    elif step == 2:
        _render_tariff_curve_step(pre)
    else:
        _render_score_output()
