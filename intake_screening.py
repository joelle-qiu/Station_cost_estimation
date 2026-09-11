# -*- coding: utf-8 -*-
"""通用摸底表导入：VPP 组盘 + 储能投资筛站共用。作者: jiali.qiu

蓝列 = VPP：铭牌 + 户号 + 能否调控（含换电仓、柴发、桩型、可调 kW）。
绿列 = 储能投资：变压器、日用电量、场地、购电方式、白天压不下去的负荷（曲线替代）。
储能投资不看能否调控。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tariff_lib import load_library, match_tariff, tariff_row_fields

# 须与 _gen_intake_xlsx.py 一致。
# 蓝列到「光伏可限发kW」= VPP（含能否调控：换电仓、柴发、桩型、可调kW）。
# 绿列从「白天压不下去的负荷（千瓦）」= 储能投资（曲线替代 + 变压器 + 日电量 + 场地 + 购电）。
# 储能投资不看可调；有没有桩看蓝列充电kW。
INTAKE_COLUMNS = [
    "场站名称",
    "省",
    "市",
    "区县+地址",
    "资源类型",
    "充电kW",
    "储能功率kW",
    "储能kWh",
    "光伏kWp",
    "厂区其他可调（千瓦）",
    "一次可调控时长（小时）",
    "电费户号",
    "独立户号?",
    "能调控?",
    "用电性质",
    "加密网关?",
    "充电桩型",
    "充电桩可远程限功率（千瓦）",
    "V2G可调kW",
    "换电仓功率能否远程下调",
    "柴发",
    "光伏可限发kW",
    "白天压不下去的负荷（千瓦）",
    "变压器额定kVA",
    "变压器还能再接（千伏安）",
    "电费合同需量（千瓦）",
    "日均用电或充电量kWh",
    "场地能否放储能柜",
    "购电方式",
    "备注",
]
VPP_LAST_COL = "光伏可限发kW"
STORAGE_FIRST_COL = "白天压不下去的负荷（千瓦）"
STORAGE_LAST_COL = "购电方式"

SCENE_OPTIONS = [
    "纯充电",
    "独立储能",
    "充电+储能",
    "光储充",
    "分布式光伏",
    "可调负荷",
    "换电",
    "厂区负荷",
    "厂区+充电",
    "其他",
]
YES_NO = ["是", "否", "不确定"]
CTRL_OPTS = ["能", "部分能", "不能", "需改造", "不确定"]
ARCH_OPTS = ["分体式", "一体式", "不适用"]
ACCT_OPTS = ["独立", "合表", "不确定"]
USER_CLASS = ["充电设施", "非工业", "工业", "商业", "其他", "不确定"]
PURCHASE = ["电网代理购电", "已直接入市/售电", "不清楚"]
HOURS_OPTS = ["0.5", "1", "2", "4", "不确定"]
GATEWAY_OPTS = ["已装", "未装", "不适用", "不确定"]
GENSET_OPTS = ["无", "有且可调", "有但不可调", "不确定"]

_ALIASES = {
    "城市": "市",
    "场站类型": "资源类型",
    "场景类型": "资源类型",
    "充电额定kW": "充电kW",
    "能调控": "能调控?",
    "独立户号": "独立户号?",
    "可调负荷kW": "厂区其他可调（千瓦）",
    "其他可调kW": "厂区其他可调（千瓦）",
    "可持续h": "一次可调控时长（小时）",
    "充电可调kW": "充电桩可远程限功率（千瓦）",
    "换电仓位可调": "换电仓功率能否远程下调",
    "换电仓能否限功率": "换电仓功率能否远程下调",
    "厂区白天最小kW": "白天压不下去的负荷（千瓦）",
    "剩余可用kVA": "变压器还能再接（千伏安）",
    "合同需量kW": "电费合同需量（千瓦）",
}


def _yes(v: Any) -> bool:
    s = str(v or "").strip()
    return s in ("是", "有", "能", "独立", "分表独立户号")


def _num(v: Any) -> float:
    try:
        if v is None or str(v).strip() in ("", "nan", "None"):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _cell(row: pd.Series, *keys: str) -> Any:
    names = [str(x) for x in row.index]
    for k in keys:
        if k not in names:
            continue
        v = row.iat[names.index(k)]
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        s = str(v).strip()
        if s in ("", "nan", "None"):
            continue
        return v
    return ""


def normalize_intake(df: pd.DataFrame) -> pd.DataFrame:
    """兼容本表、旧三省 VPP「2-资源清单」、以及上一版配储表列名。"""
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    rename = {}
    for src, dst in _ALIASES.items():
        if src in out.columns and dst not in out.columns:
            rename[src] = dst
    out.rename(columns=rename, inplace=True)
    if "独立户号?" in out.columns and out.columns.tolist().count("独立户号?") > 1:
        # 避免两列改成同名后 get() 返回 Series
        keep = ~out.columns.duplicated()
        out = out.loc[:, keep]
    # 共表列：分表 → 独立（仅当还没有户号列）
    if "独立户号?" not in out.columns and "共表或分表" in out.columns:
        mapped = out["共表或分表"].astype(str).replace(
            {"分表独立户号": "独立", "共表/合表": "合表"}
        )
        out["独立户号?"] = mapped
    for col in INTAKE_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    return out[INTAKE_COLUMNS]


def _has_storage(row: pd.Series) -> bool:
    return (
        _yes(_cell(row, "有储能"))
        or _num(_cell(row, "储能功率kW")) >= 50
        or _num(_cell(row, "储能kWh")) >= 100
    )


def _charge_kw(row: pd.Series) -> float:
    return _num(_cell(row, "充电kW", "充电额定kW"))


def _factory_kw(row: pd.Series) -> float:
    return max(
        _num(_cell(row, "白天压不下去的负荷（千瓦）", "厂区白天最小kW")),
        _num(_cell(row, "厂区其他可调（千瓦）", "其他可调kW", "可调负荷kW")),
    )


def _independent(row: pd.Series) -> bool:
    acct = str(_cell(row, "独立户号?", "独立户号") or "").strip()
    meter = str(_cell(row, "共表或分表") or "").strip()
    if acct in ("合表", "否") or meter in ("共表/合表",):
        return False
    return acct in ("独立", "是", "分表独立户号") or meter == "分表独立户号"


def _can_ctrl(row: pd.Series) -> bool:
    v = str(_cell(row, "能调控?", "能调控", "充电可调") or "").strip()
    return v in ("能", "部分能", "是", "需改造")


def vpp_adjustable_kw(row: pd.Series) -> float:
    """对内粗可调：充电×50% + 储能×90% + 其他可调×70%。光伏不计入负荷调节。"""
    return _charge_kw(row) * 0.5 + _num(_cell(row, "储能功率kW")) * 0.9 + _num(
        _cell(row, "厂区其他可调（千瓦）", "其他可调kW", "可调负荷kW")
    ) * 0.7


# 页签①结果表：结论 / 推荐下一步用黄底突出，方便负责人扫一眼。
SCREEN_HIGHLIGHT_COLS = ("结论", "推荐下一步")
SCREEN_RESULT_FRONT = [
    "红绿灯",
    "结论",
    "推荐下一步",
    "原因",
    "场站名称",
    "省",
    "市",
    "资源类型",
    "粗可调kW",
    "充电kW",
    "其他可调kW",
    "厂区其他可调（千瓦）",
    "分型倾向",
    "电价库",
    "电价形态",
    "电价标记",
    "库内价可否测算套利",
    "是否分时",
    "价差",
    "电价置信",
    "EGC预制",
    "缺失",
]


def order_screen_columns(df: pd.DataFrame) -> pd.DataFrame:
    """把结论、下一步、原因放到最前，其余列跟在后面。"""
    front = [c for c in SCREEN_RESULT_FRONT if c in df.columns]
    rest = [c for c in df.columns if c not in front]
    return df[front + rest]


def _kw_txt(v: float, unit: str = "千瓦") -> str:
    if v is None or float(v) <= 0:
        return f"未填（按 0 {unit} 计）"
    return f"约 {v:.0f} {unit}"


def _tou_value_sentence(
    *,
    has_tou: bool | None,
    tou_effective: bool | None,
    spread: float | None,
    purchase: str,
) -> str:
    """电价对配储保底账意味着什么。不算新规则，只解释已判定结果。"""
    if str(purchase or "").startswith("已直接"):
        return (
            "购电方式已直接入市或走售电，政府核定峰谷可能不再执行，"
            "库内价差不能当作配储投资的保底收益。"
        )
    if has_tou is None:
        return "电价库尚未匹配到该城市，现在不能用库内峰谷价差当投资保底。"
    if tou_effective is False:
        return (
            "当地目前不能按政府核定固定峰谷做保底测算"
            "（动态分时、已入市或库内无有效分时）。"
        )
    if spread is not None:
        return f"当地仍可按政府核定峰谷做保底参考，库内价差约 {spread:.2f} 元/千瓦时。"
    return "分时形态已匹配，但库内尚未写入价差数字。"


def _vpp_tail(adj: float, ctrl_raw: str) -> str:
    base = (
        f"虚拟电厂粗可调约 {adj:.0f} 千瓦"
        "（充电×50%＋储能×90%＋厂区其他可调×70%，只作组盘量级，不是结算容量）。"
    )
    if str(ctrl_raw or "").strip() in ("否", "不能"):
        return (
            base
            + "摸底已写明不能调控，虚拟电厂组盘需改造或先剔除本站；"
            "这不影响「值不值得为配储去要曲线」的判断。"
        )
    return base


def _meter_sentence(independent: bool, he_biao: bool) -> str:
    if he_biao or not independent:
        return (
            "电费与物业或其他用户合表，虚拟电厂名录和储能需量账户都难以独立结算，"
            "装柜后省下来的钱可能进不了本运营商账户。"
        )
    return "户号独立，电费和需量可以单独记账，具备继续评估投资的结算条件。"


def screen_station(row: pd.Series, tariff: dict[str, Any] | None) -> dict[str, Any]:
    """单站判定：配储红绿灯 + VPP 粗可调。不算 IRR。"""
    chg = _charge_kw(row)
    fac = _factory_kw(row)
    tf = _num(_cell(row, "变压器额定kVA"))
    independent = _independent(row)
    purchase = str(_cell(row, "购电方式") or "").strip()
    user_class = str(_cell(row, "用电性质") or "").strip()
    site_ok = str(_cell(row, "场地能否放储能柜") or "").strip()
    scene = str(_cell(row, "资源类型", "场景类型") or "").strip()
    adj = round(vpp_adjustable_kw(row), 1)
    hours = str(_cell(row, "一次可调控时长（小时）", "可持续h") or "").strip()

    vpp_extra = {
        "粗可调kW": adj,
        "VPP口径": "充电×50%+储能×90%+其他可调×70%；光伏不计入负荷调节",
        "可持续h": hours,
    }

    has_tou = bool(tariff.get("has_tou")) if tariff else None
    market_kills_tou = bool(tariff.get("market_no_gov_tou")) if tariff else False
    spread = float(tariff["spread_yuan"]) if tariff and tariff.get("spread_yuan") is not None else None
    waiver = bool(tariff.get("demand_waiver_42")) if tariff else True
    charge_like = user_class == "充电设施" or (
        user_class == "非工业" and chg > 0
    ) or scene in (
        "纯充电",
        "充电+储能",
        "光储充",
        "换电",
        "厂区+充电",
    )
    factory_scene = scene in ("厂区负荷", "厂区+充电", "可调负荷", "其他")
    ctrl_raw = str(_cell(row, "能调控?", "能调控") or "").strip()
    acct_raw = str(_cell(row, "独立户号?", "独立户号", "共表或分表") or "").strip()
    he_biao = (not independent) and acct_raw in ("合表", "共表/合表", "否")
    missing: list[str] = []

    def _pack(d: dict[str, Any]) -> dict[str, Any]:
        out = dict(vpp_extra)
        out.update(d)
        return out

    if not str(_cell(row, "场站名称") or "").strip():
        return _pack({
            "红绿灯": "红",
            "结论": "暂缓",
            "推荐下一步": "请补全场站名称后再导入。本行暂不进入配储名单，也不进入虚拟电厂组盘。",
            "分型倾向": "—",
            "原因": (
                "这一行没有场站名称，无法进入筛站名单，也无法通知对应负责人去要曲线或电费单。"
                "配储和虚拟电厂都需要能对上具体场站，空名称没有业务价值。"
            ),
            "缺失": "场站名称",
            "EGC预制": "—",
        })

    if _has_storage(row):
        return _pack({
            "红绿灯": "灰",
            "结论": "存量优化",
            "推荐下一步": (
                "不要再推新柜。请向负责人要关口或储能运行曲线，按已有资产做策略与 EGC 复核；"
                "虚拟电厂按储能类或负荷类拆单元。"
            ),
            "分型倾向": "存量",
            "原因": (
                "摸底表已经填写现有储能功率或容量，说明柜子已经在场。"
                "配储投资的价值是把已有储能用好（策略、需量、峰谷），而不是再装一套新柜；"
                "再装会重复占用场地和变压器余量，增量投资通常不值。"
                "虚拟电厂仍可以把已有储能作为可调资源组单元，这一层价值还在。"
                + _vpp_tail(adj, ctrl_raw)
            ),
            "缺失": "",
            "EGC预制": "按已有资产勾选，不预配新柜",
        })

    if site_ok == "否":
        return _pack({
            "红绿灯": "红",
            "结论": "不配储",
            "推荐下一步": (
                "不要为配储去收集曲线。可评估有序充电或负荷调控，"
                "并把可调容量纳入虚拟电厂 / 需求响应组盘。"
            ),
            "分型倾向": "D",
            "原因": (
                "场地明确不能放置储能柜，配储投资没有物理落点，装柜价值为零。"
                "充电或厂区如果还能远程调节，价值在有序用电和虚拟电厂需求响应，不在储能投资。"
                + _vpp_tail(adj, ctrl_raw)
            ),
            "缺失": "",
            "EGC预制": "不配储；可调则勾有序/可调负荷",
        })

    tou_effective = has_tou
    if purchase == "已直接入市/售电" and market_kills_tou:
        tou_effective = False
    if has_tou is None:
        missing.append("电价库未匹配，请核省/市")
    if not str(_cell(row, "变压器额定kVA") or "").strip():
        missing.append("变压器额定kVA")
    if not str(_cell(row, "日均用电或充电量kWh") or "").strip():
        missing.append("日均电量")

    tou_s = _tou_value_sentence(
        has_tou=has_tou,
        tou_effective=tou_effective,
        spread=spread,
        purchase=purchase,
    )
    meter_s = _meter_sentence(independent, he_biao)

    if chg < 120 and fac < 150 and tf < 200:
        return _pack({
            "红绿灯": "红",
            "结论": "暂缓",
            "推荐下一步": (
                "不优先向该站要曲线做配储。虚拟电厂可先把同节点小站记入资源池，"
                "等攒够规模再组单元。"
            ),
            "分型倾向": "D",
            "原因": (
                f"充电 {_kw_txt(chg)}、厂区白天压不下去的负荷 {_kw_txt(fac)}、"
                f"变压器 {_kw_txt(tf, '千伏安')}，都低于值得专门收集曲线做配储评估的规模。"
                "柜子固定成本摊不薄，投资回收很难看；把要数精力放在大站上更划算。"
                "虚拟电厂仍可以把同一节点的小站攒在一起，聚合层还有价值。"
                + _vpp_tail(adj, ctrl_raw)
            ),
            "缺失": "；".join(missing),
            "EGC预制": "—",
        })

    charge_value = chg >= 240
    factory_value = fac >= 200 or (tf >= 315 and factory_scene)
    if scene in ("厂区负荷", "可调负荷"):
        factory_value = factory_value or tf >= 250 or fac >= 150

    no_spread = tou_effective is False or (spread is not None and spread < 0.45)
    if charge_like and no_spread and waiver and fac < 200:
        return _pack({
            "红绿灯": "红",
            "结论": "不配储",
            "推荐下一步": (
                "不要为配储去要曲线。请评估有序充电（远程限功率、错峰），"
                "并把可调容量纳入虚拟电厂 / 需求响应。"
            ),
            "分型倾向": "D",
            "原因": (
                "这是充电设施（或充电场景）用户。"
                + tou_s
                + "同时充电设施往往可以按政策免收需量电费，靠储能削峰省基本电费的账也做不出来。"
                "峰谷套利和需量削减两条保底收益都空，配储投资没有可靠价值。"
                "真正还值得做的是有序充电和虚拟电厂需求响应，不装柜也能兑现。"
                + ("合表会进一步让投资账户对不上。" if he_biao else "")
                + _vpp_tail(adj, ctrl_raw)
            ),
            "缺失": "；".join(missing),
            "EGC预制": "储充互动不预配；勾充电可调则只做有序",
        })

    if factory_value and not charge_value:
        tendency = "A候选" if (tou_effective is False or no_spread) else "C候选"
        color = "绿" if independent and tf >= 315 else "黄"
        egc = "纯储能 或 光储互动（有光伏时）"
        if color == "绿":
            value_s = (
                f"厂区白天压不下去的负荷 {_kw_txt(fac)}、变压器 {_kw_txt(tf, '千伏安')}，"
                "用电相对稳定，具备削峰或峰谷套利的空间。"
                + meter_s
                + tou_s
                + "绿色只表示值得去要关口曲线，还不是投资批复；曲线确认峰有多高、能顶多久之后，才能进页签②评分。"
            )
            nxt = (
                "请向场站或园区负责人索取典型日关口曲线、近月电费单和生产班次。"
                "曲线结论用手填进页签②评分。虚拟电厂按负荷类组单元。"
            )
        else:
            value_s = (
                f"厂区负荷已有规模（白天 {_kw_txt(fac)}、变压器 {_kw_txt(tf, '千伏安')}），"
                "具备继续评估的线索，但户号尚未独立或变压器未到约 315 千伏安，"
                "储能需量账户或接入条件还锁不成绿名单。"
                + meter_s
                + tou_s
                + "黄色表示值得补数并收集曲线，不要当成已经可投。"
            )
            nxt = (
                "请先补独立户号或变压器参数，并向负责人要关口曲线、电费单。"
                "虚拟电厂可先按负荷类观察，结算路径需与户号一并确认。"
            )
        return _pack({
            "红绿灯": color,
            "结论": "高价值·厂区" if color == "绿" else "高价值候选·厂区",
            "推荐下一步": nxt,
            "分型倾向": tendency,
            "原因": value_s + _vpp_tail(adj, ctrl_raw),
            "缺失": "；".join(missing),
            "EGC预制": egc,
        })

    if charge_value:
        tendency = "B候选" if tou_effective and spread and spread >= 0.5 else "待电价复核"
        color = "绿" if independent and tou_effective else "黄"
        mix = scene == "厂区+充电"
        if mix:
            tendency = "C候选"
        mix_s = (
            f"同时有厂区白天负荷 {_kw_txt(fac)}，是厂区加充电的混合站，结算和曲线都要按关口一并看。"
            if mix and fac > 0
            else ""
        )
        if color == "绿":
            value_s = (
                f"充电装机 {_kw_txt(chg)}，规模够做储充互动评估。"
                + mix_s
                + meter_s
                + tou_s
                + "绿色表示值得去要充电或关口曲线，确认谷段有没有空窗、峰段能顶多久、"
                "以及是否适用充电设施免需量——免需量会削弱纯削峰价值，必须问清。"
                "绿色仍不是投资批复。"
            )
            nxt = (
                "请向负责人索取典型日充电或关口曲线、服务费口径，并书面确认是否适用充电设施免需量。"
                "曲线结论用手填进页签②。虚拟电厂按负荷类组单元。"
            )
        elif he_biao:
            value_s = (
                f"充电装机 {_kw_txt(chg)}，体量够继续看。"
                + mix_s
                + meter_s
                + tou_s
                + "黄色是候选：曲线可以要，投资不能锁；未分表前不要按绿名单报装柜。"
            )
            nxt = (
                "收集充电或关口曲线的同时，推动分表或书面确认结算路径。"
                "未独立前不要按绿名单报投资。虚拟电厂组单元需与结算主体一致。"
            )
        else:
            value_s = (
                f"充电装机 {_kw_txt(chg)}，体量够。"
                + mix_s
                + meter_s
                + tou_s
                + "黄色表示可以要曲线，但必须用手填零售或现货价，不能套用电价库里的峰谷做保底。"
            )
            nxt = (
                "向负责人要充电或关口曲线，并取得零售套餐或现货结算凭证。"
                "页签②必须手填价差，禁止套用电价库。虚拟电厂仍可按可调资源组单元。"
            )
        return _pack({
            "红绿灯": color,
            "结论": "高价值·充电" if color == "绿" else "高价值候选·充电",
            "推荐下一步": nxt,
            "分型倾向": tendency,
            "原因": value_s + _vpp_tail(adj, ctrl_raw),
            "缺失": "；".join(missing),
            "EGC预制": "储充互动（待确认可调枪与柜）",
        })

    if chg >= 120 or fac >= 100 or tf >= 250 or adj >= 200:
        return _pack({
            "红绿灯": "黄",
            "结论": "可培育",
            "推荐下一步": (
                "请先补变压器额定容量、日均用电或充电量后再导入一次。"
                "调控能力字段给虚拟电厂用，不挡配储筛站。"
            ),
            "分型倾向": "待补数",
            "原因": (
                f"充电 {_kw_txt(chg)}、厂区白天负荷 {_kw_txt(fac)}、"
                f"变压器 {_kw_txt(tf, '千伏安')}，已有一定规模，"
                "但变压器额定、日均电量或购电方式还不完整，无法判断峰谷空间和需量账户。"
                "值得留在池子里补数，还不到锁定高价值、去要全套曲线的程度。"
                + meter_s
                + _vpp_tail(adj, ctrl_raw)
            ),
            "缺失": "；".join(missing),
            "EGC预制": "—",
        })

    return _pack({
        "红绿灯": "红",
        "结论": "暂缓",
        "推荐下一步": "本站不优先要数。若同节点还有其他资源，可并入虚拟电厂资源池一并观察。",
        "分型倾向": "—",
        "原因": (
            "未达到配储高价值门槛（充电未到约 240 千瓦，厂区白天负荷或变压器也未到线索线），"
            "专门收集曲线的投入产出不合算。"
            + tou_s
            + _vpp_tail(adj, ctrl_raw)
        ),
        "缺失": "；".join(missing),
        "EGC预制": "—",
    })


def run_screen(df: pd.DataFrame) -> pd.DataFrame:
    """导入后批量：配储红绿灯 + VPP 粗可调 + 电价匹配。"""
    base = normalize_intake(df)
    lib = load_library()
    rows = []
    for _, row in base.iterrows():
        t = match_tariff(str(row.get("省") or ""), str(row.get("市") or ""), lib)
        judged = screen_station(row, t)
        rec = row.to_dict()
        purchase_market = str(row.get("购电方式") or "").startswith("已直接")
        rec.update(tariff_row_fields(t, purchase_market=purchase_market))
        rec.update(judged)
        rows.append(rec)
    return pd.DataFrame(rows)
