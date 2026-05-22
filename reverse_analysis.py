"""
逆向分析：与正向测算解耦，仅依赖数值基线（投资、稳态运营年净收益、测算年限等）。
作者: jiali.qiu
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

SubsidySolveMode = Literal["once", "installment"]
PaybackMetric = Literal["interp", "static"]


@dataclass(frozen=True)
class ForwardBaseline:
    """正向测算基线（已剔除政府补贴）。"""

    capex_yuan: float
    net_operating_year_yuan: float
    horizon_years: int
    payback_static_years: float
    payback_interp_years: float | None


@dataclass(frozen=True)
class SubsidyReverseResult:
    """目标回收期反算补贴结果。"""

    ok: bool
    message: str
    subsidy_wan: float
    payback_static_years: float
    payback_interp_years: float | None
    already_met: bool
    cf_baseline_wan: np.ndarray
    cf_with_subsidy_wan: np.ndarray


def build_cash_flows_wan(
    capex_yuan: float,
    net_operating_year_yuan: float,
    horizon_years: int,
    *,
    subsidy_once_wan: float = 0.0,
    subsidy_annual_wan: float = 0.0,
    subsidy_annual_years: int = 0,
) -> np.ndarray:
    """
    构造各年现金流（万元）：第 0 年 -总投资；运营年 + 补贴。
    一次性补贴仅第 1 年；分期补贴在第 1..Y 年。
    """
    n = max(1, int(horizon_years))
    cf = np.zeros(n + 1, dtype=float)
    cf[0] = -max(0.0, float(capex_yuan)) / 1e4
    base = max(0.0, float(net_operating_year_yuan)) / 1e4
    once = max(0.0, float(subsidy_once_wan))
    annual = max(0.0, float(subsidy_annual_wan))
    y_cap = max(0, int(subsidy_annual_years))
    for y in range(1, n + 1):
        sub = 0.0
        if y == 1:
            sub += once
        if y <= y_cap:
            sub += annual
        cf[y] = base + sub
    return cf


def payback_time_years(cf_wan: np.ndarray) -> float | None:
    """累计现金流首次由负转正时的线性插值回本（年）。"""
    cum = np.cumsum(cf_wan)
    if cum[-1] < 0:
        return None
    for i in range(1, len(cum)):
        if cum[i - 1] < 0 <= cum[i]:
            prev = cum[i - 1]
            cur = cum[i]
            if cur == prev:
                return float(i)
            t = -prev / (cur - prev)
            return (i - 1) + t
    if cum[0] >= 0:
        return 0.0
    return None


def payback_static_years(capex_yuan: float, net_year_yuan: float) -> float:
    """静态回本 = 总投资 / 年净收益。"""
    if net_year_yuan <= 1e-6:
        return float("inf")
    return float(capex_yuan) / float(net_year_yuan)


def baseline_from_projection(
    capex_yuan: float,
    net_operating_year_yuan: float,
    horizon_years: int,
) -> ForwardBaseline:
    """由正向 projection 关键字段构造逆向基线。"""
    cf0 = build_cash_flows_wan(capex_yuan, net_operating_year_yuan, horizon_years)
    return ForwardBaseline(
        capex_yuan=float(capex_yuan),
        net_operating_year_yuan=float(net_operating_year_yuan),
        horizon_years=max(1, int(horizon_years)),
        payback_static_years=payback_static_years(capex_yuan, net_operating_year_yuan),
        payback_interp_years=payback_time_years(cf0),
    )


def _net_year_with_subsidy(
    baseline: ForwardBaseline,
    *,
    subsidy_once_wan: float,
    subsidy_annual_wan: float,
    subsidy_annual_years: int,
) -> float:
    """静态回本口径下的等效年净收益（万元→元）。"""
    extra_wan = 0.0
    if subsidy_annual_wan > 0 and subsidy_annual_years > 0:
        extra_wan += subsidy_annual_wan
    if subsidy_once_wan > 0 and baseline.horizon_years > 0:
        extra_wan += subsidy_once_wan / float(baseline.horizon_years)
    return baseline.net_operating_year_yuan + extra_wan * 1e4


def _payback_for_subsidy(
    baseline: ForwardBaseline,
    metric: PaybackMetric,
    *,
    subsidy_once_wan: float,
    subsidy_annual_wan: float,
    subsidy_annual_years: int,
) -> float | None:
    if metric == "static":
        net_y = _net_year_with_subsidy(
            baseline,
            subsidy_once_wan=subsidy_once_wan,
            subsidy_annual_wan=subsidy_annual_wan,
            subsidy_annual_years=subsidy_annual_years,
        )
        pb = payback_static_years(baseline.capex_yuan, net_y)
        return pb if np.isfinite(pb) else None
    cf = build_cash_flows_wan(
        baseline.capex_yuan,
        baseline.net_operating_year_yuan,
        baseline.horizon_years,
        subsidy_once_wan=subsidy_once_wan,
        subsidy_annual_wan=subsidy_annual_wan,
        subsidy_annual_years=subsidy_annual_years,
    )
    return payback_time_years(cf)


def _search_subsidy_amount(
    baseline: ForwardBaseline,
    target_years: float,
    metric: PaybackMetric,
    mode: SubsidySolveMode,
    installment_years: int,
) -> SubsidyReverseResult:
    cf_base = build_cash_flows_wan(
        baseline.capex_yuan,
        baseline.net_operating_year_yuan,
        baseline.horizon_years,
    )
    pb0 = _payback_for_subsidy(
        baseline,
        metric,
        subsidy_once_wan=0.0,
        subsidy_annual_wan=0.0,
        subsidy_annual_years=0,
    )
    pb0_static = payback_static_years(
        baseline.capex_yuan, baseline.net_operating_year_yuan
    )

    if target_years <= 0:
        return SubsidyReverseResult(
            ok=False,
            message="目标回本周期须大于 0 年。",
            subsidy_wan=0.0,
            payback_static_years=pb0_static,
            payback_interp_years=payback_time_years(cf_base),
            already_met=False,
            cf_baseline_wan=cf_base,
            cf_with_subsidy_wan=cf_base,
        )

    if baseline.net_operating_year_yuan <= 1e-6:
        return SubsidyReverseResult(
            ok=False,
            message="稳态运营年净收益 ≤ 0，无法通过补贴实现目标回收期。",
            subsidy_wan=0.0,
            payback_static_years=pb0_static,
            payback_interp_years=pb0,
            already_met=False,
            cf_baseline_wan=cf_base,
            cf_with_subsidy_wan=cf_base,
        )

    pb_check = pb0 if metric == "interp" else pb0_static
    if pb_check is not None and pb_check <= target_years + 1e-6:
        return SubsidyReverseResult(
            ok=True,
            message="当前基线（无补贴）已满足目标回收期，补贴需求为 0。",
            subsidy_wan=0.0,
            payback_static_years=pb0_static,
            payback_interp_years=payback_time_years(cf_base),
            already_met=True,
            cf_baseline_wan=cf_base,
            cf_with_subsidy_wan=cf_base,
        )

    capex_wan = baseline.capex_yuan / 1e4
    hi = max(capex_wan * 3.0, 1.0)
    inst_y = max(1, min(int(installment_years), baseline.horizon_years))

    def pb_at(amount: float) -> float | None:
        if mode == "once":
            return _payback_for_subsidy(
                baseline,
                metric,
                subsidy_once_wan=amount,
                subsidy_annual_wan=0.0,
                subsidy_annual_years=0,
            )
        return _payback_for_subsidy(
            baseline,
            metric,
            subsidy_once_wan=0.0,
            subsidy_annual_wan=amount,
            subsidy_annual_years=inst_y,
        )

    while pb_at(hi) is None or (
        pb_at(hi) is not None and pb_at(hi) > target_years + 1e-6
    ):
        hi *= 2.0
        if hi > 1e7:
            return SubsidyReverseResult(
                ok=False,
                message="在合理补贴上限内无法达到目标回收期，请放宽目标或优化方案。",
                subsidy_wan=0.0,
                payback_static_years=pb0_static,
                payback_interp_years=pb0,
                already_met=False,
                cf_baseline_wan=cf_base,
                cf_with_subsidy_wan=cf_base,
            )

    lo = 0.0
    for _ in range(64):
        mid = (lo + hi) / 2.0
        pb_mid = pb_at(mid)
        if pb_mid is None:
            lo = mid
            continue
        if pb_mid <= target_years + 1e-6:
            hi = mid
        else:
            lo = mid

    amount = hi
    if mode == "once":
        cf_sol = build_cash_flows_wan(
            baseline.capex_yuan,
            baseline.net_operating_year_yuan,
            baseline.horizon_years,
            subsidy_once_wan=amount,
        )
        label = f"一次性补贴（第 1 年）约 **{amount:,.2f}** 万元"
    else:
        cf_sol = build_cash_flows_wan(
            baseline.capex_yuan,
            baseline.net_operating_year_yuan,
            baseline.horizon_years,
            subsidy_annual_wan=amount,
            subsidy_annual_years=inst_y,
        )
        label = (
            f"分期补贴约 **{amount:,.2f}** 万元/年 × **{inst_y}** 年"
        )

    pb_s = payback_static_years(
        baseline.capex_yuan,
        _net_year_with_subsidy(
            baseline,
            subsidy_once_wan=amount if mode == "once" else 0.0,
            subsidy_annual_wan=amount if mode == "installment" else 0.0,
            subsidy_annual_years=inst_y if mode == "installment" else 0,
        ),
    )
    pb_i = payback_time_years(cf_sol)
    metric_label = "插值回本" if metric == "interp" else "静态回本"
    return SubsidyReverseResult(
        ok=True,
        message=f"为在 **{target_years:.1f}** 年内达到目标（{metric_label}），{label}。",
        subsidy_wan=amount,
        payback_static_years=pb_s,
        payback_interp_years=pb_i,
        already_met=False,
        cf_baseline_wan=cf_base,
        cf_with_subsidy_wan=cf_sol,
    )


def solve_subsidy_for_target_payback(
    baseline: ForwardBaseline,
    target_payback_years: float,
    *,
    metric: PaybackMetric = "interp",
    subsidy_mode: SubsidySolveMode = "once",
    installment_years: int = 5,
) -> SubsidyReverseResult:
    """
    反算达到目标回收期所需的最低政府补贴（万元）。
    基线不含政府补贴；仅叠加反算补贴本身。
    """
    return _search_subsidy_amount(
        baseline,
        float(target_payback_years),
        metric,
        subsidy_mode,
        installment_years,
    )


def cashflow_compare_rows(
    baseline: ForwardBaseline,
    result: SubsidyReverseResult,
) -> list[dict[str, Any]]:
    """逐年现金流对照表行。"""
    rows: list[dict[str, Any]] = []
    n = baseline.horizon_years
    cum_b = np.cumsum(result.cf_baseline_wan)
    cum_s = np.cumsum(result.cf_with_subsidy_wan)
    for y in range(0, n + 1):
        label = "第0年(投资)" if y == 0 else f"第{y}年"
        rows.append(
            {
                "年份": label,
                "无补贴(万元)": result.cf_baseline_wan[y],
                "无补贴累计": cum_b[y],
                "含补贴(万元)": result.cf_with_subsidy_wan[y],
                "含补贴累计": cum_s[y],
            }
        )
    return rows
