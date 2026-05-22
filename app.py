# -*- coding: utf-8 -*-
"""
工商业储充场站成本收益测算工具 — Streamlit 单页应用。

作者: zhi.qu
说明: 业务口径调整请优先修改 compute_projection() 内公式与分项字典。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

from reverse_analysis import (
    ForwardBaseline,
    PaybackMetric,
    SubsidySolveMode,
    baseline_from_projection,
    cashflow_compare_rows,
    solve_subsidy_for_target_payback,
)
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:
    import numpy_financial as npf
except ImportError:
    npf = None


# ----------------------------
# Streamlit：页面配置（必须尽早调用）
# ----------------------------
st.set_page_config(
    page_title="工商业储充场站成本收益测算工具",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ----------------------------
# 常量：储能用途枚举（与表单下拉框一致）
# ----------------------------
USAGE_PEAK_VALLEY_ONLY = "纯峰谷套利"
USAGE_DYNAMIC_CAPACITY = "纯动态增容+充电"
USAGE_HYBRID = "峰谷套利+充电"
# 旧版配置/方案快照中的下拉文案（载入时映射到 USAGE_HYBRID）
USAGE_HYBRID_LEGACY = "峰谷套利+大功率快充混合"

USAGE_OPTIONS = [USAGE_PEAK_VALLEY_ONLY, USAGE_HYBRID, USAGE_DYNAMIC_CAPACITY]
# 峰谷+充电：简化假设用户充电发生在峰时段（界面计算过程会高亮提示）
HYBRID_PEAK_CHARGE_ASSUMPTION = (
    "简化假设：<strong>充电全部发生在峰时段</strong>，用户按峰电价支付电量电费，"
    "另按元/kWh 收取充电服务费（服务费不含电费）。"
)
STORAGE_INSTALL_NO = "不安装工商业储能"
STORAGE_INSTALL_YES = "安装工商业储能"
STORAGE_INSTALL_OPTIONS = [STORAGE_INSTALL_NO, STORAGE_INSTALL_YES]
GOV_SUBSIDY_NONE = "无"
GOV_SUBSIDY_ONCE = "一次性"
GOV_SUBSIDY_INSTALLMENT = "分期"
GOV_SUBSIDY_MODE_OPTIONS = [GOV_SUBSIDY_NONE, GOV_SUBSIDY_ONCE, GOV_SUBSIDY_INSTALLMENT]
# 第三步未装储能时，第四步储能工程 CAPEX 口径说明（表单与结果区共用）
STORAGE_ENG_CAPEX_EXCLUDED_NOTE = (
    "当前为 **不安装工商业储能**：本区填写仅作预留；**确认测算时储能工程 CAPEX 不计入** "
    "总投资、回本周期与 ROI。"
)
CYCLE_STRATEGY_1 = "一充一放"
CYCLE_STRATEGY_2 = "二充二放"
CYCLE_STRATEGY_15 = "1.5充1.5放"
CYCLE_STRATEGY_OPTIONS = [CYCLE_STRATEGY_1, CYCLE_STRATEGY_2, CYCLE_STRATEGY_15]
HP_OPTIONS = ["否", "是"]  # 用于「是否配置分体式液冷480kW」等是/否开关
PARKING_VEHICLE_OPTIONS = ["乘用车", "重卡"]
TRANSFORMER_UPGRADE_OPTIONS = ["不增容", "原址换大", "高压申请"]
# 现有变压器额定容量（选填）标准档位
TRANSFORMER_RATED_KVA_OPTIONS: list[int] = [
    80, 100, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
]
TRANSFORMER_RATED_SELECT_LABELS: list[str] = ["（不填）"] + [
    f"{k} kVA" for k in TRANSFORMER_RATED_KVA_OPTIONS
]
POWER_FACTOR_MIN = 0.8
POWER_FACTOR_MAX = 0.9
POWER_FACTOR_DEFAULT = 0.85
SITE_LAYOUT_OPTIONS = ["室内", "室外"]
ULTRA_480_DEFAULT_PRICE_WAN = 40.0
PILE_60_DEFAULT_PRICE_WAN = 3.0
PILE_120_DEFAULT_PRICE_WAN = 8.0
_SLOT_DESC_BY_VEHICLE: dict[str, str] = {
    "乘用车": "乘用车标准泊位",
    "重卡": "重卡泊位",
}

# 液冷 480kW：业务约定「一套主机」带 **4 个充电终端**（业务口径可调常量）
ULTRA_CHARGE_TERMINALS_PER_RACK = 4
# 120kW 双枪：1 台桩占 2 个车位
PILE_120_SPOTS_PER_UNIT = 2
# 功率余量超过该值（kW）时在「数量与单价」区给出优化建议
CHARGER_UTILIZATION_SLACK_KW = 60.0
# 储能电池 SOH 衰减系数（第 1–15 年），影响可放电量与储能充电成本
BATTERY_SOH_BY_YEAR: tuple[float, ...] = (
    0.933496,
    0.906421,
    0.883572,
    0.863047,
    0.844068,
    0.826222,
    0.809229,
    0.792924,
    0.777192,
    0.761945,
    0.747117,
    0.732639,
    0.71847,
    0.704576,
    0.69093,
)
DEFAULT_VAT_RATE = 0.13
DEFAULT_INCOME_TAX_RATE = 0.25
DEFAULT_DISCOUNT_RATE = 0.08
DEFAULT_DEPRECIATION_YEARS = 10
DEFAULT_SALVAGE_RATE = 0.05
SURTAX_RATE_ON_VAT_PAYABLE = 0.12  # 附加税简化为应纳增值税×12%（城建+教育附加）
# 优化建议用额定（未纳入投资测算模型）
PILE_RATED_KW_240_RECOMMEND = 240.0
KT_SIMULTANEITY_NOTE = (
    "* **同时使用率 Kt ≤ 1**（同一时间启用的桩功率占比）。"
    "一般规划取 **中间值 0.7**；项目有实测或运行数据时按实际值填写。"
    "仅 **Kt &lt; 1** 时存在超需风险，需考虑桩侧限功率或微网监测（成本见第四部分）。"
    "**Kt = 1** 为满载同时，无需微网控制器。"
)
KT_MICROGRID_RISK_NOTE = (
    "**Kt < 1**：存在超需风险，建议配置**桩侧限功率**或**本地微网控制器**"
    "（成本计入，见第四部分）。"
    "一般规划时 Kt 取中间值 **0.7**；有实测/运行数据时按实际值选取。"
)
KT_MICROGRID_NOT_REQUIRED_NOTE = (
    "**Kt = 1**：按模型可同时满载，**无需**配置微网控制器；第四部分相关费用可填 **0**。"
)


def _render_planning_kt_feedback(kt: float) -> None:
    """第二步配电系数区：Kt 与微网建议（与「同时使用率」下拉联动）。"""
    if kt_needs_microgrid_controller(kt):
        _html_note(
            f"{_md_emphasis_to_html(KT_MICROGRID_RISK_NOTE)} "
            f"当前规划用 <strong>Kt = {kt:.2f}</strong>。"
        )
    else:
        st.caption(KT_MICROGRID_NOT_REQUIRED_NOTE)


def _html_kt_microgrid_alert(kt: float | None) -> None:
    """第四部分 · 微网控制器：Kt&lt;1 时黄底醒目提示。"""
    if kt is None:
        return
    if kt_needs_microgrid_controller(kt):
        st.markdown(
            f'<div class="kt-microgrid-banner"><p>{_md_emphasis_to_html(KT_MICROGRID_RISK_NOTE)}</p></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="kt-microgrid-banner kt-microgrid-ok"><p>'
            f"{KT_MICROGRID_NOT_REQUIRED_NOTE}</p></div>",
            unsafe_allow_html=True,
        )


def kt_needs_microgrid_controller(kt: float) -> bool:
    """Kt 严格小于 1 时需要微网控制器监测口径。"""
    return float(kt) < 1.0 - 1e-6


def _html_construction_subsection_title(text: str) -> None:
    """第四部分工程块标题（H3，与结果区分项同级）。"""
    _html_block_title(text)


def _html_construction_section_divider() -> None:
    """第四部分各工程块之间的虚线分隔。"""
    st.markdown('<hr class="construction-section-dash" />', unsafe_allow_html=True)


def _html_construction_section_total(total_yuan: float, section_name: str) -> None:
    """第四部分各子区块末尾：本分项总价（元/万元）。"""
    _html_summary_line(
        f"{section_name} 合计：<strong>{total_yuan / 1e4:.2f} 万元</strong> "
        f"（{total_yuan:,.0f} 元）"
    )


# 第四部分：充电桩工程 — 路径长度 = 第一步「变压器到最远车位」× 系数
ROUTE_LEN_FACTOR = 1.2

# （字段后缀, 界面简称, 默认单价 元/米或元/件）
CHARGER_CABLE_SPECS: list[tuple[str, str, float]] = [
    ("cable_yjlhv300", "ZC-YJLHV22 3×300+1×150", 115.0),
    ("cable_yjlhv240", "ZC-YJLHV22 3×240+2×120", 105.0),
    ("cable_yjv240", "ZC-YJV22 4×240", 1030.0),
    ("cable_yjv70", "ZC-YJV22 1×70", 70.0),
    ("cable_yjv185", "ZC-YJV22 3×185+2×95", 790.0),
]

CHARGER_PIPE_SPECS: list[tuple[str, str, float]] = [
    ("pipe50", "热镀锌钢管 φ50", 3.0),
    ("pipe100", "热镀锌钢管 φ100", 60.0),
]

DC_CABLE_MATERIAL_COPPER = "铜缆"
DC_CABLE_MATERIAL_ALUMINUM = "铝缆"
DC_CABLE_MATERIAL_OPTIONS = [DC_CABLE_MATERIAL_COPPER, DC_CABLE_MATERIAL_ALUMINUM]

CHARGER_ENG_LEN_KEYS = (
    [f"chg_eng_{s[0]}_len_m" for s in CHARGER_CABLE_SPECS]
    + [f"chg_eng_{s[0]}_len_m" for s in CHARGER_PIPE_SPECS]
    + [
        "chg_eng_dc_copper_len_m",
        "chg_eng_dc_aluminum_len_m",
        "chg_eng_lay_direct_len_m",
        "chg_eng_lay_slope_len_m",
    ]
)

CHARGER_ENG_BUILTIN_DEFAULTS: dict[str, Any] = {
    "chg_eng_ggd_qty": 1,
    "chg_eng_ggd_unit_yuan": 18000.0,
    "chg_eng_ggd_install_yuan": 1050.0,
    "chg_eng_meter_qty": 1,
    "chg_eng_meter_unit_yuan": 2300.0,
    "chg_eng_meter_install_yuan": 105.0,
    "chg_eng_cable_yjlhv300_unit_yuan": 115.0,
    "chg_eng_cable_yjlhv240_unit_yuan": 105.0,
    "chg_eng_cable_yjv240_unit_yuan": 1030.0,
    "chg_eng_cable_yjv70_unit_yuan": 70.0,
    "chg_eng_cable_yjv185_unit_yuan": 790.0,
    "chg_eng_pipe50_unit_yuan": 3.0,
    "chg_eng_pipe100_unit_yuan": 60.0,
    "chg_eng_grounding_flat_yuan": 200.0,
    "chg_eng_fire_seal_yuan": 2900.0,
    "chg_eng_lay_direct_unit_yuan": 42.0,
    "chg_eng_lay_slope_unit_yuan": 240.0,
    "chg_eng_extinguisher_qty": 4,
    "chg_eng_extinguisher_unit_yuan": 525.0,
    "chg_eng_cement_terminal_qty": 0,
    "chg_eng_cement_terminal_unit_yuan": 300.0,
    "chg_eng_microgrid_qty": 1,
    "chg_eng_microgrid_unit_yuan": 6000.0,
    "chg_eng_microgrid_debug_days": 1.0,
    "chg_eng_microgrid_debug_unit_yuan": 1000.0,
    "chg_eng_microgrid_equip_yuan": 6000.0,
    "chg_eng_microgrid_debug_yuan": 1000.0,
    "chg_eng_dc_material": DC_CABLE_MATERIAL_COPPER,
    "chg_eng_dc_copper_unit_yuan": 0.0,
    "chg_eng_dc_aluminum_unit_yuan": 0.0,
}

STORAGE_ENG_LEN_KEYS = (
    "sto_eng_rs485_len_m",
    "sto_eng_cable_yjlhv120_len_m",
    "sto_eng_pipe100_len_m",
)

STORAGE_ENG_BUILTIN_DEFAULTS: dict[str, Any] = {
    "sto_eng_rs485_unit_yuan": 5.5,
    "sto_eng_rs485_install_yuan": 275.0,
    "sto_eng_anti_meter_qty": 2,
    "sto_eng_anti_meter_unit_yuan": 1500.0,
    "sto_eng_anti_meter_install_yuan": 1100.0,
    "sto_eng_cable_yjlhv120_unit_yuan": 50.0,
    "sto_eng_cable_head_qty": 2,
    "sto_eng_cable_head_unit_yuan": 220.0,
    "sto_eng_pipe100_unit_yuan": 60.0,
    "sto_eng_grounding_flat_qty": 20,
    "sto_eng_grounding_flat_unit_yuan": 22.0,
    "sto_eng_grounding_rod_qty": 4,
    "sto_eng_grounding_rod_unit_yuan": 71.0,
    "sto_eng_cabinet_base_qty": 1,
    "sto_eng_cabinet_base_unit_yuan": 3300.0,
    "sto_eng_system_debug_yuan": 5500.0,
    "sto_eng_other_install_yuan": 2000.0,
}


@dataclass
class ChargerEngineeringInputs:
    """充电桩工程 CAPEX 分项（单位：元；长度为米）。"""

    ggd_qty: int
    ggd_unit_yuan: float
    ggd_install_yuan: float
    meter_qty: int
    meter_unit_yuan: float
    meter_install_yuan: float
    cable_yjlhv300_len_m: float
    cable_yjlhv300_unit_yuan: float
    cable_yjlhv240_len_m: float
    cable_yjlhv240_unit_yuan: float
    cable_yjv240_len_m: float
    cable_yjv240_unit_yuan: float
    cable_yjv70_len_m: float
    cable_yjv70_unit_yuan: float
    cable_yjv185_len_m: float
    cable_yjv185_unit_yuan: float
    pipe50_len_m: float
    pipe50_unit_yuan: float
    pipe100_len_m: float
    pipe100_unit_yuan: float
    grounding_flat_yuan: float
    fire_seal_yuan: float
    lay_direct_len_m: float
    lay_direct_unit_yuan: float
    lay_slope_len_m: float
    lay_slope_unit_yuan: float
    extinguisher_qty: int
    extinguisher_unit_yuan: float
    cement_terminal_qty: int
    cement_terminal_unit_yuan: float
    microgrid_equip_yuan: float
    microgrid_debug_yuan: float
    dc_material: str
    dc_copper_len_m: float
    dc_copper_unit_yuan: float
    dc_aluminum_len_m: float
    dc_aluminum_unit_yuan: float


def charger_terminal_count(n60: int, n120: int, n480: int) -> int:
    """充电终端数：60kW 1枪/台，120kW 2枪/台，480kW 4终端/套。"""
    return (
        max(0, int(n60)) * 1
        + max(0, int(n120)) * 2
        + max(0, int(n480)) * ULTRA_CHARGE_TERMINALS_PER_RACK
    )


PARKING_SPOTS_EXPAND_HINT = (
    "占用车位超过场站车位数，落地需**扩充车位**；本工具仍可按当前桩数继续测算（仅供方案比选）。"
)


def cement_terminal_qty_from_cfg(c: dict[str, Any], d: dict[str, Any]) -> int:
    """水泥基础终端数：优先表单/配置 `chg_eng_cement_terminal_qty`，否则按桩数推算。"""
    qty = int(c.get("chg_eng_cement_terminal_qty", 0) or 0)
    if qty > 0:
        return qty
    return charger_terminal_count(
        int(d.get("n_pile_60", 0)),
        int(d.get("n_pile_120", 0)),
        int(d.get("n_ultra_480", 0)),
    )


def _html_len_price_header() -> None:
    h1, h2 = st.columns(2)
    with h1:
        st.markdown('<p class="ui-tone-neutral"><strong>长度 / 数量</strong></p>', unsafe_allow_html=True)
    with h2:
        st.markdown('<p class="ui-tone-neutral"><strong>单价(元 或 元/m)</strong></p>', unsafe_allow_html=True)


def _html_op_param_box(title: str) -> None:
    """第五部分：运营参数小节标题（无外框）。"""
    _html_substep_title(title)


def _html_op_param_box_end() -> None:
    pass


def _html_note(text: str) -> None:
    """备注 / 引导说明（琥珀色正文 + 左侧细线，支持 HTML 或 **粗体**）。"""
    body = text if "<" in text else _md_emphasis_to_html(text)
    st.markdown(
        f'<p class="ui-tone-note ui-tone-note-line">{body}</p>',
        unsafe_allow_html=True,
    )


def _html_stat(text: str) -> None:
    """统计、粗算、沿用数据等辅助数字说明。"""
    st.markdown(f'<p class="ui-tone-stat">{text}</p>', unsafe_allow_html=True)


def _html_link_hint(text: str) -> None:
    """联动字段来源说明。"""
    st.markdown(f'<p class="ui-tone-link">{text}</p>', unsafe_allow_html=True)


def _html_summary_line(text: str) -> None:
    """分项合计、总结行。"""
    st.markdown(f'<p class="ui-tone-summary">{text}</p>', unsafe_allow_html=True)


def _md_emphasis_to_html(text: str) -> str:
    """将 **粗体** 转为 HTML（用于不用 blockquote 的说明行）。"""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def _html_note_panel(text: str) -> None:
    """长说明（与 _html_note 同风格，支持多行与 **粗体**）。"""
    _html_note("<br/>".join(_md_emphasis_to_html(ln) for ln in text.strip().splitlines()))


def _default_route_len_m(farthest_m: float, cfg: dict[str, Any]) -> float:
    if farthest_m > 0:
        return float(farthest_m) * ROUTE_LEN_FACTOR
    legacy = float(cfg.get("cable_len_m", 0.0))
    return legacy if legacy > 0 else 0.0


def _charger_eng_defaults_merged(cfg: dict[str, Any], farthest_m: float) -> dict[str, Any]:
    """合并内置/配置文件默认值；长度类默认 = 路径长度。"""
    out = {**CHARGER_ENG_BUILTIN_DEFAULTS}
    for k, v in cfg.items():
        if k.startswith("chg_eng_"):
            out[k] = v
    mg_qty = max(0, int(float(out.get("chg_eng_microgrid_qty", 1) or 1)))
    mg_unit = max(0.0, float(out.get("chg_eng_microgrid_unit_yuan", 6000.0)))
    mg_days = max(0.0, float(out.get("chg_eng_microgrid_debug_days", 1.0)))
    mg_dbg_unit = max(0.0, float(out.get("chg_eng_microgrid_debug_unit_yuan", 1000.0)))
    out["chg_eng_microgrid_qty"] = mg_qty
    out["chg_eng_microgrid_unit_yuan"] = mg_unit
    out["chg_eng_microgrid_debug_days"] = mg_days
    out["chg_eng_microgrid_debug_unit_yuan"] = mg_dbg_unit
    if "chg_eng_microgrid_qty" in cfg or "chg_eng_microgrid_unit_yuan" in cfg:
        out["chg_eng_microgrid_equip_yuan"] = mg_qty * mg_unit
        out["chg_eng_microgrid_debug_yuan"] = mg_days * mg_dbg_unit
    else:
        equip_legacy = float(cfg.get("chg_eng_microgrid_equip_yuan", 0) or 0) or float(
            cfg.get("microgrid_controller_equip_wan", 0) or 0
        ) * 1e4
        debug_legacy = float(cfg.get("chg_eng_microgrid_debug_yuan", 0) or 0) or float(
            cfg.get("microgrid_controller_debug_wan", 0) or 0
        ) * 1e4
        if equip_legacy > 0:
            out["chg_eng_microgrid_equip_yuan"] = equip_legacy
        if debug_legacy > 0:
            out["chg_eng_microgrid_debug_yuan"] = debug_legacy
    route = _default_route_len_m(farthest_m, cfg)
    out["chg_eng_route_len_m"] = route
    for lk in CHARGER_ENG_LEN_KEYS:
        if float(cfg.get(lk, 0) or 0) > 0:
            out[lk] = float(cfg[lk])
        else:
            out[lk] = route
    return out


def charger_engineering_from_dict(d: dict[str, Any]) -> ChargerEngineeringInputs:
    c = _charger_eng_defaults_merged(d, float(d.get("transformer_to_farthest_m", 0.0)))
    return ChargerEngineeringInputs(
        ggd_qty=int(c["chg_eng_ggd_qty"]),
        ggd_unit_yuan=float(c["chg_eng_ggd_unit_yuan"]),
        ggd_install_yuan=float(c["chg_eng_ggd_install_yuan"]),
        meter_qty=int(c["chg_eng_meter_qty"]),
        meter_unit_yuan=float(c["chg_eng_meter_unit_yuan"]),
        meter_install_yuan=float(c["chg_eng_meter_install_yuan"]),
        cable_yjlhv300_len_m=float(c["chg_eng_cable_yjlhv300_len_m"]),
        cable_yjlhv300_unit_yuan=float(c["chg_eng_cable_yjlhv300_unit_yuan"]),
        cable_yjlhv240_len_m=float(c["chg_eng_cable_yjlhv240_len_m"]),
        cable_yjlhv240_unit_yuan=float(c["chg_eng_cable_yjlhv240_unit_yuan"]),
        cable_yjv240_len_m=float(c["chg_eng_cable_yjv240_len_m"]),
        cable_yjv240_unit_yuan=float(c["chg_eng_cable_yjv240_unit_yuan"]),
        cable_yjv70_len_m=float(c["chg_eng_cable_yjv70_len_m"]),
        cable_yjv70_unit_yuan=float(c["chg_eng_cable_yjv70_unit_yuan"]),
        cable_yjv185_len_m=float(c["chg_eng_cable_yjv185_len_m"]),
        cable_yjv185_unit_yuan=float(c["chg_eng_cable_yjv185_unit_yuan"]),
        pipe50_len_m=float(c["chg_eng_pipe50_len_m"]),
        pipe50_unit_yuan=float(c["chg_eng_pipe50_unit_yuan"]),
        pipe100_len_m=float(c["chg_eng_pipe100_len_m"]),
        pipe100_unit_yuan=float(c["chg_eng_pipe100_unit_yuan"]),
        grounding_flat_yuan=float(c["chg_eng_grounding_flat_yuan"]),
        fire_seal_yuan=float(c["chg_eng_fire_seal_yuan"]),
        lay_direct_len_m=float(c["chg_eng_lay_direct_len_m"]),
        lay_direct_unit_yuan=float(c["chg_eng_lay_direct_unit_yuan"]),
        lay_slope_len_m=float(c["chg_eng_lay_slope_len_m"]),
        lay_slope_unit_yuan=float(c["chg_eng_lay_slope_unit_yuan"]),
        extinguisher_qty=int(c["chg_eng_extinguisher_qty"]),
        extinguisher_unit_yuan=float(c["chg_eng_extinguisher_unit_yuan"]),
        cement_terminal_qty=cement_terminal_qty_from_cfg(c, d),
        cement_terminal_unit_yuan=float(c["chg_eng_cement_terminal_unit_yuan"]),
        microgrid_equip_yuan=float(c["chg_eng_microgrid_equip_yuan"]),
        microgrid_debug_yuan=float(c["chg_eng_microgrid_debug_yuan"]),
        dc_material=str(c.get("chg_eng_dc_material", DC_CABLE_MATERIAL_COPPER)),
        dc_copper_len_m=float(c["chg_eng_dc_copper_len_m"]),
        dc_copper_unit_yuan=float(c["chg_eng_dc_copper_unit_yuan"]),
        dc_aluminum_len_m=float(c["chg_eng_dc_aluminum_len_m"]),
        dc_aluminum_unit_yuan=float(c["chg_eng_dc_aluminum_unit_yuan"]),
    )


def compute_charger_engineering_capex(ce: ChargerEngineeringInputs) -> tuple[float, dict[str, float]]:
    """返回 (合计元, 分项元)。"""
    bd: dict[str, float] = {}
    bd["400V并网柜(GGD)"] = max(0, ce.ggd_qty) * max(0.0, ce.ggd_unit_yuan) + max(
        0.0, ce.ggd_install_yuan
    )
    bd["关口计量表(0.2S)"] = max(0, ce.meter_qty) * max(0.0, ce.meter_unit_yuan) + max(
        0.0, ce.meter_install_yuan
    )
    for suffix, label, _ in CHARGER_CABLE_SPECS:
        ln = max(0.0, float(getattr(ce, f"{suffix}_len_m")))
        up = max(0.0, float(getattr(ce, f"{suffix}_unit_yuan")))
        bd[f"交流电缆·{label}"] = ln * up
    for suffix, label, _ in CHARGER_PIPE_SPECS:
        ln = max(0.0, float(getattr(ce, f"{suffix}_len_m")))
        up = max(0.0, float(getattr(ce, f"{suffix}_unit_yuan")))
        bd[label] = ln * up
    if ce.dc_material == DC_CABLE_MATERIAL_ALUMINUM:
        bd["直流电缆·铝缆"] = max(0.0, ce.dc_aluminum_len_m) * max(0.0, ce.dc_aluminum_unit_yuan)
    else:
        bd["直流电缆·铜缆"] = max(0.0, ce.dc_copper_len_m) * max(0.0, ce.dc_copper_unit_yuan)
    bd["水泥基础(终端)"] = max(0, ce.cement_terminal_qty) * max(0.0, ce.cement_terminal_unit_yuan)
    bd["接地扁铁"] = max(0.0, ce.grounding_flat_yuan)
    bd["防火封堵"] = max(0.0, ce.fire_seal_yuan)
    bd["敷设·直埋"] = max(0.0, ce.lay_direct_len_m) * max(0.0, ce.lay_direct_unit_yuan)
    bd["敷设·坡路"] = max(0.0, ce.lay_slope_len_m) * max(0.0, ce.lay_slope_unit_yuan)
    bd["灭火器"] = max(0, ce.extinguisher_qty) * max(0.0, ce.extinguisher_unit_yuan)
    total = sum(bd.values())
    return total, {k: v for k, v in bd.items() if v > 1e-6}


def render_related_materials_section(
    d: dict[str, Any],
    *,
    spots: int,
    usage: str,
    storage_install: str,
    storage_sets: int,
    peak_reduction_kw: float,
    kva_existing: float,
    kva_remaining_available: float,
    kva_target: float,
    transformer_unit_price: float,
    upgrade_mode: str,
    power_factor: float,
    n_pile_60: int,
    n_pile_120: int,
    n_ultra_480: int,
    plan_kx: float,
    plan_kt: float,
    plan_cos: float,
    plan_eta: float,
) -> tuple[float, float, float]:
    """
    第四部分 · 相关物料：车位土建、其他杂项、变压器增容投资（与第三部分目标容量/单价联动）。
    返回 (单车位土建万元, 其他杂项万元, 本分项合计元)。
    作者：jiali.qiu
    """
    _html_construction_section_divider()
    _html_construction_subsection_title("相关物料")
    st.caption(
        "含车位土建、其他杂项；**变压器增容投资** 与 **第三部分·配电增容** 所填目标总容量、"
        "增容单价一致（含储能动态增容不足时的变压器补足）。"
    )

    preview = SimpleNamespace(
        kva_existing=float(kva_existing),
        kva_remaining_available=float(kva_remaining_available),
        kva_target=float(kva_target),
        transformer_unit_price=float(transformer_unit_price),
        usage=str(usage),
        storage_installed=str(storage_install),
        peak_reduction_kw=float(peak_reduction_kw),
        storage_sets=int(storage_sets),
        demand_coeff_kx=float(plan_kx),
        simultaneity_coeff_kt=float(plan_kt),
        planning_cos_phi=float(plan_cos),
        planning_load_rate_eta=float(plan_eta),
        power_factor=float(power_factor),
        n_pile_60=int(n_pile_60),
        n_pile_120=int(n_pile_120),
        n_ultra_480=int(n_ultra_480),
    )
    tf = compute_transformer_upgrade_capex(preview)

    st.markdown("**变压器增容投资**")
    if tf["capex_yuan"] > 1e-6:
        st.markdown(
            f"- **{tf['label']}**：ΔkVA = 目标 **{tf['k_target_eff']:.0f}** − 基准 **{tf['baseline_kva']:.0f}** "
            f"= **{tf['delta_kva']:.1f} kVA** × 单价 **{transformer_unit_price:.0f}** 元/kVA "
            f"= **{tf['capex_yuan'] / 1e4:.2f} 万元**"
        )
        if tf["dynamic_insufficient"]:
            st.caption("储能动态增容未完全覆盖桩需缺口，已按上式计入变压器增容 CAPEX。")
    elif str(usage) == USAGE_DYNAMIC_CAPACITY and not tf["dynamic_insufficient"]:
        st.caption("纯动态增容且储能已覆盖缺口：**不计**变压器增容投资。")
    elif upgrade_mode == "不增容" and tf["delta_kva"] <= 1e-6:
        st.caption("第一步为「不增容」且目标容量未高于基准：增容投资为 0。")
    else:
        st.caption("当前目标容量与基准相同或增容单价为 0：增容投资为 0。")

    pk1, pk2 = st.columns(2)
    with pk1:
        per_spot = float(
            st.number_input(
                "单车位土建(万元/位)",
                min_value=0.0,
                value=float(d["parking_cost_per_spot_wan"]),
                step=0.1,
                help="土建投资 = 第一步「可建设停车位数量」× 本单价。",
                key="mat_parking_per_spot",
            )
        )
    with pk2:
        other_wan = float(
            st.number_input(
                "其他杂项(万元)",
                min_value=0.0,
                value=float(d["other_construction_wan"]),
                step=1.0,
                help="未列入各工程分项的物料、辅材等。",
                key="mat_other_misc_wan",
            )
        )

    civil_yuan = max(0, int(spots)) * per_spot * 1e4
    misc_yuan = other_wan * 1e4
    tf_yuan = float(tf["capex_yuan"])
    section_yuan = civil_yuan + misc_yuan + tf_yuan
    if civil_yuan > 1e-6:
        st.caption(f"车位土建：**{civil_yuan / 1e4:.2f}** 万元（{spots} 位 × {per_spot:.2f} 万元/位）")
    if misc_yuan > 1e-6:
        st.caption(f"其他杂项：**{misc_yuan / 1e4:.2f}** 万元")
    _html_construction_section_total(section_yuan, "相关物料")
    return per_spot, other_wan, section_yuan


def render_other_equipment_microgrid(d: dict[str, Any], kt: float | None) -> tuple[float, float]:
    """
    第四部分 · 微网控制器（与充电桩工程并列）：设备=数量×单价，调试=天数×元/天。
    作者: jiali.qiu
    """
    _html_construction_section_divider()
    _html_construction_subsection_title("微网控制器")
    _html_kt_microgrid_alert(kt)
    if kt is not None and not kt_needs_microgrid_controller(kt):
        st.caption("同时使用率 **Kt = 1**：无需微网控制器，下述费用不计入投资。")
        _html_construction_section_total(0.0, "微网控制器")
        return 0.0, 0.0
    cfg = _charger_eng_defaults_merged(d, float(d.get("transformer_to_farthest_m", 0.0)))
    r1, r2, r3, r4 = st.columns(4)
    with r1:
        mg_qty = int(
            st.number_input(
                "设备数量(个)",
                min_value=0,
                value=int(cfg["chg_eng_microgrid_qty"]),
                step=1,
                key="mg_eng_qty",
                help="Kt<1 时建议至少 1 个；Kt=1 可填 0。",
            )
        )
    with r2:
        mg_unit = float(
            st.number_input(
                "设备单价(元/个)",
                min_value=0.0,
                value=float(cfg["chg_eng_microgrid_unit_yuan"]),
                step=100.0,
                key="mg_eng_unit_yuan",
            )
        )
    with r3:
        mg_days = float(
            st.number_input(
                "调试天数(天)",
                min_value=0.0,
                value=float(cfg["chg_eng_microgrid_debug_days"]),
                step=0.5,
                key="mg_eng_debug_days",
            )
        )
    with r4:
        mg_dbg_unit = float(
            st.number_input(
                "调试单价(元/天)",
                min_value=0.0,
                value=float(cfg["chg_eng_microgrid_debug_unit_yuan"]),
                step=50.0,
                key="mg_eng_debug_unit_yuan",
            )
        )
    microgrid_equip = max(0, mg_qty) * max(0.0, mg_unit)
    microgrid_debug = max(0.0, mg_days) * max(0.0, mg_dbg_unit)
    st.caption(
        f"设备费 = **{mg_qty}** 个 × **{mg_unit:,.0f}** 元/个 = **{microgrid_equip:,.0f}** 元；"
        f"调试费 = **{mg_days:.1f}** 天 × **{mg_dbg_unit:,.0f}** 元/天 = **{microgrid_debug:,.0f}** 元。"
    )
    mg_total = microgrid_equip + microgrid_debug
    _html_construction_section_total(mg_total, "微网控制器")
    return microgrid_equip, microgrid_debug


def render_charger_engineering_inputs(
    d: dict[str, Any],
    farthest_m: float,
    *,
    n_pile_60: int,
    n_pile_120: int,
    n_ultra_480: int,
) -> ChargerEngineeringInputs:
    """第四部分 · 充电桩工程（左长度/数量、右单价；微网见并列「微网控制器」）。"""
    cfg = _charger_eng_defaults_merged(d, farthest_m)
    route = float(cfg["chg_eng_route_len_m"])
    terminals = charger_terminal_count(n_pile_60, n_pile_120, n_ultra_480)
    _html_construction_subsection_title("充电桩工程")
    st.caption(
        f"默认敷设路径长度 = 最远车位距离 × {ROUTE_LEN_FACTOR} = **{route:.1f} m**"
        f"（第一步 {farthest_m:.0f} m）；电缆/钢管/直流电缆长度初值同此，可逐项修改。"
    )
    e1, e2, e3 = st.columns(3)
    with e1:
        ggd_qty = int(
            st.number_input(
                "400V并网柜 数量(台)",
                min_value=0,
                value=int(cfg["chg_eng_ggd_qty"]),
                step=1,
                help="GGD：250A×2+500A×2+50A+25A×3 塑断等。",
                key="chg_eng_ggd_qty",
            )
        )
        ggd_unit = float(
            st.number_input(
                "并网柜设备单价(元/台)",
                min_value=0.0,
                value=float(cfg["chg_eng_ggd_unit_yuan"]),
                step=100.0,
                key="chg_eng_ggd_unit_yuan",
            )
        )
    with e2:
        ggd_install = float(
            st.number_input(
                "并网柜建安费(元)",
                min_value=0.0,
                value=float(cfg["chg_eng_ggd_install_yuan"]),
                step=50.0,
                key="chg_eng_ggd_install_yuan",
            )
        )
        meter_qty = int(
            st.number_input(
                "关口计量表 数量(块)",
                min_value=0,
                value=int(cfg["chg_eng_meter_qty"]),
                step=1,
                key="chg_eng_meter_qty",
            )
        )
    with e3:
        meter_unit = float(
            st.number_input(
                "计量表单价(元/块)",
                min_value=0.0,
                value=float(cfg["chg_eng_meter_unit_yuan"]),
                step=50.0,
                key="chg_eng_meter_unit_yuan",
            )
        )
        meter_install = float(
            st.number_input(
                "计量表安装费(元)",
                min_value=0.0,
                value=float(cfg["chg_eng_meter_install_yuan"]),
                step=10.0,
                key="chg_eng_meter_install_yuan",
            )
        )

    st.markdown("**水泥基础**")
    cfg_cement_qty = int(cfg.get("chg_eng_cement_terminal_qty", 0) or 0)
    cement_default = cfg_cement_qty if cfg_cement_qty > 0 else terminals
    st.caption(
        f"按 **终端数 × 单价** 计入 CAPEX；参考桩配置约 **{terminals}** 个终端"
        f"（60kW×1 + 120kW×2 + 480kW×{ULTRA_CHARGE_TERMINALS_PER_RACK}/套），可改。"
    )
    cem1, cem2 = st.columns(2)
    with cem1:
        cement_qty = int(
            st.number_input(
                "水泥基础 终端数(个)",
                min_value=0,
                value=cement_default,
                step=1,
                key="chg_eng_cement_terminal_qty",
                help="与桩数可不一致；以本栏填写为准计入测算。",
            )
        )
    with cem2:
        cement_unit = float(
            st.number_input(
                "水泥基础单价(元/终端)",
                min_value=0.0,
                value=float(cfg["chg_eng_cement_terminal_unit_yuan"]),
                step=10.0,
                key="chg_eng_cement_terminal_unit_yuan",
            )
        )

    g1, g2 = st.columns(2)
    with g1:
        grounding_flat = float(
            st.number_input(
                "接地扁铁(元)",
                min_value=0.0,
                value=float(cfg["chg_eng_grounding_flat_yuan"]),
                step=10.0,
                key="chg_eng_grounding_flat_yuan",
            )
        )
    with g2:
        fire_seal = float(
            st.number_input(
                "防火封堵(元)",
                min_value=0.0,
                value=float(cfg["chg_eng_fire_seal_yuan"]),
                step=50.0,
                key="chg_eng_fire_seal_yuan",
            )
        )

    x1, x2 = st.columns(2)
    with x1:
        ext_qty = int(
            st.number_input(
                "灭火器 数量(个)",
                min_value=0,
                value=int(cfg["chg_eng_extinguisher_qty"]),
                step=1,
                key="chg_eng_extinguisher_qty",
            )
        )
    with x2:
        ext_unit = float(
            st.number_input(
                "灭火器 单价(元/个)",
                min_value=0.0,
                value=float(cfg["chg_eng_extinguisher_unit_yuan"]),
                step=10.0,
                key="chg_eng_extinguisher_unit_yuan",
            )
        )

    st.markdown("**电缆与管材（长度 × 单价）**")
    st.caption("以下四项按顺序填写：交流电缆 → 热镀锌钢管 → 直流电缆 → 电缆敷设。")

    with st.expander("① 低压交流电力电缆", expanded=False):
        _html_len_price_header()
        for suffix, label, _def_unit in CHARGER_CABLE_SPECS:
            c_len, c_unit = st.columns(2)
            lk = f"chg_eng_{suffix}_len_m"
            uk = f"chg_eng_{suffix}_unit_yuan"
            with c_len:
                cfg[lk] = float(
                    st.number_input(
                        f"{label} 长度(m)",
                        min_value=0.0,
                        value=float(cfg[lk]),
                        step=1.0,
                        key=f"form_{lk}",
                    )
                )
            with c_unit:
                cfg[uk] = float(
                    st.number_input(
                        f"{label} 单价(元/m)",
                        min_value=0.0,
                        value=float(cfg[uk]),
                        step=1.0,
                        key=f"form_{uk}",
                    )
                )

    with st.expander("② 热镀锌钢管", expanded=False):
        _html_len_price_header()
        pipe_vals: dict[str, tuple[float, float]] = {}
        for suffix, label, _def_unit in CHARGER_PIPE_SPECS:
            c_len, c_unit = st.columns(2)
            lk = f"chg_eng_{suffix}_len_m"
            uk = f"chg_eng_{suffix}_unit_yuan"
            with c_len:
                ln = float(
                    st.number_input(
                        f"{label} 长度(m)",
                        min_value=0.0,
                        value=float(cfg[lk]),
                        step=1.0,
                        key=lk,
                    )
                )
            with c_unit:
                up = float(
                    st.number_input(
                        f"{label} 单价(元/m)",
                        min_value=0.0,
                        value=float(cfg[uk]),
                        step=0.5 if suffix == "pipe50" else 1.0,
                        key=uk,
                    )
                )
            pipe_vals[suffix] = (ln, up)
        pipe50_len, pipe50_unit = pipe_vals["pipe50"]
        pipe100_len, pipe100_unit = pipe_vals["pipe100"]

    with st.expander("③ 低压直流电力电缆（铜缆 / 铝缆 二选一）", expanded=False):
        _html_len_price_header()
        dc_mat = st.radio(
            "电缆材质",
            options=DC_CABLE_MATERIAL_OPTIONS,
            index=_select_index(
                DC_CABLE_MATERIAL_OPTIONS,
                str(cfg.get("chg_eng_dc_material", DC_CABLE_MATERIAL_COPPER)),
                DC_CABLE_MATERIAL_COPPER,
            ),
            horizontal=True,
            key="chg_eng_dc_material",
        )
        if dc_mat == DC_CABLE_MATERIAL_COPPER:
            st.caption("已选 **铜缆**；铝缆长度与单价不参与合计。")
            dc_c_len, dc_c_unit = st.columns(2)
            with dc_c_len:
                dc_copper_len = float(
                    st.number_input(
                        "铜缆 长度(m)",
                        min_value=0.0,
                        value=float(cfg["chg_eng_dc_copper_len_m"]),
                        step=1.0,
                        key="chg_eng_dc_copper_len_m",
                    )
                )
            with dc_c_unit:
                dc_copper_unit = float(
                    st.number_input(
                        "铜缆 单价(元/m)",
                        min_value=0.0,
                        value=float(cfg["chg_eng_dc_copper_unit_yuan"]),
                        step=1.0,
                        key="chg_eng_dc_copper_unit_yuan",
                    )
                )
            dc_aluminum_len = float(cfg["chg_eng_dc_aluminum_len_m"])
            dc_aluminum_unit = float(cfg["chg_eng_dc_aluminum_unit_yuan"])
        else:
            st.caption("已选 **铝缆**；铜缆长度与单价不参与合计。")
            dc_a_len, dc_a_unit = st.columns(2)
            with dc_a_len:
                dc_aluminum_len = float(
                    st.number_input(
                        "铝缆 长度(m)",
                        min_value=0.0,
                        value=float(cfg["chg_eng_dc_aluminum_len_m"]),
                        step=1.0,
                        key="chg_eng_dc_aluminum_len_m",
                    )
                )
            with dc_a_unit:
                dc_aluminum_unit = float(
                    st.number_input(
                        "铝缆 单价(元/m)",
                        min_value=0.0,
                        value=float(cfg["chg_eng_dc_aluminum_unit_yuan"]),
                        step=1.0,
                        key="chg_eng_dc_aluminum_unit_yuan",
                    )
                )
            dc_copper_len = float(cfg["chg_eng_dc_copper_len_m"])
            dc_copper_unit = float(cfg["chg_eng_dc_copper_unit_yuan"])

    with st.expander("④ 电缆敷设", expanded=False):
        _html_len_price_header()
        lay1, lay2 = st.columns(2)
        with lay1:
            lay_direct_len = float(
                st.number_input(
                    "直埋敷设 长度(m)",
                    min_value=0.0,
                    value=float(cfg["chg_eng_lay_direct_len_m"]),
                    step=1.0,
                    key="chg_eng_lay_direct_len_m",
                )
            )
        with lay2:
            lay_direct_unit = float(
                st.number_input(
                    "直埋敷设 单价(元/m)",
                    min_value=0.0,
                    value=float(cfg["chg_eng_lay_direct_unit_yuan"]),
                    step=1.0,
                    key="chg_eng_lay_direct_unit_yuan",
                )
            )
        lay3, lay4 = st.columns(2)
        with lay3:
            lay_slope_len = float(
                st.number_input(
                    "坡路敷设 长度(m)",
                    min_value=0.0,
                    value=float(cfg["chg_eng_lay_slope_len_m"]),
                    step=1.0,
                    key="chg_eng_lay_slope_len_m",
                )
            )
        with lay4:
            lay_slope_unit = float(
                st.number_input(
                    "坡路敷设 单价(元/m)",
                    min_value=0.0,
                    value=float(cfg["chg_eng_lay_slope_unit_yuan"]),
                    step=5.0,
                    key="chg_eng_lay_slope_unit_yuan",
                )
            )

    ce = ChargerEngineeringInputs(
        ggd_qty=ggd_qty,
        ggd_unit_yuan=ggd_unit,
        ggd_install_yuan=ggd_install,
        meter_qty=meter_qty,
        meter_unit_yuan=meter_unit,
        meter_install_yuan=meter_install,
        cable_yjlhv300_len_m=float(cfg["chg_eng_cable_yjlhv300_len_m"]),
        cable_yjlhv300_unit_yuan=float(cfg["chg_eng_cable_yjlhv300_unit_yuan"]),
        cable_yjlhv240_len_m=float(cfg["chg_eng_cable_yjlhv240_len_m"]),
        cable_yjlhv240_unit_yuan=float(cfg["chg_eng_cable_yjlhv240_unit_yuan"]),
        cable_yjv240_len_m=float(cfg["chg_eng_cable_yjv240_len_m"]),
        cable_yjv240_unit_yuan=float(cfg["chg_eng_cable_yjv240_unit_yuan"]),
        cable_yjv70_len_m=float(cfg["chg_eng_cable_yjv70_len_m"]),
        cable_yjv70_unit_yuan=float(cfg["chg_eng_cable_yjv70_unit_yuan"]),
        cable_yjv185_len_m=float(cfg["chg_eng_cable_yjv185_len_m"]),
        cable_yjv185_unit_yuan=float(cfg["chg_eng_cable_yjv185_unit_yuan"]),
        pipe50_len_m=pipe50_len,
        pipe50_unit_yuan=pipe50_unit,
        pipe100_len_m=pipe100_len,
        pipe100_unit_yuan=pipe100_unit,
        grounding_flat_yuan=grounding_flat,
        fire_seal_yuan=fire_seal,
        lay_direct_len_m=lay_direct_len,
        lay_direct_unit_yuan=lay_direct_unit,
        lay_slope_len_m=lay_slope_len,
        lay_slope_unit_yuan=lay_slope_unit,
        extinguisher_qty=ext_qty,
        extinguisher_unit_yuan=ext_unit,
        cement_terminal_qty=cement_qty,
        cement_terminal_unit_yuan=cement_unit,
        microgrid_equip_yuan=0.0,
        microgrid_debug_yuan=0.0,
        dc_material=dc_mat,
        dc_copper_len_m=dc_copper_len,
        dc_copper_unit_yuan=dc_copper_unit,
        dc_aluminum_len_m=dc_aluminum_len,
        dc_aluminum_unit_yuan=dc_aluminum_unit,
    )
    chg_total, _ = compute_charger_engineering_capex(ce)
    _html_construction_section_total(chg_total, "充电桩工程")
    return ce


@dataclass
class StorageEngineeringInputs:
    """储能工程 CAPEX 分项（单位：元）。"""

    rs485_len_m: float
    rs485_unit_yuan: float
    rs485_install_yuan: float
    anti_meter_qty: int
    anti_meter_unit_yuan: float
    anti_meter_install_yuan: float
    cable_yjlhv120_len_m: float
    cable_yjlhv120_unit_yuan: float
    cable_head_qty: int
    cable_head_unit_yuan: float
    pipe100_len_m: float
    pipe100_unit_yuan: float
    grounding_flat_qty: int
    grounding_flat_unit_yuan: float
    grounding_rod_qty: int
    grounding_rod_unit_yuan: float
    cabinet_base_qty: int
    cabinet_base_unit_yuan: float
    system_debug_yuan: float
    other_install_yuan: float


def _storage_eng_defaults_merged(cfg: dict[str, Any], farthest_m: float) -> dict[str, Any]:
    out = {**STORAGE_ENG_BUILTIN_DEFAULTS}
    for k, v in cfg.items():
        if k.startswith("sto_eng_"):
            out[k] = v
    route = _default_route_len_m(farthest_m, cfg)
    out["sto_eng_route_len_m"] = route
    for lk in STORAGE_ENG_LEN_KEYS:
        if float(cfg.get(lk, 0) or 0) > 0:
            out[lk] = float(cfg[lk])
        else:
            out[lk] = route
    return out


def compute_storage_engineering_capex(
    se: StorageEngineeringInputs, *, include: bool
) -> tuple[float, dict[str, float]]:
    if not include:
        return 0.0, {}
    bd: dict[str, float] = {}
    bd["RS485通讯线"] = (
        max(0.0, se.rs485_len_m) * max(0.0, se.rs485_unit_yuan) + max(0.0, se.rs485_install_yuan)
    )
    bd["防过载防逆流电表"] = (
        max(0, se.anti_meter_qty) * max(0.0, se.anti_meter_unit_yuan)
        + max(0.0, se.anti_meter_install_yuan)
    )
    bd["电缆·YJLHV22 3×120+1×70"] = (
        max(0.0, se.cable_yjlhv120_len_m) * max(0.0, se.cable_yjlhv120_unit_yuan)
    )
    bd["低压电缆头"] = max(0, se.cable_head_qty) * max(0.0, se.cable_head_unit_yuan)
    bd["热镀锌钢管φ100"] = max(0.0, se.pipe100_len_m) * max(0.0, se.pipe100_unit_yuan)
    bd["接地扁铁"] = max(0, se.grounding_flat_qty) * max(0.0, se.grounding_flat_unit_yuan)
    bd["接地极(热镀锌角钢)"] = max(0, se.grounding_rod_qty) * max(0.0, se.grounding_rod_unit_yuan)
    bd["储能柜基础"] = max(0, se.cabinet_base_qty) * max(0.0, se.cabinet_base_unit_yuan)
    bd["整系统调试"] = max(0.0, se.system_debug_yuan)
    bd["其他建安费"] = max(0.0, se.other_install_yuan)
    total = sum(bd.values())
    return total, {k: v for k, v in bd.items() if v > 1e-6}


def render_storage_engineering_inputs(
    d: dict[str, Any], farthest_m: float, *, storage_installed: bool
) -> StorageEngineeringInputs:
    """第四部分 · 储能工程。"""
    cfg = _storage_eng_defaults_merged(d, farthest_m)
    route = float(cfg["sto_eng_route_len_m"])
    _html_construction_section_divider()
    _html_construction_subsection_title("储能工程")
    if not storage_installed:
        _html_note_panel(STORAGE_ENG_CAPEX_EXCLUDED_NOTE)
    else:
        st.caption(
            f"RS485/电缆/钢管长度默认 = 最远距离 × {ROUTE_LEN_FACTOR} = **{route:.1f} m**"
            f"（第一步 {farthest_m:.0f} m），可修改。"
        )

    r1, r2, r3 = st.columns(3)
    with r1:
        rs485_len = float(
            st.number_input(
                "RS485通讯线 长度(m)",
                min_value=0.0,
                value=float(cfg["sto_eng_rs485_len_m"]),
                step=1.0,
                key="sto_eng_rs485_len_m",
            )
        )
        rs485_unit = float(
            st.number_input(
                "RS485 单价(元/m)",
                min_value=0.0,
                value=float(cfg["sto_eng_rs485_unit_yuan"]),
                step=0.1,
                key="sto_eng_rs485_unit_yuan",
            )
        )
    with r2:
        rs485_install = float(
            st.number_input(
                "RS485 建安费(元)",
                min_value=0.0,
                value=float(cfg["sto_eng_rs485_install_yuan"]),
                step=10.0,
                key="sto_eng_rs485_install_yuan",
            )
        )
        anti_qty = int(
            st.number_input(
                "防过载防逆流电表 数量(块)",
                min_value=0,
                value=int(cfg["sto_eng_anti_meter_qty"]),
                step=1,
                help="400V 并网用。",
                key="sto_eng_anti_meter_qty",
            )
        )
    with r3:
        anti_unit = float(
            st.number_input(
                "防逆流表单价(元/块)",
                min_value=0.0,
                value=float(cfg["sto_eng_anti_meter_unit_yuan"]),
                step=50.0,
                key="sto_eng_anti_meter_unit_yuan",
            )
        )
        anti_install = float(
            st.number_input(
                "防逆流表建安费(元)",
                min_value=0.0,
                value=float(cfg["sto_eng_anti_meter_install_yuan"]),
                step=50.0,
                key="sto_eng_anti_meter_install_yuan",
            )
        )

    c1, c2 = st.columns(2)
    with c1:
        cable_len = float(
            st.number_input(
                "ZC-YJLHV22 3×120+1×70 长度(m)",
                min_value=0.0,
                value=float(cfg["sto_eng_cable_yjlhv120_len_m"]),
                step=1.0,
                key="sto_eng_cable_yjlhv120_len_m",
            )
        )
        cable_unit = float(
            st.number_input(
                "上述电缆单价(元/m)",
                min_value=0.0,
                value=float(cfg["sto_eng_cable_yjlhv120_unit_yuan"]),
                step=1.0,
                key="sto_eng_cable_yjlhv120_unit_yuan",
            )
        )
    with c2:
        head_qty = int(
            st.number_input(
                "低压电缆头 数量(个)",
                min_value=0,
                value=int(cfg["sto_eng_cable_head_qty"]),
                step=1,
                key="sto_eng_cable_head_qty",
            )
        )
        head_unit = float(
            st.number_input(
                "电缆头单价(元/个)",
                min_value=0.0,
                value=float(cfg["sto_eng_cable_head_unit_yuan"]),
                step=10.0,
                key="sto_eng_cable_head_unit_yuan",
            )
        )

    p1, p2 = st.columns(2)
    with p1:
        pipe_len = float(
            st.number_input(
                "热镀锌钢管 φ100 长度(m)",
                min_value=0.0,
                value=float(cfg["sto_eng_pipe100_len_m"]),
                step=1.0,
                key="sto_eng_pipe100_len_m",
            )
        )
        pipe_unit = float(
            st.number_input(
                "φ100 单价(元/m)",
                min_value=0.0,
                value=float(cfg["sto_eng_pipe100_unit_yuan"]),
                step=1.0,
                key="sto_eng_pipe100_unit_yuan",
            )
        )
    with p2:
        flat_qty = int(
            st.number_input(
                "接地扁铁 数量(根/条)",
                min_value=0,
                value=int(cfg["sto_eng_grounding_flat_qty"]),
                step=1,
                key="sto_eng_grounding_flat_qty",
            )
        )
        flat_unit = float(
            st.number_input(
                "接地扁铁单价(元)",
                min_value=0.0,
                value=float(cfg["sto_eng_grounding_flat_unit_yuan"]),
                step=1.0,
                key="sto_eng_grounding_flat_unit_yuan",
            )
        )

    g1, g2, g3, g4 = st.columns(4)
    with g1:
        rod_qty = int(
            st.number_input(
                "接地极 数量(根)",
                min_value=0,
                value=int(cfg["sto_eng_grounding_rod_qty"]),
                step=1,
                help="热镀锌角钢 50×5 L=2500mm，镀锌层≥65μm。",
                key="sto_eng_grounding_rod_qty",
            )
        )
    with g2:
        rod_unit = float(
            st.number_input(
                "接地极单价(元/根)",
                min_value=0.0,
                value=float(cfg["sto_eng_grounding_rod_unit_yuan"]),
                step=1.0,
                key="sto_eng_grounding_rod_unit_yuan",
            )
        )
    with g3:
        base_qty = int(
            st.number_input(
                "储能柜基础 数量(处)",
                min_value=0,
                value=int(cfg["sto_eng_cabinet_base_qty"]),
                step=1,
                key="sto_eng_cabinet_base_qty",
            )
        )
    with g4:
        base_unit = float(
            st.number_input(
                "储能柜基础单价(元/处)",
                min_value=0.0,
                value=float(cfg["sto_eng_cabinet_base_unit_yuan"]),
                step=100.0,
                key="sto_eng_cabinet_base_unit_yuan",
            )
        )

    f1, f2 = st.columns(2)
    with f1:
        system_debug = float(
            st.number_input(
                "整系统调试(元)",
                min_value=0.0,
                value=float(cfg["sto_eng_system_debug_yuan"]),
                step=100.0,
                key="sto_eng_system_debug_yuan",
            )
        )
    with f2:
        other_install = float(
            st.number_input(
                "其他建安费(元)",
                min_value=0.0,
                value=float(cfg["sto_eng_other_install_yuan"]),
                step=100.0,
                key="sto_eng_other_install_yuan",
            )
        )

    se = StorageEngineeringInputs(
        rs485_len_m=rs485_len,
        rs485_unit_yuan=rs485_unit,
        rs485_install_yuan=rs485_install,
        anti_meter_qty=anti_qty,
        anti_meter_unit_yuan=anti_unit,
        anti_meter_install_yuan=anti_install,
        cable_yjlhv120_len_m=cable_len,
        cable_yjlhv120_unit_yuan=cable_unit,
        cable_head_qty=head_qty,
        cable_head_unit_yuan=head_unit,
        pipe100_len_m=pipe_len,
        pipe100_unit_yuan=pipe_unit,
        grounding_flat_qty=flat_qty,
        grounding_flat_unit_yuan=flat_unit,
        grounding_rod_qty=rod_qty,
        grounding_rod_unit_yuan=rod_unit,
        cabinet_base_qty=base_qty,
        cabinet_base_unit_yuan=base_unit,
        system_debug_yuan=system_debug,
        other_install_yuan=other_install,
    )
    sto_total, _ = compute_storage_engineering_capex(se, include=storage_installed)
    _html_construction_section_total(sto_total, "储能工程")
    return se


# 充电桩额定功率（kW/台或套），用于装机功率合计与电网申报提示
PILE_RATED_KW_60 = 60.0
PILE_RATED_KW_120 = 120.0
PILE_RATED_KW_ULTRA_PER_SET = 480.0
# 单站充电设施装机超过该值需向电网公司申报（业务提醒阈值）
GRID_DECLARATION_THRESHOLD_KW = 160.0

# 第二步充电桩配置方式
CHARGER_CONFIG_MODE_A = "A：按剩余容量自动规划"
CHARGER_CONFIG_MODE_B = "B：自定义桩和储能数量"
CHARGER_CONFIG_MODES = [CHARGER_CONFIG_MODE_A, CHARGER_CONFIG_MODE_B]
# 工商业储能单套规格（B 模式固定展示）
STORAGE_KWH_PER_SET_DEFAULT = 262.0
STORAGE_KW_PER_SET_DEFAULT = 125.0
# 配电需求系数默认值（桩实际输出/额定，常见 0.4–0.85）
DEMAND_COEFF_KX_DEFAULT = 0.65
DEMAND_COEFF_KX_MIN = 0.4
DEMAND_COEFF_KX_MAX = 0.85
# 同时使用率 Kt（同一时间使用的充电桩比例）
KT_LEVEL_FULL = "1 (满载同时)"
KT_LEVEL_HIGH = "高 (0.7–0.9)"
KT_LEVEL_MID = "中间值 (0.7)"
KT_LEVEL_LOW = "低 (0.6–0.7)"
KT_LEVEL_OPTIONS = [KT_LEVEL_FULL, KT_LEVEL_HIGH, KT_LEVEL_MID, KT_LEVEL_LOW]
SIMULTANEITY_KT_DEFAULT = 0.7
# 480kW 液冷 1 套占用车位数（业务约定）
ULTRA_PARKING_SPOTS_PER_SET = 4
# 规划用功率因数 cosΦ（补偿后 0.9–0.95）
PLAN_COS_PHI_DEFAULT = 0.92
PLAN_COS_PHI_MIN = 0.9
PLAN_COS_PHI_MAX = 0.95
# 负载率 η
PLAN_LOAD_ETA_DEFAULT = 0.75
PLAN_LOAD_ETA_MIN = 0.7
PLAN_LOAD_ETA_MAX = 0.8
CHARGER_PLAN_FORMULA_MD = r"""
**变压器容量校验（kVA）**

\[
\text{变压器容量 (kVA)} = \frac{P_{\text{桩总}}(\text{kW}) \times K_x \times K_t}{\cos\varphi \times \eta}
\]

- **\(K_x\)**：需要系数，充电桩实际输出功率与额定功率之比，常取 **0.4–0.85**
- **\(K_t\)**：同时使用率（同一时间使用的充电桩比例，**取值 ≤1**）：**1 满载同时**，**高 0.7–0.9**，**低 0.6–0.7**，**中间值 0.7**
- **Kt < 1** 时建议配置**微网控制器**（见第四部分）做不超需监测；**Kt = 1** 则无需
- **\(\cos\varphi\)**：功率因数（补偿后），常取 **0.9–0.95**
- **\(\eta\)**：负载率，推荐 **0.7–0.8**

由剩余可用容量反推允许的最大桩总功率：
\(P_{\max} = \dfrac{\text{剩余可用(kVA)} \times \cos\varphi \times \eta}{K_x \times K_t}\)
"""

USAGE_STRATEGY_LOGIC_MD = """**三种储能用途——储能与充电桩「各算各账」还是「储能给桩供电」**

| 模式 | 储能主要作用 | 本工具年化收益（元，简化静态） |
|------|--------------|-------------------------------|
| **纯峰谷套利** | 储能只对电网低买高卖；桩电另从电网取 | 储能套利 + 充电服务费 + VPP − 运维（**不扣**桩侧购电，默认电费由用户承担） |
| **峰谷套利+充电** | 谷充峰放，放电优先供桩，不够再购峰电 | 日毛利×天数 + 余电套利 + VPP − 运维 |
| **纯动态增容+充电** | 削峰顶变压器，不主打价差循环 | 节省基本/需量电费 + 可选避开的变压器投资年化 + 充电服务费 + VPP − 运维 |

**1. 纯峰谷套利**（储充独立）  
- 储能年收益 = `装机(kWh) × 日循环 × (峰电价−谷电价) × 效率 × 运营天数`  
- 充电服务费 = `日总充电量 × 服务费 × 运营天数`（不扣网购电）

**2. 峰谷套利+充电**（储能给桩供电；**假设充电在峰时段**）  
- 日总充电量 = 各枪日充电量之和；储能日可放 `E₀=装机×日循环`；供桩 `E=min(E₀,日总充电量)`  
- **日毛利** = 日充电×服务费 + 日充电×峰电价 − (日充电−E)×峰电价 − (E÷η)×谷电价  
  = 日充电×服务费 + **E×(峰电价−谷电价÷η)**（储能供桩的价差收益 + 服务费，非「只减成本」）  
- 若 `E₀>日总充电量`，余电按模式1价差公式加计入「余电上网套利」

**3. 纯动态增容+充电**（削峰省基本电费）  
- **配电**：沿用表单「现有/可用变压器容量」，**不计**变压器新建/增容 CAPEX。  
- 节省基本电费 = `削减峰值(kW) × 需量/基本电价(元/kW·月) × 12`（或手填万元/年覆盖）  
- 可选：避开的一次性变压器投资 ÷ 折旧年限；充电服务费同模式1（储能不承担主能量供给）
"""


def _slot_desc_from_vehicle(vehicle: str) -> str:
    return _SLOT_DESC_BY_VEHICLE.get(str(vehicle).strip(), str(vehicle).strip())


def _rated_kva_from_select_label(label: str) -> float:
    """将下拉选项转为 kVA 数值；「（不填）」为 0。"""
    if label == TRANSFORMER_RATED_SELECT_LABELS[0]:
        return 0.0
    try:
        return float(str(label).replace("kVA", "").strip())
    except ValueError:
        return 0.0


def _rated_kva_select_index(kva: float) -> int:
    """根据已存 kVA 定位下拉索引。"""
    v = int(round(float(kva)))
    if v <= 0:
        return 0
    labels = [f"{k} kVA" for k in TRANSFORMER_RATED_KVA_OPTIONS]
    try:
        return 1 + labels.index(f"{v} kVA")
    except ValueError:
        return 0


def _transformer_baseline_kva(kva_existing: float, kva_remaining: float) -> float:
    """增容 ΔkVA 与目标容量校验的基准：有额定用额定，否则用剩余可用。"""
    rated = float(kva_existing)
    if rated > 0:
        return rated
    return max(0.0, float(kva_remaining))


def _original_transformer_kva(kva_existing: float, kva_remaining: float) -> float:
    """第一步「原变压器」容量口径（与 baseline 一致：额定优先，未填则用剩余可用）。"""
    return _transformer_baseline_kva(kva_existing, kva_remaining)


def usable_station_capacity_kw(kva_remaining: float, power_factor: float) -> float:
    """储充场站可用容量 ≈ 剩余可用变压器容量(kVA) × 功率因数 → kW 量级。"""
    return max(0.0, float(kva_remaining)) * float(np.clip(power_factor, 0.0, 1.0))


def required_transformer_kva(
    pile_kw_total: float,
    kx: float,
    kt: float,
    cos_phi: float,
    eta: float,
) -> float:
    """按配电公式计算所需变压器容量 (kVA)。"""
    denom = max(float(cos_phi) * float(eta), 1e-9)
    return max(0.0, float(pile_kw_total)) * max(0.0, float(kx)) * max(0.0, float(kt)) / denom


def max_pile_kw_from_remaining_kva(
    kva_remaining: float,
    kx: float,
    kt: float,
    cos_phi: float,
    eta: float,
) -> float:
    """由剩余可用 kVA 反推允许的最大充电桩额定功率合计 (kW)。"""
    factor = max(float(kx) * float(kt), 1e-9)
    return max(0.0, float(kva_remaining)) * max(0.0, float(cos_phi)) * max(0.0, float(eta)) / factor


def kt_from_level(level: str) -> float:
    """同时使用率档位 → 规划用 Kt 数值。"""
    s = str(level).strip()
    if s.startswith("1"):
        return 1.0
    if s.startswith("高"):
        return 0.8
    if s.startswith("低"):
        return 0.65
    return 0.7


def kt_level_from_coeff(kt: float) -> str:
    """已存 Kt 数值 → 下拉档位（用于 session 初值）。"""
    v = float(kt)
    if v >= 1.0 - 1e-6:
        return KT_LEVEL_FULL
    if v >= 0.75:
        return KT_LEVEL_HIGH
    if v <= 0.66:
        return KT_LEVEL_LOW
    return KT_LEVEL_MID


def compute_transformer_upgrade_capex(
    inp: InputModel | Any,
    *,
    n_pile_60: int | None = None,
    n_pile_120: int | None = None,
    n_ultra_480: int | None = None,
) -> dict[str, Any]:
    """
    变压器增容投资（元），与 compute_projection 同口径。
    计入第四部分「相关物料」；动态增容足够时 ΔkVA=0。
    作者：jiali.qiu
    """
    n60 = max(0, int(n_pile_60 if n_pile_60 is not None else inp.n_pile_60))
    n120 = max(0, int(n_pile_120 if n_pile_120 is not None else inp.n_pile_120))
    n480 = max(0, int(n_ultra_480 if n_ultra_480 is not None else inp.n_ultra_480))
    k_rated = float(inp.kva_existing)
    k_remain = float(inp.kva_remaining_available)
    pf = float(inp.power_factor)
    baseline_kva = _transformer_baseline_kva(k_rated, k_remain)
    k_target_inp = float(inp.kva_target)
    pu = max(0.0, float(inp.transformer_unit_price))
    usage = str(inp.usage)
    install_storage = str(inp.storage_installed) != STORAGE_INSTALL_NO

    pile_req_kva = required_transformer_kva(
        total_installed_charger_kw(n60, n120, n480),
        float(inp.demand_coeff_kx),
        float(inp.simultaneity_coeff_kt),
        float(inp.planning_cos_phi),
        float(inp.planning_load_rate_eta),
    )
    gap_kva = charger_capacity_gap_kva(pile_req_kva, k_remain)
    dynamic_insufficient = False
    if install_storage and usage == USAGE_DYNAMIC_CAPACITY:
        dynamic_insufficient = bool(
            assess_dynamic_storage_coverage(
                peak_reduction_kw=float(inp.peak_reduction_kw),
                storage_sets=int(inp.storage_sets),
                req_kva=pile_req_kva,
                kva_remaining=k_remain,
                power_factor=pf,
            )["insufficient"]
        )

    if usage == USAGE_DYNAMIC_CAPACITY and not dynamic_insufficient:
        return {
            "capex_yuan": 0.0,
            "delta_kva": 0.0,
            "baseline_kva": baseline_kva,
            "k_target_eff": baseline_kva,
            "gap_kva": gap_kva,
            "dynamic_insufficient": False,
            "label": "变压器增容(动态增容已覆盖)",
            "detail_key": "",
        }

    k_target_eff = float(k_target_inp)
    delta_kva = max(0.0, k_target_eff - baseline_kva)
    capex_yuan = delta_kva * pu
    if usage == USAGE_DYNAMIC_CAPACITY and dynamic_insufficient:
        label = "变压器增容(动态不足补足)"
        detail_key = "相关物料·变压器增容(动态不足补足)"
    else:
        label = "变压器增容(ΔkVA)"
        detail_key = "相关物料·变压器增容(ΔkVA)"
    return {
        "capex_yuan": capex_yuan,
        "delta_kva": delta_kva,
        "baseline_kva": baseline_kva,
        "k_target_eff": k_target_eff,
        "gap_kva": gap_kva,
        "dynamic_insufficient": dynamic_insufficient,
        "label": label,
        "detail_key": detail_key,
    }


def charger_parking_spots_used(n60: int, n120: int, n480: int) -> int:
    """充电桩占用车位数（480kW 每套占 4 位，120kW 每台 2 位）。"""
    return (ULTRA_PARKING_SPOTS_PER_SET * max(0, n480)) + spots_used_by_60_120(n60, n120)


def recommend_transformer_rated_kva(required_total_kva: float) -> int | None:
    """按标准档位向上推荐变压器额定容量。"""
    need = max(0.0, float(required_total_kva))
    if need <= 0:
        return None
    for k in TRANSFORMER_RATED_KVA_OPTIONS:
        if k >= need - 1e-6:
            return int(k)
    return int(TRANSFORMER_RATED_KVA_OPTIONS[-1])


def charger_capacity_gap_kva(req_kva: float, kva_remaining: float) -> float:
    """相对第一步「剩余可用」的容量缺口 (kVA)，用于提示需增容规模。"""
    return max(0.0, float(req_kva) - float(kva_remaining))


def transformer_target_covers_capacity_gap(
    k_target: float,
    baseline_kva: float,
    gap_kva: float,
) -> bool:
    """目标总容量是否达到「基准 + 相对剩余可用缺口」的下限（用于动态增容改走变压器补足时）。"""
    return float(k_target) >= float(baseline_kva) + max(0.0, float(gap_kva)) - 1e-3


def suggested_transformer_upgrade(
    baseline_kva: float,
    expansion_kva: float,
) -> tuple[float, int | None, float]:
    """
    在基准容量（额定或剩余可用）上增加缺口，向上匹配标准档位。
    返回 (建议目标总容量 kVA, 标准档位 kVA 或 None, 缺口 kVA)。
    """
    baseline = max(0.0, float(baseline_kva))
    expansion = max(0.0, float(expansion_kva))
    suggested_total = baseline + expansion
    rec = recommend_transformer_rated_kva(suggested_total)
    target = float(rec) if rec is not None else suggested_total
    return target, rec, expansion


def render_transformer_investment_note(
    *,
    usage: str,
    upgrade_mode: str,
    baseline_kva: float,
    k_target: float,
    pu: float,
    req_plan_kva: float,
    k_remain: float,
    pf: float,
    install_storage: bool,
    peak_reduction_kw: float,
    storage_sets: int,
) -> None:
    """第三部分后：增容投资粗算（计入总投资，与 compute_projection 同口径）。"""
    gap_kva = charger_capacity_gap_kva(req_plan_kva, k_remain)
    dyn_insuf = False
    if install_storage and str(usage) == USAGE_DYNAMIC_CAPACITY:
        dyn_insuf = assess_dynamic_storage_coverage(
            peak_reduction_kw=peak_reduction_kw,
            storage_sets=storage_sets,
            req_kva=req_plan_kva,
            kva_remaining=k_remain,
            power_factor=pf,
        )["insufficient"]

    if str(usage) == USAGE_DYNAMIC_CAPACITY and not dyn_insuf:
        st.success(
            "纯动态增容模式且储能削峰/功率满足桩需：**不计**变压器增容 CAPEX（ΔkVA=0）。"
        )
        return

    k_use = float(k_target)
    sug, rec, _ = suggested_transformer_upgrade(baseline_kva, gap_kva)
    if dyn_insuf:
        tf_ok = transformer_target_covers_capacity_gap(k_use, baseline_kva, gap_kva)
        if tf_ok:
            st.warning(
                f"储能动态增容（**{peak_reduction_kw:.0f} kW** / 装机 **"
                f"{storage_sets * STORAGE_KW_PER_SET_DEFAULT:.0f} kW**）不足以覆盖缺口约 "
                f"**{gap_kva * pf:.0f} kW**；已按您填写的目标总容量 **{k_use:.0f} kVA** 用变压器增容补足。"
            )
        else:
            st.error(
                f"**容量不足**：储能动态增容不足以覆盖缺口约 **{gap_kva * pf:.0f} kW**（{gap_kva:.1f} kVA×PF），"
                f"且目标总容量 **{k_use:.0f} kVA** 低于建议下限 **{sug:.0f} kVA**"
                + (f"（标准档位 **{rec} kVA**）" if rec else "")
                + "。请提高目标容量或削峰/储能功率。"
            )
    elif gap_kva > 1e-3 and k_use < sug - 1e-3:
        st.warning(
            f"相对剩余可用存在缺口 **{gap_kva:.1f} kVA**；当前目标 **{k_use:.0f} kVA** 低于建议 **{sug:.0f} kVA**"
            + (f"（档位 **{rec} kVA**）" if rec else "")
            + "。"
        )

    delta = max(0.0, k_use - baseline_kva)
    if delta <= 1e-6:
        if gap_kva > 1e-3 and upgrade_mode == "不增容":
            st.warning("存在容量缺口，但第一步为「不增容」且目标容量未高于基准，增容 CAPEX 为 0。")
        return

    st.markdown("**变压器增容投资（计入总投资）**")
    st.caption(
        f"ΔkVA = 目标 **{k_use:.0f}** − 基准 **{baseline_kva:.0f}** = **{delta:.1f} kVA** · "
        f"单价 **{pu:.0f} 元/kVA** → 粗算 **{delta * pu / 1e4:.2f} 万元**（计入 **第四部分·相关物料**）"
    )


def assess_dynamic_storage_coverage(
    *,
    peak_reduction_kw: float,
    storage_sets: int,
    req_kva: float,
    kva_remaining: float,
    power_factor: float,
) -> dict[str, Any]:
    """
    校验储能动态增容是否覆盖充电桩相对「剩余可用」的容量缺口。
    需同时满足：削峰申报值 ≥ 缺口 kW，且储能装机功率 ≥ 缺口 kW。
    """
    gap_kva = charger_capacity_gap_kva(req_kva, kva_remaining)
    gap_kw = gap_kva * max(0.0, float(power_factor))
    storage_kw = max(0, int(storage_sets)) * STORAGE_KW_PER_SET_DEFAULT
    shave_kw = max(0.0, float(peak_reduction_kw))
    if gap_kw <= 1e-3:
        return {
            "gap_kva": gap_kva,
            "gap_kw": gap_kw,
            "storage_kw": storage_kw,
            "shave_kw": shave_kw,
            "covers": True,
            "insufficient": False,
        }
    covers = shave_kw >= gap_kw - 1e-3 and storage_kw >= gap_kw - 1e-3
    return {
        "gap_kva": gap_kva,
        "gap_kw": gap_kw,
        "storage_kw": storage_kw,
        "shave_kw": shave_kw,
        "covers": covers,
        "insufficient": not covers,
    }


# ----------------------------
# 模式三适用性测算（选型参考，不参与确认测算主公式）
# 作者: jiali.qiu
# ----------------------------
MODE3_ADVICE_NO_NEED = "无需模式三（不推荐）"
MODE3_ADVICE_NOT_SUITABLE = "模式三不适用：建议增容或限制负荷"
MODE3_ADVICE_TF_UPGRADE = "推荐选择变压器增容"
MODE3_ADVICE_HYBRID = "建议改为混合策略（峰谷套利+充电，并叠加动态增容）"
MODE3_ADVICE_PURE_M3 = "推荐纯模式三（只削峰，不套利）"

MODE3_BILLING_KVA = "按容量(元/kVA·月)"
MODE3_BILLING_KW = "按需量(元/kW·月)"
MODE3_BILLING_OPTIONS = [MODE3_BILLING_KVA, MODE3_BILLING_KW]
MODE3_GRID_UPGRADE_OPTIONS = ["待确认", "不允许", "允许"]
MODE3_PEAK_HOURS_MAX_DEFAULT = 2.0
MODE3_SPREAD_HYBRID_YUAN_DEFAULT = 0.7


@dataclass(frozen=True)
class Mode3AdvisoryInput:
    """模式三适用性测算输入（选型参考）。"""

    transformer_rated_kva: float
    power_factor: float
    peak_load_pct: float
    peak_demand_kw: float
    declared_demand_kw: float
    overload_hours_per_day: float
    billing_mode: str
    basic_yuan_per_kw_month: float
    basic_yuan_per_kva_month: float
    grid_upgrade_allowed: str
    upgrade_cost_wan: float
    storage_capex_wan_est: float
    peak_price: float
    valley_price: float
    estimated_shave_kw: float
    demand_penalty_multiplier: float | None = None
    overload_peak_fixed: bool | None = None
    upgrade_cycle_note: str | None = None
    storage_site_ok: bool | None = None


@dataclass(frozen=True)
class Mode3AdvisoryResult:
    """模式三适用性测算输出。"""

    recommendation: str
    summary: str
    decision_trace: tuple[str, ...]
    suggested_usage: str | None
    payback_storage_years: float | None
    payback_upgrade_years: float | None
    pending_optional: tuple[str, ...]


def _mode3_peak_kw(inp: Mode3AdvisoryInput) -> tuple[float, float]:
    """由额定容量×负载率或直接填写峰值功率，得到峰值 kW / kVA。"""
    pf = max(0.01, float(inp.power_factor))
    rated_kva = max(0.0, float(inp.transformer_rated_kva))
    if float(inp.peak_demand_kw) > 1e-3:
        peak_kw = float(inp.peak_demand_kw)
    else:
        rated_kw = rated_kva * pf
        peak_kw = rated_kw * max(0.0, float(inp.peak_load_pct)) / 100.0
    peak_kva = peak_kw / pf
    return peak_kw, peak_kva


def assess_mode3_suitability(
    inp: Mode3AdvisoryInput,
    *,
    peak_hours_max: float = MODE3_PEAK_HOURS_MAX_DEFAULT,
    spread_hybrid_yuan: float = MODE3_SPREAD_HYBRID_YUAN_DEFAULT,
) -> Mode3AdvisoryResult:
    """
    按业务决策树粗判模式三适用性（可研级，非调度仿真）。
    作者: jiali.qiu
    """
    peak_hours_max = max(0.5, float(peak_hours_max))
    spread_hybrid_yuan = max(0.0, float(spread_hybrid_yuan))
    trace: list[str] = []
    pending: list[str] = []
    peak_kw, peak_kva = _mode3_peak_kw(inp)
    rated_kva = max(0.0, float(inp.transformer_rated_kva))
    pf = max(0.01, float(inp.power_factor))
    rated_kw = rated_kva * pf

    over_rated = rated_kva > 1e-3 and peak_kva > rated_kva + 1e-3
    over_declared = False
    if str(inp.billing_mode) == MODE3_BILLING_KW and float(inp.declared_demand_kw) > 1e-3:
        over_declared = peak_kw > float(inp.declared_demand_kw) + 1e-3
    has_peak_issue = over_rated or over_declared

    trace.append(
        f"① 峰值粗算：约 **{peak_kw:.0f} kW**（{peak_kva:.1f} kVA，PF={pf:.2f}）"
        f" · 额定 **{rated_kva:.0f} kVA**（≈{rated_kw:.0f} kW）"
    )
    if str(inp.billing_mode) == MODE3_BILLING_KW:
        trace.append(
            f"   申报需量 **{inp.declared_demand_kw:.0f} kW**"
            + (" · **超过申报需量**" if over_declared else " · 未超申报需量")
        )
    if over_rated:
        trace.append("   **超过变压器额定容量（或负载率>100%）**")
    if not has_peak_issue:
        trace.append("② 结论：未同时出现「超容量/超需量」→ **无需模式三**")
        return Mode3AdvisoryResult(
            recommendation=MODE3_ADVICE_NO_NEED,
            summary="当前填写的峰值未超过变压器额定或申报需量，削峰/动态增容诉求不强。",
            decision_trace=tuple(trace),
            suggested_usage=USAGE_HYBRID,
            payback_storage_years=None,
            payback_upgrade_years=None,
            pending_optional=tuple(pending),
        )

    trace.append("② **存在尖峰/超需量问题** → 进入模式三适用性判断")
    hours = max(0.0, float(inp.overload_hours_per_day))
    trace.append(
        f"③ 超载持续时间：**{hours:.1f} 小时/天**（阈值 ≤ {peak_hours_max:.2f} 小时/天）"
    )
    if hours > peak_hours_max + 1e-6:
        trace.append("   长时过载 → **模式三不适用**（储能难以经济覆盖长时超额用电）")
        return Mode3AdvisoryResult(
            recommendation=MODE3_ADVICE_NOT_SUITABLE,
            summary=(
                f"尖峰持续时间超过约 {peak_hours_max:.1f} 小时/天，更适合变压器增容、限制同时充电负荷，"
                "或调整运营策略，而非仅靠短时削峰。"
            ),
            decision_trace=tuple(trace),
            suggested_usage=USAGE_PEAK_VALLEY_ONLY,
            payback_storage_years=None,
            payback_upgrade_years=None,
            pending_optional=tuple(pending),
        )

    shave_kw = max(0.0, float(inp.estimated_shave_kw))
    if shave_kw < 1e-3 and over_rated and rated_kw > 1e-3:
        shave_kw = max(0.0, peak_kw - rated_kw)
    if shave_kw < 1e-3 and over_declared:
        shave_kw = max(shave_kw, peak_kw - float(inp.declared_demand_kw))

    if str(inp.billing_mode) == MODE3_BILLING_KVA:
        basic_month = max(0.0, float(inp.basic_yuan_per_kva_month))
        if basic_month > 1e-6 and rated_kva > 1e-3:
            excess_kva = max(0.0, peak_kva - rated_kva)
            annual_save = excess_kva * basic_month * 12.0
        else:
            annual_save = shave_kw * max(0.0, float(inp.basic_yuan_per_kw_month)) * 12.0
            pending.append("基本电费按容量计费时，请确认「元/kVA·月」单价是否已填写")
    else:
        annual_save = shave_kw * max(0.0, float(inp.basic_yuan_per_kw_month)) * 12.0
        if inp.demand_penalty_multiplier is None:
            pending.append("需量惩罚倍数（超申报加收，常见 2–3 倍）— 待确认")

    storage_capex = max(0.0, float(inp.storage_capex_wan_est)) * 1e4
    upgrade_capex = max(0.0, float(inp.upgrade_cost_wan)) * 1e4
    spb: float | None = None
    upb: float | None = None
    if annual_save > 1e-3 and storage_capex > 1e-3:
        spb = storage_capex / annual_save
    if annual_save > 1e-3 and upgrade_capex > 1e-3:
        upb = upgrade_capex / annual_save

    grid_ok = str(inp.grid_upgrade_allowed)
    trace.append(f"④ 电网增容可行性：**{grid_ok}**")
    if grid_ok == "待确认":
        pending.append("电网是否允许增容 — 待联系供电公司确认")
    if upgrade_capex < 1e-3:
        pending.append("增容预估费用（万元）— 待估算后用于回收期对比")
    if storage_capex < 1e-3:
        pending.append("储能投资粗算（万元）— 待填写或由上节套数×单价推算")
    if inp.overload_peak_fixed is None:
        pending.append("超载发生时段是否固定 — 待确认（影响充放编排）")
    if inp.storage_site_ok is None:
        pending.append("储能可用安装面积/消防并网 — 待现场确认")

    if grid_ok == "不允许":
        trace.append("   增容不可行 → **倾向推荐模式三路径**")
        branch = "mode3"
        if spb is not None or upb is not None:
            _append_mode3_payback_calc_trace(
                trace,
                inp=inp,
                annual_save=annual_save,
                shave_kw=shave_kw,
                storage_capex=storage_capex,
                upgrade_capex=upgrade_capex,
                spb=spb,
                upb=upb,
                peak_kva=peak_kva,
                rated_kva=rated_kva,
            )
    elif grid_ok == "待确认" and upgrade_capex < 1e-3:
        trace.append("   增容可行性待确认且缺增容费用 → 暂按 **模式三路径**（请补全选填项后重算）")
        branch = "mode3"
        if spb is not None or upb is not None:
            _append_mode3_payback_calc_trace(
                trace,
                inp=inp,
                annual_save=annual_save,
                shave_kw=shave_kw,
                storage_capex=storage_capex,
                upgrade_capex=upgrade_capex,
                spb=spb,
                upb=upb,
                peak_kva=peak_kva,
                rated_kva=rated_kva,
            )
    elif spb is not None and upb is not None:
        _append_mode3_payback_calc_trace(
            trace,
            inp=inp,
            annual_save=annual_save,
            shave_kw=shave_kw,
            storage_capex=storage_capex,
            upgrade_capex=upgrade_capex,
            spb=spb,
            upb=upb,
            peak_kva=peak_kva,
            rated_kva=rated_kva,
        )
        if upb < spb - 1e-6:
            trace.append("   增容回收更短 → **推荐变压器增容**")
            return Mode3AdvisoryResult(
                recommendation=MODE3_ADVICE_TF_UPGRADE,
                summary=(
                    f"在可同时落地的前提下，增容一次性投资回收期（约 {upb:.1f} 年）"
                    f"短于储能方案（约 {spb:.1f} 年），优先考虑变压器增容。"
                ),
                decision_trace=tuple(trace),
                suggested_usage=USAGE_PEAK_VALLEY_ONLY,
                payback_storage_years=spb,
                payback_upgrade_years=upb,
                pending_optional=tuple(pending),
            )
        trace.append("   储能回收更短或相当 → **进入模式三/混合策略判断**")
        branch = "mode3"
    elif spb is not None or upb is not None:
        _append_mode3_payback_calc_trace(
            trace,
            inp=inp,
            annual_save=annual_save,
            shave_kw=shave_kw,
            storage_capex=storage_capex,
            upgrade_capex=upgrade_capex,
            spb=spb,
            upb=upb,
            peak_kva=peak_kva,
            rated_kva=rated_kva,
        )
        branch = "mode3"
    elif grid_ok == "允许" and upgrade_capex > 1e-3 and (spb is None):
        if upb is not None and annual_save > 1e-3:
            _append_mode3_payback_calc_trace(
                trace,
                inp=inp,
                annual_save=annual_save,
                shave_kw=shave_kw,
                storage_capex=storage_capex,
                upgrade_capex=upgrade_capex,
                spb=None,
                upb=upb,
                peak_kva=peak_kva,
                rated_kva=rated_kva,
            )
        trace.append("   已填增容投资但储能投资/年节省不足 → 暂按 **增容** 提示（请补储能投资）")
        return Mode3AdvisoryResult(
            recommendation=MODE3_ADVICE_TF_UPGRADE,
            summary="增容费用已填但储能侧投资或节省口径不完整，暂倾向变压器增容；补全储能粗算后可重算。",
            decision_trace=tuple(trace),
            suggested_usage=USAGE_PEAK_VALLEY_ONLY,
            payback_storage_years=spb,
            payback_upgrade_years=upb,
            pending_optional=tuple(pending),
        )
    else:
        trace.append("   增容可行但缺完整投资对比 → **倾向模式三路径**（建议补全选填后重算）")
        branch = "mode3"

    spread = max(0.0, float(inp.peak_price) - float(inp.valley_price))
    trace.append(
        f"⑥ 峰谷价差 **{spread:.2f} 元/kWh**（混合策略阈值 > {spread_hybrid_yuan:.2f} 元/kWh）"
    )
    if branch == "mode3" and spread > spread_hybrid_yuan + 1e-6:
        trace.append("   价差较大 → **建议混合策略（模式二 + 动态增容）**")
        return Mode3AdvisoryResult(
            recommendation=MODE3_ADVICE_HYBRID,
            summary=(
                f"适合削峰降需量，且当地峰谷价差约 {spread:.2f} 元/kWh 较大，"
                "可在动态增容基础上叠加「峰谷套利+充电」提高收益。"
            ),
            decision_trace=tuple(trace),
            suggested_usage=USAGE_HYBRID,
            payback_storage_years=spb,
            payback_upgrade_years=upb,
            pending_optional=tuple(pending),
        )

    trace.append("   → **推荐纯模式三**（只削峰，不主打峰谷套利）")
    return Mode3AdvisoryResult(
        recommendation=MODE3_ADVICE_PURE_M3,
        summary="短时尖峰、增容困难或储能回收更优，且峰谷价差不足以支撑混合套利时，建议选「纯动态增容+充电」。",
        decision_trace=tuple(trace),
        suggested_usage=USAGE_DYNAMIC_CAPACITY,
        payback_storage_years=spb,
        payback_upgrade_years=upb,
        pending_optional=tuple(pending),
    )


def _append_mode3_payback_calc_trace(
    trace: list[str],
    *,
    inp: Mode3AdvisoryInput,
    annual_save: float,
    shave_kw: float,
    storage_capex: float,
    upgrade_capex: float,
    spb: float | None,
    upb: float | None,
    peak_kva: float,
    rated_kva: float,
) -> None:
    """在决策明细中展开年节省与增容/储能回收期算式。作者: jiali.qiu"""
    if annual_save < 1e-3:
        trace.append("⑤ 年节省过小，无法计算回收期（请补全削峰 kW 与电价）")
        return
    trace.append("⑤ **年节省与回收期计算过程**（同口径对比）")
    if str(inp.billing_mode) == MODE3_BILLING_KVA and float(inp.basic_yuan_per_kva_month) > 1e-6:
        excess_kva = max(0.0, peak_kva - rated_kva)
        trace.append(
            f"   **年节省** = 超额 kVA × 容量电价 × 12"
            f" = {excess_kva:.1f} × {inp.basic_yuan_per_kva_month:.2f} × 12"
            f" = **{annual_save:,.0f}** 元/年（**{annual_save / 1e4:.2f}** 万元/年）"
        )
    else:
        trace.append(
            f"   **年节省** = 削峰 kW × 需量电价(元/kW·月) × 12"
            f" = {shave_kw:.0f} × {inp.basic_yuan_per_kw_month:.2f} × 12"
            f" = **{annual_save:,.0f}** 元/年（**{annual_save / 1e4:.2f}** 万元/年）"
        )
    if spb is not None:
        sto_wan = storage_capex / 1e4
        trace.append(
            f"   **储能方案回收期** = 储能投资 ÷ 年节省"
            f" = {sto_wan:.2f} 万元 ÷ {annual_save / 1e4:.2f} 万元/年"
            f" = **{spb:.2f} 年**（约 **{spb:.1f}** 年）"
        )
    if upb is not None:
        up_wan = upgrade_capex / 1e4
        trace.append(
            f"   **增容一次性投资回收期** = 增容投资 ÷ 年节省"
            f" = {up_wan:.2f} 万元 ÷ {annual_save / 1e4:.2f} 万元/年"
            f" = **{upb:.2f} 年**（约 **{upb:.1f}** 年）"
        )
    if spb is not None and upb is not None:
        cmp = "增容更短" if upb < spb - 1e-6 else "储能更短或相当"
        trace.append(
            f"   **对比**：增容约 **{upb:.1f} 年** vs 储能约 **{spb:.1f} 年** → {cmp}"
        )


def _m3_link_caption(source: str) -> None:
    """模式三测算：标注联动字段的数据来源。作者: jiali.qiu"""
    _html_link_hint(f"🔗 联动：{source}")


def _apply_mode3_advisory_context_hints(
    *,
    rated_default: float,
    load_pct_hint: float,
    gap_kw: float,
    declared_kw_hint: float,
    storage_wan_hint: float,
    upgrade_wan_hint: float,
    peak_price: float,
    valley_price: float,
    peak_hours_max: float,
    basic_kw_hint: float,
) -> None:
    """
    用当前场站/桩配置/默认值刷新模式三测算表单联动项（保留用户已填的选填备注类字段）。
    作者: jiali.qiu
    """
    st.session_state["m3_adv_rated_kva"] = float(rated_default)
    st.session_state["m3_adv_peak_pct"] = float(load_pct_hint)
    st.session_state["m3_adv_peak_kw"] = 0.0
    st.session_state["m3_adv_declared_kw"] = float(declared_kw_hint)
    st.session_state["m3_adv_shave_kw"] = float(gap_kw)
    st.session_state["m3_adv_up_wan"] = float(upgrade_wan_hint)
    st.session_state["m3_adv_sto_wan"] = float(storage_wan_hint)
    st.session_state["m3_adv_peak_p"] = float(peak_price)
    st.session_state["m3_adv_valley_p"] = float(valley_price)
    st.session_state["m3_adv_basic_kw"] = float(basic_kw_hint)
    _sync_session_linked_float(
        "m3_adv_hours",
        "_m3_adv_hours_linked_peak_max",
        min(24.0, float(peak_hours_max)),
    )
    st.session_state.pop("m3_adv_last_result", None)


def _sync_session_linked_float(
    state_key: str,
    linked_meta_key: str,
    new_default: float,
) -> None:
    """
    将 session 中的数值与配置默认值联动：配置变更时，若用户仍等于上一版默认值则同步更新。
    作者: jiali.qiu
    """
    new_v = float(new_default)
    prev_linked = st.session_state.get(linked_meta_key)
    if state_key not in st.session_state:
        st.session_state[state_key] = new_v
    elif prev_linked is not None and abs(float(prev_linked) - new_v) > 1e-6:
        if abs(float(st.session_state[state_key]) - float(prev_linked)) < 1e-6:
            st.session_state[state_key] = new_v
    st.session_state[linked_meta_key] = new_v


def render_mode3_advisory_tool(
    *,
    defaults: dict[str, Any],
    power_factor: float,
    kva_existing: float,
    kva_remaining: float,
    baseline_kva: float,
    peak_price: float,
    valley_price: float,
    pile_req_kva: float,
    gap_kva: float,
    storage_sets: int,
    storage_kwh_per_set: float,
    price_storage_per_kwh: float,
    upgrade_cost_wan_hint: float,
    basic_kw_price_hint: float,
) -> Mode3AdvisoryResult | None:
    """
    第三部分：在「储能主要用途」下拉框之前展示模式三适用性测算面板。
    作者: jiali.qiu
    """
    rated_default = float(kva_existing) if float(kva_existing) > 1e-3 else float(baseline_kva)
    if rated_default < 1e-3:
        rated_default = max(float(kva_remaining), 1.0)
    pf = max(0.01, float(power_factor))
    gap_kw = max(0.0, float(gap_kva)) * pf
    req_kva = max(0.0, float(pile_req_kva))
    load_pct_hint = 100.0
    if rated_default > 1e-3 and req_kva > 1e-3:
        load_pct_hint = float(np.clip(req_kva / rated_default * 100.0, 50.0, 200.0))
    storage_wan_hint = (
        max(0, int(storage_sets))
        * max(0.0, float(storage_kwh_per_set))
        * max(0.0, float(price_storage_per_kwh))
        * 1000.0
        / 1e4
    )
    if storage_wan_hint < 0.01:
        storage_wan_hint = max(
            0.0,
            float(defaults.get("storage_kwh_per_set", STORAGE_KWH_PER_SET_DEFAULT))
            * float(defaults.get("price_storage_per_kwh", 0.65))
            * 1000.0
            / 1e4,
        )

    peak_hours_max = max(
        0.5, float(defaults.get("mode3_peak_hours_max", MODE3_PEAK_HOURS_MAX_DEFAULT))
    )
    spread_hybrid_yuan = max(
        0.0, float(defaults.get("mode3_spread_hybrid_yuan", MODE3_SPREAD_HYBRID_YUAN_DEFAULT))
    )

    with st.expander("模式三适用性测算（选型参考，不影响确认测算）", expanded=False):
        st.caption(
            "根据变压器负载、超载时长、增容可行性与投资回收期粗判，"
            "帮助决定是否选择「纯动态增容+充电」。结果仅供参考，确认测算仍以您在下方选择的用途为准。"
            f" 当前阈值：超载 ≤ **{peak_hours_max:.2f}** 小时/天；混合策略价差 > **{spread_hybrid_yuan:.2f}** 元/kWh"
            "（可在侧栏或参数默认值编辑器中修改 `mode3_peak_hours_max` / `mode3_spread_hybrid_yuan`）。"
        )
        declared_kw_hint = max(usable_station_capacity_kw(kva_remaining, pf), gap_kw)
        if "m3_adv_rated_kva" not in st.session_state:
            _apply_mode3_advisory_context_hints(
                rated_default=rated_default,
                load_pct_hint=load_pct_hint,
                gap_kw=gap_kw,
                declared_kw_hint=declared_kw_hint,
                storage_wan_hint=storage_wan_hint,
                upgrade_wan_hint=upgrade_cost_wan_hint,
                peak_price=peak_price,
                valley_price=valley_price,
                peak_hours_max=peak_hours_max,
                basic_kw_hint=basic_kw_price_hint,
            )

        btn_refresh, btn_run = st.columns(2)
        with btn_refresh:
            if st.button(
                "刷新联动数据",
                key="m3_adv_refresh_btn",
                type="secondary",
                use_container_width=True,
                help="根据当前场站、桩数量、配电增容与储能粗算，重新带入额定容量、峰值负载率、削峰 kW、投资粗算等。",
            ):
                _apply_mode3_advisory_context_hints(
                    rated_default=rated_default,
                    load_pct_hint=load_pct_hint,
                    gap_kw=gap_kw,
                    declared_kw_hint=declared_kw_hint,
                    storage_wan_hint=storage_wan_hint,
                    upgrade_wan_hint=upgrade_cost_wan_hint,
                    peak_price=peak_price,
                    valley_price=valley_price,
                    peak_hours_max=peak_hours_max,
                    basic_kw_hint=basic_kw_price_hint,
                )
                st.rerun()
        with btn_run:
            run_m3 = st.button(
                "运行模式三适用性测算",
                type="secondary",
                use_container_width=True,
                key="m3_adv_run_btn",
            )

        st.markdown(
            f"**当前联动参考**（改桩数/增容/储能套数后请先点「刷新联动数据」）  \n"
            f"规划需用 **{pile_req_kva:.1f} kVA** → 负载率约 **{load_pct_hint:.0f}%** · "
            f"容量缺口 **{gap_kw:.0f} kW** · 储能投资粗算 **{storage_wan_hint:.2f}** 万元 · "
            f"增容投资粗算 **{upgrade_cost_wan_hint:.2f}** 万元"
        )

        _html_note_panel(
            "**请您确认或填写（测算关键项）**：每日超载持续时间 *、基本电费计费方式 *、"
            "电网是否允许增容 *、储能投资粗算 *；按需量计费时填写申报需量；"
            "回收期对比需填写增容预估费用与储能投资（可联动刷新）。"
        )
        with st.expander("联动项与默认值来源说明", expanded=False):
            st.markdown(
                f"""
- **变压器额定容量** ← 第一步「现有变压器额定」或「剩余可用」基准（当前约 **{rated_default:.0f} kVA**）
- **当前峰值负载率** ← 第二步桩规划需用 **{pile_req_kva:.1f} kVA** ÷ 额定容量（刷新后更新）
- **申报需量 / 削峰 kW** ← 第一步剩余可用×功率因数、桩相对「剩余可用」的容量缺口（约 **{gap_kw:.0f} kW**）
- **增容预估费用** ← 第二步「配电增容」ΔkVA × 增容单价粗算
- **储能投资粗算** ← 第三部分储能套数 × 单套容量 × 单价
- **峰/谷电价** ← 表单默认参数（与第三部分分时电价一致时可刷新带入）
- **需量电价** ← 默认参数 `basic_electricity_yuan_per_kw_month`
- **每日超载持续时间**初值 ← 配置项 `mode3_peak_hours_max`（当前 **{peak_hours_max:.2f}** 小时/天）
- **判定阈值**：超载上限 **{peak_hours_max:.2f}** 小时/天；混合策略价差 **>{spread_hybrid_yuan:.2f}** 元/kWh
                """
            )

        st.markdown(
            "**信息收集清单**（带 * 为必填；🔗 为可联动刷新项；选填项建议与供电/现场核对）"
        )
        req_col, opt_col = st.columns(2)
        with req_col:
            st.markdown("**必填**")
            m3_rated = float(
                st.number_input(
                    "* 变压器额定容量(kVA)",
                    min_value=1.0,
                    step=10.0,
                    help="🔗 联动第一步额定/剩余可用基准；改场站后请刷新。",
                    key="m3_adv_rated_kva",
                )
            )
            _m3_link_caption(
                f"第一步变压器（额定或剩余可用基准，当前约 {rated_default:.0f} kVA）"
            )
            m3_peak_pct = float(
                st.number_input(
                    "* 当前峰值负载率(%)",
                    min_value=0.0,
                    max_value=300.0,
                    step=1.0,
                    help="🔗 联动第二步桩配置；>100% 表示超载。改桩数后请点「刷新联动数据」。",
                    key="m3_adv_peak_pct",
                )
            )
            _m3_link_caption(
                f"第二步规划需用 {pile_req_kva:.1f} kVA ÷ 额定 → 约 {load_pct_hint:.0f}%"
            )
            m3_peak_kw = float(
                st.number_input(
                    "峰值功率(kW,0=用负载率推算)",
                    min_value=0.0,
                    step=10.0,
                    help="填 0 时由「额定×负载率×功率因数」推算；可手填实测峰值。",
                    key="m3_adv_peak_kw",
                )
            )
            _sync_session_linked_float(
                "m3_adv_hours",
                "_m3_adv_hours_linked_peak_max",
                min(24.0, peak_hours_max),
            )
            m3_hours = float(
                st.number_input(
                    "* 每日超载持续时间(小时/天)",
                    min_value=0.0,
                    max_value=24.0,
                    step=0.5,
                    help=(
                        f"⚠️ 请按现场尖峰时长填写；初值联动 mode3_peak_hours_max（{peak_hours_max:.2f} h）。"
                        f"判定上限 ≤ {peak_hours_max:.2f} 小时/天。"
                    ),
                    key="m3_adv_hours",
                )
            )
            m3_billing = st.selectbox(
                "* 基本电费计费方式",
                options=MODE3_BILLING_OPTIONS,
                index=_select_index(
                    MODE3_BILLING_OPTIONS,
                    str(st.session_state.get("m3_adv_billing", MODE3_BILLING_KW)),
                    MODE3_BILLING_KW,
                ),
                help="⚠️ 请选择与电费账单一致的计费方式（容量 / 需量）。",
                key="m3_adv_billing",
            )
            m3_declared = float(
                st.number_input(
                    "申报需量(kW,按需量计费时填)",
                    min_value=0.0,
                    step=10.0,
                    help="🔗 联动第一步剩余可用×PF；按需量计费时请核对账单申报值。",
                    key="m3_adv_declared_kw",
                )
            )
            _m3_link_caption(f"默认取剩余可用约 {declared_kw_hint:.0f} kW（刷新后更新）")
            m3_basic_kw = float(
                st.number_input(
                    "* 需量/基本电价(元/kW·月)",
                    min_value=0.0,
                    step=1.0,
                    help="🔗 默认参数 basic_electricity_yuan_per_kw_month；请按当地电价核对。",
                    key="m3_adv_basic_kw",
                )
            )
            m3_basic_kva = float(
                st.number_input(
                    "容量电价(元/kVA·月,按容量计费时填)",
                    min_value=0.0,
                    step=1.0,
                    key="m3_adv_basic_kva",
                )
            )
            m3_shave = float(
                st.number_input(
                    "预估削峰功率(kW,0=自动)",
                    min_value=0.0,
                    step=10.0,
                    help="🔗 联动容量缺口 kW；填 0 时测算按峰值与需量自动推算。",
                    key="m3_adv_shave_kw",
                )
            )
            _m3_link_caption(f"第二步相对「剩余可用」缺口约 {gap_kw:.0f} kW")
        with opt_col:
            st.markdown("**选填（待后续确认）**")
            m3_penalty = st.number_input(
                "需量惩罚倍数(超申报加收)",
                min_value=0.0,
                value=float(st.session_state.get("m3_adv_penalty", 0.0)),
                step=0.5,
                help="0 表示暂未确认；常见 2–3 倍。",
                key="m3_adv_penalty",
            )
            m3_peak_fixed = st.selectbox(
                "超载时段是否固定",
                options=["待确认", "是", "否"],
                index=0,
                key="m3_adv_peak_fixed",
            )
            m3_grid = st.selectbox(
                "* 电网是否允许增容",
                options=MODE3_GRID_UPGRADE_OPTIONS,
                index=_select_index(
                    MODE3_GRID_UPGRADE_OPTIONS,
                    str(st.session_state.get("m3_adv_grid", "待确认")),
                    0,
                ),
                help="⚠️ 影响「推荐增容」还是「推荐模式三」分支，请向供电公司确认。",
                key="m3_adv_grid",
            )
            m3_up_wan = float(
                st.number_input(
                    "增容预估费用(万元)",
                    min_value=0.0,
                    step=10.0,
                    help="🔗 联动第二步配电增容 ΔkVA×单价；回收期对比必填其一（与储能投资）。",
                    key="m3_adv_up_wan",
                )
            )
            _m3_link_caption(f"配电增容粗算约 {upgrade_cost_wan_hint:.2f} 万元（刷新后更新）")
            st.text_area(
                "增容施工周期/场地限制说明",
                value=str(st.session_state.get("m3_adv_up_note", "")),
                height=68,
                key="m3_adv_up_note",
            )
            m3_site = st.selectbox(
                "储能安装条件是否具备",
                options=["待确认", "具备", "不具备"],
                index=0,
                key="m3_adv_site",
            )
            st.text_input("并网点距离/线缆备注", key="m3_adv_cable_note")
            st.text_input("消防/并网备案备注", key="m3_adv_fire_note")

        fin1, fin2, fin3 = st.columns(3)
        with fin1:
            m3_sto_wan = float(
                st.number_input(
                    "* 储能投资粗算(万元)",
                    min_value=0.0,
                    step=10.0,
                    help="🔗 联动第三部分储能套数×容量×单价；回收期对比必填。",
                    key="m3_adv_sto_wan",
                )
            )
            _m3_link_caption(f"第三部分储能粗算约 {storage_wan_hint:.2f} 万元")
        with fin2:
            m3_peak_p = float(
                st.number_input(
                    "高峰电价(元/kWh)",
                    min_value=0.0,
                    step=0.01,
                    help="🔗 联动表单峰电价；影响混合策略（模式二+削峰）判断。",
                    key="m3_adv_peak_p",
                )
            )
        with fin3:
            m3_valley_p = float(
                st.number_input(
                    "低谷电价(元/kWh)",
                    min_value=0.0,
                    step=0.01,
                    help="🔗 联动表单谷电价；峰−谷价差 > 阈值时建议混合策略。",
                    key="m3_adv_valley_p",
                )
            )

        result: Mode3AdvisoryResult | None = None
        if run_m3:
            peak_fixed: bool | None = None
            if m3_peak_fixed == "是":
                peak_fixed = True
            elif m3_peak_fixed == "否":
                peak_fixed = False
            site_ok: bool | None = None
            if m3_site == "具备":
                site_ok = True
            elif m3_site == "不具备":
                site_ok = False
            penalty = float(m3_penalty) if float(m3_penalty) > 1e-6 else None
            inp = Mode3AdvisoryInput(
                transformer_rated_kva=m3_rated,
                power_factor=pf,
                peak_load_pct=m3_peak_pct,
                peak_demand_kw=m3_peak_kw,
                declared_demand_kw=m3_declared,
                overload_hours_per_day=m3_hours,
                billing_mode=str(m3_billing),
                basic_yuan_per_kw_month=m3_basic_kw,
                basic_yuan_per_kva_month=m3_basic_kva,
                grid_upgrade_allowed=str(m3_grid),
                upgrade_cost_wan=m3_up_wan,
                storage_capex_wan_est=m3_sto_wan,
                peak_price=m3_peak_p,
                valley_price=m3_valley_p,
                estimated_shave_kw=m3_shave,
                demand_penalty_multiplier=penalty,
                overload_peak_fixed=peak_fixed,
                upgrade_cycle_note=str(st.session_state.get("m3_adv_up_note", "")),
                storage_site_ok=site_ok,
            )
            result = assess_mode3_suitability(
                inp,
                peak_hours_max=peak_hours_max,
                spread_hybrid_yuan=spread_hybrid_yuan,
            )
            st.session_state["m3_adv_last_result"] = result

        result = st.session_state.get("m3_adv_last_result")
        if isinstance(result, Mode3AdvisoryResult):
            rec = result.recommendation
            if rec == MODE3_ADVICE_NO_NEED:
                _html_note_panel(f"**{rec}**\n\n{result.summary}")
            elif rec == MODE3_ADVICE_NOT_SUITABLE:
                st.warning(f"**{rec}**\n\n{result.summary}")
            elif rec == MODE3_ADVICE_TF_UPGRADE:
                st.warning(f"**{rec}**\n\n{result.summary}")
            elif rec == MODE3_ADVICE_HYBRID:
                st.success(f"**{rec}**\n\n{result.summary}")
            else:
                st.success(f"**{rec}**\n\n{result.summary}")
            if result.suggested_usage:
                st.caption(
                    f"建议在下方「储能主要用途」中选择：**{result.suggested_usage}**"
                )
            if result.payback_storage_years is not None or result.payback_upgrade_years is not None:
                pb_parts = []
                if result.payback_storage_years is not None:
                    pb_parts.append(f"储能回收期约 **{result.payback_storage_years:.1f}** 年")
                if result.payback_upgrade_years is not None:
                    pb_parts.append(f"增容回收期约 **{result.payback_upgrade_years:.1f}** 年")
                st.caption(" · ".join(pb_parts))
            with st.expander("决策过程明细", expanded=False):
                for line in result.decision_trace:
                    st.markdown(line)
            if result.pending_optional:
                st.markdown("**待后续确认的选填项：**")
                for item in result.pending_optional:
                    st.markdown(f"- {item}")
            if result.suggested_usage and st.button(
                "采纳建议用途（写入下方下拉框）",
                key="m3_adv_apply_usage",
            ):
                st.session_state["storage_usage_select"] = result.suggested_usage
                st.rerun()
    return result if isinstance(result, Mode3AdvisoryResult) else None


def spots_used_by_60_120(n60: int, n120: int) -> int:
    """60kW 每台 1 车位；120kW 双枪每台 2 车位。"""
    return max(0, int(n60)) + max(0, int(n120)) * PILE_120_SPOTS_PER_UNIT


def _optimize_60_120_pair(max_kw: float, max_spots: int) -> tuple[int, int]:
    """
    在剩余容量与车位内布置 60/120kW，使总功率尽可能大（同功率优先 120kW）。
    max_spots：可供 60/120 占用的车位数（已扣除 480kW 占位数）。
    """
    max_spots = max(0, int(max_spots))
    budget = max(0.0, float(max_kw))
    if max_spots <= 0 or budget <= 0:
        return 0, 0

    best_n60, best_n120, best_kw = 0, 0, -1.0
    max_n120 = max_spots // PILE_120_SPOTS_PER_UNIT
    for n120 in range(max_n120 + 1):
        spots_after_120 = max_spots - n120 * PILE_120_SPOTS_PER_UNIT
        if spots_after_120 < 0:
            continue
        for n60 in range(spots_after_120 + 1):
            kw = n60 * PILE_RATED_KW_60 + n120 * PILE_RATED_KW_120
            if kw <= budget + 1e-6 and (
                kw > best_kw or (abs(kw - best_kw) < 1e-6 and n120 > best_n120)
            ):
                best_n60, best_n120, best_kw = n60, n120, kw
    return best_n60, best_n120


def optimize_charger_mix(
    kva_remaining: float,
    *,
    include_480: bool,
    kx: float,
    kt: float,
    cos_phi: float,
    eta: float,
    parking_spots: int,
) -> dict[str, Any]:
    """
    按剩余 kVA 规划桩型：
    - 若选 480kW：先扣 1 套（占 ULTRA_PARKING_SPOTS_PER_SET 个车位）及对应 kVA；
    - 余量与（总车位 − 480 占位数）内优先布置 120kW（每台占 2 车位），再 60kW（每台 1 车位），总功率尽量大。
    """
    kva = max(0.0, float(kva_remaining))
    spot_cap = max(0, int(parking_spots))
    need_kva_480 = required_transformer_kva(
        PILE_RATED_KW_ULTRA_PER_SET, kx, kt, cos_phi, eta
    )
    can_480 = (
        include_480
        and need_kva_480 <= kva + 1e-6
        and spot_cap >= ULTRA_PARKING_SPOTS_PER_SET
    )

    best: dict[str, Any] = {
        "n60": 0,
        "n120": 0,
        "n480": 0,
        "total_kw": 0.0,
        "required_kva": 0.0,
        "fits": True,
        "spots_for_60_120": spot_cap,
        "ultra_spots_used": 0,
    }

    for n480 in ([1] if can_480 else [0]):
        ultra_spots = ULTRA_PARKING_SPOTS_PER_SET if n480 else 0
        spots_for_60_120 = spot_cap - ultra_spots
        if spots_for_60_120 < 0:
            continue

        kva_rest = kva - (need_kva_480 if n480 else 0.0)
        if kva_rest < -1e-6:
            continue
        max_kw_rest = max_pile_kw_from_remaining_kva(kva_rest, kx, kt, cos_phi, eta)

        n60, n120 = _optimize_60_120_pair(max_kw_rest, spots_for_60_120)
        total_kw = (
            n60 * PILE_RATED_KW_60
            + n120 * PILE_RATED_KW_120
            + n480 * PILE_RATED_KW_ULTRA_PER_SET
        )
        req_kva = required_transformer_kva(total_kw, kx, kt, cos_phi, eta)
        if req_kva > kva + 1e-6:
            continue

        if total_kw > float(best["total_kw"]) + 1e-6 or (
            abs(total_kw - float(best["total_kw"])) < 1e-6
            and n120 > int(best["n120"])
        ):
            best = {
                "n60": n60,
                "n120": n120,
                "n480": n480,
                "total_kw": total_kw,
                "required_kva": req_kva,
                "fits": True,
                "spots_for_60_120": spots_for_60_120,
                "ultra_spots_used": ultra_spots,
                "max_kw_for_60_120": max_kw_rest,
            }

    best["can_480"] = can_480
    best["need_kva_480"] = need_kva_480
    best["max_pile_kw"] = max_pile_kw_from_remaining_kva(kva, kx, kt, cos_phi, eta)
    best["kx"] = kx
    best["kt"] = kt
    best["cos_phi"] = cos_phi
    best["eta"] = eta
    best["kva_remaining"] = kva
    best["parking_spots_total"] = spot_cap
    return best


def _format_charger_plan_summary(
    plan: dict[str, Any], kva_remaining: float, parking_spots: int
) -> str:
    """生成最优方案结论（一行摘要）。"""
    plan = _normalize_charger_plan(plan)
    n60, n120, n480 = plan["n60"], plan["n120"], plan["n480"]
    parts: list[str] = []
    if n480:
        parts.append(f"480kW×{n480}套")
    if n120:
        parts.append(f"120kW×{n120}台(双枪)")
    if n60:
        parts.append(f"60kW×{n60}台")
    mix = " + ".join(parts) if parts else "（无桩，容量不足或系数过严）"
    spots_used = (
        (ULTRA_PARKING_SPOTS_PER_SET if n480 else 0) + spots_used_by_60_120(n60, n120)
    )
    return (
        f"**推荐搭配：** {mix} · 装机 **{plan['total_kw']:.0f} kW** · "
        f"需用变压器 **{plan['required_kva']:.1f} kVA** / 剩余 **{kva_remaining:.1f} kVA** · "
        f"占车位 **{spots_used}** / {parking_spots}"
    )


def _format_charger_plan_calc_process(
    plan: dict[str, Any],
    *,
    include_480: bool,
) -> str:
    """推荐搭配的计算过程（Markdown）。"""
    plan = _normalize_charger_plan(plan)
    kva = float(plan.get("kva_remaining", 0))
    kx = float(plan.get("kx", DEMAND_COEFF_KX_DEFAULT))
    kt = float(plan.get("kt", SIMULTANEITY_KT_DEFAULT))
    cos_phi = float(plan.get("cos_phi", PLAN_COS_PHI_DEFAULT))
    eta = float(plan.get("eta", PLAN_LOAD_ETA_DEFAULT))
    pmax = float(plan.get("max_pile_kw", 0))
    n60, n120, n480 = int(plan["n60"]), int(plan["n120"]), int(plan["n480"])
    spots_total = int(plan.get("parking_spots_total", 0))

    lines = [
        "**① 由剩余容量反推允许桩总功率**",
        f"- 剩余可用变压器：**{kva:.1f} kVA**",
        f"- 系数：Kx={kx:.2f}，Kt={kt:.2f}，cosΦ={cos_phi:.2f}，η={eta:.2f}",
        (
            f"- P_max = 剩余×cosΦ×η / (Kx×Kt)"
            f" = {kva:.1f}×{cos_phi:.2f}×{eta:.2f} / ({kx:.2f}×{kt:.2f})"
            f" ≈ **{pmax:.1f} kW**"
        ),
    ]

    kva_rest = kva
    pmax_rest = pmax
    spots_for_60_120 = spots_total

    if include_480 and n480 > 0:
        need480 = float(plan.get("need_kva_480", 0))
        kva_rest = kva - need480
        pmax_rest = float(plan.get("max_kw_for_60_120", 0))
        spots_for_60_120 = int(plan.get("spots_for_60_120", 0))
        lines.extend(
            [
                "",
                "**② 分体式液冷 480kW（1 套）**",
                (
                    f"- 需用 kVA = 480×Kx×Kt/(cosΦ×η) = 480×{kx:.2f}×{kt:.2f}/({cos_phi:.2f}×{eta:.2f})"
                    f" ≈ **{need480:.1f} kVA**"
                ),
                f"- 占车位 **{ULTRA_PARKING_SPOTS_PER_SET}** 个 → 余车位 **{spots_for_60_120}** = {spots_total} − {ULTRA_PARKING_SPOTS_PER_SET}",
                (
                    f"- 余 kVA **{kva_rest:.1f}** → 余允许功率约 **{pmax_rest:.1f} kW**"
                    f"（用于 60/120 搭配）"
                ),
            ]
        )
    else:
        lines.extend(
            [
                "",
                f"**② 车位约束**：总 **{spots_total}** 位（未配置 480kW）",
                "- 60kW：1 台占 **1** 位；120kW 双枪：1 台占 **2** 位",
            ]
        )

    kw_60_120 = n60 * PILE_RATED_KW_60 + n120 * PILE_RATED_KW_120
    spots_60_120 = spots_used_by_60_120(n60, n120)
    lines.extend(
        [
            "",
            "**③ 在余量内优选 60kW / 120kW（功率尽量大，优先 120kW）**",
            f"- 可用车位：**{spots_for_60_120}** 位；可用功率约：**{pmax_rest:.1f} kW**",
            (
                f"- 选用：**{n120} 台×120kW**（占 {n120 * PILE_120_SPOTS_PER_UNIT} 位）"
                f" + **{n60} 台×60kW**（占 {n60} 位）→ 合计占 **{spots_60_120}** 位"
            ),
            f"- 60/120 装机 = {n60}×60 + {n120}×120 = **{kw_60_120:.0f} kW**",
        ]
    )

    total_kw = float(plan["total_kw"])
    req_kva = float(plan["required_kva"])
    lines.extend(
        [
            "",
            "**④ 汇总校验**",
            f"- 桩总功率 = {kw_60_120:.0f} + {n480 * PILE_RATED_KW_ULTRA_PER_SET:.0f} = **{total_kw:.0f} kW**",
            (
                f"- 需用变压器 = {total_kw:.0f}×Kx×Kt/(cosΦ×η) ≈ **{req_kva:.1f} kVA**"
                f" ≤ 剩余 **{kva:.1f} kVA** ✓"
                if req_kva <= kva + 1e-3
                else f"- 需用变压器 **{req_kva:.1f} kVA** 超过剩余 **{kva:.1f} kVA**"
            ),
        ]
    )
    slack_kw = pmax - total_kw
    lines.extend(
        [
            "",
            f"**功率余量**：P_max − 已配装机 ≈ **{slack_kw:.1f} kW**"
            + (f"（>{CHARGER_UTILIZATION_SLACK_KW:.0f} kW 时将给出优化建议）" if slack_kw > CHARGER_UTILIZATION_SLACK_KW else ""),
        ]
    )
    return "\n".join(lines)


def _pile_config_total_kw(n60: int, n120: int, n480: int) -> float:
    return (
        max(0, n60) * PILE_RATED_KW_60
        + max(0, n120) * PILE_RATED_KW_120
        + max(0, n480) * PILE_RATED_KW_ULTRA_PER_SET
    )


def _normalize_charger_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """合并规划器嵌套 opt，并补齐 total_kw / required_kva 等键。"""
    p = dict(plan)
    nested = p.get("opt")
    if isinstance(nested, dict):
        p = {**p, **nested}
    n60 = int(p.get("n60", 0))
    n120 = int(p.get("n120", 0))
    n480 = int(p.get("n480", 0))
    if "total_kw" not in p:
        p["total_kw"] = _pile_config_total_kw(n60, n120, n480)
    if "required_kva" not in p:
        p["required_kva"] = required_transformer_kva(
            float(p["total_kw"]),
            float(p.get("kx", DEMAND_COEFF_KX_DEFAULT)),
            float(p.get("kt", SIMULTANEITY_KT_DEFAULT)),
            float(p.get("cos_phi", PLAN_COS_PHI_DEFAULT)),
            float(p.get("eta", PLAN_LOAD_ETA_DEFAULT)),
        )
    if "max_pile_kw" not in p and "max_kw" in p:
        p["max_pile_kw"] = float(p["max_kw"])
    return p


def suggest_charger_utilization_improvements(plan: dict[str, Any]) -> list[str]:
    """
    当 P_max − 已配装机 > 60kW 时，结合剩余车位与 kVA 给出加桩/升功率建议。
    """
    plan = _normalize_charger_plan(plan)
    kva = float(plan.get("kva_remaining", 0))
    kx = float(plan.get("kx", DEMAND_COEFF_KX_DEFAULT))
    kt = float(plan.get("kt", SIMULTANEITY_KT_DEFAULT))
    cos_phi = float(plan.get("cos_phi", PLAN_COS_PHI_DEFAULT))
    eta = float(plan.get("eta", PLAN_LOAD_ETA_DEFAULT))
    pmax = float(plan.get("max_pile_kw", 0))
    n60, n120, n480 = int(plan["n60"]), int(plan["n120"]), int(plan["n480"])
    total = float(plan.get("total_kw", 0))
    slack = pmax - total
    spots_total = int(plan.get("parking_spots_total", 0))
    spots_used = (ULTRA_PARKING_SPOTS_PER_SET if n480 else 0) + spots_used_by_60_120(n60, n120)
    spots_left = spots_total - spots_used

    hints: list[str] = []
    if slack < CHARGER_UTILIZATION_SLACK_KW:
        hints.append(
            f"功率余量约 **{slack:.0f} kW**（低于 {CHARGER_UTILIZATION_SLACK_KW:.0f} kW 提示线），"
            "当前方案与允许功率匹配度较高。"
        )
        return hints

    hints.append(
        f"功率余量约 **{slack:.0f} kW**（允许 **{pmax:.0f} kW** − 已配 **{total:.0f} kW**）。"
        "在车位与变压器允许前提下，可考虑："
    )

    trials: list[tuple[str, int, int, int]] = []
    if spots_left >= 1:
        trials.append(("增加 1 台 60kW", n60 + 1, n120, n480))
    if spots_left >= PILE_120_SPOTS_PER_UNIT:
        trials.append(("增加 1 台 120kW 双枪", n60, n120 + 1, n480))
    if spots_left >= PILE_120_SPOTS_PER_UNIT * 2:
        trials.append(
            (
                f"增加 2 台 120kW 双枪（合计约 {PILE_RATED_KW_240_RECOMMEND:.0f} kW 级）",
                n60,
                n120 + 2,
                n480,
            )
        )
    if spots_left >= ULTRA_PARKING_SPOTS_PER_SET and n480 == 0 and plan.get("can_480"):
        trials.append(("增加 1 套 480kW 液冷", n60, n120, 1))

    best: tuple[str, int, int, int, float] | None = None
    for label, t60, t120, t480 in trials:
        tkw = _pile_config_total_kw(t60, t120, t480)
        if tkw > pmax + 1e-6:
            continue
        if required_transformer_kva(tkw, kx, kt, cos_phi, eta) > kva + 1e-6:
            continue
        if best is None or tkw > best[4]:
            best = (label, t60, t120, t480, tkw)

    if best is not None:
        label, t60, t120, t480, tkw = best
        hints.append(
            f"- **推荐优化（单步）**：{label} → 装机 **{tkw:.0f} kW**，"
            f"余量约 **{pmax - tkw:.0f} kW**；参考数量：60kW×{t60}、120kW×{t120}、480kW×{t480}套"
        )
    if slack >= PILE_RATED_KW_120 and n120 == 0 and spots_left >= PILE_120_SPOTS_PER_UNIT:
        hints.append("- 余量 ≥120kW 且未配 120kW：**优先增加 1 台 120kW 双枪**（占 2 车位）。")
    if slack >= PILE_RATED_KW_240_RECOMMEND and spots_left >= PILE_120_SPOTS_PER_UNIT * 2:
        hints.append(
            f"- 余量 ≥{PILE_RATED_KW_240_RECOMMEND:.0f}kW：可考虑 **2 台 120kW 双枪**（约 240kW 级）"
            "或 **1 套 480kW**（占 4 车位，需容量校验）。"
        )
    if slack >= PILE_RATED_KW_60 and spots_left >= 1 and (best is None or best[4] < total + PILE_RATED_KW_60):
        hints.append("- 或 **+1 台 60kW**（占 1 车位）消化部分余量。")
    if spots_left <= 0 and slack >= CHARGER_UTILIZATION_SLACK_KW:
        hints.append(
            f"- **车位已满**（{spots_used}/{spots_total}），功率余量约 **{slack:.0f} kW**。"
            "在变压器容量允许前提下，可考虑**扩充车位**后加桩："
        )
        expand_options: list[tuple[str, int, int, int, int]] = []
        if slack + 1e-6 >= PILE_RATED_KW_60:
            expand_options.append(
                ("扩充 **1** 个车位 → **+1 台 60kW**", 1, n60 + 1, n120, n480)
            )
        if slack + 1e-6 >= PILE_RATED_KW_120:
            expand_options.append(
                (
                    "扩充 **2** 个车位 → **+1 台 120kW 双枪**",
                    2,
                    n60,
                    n120 + 1,
                    n480,
                )
            )
        if slack + 1e-6 >= PILE_RATED_KW_240_RECOMMEND:
            expand_options.append(
                (
                    f"扩充 **2** 个车位 → **+2 台 120kW**（约 {PILE_RATED_KW_240_RECOMMEND:.0f} kW）",
                    2,
                    n60,
                    n120 + 2,
                    n480,
                )
            )
        added_expand = False
        for label, extra_spots, t60, t120, t480 in expand_options:
            tkw = _pile_config_total_kw(t60, t120, t480)
            if tkw > pmax + 1e-6:
                continue
            if required_transformer_kva(tkw, kx, kt, cos_phi, eta) > kva + 1e-6:
                continue
            added_expand = True
            new_spots = spots_total + extra_spots
            new_used = charger_parking_spots_used(t60, t120, t480)
            hints.append(
                f"  - {label} → 装机 **{tkw:.0f} kW**，余量约 **{pmax - tkw:.0f} kW**；"
                f"车位由 {spots_total} 扩至 **{new_spots}** 个（占用 **{new_used}** 位）"
            )
        if expand_options and not added_expand:
            hints.append(
                "- 功率余量充足，但需用变压器已接近剩余容量上限：除扩车位外，可能还需**变压器增容**。"
            )

    return hints


def format_charger_config_confirm_summary(
    plan: dict[str, Any],
    *,
    plan_mode: str,
    kva_remaining: float,
    parking_spots: int,
) -> str:
    """「数量与单价」区：当前配置汇总 + 功率余量优化建议。"""
    plan = _normalize_charger_plan(plan)
    n60, n120, n480 = int(plan["n60"]), int(plan["n120"]), int(plan["n480"])
    spots_used = (ULTRA_PARKING_SPOTS_PER_SET if n480 else 0) + spots_used_by_60_120(n60, n120)
    lines = [
        "**当前推荐配置（上节规划结果，下方数量可改，提交后以您填写为准）**",
        f"- 配置方式：**{plan_mode}**",
        _format_charger_plan_summary(plan, kva_remaining, parking_spots),
        f"- 需用变压器约 **{float(plan['required_kva']):.1f} kVA**（剩余 **{kva_remaining:.1f} kVA**）",
        f"- 占用车位 **{spots_used}** / **{parking_spots}**（120kW 每台占 2 位）",
        "",
        "**功率利用率优化建议**",
    ]
    lines.extend(suggest_charger_utilization_improvements(plan))
    return "\n".join(lines)


def render_custom_chargers_storage_planner(
    sk: str,
    *,
    kva_remaining: float,
    kva_existing: float,
    parking_spots: int,
    defs: dict[str, Any],
) -> dict[str, Any]:
    """
    B：自定义桩和储能数量 — 桩型选型、配电校验、增容推荐与储能套数（规格固定 262kWh/125kW）。
    """
    _init_charger_plan_session_state(defs)
    _html_substep_title("B：自定义桩和储能数量")
    st.caption(
        f"第一步剩余可用 **{kva_remaining:.1f} kVA** · 场站车位 **{parking_spots}** 个；"
        "填写桩型数量后自动汇总功率与占位数。"
    )

    with st.expander("配电计算公式与系数说明", expanded=False):
        st.markdown(CHARGER_PLAN_FORMULA_MD)
        st.markdown(KT_SIMULTANEITY_NOTE)

    c1, c2, c3 = st.columns(3)
    with c1:
        kt_level = st.selectbox(
            "同时使用率",
            options=KT_LEVEL_OPTIONS,
            index=_select_index(
                KT_LEVEL_OPTIONS,
                str(st.session_state.get(sk + "kt_level", KT_LEVEL_MID)),
                KT_LEVEL_MID,
            ),
            help=(
                "Kt=同时启用的桩功率占比。1=满载同时；高0.7–0.9；低0.6–0.7；"
                "中间值0.7为一般规划默认，有实测值时按实际填写。"
            ),
            key=sk + "b_kt_level",
        )
        kt = kt_from_level(kt_level)
    with c2:
        kx = float(
            st.number_input(
                "需要系数 Kx",
                min_value=DEMAND_COEFF_KX_MIN,
                max_value=DEMAND_COEFF_KX_MAX,
                value=float(defs.get("demand_coeff_kx", DEMAND_COEFF_KX_DEFAULT)),
                step=0.05,
                key=sk + "b_kx",
            )
        )
    with c3:
        cos_phi = float(
            st.number_input(
                "功率因数 cosΦ",
                min_value=PLAN_COS_PHI_MIN,
                max_value=PLAN_COS_PHI_MAX,
                value=float(defs.get("planning_cos_phi", PLAN_COS_PHI_DEFAULT)),
                step=0.01,
                key=sk + "b_cos_phi",
            )
        )
    eta = float(
        st.number_input(
            "负载率 η",
            min_value=PLAN_LOAD_ETA_MIN,
            max_value=PLAN_LOAD_ETA_MAX,
            value=float(defs.get("planning_load_rate_eta", PLAN_LOAD_ETA_DEFAULT)),
            step=0.05,
            key=sk + "b_eta",
        )
    )
    _render_planning_kt_feedback(kt)

    _html_substep_title("数量与单价配置")

    st.markdown("**充电桩配置**")
    p1, p2, p3 = st.columns(3)
    with p1:
        n60 = int(
            st.number_input(
                "60kW（1枪/台）",
                min_value=0,
                value=int(defs.get("n_pile_60", 0)),
                step=1,
                key=sk + "b_n60",
            )
        )
    with p2:
        n120 = int(
            st.number_input(
                "120kW（双枪/台）",
                min_value=0,
                value=int(defs.get("n_pile_120", 0)),
                step=1,
                key=sk + "b_n120",
            )
        )
    with p3:
        n480 = int(
            st.number_input(
                "480kW（4枪/套分体式）",
                min_value=0,
                value=int(defs.get("n_ultra_480", 0)),
                step=1,
                key=sk + "b_n480",
                help=f"每套占 {ULTRA_PARKING_SPOTS_PER_SET} 个车位、{ULTRA_CHARGE_TERMINALS_PER_RACK} 个终端。",
            )
        )

    total_kw = _pile_config_total_kw(n60, n120, n480)
    req_kva = required_transformer_kva(total_kw, kx, kt, cos_phi, eta)
    spots_used = charger_parking_spots_used(n60, n120, n480)
    baseline_kva = _transformer_baseline_kva(kva_existing, kva_remaining)
    expansion_kva = charger_capacity_gap_kva(req_kva, kva_remaining)
    need_upgrade = expansion_kva > 1e-3
    sug_target, rec_kva, _ = suggested_transformer_upgrade(baseline_kva, expansion_kva)

    guns_60 = n60
    guns_120 = n120 * 2
    guns_480 = n480 * ULTRA_CHARGE_TERMINALS_PER_RACK
    st.markdown(
        f"**占用车位：** **{spots_used}** / {parking_spots} 个"
        f"（60kW×{n60} + 120kW×{n120}×2位 + 480kW×{n480}×{ULTRA_PARKING_SPOTS_PER_SET}位） · "
        f"充电枪合计约 **{guns_60 + guns_120 + guns_480}** 枪 · 装机 **{total_kw:.0f} kW**"
    )
    if spots_used > parking_spots:
        st.warning(f"占用车位 {spots_used} 个，超过场站 {parking_spots} 个，请减桩或增加车位。")

    st.markdown("**储能配置**")
    storage_install = st.radio(
        "是否安装工商业储能",
        options=STORAGE_INSTALL_OPTIONS,
        index=_select_index(
            STORAGE_INSTALL_OPTIONS,
            str(defs.get("storage_installed", STORAGE_INSTALL_NO)),
            STORAGE_INSTALL_NO,
        ),
        horizontal=True,
        key=sk + "b_storage_install",
    )
    install_storage = storage_install == STORAGE_INSTALL_YES
    storage_sets = 0
    p_kwh = float(defs.get("price_storage_per_kwh", 0.65))
    if install_storage:
        s_qty, s_price = st.columns(2)
        with s_qty:
            storage_sets = int(
                st.number_input(
                    "储能套数",
                    min_value=1,
                    value=max(1, int(defs.get("storage_sets", 1))),
                    step=1,
                    key=sk + "b_storage_sets",
                )
            )
        with s_price:
            p_kwh = float(
                st.number_input(
                    "储能单价(元/kWh)",
                    min_value=0.0,
                    value=float(defs.get("price_storage_per_kwh", 0.65)),
                    step=0.01,
                    key=sk + "b_price_storage_per_kwh",
                )
            )
        total_sto_kwh = storage_sets * STORAGE_KWH_PER_SET_DEFAULT
        st.caption(
            f"固定规格：**{STORAGE_KWH_PER_SET_DEFAULT:.0f} kWh/套** · "
            f"**{STORAGE_KW_PER_SET_DEFAULT:.0f} kW/套**；"
            f"合计 **{total_sto_kwh:.0f} kWh** / "
            f"**{storage_sets * STORAGE_KW_PER_SET_DEFAULT:.0f} kW** · "
            f"设备投资粗算 **{total_sto_kwh * p_kwh * 1000.0 / 1e4:.2f} 万元**"
        )
    else:
        st.caption(STORAGE_ENG_CAPEX_EXCLUDED_NOTE.replace("本区填写仅作预留；", ""))

    st.markdown("**配电增容**")
    st.markdown(
        f"- 需用变压器约 **{req_kva:.1f} kVA** · 第一步剩余可用 **{kva_remaining:.1f} kVA**"
        + (
            f" → **需增容约 {expansion_kva:.1f} kVA**（相对剩余可用的缺口）"
            if need_upgrade
            else " → **当前剩余可用可满足**（相对缺口 ≈ 0）"
        )
    )
    if need_upgrade:
        if rec_kva is not None:
            _html_note_panel(
                f"建议变压器：**基准 {baseline_kva:.0f} kVA + 增容 {expansion_kva:.1f} kVA** "
                f"→ 目标总容量 **{sug_target:.0f} kVA**（标准档位 **{rec_kva} kVA**）。"
                "若第一步为「不增容」请改选原址换大/高压申请。"
            )
        else:
            st.warning(
                f"建议在基准 {baseline_kva:.0f} kVA 上增容 {expansion_kva:.1f} kVA（目标约 {sug_target:.0f} kVA），"
                "请结合电网批复选择变压器档位。"
            )
    else:
        st.success("按当前系数，剩余可用容量可支撑该桩配置，无需增容。")

    e1, e2 = st.columns(2)
    with e1:
        k_target = float(
            st.number_input(
                "目标变压器总容量(kVA)",
                min_value=float(baseline_kva),
                value=float(
                    max(defs.get("kva_target", sug_target), sug_target, baseline_kva)
                ),
                step=50.0,
                key=sk + "b_k_target",
                help="CAPEX 按 max(0, 目标−基准)×单价；建议采用上方标准档位。",
            )
        )
    with e2:
        pu = float(
            st.number_input(
                "变压器增容单价(元/kVA)",
                min_value=0.0,
                value=float(defs.get("transformer_unit_price", 400.0)),
                step=10.0,
                key=sk + "b_pu",
                help="CAPEX 按 max(0, 目标容量−基准容量)×单价 计入。",
            )
        )
    delta_from_target = max(0.0, k_target - baseline_kva)
    est_upgrade_wan = delta_from_target * pu / 1e4
    st.caption(
        f"增容 ΔkVA ≈ **{delta_from_target:.1f}**（目标 {k_target:.0f} − 基准 {baseline_kva:.0f}）· "
        f"投资粗算 **{est_upgrade_wan:.2f} 万元**"
    )

    return {
        "mode": CHARGER_CONFIG_MODE_B,
        "n60": n60,
        "n120": n120,
        "n480": n480,
        "ultra_on": "是" if n480 > 0 else "否",
        "ultra_allowed": True,
        "kx": kx,
        "kt": kt,
        "cos_phi": cos_phi,
        "eta": eta,
        "total_kw": total_kw,
        "required_kva": req_kva,
        "delta_kva": expansion_kva,
        "expansion_kva": expansion_kva,
        "need_upgrade": need_upgrade,
        "kva_remaining": kva_remaining,
        "baseline_kva": baseline_kva,
        "recommended_kva": rec_kva,
        "k_target": k_target,
        "transformer_unit_price": pu,
        "spots_used": spots_used,
        "parking_spots_total": parking_spots,
        "storage_installed": storage_install,
        "storage_sets": storage_sets,
        "storage_kwh_per_set": STORAGE_KWH_PER_SET_DEFAULT,
        "storage_kw_per_set": STORAGE_KW_PER_SET_DEFAULT,
        "price_storage_per_kwh": p_kwh,
        "max_pile_kw": max_pile_kw_from_remaining_kva(kva_remaining, kx, kt, cos_phi, eta),
    }


def _init_charger_plan_session_state(defs: dict[str, Any]) -> None:
    """仅初始化无对应 widget 的 session 键，避免与 widget key 冲突导致 StreamlitAPIException。"""
    sk = "charger_plan_"
    if sk + "kx" not in st.session_state:
        st.session_state[sk + "kx"] = float(defs.get("demand_coeff_kx", DEMAND_COEFF_KX_DEFAULT))
    if sk + "cos_phi" not in st.session_state:
        st.session_state[sk + "cos_phi"] = float(defs.get("planning_cos_phi", PLAN_COS_PHI_DEFAULT))
    if sk + "eta" not in st.session_state:
        st.session_state[sk + "eta"] = float(defs.get("planning_load_rate_eta", PLAN_LOAD_ETA_DEFAULT))
    if sk + "want_480" not in st.session_state:
        st.session_state[sk + "want_480"] = str(defs.get("ultra_480_enabled", "否"))
    if sk + "kt_level" not in st.session_state:
        st.session_state[sk + "kt_level"] = kt_level_from_coeff(
            float(defs.get("simultaneity_coeff_kt", SIMULTANEITY_KT_DEFAULT))
        )


def render_charger_capacity_planner(
    *,
    kva_remaining: float,
    kva_existing: float,
    parking_spots: int,
    defs: dict[str, Any],
) -> dict[str, Any]:
    """
    第二步充电桩区（表单外，可实时刷新）：A 按容量规划 / B 自定义。
    返回当前规划结果，供表单内写入桩数。
    """
    sk = "charger_plan_"
    _init_charger_plan_session_state(defs)

    mode = st.radio(
        "配置方式",
        options=CHARGER_CONFIG_MODES,
        index=_select_index(
            CHARGER_CONFIG_MODES,
            str(st.session_state.get(sk + "mode_radio", CHARGER_CONFIG_MODE_A)),
            CHARGER_CONFIG_MODE_A,
        ),
        horizontal=True,
        key=sk + "mode_radio",
    )

    out: dict[str, Any] = {
        "mode": mode,
        "n60": int(defs.get("n_pile_60", 0)),
        "n120": int(defs.get("n_pile_120", 0)),
        "n480": 0,
        "ultra_on": "否",
        "ultra_allowed": False,
        "kx": DEMAND_COEFF_KX_DEFAULT,
        "kt": SIMULTANEITY_KT_DEFAULT,
        "cos_phi": PLAN_COS_PHI_DEFAULT,
        "eta": PLAN_LOAD_ETA_DEFAULT,
    }

    if mode == CHARGER_CONFIG_MODE_B:
        return render_custom_chargers_storage_planner(
            sk,
            kva_remaining=kva_remaining,
            kva_existing=kva_existing,
            parking_spots=parking_spots,
            defs=defs,
        )

    _html_substep_title("a. 按剩余容量规划（一般客户）")
    st.caption(f"第一步剩余可用变压器容量：**{kva_remaining:.1f} kVA**（配电公式直接采用该值）。")

    with st.expander("配电计算公式与系数说明", expanded=False):
        st.markdown(CHARGER_PLAN_FORMULA_MD)
        st.markdown(KT_SIMULTANEITY_NOTE)

    c1, c2, c3 = st.columns(3)
    with c1:
        kt_level = st.selectbox(
            "同时使用率",
            options=KT_LEVEL_OPTIONS,
            index=_select_index(
                KT_LEVEL_OPTIONS,
                str(st.session_state.get(sk + "kt_level", KT_LEVEL_MID)),
                KT_LEVEL_MID,
            ),
            help=(
                "Kt=同时启用的桩功率占比。1=满载同时；高0.7–0.9；低0.6–0.7；"
                "中间值0.7为一般规划默认，有实测值时按实际填写。"
            ),
            key=sk + "kt_level",
        )
        kt = kt_from_level(kt_level)
    with c2:
        kx = float(
            st.number_input(
                "需要系数 Kx",
                min_value=DEMAND_COEFF_KX_MIN,
                max_value=DEMAND_COEFF_KX_MAX,
                step=0.05,
                help="实际输出功率/额定功率，常取 0.4–0.85",
                key=sk + "kx",
            )
        )
    with c3:
        cos_phi = float(
            st.number_input(
                "功率因数 cosΦ",
                min_value=PLAN_COS_PHI_MIN,
                max_value=PLAN_COS_PHI_MAX,
                step=0.01,
                help="补偿后常取 0.9–0.95",
                key=sk + "cos_phi",
            )
        )
    eta = float(
        st.number_input(
            "负载率 η",
            min_value=PLAN_LOAD_ETA_MIN,
            max_value=PLAN_LOAD_ETA_MAX,
            step=0.05,
            help="推荐 0.7–0.8",
            key=sk + "eta",
        )
    )
    _render_planning_kt_feedback(kt)

    need_kva_480 = required_transformer_kva(
        PILE_RATED_KW_ULTRA_PER_SET, kx, kt, cos_phi, eta
    )
    spots_ok_for_480 = int(parking_spots) >= ULTRA_PARKING_SPOTS_PER_SET
    kva_ok_for_480 = need_kva_480 <= float(kva_remaining) + 1e-6
    ultra_allowed = kva_ok_for_480 and spots_ok_for_480
    max_kw = max_pile_kw_from_remaining_kva(kva_remaining, kx, kt, cos_phi, eta)

    _html_substep_title("b. 分体式液冷 480kW（选填）")
    st.caption(
        f"先确认是否配置 **1 套** 480kW 液冷超充（默认 **{ULTRA_480_DEFAULT_PRICE_WAN:.0f} 万元/套**），"
        f"再据此生成 60/120 最优搭配（120kW 双枪每台占 2 车位）。"
    )
    if ultra_allowed:
        want_480 = st.selectbox(
            "是否配置分体式液冷 480kW（1 套）",
            options=HP_OPTIONS,
            index=_select_index(HP_OPTIONS, str(st.session_state.get(sk + "want_480", "否")), "否"),
            help="选「是」时在容量允许前提下优先纳入 1 套 480kW，并自动搭配 60/120kW。",
            key=sk + "want_480",
        )
    else:
        reasons: list[str] = []
        if not kva_ok_for_480:
            reasons.append(
                f"1 套约需 <strong>{need_kva_480:.1f} kVA</strong>，超过剩余 "
                f"<strong>{kva_remaining:.1f} kVA</strong>（需增容或加储能）"
            )
        if not spots_ok_for_480:
            reasons.append(
                f"车位 <strong>{parking_spots}</strong> 个，不足 480kW 占用的 "
                f"<strong>{ULTRA_PARKING_SPOTS_PER_SET}</strong> 位"
            )
        st.markdown(
            '<div class="grid-declaration-banner" style="margin:0.5rem 0;">'
            '<div class="grid-declaration-title">480kW 暂不可选</div>'
            f'<p class="grid-declaration-body">{"；".join(reasons)}。</p></div>',
            unsafe_allow_html=True,
        )
        st.selectbox(
            "是否配置分体式液冷 480kW（1 套）",
            options=HP_OPTIONS,
            index=0,
            disabled=True,
            key=sk + "want_480_locked",
        )
        want_480 = "否"
    include_480 = want_480 == "是"

    _html_substep_title("c. 60 / 120 / 480 最优搭配")

    opt = optimize_charger_mix(
        kva_remaining,
        include_480=include_480,
        kx=kx,
        kt=kt,
        cos_phi=cos_phi,
        eta=eta,
        parking_spots=parking_spots,
    )

    st.markdown(
        f'<p class="ui-tone-stat">按系数测算：允许桩总功率约 <strong>{max_kw:.0f} kW</strong>。</p>',
        unsafe_allow_html=True,
    )
    st.markdown(_format_charger_plan_summary(opt, kva_remaining, parking_spots))
    with st.expander("推荐搭配 · 计算过程", expanded=True):
        st.markdown(_format_charger_plan_calc_process(opt, include_480=include_480))

    if opt["total_kw"] <= 0 and kva_remaining > 0:
        st.warning("当前剩余容量与系数下无法布置充电桩，建议 **增容**、**加储能** 或放宽 Kx/Kt。")

    out.update(opt)
    out.update(
        {
            "mode": mode,
            "n60": int(opt["n60"]),
            "n120": int(opt["n120"]),
            "n480": int(opt["n480"]),
            "ultra_on": "是" if opt["n480"] > 0 else "否",
            "ultra_allowed": ultra_allowed,
            "kx": kx,
            "kt": kt,
            "cos_phi": cos_phi,
            "eta": eta,
            "max_kw": max_kw,
        }
    )
    return out


def charger_recommend_stub_text(
    city: str,
    vehicle: str,
    layout: str,
    farthest_m: float,
    spots: int,
    usable_kw: float,
    kva_remaining: float,
    power_factor: float,
    kva_rated: float,
    upgrade_mode: str,
) -> str:
    """占位：后续可按城市、车型、距离与变压器方案自动推荐选型与桩数。"""
    if spots <= 0 or usable_kw <= 0:
        return "**推荐占位：** 请先在第一步确认车位数量，并填写剩余可用容量与功率因数。"
    hints: list[str] = []
    hints.append(
        f"储充可用约 **{usable_kw:.0f} kW**（剩余 **{kva_remaining:.0f} kVA** × 功率因数 **{power_factor:.2f}**）。"
    )
    if kva_rated > 0:
        hints.append(f"变压器额定 **{kva_rated:.0f} kVA**（选填备案）。")
    if usable_kw < 500:
        hints.append("配电余量偏小：分体式液冷480kW 建议谨慎配置，可优先考虑 60/120kW 组合。")
    elif usable_kw >= 1200:
        hints.append("变压器容量充裕：在满足车位布局前提下，可考虑 **120kW 双枪** 或 **分体式液冷480kW**。")
    approx = max(2, spots // 3)
    hints.append(f"泊位 **{spots}**（**{vehicle}**）占位建议常规快充 **≈ {approx} 台** 量级起步。")
    if upgrade_mode == "不增容":
        hints.append("增容方案：**不增容**，测算按现有 kVA 计配电，不叠加 ΔkVA 投资。")
    elif upgrade_mode == "原址换大":
        hints.append("增容方案：**原址换大**，请在下方填写目标总容量。")
    else:
        hints.append("增容方案：**高压申请**，目标容量与外线投资需结合电网批复单独校核。")
    if farthest_m > 0:
        hints.append(f"变压器至最远车位约 **{farthest_m:.0f} m**（已联动线缆长度初值，可再改）。")
    if layout:
        hints.append(f"场站环境：**{layout}**。")
    if city.strip():
        hints.append(f"场站地点：**{city.strip()}**。")
    return "**推荐占位（非最终方案）：** " + " ".join(hints)


def _html_column_title(text: str) -> None:
    """左右主栏标题（栏级，低于页面 title）。"""
    st.markdown(f'<div class="ui-column-title">{text}</div>', unsafe_allow_html=True)


def _html_step_title(text: str) -> None:
    """五步流程主标题（H1）。"""
    st.markdown(f'<div class="ui-h1-step">{text}</div>', unsafe_allow_html=True)


def _html_substep_title(text: str) -> None:
    """步内流程小节（H2，按使用顺序编号）。"""
    st.markdown(f'<div class="ui-h2-substep">{text}</div>', unsafe_allow_html=True)


def _html_block_title(text: str) -> None:
    """工程块 / 投资分项等分类标题（H3）。"""
    st.markdown(f'<div class="ui-h3-block">{text}</div>', unsafe_allow_html=True)


def _html_result_zone_title(text: str) -> None:
    """右侧结果区按阅读顺序分区（H2）。"""
    st.markdown(f'<div class="ui-h2-result-zone">{text}</div>', unsafe_allow_html=True)


def _html_module_title(text: str) -> None:
    """右侧一级模块标题（正向 / 逆向）。"""
    st.markdown(f'<div class="ui-h1-module">{text}</div>', unsafe_allow_html=True)


def _html_flow_zone_open(title: str | None = None) -> None:
    """步内小节：仅显示 H2 标题，不用虚线框。"""
    if title:
        _html_substep_title(title)


def _html_flow_zone_close() -> None:
    pass


def _html_step_divider() -> None:
    """五步之间的实线分隔。"""
    st.markdown('<hr class="ui-step-divider" />', unsafe_allow_html=True)


def _html_section_title(text: str) -> None:
    """兼容旧调用 → 五步主标题。"""
    _html_step_title(text)


def _html_part_divider() -> None:
    """兼容旧调用 → 五步之间分隔。"""
    _html_step_divider()


def cycles_from_strategy(strategy: str) -> float:
    """充放策略 → 日循环次数。"""
    s = str(strategy).strip()
    if s == CYCLE_STRATEGY_2:
        return 2.0
    if s == CYCLE_STRATEGY_15:
        return 1.5
    return 1.0


def strategy_from_cycles(cycles: float) -> str:
    c = float(cycles)
    if c >= 1.75:
        return CYCLE_STRATEGY_2
    if c >= 1.25:
        return CYCLE_STRATEGY_15
    return CYCLE_STRATEGY_1


def total_installed_charger_kw(n60: int, n120: int, n_ultra: int) -> float:
    """充电桩装机功率合计（kW）= 各型号数量 × 额定功率。"""
    return (
        max(0, int(n60)) * PILE_RATED_KW_60
        + max(0, int(n120)) * PILE_RATED_KW_120
        + max(0, int(n_ultra)) * PILE_RATED_KW_ULTRA_PER_SET
    )


def _render_grid_declaration_notice(total_kw: float | None = None) -> None:
    """
    页面上高亮提示：装机功率超过 160kW 需向电网申报。
    total_kw 为 None 时仅展示规则；超过阈值时使用 alert 样式并显示当前合计。
    """
    th = GRID_DECLARATION_THRESHOLD_KW
    over = total_kw is not None and total_kw > th
    alert_cls = " alert" if over else ""
    if over:
        detail = (
            f"<p class=\"grid-declaration-body\">当前表单充电桩<strong>装机合计约 "
            f"{total_kw:,.0f} kW</strong>，已超过 <strong>{th:.0f} kW</strong>。"
            "请同步安排<strong>电网报装/接入方案审查</strong>，本工具测算未包含报装周期与相关费用。</p>"
        )
    elif total_kw is not None:
        detail = (
            f"<p class=\"grid-declaration-body\">当前充电桩装机合计约 <strong>{total_kw:,.0f} kW</strong>，"
            f"未超过 {th:.0f} kW 申报线（按额定功率粗算，以实际并网方案为准）。</p>"
        )
    else:
        detail = (
            f"<p class=\"grid-declaration-body\">按桩型数量×额定功率粗算装机；"
            f"合计超过 <strong>{th:.0f} kW</strong> 时须向电网公司办理接入申报。</p>"
        )
    st.markdown(
        f'<div class="grid-declaration-banner{alert_cls}">'
        f'<div class="grid-declaration-title">⚠ 电网接入提示</div>'
        f"<p class=\"grid-declaration-lead\">"
        f"充电设施<strong>装机功率合计超过 {th:.0f} kW</strong>时，"
        f"需按当地要求<strong>向电网公司申报</strong>（报装容量、接入方式、保护整定等）。"
        f"</p>{detail}</div>",
        unsafe_allow_html=True,
    )


# ----------------------------
# 本地默认配置文件（可被页面「在线保存」修改）
# ----------------------------
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "cost_defaults.json"

# （分组中文, 字段说明, JSON 字段名, streamlit/editor 简易类型标记）
PARAMETER_DEF_ROWS: list[tuple[str, str, str, str]] = [
    ("场站信息", "场站地点(城市/行政区/项目名)", "station_city", "opt_str"),
    ("场站信息", "车位规格(乘用车/重卡)", "parking_vehicle_type", "str"),
    ("场站信息", "场地车位规格说明(派生/可改)", "parking_slot_size_desc", "opt_str"),
    ("场站信息", "可建设停车位数量(个)", "parking_spots", "int"),
    ("场站信息", "变压器增容方式", "transformer_upgrade_mode", "str"),
    ("场站信息", "变压器到最远车位距离(m)", "transformer_to_farthest_m", "float"),
    ("场站信息", "场站环境(室内/室外)", "site_layout", "str"),
    ("电价与变压器", "高峰电价(元/kWh)", "peak_price", "float"),
    ("电价与变压器", "低谷电价(元/kWh)", "valley_price", "float"),
    ("电价与变压器", "平时电价(元/kWh)", "flat_price", "float"),
    ("场站信息", "现有变压器额定容量(kVA,选填)", "kva_existing", "float"),
    ("场站信息", "剩余可用变压器容量(kVA,必填)", "kva_remaining_available", "float"),
    ("场站信息", "功率因数(0.8-0.9)", "power_factor", "float"),
    ("电价与变压器", "目标变压器总容量(kVA)", "kva_target", "float"),
    ("电价与变压器", "变压器增容单价(元/kVA)", "transformer_unit_price", "float"),
    ("充电桩", "是否配置分体式液冷480kW", "ultra_480_enabled", "str"),
    ("充电桩", "60kW 桩(台)", "n_pile_60", "int"),
    ("充电桩", "60kW 单价(万元/台)", "price_pile_60_wan", "float"),
    ("充电桩", "120kW 双枪(台)", "n_pile_120", "int"),
    ("充电桩", "120kW 单价(万元/台)", "price_pile_120_wan", "float"),
    ("充电桩", f"480kW液冷超充(套,每套{ULTRA_CHARGE_TERMINALS_PER_RACK}终端)", "n_ultra_480", "int"),
    ("充电桩", "480kW 单价(万元/套)", "price_ultra_480_wan", "float"),
    ("储能", "是否安装工商业储能", "storage_installed", "str"),
    ("储能", "充放策略", "cycle_strategy", "str"),
    ("储能", "储能主要用途", "usage", "usage"),
    ("储能", "储能套数", "storage_sets", "int"),
    ("储能", "单套容量(kWh)", "storage_kwh_per_set", "float"),
    ("储能", "储能单价(元/kWh)", "price_storage_per_kwh", "float"),
    ("储能", "削减峰值功率(kW,纯动态增容)", "peak_reduction_kw", "float"),
    ("储能", "需量/基本电价(元/kW·月,纯动态增容)", "basic_electricity_yuan_per_kw_month", "float"),
    (
        "储能",
        "年节省基本电费手填覆盖(万元/年,>0时优先于上式)",
        "basic_electricity_saved_wan_per_year",
        "float",
    ),
    ("储能", "避开的变压器投资(万元,一次性,纯动态增容)", "transformer_avoided_wan", "float"),
    ("储能", "变压器投资折旧年限(年)", "transformer_avoided_depreciation_years", "int"),
    (
        "储能",
        "模式三测算·超载时长上限(小时/天,≤则适用削峰)",
        "mode3_peak_hours_max",
        "float",
    ),
    (
        "储能",
        "模式三测算·混合策略价差阈值(元/kWh,峰−谷大于则建议模式二+削峰)",
        "mode3_spread_hybrid_yuan",
        "float",
    ),
    ("施工", "400V并网柜数量(台)", "chg_eng_ggd_qty", "int"),
    ("施工", "400V并网柜单价(元/台)", "chg_eng_ggd_unit_yuan", "float"),
    ("施工", "关口计量表数量(块)", "chg_eng_meter_qty", "int"),
    ("施工", "储能·防逆流电表数量(块)", "sto_eng_anti_meter_qty", "int"),
    ("施工", "储能·RS485单价(元/m)", "sto_eng_rs485_unit_yuan", "float"),
    ("施工", "储能·整系统调试(元)", "sto_eng_system_debug_yuan", "float"),
    ("施工", "单车位土建(万元/位)", "parking_cost_per_spot_wan", "float"),
    ("施工", "相关物料·其他杂项(万元)", "other_construction_wan", "float"),
    ("施工", "微网控制器设备(万元)", "microgrid_controller_equip_wan", "float"),
    ("施工", "微网控制器调试(万元)", "microgrid_controller_debug_wan", "float"),
    ("施工", "微网控制器数量(个)", "chg_eng_microgrid_qty", "int"),
    ("施工", "微网控制器单价(元/个)", "chg_eng_microgrid_unit_yuan", "float"),
    ("施工", "微网调试天数(天)", "chg_eng_microgrid_debug_days", "float"),
    ("施工", "微网调试单价(元/天)", "chg_eng_microgrid_debug_unit_yuan", "float"),
    ("运营与测算", "年运营天数", "operating_days", "float"),
    ("运营与测算", "充电服务费(元/kWh)", "service_fee_per_kwh", "float"),
    ("运营与测算", "常规单枪日均(kWh)", "energy_per_gun_day", "float"),
    ("运营与测算", "超充单枪日均(kWh/终端)", "ultra_energy_per_gun_day", "float"),
    ("运营与测算", "储能日循环(次/天)", "storage_cycles_per_day", "float"),
    ("运营与测算", "储能效率(0–1)", "round_trip_efficiency", "float"),
    ("运营与测算", "年运维占总投资(%) — 仅存百分数数字", "annual_opex_percent", "float"),
    ("运营与测算", "VPP/需求响应(万元/年)", "vpp_revenue_wan", "float"),
    ("运营与测算", "政府补贴方式(无/一次性/分期)", "gov_subsidy_mode", "str"),
    ("运营与测算", "政府补贴一次性(万元)", "gov_subsidy_once_wan", "float"),
    ("运营与测算", "政府补贴分期(万元/年)", "gov_subsidy_annual_wan", "float"),
    ("运营与测算", "政府补贴分期年限(年)", "gov_subsidy_years", "int"),
    ("运营与测算", "现金流/IRR年限(年)", "horizon_years", "int"),
    ("经营指标", "是否计算利润表/现金流/财务评价", "calc_financial_statements", "bool"),
    ("经营指标", "增值税率(0–1)", "vat_rate", "float"),
    ("经营指标", "所得税率(0–1)", "income_tax_rate", "float"),
    ("经营指标", "折现率(0–1)", "discount_rate", "float"),
    ("经营指标", "折旧年限(年)", "depreciation_years", "int"),
    ("经营指标", "残值率(0–1)", "salvage_rate", "float"),
    ("经营指标", "辅助服务次数(次/年)", "ancillary_times_per_year", "float"),
    ("经营指标", "辅助服务单价(元/次)", "ancillary_unit_price_yuan", "float"),
]

DEFAULT_PARAM_KEYS = [row[2] for row in PARAMETER_DEF_ROWS]


def _builtin_defaults_raw() -> dict[str, Any]:
    """内置参数（配置文件缺失或未包含某键时使用）。"""
    return {
        "station_city": "",
        "parking_vehicle_type": "乘用车",
        "parking_slot_size_desc": "",
        "parking_spots": 0,
        "transformer_upgrade_mode": "不增容",
        "transformer_to_farthest_m": 0.0,
        "site_layout": "室外",
        "peak_price": 1.2,
        "valley_price": 0.3,
        "flat_price": 0.7,
        "kva_existing": 0.0,
        "kva_remaining_available": 0.0,
        "power_factor": POWER_FACTOR_DEFAULT,
        "kva_target": 0.0,
        "ultra_480_enabled": "否",
        "charger_config_mode": CHARGER_CONFIG_MODE_A,
        "demand_coeff_kx": DEMAND_COEFF_KX_DEFAULT,
        "simultaneity_coeff_kt": SIMULTANEITY_KT_DEFAULT,
        "planning_cos_phi": PLAN_COS_PHI_DEFAULT,
        "planning_load_rate_eta": PLAN_LOAD_ETA_DEFAULT,
        "storage_installed": STORAGE_INSTALL_NO,
        "cycle_strategy": CYCLE_STRATEGY_1,
        "usage": USAGE_HYBRID,
        "n_pile_60": 0,
        "price_pile_60_wan": PILE_60_DEFAULT_PRICE_WAN,
        "n_pile_120": 0,
        "price_pile_120_wan": PILE_120_DEFAULT_PRICE_WAN,
        "n_ultra_480": 0,
        "price_ultra_480_wan": ULTRA_480_DEFAULT_PRICE_WAN,
        "storage_sets": 0,
        "storage_kwh_per_set": STORAGE_KWH_PER_SET_DEFAULT,
        "price_storage_per_kwh": 0.65,
        "peak_reduction_kw": 200.0,
        "basic_electricity_yuan_per_kw_month": 35.0,
        "basic_electricity_saved_wan_per_year": 0.0,
        "transformer_avoided_wan": 0.0,
        "transformer_avoided_depreciation_years": 15,
        "mode3_peak_hours_max": MODE3_PEAK_HOURS_MAX_DEFAULT,
        "mode3_spread_hybrid_yuan": MODE3_SPREAD_HYBRID_YUAN_DEFAULT,
        "transformer_unit_price": 400.0,
        "cable_len_m": 0.0,
        "parking_cost_per_spot_wan": 1.2,
        "other_construction_wan": 0.0,
        "microgrid_controller_equip_wan": 0.0,
        "microgrid_controller_debug_wan": 0.0,
        **CHARGER_ENG_BUILTIN_DEFAULTS,
        **STORAGE_ENG_BUILTIN_DEFAULTS,
        "operating_days": 330.0,
        "service_fee_per_kwh": 0.4,
        "energy_per_gun_day": 200.0,
        "ultra_energy_per_gun_day": 250.0,
        "storage_cycles_per_day": 1.0,
        "round_trip_efficiency": 0.9,
        "annual_opex_percent": 2.0,
        "vpp_revenue_wan": 0.0,
        "gov_subsidy_mode": GOV_SUBSIDY_NONE,
        "gov_subsidy_once_wan": 0.0,
        "gov_subsidy_annual_wan": 0.0,
        "gov_subsidy_years": 5,
        "horizon_years": 15,
        "calc_financial_statements": False,
        "vat_rate": DEFAULT_VAT_RATE,
        "income_tax_rate": DEFAULT_INCOME_TAX_RATE,
        "discount_rate": DEFAULT_DISCOUNT_RATE,
        "depreciation_years": DEFAULT_DEPRECIATION_YEARS,
        "salvage_rate": DEFAULT_SALVAGE_RATE,
        "ancillary_times_per_year": 0.0,
        "ancillary_unit_price_yuan": 0.0,
    }


def _coerce_scalar(key: str, raw: Any, kind: str) -> Any:
    """将单行配置安全转为 Python 类型（用于载入 JSON / 编辑器回写）。"""
    if kind == "opt_str":
        if raw is None:
            return ""
        return str(raw).strip()

    if isinstance(raw, str) and raw.strip() == "":
        raw = None
    if raw is None:
        raise ValueError(f"字段 {key} 不能为空")

    if key in ("high_power_fast_charge", "ultra_480_enabled"):
        s = str(raw).strip()
        if s not in HP_OPTIONS:
            raise ValueError("必须为「否」或「是」")
        return s

    if key == "parking_vehicle_type":
        s = str(raw).strip()
        if s not in PARKING_VEHICLE_OPTIONS:
            raise ValueError(f"必须为 {PARKING_VEHICLE_OPTIONS}")
        return s

    if key == "transformer_upgrade_mode":
        s = str(raw).strip()
        if s not in TRANSFORMER_UPGRADE_OPTIONS:
            raise ValueError(f"必须为 {TRANSFORMER_UPGRADE_OPTIONS}")
        return s

    if key == "site_layout":
        s = str(raw).strip()
        if s not in SITE_LAYOUT_OPTIONS:
            raise ValueError(f"必须为 {SITE_LAYOUT_OPTIONS}")
        return s

    if key == "storage_installed":
        s = str(raw).strip()
        if s not in STORAGE_INSTALL_OPTIONS:
            raise ValueError(f"必须为 {STORAGE_INSTALL_OPTIONS}")
        return s

    if key == "cycle_strategy":
        s = str(raw).strip()
        if s not in CYCLE_STRATEGY_OPTIONS:
            raise ValueError(f"必须为 {CYCLE_STRATEGY_OPTIONS}")
        return s

    if kind == "usage" or key == "usage":
        s = str(raw).strip()
        if s == USAGE_HYBRID_LEGACY:
            s = USAGE_HYBRID
        if s not in USAGE_OPTIONS:
            raise ValueError(f"必须为 {USAGE_OPTIONS}")
        return s

    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        return s in ("1", "true", "yes", "on", "是")

    if kind == "int":
        return int(float(raw))

    if kind == "float":
        return float(raw)

    if kind == "str":
        return str(raw).strip()

    raise ValueError(f"未知类型 kind={kind}")


def _sanitize_defaults_bundle(d: dict[str, Any]) -> dict[str, Any]:
    """类型转换 + 基本范围兜底。"""
    merged = {**_builtin_defaults_raw(), **d}
    # 旧版「大功率配电增配套餐」键迁移为液冷480kW开关
    if "ultra_480_enabled" not in merged and "high_power_fast_charge" in merged:
        merged["ultra_480_enabled"] = merged.get("high_power_fast_charge", "否")
    merged.pop("high_power_fast_charge", None)
    merged.pop("high_power_extra_wan", None)
    rd = {r[2]: r for r in PARAMETER_DEF_ROWS}
    out: dict[str, Any] = {}
    for key in DEFAULT_PARAM_KEYS:
        kind = rd[key][3]
        out[key] = _coerce_scalar(key, merged[key], kind)

    eta = float(out.get("round_trip_efficiency", 0.9))
    out["round_trip_efficiency"] = float(np.clip(eta, 0.0, 1.0))

    hy = int(out.get("horizon_years", 15))
    out["horizon_years"] = int(np.clip(hy, 3, 40))

    if out.get("ultra_480_enabled") not in HP_OPTIONS:
        out["ultra_480_enabled"] = "否"
    if out.get("parking_vehicle_type") not in PARKING_VEHICLE_OPTIONS:
        out["parking_vehicle_type"] = "乘用车"
    if out.get("transformer_upgrade_mode") not in TRANSFORMER_UPGRADE_OPTIONS:
        out["transformer_upgrade_mode"] = "不增容"
    if out.get("site_layout") not in SITE_LAYOUT_OPTIONS:
        out["site_layout"] = "室外"
    if out.get("storage_installed") not in STORAGE_INSTALL_OPTIONS:
        out["storage_installed"] = STORAGE_INSTALL_NO
    if out.get("cycle_strategy") not in CYCLE_STRATEGY_OPTIONS:
        out["cycle_strategy"] = CYCLE_STRATEGY_1
    if not str(out.get("parking_slot_size_desc", "")).strip():
        out["parking_slot_size_desc"] = _slot_desc_from_vehicle(str(out["parking_vehicle_type"]))
    u = out.get("usage")
    if u == USAGE_HYBRID_LEGACY:
        out["usage"] = USAGE_HYBRID
    elif u not in USAGE_OPTIONS:
        out["usage"] = USAGE_HYBRID

    dep = int(out.get("transformer_avoided_depreciation_years", 15))
    out["mode3_peak_hours_max"] = float(
        np.clip(float(out.get("mode3_peak_hours_max", MODE3_PEAK_HOURS_MAX_DEFAULT)), 0.5, 12.0)
    )
    out["mode3_spread_hybrid_yuan"] = float(
        np.clip(
            float(out.get("mode3_spread_hybrid_yuan", MODE3_SPREAD_HYBRID_YUAN_DEFAULT)),
            0.0,
            10.0,
        )
    )
    out["transformer_avoided_depreciation_years"] = int(np.clip(dep, 1, 40))

    pf = float(out.get("power_factor", POWER_FACTOR_DEFAULT))
    out["power_factor"] = float(np.clip(pf, POWER_FACTOR_MIN, POWER_FACTOR_MAX))

    rated = float(out.get("kva_existing", 0.0))
    if rated > 0 and rated not in TRANSFORMER_RATED_KVA_OPTIONS:
        out["kva_existing"] = 0.0

    return out


def load_site_cost_defaults() -> dict[str, Any]:
    """
    合并顺序：内置默认 ← 磁盘 JSON（仅覆盖合法键）。
    若文件损坏或键非法，则忽略该键并回退内置。
    """
    base = _builtin_defaults_raw()
    if not CONFIG_PATH.is_file():
        return _sanitize_defaults_bundle(base)

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _sanitize_defaults_bundle(base)
        merged = {**base, **{k: v for k, v in data.items() if k in DEFAULT_PARAM_KEYS}}
        return _sanitize_defaults_bundle(merged)
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return _sanitize_defaults_bundle(base)


def save_site_cost_defaults(new_values: dict[str, Any]) -> None:
    """原子写入 JSON，避免中途失败导致文件损坏。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: new_values[k] for k in DEFAULT_PARAM_KEYS if k in new_values}
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(CONFIG_PATH)


def defaults_dict_to_editor_df(defs: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for grp, lbl, key, _kind in PARAMETER_DEF_ROWS:
        rows.append(
            {
                "分组": grp,
                "参数": lbl,
                "字段键": key,
                "默认值": defs.get(key, _builtin_defaults_raw()[key]),
            }
        )
    return pd.DataFrame(rows)


def editor_df_to_defaults_dict(df: pd.DataFrame) -> dict[str, Any]:
    """从编辑后的表还原为配置字典；按「字段键」列映射。"""
    rd = {r[2]: r for r in PARAMETER_DEF_ROWS}
    out: dict[str, Any] = {}
    errs: list[str] = []
    for _, row in df.iterrows():
        key = str(row["字段键"]).strip()
        if key not in rd:
            errs.append(f"未知字段键：{key}")
            continue
        kind = rd[key][3]
        raw = row["默认值"]
        try:
            out[key] = _coerce_scalar(key, raw, kind)
        except Exception as e:
            errs.append(f"{key}: {e}")
    if errs:
        raise ValueError("\n".join(errs))
    return _sanitize_defaults_bundle({**_builtin_defaults_raw(), **out})


def render_site_defaults_editor() -> None:
    """
    在线编辑并写回 `config/cost_defaults.json`。
    保存后 `st.rerun()`，左侧表单将使用新默认初值。
    """
    st.markdown("---")
    with st.expander("默认成本参数（本地配置文件，可在线修改）", expanded=False):
        st.caption(
            f"配置文件路径：`{CONFIG_PATH}`"
            " — 保存后下次打开或点击 **项目正向收益及回收期测算** 前即按新默认填表（Streamlit 会整页重跑）。"
        )
        current = load_site_cost_defaults()
        df0 = defaults_dict_to_editor_df(current)
        edited = st.data_editor(
            df0,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            column_config={
                "分组": st.column_config.TextColumn("分组", disabled=True, width="small"),
                "参数": st.column_config.TextColumn("参数说明", disabled=True, width="large"),
                "字段键": st.column_config.TextColumn("内部键", disabled=True, width="small"),
                "默认值": st.column_config.TextColumn("默认值（可改）", width="medium"),
            },
            key="site_cost_defaults_editor",
        )
        b1, b2, b3 = st.columns([1, 1, 2])
        if b1.button("保存到本地配置文件", type="primary"):
            try:
                new_d = editor_df_to_defaults_dict(edited)
                save_site_cost_defaults(new_d)
                st.success("已写入 config/cost_defaults.json")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        if b2.button("从磁盘重新加载（放弃未保存的表内编辑）"):
            st.rerun()
        b3.caption("也可直接用编辑器打开 JSON；保存后点「从磁盘重新加载」。")


def _inject_global_style() -> None:
    """浅色工业极简：留白、细线分割、单色强调。"""
    st.markdown(
        r"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  html, body, [class*="css"] {
    font-family: 'Instrument Sans', system-ui, sans-serif !important;
  }
  .block-container {
    padding-top: 1.25rem;
    padding-bottom: 2rem;
    max-width: 1580px;
  }
  h1 {
    letter-spacing: -0.03em;
    font-weight: 700;
    color: #18181b;
  }
  h2 { color: #27272a; font-weight: 600; letter-spacing: -0.02em; }
  h3, h4 { color: #3f3f46; font-weight: 600; }
  div[data-testid="stVerticalBlock"] > div:first-child hr {
    opacity: 0.35;
  }
  section[data-testid="stSidebar"] { display: none !important; }
  div[data-testid="collapsedControl"] { display: none !important; }
  .stApp {
    background: linear-gradient(180deg, #f9fafb 0%, #eceff2 65%, #eef1f4 100%);
    color: #27272a;
  }
  div[data-testid="stDecoration"] { visibility: hidden; height: 0; }
  /* 去掉详细测算区内层 form 灰框，第三/四部分与第二部分版式一致 */
  div[data-testid="column"] form[data-testid="stForm"] {
    border: none !important;
    background: transparent !important;
    box-shadow: none !important;
    padding: 0 !important;
    margin: 0 !important;
  }
  /* 左右主分栏卡片（Streamlit 列容器）——首行二分栏通常为参数/结果 */
  div[data-testid="stHorizontalBlock"]:nth-of-type(1) > div[data-testid="column"] {
    background-color: #ffffff;
    border: 1px solid #dae0ea;
    border-radius: 6px;
    padding: 14px 16px 18px;
    box-shadow: 0 1px 2px rgba(15,23,42,0.04);
    max-height: min(92vh, 920px);
    overflow-y: auto;
  }
  div[data-testid="stHorizontalBlock"]:nth-of-type(1) > div[data-testid="column"]:first-child {
    background-color: #f8fafc;
    flex: 1.72 1 0% !important;
    min-width: 56% !important;
  }
  div[data-testid="stHorizontalBlock"]:nth-of-type(1) > div[data-testid="column"]:last-child {
    flex: 1 1 0% !important;
    min-width: 34% !important;
  }
  hr.ui-step-divider,
  hr.section-part-divider {
    border: none;
    border-top: 2px solid #64748b;
    margin: 1.5rem 0 1.25rem;
  }
  .ui-column-title {
    font-size: 1.05rem;
    font-weight: 700;
    color: #475569;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    margin: 0 0 0.65rem;
    padding-bottom: 0.35rem;
    border-bottom: 1px solid #cbd5e1;
  }
  .ui-h1-module {
    font-size: 1.28rem;
    font-weight: 800;
    color: #0f172a;
    margin: 1.75rem 0 0.85rem;
    padding: 0.35rem 0 0.45rem;
    border-bottom: 3px solid #334155;
    letter-spacing: 0.02em;
  }
  div[data-testid="column"] .ui-h1-module:first-of-type {
    margin-top: 0.15rem;
  }
  .ui-h1-step,
  .section-block-title {
    font-size: 1.22rem;
    font-weight: 800;
    color: #0f172a;
    letter-spacing: -0.02em;
    line-height: 1.35;
    margin: 0 0 0.55rem;
    padding: 0.35rem 0 0.45rem 0.65rem;
    border-left: 4px solid #0f766e;
    border-bottom: none;
  }
  .ui-h2-substep {
    font-size: 0.98rem;
    font-weight: 700;
    color: #1e293b;
    margin: 0.75rem 0 0.35rem;
    padding-bottom: 0;
    border-bottom: none;
  }
  .ui-h2-result-zone {
    font-size: 1.02rem;
    font-weight: 700;
    color: #0f172a;
    margin: 1.1rem 0 0.5rem;
    padding: 0.3rem 0 0.35rem 0.55rem;
    border-left: 3px solid #0369a1;
  }
  .ui-h3-block,
  .construction-subsection-title {
    font-size: 0.92rem;
    font-weight: 700;
    color: #334155;
    margin: 0.5rem 0 0.4rem;
    letter-spacing: -0.01em;
  }
  .part-sub-block {
    margin: 0.75rem 0 1rem;
    padding: 0.5rem 0 0.25rem;
  }
  .section-mini {
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: #64748b;
    margin: 10px 0 8px;
    padding-bottom: 4px;
    border-bottom: 1px solid #e4e8ef;
  }
  .metric-hint {
    font-size: 0.78rem;
    color: var(--ui-amber-text) !important;
    line-height: 1.45;
    display: block;
    margin: 0 0 0.5rem 0;
  }
  .kpi-wrap {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
    margin: 0 0 16px;
  }
  .kpi-card {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 12px 14px;
    box-shadow: 0 1px 2px rgba(15,23,42,0.04);
  }
  .kpi-card.alt {
    border-left: 3px solid #0f766e;
  }
  .kpi-card.alt2 {
    border-left: 3px solid #475569;
  }
  .kpi-label {
    color: #64748b;
    font-size: 0.8125rem;
    font-weight: 600;
  }
  .kpi-value {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 1.55rem;
    font-weight: 600;
    margin-top: 4px;
    color: #0f172a;
  }
  .kpi-unit { font-size: 0.85rem; color: #94a3b8; margin-left: 6px; }
  .kpi-sub {
    color: var(--ui-amber-text-soft);
    font-size: 0.78rem;
    margin-top: 4px;
    line-height: 1.35;
  }
  hr.page-hero-divider {
    border: none;
    border-top: 1px solid #d7dde8;
    margin: 0 0 14px;
  }
  /* 语义色：琥珀黄 = 备注 / 统计 / 联动 / 总结 / 高亮说明（与电网提示条同系） */
  :root {
    --ui-amber-text: #92400e;
    --ui-amber-text-strong: #78350f;
    --ui-amber-text-soft: #b45309;
    --ui-amber-bg: #fffbeb;
    --ui-amber-bg-mid: #fef3c7;
    --ui-amber-border: #f59e0b;
    --ui-neutral-meta: #64748b;
  }
  .ui-tone-note, .muted-hint {
    font-size: 0.8rem;
    color: var(--ui-amber-text) !important;
    line-height: 1.45;
    margin: 0 0 8px;
  }
  .ui-tone-note strong, .muted-hint strong {
    color: var(--ui-amber-text-strong);
    font-weight: 700;
  }
  .ui-tone-stat {
    font-size: 0.8rem;
    color: var(--ui-amber-text-soft) !important;
    font-weight: 600;
    line-height: 1.45;
    margin: 0.35rem 0 0.5rem;
  }
  .ui-tone-stat strong {
    color: var(--ui-amber-text-strong);
    font-weight: 800;
  }
  .ui-tone-link {
    font-size: 0.78rem;
    color: var(--ui-amber-text-soft) !important;
    line-height: 1.4;
    margin: 0.1rem 0 0.35rem;
  }
  .ui-tone-summary, .ui-section-total {
    font-size: 0.8rem;
    font-weight: 700;
    color: var(--ui-amber-text) !important;
    margin: 0.65rem 0 0.35rem;
    line-height: 1.45;
  }
  .ui-tone-summary strong, .ui-section-total strong {
    color: var(--ui-amber-text-strong);
    font-weight: 800;
  }
  .ui-tone-neutral {
    font-size: 0.78rem;
    color: var(--ui-neutral-meta) !important;
    line-height: 1.35;
    margin: 0 0 6px;
  }
  .ui-tone-note-line {
    border-left: 2px solid var(--ui-amber-border);
    padding-left: 0.55rem;
    margin: 0.2rem 0 0.55rem;
  }
  /* 避免 Streamlit 给 markdown/HTML 块加圆角白底（像空输入框/进度条） */
  [data-testid="stMarkdownContainer"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
  }
  [data-testid="stMarkdownContainer"] > div {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    border-radius: 0 !important;
  }
  [data-testid="stHtml"],
  [data-testid="stHtml"] > div {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    border-radius: 0 !important;
    padding: 0 !important;
  }
  div[data-testid="stHorizontalBlock"]:nth-of-type(1) [data-testid="stCaptionContainer"] p,
  div[data-testid="stHorizontalBlock"]:nth-of-type(1) [data-testid="stCaptionContainer"] {
    color: var(--ui-amber-text) !important;
    font-size: 0.8rem !important;
    line-height: 1.45 !important;
  }
  div[data-testid="stHorizontalBlock"]:nth-of-type(1) [data-testid="stCaptionContainer"] strong {
    color: var(--ui-amber-text-strong) !important;
    font-weight: 700;
  }
  [data-testid="stExpander"] [data-testid="stCaptionContainer"] p,
  [data-testid="stExpander"] [data-testid="stCaptionContainer"] {
    color: var(--ui-amber-text) !important;
    font-size: 0.8rem !important;
  }
  hr.construction-section-dash {
    border: none;
    border-top: 1px dashed #94a3b8;
    margin: 1rem 0 0.85rem;
    opacity: 0.95;
  }
  .revenue-calc-box {
    font-size: 0.84rem;
    color: var(--ui-amber-text-strong);
    line-height: 1.55;
    background: transparent;
    border: none;
    border-left: 3px solid var(--ui-amber-border);
    padding: 0 0 0 0.55rem;
    margin: 0.35rem 0 0.65rem;
  }
  .revenue-calc-box code {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.8rem;
    color: var(--ui-amber-text-soft);
  }
  .grid-declaration-banner {
    background: linear-gradient(105deg, #fffbeb 0%, #fef3c7 55%, #fde68a 100%);
    border: none;
    border-left: 4px solid #d97706;
    border-radius: 0;
    padding: 14px 18px;
    margin: 0 0 16px;
    box-shadow: 0 2px 10px rgba(217, 119, 6, 0.18);
  }
  .grid-declaration-banner.alert {
    background: linear-gradient(105deg, #fef2f2 0%, #fecaca 45%, #fca5a5 100%);
    border-color: #dc2626;
    border-left-color: #b91c1c;
    box-shadow: 0 3px 14px rgba(220, 38, 38, 0.22);
  }
  .grid-declaration-title {
    font-size: 1.08rem;
    font-weight: 800;
    color: #92400e;
    letter-spacing: -0.02em;
    margin-bottom: 6px;
  }
  .grid-declaration-banner.alert .grid-declaration-title {
    color: #991b1b;
  }
  .grid-declaration-lead {
    margin: 0 0 6px;
    font-size: 0.92rem;
    font-weight: 600;
    color: #78350f;
    line-height: 1.45;
  }
  .grid-declaration-banner.alert .grid-declaration-lead {
    color: #7f1d1d;
  }
  .grid-declaration-body {
    margin: 0;
    font-size: 0.86rem;
    color: var(--ui-amber-text-strong);
    line-height: 1.4;
  }
  .grid-declaration-banner.alert .grid-declaration-body {
    color: #450a0a;
    font-weight: 600;
  }
  .op-param-box {
    background: transparent;
    border: none;
    padding: 0.15rem 0 0.5rem;
    margin: 0.35rem 0 0.75rem;
  }
  .op-param-box-title {
    font-size: 0.95rem;
    font-weight: 700;
    color: #334155;
    margin-bottom: 8px;
  }
  .kt-microgrid-banner {
    background: linear-gradient(105deg, #fffbeb 0%, #fef3c7 55%, #fde68a 100%);
    border: none;
    border-left: 4px solid #d97706;
    border-radius: 0;
    padding: 12px 16px;
    margin: 0 0 12px;
    box-shadow: 0 2px 10px rgba(217, 119, 6, 0.18);
  }
  .kt-microgrid-banner.kt-microgrid-ok {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-left: 4px solid #94a3b8;
    box-shadow: none;
  }
  .kt-microgrid-banner-title {
    font-size: 0.95rem;
    font-weight: 800;
    color: #92400e;
    margin-bottom: 6px;
  }
  .kt-microgrid-banner p {
    margin: 0;
    font-size: 0.88rem;
    font-weight: 600;
    color: #78350f;
    line-height: 1.45;
  }
  .kt-microgrid-banner.kt-microgrid-ok p {
    color: #64748b;
    font-weight: 500;
  }
</style>
        """,
        unsafe_allow_html=True,
    )


@dataclass
class InputModel:
    """表单输入聚合（便于序列化进方案对比）。"""

    station_city: str
    parking_vehicle_type: str
    parking_slot_size_desc: str
    transformer_upgrade_mode: str
    transformer_to_farthest_m: float
    site_layout: str

    peak_price: float
    valley_price: float
    flat_price: float
    kva_existing: float
    kva_remaining_available: float
    power_factor: float
    kva_target: float
    ultra_480_enabled: str  # "否" / "是"
    charger_config_mode: str
    demand_coeff_kx: float
    simultaneity_coeff_kt: float
    planning_cos_phi: float
    planning_load_rate_eta: float
    storage_installed: str
    cycle_strategy: str
    usage: str
    n_pile_60: int
    n_pile_120: int
    price_pile_60_wan: float
    price_pile_120_wan: float
    n_ultra_480: int
    price_ultra_480_wan: float
    storage_sets: int
    price_storage_per_kwh: float
    storage_kwh_per_set: float
    peak_reduction_kw: float
    basic_electricity_yuan_per_kw_month: float
    basic_electricity_saved_wan_per_year: float
    transformer_avoided_wan: float
    transformer_avoided_depreciation_years: int
    transformer_unit_price: float
    charger_engineering: ChargerEngineeringInputs
    storage_engineering: StorageEngineeringInputs
    parking_spots: int
    parking_cost_per_spot_wan: float
    other_construction_wan: float
    microgrid_controller_equip_wan: float
    microgrid_controller_debug_wan: float
    operating_days: float
    service_fee_per_kwh: float
    energy_per_gun_day: float
    ultra_energy_per_gun_day: float
    storage_cycles_per_day: float
    round_trip_efficiency: float
    opex_ratio: float
    vpp_revenue_wan: float
    gov_subsidy_mode: str
    gov_subsidy_once_wan: float
    gov_subsidy_annual_wan: float
    gov_subsidy_years: int
    horizon_years: int
    calc_financial_statements: bool
    vat_rate: float
    income_tax_rate: float
    discount_rate: float
    depreciation_years: int
    salvage_rate: float
    ancillary_times_per_year: float
    ancillary_unit_price_yuan: float


REVENUE_METRIC_HINTS: dict[str, str] = {
    "充电服务费(对用户,毛)": "不含税口径下与利润表「充电服务费」一致；峰谷+充电模式已含在储充毛利内，不再单列。",
    "储充一体经营毛利(年化)": (
        "日毛利×运营天数；峰时段充电假设下："
        "日毛利=充电量×服务费+充电量×峰价−峰段网购−谷段储能充电"
        "=充电量×服务费+E×(峰−谷÷η)，E=min(供桩量,日充电)。"
    ),
    "储能峰谷套利(独立运行)": "装机×日循环×(峰−谷)×效率×天数（不含税）；随电池 SOH 逐年衰减。",
    "余电上网峰谷套利": "储能日可放超过桩需求部分×(峰−谷)×效率×天数。",
    "节省基本电费/需量(公式)": "动态增容值×需量电价×12；不含税。",
    "节省基本电费/需量(手填)": "覆盖公式估算的基本电费节省（万元/年→元）。",
    "避开变压器投资(年化)": "一次性避投金额÷折旧年限，计入其他经营收入。",
    "虚拟电厂/需求响应": "VPP 或需求响应年收入（不含税）。",
    "政府补贴(一次性,第1年)": "仅在第 1 个运营年计入现金流与收入；粗算 KPI「年净收益」不含此项。",
    "政府补贴(分期)": "在补贴年限内每年计入经营收入（不含税）；与现金流各年一致。",
}


def gov_subsidy_yuan_in_year(inp: InputModel, year_1based: int) -> float:
    """
    第 year 个运营年的政府补贴现金流（元，不含税）。
    一次性：仅第 1 年；分期：第 1..补贴年限 年。
    作者: jiali.qiu
    """
    y = max(1, int(year_1based))
    mode = str(inp.gov_subsidy_mode).strip()
    if mode == GOV_SUBSIDY_ONCE:
        return max(0.0, float(inp.gov_subsidy_once_wan)) * 1e4 if y == 1 else 0.0
    if mode == GOV_SUBSIDY_INSTALLMENT:
        cap = min(
            max(1, int(inp.gov_subsidy_years)),
            max(1, int(inp.horizon_years)),
        )
        if y <= cap:
            return max(0.0, float(inp.gov_subsidy_annual_wan)) * 1e4
    return 0.0


def _gov_subsidy_revenue_breakdown_lines(inp: InputModel) -> dict[str, float]:
    """收入分项展示用（与 compute_projection / 财报各年口径一致）。"""
    mode = str(inp.gov_subsidy_mode).strip()
    if mode == GOV_SUBSIDY_ONCE:
        v = gov_subsidy_yuan_in_year(inp, 1)
        return {"政府补贴(一次性,第1年)": v} if v > 1e-6 else {}
    if mode == GOV_SUBSIDY_INSTALLMENT:
        v = gov_subsidy_yuan_in_year(inp, 1)
        return {"政府补贴(分期)": v} if v > 1e-6 else {}
    return {}


def battery_soh_factor(year_1based: int) -> float:
    """第 year 年电池 SOH（1 起算）；超出表长取末年系数。"""
    y = max(1, int(year_1based))
    idx = min(y - 1, len(BATTERY_SOH_BY_YEAR) - 1)
    return float(BATTERY_SOH_BY_YEAR[idx])


def _html_metric_hint(key: str) -> None:
    hint = REVENUE_METRIC_HINTS.get(key)
    if hint:
        st.markdown(f'<span class="metric-hint">{hint}</span>', unsafe_allow_html=True)


def _daily_charge_kwh(inp: InputModel) -> float:
    """日总充电量(kWh)：60kW 1枪/台，120kW 2枪/台，480kW 按套×终端。"""
    n60 = max(0, int(inp.n_pile_60))
    n120 = max(0, int(inp.n_pile_120))
    n_ultra = max(0, int(inp.n_ultra_480))
    e_std = max(0.0, float(inp.energy_per_gun_day))
    e_ultra = max(0.0, float(inp.ultra_energy_per_gun_day))
    return n60 * e_std + n120 * e_std + n_ultra * e_ultra


def _hybrid_daily_gross_components(
    kwh_charge_day: float,
    e_to_pile: float,
    *,
    peak: float,
    valley: float,
    eta: float,
    fee: float,
) -> dict[str, float]:
    """
    峰谷套利+充电日毛利分项（假设充电发生在峰时段）。
    日毛利 = 日充电×服务费 + E×(峰−谷÷η)，E 为储能供桩量(kWh)。
    作者: jiali.qiu
    """
    eta_cl = max(1e-6, float(eta))
    kwh = max(0.0, float(kwh_charge_day))
    e = max(0.0, float(e_to_pile))
    pk = max(0.0, float(peak))
    vl = max(0.0, float(valley))
    f = max(0.0, float(fee))
    grid_kwh = max(0.0, kwh - e)
    storage_charge_kwh = e / eta_cl if e > 0 else 0.0
    service_rev = kwh * f
    charge_elec_rev = kwh * pk
    grid_cost = grid_kwh * pk
    storage_cost = storage_charge_kwh * vl
    arbitrage_on_pile = e * (pk - vl / eta_cl)
    daily_gross = service_rev + charge_elec_rev - grid_cost - storage_cost
    return {
        "daily_gross": daily_gross,
        "grid_kwh": grid_kwh,
        "storage_charge_kwh": storage_charge_kwh,
        "service_rev": service_rev,
        "charge_elec_rev": charge_elec_rev,
        "grid_cost": grid_cost,
        "storage_cost": storage_cost,
        "arbitrage_on_pile": arbitrage_on_pile,
    }


def _annual_revenue_by_usage(
    inp: InputModel,
    *,
    storage_kwh_total: float,
    kwh_charge_day: float,
    days: float,
    peak: float,
    valley: float,
    eta: float,
    fee: float,
) -> tuple[float, dict[str, float], dict[str, float]]:
    """
    按储能用途返回 (年运营收入合计元, 年收入分项, 年可变成本分项)。
    单位：元；VPP 在调用方并入。
    """
    cycles = max(0.0, float(inp.storage_cycles_per_day))
    spread = max(0.0, peak - valley)
    e_max_day = storage_kwh_total * cycles
    rev: dict[str, float] = {}
    cost: dict[str, float] = {}

    if inp.usage == USAGE_PEAK_VALLEY_ONLY:
        rev["储能峰谷套利(独立运行)"] = storage_kwh_total * cycles * spread * eta * days
        rev["充电服务费(对用户,毛)"] = kwh_charge_day * fee * days
        return sum(rev.values()), rev, cost

    if inp.usage == USAGE_HYBRID:
        e_to_pile = min(e_max_day, kwh_charge_day) if kwh_charge_day > 0 else 0.0
        excess = max(0.0, e_max_day - e_to_pile)
        hybrid = _hybrid_daily_gross_components(
            kwh_charge_day,
            e_to_pile,
            peak=peak,
            valley=valley,
            eta=eta,
            fee=fee,
        )
        daily_gross = float(hybrid["daily_gross"])
        # 日毛利含服务费收入、用户峰段电费收入，扣除峰网购与谷段储能充电成本
        rev["储充一体经营毛利(年化)"] = daily_gross * days
        if excess > 0 and spread > 0:
            rev["余电上网峰谷套利"] = excess * spread * eta * days
        return sum(rev.values()), rev, cost

    # 纯动态增容+充电
    manual_wan = max(0.0, float(inp.basic_electricity_saved_wan_per_year))
    if manual_wan > 0:
        rev["节省基本电费/需量(手填)"] = manual_wan * 1e4
    else:
        pk = max(0.0, float(inp.peak_reduction_kw))
        bp = max(0.0, float(inp.basic_electricity_yuan_per_kw_month))
        rev["节省基本电费/需量(公式)"] = pk * bp * 12.0

    avoided_wan = max(0.0, float(inp.transformer_avoided_wan))
    if avoided_wan > 0:
        dep_y = max(1, int(inp.transformer_avoided_depreciation_years))
        rev["避开变压器投资(年化)"] = avoided_wan * 1e4 / dep_y

    rev["充电服务费(对用户,毛)"] = kwh_charge_day * fee * days
    return sum(rev.values()), rev, cost


def _storage_revenue_line_keys(usage: str) -> set[str]:
    """按储能用途划分「储能相关」年收入分项键（不含 VPP）。"""
    if usage == USAGE_PEAK_VALLEY_ONLY:
        return {"储能峰谷套利(独立运行)"}
    if usage == USAGE_HYBRID:
        return {"储充一体经营毛利(年化)", "余电上网峰谷套利"}
    return {
        "节省基本电费/需量(手填)",
        "节省基本电费/需量(公式)",
        "避开变压器投资(年化)",
    }


def _daily_charge_kwh_breakdown(
    n_pile_60: int,
    n_pile_120: int,
    n_ultra_480: int,
    energy_per_gun_day: float,
    ultra_energy_per_gun_day: float,
) -> tuple[float, str]:
    """日总充电量及分项说明（与 _daily_charge_kwh 同口径）。"""
    n60 = max(0, int(n_pile_60))
    n120 = max(0, int(n_pile_120))
    nu = max(0, int(n_ultra_480))
    e_std = max(0.0, float(energy_per_gun_day))
    e_ultra = max(0.0, float(ultra_energy_per_gun_day))
    p60 = n60 * e_std
    p120 = n120 * e_std
    pultra = nu * e_ultra
    total = p60 + p120 + pultra
    detail = (
        f"60kW {n60} 台×{e_std:.0f} + 120kW {n120} 台×{e_std:.0f}（双枪按台计）"
        f" + 480kW {nu} 套×{e_ultra:.0f}（单终端日均）"
        f" = **{total:.0f}** kWh/天"
    )
    return total, detail


def _html_revenue_calc_box(lines: list[str]) -> None:
    """收入计算过程说明框。"""
    if not lines:
        return
    inner = "".join(f"<p>{line}</p>" for line in lines)
    st.markdown(f'<div class="revenue-calc-box">{inner}</div>', unsafe_allow_html=True)


def _charger_service_fee_calc_lines(
    kwh_day: float,
    fee: float,
    days: float,
    *,
    n_pile_60: int,
    n_pile_120: int,
    n_ultra_480: int,
    energy_per_gun_day: float,
    ultra_energy_per_gun_day: float,
) -> list[str]:
    """充电服务费年化计算过程（Markdown 片段，供说明框）。"""
    annual = kwh_day * fee * days
    _, breakdown = _daily_charge_kwh_breakdown(
        n_pile_60, n_pile_120, n_ultra_480, energy_per_gun_day, ultra_energy_per_gun_day
    )
    return [
        f"<strong>日总充电量</strong>：{breakdown}",
        f"<strong>年化</strong> = 日总充电量 × 服务费 × 运营天数"
        f" = {kwh_day:.0f} × {fee:.3f} × {days:.0f}"
        f" = <code>{annual:,.0f}</code> 元/年（<strong>{annual / 1e4:.2f}</strong> 万元）",
        "纯峰谷套利、纯动态增容模式下均按上式计「充电服务费」；峰谷+充电模式下服务费已并入储充毛利，见储能计算过程。",
    ]


def _storage_revenue_calc_lines(
    usage: str,
    *,
    storage_kwh: float,
    cyc: float,
    kwh_day: float,
    days: float,
    peak: float,
    valley: float,
    eta: float,
    fee: float,
    rev_bd: dict[str, float],
    storage_keys: set[str],
    peak_reduction_kw: float = 0.0,
    basic_electricity_yuan_per_kw_month: float = 35.0,
    basic_electricity_saved_wan_per_year: float = 0.0,
    transformer_avoided_wan: float = 0.0,
    transformer_avoided_depreciation_years: int = 15,
) -> list[str]:
    """储能相关年化收入计算过程。"""
    spread = max(0.0, peak - valley)
    lines: list[str] = []
    usage_s = str(usage)

    if usage_s == USAGE_HYBRID:
        e_max = storage_kwh * cyc
        e_pile = min(e_max, kwh_day) if kwh_day > 0 else 0.0
        excess = max(0.0, e_max - e_pile)
        hybrid = _hybrid_daily_gross_components(
            kwh_day,
            e_pile,
            peak=peak,
            valley=valley,
            eta=eta,
            fee=fee,
        )
        daily_gross = float(hybrid["daily_gross"])
        annual_hybrid = float(rev_bd.get("储充一体经营毛利(年化)", daily_gross * days))
        grid_kwh = float(hybrid["grid_kwh"])
        storage_charge_day = float(hybrid["storage_charge_kwh"])
        arb_e = float(hybrid["arbitrage_on_pile"])
        lines.extend(
            [
                HYBRID_PEAK_CHARGE_ASSUMPTION,
                f"<strong>装机</strong> {storage_kwh:.0f} kWh，<strong>日循环</strong> {cyc:.1f} 次 → "
                f"日可放 E₀ = {storage_kwh:.0f} × {cyc:.1f} = <code>{e_max:.0f}</code> kWh",
                f"<strong>供桩量</strong> E = min(E₀, 日充电) = min({e_max:.0f}, {kwh_day:.0f}) = <code>{e_pile:.0f}</code> kWh/天",
                f"<strong>服务费收入</strong> = 日充电 × 服务费 = {kwh_day:.0f} × {fee:.3f}"
                f" = <code>{hybrid['service_rev']:,.0f}</code> 元/天",
                f"<strong>用户峰段电费收入</strong> = 日充电 × 峰价 = {kwh_day:.0f} × {peak:.2f}"
                f" = <code>{hybrid['charge_elec_rev']:,.0f}</code> 元/天",
                f"<strong>峰段网购成本</strong> = (日充电 − E) × 峰价 = {grid_kwh:.0f} × {peak:.2f}"
                f" = <code>{hybrid['grid_cost']:,.0f}</code> 元/天",
                f"<strong>谷段储能充电成本</strong> = E ÷ η × 谷价 = {e_pile:.0f} ÷ {eta:.0%} × {valley:.2f}"
                f" = <code>{hybrid['storage_cost']:,.0f}</code> 元/天",
                f"<strong>储能供桩价差</strong> E×(峰−谷÷η) = {e_pile:.0f}×({peak:.2f}−{valley:.2f}÷{eta:.0%})"
                f" = <code>{arb_e:,.0f}</code> 元/天",
                f"<strong>日毛利</strong> = 服务费 + 用户峰电费 − 峰网购 − 谷储能充电"
                f" = {kwh_day:.0f}×{fee:.3f} + {kwh_day:.0f}×{peak:.2f} − {hybrid['grid_cost']:,.0f} − {hybrid['storage_cost']:,.0f}"
                f" = <code>{daily_gross:,.0f}</code> 元/天",
                f"（等价 <strong>日充电×服务费 + E×(峰−谷÷η)</strong>）",
                f"<strong>储充一体年化</strong> = 日毛利 × {days:.0f} 天 = <code>{annual_hybrid:,.0f}</code> 元"
                f"（<strong>{annual_hybrid / 1e4:.2f}</strong> 万元）",
            ]
        )
        if excess > 1e-6 and spread > 0:
            excess_ann = excess * spread * eta * days
            lines.append(
                f"<strong>余电套利</strong> = (E₀−E)×(峰−谷)×η×天数"
                f" = {excess:.0f}×{spread:.2f}×{eta:.0%}×{days:.0f}"
                f" = <code>{excess_ann:,.0f}</code> 元/年"
            )
        lines.append(
            "本模式下<strong>不再单独列</strong>「充电服务费」收入行（已并入储充一体日毛利）。"
        )
        return lines

    if usage_s == USAGE_PEAK_VALLEY_ONLY:
        arb = storage_kwh * cyc * spread * eta * days
        lines.extend(
            [
                f"<strong>峰谷价差</strong> = 峰 {peak:.2f} − 谷 {valley:.2f} = {spread:.2f} 元/kWh",
                f"<strong>储能套利年化</strong> = 装机×日循环×价差×效率×天数"
                f" = {storage_kwh:.0f}×{cyc:.1f}×{spread:.2f}×{eta:.0%}×{days:.0f}"
                f" = <code>{arb:,.0f}</code> 元/年（<strong>{arb / 1e4:.2f}</strong> 万元）",
            ]
        )
        return lines

    # 纯动态增容
    manual_wan = max(0.0, float(basic_electricity_saved_wan_per_year))
    pk = max(0.0, float(peak_reduction_kw))
    bp = max(0.0, float(basic_electricity_yuan_per_kw_month))
    if manual_wan > 0:
        basic_yuan = manual_wan * 1e4
        lines.append(
            f"<strong>基本电费节省</strong>：手填 <code>{manual_wan:.2f}</code> 万元/年"
            f" = <code>{basic_yuan:,.0f}</code> 元/年"
        )
    else:
        basic_yuan = pk * bp * 12.0
        lines.append(
            f"<strong>基本电费节省</strong> = 削峰 {pk:.0f} kW × {bp:.1f} 元/kW·月 × 12"
            f" = <code>{basic_yuan:,.0f}</code> 元/年"
        )
    avoided_wan = max(0.0, float(transformer_avoided_wan))
    if avoided_wan > 0:
        dep_y = max(1, int(transformer_avoided_depreciation_years))
        avoid_yuan = avoided_wan * 1e4 / dep_y
        lines.append(
            f"<strong>避投年化</strong> = {avoided_wan:.2f} 万元 ÷ {dep_y} 年"
            f" = <code>{avoid_yuan:,.0f}</code> 元/年"
        )
    lines.append("动态增容模式<strong>不计</strong>峰谷日循环套利；充电服务费见下方「充电服务费」计算过程。")
    return lines


def render_charger_service_fee_calc_preview(
    *,
    operating_days: float,
    service_fee_per_kwh: float,
    n_pile_60: int,
    n_pile_120: int,
    n_ultra_480: int,
    energy_per_gun_day: float,
    ultra_energy_per_gun_day: float,
    usage: str,
) -> None:
    """第五部分充电桩运营区：充电服务费年化计算过程。"""
    if str(usage) == USAGE_HYBRID:
        return
    days = max(0.0, float(operating_days))
    fee = max(0.0, float(service_fee_per_kwh))
    kwh_day, _ = _daily_charge_kwh_breakdown(
        n_pile_60, n_pile_120, n_ultra_480, energy_per_gun_day, ultra_energy_per_gun_day
    )
    annual = kwh_day * fee * days
    with st.expander("充电服务费年化 · 计算过程", expanded=False):
        _html_revenue_calc_box(
            _charger_service_fee_calc_lines(
                kwh_day,
                fee,
                days,
                n_pile_60=n_pile_60,
                n_pile_120=n_pile_120,
                n_ultra_480=n_ultra_480,
                energy_per_gun_day=energy_per_gun_day,
                ultra_energy_per_gun_day=ultra_energy_per_gun_day,
            )
        )
        st.caption(f"合计约 **{annual / 1e4:.2f}** 万元/年（与确认测算结果表一致）。")


def render_storage_operations_preview(
    *,
    usage: str,
    storage_sets: int,
    storage_kwh_per_set: float,
    price_storage_per_kwh: float,
    storage_cycles_per_day: float,
    round_trip_efficiency: float,
    peak_price: float,
    valley_price: float,
    operating_days: float,
    service_fee_per_kwh: float,
    n_pile_60: int,
    n_pile_120: int,
    n_ultra_480: int,
    energy_per_gun_day: float,
    ultra_energy_per_gun_day: float,
    peak_reduction_kw: float = 0.0,
    basic_electricity_yuan_per_kw_month: float = 35.0,
    basic_electricity_saved_wan_per_year: float = 0.0,
    transformer_avoided_wan: float = 0.0,
    transformer_avoided_depreciation_years: int = 15,
    cycle_strategy: str = "",
) -> None:
    """
    第五部分「储能运营」区：展示装机、中间量与年化收入粗算（与 compute_projection 同口径）。
    作者: jiali.qiu
    """
    usage_s = str(usage)
    is_dynamic = usage_s == USAGE_DYNAMIC_CAPACITY
    is_hybrid = usage_s == USAGE_HYBRID
    is_pv_only = usage_s == USAGE_PEAK_VALLEY_ONLY

    sets = max(0, int(storage_sets))
    e_set = max(0.0, float(storage_kwh_per_set))
    storage_kwh = e_set * sets
    storage_kw = sets * STORAGE_KW_PER_SET_DEFAULT
    days = max(0.0, float(operating_days))
    eta = float(np.clip(round_trip_efficiency, 1e-6, 1.0))
    # 纯动态增容不计峰谷循环套利，日循环固定为 0（与 compute_projection 一致）
    cyc = 0.0 if is_dynamic else max(0.0, float(storage_cycles_per_day))
    peak = max(0.0, float(peak_price))
    valley = max(0.0, float(valley_price))
    spread = max(0.0, peak - valley)
    fee = max(0.0, float(service_fee_per_kwh))
    kwh_day = (
        max(0, int(n_pile_60)) * max(0.0, energy_per_gun_day)
        + max(0, int(n_pile_120)) * max(0.0, energy_per_gun_day)
        + max(0, int(n_ultra_480)) * max(0.0, ultra_energy_per_gun_day)
    )
    capex_storage_yuan = float(price_storage_per_kwh) * e_set * 1000.0 * sets

    stub = SimpleNamespace(
        usage=usage_s,
        storage_cycles_per_day=cyc,
        peak_reduction_kw=float(peak_reduction_kw),
        basic_electricity_yuan_per_kw_month=float(basic_electricity_yuan_per_kw_month),
        basic_electricity_saved_wan_per_year=float(basic_electricity_saved_wan_per_year),
        transformer_avoided_wan=float(transformer_avoided_wan),
        transformer_avoided_depreciation_years=int(transformer_avoided_depreciation_years),
        n_pile_60=int(n_pile_60),
        n_pile_120=int(n_pile_120),
        n_ultra_480=int(n_ultra_480),
        energy_per_gun_day=float(energy_per_gun_day),
        ultra_energy_per_gun_day=float(ultra_energy_per_gun_day),
    )
    _op_rev, rev_bd, _ = _annual_revenue_by_usage(
        stub,
        storage_kwh_total=storage_kwh,
        kwh_charge_day=kwh_day,
        days=days,
        peak=peak,
        valley=valley,
        eta=eta,
        fee=fee,
    )
    storage_keys = _storage_revenue_line_keys(usage_s)
    storage_rev_yuan = sum(float(rev_bd.get(k, 0.0)) for k in storage_keys)
    charger_rev_yuan = float(rev_bd.get("充电服务费(对用户,毛)", 0.0))

    st.markdown("**运营粗算（与确认测算同口径，随表单联动）**")
    if is_dynamic:
        st.caption(
            f"储能主要用途：**{usage_s}** · 不计充放策略/日循环套利 · "
            "收益项：节省基本电费（削峰）、可选避开变压器投资年化、充电服务费。"
        )
    elif (is_hybrid or is_pv_only) and cycle_strategy and cyc > 1e-6:
        st.caption(
            f"储能主要用途：**{usage_s}** · 充放策略：**{cycle_strategy}** → 日循环 **{cyc:.1f} 次/天**"
        )
    else:
        st.caption(f"储能主要用途：**{usage_s}**")

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("储能装机", f"{storage_kwh:.0f} kWh", f"{storage_kw:.0f} kW · {sets} 套")
    h2.metric("设备投资粗算", f"{capex_storage_yuan / 1e4:.2f} 万元", f"{price_storage_per_kwh:.2f} 元/kWh")
    if is_dynamic:
        h3.metric(
            "动态增容值",
            f"{max(0.0, peak_reduction_kw):.0f} kW",
            f"装机功率 {storage_kw:.0f} kW",
        )
    else:
        h3.metric("日可放电量 E₀", f"{storage_kwh * cyc:.0f} kWh", f"× 效率 {eta:.0%}")
    h4.metric("日充电量(桩)", f"{kwh_day:.0f} kWh", f"运营 {days:.0f} 天/年")

    if is_hybrid and storage_kwh > 0:
        e_max = storage_kwh * cyc
        e_pile = min(e_max, kwh_day) if kwh_day > 0 else 0.0
        excess = max(0.0, e_max - e_pile)
        hybrid_prev = _hybrid_daily_gross_components(
            kwh_day, e_pile, peak=peak, valley=valley, eta=eta, fee=fee
        )
        st.caption(HYBRID_PEAK_CHARGE_ASSUMPTION)
        st.caption(
            f"储充一体：供桩 **{e_pile:.0f}** kWh/天 · 日毛利粗算 **{hybrid_prev['daily_gross']:,.0f}** 元/天"
            f"（= 日充电×服务费 + E×(峰−谷÷η)）· 余电 **{excess:.0f}** kWh/天"
        )
    elif is_pv_only and storage_kwh > 0:
        st.caption(
            f"独立套利：年收益 ≈ 装机×循环×(峰−谷)×η×天数 = "
            f"{storage_kwh:.0f}×{cyc:.1f}×{spread:.2f}×{eta:.2%}×{days:.0f}"
        )
    elif is_dynamic:
        pk = max(0.0, float(peak_reduction_kw))
        bp = max(0.0, float(basic_electricity_yuan_per_kw_month))
        st.caption(
            f"动态增容：削峰 **{pk:.0f} kW** · 需量电价 **{bp:.1f}** 元/kW·月"
            + (
                f" · 手填节省 **{basic_electricity_saved_wan_per_year:.2f}** 万元/年"
                if basic_electricity_saved_wan_per_year > 0
                else f" · 公式节省约 **{pk * bp * 12 / 1e4:.2f}** 万元/年"
            )
        )

    rows: list[dict[str, Any]] = []
    for k, v in rev_bd.items():
        if abs(v) < 1e-6:
            continue
        tag = "储能相关" if k in storage_keys else "充电相关"
        rows.append({"归属": tag, "年收入分项": k, "金额(万元)": round(v / 1e4, 4)})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    s1, s2 = st.columns(2)
    s1.metric("储能相关年化收入", f"{storage_rev_yuan / 1e4:.2f} 万元")
    if charger_rev_yuan > 1e-6 and not is_hybrid:
        s2.metric("充电服务费(年化)", f"{charger_rev_yuan / 1e4:.2f} 万元", "同模式下的桩收入")
    elif is_hybrid:
        s2.caption(
            "峰谷+充电：储充一体毛利已含服务费与用户峰段电费收入，扣除网购与谷充成本。"
        )
    elif is_dynamic and charger_rev_yuan > 1e-6:
        s2.caption(
            f"充电服务费年化约 **{charger_rev_yuan / 1e4:.2f} 万元**（已计入上表「充电相关」或合计）。"
        )

    with st.expander("储能相关年化收入 · 计算过程", expanded=False):
        _html_revenue_calc_box(
            _storage_revenue_calc_lines(
                usage_s,
                storage_kwh=storage_kwh,
                cyc=cyc,
                kwh_day=kwh_day,
                days=days,
                peak=peak,
                valley=valley,
                eta=eta,
                fee=fee,
                rev_bd=rev_bd,
                storage_keys=storage_keys,
                peak_reduction_kw=float(peak_reduction_kw),
                basic_electricity_yuan_per_kw_month=float(basic_electricity_yuan_per_kw_month),
                basic_electricity_saved_wan_per_year=float(basic_electricity_saved_wan_per_year),
                transformer_avoided_wan=float(transformer_avoided_wan),
                transformer_avoided_depreciation_years=int(transformer_avoided_depreciation_years),
            )
        )
        st.caption(f"储能相关合计约 **{storage_rev_yuan / 1e4:.2f}** 万元/年。")

    if is_hybrid:
        with st.expander("充电服务费（已并入储充毛利）· 参考计算", expanded=False):
            _html_revenue_calc_box(
                _charger_service_fee_calc_lines(
                    kwh_day,
                    fee,
                    days,
                    n_pile_60=int(n_pile_60),
                    n_pile_120=int(n_pile_120),
                    n_ultra_480=int(n_ultra_480),
                    energy_per_gun_day=float(energy_per_gun_day),
                    ultra_energy_per_gun_day=float(ultra_energy_per_gun_day),
                )
            )
    elif charger_rev_yuan > 1e-6 or is_dynamic:
        with st.expander("充电服务费年化 · 计算过程", expanded=False):
            _html_revenue_calc_box(
                _charger_service_fee_calc_lines(
                    kwh_day,
                    fee,
                    days,
                    n_pile_60=int(n_pile_60),
                    n_pile_120=int(n_pile_120),
                    n_ultra_480=int(n_ultra_480),
                    energy_per_gun_day=float(energy_per_gun_day),
                    ultra_energy_per_gun_day=float(ultra_energy_per_gun_day),
                )
            )


def compute_projection(inp: InputModel) -> dict[str, Any]:
    """
    核心测算：单位约定
    - Capex / Opex / Revenue / Net：元（内部统一用元，便于加总）
    - 展示万元：除以 1e4

    储能三种模式见 `USAGE_STRATEGY_LOGIC_MD` 与 `_annual_revenue_by_usage()`。
    充电量口径：
    - 60kW 桩：默认按 **1 枪/台** × 单枪日均电量
    - 120kW 桩：按 **双枪（2 枪/台）** × 单枪日均电量
    - 480kW 液冷：**套数 × 终端数常量** ×「超充单终端日均」，其中终端数常量见 `ULTRA_CHARGE_TERMINALS_PER_RACK`
    """
    # --- 储能系统 CAPEX ---
    # 单套能量 e_per_set_kwh（默认 262）× 单价 price_storage_per_kwh（元/kWh）× 1000
    # 与需求给出的示例一致：0.65 * 262 * 1000（元）为单套粗算投资
    e_per_set_kwh = float(inp.storage_kwh_per_set)
    unit_storage_cost_yuan = float(inp.price_storage_per_kwh) * e_per_set_kwh * 1000.0
    storage_kwh_total = e_per_set_kwh * max(0, int(inp.storage_sets))
    capex_storage = unit_storage_cost_yuan * max(0, int(inp.storage_sets))

    # --- 充电桩 Capex（万元 → 元）---
    capex_pile_60 = max(0, int(inp.n_pile_60)) * float(inp.price_pile_60_wan) * 1e4
    capex_pile_120 = max(0, int(inp.n_pile_120)) * float(inp.price_pile_120_wan) * 1e4
    capex_ultra = max(0, int(inp.n_ultra_480)) * float(inp.price_ultra_480_wan) * 1e4
    capex_chargers_total = capex_pile_60 + capex_pile_120 + capex_ultra

    k_rated = float(inp.kva_existing)
    k_remain = float(inp.kva_remaining_available)
    pf = float(inp.power_factor)
    usable_kw = usable_station_capacity_kw(k_remain, pf)
    install_storage = str(inp.storage_installed) != STORAGE_INSTALL_NO
    tf_capex = compute_transformer_upgrade_capex(inp)
    capex_transformer_delta = float(tf_capex["capex_yuan"])
    delta_kva = float(tf_capex["delta_kva"])
    baseline_kva = float(tf_capex["baseline_kva"])
    k_target_eff = float(tf_capex["k_target_eff"])
    gap_kva_piles = float(tf_capex["gap_kva"])
    dynamic_insufficient = bool(tf_capex["dynamic_insufficient"])
    tf_capex_label = str(tf_capex["label"])
    tf_detail_key = str(tf_capex.get("detail_key", ""))
    pile_req_kva = required_transformer_kva(
        total_installed_charger_kw(inp.n_pile_60, inp.n_pile_120, inp.n_ultra_480),
        float(inp.demand_coeff_kx),
        float(inp.simultaneity_coeff_kt),
        float(inp.planning_cos_phi),
        float(inp.planning_load_rate_eta),
    )

    # --- 施工（第四部分：充电桩工程 / 微网控制器 / 储能工程 / 相关物料）---
    capex_charger_eng, charger_eng_detail = compute_charger_engineering_capex(inp.charger_engineering)
    capex_storage_eng, storage_eng_detail = compute_storage_engineering_capex(
        inp.storage_engineering, include=install_storage
    )
    capex_civil_wan = max(0, int(inp.parking_spots)) * max(0.0, float(inp.parking_cost_per_spot_wan))
    capex_civil = capex_civil_wan * 1e4
    capex_materials_misc = max(0.0, float(inp.other_construction_wan)) * 1e4
    capex_microgrid = (
        max(0.0, float(inp.microgrid_controller_equip_wan))
        + max(0.0, float(inp.microgrid_controller_debug_wan))
    ) * 1e4
    capex_other_equipment = capex_microgrid
    capex_materials_section = capex_civil + capex_materials_misc + capex_transformer_delta
    capex_construction = (
        capex_charger_eng + capex_storage_eng + capex_other_equipment + capex_materials_section
    )

    capex_bd: dict[str, float] = {
        "快充60kW": capex_pile_60,
        "快充120kW双枪": capex_pile_120,
        "分体式液冷480kW(套)": capex_ultra,
        "储能系统(设备)": capex_storage,
        "【合计】充电桩工程": capex_charger_eng,
        "【合计】微网控制器": capex_other_equipment,
        "【合计】储能工程": capex_storage_eng,
        "【合计】相关物料": capex_materials_section,
    }
    for k, v in charger_eng_detail.items():
        capex_bd[f"充电桩工程·{k}"] = v
    for k, v in storage_eng_detail.items():
        capex_bd[f"储能工程·{k}"] = v
    if capex_microgrid > 1e-6:
        capex_bd["微网控制器(设备+调试)"] = capex_microgrid
    if capex_civil > 1e-6:
        capex_bd["相关物料·车位土建"] = capex_civil
    if capex_materials_misc > 1e-6:
        capex_bd["相关物料·其他杂项"] = capex_materials_misc
    if capex_transformer_delta > 1e-6 and tf_detail_key:
        capex_bd[tf_detail_key] = capex_transformer_delta

    capex_total = capex_chargers_total + capex_storage + capex_construction

    # --- 年收益（三种储能用途，见 _annual_revenue_by_usage）---
    eta = float(np.clip(inp.round_trip_efficiency, 1e-6, 1.0))
    days = max(0.0, float(inp.operating_days))
    peak = float(inp.peak_price)
    valley = float(inp.valley_price)
    fee = max(0.0, float(inp.service_fee_per_kwh))
    kwh_charge_day = _daily_charge_kwh(inp)

    storage_kwh_year1 = storage_kwh_total * battery_soh_factor(1)
    op_rev, rev_breakdown, var_cost = _annual_revenue_by_usage(
        inp,
        storage_kwh_total=storage_kwh_year1,
        kwh_charge_day=kwh_charge_day,
        days=days,
        peak=peak,
        valley=valley,
        eta=eta,
        fee=fee,
    )
    revenue_vpp = max(0.0, float(inp.vpp_revenue_wan)) * 1e4
    rev_breakdown = {**rev_breakdown, "虚拟电厂/需求响应": revenue_vpp}
    rev_breakdown.update(_gov_subsidy_revenue_breakdown_lines(inp))
    subsidy_installment_yuan = (
        gov_subsidy_yuan_in_year(inp, 1)
        if str(inp.gov_subsidy_mode).strip() == GOV_SUBSIDY_INSTALLMENT
        else 0.0
    )
    revenue_operating = op_rev + revenue_vpp + subsidy_installment_yuan
    revenue_year = revenue_operating + (
        gov_subsidy_yuan_in_year(inp, 1)
        if str(inp.gov_subsidy_mode).strip() == GOV_SUBSIDY_ONCE
        else 0.0
    )

    opex_year = capex_total * max(0.0, float(inp.opex_ratio))
    net_operating_year = revenue_operating - opex_year
    net_year = net_operating_year

    payback_years = (capex_total / net_year) if net_year > 0 else float("inf")
    roi = (net_year / capex_total * 100.0) if capex_total > 0 else float("nan")

    vcbd = {k: v for k, v in var_cost.items() if v != 0.0}

    return {
        "capex_total": capex_total,
        "capex_breakdown": capex_bd,
        "capex_section_totals": {
            "充电桩工程": capex_charger_eng,
            "微网控制器": capex_other_equipment,
            "储能工程": capex_storage_eng,
            "相关物料": capex_materials_section,
        },
        "revenue_year": revenue_year,
        "revenue_breakdown": {k: v for k, v in rev_breakdown.items() if v != 0.0},
        "variable_cost_breakdown": vcbd,
        "opex_year": opex_year,
        "net_year": net_year,
        "net_operating_year": net_operating_year,
        "payback_years": payback_years,
        "roi_percent": roi,
        "storage_kwh_total": storage_kwh_total,
        "gov_subsidy_once_yuan": gov_subsidy_yuan_in_year(inp, 1)
        if str(inp.gov_subsidy_mode).strip() == GOV_SUBSIDY_ONCE
        else 0.0,
        "delta_kva": delta_kva,
        "meta": {
            "储能用途": inp.usage,
            "储充可用容量(kW约)": usable_kw,
            "剩余可用变压器(kVA)": k_remain,
            "功率因数": pf,
            "现有变压器额定(kVA)": k_rated if k_rated > 0 else None,
            "目标变压器总容量(kVA)": k_target_eff if k_target_eff > 0 else None,
            "相对剩余可用缺口(kVA)": gap_kva_piles if gap_kva_piles > 1e-6 else None,
            "动态增容不足": dynamic_insufficient if install_storage else None,
            "规划需用变压器(kVA)": pile_req_kva,
            "充电桩装机合计(kW)": total_installed_charger_kw(
                inp.n_pile_60, inp.n_pile_120, inp.n_ultra_480
            ),
            "需电网申报(>160kW)": total_installed_charger_kw(
                inp.n_pile_60, inp.n_pile_120, inp.n_ultra_480
            )
            > GRID_DECLARATION_THRESHOLD_KW,
            "充电桩配置方式": inp.charger_config_mode,
            "规划系数Kx/Kt/cosΦ/η": (
                inp.demand_coeff_kx,
                inp.simultaneity_coeff_kt,
                inp.planning_cos_phi,
                inp.planning_load_rate_eta,
            ),
            "日总充电量(kWh)": kwh_charge_day,
            "峰电价": float(inp.peak_price),
            "谷电价": float(inp.valley_price),
            "平时电价(本模式未用于主公式)": float(inp.flat_price),
        },
    }


def build_cash_flows_wan(
    capex_yuan: float,
    net_operating_year_yuan: float,
    horizon_years: int,
    inp: InputModel | None = None,
) -> np.ndarray:
    """
    构造各年现金流（万元），用于 IRR 与累计曲线：
    - 第0年：-总投资
    - 第1..N年：运营年净收益 + 政府补贴（一次性仅第1年，分期按年限）
    """
    n = max(1, int(horizon_years))
    cf = np.zeros(n + 1, dtype=float)
    cf[0] = -capex_yuan / 1e4
    base = float(net_operating_year_yuan) / 1e4
    for y in range(1, n + 1):
        sub_wan = gov_subsidy_yuan_in_year(inp, y) / 1e4 if inp is not None else 0.0
        cf[y] = base + sub_wan
    return cf


def irr_annual(cf_wan: np.ndarray) -> float | None:
    """返回年化 IRR；失败则 None。"""
    if npf is None:
        return None
    try:
        r = npf.irr(cf_wan)
        if r is None or not np.isfinite(r):
            return None
        return float(r)
    except Exception:
        return None


def cumulative_cf(cf_wan: np.ndarray) -> np.ndarray:
    """累计现金流（万元）。"""
    return np.cumsum(cf_wan)


def payback_time_years(cf_wan: np.ndarray) -> float | None:
    """
    线性插值估计回本时间（年）：
    在累计现金流首次穿过 0 的时刻进行插值。
    """
    cum = cumulative_cf(cf_wan)
    if cum[-1] < 0:
        return None
    for i in range(1, len(cum)):
        if cum[i - 1] < 0 <= cum[i]:
            # 在区间 [i-1, i] 内线性插值：cum(i-1) + t*(cf[i]) = 0 -> 注意 cum(i)=cum(i-1)+cf[i]
            prev = cum[i - 1]
            cur = cum[i]
            if cur == prev:
                return float(i)
            t = -prev / (cur - prev)  # 从 i-1 时刻起前进的份额
            return (i - 1) + t
    # 若在起点已非负
    if cum[0] >= 0:
        return 0.0
    return None


def _annual_storage_charge_cost_yuan(
    inp: InputModel,
    *,
    storage_kwh_total: float,
    kwh_charge_day: float,
    days: float,
    valley: float,
    eta: float,
    soh: float,
) -> float:
    """储能谷段充电电费（不含税，元/年）；随 SOH 降低可放电量。"""
    if str(inp.storage_installed) == STORAGE_INSTALL_NO:
        return 0.0
    if inp.usage == USAGE_DYNAMIC_CAPACITY:
        return 0.0
    cycles = max(0.0, float(inp.storage_cycles_per_day))
    eff_kwh = max(0.0, storage_kwh_total) * max(0.0, soh)
    e_max_day = eff_kwh * cycles
    if inp.usage == USAGE_HYBRID:
        e_to_pile = min(e_max_day, kwh_charge_day) if kwh_charge_day > 0 else 0.0
        storage_charge_day = e_to_pile / eta if e_to_pile > 0 and eta > 0 else 0.0
        return storage_charge_day * valley * days
    if inp.usage == USAGE_PEAK_VALLEY_ONLY:
        charge_kwh_day = e_max_day / eta if eta > 0 else 0.0
        return charge_kwh_day * valley * days
    return 0.0


def _profit_revenue_five_lines(
    inp: InputModel, rev_bd: dict[str, float], ancillary_yuan: float
) -> dict[str, float]:
    """利润表五项收入（不含税，元）。"""
    storage_keys = _storage_revenue_line_keys(str(inp.usage))
    lines: dict[str, float] = {
        "充电服务费": 0.0,
        "储能经营收入": 0.0,
        "VPP与需求响应": float(rev_bd.get("虚拟电厂/需求响应", 0.0)),
        "辅助服务": max(0.0, ancillary_yuan),
        "其他收入": 0.0,
    }
    gov = float(rev_bd.get("政府补贴(分期)", 0.0)) + float(
        rev_bd.get("政府补贴(一次性,第1年)", 0.0)
    )
    if inp.usage == USAGE_HYBRID:
        lines["储能经营收入"] = sum(float(rev_bd.get(k, 0.0)) for k in storage_keys)
        lines["其他收入"] = gov
    else:
        lines["充电服务费"] = float(rev_bd.get("充电服务费(对用户,毛)", 0.0))
        lines["储能经营收入"] = sum(float(rev_bd.get(k, 0.0)) for k in storage_keys)
        other = float(rev_bd.get("避开变压器投资(年化)", 0.0))
        lines["其他收入"] = other + gov
    return lines


def _income_tax_three_free_three_half(
    pretax_profit: float,
    loss_carry: float,
    year_1based: int,
    tax_rate: float,
) -> tuple[float, float]:
    """三免三减半 + 可弥补亏损；返回 (所得税, 年末亏损结转)。"""
    if pretax_profit <= 0:
        return 0.0, loss_carry + abs(pretax_profit)
    offset = min(loss_carry, pretax_profit)
    taxable = pretax_profit - offset
    new_loss = max(0.0, loss_carry - pretax_profit)
    y = int(year_1based)
    if y <= 3:
        eff = 0.0
    elif y <= 6:
        eff = tax_rate * 0.5
    else:
        eff = tax_rate
    return taxable * eff, new_loss


def compute_financial_statements(inp: InputModel, proj: dict[str, Any]) -> dict[str, Any]:
    """
    多年利润表、现金流量表与财务评价（收入/成本不含税；增值税留抵结转）。
    作者：jiali.qiu
    """
    n = max(1, int(inp.horizon_years))
    vat_r = max(0.0, float(inp.vat_rate))
    tax_r = max(0.0, float(inp.income_tax_rate))
    disc = max(0.0, float(inp.discount_rate))
    dep_y = max(1, int(inp.depreciation_years))
    salvage_r = max(0.0, min(1.0, float(inp.salvage_rate)))

    capex_total = float(proj["capex_total"])
    sec = proj.get("capex_section_totals", {})
    civil = float(sec.get("相关物料", 0.0))
    depreciable = max(0.0, capex_total - civil)
    annual_dep = depreciable / dep_y
    salvage_yuan = depreciable * salvage_r

    storage_kwh = float(proj.get("storage_kwh_total", 0.0))
    days = max(0.0, float(inp.operating_days))
    peak = float(inp.peak_price)
    valley = float(inp.valley_price)
    eta = max(0.01, min(1.0, float(inp.round_trip_efficiency)))
    fee = float(inp.service_fee_per_kwh)
    kwh_day = float(proj.get("meta", {}).get("日总充电量(kWh)", _daily_charge_kwh(inp)))
    ancillary = max(0.0, float(inp.ancillary_times_per_year)) * max(
        0.0, float(inp.ancillary_unit_price_yuan)
    )

    profit_rows: list[dict[str, Any]] = []
    cf_rows: list[dict[str, Any]] = []
    net_cf_wan: list[float] = []
    vat_credit = 0.0
    loss_carry = 0.0

    # 第 0 年：投资
    capex_vat_input = capex_total * vat_r
    vat_credit += capex_vat_input
    net_cf_wan.append(-capex_total / 1e4)
    cf_rows.append(
        {
            "年份": 0,
            "营业收入(不含税)": 0.0,
            "增值税进项返还": 0.0,
            "经营现金流入": 0.0,
            "附加税": 0.0,
            "所得税": 0.0,
            "储能充电电费": 0.0,
            "OPEX": 0.0,
            "经营现金流出": 0.0,
            "投资现金流出": capex_total / 1e4,
            "残值回收": 0.0,
            "净现金流": -capex_total / 1e4,
            "累计净现金流": -capex_total / 1e4,
        }
    )

    cum = net_cf_wan[0]
    for y in range(1, n + 1):
        soh = battery_soh_factor(y)
        eff_storage = storage_kwh * soh
        op_rev, rev_bd, _ = _annual_revenue_by_usage(
            inp,
            storage_kwh_total=eff_storage,
            kwh_charge_day=kwh_day,
            days=days,
            peak=peak,
            valley=valley,
            eta=eta,
            fee=fee,
        )
        rev_bd = {**rev_bd, "虚拟电厂/需求响应": max(0.0, float(inp.vpp_revenue_wan)) * 1e4}
        sub_y = gov_subsidy_yuan_in_year(inp, y)
        if sub_y > 1e-6:
            if str(inp.gov_subsidy_mode).strip() == GOV_SUBSIDY_ONCE:
                rev_bd["政府补贴(一次性,第1年)"] = sub_y
            else:
                rev_bd["政府补贴(分期)"] = sub_y
        rev_lines = _profit_revenue_five_lines(inp, rev_bd, ancillary)
        revenue_ex = sum(rev_lines.values())

        storage_charge = _annual_storage_charge_cost_yuan(
            inp,
            storage_kwh_total=storage_kwh,
            kwh_charge_day=kwh_day,
            days=days,
            valley=valley,
            eta=eta,
            soh=soh,
        )
        opex = capex_total * max(0.0, float(inp.opex_ratio))

        output_vat = revenue_ex * vat_r
        input_vat = storage_charge * vat_r
        vat_credit_before = vat_credit
        vat_payable = max(0.0, output_vat - input_vat - vat_credit)
        vat_credit = max(0.0, vat_credit + input_vat - output_vat)
        vat_refund_cash = max(0.0, vat_credit_before + input_vat - output_vat - vat_credit)
        surtax = vat_payable * SURTAX_RATE_ON_VAT_PAYABLE

        operating_cost = storage_charge + annual_dep + opex + surtax
        pretax = revenue_ex - operating_cost
        income_tax, loss_carry = _income_tax_three_free_three_half(
            pretax, loss_carry, y, tax_r
        )
        net_profit = pretax - income_tax

        op_in = revenue_ex + vat_refund_cash
        op_out = surtax + income_tax + storage_charge + opex
        inv_out = 0.0
        salvage_wan = 0.0
        if y == n and salvage_yuan > 0:
            salvage_wan = salvage_yuan / 1e4
        net_y = (op_in - op_out) / 1e4 - inv_out + salvage_wan
        cum += net_y
        net_cf_wan.append(net_y)

        profit_rows.append(
            {
                "年份": y,
                "SOH": soh,
                **{f"收入·{k}": v / 1e4 for k, v in rev_lines.items()},
                "营业收入合计": revenue_ex / 1e4,
                "储能充电成本": storage_charge / 1e4,
                "折旧": annual_dep / 1e4,
                "OPEX": opex / 1e4,
                "附加税": surtax / 1e4,
                "营业成本合计": operating_cost / 1e4,
                "税前利润": pretax / 1e4,
                "所得税": income_tax / 1e4,
                "净利润": net_profit / 1e4,
            }
        )
        cf_rows.append(
            {
                "年份": y,
                "营业收入(不含税)": revenue_ex / 1e4,
                "增值税进项返还": vat_refund_cash / 1e4,
                "经营现金流入": op_in / 1e4,
                "附加税": surtax / 1e4,
                "所得税": income_tax / 1e4,
                "储能充电电费": storage_charge / 1e4,
                "OPEX": opex / 1e4,
                "经营现金流出": op_out / 1e4,
                "投资现金流出": inv_out,
                "残值回收": salvage_wan,
                "净现金流": net_y,
                "累计净现金流": cum,
            }
        )

    cf_arr = np.array(net_cf_wan, dtype=float)
    dates = np.arange(len(cf_arr), dtype=float)
    static_pb = payback_time_years(cf_arr)
    npv_val = float(np.sum(cf_arr / np.power(1.0 + disc, dates))) if disc >= 0 else float("nan")
    xnpv_val: float | None = None
    xirr_val: float | None = None
    if npf is not None:
        try:
            xnpv_val = float(npf.xnpv(disc, cf_arr, dates))
            xirr_val = float(npf.xirr(cf_arr, dates))
            if not np.isfinite(xirr_val):
                xirr_val = None
        except Exception:
            xnpv_val = None
            xirr_val = None
    init_inv = abs(cf_arr[0]) if cf_arr[0] < 0 else capex_total / 1e4
    pi = (npv_val / init_inv) if init_inv > 1e-9 and np.isfinite(npv_val) else float("nan")

    return {
        "profit_df": pd.DataFrame(profit_rows),
        "cashflow_df": pd.DataFrame(cf_rows),
        "net_cf_wan": cf_arr,
        "metrics": {
            "静态投资回收期(年)": static_pb,
            "NPV(万元)": npv_val,
            "XNPV(万元)": xnpv_val,
            "IRR(年化)": xirr_val,
            "PI": pi,
            "折现率": disc,
            "可折旧资产(万元)": depreciable / 1e4,
            "期末残值(万元)": salvage_yuan / 1e4,
        },
    }


def _select_index(options: list[str], value: str, fallback: str) -> int:
    try:
        return options.index(value)
    except ValueError:
        return options.index(fallback)


def _build_detail_defaults(site_basics: dict[str, Any]) -> dict[str, Any]:
    """
    第二步表单初值：场站信息来自第一步；桩/储能等数量类默认 0，避免一上来铺满示例配置。
    电价、单价等仍可从 cost_defaults.json 读取。
    """
    cfg = load_site_cost_defaults()
    upgrade = str(site_basics.get("transformer_upgrade_mode", "不增容"))
    k_existing = float(site_basics.get("kva_existing", 0.0))
    k_remain = float(site_basics.get("kva_remaining_available", 0.0))
    pf = float(site_basics.get("power_factor", POWER_FACTOR_DEFAULT))
    orig_kva = _original_transformer_kva(k_existing, k_remain)
    saved_target = float(cfg.get("kva_target", 0.0))
    if upgrade == "不增容":
        k_target = orig_kva
    elif saved_target < orig_kva - 1e-3:
        k_target = orig_kva
    elif upgrade == "原址换大":
        k_target = saved_target
    else:
        k_target = max(orig_kva, saved_target)
    if float(cfg.get("price_pile_60_wan", 0.0)) <= 0:
        cfg["price_pile_60_wan"] = PILE_60_DEFAULT_PRICE_WAN
    if float(cfg.get("price_pile_120_wan", 0.0)) <= 0:
        cfg["price_pile_120_wan"] = PILE_120_DEFAULT_PRICE_WAN

    vehicle = str(site_basics.get("parking_vehicle_type", "乘用车"))
    farthest = float(site_basics.get("transformer_to_farthest_m", 0.0))

    cfg.update(
        {
            "station_city": str(site_basics.get("station_city", "")).strip(),
            "parking_vehicle_type": vehicle,
            "parking_slot_size_desc": _slot_desc_from_vehicle(vehicle),
            "parking_spots": int(site_basics.get("parking_spots", 0)),
            "kva_existing": k_existing,
            "kva_remaining_available": k_remain,
            "power_factor": pf,
            "kva_target": k_target,
            "transformer_upgrade_mode": upgrade,
            "transformer_to_farthest_m": farthest,
            "site_layout": str(site_basics.get("site_layout", "室外")),
            **_charger_eng_defaults_merged(cfg, farthest),
            **_storage_eng_defaults_merged(cfg, farthest),
            "n_pile_60": 0,
            "n_pile_120": 0,
            "n_ultra_480": 0,
            "ultra_480_enabled": "否",
            "price_ultra_480_wan": ULTRA_480_DEFAULT_PRICE_WAN,
            "storage_sets": 0,
        }
    )
    return cfg


def render_site_basics_step() -> tuple[bool, list[str]]:
    """
    第一步：场站地点、车位规格与数量、变压器与距离、室内/室外。
    返回 (是否刚完成确认, 校验错误列表)。
    """
    b = st.session_state.get("site_basics") or {}
    errs: list[str] = []

    with st.form("site_basics_form"):
        _html_step_title("第一步：确认场站基础信息")
        _html_note("请先确认场站条件；完成后再进入第二步起的桩数、储能、施工与运营测算。")
        city = st.text_input(
            "场站地点（城市 / 行政区 / 项目名称）",
            value=str(b.get("station_city", "")),
            placeholder="例如：上海市浦东新区 XX 物流园",
        )
        v1, v2 = st.columns(2)
        with v1:
            vehicle = st.selectbox(
                "车位规格",
                options=PARKING_VEHICLE_OPTIONS,
                index=_select_index(
                    PARKING_VEHICLE_OPTIONS, str(b.get("parking_vehicle_type", "乘用车")), "乘用车"
                ),
            )
        with v2:
            layout = st.selectbox(
                "场站环境",
                options=SITE_LAYOUT_OPTIONS,
                index=_select_index(SITE_LAYOUT_OPTIONS, str(b.get("site_layout", "室外")), "室外"),
            )
        spots = int(
            st.number_input(
                "车位数量（个）",
                min_value=1,
                value=max(1, int(b.get("parking_spots", 1))),
                step=1,
            )
        )
        t1, t2 = st.columns(2)
        with t1:
            rated_label = st.selectbox(
                "现有变压器容量（选填）",
                options=TRANSFORMER_RATED_SELECT_LABELS,
                index=_rated_kva_select_index(float(b.get("kva_existing", 0.0))),
                help="物业台账额定容量；不填则增容测算以「剩余可用」为基准。",
            )
            k_existing = _rated_kva_from_select_label(rated_label)
        with t2:
            upgrade_mode = st.selectbox(
                "变压器是否可以增容",
                options=TRANSFORMER_UPGRADE_OPTIONS,
                index=_select_index(
                    TRANSFORMER_UPGRADE_OPTIONS,
                    str(b.get("transformer_upgrade_mode", "不增容")),
                    "不增容",
                ),
                help="不增容：测算按现有容量；原址换大/高压申请：第二步填写目标总容量。",
            )
        t3, t4 = st.columns(2)
        with t3:
            k_remain = float(
                st.number_input(
                    "剩余可用变压器容量 (kVA) *",
                    min_value=0.0,
                    value=float(b.get("kva_remaining_available", 0.0)),
                    step=10.0,
                    help="必填。分配给本储充场站、尚未被其他负荷占用的变压器容量。",
                )
            )
        with t4:
            power_factor = float(
                st.number_input(
                    "功率因数 *",
                    min_value=POWER_FACTOR_MIN,
                    max_value=POWER_FACTOR_MAX,
                    value=float(np.clip(float(b.get("power_factor", POWER_FACTOR_DEFAULT)), POWER_FACTOR_MIN, POWER_FACTOR_MAX)),
                    step=0.01,
                    help=f"建议 {POWER_FACTOR_MIN}–{POWER_FACTOR_MAX}；与剩余可用容量相乘为储充可用能力。",
                )
            )
        if k_remain > 0 and power_factor >= POWER_FACTOR_MIN:
            st.caption(
                f"储充场站可用约 **{usable_station_capacity_kw(k_remain, power_factor):.0f} kW** "
                f"（{k_remain:.0f} kVA × {power_factor:.2f}）。"
            )
        farthest_m = float(
            st.number_input(
                "变压器到最远车位距离 (m)",
                min_value=0.0,
                value=float(b.get("transformer_to_farthest_m", 0.0)),
                step=5.0,
                help=f"用于第四步电缆/敷设长度初值（×{ROUTE_LEN_FACTOR}），可在施工区再调整。",
            )
        )
        submitted = st.form_submit_button("确认场站信息，进入详细测算", type="primary", use_container_width=True)

    if not submitted:
        return False, []

    city_s = str(city).strip()
    if not city_s:
        errs.append("请填写场站地点")
    if k_remain <= 0:
        errs.append("请填写剩余可用变压器容量（须大于 0）")
    if not (POWER_FACTOR_MIN <= power_factor <= POWER_FACTOR_MAX):
        errs.append(f"功率因数须在 {POWER_FACTOR_MIN}–{POWER_FACTOR_MAX} 之间")
    if errs:
        return True, errs

    st.session_state.site_basics = {
        "station_city": city_s,
        "parking_vehicle_type": str(vehicle),
        "parking_spots": spots,
        "kva_existing": k_existing,
        "kva_remaining_available": k_remain,
        "power_factor": power_factor,
        "transformer_upgrade_mode": str(upgrade_mode),
        "transformer_to_farthest_m": farthest_m,
        "site_layout": str(layout),
    }
    st.session_state.site_basics_confirmed = True
    return True, []


def render_input_form(defs: dict[str, Any]) -> tuple[bool, InputModel | None, list[str]]:
    """
    左侧单页表单：分区 + 栅格排布，模拟「参数表」。
    初值来自 `defs`（一般由 load_site_cost_defaults() 提供）。
    返回：(是否点击「项目正向收益及回收期测算」, 校验通过时的模型, 错误列表)
    """
    errs: list[str] = []
    d = defs

    annual_opex_pct = float(d["annual_opex_percent"])

    upgrade_mode = str(d.get("transformer_upgrade_mode", "不增容"))
    vehicle = str(d.get("parking_vehicle_type", "乘用车"))
    layout = str(d.get("site_layout", "室外"))
    farthest_m = float(d.get("transformer_to_farthest_m", 0.0))
    ct1 = str(d.get("station_city", "")).strip()
    spots = int(d["parking_spots"])
    k_remain = float(d.get("kva_remaining_available", 0.0))
    pf = float(d.get("power_factor", POWER_FACTOR_DEFAULT))
    usable_kw = usable_station_capacity_kw(k_remain, pf)
    k_rated = float(d.get("kva_existing", 0.0))
    rated_txt = f"{k_rated:.0f} kVA" if k_rated > 0 else "未填额定"

    _html_step_title("第二步：充电桩")
    _html_flow_zone_open("2.1 桩型与功率规划")
    charger_plan = render_charger_capacity_planner(
        kva_remaining=k_remain,
        kva_existing=k_rated,
        parking_spots=spots,
        defs=d,
    )
    plan_mode_outer = str(charger_plan.get("mode", CHARGER_CONFIG_MODE_A))
    is_mode_b = plan_mode_outer == CHARGER_CONFIG_MODE_B

    # 分时电价 / 储能 / 施工等默认值（提交时按第三部分选择覆盖）
    peak = float(d["peak_price"])
    valley = float(d["valley_price"])
    flat_ = float(d["flat_price"])
    usage = str(d.get("usage", USAGE_HYBRID))
    sets = int(d.get("storage_sets", 0))
    e_set = float(d.get("storage_kwh_per_set", 262.0))
    p_kwh = float(d.get("price_storage_per_kwh", 0.65))
    cyc = float(d.get("storage_cycles_per_day", 1.0))
    cycle_strategy = strategy_from_cycles(cyc)
    peak_red_kw = float(d.get("peak_reduction_kw", 0.0))
    basic_kw_price = float(d.get("basic_electricity_yuan_per_kw_month", 35.0))
    basic_save_wan = float(d.get("basic_electricity_saved_wan_per_year", 0.0))
    tf_avoid_wan = float(d.get("transformer_avoided_wan", 0.0))
    tf_avoid_years = int(d.get("transformer_avoided_depreciation_years", 15))
    eta = float(d.get("round_trip_efficiency", 0.9))
    k_existing = float(d["kva_existing"])
    baseline_kva = _transformer_baseline_kva(k_existing, k_remain)
    orig_kva = _original_transformer_kva(k_existing, k_remain)
    k_target = float(d.get("kva_target", orig_kva))
    pu = float(d.get("transformer_unit_price", 400.0))
    storage_install = str(d.get("storage_installed", STORAGE_INSTALL_NO))

    _html_flow_zone_close()
    st.markdown(
        f'<p class="muted-hint">场站摘要 · <strong>{ct1}</strong> · '
        f"{vehicle} × {spots} 位 · 剩余可用 {k_remain:.0f} kVA × PF {pf:.2f} ≈ "
        f"<strong>{usable_kw:.0f} kW</strong></p>",
        unsafe_allow_html=True,
    )

    _html_flow_zone_open("2.2 数量与单价")
    plan_mode = str(charger_plan.get("mode", CHARGER_CONFIG_MODE_A))
    if plan_mode == CHARGER_CONFIG_MODE_A:
        with st.expander("当前配置汇总与功率优化建议", expanded=True):
            st.markdown(
                format_charger_config_confirm_summary(
                    charger_plan,
                    plan_mode=plan_mode,
                    kva_remaining=k_remain,
                    parking_spots=spots,
                )
            )
        st.caption(
            "**a 按剩余容量规划**：下表桩数默认=上节 c 步推荐，**可修改**；"
            f"60kW 默认 **{PILE_60_DEFAULT_PRICE_WAN:.0f} 万元/台**、"
            f"120kW 默认 **{PILE_120_DEFAULT_PRICE_WAN:.0f} 万元/台**；"
            "2.3 目标容量默认与第一步原变压器联动。"
        )
        def_n60 = int(charger_plan["n60"])
        def_n120 = int(charger_plan["n120"])
        def_n480 = int(charger_plan["n480"])
        ultra_on = str(charger_plan["ultra_on"])
        max_n480 = 1 if charger_plan.get("can_480") or def_n480 > 0 else 0
        g1, g2, g3 = st.columns(3)
        with g1:
            n60 = int(
                st.number_input(
                    "60kW 桩(台)",
                    min_value=0,
                    max_value=max(0, spots),
                    value=def_n60,
                    step=1,
                )
            )
            p60_wan = float(
                st.number_input(
                    "60kW 单价(万元/台)",
                    min_value=0.0,
                    value=float(d["price_pile_60_wan"]),
                    step=1.0,
                )
            )
        with g2:
            n120 = int(
                st.number_input(
                    "120kW 双枪(台,每台占2车位)",
                    min_value=0,
                    max_value=max(0, spots // PILE_120_SPOTS_PER_UNIT),
                    value=def_n120,
                    step=1,
                )
            )
            p120_wan = float(
                st.number_input(
                    "120kW 单价(万元/台)",
                    min_value=0.0,
                    value=float(d.get("price_pile_120_wan", PILE_120_DEFAULT_PRICE_WAN)),
                    step=0.5,
                )
            )
        with g3:
            n480 = int(
                st.number_input(
                    "480kW 液冷(套)",
                    min_value=0,
                    max_value=max_n480,
                    value=def_n480,
                    step=1,
                    help="容量不足时上节不可选 480kW；1 套占 4 车位。",
                )
            )
            p480_wan = float(
                st.number_input(
                    "480kW 单价(万元/套)",
                    min_value=0.0,
                    value=float(d.get("price_ultra_480_wan", ULTRA_480_DEFAULT_PRICE_WAN)),
                    step=1.0,
                    disabled=(n480 <= 0),
                )
            )
        ultra_on = "是" if n480 > 0 else "否"
        spots_used_a = charger_parking_spots_used(n60, n120, n480)
        if spots_used_a > spots:
            st.warning(
                f"占用车位 **{spots_used_a}** / **{spots}** 个。{PARKING_SPOTS_EXPAND_HINT}"
            )
        plan_kx = float(charger_plan.get("kx", DEMAND_COEFF_KX_DEFAULT))
        plan_kt = float(charger_plan.get("kt", SIMULTANEITY_KT_DEFAULT))
        plan_cos = float(charger_plan.get("cos_phi", PLAN_COS_PHI_DEFAULT))
        plan_eta = float(charger_plan.get("eta", PLAN_LOAD_ETA_DEFAULT))
    else:
        cp = _normalize_charger_plan(charger_plan)
        n60 = int(cp["n60"])
        n120 = int(cp["n120"])
        n480 = int(cp["n480"])
        ultra_on = str(cp.get("ultra_on", "否"))
        plan_kx = float(cp.get("kx", DEMAND_COEFF_KX_DEFAULT))
        plan_kt = float(cp.get("kt", SIMULTANEITY_KT_DEFAULT))
        plan_cos = float(cp.get("cos_phi", PLAN_COS_PHI_DEFAULT))
        plan_eta = float(cp.get("eta", PLAN_LOAD_ETA_DEFAULT))
        plan_mode = CHARGER_CONFIG_MODE_B
        st.caption(
            "**B 自定义**：桩数、储能、配电增容见上节「数量与单价配置」；此处仅填 **充电桩设备单价（万元）**。"
        )
        st.markdown(
            f"上节已选：60kW×{n60} · 120kW×{n120} · 480kW×{n480}套 · "
            f"装机 **{float(cp.get('total_kw', 0)):.0f} kW** · "
            f"占车位 **{int(cp.get('spots_used', 0))}** / {spots}"
        )
        g1, g2, g3 = st.columns(3)
        with g1:
            p60_wan = float(
                st.number_input(
                    "60kW 单价(万元/台)",
                    min_value=0.0,
                    value=float(d.get("price_pile_60_wan", PILE_60_DEFAULT_PRICE_WAN)),
                    step=0.5,
                )
            )
        with g2:
            p120_wan = float(
                st.number_input(
                    "120kW 单价(万元/台)",
                    min_value=0.0,
                    value=float(d.get("price_pile_120_wan", PILE_120_DEFAULT_PRICE_WAN)),
                    step=0.5,
                )
            )
        with g3:
            p480_wan = float(
                st.number_input(
                    "480kW 单价(万元/套)",
                    min_value=0.0,
                    value=float(d.get("price_ultra_480_wan", ULTRA_480_DEFAULT_PRICE_WAN)),
                    step=1.0,
                    disabled=(n480 <= 0),
                )
            )

    _html_flow_zone_close()
    _html_flow_zone_open("2.3 配电增容")
    st.caption(f"第一步增容方案：**{upgrade_mode}**；储充可用约 **{usable_kw:.0f} kW**。")
    if plan_mode == CHARGER_CONFIG_MODE_B:
        k_target = float(charger_plan.get("k_target", baseline_kva))
        pu = float(charger_plan.get("transformer_unit_price", d["transformer_unit_price"]))
        rec_kva = charger_plan.get("recommended_kva")
        d_kva = float(charger_plan.get("delta_kva", 0.0))
        rec_txt = f"**{rec_kva} kVA**" if rec_kva else "—"
        exp_b = float(charger_plan.get("expansion_kva", d_kva))
        base_b = float(charger_plan.get("baseline_kva", baseline_kva))
        st.markdown(
            f"B 模式上节已填：目标容量 **{k_target:.0f} kVA** · 增容单价 **{pu:.0f} 元/kVA** · "
            f"相对剩余缺口 **{exp_b:.1f} kVA**（基准 **{base_b:.0f}** + 缺口 → 建议档位 {rec_txt}）"
        )
        if upgrade_mode == "不增容" and float(charger_plan.get("need_upgrade", False)):
            st.warning(
                "上节测算需增容，但第一步为「不增容」；请返回修改增容方式，或下调桩数/系数。"
            )
    elif upgrade_mode == "不增容":
        k_target = orig_kva
        pu = float(d["transformer_unit_price"])
        st.caption(
            f"当前为 **不增容**：目标容量与第一步原变压器 **{orig_kva:.0f} kVA** 联动，ΔkVA = 0。"
        )
        pile_kw_pre = _pile_config_total_kw(n60, n120, n480)
        req_pre = required_transformer_kva(pile_kw_pre, plan_kx, plan_kt, plan_cos, plan_eta)
        exp_pre = charger_capacity_gap_kva(req_pre, k_remain)
        if exp_pre > 1e-3:
            _, rec_pre, _ = suggested_transformer_upgrade(baseline_kva, exp_pre)
            rec_txt = f"**{rec_pre} kVA**" if rec_pre else "—"
            st.warning(
                f"桩配置相对剩余可用缺口约 **{exp_pre:.1f} kVA**；若需落地建议改第一步增容方式，"
                f"或在基准 {baseline_kva:.0f} kVA 上扩建后目标约 {rec_txt}。"
            )
    else:
        pile_kw_pre = _pile_config_total_kw(n60, n120, n480)
        req_pre = required_transformer_kva(pile_kw_pre, plan_kx, plan_kt, plan_cos, plan_eta)
        exp_pre = charger_capacity_gap_kva(req_pre, k_remain)
        sug_pre, rec_pre, _ = suggested_transformer_upgrade(baseline_kva, exp_pre)
        if exp_pre > 1e-3:
            _html_note_panel(
                f"相对剩余可用需增容 **{exp_pre:.1f} kVA**；原变压器 **{orig_kva:.0f} kVA** 上扩建 → "
                f"建议目标总容量 **{sug_pre:.0f} kVA**"
                + (f"（标准档位 **{rec_pre} kVA**）" if rec_pre else "")
                + "；下方默认值已与原变压器联动，可按建议调高。"
            )
        k_target = float(
            st.number_input(
                "目标变压器总容量(kVA)",
                min_value=float(baseline_kva),
                value=max(float(d.get("kva_target", orig_kva)), orig_kva),
                step=50.0,
                help=(
                    f"默认与第一步原变压器容量联动（约 {orig_kva:.0f} kVA）。"
                    "CAPEX 按 max(0, 目标−基准)×单价计入总投资。"
                ),
                key="a_kva_target",
            )
        )
        pu = float(
            st.number_input(
                "变压器增容单价(元/kVA, 按ΔkVA)",
                min_value=0.0,
                value=float(d["transformer_unit_price"]),
                step=10.0,
            )
        )
        delta_pre = max(0.0, k_target - baseline_kva)
        if delta_pre > 1e-6:
            st.caption(
                f"增容 ΔkVA ≈ **{delta_pre:.1f}** · 投资粗算 **{delta_pre * pu / 1e4:.2f} 万元**（计入测算）"
            )

    _html_flow_zone_close()
    _html_step_divider()
    _html_step_title("第三部分：工商业储能")

    if is_mode_b:
        storage_install = str(charger_plan.get("storage_installed", STORAGE_INSTALL_NO))
        install_storage = storage_install == STORAGE_INSTALL_YES
        sets = int(charger_plan.get("storage_sets", 0))
        e_set = float(
            charger_plan.get("storage_kwh_per_set", STORAGE_KWH_PER_SET_DEFAULT)
        )
        st.caption(
            f"**B 模式**已在上方配置：{'安装' if install_storage else '不安装'}储能"
            + (
                f" · **{sets}** 套 × {e_set:.0f} kWh × {STORAGE_KW_PER_SET_DEFAULT:.0f} kW/套"
                if install_storage
                else ""
            )
            + "；以下填写用途、电价与循环策略。"
        )
    else:
        storage_install = st.radio(
            "是否安装工商业储能",
            options=STORAGE_INSTALL_OPTIONS,
            index=_select_index(
                STORAGE_INSTALL_OPTIONS,
                str(d.get("storage_installed", STORAGE_INSTALL_NO)),
                STORAGE_INSTALL_NO,
            ),
            horizontal=True,
        )
        install_storage = storage_install == STORAGE_INSTALL_YES

    if not install_storage:
        _html_note_panel(
            "不安装储能：跳过 **储能系统设备投资** 与套利收益测算；"
            "第四部分「储能工程」可填 0 或预留，**确认测算时不计入储能工程 CAPEX**。"
        )
        sets = 0
        usage = USAGE_PEAK_VALLEY_ONLY
        cyc = 0.0
        cycle_strategy = CYCLE_STRATEGY_1
        peak_red_kw = 0.0
        basic_save_wan = 0.0
        tf_avoid_wan = 0.0
    else:
        with st.expander("三种「储能主要用途」计算逻辑说明", expanded=False):
            st.markdown(USAGE_STRATEGY_LOGIC_MD)

        if is_mode_b:
            p_kwh = float(
                charger_plan.get("price_storage_per_kwh", d.get("price_storage_per_kwh", 0.65))
            )
            total_kwh = sets * e_set
            invest_wan = total_kwh * p_kwh * 1000.0 / 1e4
            st.caption(
                f"**规模与投资**已在上节「储能配置」填写：装机 **{total_kwh:.0f} kWh** / "
                f"**{sets * STORAGE_KW_PER_SET_DEFAULT:.0f} kW** · 单价 **{p_kwh:.2f} 元/kWh** · "
                f"投资粗算 **{invest_wan:.2f} 万元**。以下选择用途与电价。"
            )
        else:
            _html_substep_title("储能规模与投资")
            s1, s2, s3 = st.columns(3)
            with s1:
                sets = int(
                    st.number_input(
                        "储能套数", min_value=1, value=max(1, int(d["storage_sets"])), step=1
                    )
                )
            with s2:
                e_set = float(
                    st.number_input(
                        "单套容量(kWh)",
                        min_value=1.0,
                        value=float(d["storage_kwh_per_set"]),
                        step=1.0,
                    )
                )
            with s3:
                p_kwh = float(
                    st.number_input(
                        "储能单价(元/kWh)",
                        min_value=0.0,
                        value=float(d["price_storage_per_kwh"]),
                        step=0.01,
                    )
                )
            total_kwh = sets * e_set
            invest_wan = total_kwh * p_kwh * 1000.0 / 1e4
            st.caption(
                f"装机 **{total_kwh:.0f} kWh**（{sets} 套 × {e_set:.0f} kWh/套）· "
                f"投资粗算 **{invest_wan:.2f} 万元**（= 容量 × 单价 × 1000 / 1e4）"
            )

        req_m3_advisory = required_transformer_kva(
            _pile_config_total_kw(n60, n120, n480),
            plan_kx,
            plan_kt,
            plan_cos,
            plan_eta,
        )
        gap_m3_advisory = charger_capacity_gap_kva(req_m3_advisory, k_remain)
        upgrade_wan_hint = (
            max(0.0, float(k_target) - baseline_kva) * float(pu) / 1e4
            if str(upgrade_mode) != "不增容"
            else 0.0
        )
        render_mode3_advisory_tool(
            defaults=d,
            power_factor=pf,
            kva_existing=k_existing,
            kva_remaining=k_remain,
            baseline_kva=baseline_kva,
            peak_price=peak,
            valley_price=valley,
            pile_req_kva=req_m3_advisory,
            gap_kva=gap_m3_advisory,
            storage_sets=sets,
            storage_kwh_per_set=e_set,
            price_storage_per_kwh=p_kwh,
            upgrade_cost_wan_hint=upgrade_wan_hint,
            basic_kw_price_hint=float(d.get("basic_electricity_yuan_per_kw_month", 35.0)),
        )

        _html_substep_title("储能主要用途")
        usage_default = str(
            st.session_state.get("storage_usage_select", str(d.get("usage", USAGE_HYBRID)))
        )
        if usage_default not in USAGE_OPTIONS:
            usage_default = USAGE_HYBRID
        if "storage_usage_select" not in st.session_state:
            st.session_state["storage_usage_select"] = usage_default
        usage = st.selectbox(
            "储能主要用途",
            options=USAGE_OPTIONS,
            help="决定年收益公式；纯动态增容不计储能循环套利。可先运行上方「模式三适用性测算」。",
            key="storage_usage_select",
        )
        is_dynamic_usage = usage == USAGE_DYNAMIC_CAPACITY

        if is_dynamic_usage:
            _html_substep_title("动态增容参数")
            dyn1, dyn2, dyn3 = st.columns(3)
            with dyn1:
                peak_red_kw = float(
                    st.number_input(
                        "动态增容值(kW)",
                        min_value=0.0,
                        value=float(d.get("peak_reduction_kw", 0.0)),
                        step=10.0,
                        help="储能削峰可减少的峰值需量/负荷（kW）。",
                    )
                )
            with dyn2:
                basic_kw_price = float(
                    st.number_input(
                        "需量/基本电价(元/kW·月)",
                        min_value=0.0,
                        value=float(d.get("basic_electricity_yuan_per_kw_month", 35.0)),
                        step=1.0,
                    )
                )
            with dyn3:
                basic_save_wan = float(
                    st.number_input(
                        "年节省基本电费手填(万元/年,0=用公式)",
                        min_value=0.0,
                        value=float(d.get("basic_electricity_saved_wan_per_year", 0.0)),
                        step=5.0,
                    )
                )
            est_save_yuan = peak_red_kw * basic_kw_price * 12.0
            st.caption(
                f"公式估算年节省基本电费约 **{est_save_yuan/1e4:.2f} 万元/年** "
                f"（= 动态增容值 × 需量电价 × 12）；手填>0 时优先用手填。"
            )
            d4, d5 = st.columns(2)
            with d4:
                tf_avoid_wan = float(
                    st.number_input(
                        "避开变压器投资(万元,一次性)",
                        min_value=0.0,
                        value=float(d.get("transformer_avoided_wan", 0.0)),
                        step=10.0,
                    )
                )
            with d5:
                tf_avoid_years = int(
                    st.number_input(
                        "变压器投资折旧年限(年)",
                        min_value=1,
                        max_value=40,
                        value=int(d.get("transformer_avoided_depreciation_years", 15)),
                        step=1,
                    )
                )
            cyc = 0.0
            cycle_strategy = "不适用(纯动态增容)"
            req_dyn = required_transformer_kva(
                _pile_config_total_kw(n60, n120, n480),
                plan_kx,
                plan_kt,
                plan_cos,
                plan_eta,
            )
            dyn_cov = assess_dynamic_storage_coverage(
                peak_reduction_kw=peak_red_kw,
                storage_sets=sets,
                req_kva=req_dyn,
                kva_remaining=k_remain,
                power_factor=pf,
            )
            gap_dyn = float(dyn_cov["gap_kva"])
            if dyn_cov["insufficient"] and gap_dyn > 1e-3:
                sug_dyn, rec_dyn, _ = suggested_transformer_upgrade(baseline_kva, gap_dyn)
                st.markdown("**改由变压器增容补足（计入 CAPEX）**")
                st.caption(
                    f"储能削峰/功率不足覆盖缺口 **{dyn_cov['gap_kw']:.0f} kW**（{gap_dyn:.1f} kVA）；"
                    f"建议目标 **{baseline_kva:.0f} + {gap_dyn:.1f} → {sug_dyn:.0f} kVA**"
                    + (f"（标准档位 **{rec_dyn} kVA**）" if rec_dyn else "")
                    + "。修改目标容量后本段提示与下方投资粗算会联动更新。"
                )
                if "dyn_insuf_k_target" not in st.session_state:
                    st.session_state["dyn_insuf_k_target"] = max(
                        float(d.get("kva_target", sug_dyn)),
                        sug_dyn,
                        baseline_kva,
                    )
                k_target = float(
                    st.number_input(
                        "目标变压器总容量(kVA)",
                        min_value=float(baseline_kva),
                        step=50.0,
                        key="dyn_insuf_k_target",
                        help="按您填写的目标容量计算 ΔkVA 与增容投资（不低于基准）。",
                    )
                )
                pu = float(
                    st.number_input(
                        "变压器增容单价(元/kVA)",
                        min_value=0.0,
                        value=float(d["transformer_unit_price"]),
                        step=10.0,
                        key="dyn_insuf_pu",
                    )
                )
                if transformer_target_covers_capacity_gap(k_target, baseline_kva, gap_dyn):
                    _html_note_panel(
                        f"当前目标 **{k_target:.0f} kVA** 可补足容量缺口；增容 ΔkVA ≈ "
                        f"**{max(0.0, k_target - baseline_kva):.1f}**（见下方投资粗算）。"
                    )
                else:
                    st.error(
                        f"**容量不足**：目标 **{k_target:.0f} kVA** 仍低于建议 **{sug_dyn:.0f} kVA**，"
                        "请提高目标容量或储能削峰/功率。"
                    )
            else:
                k_target = baseline_kva
                if gap_dyn <= 1e-3:
                    st.success("动态增容能力可满足当前桩配置相对剩余可用的容量需求，**不计**变压器增容 CAPEX。")
                else:
                    st.success(
                        f"动态增容 **{peak_red_kw:.0f} kW** 可覆盖缺口约 **{dyn_cov['gap_kw']:.0f} kW**，"
                        "**不计**变压器增容 CAPEX。"
                    )
        else:
            _html_substep_title("充放策略与分时电价")
            cycle_strategy = st.selectbox(
                "充放策略",
                options=CYCLE_STRATEGY_OPTIONS,
                index=_select_index(
                    CYCLE_STRATEGY_OPTIONS,
                    strategy_from_cycles(float(d.get("storage_cycles_per_day", 1.0))),
                    CYCLE_STRATEGY_1,
                ),
                help="一充一放=1 次/天；二充二放=2 次/天；1.5充1.5放=1.5 次/天。",
            )
            cyc = cycles_from_strategy(cycle_strategy)
            st.caption(f"对应储能日循环 **{cyc:.1f} 次/天**（写入测算模型）。")

            pc1, pc2, pc3 = st.columns(3)
            with pc1:
                peak = float(
                    st.number_input(
                        "高峰电价(元/kWh)", min_value=0.0, value=float(d["peak_price"]), step=0.01
                    )
                )
            with pc2:
                valley = float(
                    st.number_input(
                        "低谷电价(元/kWh)", min_value=0.0, value=float(d["valley_price"]), step=0.01
                    )
                )
            with pc3:
                flat_ = float(
                    st.number_input(
                        "平时电价(元/kWh)", min_value=0.0, value=float(d["flat_price"]), step=0.01
                    )
                )
            st.caption("峰/谷电价用于储能套利及「峰谷套利+充电」模式下的购电成本粗算。")

            se1, se2 = st.columns(2)
            with se1:
                eta = float(
                    st.number_input(
                        "储能效率(0–1)",
                        min_value=0.0,
                        max_value=1.0,
                        value=float(d["round_trip_efficiency"]),
                        step=0.01,
                    )
                )
            peak_red_kw = 0.0
            basic_save_wan = 0.0
            tf_avoid_wan = 0.0

    req_plan_kva_form = required_transformer_kva(
        _pile_config_total_kw(n60, n120, n480),
        plan_kx,
        plan_kt,
        plan_cos,
        plan_eta,
    )
    render_transformer_investment_note(
        usage=str(usage),
        upgrade_mode=upgrade_mode,
        baseline_kva=baseline_kva,
        k_target=k_target,
        pu=pu,
        req_plan_kva=req_plan_kva_form,
        k_remain=k_remain,
        pf=pf,
        install_storage=install_storage,
        peak_reduction_kw=peak_red_kw,
        storage_sets=sets,
    )

    _html_step_divider()
    _html_step_title("第四部分：施工成本")
    st.caption(
        "按下方顺序填写：**先完成桩侧施工 → 微网（若需要）→ 储能（若安装）→ 场地与增容物料**；"
        "各工程块之间为虚线分隔，金额按「元」录入、结果以万元展示。"
    )
    plan_kt_micro = float(
        charger_plan.get("kt", d.get("simultaneity_coeff_kt", SIMULTANEITY_KT_DEFAULT))
    )
    charger_eng = render_charger_engineering_inputs(
        d,
        farthest_m,
        n_pile_60=n60,
        n_pile_120=n120,
        n_ultra_480=n480,
    )
    mg_equip_yuan, mg_debug_yuan = render_other_equipment_microgrid(d, plan_kt_micro)

    install_storage_form = storage_install != STORAGE_INSTALL_NO
    storage_eng = render_storage_engineering_inputs(
        d, farthest_m, storage_installed=install_storage_form
    )

    per_spot, other_wan, _ = render_related_materials_section(
        d,
        spots=spots,
        usage=str(usage),
        storage_install=str(storage_install),
        storage_sets=sets,
        peak_reduction_kw=peak_red_kw,
        kva_existing=k_existing,
        kva_remaining_available=k_remain,
        kva_target=k_target,
        transformer_unit_price=pu,
        upgrade_mode=upgrade_mode,
        power_factor=pf,
        n_pile_60=n60,
        n_pile_120=n120,
        n_ultra_480=n480,
        plan_kx=plan_kx,
        plan_kt=plan_kt,
        plan_cos=plan_cos,
        plan_eta=plan_eta,
    )

    _html_step_divider()
    _html_step_title("第五部分：运营与测算")
    st.caption(
        "顺序：**运营参数 → 政府补贴 → 全站假设**；可选 **财务报表**。未安装储能时不显示储能运营区。"
    )

    _html_flow_zone_open("5.1 运营参数")
    _html_op_param_box("充电桩运营")
    co1, co2 = st.columns(2)
    with co1:
        d_op = float(
            st.number_input(
                "年运营天数",
                min_value=0.0,
                value=float(d["operating_days"]),
                step=1.0,
                key="op_charger_operating_days",
            )
        )
    with co2:
        fee = float(
            st.number_input(
                "充电服务费(元/kWh)",
                min_value=0.0,
                value=float(d["service_fee_per_kwh"]),
                step=0.01,
                key="op_charger_service_fee",
            )
        )
    co3, co4 = st.columns(2)
    with co3:
        e_day = float(
            st.number_input(
                "常规单枪日均(kWh)",
                min_value=0.0,
                value=float(d["energy_per_gun_day"]),
                step=10.0,
                help="120kW 按双枪计；本字段为「单枪」日均。",
                key="op_charger_energy_per_gun",
            )
        )
    with co4:
        e_ultra_day = float(
            st.number_input(
                "超充单终端日均(kWh)",
                min_value=0.0,
                value=float(d["ultra_energy_per_gun_day"]),
                step=10.0,
                help=f"液冷按套填时，放电枪数 = 套数 × {ULTRA_CHARGE_TERMINALS_PER_RACK} 终端。",
                key="op_charger_ultra_energy",
            )
        )
    render_charger_service_fee_calc_preview(
        operating_days=d_op,
        service_fee_per_kwh=fee,
        n_pile_60=n60,
        n_pile_120=n120,
        n_ultra_480=n480,
        energy_per_gun_day=e_day,
        ultra_energy_per_gun_day=e_ultra_day,
        usage=str(usage),
    )
    _html_op_param_box_end()

    if install_storage:
        _html_op_param_box("储能运营")
        if usage == USAGE_DYNAMIC_CAPACITY:
            st.caption(
                "与 **第三部分 · 储能主要用途** 联动：当前为 **纯动态增容+充电**，"
                "不计一充一放/日循环；下方按削峰、基本电费与充电服务费粗算。"
            )
        else:
            st.caption(
                "与 **第三部分**（用途、充放策略、峰/谷电价、效率）联动；"
                "下方为年化收入粗算（确认测算后右侧结果表为最终数）。"
            )
        preview_cyc = 0.0 if usage == USAGE_DYNAMIC_CAPACITY else cyc
        preview_cycle_strategy = (
            "" if usage == USAGE_DYNAMIC_CAPACITY else str(cycle_strategy)
        )
        render_storage_operations_preview(
            usage=str(usage),
            storage_sets=sets,
            storage_kwh_per_set=e_set,
            price_storage_per_kwh=p_kwh,
            storage_cycles_per_day=preview_cyc,
            round_trip_efficiency=eta,
            peak_price=peak,
            valley_price=valley,
            operating_days=d_op,
            service_fee_per_kwh=fee,
            n_pile_60=n60,
            n_pile_120=n120,
            n_ultra_480=n480,
            energy_per_gun_day=e_day,
            ultra_energy_per_gun_day=e_ultra_day,
            peak_reduction_kw=peak_red_kw,
            basic_electricity_yuan_per_kw_month=basic_kw_price,
            basic_electricity_saved_wan_per_year=basic_save_wan,
            transformer_avoided_wan=tf_avoid_wan,
            transformer_avoided_depreciation_years=tf_avoid_years,
            cycle_strategy=preview_cycle_strategy,
        )
        _html_op_param_box_end()
    _html_flow_zone_close()

    _html_flow_zone_open("5.2 政府补贴")
    _html_substep_title("政府补贴")
    _gov_mode_default = str(d.get("gov_subsidy_mode", GOV_SUBSIDY_NONE))
    if _gov_mode_default not in GOV_SUBSIDY_MODE_OPTIONS:
        _gov_mode_default = GOV_SUBSIDY_NONE
    gov_subsidy_mode = st.radio(
        "补贴方式",
        options=GOV_SUBSIDY_MODE_OPTIONS,
        index=_select_index(GOV_SUBSIDY_MODE_OPTIONS, _gov_mode_default, GOV_SUBSIDY_NONE),
        horizontal=True,
        key="op_gov_subsidy_mode",
    )
    gov_subsidy_once_wan = float(d.get("gov_subsidy_once_wan", 0.0))
    gov_subsidy_annual_wan = float(d.get("gov_subsidy_annual_wan", 0.0))
    gov_subsidy_years = int(d.get("gov_subsidy_years", 5))
    _horizon_hint = max(3, int(d.get("horizon_years", 15)))
    if gov_subsidy_mode == GOV_SUBSIDY_ONCE:
        gov_subsidy_once_wan = float(
            st.number_input(
                "一次性补贴(万元)",
                min_value=0.0,
                value=gov_subsidy_once_wan,
                step=10.0,
                key="op_gov_subsidy_once",
            )
        )
        st.caption(
            "计入 **第 1 个运营年** 收入与现金流；右侧「年净收益」为不含该笔的 **稳态运营年** 口径。"
        )
    elif gov_subsidy_mode == GOV_SUBSIDY_INSTALLMENT:
        gi1, gi2 = st.columns(2)
        with gi1:
            gov_subsidy_annual_wan = float(
                st.number_input(
                    "每年补贴(万元/年)",
                    min_value=0.0,
                    value=gov_subsidy_annual_wan,
                    step=1.0,
                    key="op_gov_subsidy_annual",
                )
            )
        with gi2:
            gov_subsidy_years = int(
                st.number_input(
                    "补贴年限(年)",
                    min_value=1,
                    max_value=40,
                    value=max(1, min(gov_subsidy_years, _horizon_hint)),
                    step=1,
                    key="op_gov_subsidy_years",
                )
            )
        st.caption(
            f"第 1–{gov_subsidy_years} 年每年计入收入；超过现金流测算年限时按年限截断。"
        )
    else:
        gov_subsidy_once_wan = 0.0
        gov_subsidy_annual_wan = 0.0
        gov_subsidy_years = 0
    _html_flow_zone_close()

    _html_flow_zone_open("5.3 全站测算假设")
    _html_substep_title("全站测算假设")
    g1, g2, g3 = st.columns(3)
    with g1:
        vpp_wan = float(
            st.number_input(
                "VPP/需求响应(万元/年)",
                min_value=0.0,
                value=float(d["vpp_revenue_wan"]),
                step=1.0,
                key="op_global_vpp",
            )
        )
    with g2:
        opex_ratio = (
            float(
                st.number_input(
                    "年运维占投资(%)",
                    min_value=0.0,
                    value=float(annual_opex_pct),
                    step=0.1,
                    key="op_global_opex_pct",
                )
            )
            / 100.0
        )
    with g3:
        horizon = int(
            st.number_input(
                "现金流/IRR 年限(年)",
                min_value=3,
                max_value=40,
                value=int(d["horizon_years"]),
                step=1,
                key="op_global_horizon",
            )
        )

    _html_flow_zone_close()
    _html_flow_zone_open("5.4 财务报表（可选）")
    st.caption(
        "勾选后右侧展示 **利润表**、**现金流量表** 与 **NPV/XIRR/PI/静态回收期**；"
        "收入与成本均为 **不含税**；增值税留抵逐年结转；所得税 **三免三减半** 并弥补亏损；"
        "储能收入与充电成本随 **SOH** 衰减。"
    )
    calc_fin = st.checkbox(
        "是否计算利润表、现金流量表与财务评价指标",
        value=bool(d.get("calc_financial_statements", False)),
        key="op_fin_calc_statements",
    )
    if calc_fin:
            f1, f2, f3 = st.columns(3)
            with f1:
                vat_rate = float(
                    st.number_input(
                        "增值税率",
                        min_value=0.0,
                        max_value=0.3,
                        value=float(d.get("vat_rate", DEFAULT_VAT_RATE)),
                        step=0.01,
                        format="%.2f",
                        key="op_fin_vat_rate",
                    )
                )
                dep_years = int(
                    st.number_input(
                        "折旧年限(年)",
                        min_value=1,
                        max_value=30,
                        value=int(d.get("depreciation_years", DEFAULT_DEPRECIATION_YEARS)),
                        step=1,
                        key="op_fin_dep_years",
                    )
                )
            with f2:
                income_tax_rate = float(
                    st.number_input(
                        "所得税率",
                        min_value=0.0,
                        max_value=0.5,
                        value=float(d.get("income_tax_rate", DEFAULT_INCOME_TAX_RATE)),
                        step=0.01,
                        format="%.2f",
                        key="op_fin_income_tax",
                    )
                )
                salvage_rate = float(
                    st.number_input(
                        "残值率(占可折旧资产)",
                        min_value=0.0,
                        max_value=1.0,
                        value=float(d.get("salvage_rate", DEFAULT_SALVAGE_RATE)),
                        step=0.01,
                        format="%.2f",
                        key="op_fin_salvage",
                    )
                )
            with f3:
                discount_rate = float(
                    st.number_input(
                        "折现率(全投资)",
                        min_value=0.0,
                        max_value=0.3,
                        value=float(d.get("discount_rate", DEFAULT_DISCOUNT_RATE)),
                        step=0.005,
                        format="%.3f",
                        key="op_fin_discount",
                    )
                )
                anc_times = float(
                    st.number_input(
                        "辅助服务次数(次/年)",
                        min_value=0.0,
                        value=float(d.get("ancillary_times_per_year", 0.0)),
                        step=1.0,
                        key="op_fin_anc_times",
                    )
                )
                anc_price = float(
                    st.number_input(
                        "辅助服务单价(元/次)",
                        min_value=0.0,
                        value=float(d.get("ancillary_unit_price_yuan", 0.0)),
                        step=100.0,
                        key="op_fin_anc_price",
                    )
                )
    else:
        vat_rate = float(d.get("vat_rate", DEFAULT_VAT_RATE))
        income_tax_rate = float(d.get("income_tax_rate", DEFAULT_INCOME_TAX_RATE))
        discount_rate = float(d.get("discount_rate", DEFAULT_DISCOUNT_RATE))
        dep_years = int(d.get("depreciation_years", DEFAULT_DEPRECIATION_YEARS))
        salvage_rate = float(d.get("salvage_rate", DEFAULT_SALVAGE_RATE))
        anc_times = float(d.get("ancillary_times_per_year", 0.0))
        anc_price = float(d.get("ancillary_unit_price_yuan", 0.0))
    _html_flow_zone_close()

    spots_used_submit = charger_parking_spots_used(n60, n120, n480)
    if spots_used_submit > spots:
        st.warning(
            f"占用车位 **{spots_used_submit}** / **{spots}** 个。{PARKING_SPOTS_EXPAND_HINT}"
        )

    reverse_refresh = bool(st.session_state.pop("reverse_refresh_pending", False))

    submitted = st.button(
        "项目正向收益及回收期测算",
        type="primary",
        use_container_width=True,
    )

    if not submitted and not reverse_refresh:
        return False, None, []

    if install_storage and sets < 1:
        errs.append("安装储能时，储能套数须至少为 1")
    if install_storage and usage != USAGE_DYNAMIC_CAPACITY and peak < valley:
        errs.append("高峰电价应不小于低谷电价（请检查分时电价）")
    baseline_kva = _transformer_baseline_kva(k_existing, k_remain)
    if plan_mode == CHARGER_CONFIG_MODE_B:
        k_target = float(
            st.session_state.get(
                "charger_plan_b_k_target",
                charger_plan.get("k_target", baseline_kva),
            )
        )
        pu = float(
            st.session_state.get(
                "charger_plan_b_pu",
                charger_plan.get("transformer_unit_price", d["transformer_unit_price"]),
            )
        )
    pile_kw = _pile_config_total_kw(n60, n120, n480)
    req_plan_kva = required_transformer_kva(pile_kw, plan_kx, plan_kt, plan_cos, plan_eta)
    gap_submit = charger_capacity_gap_kva(req_plan_kva, k_remain)
    if (
        install_storage
        and usage != USAGE_DYNAMIC_CAPACITY
        and upgrade_mode != "不增容"
        and k_target < baseline_kva
    ):
        errs.append("目标容量通常不小于额定/剩余可用基准容量（若技改下调可忽略本条）")
    dyn_insuf_submit = False
    if install_storage and usage == USAGE_DYNAMIC_CAPACITY:
        dyn_insuf_submit = assess_dynamic_storage_coverage(
            peak_reduction_kw=peak_red_kw,
            storage_sets=sets,
            req_kva=req_plan_kva,
            kva_remaining=k_remain,
            power_factor=pf,
        )["insufficient"]
        if not dyn_insuf_submit:
            k_target = baseline_kva
        elif not transformer_target_covers_capacity_gap(k_target, baseline_kva, gap_submit):
            errs.append(
                f"储能动态增容不足，且目标变压器总容量 {k_target:.0f} kVA 未达建议下限 "
                f"{baseline_kva + gap_submit:.0f} kVA（= 基准 + 相对缺口 {gap_submit:.1f} kVA）。"
            )
    if not install_storage:
        sets = 0
        cyc = 0.0
        usage = USAGE_PEAK_VALLEY_ONLY
        if upgrade_mode != "不增容" and k_target < baseline_kva:
            errs.append("目标容量通常不小于额定/剩余可用基准容量（若技改下调可忽略本条）")
    if k_remain <= 0:
        errs.append("剩余可用变压器容量须大于 0（请返回第一步填写）")

    dyn_covers = False
    if install_storage and usage == USAGE_DYNAMIC_CAPACITY:
        dyn_covers = assess_dynamic_storage_coverage(
            peak_reduction_kw=peak_red_kw,
            storage_sets=sets,
            req_kva=req_plan_kva,
            kva_remaining=k_remain,
            power_factor=pf,
        )["covers"]
    tf_covers_gap = transformer_target_covers_capacity_gap(
        k_target, baseline_kva, gap_submit
    )
    if (
        pile_kw > 0
        and gap_submit > 1e-3
        and not dyn_covers
        and usage != USAGE_DYNAMIC_CAPACITY
        and not tf_covers_gap
    ):
        need_target = baseline_kva + gap_submit
        errs.append(
            f"充电桩需用变压器约 {req_plan_kva:.1f} kVA，相对剩余可用缺口 {gap_submit:.1f} kVA；"
            f"请减桩、储能动态增容补足，或将 **目标变压器总容量** 提高到不低于 "
            f"**{need_target:.0f} kVA**（= 基准 {baseline_kva:.0f} + 缺口）。"
            f"当前填写 **{k_target:.0f} kVA**。"
        )
    if (
        dyn_insuf_submit
        and upgrade_mode == "不增容"
        and not transformer_target_covers_capacity_gap(k_target, baseline_kva, gap_submit)
    ):
        errs.append(
            "储能动态增容能力不足（容量不足），且第一步为「不增容」；请返回改增容方式、"
            "提高目标变压器容量/动态增容值，或减桩。"
        )
    if plan_mode == CHARGER_CONFIG_MODE_A and n480 > 0 and not charger_plan.get("can_480"):
        errs.append("当前剩余容量或车位不足，不宜配置 480kW 液冷（见上节提示）。")
    if plan_mode == CHARGER_CONFIG_MODE_B:
        if upgrade_mode == "不增容" and bool(charger_plan.get("need_upgrade")):
            errs.append(
                "B 模式测算需增容，但第一步为「不增容」；请返回第一步改选原址换大/高压申请，或减桩。"
            )

    inp = InputModel(
        station_city=ct1,
        parking_vehicle_type=vehicle,
        parking_slot_size_desc=_slot_desc_from_vehicle(vehicle),
        transformer_upgrade_mode=upgrade_mode,
        transformer_to_farthest_m=farthest_m,
        site_layout=layout,
        peak_price=peak,
        valley_price=valley,
        flat_price=flat_,
        kva_existing=k_existing,
        kva_remaining_available=k_remain,
        power_factor=pf,
        kva_target=k_target,
        ultra_480_enabled=str(ultra_on),
        charger_config_mode=plan_mode,
        demand_coeff_kx=plan_kx,
        simultaneity_coeff_kt=plan_kt,
        planning_cos_phi=plan_cos,
        planning_load_rate_eta=plan_eta,
        storage_installed=str(storage_install),
        cycle_strategy=str(cycle_strategy),
        usage=str(usage),
        n_pile_60=n60,
        n_pile_120=n120,
        price_pile_60_wan=p60_wan,
        price_pile_120_wan=p120_wan,
        n_ultra_480=n480,
        price_ultra_480_wan=p480_wan,
        storage_sets=sets,
        price_storage_per_kwh=p_kwh,
        storage_kwh_per_set=e_set,
        peak_reduction_kw=peak_red_kw,
        basic_electricity_yuan_per_kw_month=basic_kw_price,
        basic_electricity_saved_wan_per_year=basic_save_wan,
        transformer_avoided_wan=tf_avoid_wan,
        transformer_avoided_depreciation_years=tf_avoid_years,
        transformer_unit_price=pu,
        charger_engineering=charger_eng,
        storage_engineering=storage_eng,
        parking_spots=spots,
        parking_cost_per_spot_wan=per_spot,
        other_construction_wan=other_wan,
        microgrid_controller_equip_wan=mg_equip_yuan / 1e4,
        microgrid_controller_debug_wan=mg_debug_yuan / 1e4,
        operating_days=d_op,
        service_fee_per_kwh=fee,
        energy_per_gun_day=e_day,
        ultra_energy_per_gun_day=e_ultra_day,
        storage_cycles_per_day=cyc,
        round_trip_efficiency=eta,
        opex_ratio=opex_ratio,
        vpp_revenue_wan=vpp_wan,
        gov_subsidy_mode=str(gov_subsidy_mode),
        gov_subsidy_once_wan=gov_subsidy_once_wan,
        gov_subsidy_annual_wan=gov_subsidy_annual_wan,
        gov_subsidy_years=max(0, int(gov_subsidy_years)),
        horizon_years=horizon,
        calc_financial_statements=calc_fin,
        vat_rate=vat_rate,
        income_tax_rate=income_tax_rate,
        discount_rate=discount_rate,
        depreciation_years=dep_years,
        salvage_rate=salvage_rate,
        ancillary_times_per_year=anc_times,
        ancillary_unit_price_yuan=anc_price,
    )

    if reverse_refresh:
        if errs:
            st.session_state.reverse_refresh_errs = list(errs)
        else:
            st.session_state.reverse_baseline = _build_reverse_baseline(inp)
            st.session_state.reverse_refresh_errs = []
        return False, None, errs

    if errs:
        return True, None, errs
    return True, inp, []


def _inp_without_gov_subsidy(inp: InputModel) -> InputModel:
    """逆向分析基线：剔除第五步已填政府补贴。作者: jiali.qiu"""
    return replace(
        inp,
        gov_subsidy_mode=GOV_SUBSIDY_NONE,
        gov_subsidy_once_wan=0.0,
        gov_subsidy_annual_wan=0.0,
        gov_subsidy_years=0,
    )


def _build_reverse_baseline(inp: InputModel) -> ForwardBaseline:
    """从正向 projection 提取无补贴基线（仅依赖 compute_projection 返回值）。"""
    res = compute_projection(_inp_without_gov_subsidy(inp))
    net_op = float(res.get("net_operating_year", res["net_year"]))
    return baseline_from_projection(
        float(res["capex_total"]),
        net_op,
        int(inp.horizon_years),
    )


def _init_session_state() -> None:
    if "schemes" not in st.session_state:
        st.session_state.schemes = []
    if "confirmed_inp" not in st.session_state:
        st.session_state.confirmed_inp = None
    if "form_submit_errs" not in st.session_state:
        st.session_state.form_submit_errs = []
    if "site_basics_confirmed" not in st.session_state:
        st.session_state.site_basics_confirmed = False
    if "site_basics" not in st.session_state:
        st.session_state.site_basics = {}
    if "reverse_baseline" not in st.session_state:
        st.session_state.reverse_baseline = None
    if "reverse_refresh_errs" not in st.session_state:
        st.session_state.reverse_refresh_errs = []
    if "reverse_refresh_pending" not in st.session_state:
        st.session_state.reverse_refresh_pending = False


def _render_capex_breakdown_grouped(res: dict[str, Any]) -> None:
    """右侧投资分项：按第四部分工程块展示，块间虚线分隔。"""
    bd = res.get("capex_breakdown", {})
    sec = res.get("capex_section_totals", {})
    if not bd and not sec:
        return

    _html_substep_title("投资成本分项")
    device_rows: list[tuple[str, float]] = []
    for k, v in bd.items():
        if k.startswith("【合计】") or "·" in k:
            continue
        if k.startswith("充电桩工程") or k.startswith("储能工程") or k.startswith("相关物料"):
            continue
        if "微网" in k or "变压器" in k:
            continue
        if abs(v) > 1e-6:
            device_rows.append((k, float(v)))
    if device_rows:
        _html_block_title("设备购置与变压器")
        for k, v in device_rows:
            st.markdown(f"- {k}：**{v / 1e4:.2f}** 万元")

    section_order = ("充电桩工程", "微网控制器", "储能工程", "相关物料")
    first_block = not device_rows
    for name in section_order:
        total = float(sec.get(name, 0.0))
        detail_items = [
            (k, float(v))
            for k, v in bd.items()
            if (
                k.startswith(f"{name}·")
                or (name == "微网控制器" and k == "微网控制器(设备+调试)")
            )
            and abs(v) > 1e-6
        ]
        if total < 1e-6 and not detail_items:
            continue
        if not first_block:
            _html_construction_section_divider()
        first_block = False
        _html_construction_subsection_title(name)
        st.markdown(f"**本分项合计：{total / 1e4:.2f} 万元**")
        for k, v in detail_items:
            label = k.split("·", 1)[-1] if "·" in k else k
            st.markdown(f"- {label}：{v / 1e4:.2f} 万元")


def _render_revenue_calc_details(inp: InputModel, res: dict[str, Any]) -> None:
    """结果区：储能相关 / 充电服务费年化计算过程（与表单预览同口径）。"""
    rev = res.get("revenue_breakdown", {})
    if not rev:
        return
    meta = res.get("meta", {})
    usage = str(inp.usage)
    days = max(0.0, float(inp.operating_days))
    peak = float(inp.peak_price)
    valley = float(inp.valley_price)
    eta = float(np.clip(inp.round_trip_efficiency, 1e-6, 1.0))
    fee = float(inp.service_fee_per_kwh)
    kwh_day = float(meta.get("日总充电量(kWh)", _daily_charge_kwh(inp)))
    storage_kwh = float(res.get("storage_kwh_total", 0.0)) * battery_soh_factor(1)
    cyc = 0.0 if usage == USAGE_DYNAMIC_CAPACITY else max(0.0, float(inp.storage_cycles_per_day))
    storage_keys = _storage_revenue_line_keys(usage)
    storage_rev = sum(float(rev.get(k, 0.0)) for k in storage_keys)
    charger_rev = float(rev.get("充电服务费(对用户,毛)", 0.0))

    with st.expander("年收入 · 计算过程核对", expanded=False):
        if storage_rev > 1e-6 or str(inp.storage_installed) != STORAGE_INSTALL_NO:
            st.markdown("**储能相关年化收入**")
            _html_revenue_calc_box(
                _storage_revenue_calc_lines(
                    usage,
                    storage_kwh=storage_kwh,
                    cyc=cyc,
                    kwh_day=kwh_day,
                    days=days,
                    peak=peak,
                    valley=valley,
                    eta=eta,
                    fee=fee,
                    rev_bd=rev,
                    storage_keys=storage_keys,
                    peak_reduction_kw=float(inp.peak_reduction_kw),
                    basic_electricity_yuan_per_kw_month=float(inp.basic_electricity_yuan_per_kw_month),
                    basic_electricity_saved_wan_per_year=float(inp.basic_electricity_saved_wan_per_year),
                    transformer_avoided_wan=float(inp.transformer_avoided_wan),
                    transformer_avoided_depreciation_years=int(inp.transformer_avoided_depreciation_years),
                )
            )
        if usage == USAGE_HYBRID:
            st.markdown("**充电服务费（参考，已并入储充毛利）**")
        elif charger_rev > 1e-6:
            st.markdown("**充电服务费年化**")
        if usage == USAGE_HYBRID or charger_rev > 1e-6:
            _html_revenue_calc_box(
                _charger_service_fee_calc_lines(
                    kwh_day,
                    fee,
                    days,
                    n_pile_60=inp.n_pile_60,
                    n_pile_120=inp.n_pile_120,
                    n_ultra_480=inp.n_ultra_480,
                    energy_per_gun_day=inp.energy_per_gun_day,
                    ultra_energy_per_gun_day=inp.ultra_energy_per_gun_day,
                )
            )


def render_estimate_panel(inp: InputModel) -> None:
    """右侧：正向收益与逆向分析（解耦展示）。"""
    try:
        _html_module_title("项目收益与回收期")
        _render_forward_estimate_body(inp)
        st.markdown('<hr class="ui-step-divider" />', unsafe_allow_html=True)
        _html_module_title("项目「逆向分析」")
        _render_reverse_target_payback_subsidy(inp)
    except Exception as exc:
        st.error(f"测算结果渲染失败：{exc}")
        with st.expander("错误详情（供排查）"):
            st.exception(exc)


def _render_reverse_target_payback_subsidy(inp: InputModel) -> None:
    """
    逆向分析 · 目标回收期下的补贴需求（独立模块，基线不含政府补贴）。
    作者: jiali.qiu
    """
    _html_substep_title("目标回收期下的补贴需求")
    st.caption(
        "基线取自 **成本（含施工）+ 运营参数 + 全站测算假设**，**不含**第五步政府补贴。"
        "改左侧参数后请点 **刷新逆向基线**（将按当前表单重算基线，不更新上方正向结果）。"
    )

    r1, r2 = st.columns([1, 1])
    with r1:
        if st.button("刷新逆向基线", type="secondary", use_container_width=True, key="btn_reverse_refresh"):
            st.session_state.reverse_refresh_pending = True
            st.rerun()
    with r2:
        st.caption("需左侧表单校验通过；未改参数时与最近一次正向测算一致。")

    for e in st.session_state.get("reverse_refresh_errs") or []:
        st.error(e)

    baseline = st.session_state.get("reverse_baseline")
    if baseline is None:
        baseline = _build_reverse_baseline(inp)
        st.session_state.reverse_baseline = baseline

    capex_wan = baseline.capex_yuan / 1e4
    net_wan = baseline.net_operating_year_yuan / 1e4
    pb0_s = baseline.payback_static_years
    pb0_i = baseline.payback_interp_years
    pb0_s_txt = f"{pb0_s:.2f}" if np.isfinite(pb0_s) else "—"
    pb0_i_txt = f"{pb0_i:.2f}" if pb0_i is not None else "—"
    _html_note_panel(
        f"基线：总投资 **{capex_wan:,.2f}** 万元 · 稳态年净收益 **{net_wan:,.2f}** 万元/年 · "
        f"测算 **{baseline.horizon_years}** 年 · 无补贴回本：静态 **{pb0_s_txt}** 年 / 插值 **{pb0_i_txt}** 年"
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        target_pb = float(
            st.number_input(
                "目标回本周期(年)",
                min_value=0.0,
                max_value=20.0,
                value=5.0,
                step=0.5,
                key="rev_target_payback_years",
            )
        )
    with c2:
        metric_label = st.radio(
            "达标口径",
            options=["插值回本", "静态回本"],
            horizontal=True,
            key="rev_payback_metric",
        )
        metric: PaybackMetric = "interp" if metric_label == "插值回本" else "static"
    with c3:
        sub_mode_label = st.radio(
            "反算补贴形态",
            options=["一次性(第1年)", "分期(每年)"],
            horizontal=True,
            key="rev_subsidy_mode",
        )
        sub_mode: SubsidySolveMode = (
            "once" if sub_mode_label.startswith("一次性") else "installment"
        )

    inst_years = max(1, min(int(inp.horizon_years), int(inp.gov_subsidy_years or 5)))
    if sub_mode == "installment":
        inst_years = int(
            st.number_input(
                "分期补贴年限(年)",
                min_value=1,
                max_value=max(1, int(inp.horizon_years)),
                value=max(1, inst_years),
                step=1,
                key="rev_installment_years",
            )
        )

    run_rev = st.button("计算补贴需求", type="primary", key="btn_reverse_calc")

    if run_rev:
        result = solve_subsidy_for_target_payback(
            baseline,
            target_pb,
            metric=metric,
            subsidy_mode=sub_mode,
            installment_years=inst_years,
        )
        st.session_state.reverse_last_result = result
    else:
        result = st.session_state.get("reverse_last_result")

    if result is None:
        st.caption("填写目标回本周期后点击 **计算补贴需求**。")
        return

    if result.ok:
        if result.already_met:
            st.success(result.message)
        else:
            st.success(result.message)
        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("所需补贴(万元)", f"{result.subsidy_wan:,.2f}")
        with m2:
            pb_s = result.payback_static_years
            st.metric("含补贴静态回本(年)", f"{pb_s:.2f}" if np.isfinite(pb_s) else "—")
        with m3:
            pb_i = result.payback_interp_years
            st.metric(
                "含补贴插值回本(年)",
                f"{pb_i:.2f}" if pb_i is not None else "—",
            )
        df_cmp = pd.DataFrame(cashflow_compare_rows(baseline, result))
        st.dataframe(df_cmp, use_container_width=True, hide_index=True)
    else:
        st.warning(result.message)


def _render_forward_estimate_body(inp: InputModel) -> None:
    _html_result_zone_title("① 核心指标")
    _render_grid_declaration_notice(
        total_installed_charger_kw(inp.n_pile_60, inp.n_pile_120, inp.n_ultra_480)
    )
    res = compute_projection(inp)
    net_op_yuan = float(res.get("net_operating_year", res["net_year"]))
    cf_wan = build_cash_flows_wan(
        res["capex_total"], net_op_yuan, inp.horizon_years, inp=inp
    )
    irr = irr_annual(cf_wan)
    pb_interp = payback_time_years(cf_wan)

    capex_wan = res["capex_total"] / 1e4
    net_wan = res["net_year"] / 1e4
    pb = res["payback_years"]

    def fmt_pb(x: float) -> str:
        if not np.isfinite(x):
            return "—"
        return f"{x:.2f}"

    kpi_html = f"""
<div class="kpi-wrap">
  <div class="kpi-card">
    <div class="kpi-label">总投资</div>
    <div class="kpi-value">{capex_wan:,.2f}<span class="kpi-unit">万元</span></div>
    <div class="kpi-sub">含充电/储能/增容/施工等</div>
  </div>
  <div class="kpi-card alt">
    <div class="kpi-label">年净收益</div>
    <div class="kpi-value">{net_wan:,.2f}<span class="kpi-unit">万元</span></div>
    <div class="kpi-sub">年收益 − 年运维（静态）</div>
  </div>
  <div class="kpi-card alt2">
    <div class="kpi-label">回本周期（静态）</div>
    <div class="kpi-value">{fmt_pb(pb)}<span class="kpi-unit">年</span></div>
    <div class="kpi-sub">插值回本 ≈ {fmt_pb(pb_interp) + ' 年' if pb_interp is not None else '—'}</div>
  </div>
</div>
"""
    st.markdown(kpi_html, unsafe_allow_html=True)
    once_sub_wan = float(res.get("gov_subsidy_once_yuan", 0.0)) / 1e4
    if once_sub_wan > 1e-6:
        st.caption(
            f"政府补贴 **一次性 {once_sub_wan:,.2f} 万元** 已计入第 1 年现金流；"
            "上表「年净收益」为不含该笔的稳态运营年。"
        )

    if str(inp.storage_installed) == STORAGE_INSTALL_NO:
        _html_note_panel(STORAGE_ENG_CAPEX_EXCLUDED_NOTE)
    meta = res.get("meta", {})
    if meta.get("动态增容不足") and meta.get("目标变压器总容量(kVA)") is not None:
        st.warning(
            "储能动态增容（削峰/功率）未单独覆盖全部缺口，本次已按您填写的 **目标变压器总容量** "
            "计入 **变压器增容(动态不足补足)**；请核对 ΔkVA 与分项表。"
        )

    _html_result_zone_title("② 投资与收入")
    _render_capex_breakdown_grouped(res)

    rows: list[dict[str, Any]] = []
    for k, v in res["revenue_breakdown"].items():
        rows.append({"类别": "年收入", "分项": k, "数值": v / 1e4, "单位": "万元"})
    for k, v in res.get("variable_cost_breakdown", {}).items():
        if v != 0.0:
            rows.append({"类别": "年可变成本", "分项": k, "数值": v / 1e4, "单位": "万元"})
    rows.append({"类别": "年费用", "分项": "年运维成本", "数值": res["opex_year"] / 1e4, "单位": "万元"})
    rows.append({"类别": "年指标", "分项": "年净收益", "数值": res["net_year"] / 1e4, "单位": "万元"})
    roi_pct = res["roi_percent"]
    irr_pct = (irr * 100.0) if irr is not None else float("nan")
    rows.extend(
        [
            {
                "类别": "年指标",
                "分项": "ROI（年净收益/总投资）",
                "数值": np.nan if not np.isfinite(roi_pct) else roi_pct,
                "单位": "%",
            },
            {
                "类别": "年指标",
                "分项": f"IRR（按 {inp.horizon_years} 年现金流测算，年化）",
                "数值": irr_pct,
                "单位": "%",
            },
        ]
    )
    df = pd.DataFrame(rows)
    _html_substep_title("年收入与运营指标")
    st.dataframe(df, use_container_width=True, hide_index=True)
    _render_revenue_calc_details(inp, res)
    if res["revenue_breakdown"]:
        with st.expander("年收入分项口径说明", expanded=False):
            for k in res["revenue_breakdown"]:
                st.markdown(f"**{k}**")
                _html_metric_hint(k)

    _html_result_zone_title("③ 现金流（粗算）")
    st.caption(
        "本图**不依赖**第五步「是否计算利润表、现金流量表与财务评价指标」勾选：用 **年净收益恒定** 假设快速看图（第0年=−总投资，"
        f"第1–{inp.horizon_years}年=年净收益）。勾选财报后，下方另有 **利润表/现金流量表** 及 **财报口径** 累计曲线，"
        "二者可能因 SOH 衰减、增值税、所得税等而不完全一致。"
    )
    years = np.arange(cf_wan.shape[0])
    cum = cumulative_cf(cf_wan)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=years,
            y=cum,
            mode="lines+markers",
            name="累计净现金流",
            line=dict(color="#0f766e", width=2.5),
            marker=dict(size=6, color="#334155"),
        )
    )
    if pb_interp is not None and np.isfinite(pb_interp):
        fig.add_vline(
            x=float(pb_interp),
            line_width=1.8,
            line_dash="dash",
            line_color="#64748b",
        )
        fig.add_annotation(
            x=float(pb_interp),
            y=0.0,
            xref="x",
            yref="y",
            text=f"预估回本点<br>{pb_interp:.2f} 年",
            showarrow=True,
            arrowhead=2,
            ax=45,
            ay=-45,
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#cbd5e1",
            borderwidth=1,
            font=dict(color="#0f172a", size=12),
        )
    elif res["net_year"] <= 0:
        st.warning("年净收益≤0：本模型下无法在有限年限内回本。")

    fig.update_layout(
        template="simple_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#fafafa",
        margin=dict(l=20, r=20, t=36, b=20),
        xaxis_title="年份（第0年为建设投资）",
        yaxis_title="累计净现金流（万元）",
        xaxis=dict(gridcolor="#e7e9ee", zeroline=False),
        yaxis=dict(gridcolor="#e7e9ee", zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        font=dict(family="system-ui, sans-serif", color="#334155"),
    )
    st.plotly_chart(fig, use_container_width=True)

    if inp.calc_financial_statements:
        fin = compute_financial_statements(inp, res)
        m = fin["metrics"]
        _html_result_zone_title("④ 财务报表与评价")
        _html_substep_title("财务评价指标（全投资，不含税口径）")
        c1, c2, c3, c4 = st.columns(4)
        pb_fin = m.get("静态投资回收期(年)")
        c1.metric("静态投资回收期", f"{pb_fin:.2f} 年" if pb_fin is not None else "—")
        npv_show = m.get("XNPV(万元)") if m.get("XNPV(万元)") is not None else m.get("NPV(万元)")
        c2.metric("NPV", f"{npv_show:,.2f} 万元" if npv_show is not None and np.isfinite(npv_show) else "—")
        irr_fin = m.get("IRR(年化)")
        irr_fin_ok = irr_fin is not None and np.isfinite(irr_fin)
        c3.metric(
            "IRR (XIRR)",
            f"{irr_fin * 100:.2f}%" if irr_fin_ok else "—",
        )
        pi_v = m.get("PI")
        c4.metric("PI", f"{pi_v:.3f}" if pi_v is not None and np.isfinite(pi_v) else "—")
        st.caption(
            f"折现率 {m.get('折现率', 0) * 100:.1f}%；可折旧资产 {m.get('可折旧资产(万元)', 0):,.2f} 万元；"
            f"期末残值 {m.get('期末残值(万元)', 0):,.2f} 万元。"
            " 所得税：三免三减半 + 亏损结转；增值税留抵逐年结转。"
        )
        if not irr_fin_ok:
            irr_simple = irr_annual(cf_wan)
            irr_simple_txt = (
                f"粗算恒定年净流 IRR ≈ **{irr_simple * 100:.2f}%**（见上图，仅供参考）"
                if irr_simple is not None and np.isfinite(irr_simple)
                else "粗算 IRR 亦不可用（请安装 `numpy-financial`）"
                if npf is None
                else "粗算 IRR 亦无法收敛"
            )
            npv_fin = m.get("XNPV(万元)") if m.get("XNPV(万元)") is not None else m.get("NPV(万元)")
            npv_fin_txt = (
                f"{float(npv_fin):,.1f} 万元"
                if npv_fin is not None and np.isfinite(float(npv_fin))
                else "—"
            )
            _html_note_panel(
                "**为何 IRR (XIRR) 显示为 —？** 该指标来自 **现金流量表** 各年净流（含增值税、所得税、SOH 衰减等），"
                "与上图粗算曲线不是同一套数。常见原因：① 在折现率 "
                f"{m.get('折现率', 0) * 100:.1f}% 下 **NPV 为负**（您当前约 {npv_fin_txt}），"
                "说明按该折现率折现后项目仍不能弥补投资，经济上常无「高于折现率」的正 IRR 可报告；"
                "② 各年现金流不规则或库无法收敛唯一 IRR；③ 未安装 `numpy-financial`。"
                f" {irr_simple_txt}。静态回收期 {pb_fin:.2f} 年仍可按累计现金流插值参考。"
                if pb_fin is not None
                else ""
            )
        _html_substep_title("利润表（万元，不含税）")
        st.dataframe(fin["profit_df"], use_container_width=True, hide_index=True)
        _html_substep_title("现金流量表（万元）")
        st.dataframe(fin["cashflow_df"], use_container_width=True, hide_index=True)
        fig_fin = go.Figure()
        fig_fin.add_trace(
            go.Scatter(
                x=fin["cashflow_df"]["年份"],
                y=fin["cashflow_df"]["累计净现金流"],
                mode="lines+markers",
                name="累计净现金流(财报口径)",
                line=dict(color="#0369a1", width=2.5),
            )
        )
        fig_fin.update_layout(
            template="simple_white",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=20, r=20, t=30, b=20),
            xaxis_title="年份",
            yaxis_title="累计净现金流（万元）",
            font=dict(family="system-ui, sans-serif", color="#334155"),
        )
        st.plotly_chart(fig_fin, use_container_width=True)
        st.caption(
            "上图与「现金流量表」同口径：第0年为建设期投资流出，此后各年为经营/税费/残值等净流累计。"
        )

    _html_result_zone_title("⑤ 方案对比")
    cA, cB = st.columns(2)
    name = cA.text_input("方案名称", value=f"方案{len(st.session_state.schemes) + 1}")
    if cB.button("保存当前方案", type="primary"):
        snap = inp.__dict__.copy()
        st.session_state.schemes.append(
            {
                "name": name.strip() or f"方案{len(st.session_state.schemes) + 1}",
                "inputs": snap,
                "capex_wan": capex_wan,
                "net_wan": net_wan,
                "payback_static": pb if np.isfinite(pb) else None,
                "payback_interp": pb_interp,
                "irr_percent": (irr * 100.0) if irr is not None else None,
            }
        )
        st.success("已保存。")

    if st.session_state.schemes:
        cmp = pd.DataFrame(
            [
                {
                    "方案": s["name"],
                    "总投资(万元)": s["capex_wan"],
                    "年净收益(万元)": s["net_wan"],
                    "回本(静态,年)": s["payback_static"],
                    "回本(插值,年)": s["payback_interp"],
                    "IRR(%)": s["irr_percent"],
                }
                for s in st.session_state.schemes
            ]
        )
        st.dataframe(cmp, use_container_width=True, hide_index=True)
        sel = st.selectbox(
            "选择要删除的方案",
            options=list(range(len(st.session_state.schemes))),
            format_func=lambda i: st.session_state.schemes[i]["name"],
        )
        d1, d2 = st.columns(2)
        if d1.button("删除所选"):
            st.session_state.schemes.pop(int(sel))
            st.rerun()
        if d2.button("清空全部"):
            st.session_state.schemes = []
            st.rerun()
    else:
        st.caption("可保存多套参数以便横向对比 IRR / 回本。")


def main() -> None:
    _inject_global_style()
    _init_session_state()

    st.title("工商业储充场站成本收益测算工具")
    _html_note(
        "左侧按 <strong>第一步场站 → 第二步桩 → 第三步储能 → 第四步施工 → 第五步运营</strong> 填写；"
        "点击 <strong>项目正向收益及回收期测算</strong> 后，右侧展示 <strong>项目收益与回收期</strong>；"
        "其下 <strong>项目逆向分析</strong> 可反算目标回收期下的补贴需求。"
        " 储能收益模型见 MEMORY.md。"
    )
    st.markdown('<hr class="page-hero-divider" />', unsafe_allow_html=True)

    col_left, col_right = st.columns([1.72, 1], gap="large")

    submitted = False
    inp_ok: InputModel | None = None
    form_errs: list[str] = []
    basics_submitted = False
    basics_errs: list[str] = []

    with col_left:
        if not st.session_state.site_basics_confirmed:
            basics_submitted, basics_errs = render_site_basics_step()
            if basics_submitted and basics_errs:
                for e in basics_errs:
                    st.error(e)
            elif basics_submitted and not basics_errs:
                st.rerun()
        else:
            if st.button("重新填写场站信息", type="secondary"):
                st.session_state.site_basics_confirmed = False
                st.session_state.confirmed_inp = None
                st.rerun()
            _html_column_title("参数录入")
            st.markdown(
                '<p class="muted-hint">按 <strong>第二步 → 第三步 → 第四步 → 第五步</strong> 顺序填写，'
                "最后点击底部 <strong>项目正向收益及回收期测算</strong>。"
                "数量类默认从 0 起；单价可参考底部默认值表。</p>",
                unsafe_allow_html=True,
            )
            detail_defs = _build_detail_defaults(st.session_state.site_basics)
            submitted, inp_ok, form_errs = render_input_form(detail_defs)

    if submitted:
        st.session_state.form_submit_errs = form_errs
        if inp_ok is not None:
            st.session_state.confirmed_inp = inp_ok
            st.session_state.reverse_baseline = _build_reverse_baseline(inp_ok)
            st.session_state.reverse_refresh_errs = []
            st.session_state.pop("reverse_last_result", None)

    with col_right:
        _html_column_title("测算结果")
        if st.session_state.form_submit_errs:
            for e in st.session_state.form_submit_errs:
                st.error(e)
        if not st.session_state.site_basics_confirmed:
            _html_note_panel("请先在左侧 **第一步** 确认场站地点、车位规格与数量、变压器及环境信息。")
        elif st.session_state.confirmed_inp is None:
            _html_note_panel(
                "场站信息已确认。请在左侧填写参数并点击 **项目正向收益及回收期测算**。"
            )
        else:
            if submitted and form_errs:
                st.warning("本次提交未通过校验：右侧仍为上一版已保存结果。")
            render_estimate_panel(st.session_state.confirmed_inp)

    render_site_defaults_editor()

    if npf is None:
        st.warning("未安装 numpy-financial：IRR 不可用。请执行 `pip install numpy-financial`。")


if __name__ == "__main__":
    main()
