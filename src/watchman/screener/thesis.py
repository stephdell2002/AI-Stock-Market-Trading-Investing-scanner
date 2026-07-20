"""Plain-English theses for screened names.

2-3 sentences per name, citing the actual numbers behind the score — the
strongest pillar leads, the weakest pillar is named as the weak spot, and thin
data coverage is called out rather than smoothed over.
"""

from __future__ import annotations

import math

import pandas as pd

from watchman.screener.scoring import PILLARS


def _has(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _fmt(v: float, digits: int = 1) -> str:
    return f"{v:.{digits}f}"


def _signed(v: float) -> str:
    return f"{v:+.1f}"


def _pillar_facts(pillar: str, m: pd.Series, sector_pe: float | None) -> str | None:
    """Short factual clause for one pillar, only from present numbers."""
    parts: list[str] = []
    if pillar == "quality":
        if _has(m.get("roic")):
            parts.append(f"ROIC {_fmt(m['roic'])}%")
        if _has(m.get("debt_to_ebitda")):
            parts.append(f"debt/EBITDA {_fmt(m['debt_to_ebitda'])}x")
        if _has(m.get("fcf_conversion")):
            parts.append(f"FCF conversion {_fmt(m['fcf_conversion'], 0)}%")
        if _has(m.get("operating_margin_trend")):
            parts.append(f"operating margin {_signed(m['operating_margin_trend'])}pp/yr")
    elif pillar == "growth":
        if _has(m.get("revenue_cagr")):
            parts.append(f"revenue CAGR {_fmt(m['revenue_cagr'])}% (~3y)")
        if _has(m.get("eps_cagr")):
            parts.append(f"EPS CAGR {_fmt(m['eps_cagr'])}% (~3y)")
    elif pillar == "valuation":
        if _has(m.get("fcf_yield")):
            parts.append(f"FCF yield {_fmt(m['fcf_yield'])}%")
        if _has(m.get("trailing_pe")):
            pe_txt = f"P/E {_fmt(m['trailing_pe'])}"
            if _has(sector_pe):
                pe_txt += f" vs sector median {_fmt(sector_pe)}"
            parts.append(pe_txt)
        if _has(m.get("ev_to_ebitda")):
            parts.append(f"EV/EBITDA {_fmt(m['ev_to_ebitda'])}x")
    elif pillar == "momentum":
        if _has(m.get("rel_strength_6m")):
            parts.append(f"{_signed(m['rel_strength_6m'])}% vs SPY over 6m")
        if _has(m.get("rel_strength_12m")):
            parts.append(f"{_signed(m['rel_strength_12m'])}% over 12m")
    if not parts:
        return None
    return ", ".join(parts[:3])


def build_thesis(
    symbol: str,
    score_row: pd.Series,
    metric_row: pd.Series,
    universe_size: int,
    sector_pe: float | None,
) -> str:
    """Compose the thesis for one symbol from its scores and raw metrics."""
    name = score_row.get("name") or symbol
    rank = int(score_row["rank"])
    composite = score_row["composite"]

    ranked = sorted(
        (p for p in PILLARS if _has(score_row.get(p))),
        key=lambda p: score_row[p],
        reverse=True,
    )
    sentences: list[str] = []
    lead = f"{name} scores {composite:.0f}/100 (#{rank} of {universe_size})"
    if ranked:
        facts = _pillar_facts(ranked[0], metric_row, sector_pe)
        lead += f", led by {ranked[0]} ({facts})" if facts else f", led by {ranked[0]}"
    sentences.append(lead + ".")

    middle_facts = [
        f"{pillar}: {facts}"
        for pillar in ranked[1:-1]
        if (facts := _pillar_facts(pillar, metric_row, sector_pe))
    ]
    if middle_facts:
        joined = "; ".join(middle_facts)
        sentences.append(joined[0].upper() + joined[1:] + ".")

    if len(ranked) > 1:
        weakest = ranked[-1]
        facts = _pillar_facts(weakest, metric_row, sector_pe)
        weak = f"Weak spot: {weakest} ({score_row[weakest]:.0f}/100"
        weak += f"; {facts})." if facts else ")."
        sentences.append(weak)

    coverage = score_row.get("coverage")
    if _has(coverage) and coverage < 0.7:
        sentences.append(
            f"Caution: only {coverage:.0%} of metrics had data — treat this rank skeptically."
        )
    return " ".join(sentences)
