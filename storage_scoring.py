# -*- coding: utf-8 -*-
"""配储评分：手填峰/时长/空窗 → 分型 + 粗投资。作者: jiali.qiu

不读曲线文件。柜型锁定 125kW / 262kWh。V2G/补贴不进保底。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CABINET_KW = 125.0
CABINET_KWH = 262.0
# 包头方案四量级：柜约 0.70 元/Wh + 安装 0.25 元/Wh
YUAN_PER_WH = 0.95
ETA = 0.88
CYCLES_DEFAULT = 1.0
OPEX_PCT = 0.02
PAYBACK_GATE = 5.0
IRR_PROXY_GATE = 0.08

# 与 doc/储能配置打分表.xlsx 一致。合计权重 100。任一项「不适合」则不定配储方案。
HARD_ITEMS: list[dict[str, Any]] = [
    {
        "id": "space",
        "name": "场地尺寸与维护空间",
        "weight": 20,
        "fit": "满足机型最小间距（125kW/262kWh 整柜 + 前后左右维护通道）",
        "unfit": "任一方向间距不足",
        "short": "柜体+维护通道间距够",
        "ask": "场地尺寸和通道够放柜吗？",
        "extra": "size",
    },
    {
        "id": "ground",
        "name": "地面承载与地基",
        "weight": 20,
        "fit": "土壤相对密实度 ≥98%，地面坚实平坦、不倒塌不滑动",
        "unfit": "松散土、回填土未处理",
        "short": "密实度≥98%，硬化平坦",
        "ask": "地面能承重吗？",
        "extra": "ground",
    },
    {
        "id": "drain",
        "name": "排水与防淹",
        "weight": 10,
        "fit": "地基高出地面 ≥200~300mm，有排水系统",
        "unfit": "易积水、无排水",
        "short": "抬高≥200mm，有排水",
        "ask": "排水防淹够吗？",
        "extra": "drain",
    },
    {
        "id": "level",
        "name": "基础水平度",
        "weight": 10,
        "fit": "上表面不均匀度 <5mm",
        "unfit": "超差",
        "short": "上表面不均匀度<5mm",
        "ask": "基础水平度合格吗？",
        "extra": "",
    },
    {
        "id": "earth",
        "name": "接地条件",
        "weight": 10,
        "fit": "可预埋接地扁铁、预留接地孔",
        "unfit": "无法做接地",
        "short": "可预埋接地扁铁/孔",
        "ask": "接地能做吗？",
        "extra": "",
    },
    {
        "id": "transformer",
        "name": "变压器容量与并网余量",
        "weight": 15,
        "fit": "有余量、距离近",
        "unfit": "无余量、距离远",
        "short": "有余量且并网距离近",
        "ask": "变压器有余量、距离近吗？",
        "extra": "tf",
    },
    {
        "id": "fire",
        "name": "消防与防火间距",
        "weight": 10,
        "fit": "远离易燃易爆区、满足当地防火间距",
        "unfit": "紧邻建筑/人员密集区",
        "short": "远离易燃易爆，间距够",
        "ask": "消防间距够吗？",
        "extra": "",
    },
    {
        "id": "title",
        "name": "产权与合规",
        "weight": 5,
        "fit": "权属清晰、在红线内",
        "unfit": "权属不清、违规",
        "short": "权属清晰、在红线内",
        "ask": "产权合规吗？",
        "extra": "title",
    },
]
HARD_CHOICES = ("待确认", "适合", "不适合")


@dataclass
class HardGateResult:
    """硬性条件清单判定。complete=八项都已选；passed=全部适合。"""

    complete: bool
    passed: bool
    score: int
    fails: list[str]
    pending: list[str]


def evaluate_hard_gate(answers: dict[str, str]) -> HardGateResult:
    """按打分表权重计落地分。待确认或不适合都不能算过硬门槛。"""
    pending: list[str] = []
    fails: list[str] = []
    score = 0
    for item in HARD_ITEMS:
        a = str(answers.get(item["id"]) or "待确认").strip()
        if a == "适合":
            score += int(item["weight"])
        elif a == "不适合":
            fails.append(str(item["name"]))
        else:
            pending.append(str(item["name"]))
    return HardGateResult(
        complete=not pending,
        passed=not pending and not fails,
        score=int(score),
        fails=fails,
        pending=pending,
    )


@dataclass
class ScoreInput:
    scene: str  # 充电 / 厂区 / 混合
    tf_kva: float
    peak_kw: float
    overload_hours: float
    valley_idle_hours: float
    valley_headroom_kw: float
    peak_yuan: float
    valley_yuan: float
    two_part: bool
    demand_waiver: bool
    demand_yuan_per_kw_month: float
    daily_kwh: float
    service_fee: float
    purchase_market: bool
    site_ok: bool
    chg_controllable: bool
    hard_passed: bool = True
    site_score_pts: int | None = None


@dataclass
class ScoreResult:
    type_code: str
    type_name: str
    score_cap: int
    score_spread: int
    score_idle: int
    score_site: int
    score_total: int
    n_cabinets: int
    capex_wan: float
    annual_wan: float
    payback_years: float | None
    verdict: str
    egc_preset: str
    egc_ticks: str
    notes: list[str]


def _clip_score(x: float) -> int:
    return int(max(0, min(100, round(x))))


def recommend_cabinets(inp: ScoreInput) -> int:
    tf_kw = inp.tf_kva * 0.9
    shave = max(0.0, inp.peak_kw - tf_kw)
    by_peak = shave / CABINET_KW if shave > 1 else 0.0
    by_energy = 0.0
    if inp.valley_idle_hours >= 1 and inp.daily_kwh > 0 and inp.scene in ("充电", "混合"):
        # 谷充供桩：按日充电量的一部分 / 单柜电量
        by_energy = min(inp.daily_kwh * 0.35, inp.valley_headroom_kw * inp.valley_idle_hours) / CABINET_KWH
    n = max(by_peak, by_energy, 1.0 if (shave > 20 or inp.daily_kwh >= 800) else 0.0)
    n = min(6, max(0, round(n)))
    return int(n)


def score_and_invest(inp: ScoreInput) -> ScoreResult:
    notes: list[str] = []
    tf_kw = max(1.0, inp.tf_kva * 0.9)
    over = inp.peak_kw > tf_kw + 1
    spread = max(0.0, inp.peak_yuan - inp.valley_yuan)
    tou_ok = spread >= 0.45 and not inp.purchase_market

    # 分项分
    if over:
        cap = 40 + min(40, (inp.peak_kw - tf_kw) / max(tf_kw, 1) * 80)
        if inp.overload_hours <= 2:
            cap += 20
        elif inp.overload_hours > 4:
            cap -= 25
            notes.append("超载超过约4小时，储能难覆盖，倾向增容")
    else:
        cap = 15
    if inp.demand_waiver and inp.scene in ("充电", "混合"):
        cap = min(cap, 35)
        notes.append("充电设施或适用42号免需量，容量分封顶")

    spr = 0
    if tou_ok:
        spr = 30 + min(50, (spread - 0.45) / 0.9 * 50)
    elif inp.purchase_market:
        spr = 15
        notes.append("已入市，政府分时可能取消，价差分降低")
    else:
        spr = 20 if spread >= 0.3 else 8

    idle = 10
    if inp.valley_idle_hours >= 2 and inp.valley_headroom_kw >= 80:
        idle = 75
    elif inp.valley_idle_hours >= 1:
        idle = 45
    else:
        notes.append("谷段空窗不足，峰谷供桩难成立")

    site = float(inp.site_score_pts) if inp.site_score_pts is not None else (80 if inp.site_ok else 10)
    if not inp.site_ok:
        notes.append("场地不能放柜")
    if not inp.hard_passed:
        notes.append("硬性条件未过（储能配置打分表八项），不定配储方案")

    score_cap = _clip_score(cap)
    score_spread = _clip_score(spr)
    score_idle = _clip_score(idle)
    score_site = _clip_score(site)
    total = _clip_score(0.30 * score_cap + 0.30 * score_spread + 0.25 * score_idle + 0.15 * score_site)

    # 分型
    long_ol = over and inp.overload_hours > 2.2
    type_code, type_name, egc_preset, ticks = "D", "不配储 · 有序/EGC", "不预配储能", "安全链；充电可调则有序分配"
    if not inp.site_ok or not inp.hard_passed:
        type_code, type_name = "D", "不配储 · 硬性条件/场地限制"
    elif long_ol and (not tou_ok or inp.demand_waiver):
        type_code, type_name = "E", "只增容不配储"
        egc_preset, ticks = "—", "先增容；EGC 仅防超需裕量"
    elif over and inp.overload_hours <= 2 and not inp.demand_waiver:
        if tou_ok and inp.valley_idle_hours >= 1:
            type_code, type_name = "C", "混合 · 削峰+峰谷供桩"
            egc_preset = "储充互动" if inp.scene != "厂区" else "纯储能"
            ticks = "峰谷套利；需量管理；有可调桩则经济仲裁"
        else:
            type_code, type_name = "A", "容量型 · 动态增容/需量"
            egc_preset = "纯储能"
            ticks = "需量管理；动态增容（闪充/短尖峰才勾）"
    elif tou_ok and inp.valley_idle_hours >= 1.5 and (inp.scene in ("充电", "混合") or inp.daily_kwh >= 500):
        type_code, type_name = "B", "价差型 · 峰谷+供桩"
        egc_preset = "储充互动" if inp.scene != "厂区" else "纯储能"
        ticks = "峰谷套利（底盘）；有可调桩则经济仲裁"
    elif tou_ok and inp.scene == "厂区" and inp.valley_idle_hours >= 1:
        type_code, type_name = "B", "价差型 · 厂区峰谷"
        egc_preset, ticks = "纯储能", "峰谷套利；可选需量"

    n = 0 if type_code in ("D", "E") else recommend_cabinets(inp)
    capex = n * CABINET_KWH * 1000 * YUAN_PER_WH / 1e4
    annual = 0.0
    if n > 0 and type_code in ("A", "B", "C"):
        e0 = n * CABINET_KWH * CYCLES_DEFAULT
        if type_code in ("B", "C") and tou_ok:
            annual += e0 * spread * ETA * 330 / 1e4
        if type_code in ("A", "C") and not inp.demand_waiver and inp.two_part:
            shave = min(n * CABINET_KW, max(0.0, inp.peak_kw - tf_kw))
            annual += shave * inp.demand_yuan_per_kw_month * 12 / 1e4
        annual -= capex * OPEX_PCT
        if inp.scene in ("充电", "混合") and inp.daily_kwh > 0:
            notes.append("充电服务费不计入配储增量（桩已有收入）")

    pb = (capex / annual) if annual > 0.05 and capex > 0 else None
    if type_code in ("D", "E"):
        verdict = "不建议新增配储"
    elif pb is not None and pb <= PAYBACK_GATE:
        verdict = f"建议配储 · 粗回本约 {pb:.1f} 年（过 {PAYBACK_GATE:.0f} 年闸门）"
    elif pb is not None:
        verdict = f"谨慎 · 粗回本约 {pb:.1f} 年，过不了 {PAYBACK_GATE:.0f} 年闸门"
    elif n == 0:
        verdict = "分型可配但柜数为 0，请检查峰值/电量"
    else:
        verdict = "账算不出年增量，补电价或需量参数"

    notes.append("粗算不含 V2G/补贴/土地；绿名单上会请再用单站精算页。")
    return ScoreResult(
        type_code=type_code,
        type_name=type_name,
        score_cap=score_cap,
        score_spread=score_spread,
        score_idle=score_idle,
        score_site=score_site,
        score_total=total,
        n_cabinets=n,
        capex_wan=round(capex, 2),
        annual_wan=round(annual, 2),
        payback_years=None if pb is None else round(pb, 1),
        verdict=verdict,
        egc_preset=egc_preset,
        egc_ticks=ticks,
        notes=notes,
    )
