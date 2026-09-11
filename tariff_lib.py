# -*- coding: utf-8 -*-
"""电价库：按省/市匹配。作者: jiali.qiu

库内峰/谷只代表「电网代理购电 + 政府核定固定峰谷」。
已入市动态分时不得用库内价差做保底套利。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
TARIFF_PATH = ROOT / "config" / "tariff_library.json"

_CITY_FIRST = {"深圳": "shenzhen", "上海": "shanghai"}


def load_library() -> dict[str, Any]:
    with TARIFF_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _norm(s: str) -> str:
    return str(s or "").strip().replace("省", "").replace("市", "")


def match_tariff(province: str, city: str, library: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """按城市优先、再按省匹配。深圳单独走深圳供电局。"""
    lib = library or load_library()
    city_n = _norm(city)
    prov_n = _norm(province)
    if city_n in _CITY_FIRST:
        tid = _CITY_FIRST[city_n]
        for r in lib["regions"]:
            if r["id"] == tid:
                return r
    for r in lib["regions"]:
        cities = [_norm(c) for c in r.get("cities", [])]
        if city_n and city_n in cities:
            return r
    for r in lib["regions"]:
        if _norm(r.get("province", "")) == prov_n and r["id"] not in ("shenzhen",):
            if r["id"] == "guangdong" and city_n == "深圳":
                continue
            return r
    return None


def _regime_row(region: dict[str, Any], spec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not spec:
        return None
    usable = bool(spec.get("usable_for_spread"))
    marker = spec.get("marker") or ""
    return {
        "地区": region.get("label"),
        "适用用户": spec.get("user_label"),
        "电价形态": spec.get("form_label"),
        "标记": marker,
        "库内峰": region.get("peak_yuan") if usable else "—",
        "库内谷": region.get("valley_yuan") if usable else "—",
        "库内价差": region.get("spread_yuan") if usable else "—",
        "库内价可否测算套利": "可以（仅筛站示意）" if usable else "不可以",
        "单价锚": region.get("price_anchor") if usable else "须对手头零售/现货合同",
        "即将变动说明": spec.get("pending_detail") or "",
    }


def coverage_rows(library: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """电价库覆盖：代理购电与入市必须分行，禁止混成一列「有分时」。"""
    lib = library or load_library()
    rows: list[dict[str, Any]] = []
    for r in lib["regions"]:
        for key in ("agent", "agent_extra", "market"):
            row = _regime_row(r, r.get(key))
            if row:
                rows.append(row)
    return rows


def _pick_regime(t: dict[str, Any], purchase_market: bool) -> dict[str, Any]:
    if purchase_market:
        return t.get("market") or {}
    return t.get("agent") or {}


def tariff_row_fields(t: dict[str, Any] | None, purchase_market: bool = False) -> dict[str, Any]:
    if not t:
        return {
            "电价库": "未入库",
            "是否分时": "未知",
            "电价形态": "未知",
            "电价标记": "请手填或补入库",
            "峰电价": None,
            "谷电价": None,
            "平电价": None,
            "价差": None,
            "需量元/kW月": None,
            "容量元/kVA月": None,
            "直接入市或取消政府分时": "未知",
            "电价置信": "请手填或补入库",
            "峰段": "",
            "谷段": "",
            "库内价可否测算套利": "不可以",
        }
    spec = _pick_regime(t, purchase_market)
    usable = bool(spec.get("usable_for_spread", not purchase_market))
    marker = spec.get("marker") or ""
    return {
        "电价库": t.get("label"),
        "是否分时": spec.get("form_label") or ("是" if t.get("has_tou") else "否"),
        "电价形态": spec.get("form_label") or "",
        "电价标记": marker,
        "峰电价": t.get("peak_yuan") if usable else None,
        "谷电价": t.get("valley_yuan") if usable else None,
        "平电价": t.get("flat_yuan") if usable else None,
        "价差": t.get("spread_yuan") if usable else None,
        "需量元/kW月": t.get("demand_yuan_per_kw_month"),
        "容量元/kVA月": t.get("capacity_yuan_per_kva_month"),
        "直接入市或取消政府分时": "⭐ 已取消政府固定分时" if t.get("market_no_gov_tou") and purchase_market else (
            "代理购电执行政府分时" if not purchase_market else "入市形态见电价标记"
        ),
        "电价置信": t.get("price_confidence"),
        "峰段": t.get("peak_hours") if usable else "入市：以零售合同/现货曲线为准",
        "谷段": t.get("valley_hours") if usable else "入市：以零售合同/现货曲线为准",
        "库内价可否测算套利": "可以（仅筛站示意）" if usable else "不可以",
    }


_SPAN = re.compile(
    r"(?:次日)?\d{1,2}(?::\d{2})?\s*[–\-—~～至到]\s*(?:次日)?\d{1,2}(?::\d{2})?"
)


def _fmt_yuan(v: Any, usable: bool) -> str:
    if not usable or v is None:
        return "须手填"
    try:
        return f"{float(v):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "须手填"


def _norm_clock(span: str) -> str:
    """06–08 → 06:00–08:00。"""
    m = re.match(
        r"(次日)?(\d{1,2})(?::(\d{2}))?\s*[–\-—~～至到]\s*(次日)?(\d{1,2})(?::(\d{2}))?",
        span.strip(),
    )
    if not m:
        return span.strip()
    a_day, a_h, a_m, b_day, b_h, b_m = m.groups()
    left = f"{a_day or ''}{int(a_h):02d}:{a_m or '00'}"
    right = f"{b_day or ''}{int(b_h):02d}:{b_m or '00'}"
    return f"{left}–{right}"


def _clause_note(text: str, start: int) -> str:
    ctx = text[:start]
    for sep in ("。", "；", "："):
        if sep in ctx:
            ctx = ctx.rsplit(sep, 1)[-1]
    return ctx.strip(" 、，;；")


def _rows_from_hours(kind: str, hours: str, price: Any, usable: bool) -> list[dict[str, str]]:
    text = str(hours or "").strip()
    yuan = _fmt_yuan(price, usable)
    if not text:
        return [{"类型": kind, "时段": "库内未写明", "库内电价（元/kWh）": yuan, "说明": ""}]
    found = list(_SPAN.finditer(text))
    if not found:
        return [{"类型": kind, "时段": text, "库内电价（元/kWh）": yuan, "说明": ""}]
    rows: list[dict[str, str]] = []
    for m in found:
        note = _clause_note(text, m.start())
        label = "尖峰" if "尖峰" in note or "尖峰" in m.group(0) else kind
        if "深谷" in note:
            label = "深谷"
        rows.append(
            {
                "类型": label,
                "时段": _norm_clock(m.group(0)),
                "库内电价（元/kWh）": yuan if label in (kind, "峰", "谷", "平") else "见说明（上浮/下浮）",
                "说明": note,
            }
        )
    return rows


def cities_by_province(library: dict[str, Any] | None = None) -> dict[str, list[str]]:
    lib = library or load_library()
    out: dict[str, list[str]] = {}
    for r in lib["regions"]:
        p = str(r.get("province") or "")
        out.setdefault(p, [])
        for c in r.get("cities") or []:
            if c not in out[p]:
                out[p].append(str(c))
    return out


def tariff_schedule_rows(t: dict[str, Any] | None, purchase_market: bool = False) -> list[dict[str, str]]:
    """峰/谷/平：几点到几点、库内什么价。入市仍展示时段，电价标须手填。"""
    if not t:
        return [
            {"类型": "峰", "时段": "请先选省市", "库内电价（元/kWh）": "须手填", "说明": "未匹配电价库"},
            {"类型": "平", "时段": "请先选省市", "库内电价（元/kWh）": "须手填", "说明": "未匹配电价库"},
            {"类型": "谷", "时段": "请先选省市", "库内电价（元/kWh）": "须手填", "说明": "未匹配电价库"},
        ]
    spec = _pick_regime(t, purchase_market)
    usable = bool(spec.get("usable_for_spread", not purchase_market))
    rows: list[dict[str, str]] = []
    rows.extend(_rows_from_hours("峰", str(t.get("peak_hours") or ""), t.get("peak_yuan"), usable))
    rows.extend(_rows_from_hours("谷", str(t.get("valley_hours") or ""), t.get("valley_yuan"), usable))
    rows.append(
        {
            "类型": "平",
            "时段": "其余未单列时段",
            "库内电价（元/kWh）": _fmt_yuan(t.get("flat_yuan"), usable),
            "说明": str(t.get("ratio") or ""),
        }
    )
    return rows

