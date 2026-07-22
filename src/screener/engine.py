"""
engine.py — Financial Screener Engine (Sprint 3, Day 15–17).

Responsibilities
----------------
1. Load a flat "screener DataFrame" from nifty100.db — one row per company
   (latest available year) with all 15 filterable metrics pre-computed.
2. Apply threshold filters for any combination of the 15 metrics.
3. Run any of the 6 named presets defined in config/screener_config.yaml.
4. Compute composite quality score (0–100) with P10/P90 winsorisation.
5. Compute sector-relative composite score.

Special filter logic
--------------------
- D/E max filter  : companies in "Banking & Finance" sector are automatically
                    skipped (exempt from D/E filter — their leverage structure
                    is incomparable with non-financials).
- ICR min filter  : companies with debt_to_equity == 0 (debt-free) are treated
                    as ICR = ∞ and always pass any ICR minimum threshold.

Usage
-----
    from src.screener.engine import ScreenerEngine
    engine = ScreenerEngine()                        # loads config + DB
    df     = engine.run_preset("quality_compounder") # returns filtered+scored DF
    all_df = engine.full_universe()                   # full 92-co universe, scored
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_DB_PATH     = os.getenv("DATABASE_URL", "nifty100.db")
_CONFIG_PATH = Path(os.getenv("SCREENER_CONFIG", "config/screener_config.yaml"))

# Sectors treated as "Financials" — exempt from D/E max filter
_FINANCIAL_SECTORS = {"Banking & Finance"}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _cagr(end: float, start: float, years: int) -> float:
    """
    Compound annual growth rate.  Returns NaN for invalid inputs.

    Uses the sign-preserving CAGR formula for negative start values:
        cagr = sign(end/start) * |end/start|^(1/years) - 1
    This avoids complex numbers when start < 0.
    """
    if years <= 0 or start is None or end is None:
        return float("nan")
    if start == 0:
        return float("nan")
    ratio = end / start
    # Use abs + sign to keep the result real for negative ratios
    sign = 1.0 if ratio >= 0 else -1.0
    return sign * (abs(ratio) ** (1.0 / years)) - 1.0


def _winsorise(series: pd.Series, p_low: int = 10, p_high: int = 90) -> pd.Series:
    """Cap extreme values at the P-low and P-high percentiles."""
    lo = series.quantile(p_low / 100)
    hi = series.quantile(p_high / 100)
    return series.clip(lower=lo, upper=hi)


def _scale_0_100(series: pd.Series) -> pd.Series:
    """Min-max scale to [0, 100] after winsorisation."""
    mn, mx = series.min(), series.max()
    if mx == mn:
        return pd.Series(50.0, index=series.index)
    return (series - mn) / (mx - mn) * 100


def _norm(series: pd.Series, p_low: int = 10, p_high: int = 90) -> pd.Series:
    """Winsorise then scale to 0–100."""
    return _scale_0_100(_winsorise(series, p_low, p_high))


# ─── Data Loading ─────────────────────────────────────────────────────────────

def load_screener_dataframe(db_path: str = _DB_PATH) -> pd.DataFrame:
    """
    Build a flat screener DataFrame — one row per company.

    Pulls the latest available year's data for every company, joining:
      - companies  (name, sector, market_cap)
      - financial_ratios (ratios)
      - profitandloss  (revenue, net_profit, opm, npm, eps)
      - cashflow  (free_cashflow, operating_cashflow)
      - stock_prices  (pe_ratio, market_cap from prices table)
      - analysis  (price_to_book from analysis)

    CAGR columns (5yr, 3yr) are computed from multi-year P&L history.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── Latest-year snapshot ──────────────────────────────────────────────────
    latest_sql = """
    WITH ranked AS (
        SELECT
            c.company_id,
            c.ticker,
            c.company_name,
            c.market_cap        AS company_market_cap,
            s.sector_name,
            fr.year,
            fr.roe,
            fr.roce,
            fr.debt_to_equity,
            fr.interest_coverage,
            fr.asset_turnover,
            fr.dividend_yield,
            fr.payout_ratio,
            fr.operating_margin,
            fr.net_margin       AS npm,
            pl.revenue,
            pl.net_profit,
            pl.eps,
            pl.opm,
            cf.free_cashflow,
            cf.operating_cashflow,
            sp.pe_ratio         AS price_to_earnings,
            sp.market_cap       AS price_market_cap,
            an.price_to_book,
            ROW_NUMBER() OVER (
                PARTITION BY c.company_id
                ORDER BY fr.year DESC
            ) AS rn
        FROM companies c
        LEFT JOIN sectors          s  ON s.sector_id  = c.sector_id
        LEFT JOIN financial_ratios fr ON fr.company_id = c.company_id
        LEFT JOIN profitandloss    pl ON pl.company_id = c.company_id
                                     AND pl.year       = fr.year
        LEFT JOIN cashflow         cf ON cf.company_id = c.company_id
                                     AND cf.year       = fr.year
        LEFT JOIN stock_prices     sp ON sp.company_id = c.company_id
                                     AND sp.year       = fr.year
        LEFT JOIN analysis         an ON an.company_id = c.company_id
                                     AND an.year       = fr.year
    )
    SELECT * FROM ranked WHERE rn = 1
    """
    df = pd.read_sql_query(latest_sql, conn)
    df = df.drop(columns=["rn"], errors="ignore")

    # Resolve market_cap: prefer stock_prices, fall back to companies table
    df["market_cap"] = df["price_market_cap"].fillna(df["company_market_cap"])
    df = df.drop(columns=["price_market_cap", "company_market_cap"], errors="ignore")

    # ── Multi-year P&L for CAGR computation ───────────────────────────────────
    pl_sql = """
    SELECT pl.company_id, pl.year, pl.revenue, pl.net_profit, pl.eps
    FROM profitandloss pl
    ORDER BY pl.company_id, pl.year
    """
    pl_all = pd.read_sql_query(pl_sql, conn)

    # ── Cash flow history (FCF) for FCF CAGR ─────────────────────────────────
    cf_sql = """
    SELECT cf.company_id, cf.year, cf.free_cashflow, cf.operating_cashflow
    FROM cashflow cf
    ORDER BY cf.company_id, cf.year
    """
    cf_all = pd.read_sql_query(cf_sql, conn)
    conn.close()

    # ── Compute 5yr CAGRs ─────────────────────────────────────────────────────
    cagr_rows: list[dict[str, Any]] = []
    for cid, grp in pl_all.groupby("company_id"):
        grp = grp.sort_values("year")
        latest_yr = int(grp["year"].max())

        def _get(col: str, yr: int, _grp=grp) -> float | None:  # capture grp
            row = _grp[_grp["year"] == yr]
            if row.empty:
                return None
            v = row.iloc[0][col]
            return float(v) if pd.notna(v) else None

        rev_now  = _get("revenue",    latest_yr)
        rev_5    = _get("revenue",    latest_yr - 5)
        rev_3    = _get("revenue",    latest_yr - 3)
        pat_now  = _get("net_profit", latest_yr)
        pat_5    = _get("net_profit", latest_yr - 5)
        eps_now  = _get("eps",        latest_yr)
        eps_5    = _get("eps",        latest_yr - 5)

        def _safe_cagr(end, start, yrs) -> float:
            if end is None or start is None or start == 0:
                return float("nan")
            return round(_cagr(end, start, yrs) * 100, 2)

        cagr_rows.append({
            "company_id":       cid,
            "revenue_cagr_5yr": _safe_cagr(rev_now, rev_5, 5),
            "revenue_cagr_3yr": _safe_cagr(rev_now, rev_3, 3),
            "pat_cagr_5yr":     _safe_cagr(pat_now, pat_5, 5),
            "eps_cagr_5yr":     _safe_cagr(eps_now, eps_5, 5),
        })

    cagr_df = pd.DataFrame(cagr_rows)
    df = df.merge(cagr_df, on="company_id", how="left")

    # ── FCF CAGR & CFO/PAT ratio ──────────────────────────────────────────────
    fcf_rows: list[dict[str, Any]] = []
    for cid, grp in cf_all.groupby("company_id"):
        grp = grp.sort_values("year")
        latest_yr = int(grp["year"].max())

        def _cf_get(col: str, yr: int, _grp=grp) -> float | None:  # capture grp
            row = _grp[_grp["year"] == yr]
            if row.empty:
                return None
            v = row.iloc[0][col]
            return float(v) if pd.notna(v) else None

        fcf_now = _cf_get("free_cashflow",      latest_yr)
        fcf_5   = _cf_get("free_cashflow",      latest_yr - 5)
        cfo_now = _cf_get("operating_cashflow", latest_yr)

        # FCF CAGR: only compute if both endpoints are available (may be negative)
        if fcf_now is not None and fcf_5 is not None and fcf_5 != 0:
            fcf_cagr = round(_cagr(fcf_now, fcf_5, 5) * 100, 2)
        else:
            fcf_cagr = float("nan")

        fcf_rows.append({
            "company_id":   cid,
            "fcf_cagr_5yr": fcf_cagr,
            "cfo_latest":   cfo_now,
        })

    fcf_df = pd.DataFrame(fcf_rows)
    df = df.merge(fcf_df, on="company_id", how="left")

    # CFO/PAT ratio
    _mask = (df["net_profit"].notna()) & (df["net_profit"] != 0) & (df["cfo_latest"].notna())
    df["cfo_pat_ratio"] = df["cfo_latest"].where(_mask) / df["net_profit"].where(_mask)
    df = df.drop(columns=["cfo_latest"], errors="ignore")

    # FCF positive flag (binary)
    df["fcf_positive_flag"] = (df["free_cashflow"].fillna(0) > 0).astype(float)

    # ── D/E declining YoY flag (for Turnaround Watch preset) ─────────────────
    de_sql = """
    SELECT fr.company_id, fr.year, fr.debt_to_equity
    FROM financial_ratios fr
    ORDER BY fr.company_id, fr.year DESC
    """
    conn2  = sqlite3.connect(db_path)
    de_all = pd.read_sql_query(de_sql, conn2)
    conn2.close()
    de_decline: dict[Any, bool] = {}
    for cid, grp in de_all.groupby("company_id"):
        grp = grp.sort_values("year", ascending=False)
        if len(grp) >= 2:
            latest_de = grp.iloc[0]["debt_to_equity"]
            prior_de  = grp.iloc[1]["debt_to_equity"]
            de_decline[cid] = (
                pd.notna(latest_de) and pd.notna(prior_de)
                and float(latest_de) < float(prior_de)
            )
        else:
            de_decline[cid] = False
    df["de_declining"] = df["company_id"].map(de_decline).fillna(False)

    # ── Sanitise column types ─────────────────────────────────────────────────
    numeric_cols = [
        "roe", "roce", "debt_to_equity", "interest_coverage", "asset_turnover",
        "dividend_yield", "payout_ratio", "operating_margin", "npm",
        "revenue", "net_profit", "eps", "opm",
        "free_cashflow", "operating_cashflow",
        "price_to_earnings", "market_cap", "price_to_book",
        "revenue_cagr_5yr", "revenue_cagr_3yr", "pat_cagr_5yr", "eps_cagr_5yr",
        "fcf_cagr_5yr", "cfo_pat_ratio",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    logger.info("Screener DataFrame loaded: %d companies", len(df))
    return df.reset_index(drop=True)


# ─── Filter Application ───────────────────────────────────────────────────────

def _apply_single_filter(
    df: pd.DataFrame,
    metric: str,
    operator: str,
    threshold: float,
    metric_cfg: dict[str, Any],
) -> pd.Series:
    """
    Return a boolean mask (True = passes filter).

    Handles D/E exemption for financial sector and ICR infinity logic.
    """
    col = metric_cfg.get("column", metric)

    # Handle special internal alias for payout ratio
    if metric == "_payout_ratio_max":
        col = "payout_ratio"

    if col not in df.columns:
        logger.warning("Column '%s' not found — filter skipped", col)
        return pd.Series(True, index=df.index)

    mask = pd.Series(True, index=df.index)

    if operator == "positive":
        mask = df[col].fillna(0) > 0

    elif operator == "eq":
        mask = df[col].fillna(-1) == threshold

    elif operator == "min":
        # ICR special: debt-free companies always pass
        if metric == "interest_coverage" and metric_cfg.get("treat_debt_free_as_infinity"):
            debt_free = df["debt_to_equity"].fillna(1) == 0
            mask = debt_free | (df[col].fillna(-np.inf) >= threshold)
        else:
            mask = df[col].fillna(-np.inf) >= threshold

    elif operator == "max":
        base_mask = df[col].fillna(np.inf) <= threshold
        # D/E exemption: financials are exempt from D/E max filters
        if metric == "debt_to_equity" and metric_cfg.get("financials_exempt"):
            is_financial = df["sector_name"].isin(_FINANCIAL_SECTORS)
            mask = is_financial | base_mask   # financials always pass
        else:
            mask = base_mask

    elif operator == "declining":
        # Uses pre-computed boolean column
        if "de_declining" in df.columns:
            mask = df["de_declining"].fillna(False)
        else:
            mask = pd.Series(True, index=df.index)

    return mask


def apply_filters(
    df: pd.DataFrame,
    filters: list[dict[str, Any]],
    metric_config: dict[str, Any],
) -> pd.DataFrame:
    """
    Apply a list of filter dicts to df and return the passing rows.

    Each filter dict must have: metric, operator, threshold.
    """
    combined_mask = pd.Series(True, index=df.index)
    for flt in filters:
        metric   = flt["metric"]
        operator = flt["operator"]
        threshold = float(flt.get("threshold", 0))
        mcfg      = metric_config.get(metric, {})
        m = _apply_single_filter(df, metric, operator, threshold, mcfg)
        combined_mask = combined_mask & m

    return df[combined_mask].copy()


# ─── Composite Quality Score ──────────────────────────────────────────────────

def compute_composite_score(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """
    Compute composite_quality_score (0–100) for each company.

    Pillars and weights (from config):
      35% Profitability  : ROE, ROCE, NPM
      30% Cash Quality   : FCF CAGR, CFO/PAT, FCF positive flag
      20% Growth         : Revenue CAGR 5yr, PAT CAGR 5yr
      15% Leverage       : D/E (inverted), ICR
    """
    df = df.copy()
    score_cfg   = config.get("composite_score", {})
    p_low       = score_cfg.get("winsorisation", {}).get("lower_percentile", 10)
    p_high      = score_cfg.get("winsorisation", {}).get("upper_percentile", 90)

    def norm(col: str, invert: bool = False) -> pd.Series:
        s = df[col].fillna(df[col].median()) if col in df.columns else pd.Series(50.0, index=df.index)
        s = pd.to_numeric(s, errors="coerce").fillna(s.median() if s.median() == s.median() else 50.0)
        s = _winsorise(s, p_low, p_high)
        s = _scale_0_100(s)
        return 100 - s if invert else s

    # ── Profitability pillar (35%) ────────────────────────────────────────────
    prof_score = (
        norm("roe")   * 0.15 +
        norm("roce")  * 0.10 +
        norm("npm")   * 0.10
    ) / 0.35 * 0.35   # keep on 0-35 range, then re-weight below

    prof_score = norm("roe") * (0.15 / 0.35) + norm("roce") * (0.10 / 0.35) + norm("npm") * (0.10 / 0.35)

    # ── Cash Quality pillar (30%) ─────────────────────────────────────────────
    fcf_flag_scaled = df["fcf_positive_flag"].fillna(0) * 100     # 0 or 100
    cfo_pat_norm    = norm("cfo_pat_ratio")

    cash_score = (
        norm("fcf_cagr_5yr")  * (0.15 / 0.30) +
        cfo_pat_norm          * (0.10 / 0.30) +
        fcf_flag_scaled       * (0.05 / 0.30)
    )

    # ── Growth pillar (20%) ───────────────────────────────────────────────────
    growth_score = (
        norm("revenue_cagr_5yr") * (0.10 / 0.20) +
        norm("pat_cagr_5yr")     * (0.10 / 0.20)
    )

    # ── Leverage pillar (15%) — lower D/E = better, higher ICR = better ──────
    de_score  = norm("debt_to_equity", invert=True)
    icr_score = norm("interest_coverage")

    # Debt-free companies get max D/E score
    debt_free_mask = df["debt_to_equity"].fillna(1) == 0
    de_score[debt_free_mask] = 100.0

    leverage_score = (
        de_score  * (0.10 / 0.15) +
        icr_score * (0.05 / 0.15)
    )

    # ── Combine with pillar weights ───────────────────────────────────────────
    df["composite_quality_score"] = (
        prof_score     * 0.35 +
        cash_score     * 0.30 +
        growth_score   * 0.20 +
        leverage_score * 0.15
    ).round(2)

    return df


def compute_sector_relative_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise composite_quality_score within each sector so scores
    reflect performance relative to sector peers (0–100 within sector).
    """
    df = df.copy()
    sector_rel: list[pd.Series] = []

    for sector, grp in df.groupby("sector_name"):
        s = grp["composite_quality_score"].fillna(0)
        mn, mx = s.min(), s.max()
        if mx > mn:
            scaled = (s - mn) / (mx - mn) * 100
        else:
            scaled = pd.Series(50.0, index=grp.index)
        sector_rel.append(scaled)

    if sector_rel:
        df["sector_relative_score"] = pd.concat(sector_rel).round(2)
    else:
        df["sector_relative_score"] = 50.0

    return df


# ─── Screener Engine ──────────────────────────────────────────────────────────

class ScreenerEngine:
    """
    Main screener entry point.

    Parameters
    ----------
    db_path     : path to nifty100.db (defaults to DATABASE_URL env var)
    config_path : path to screener_config.yaml
    """

    def __init__(
        self,
        db_path: str = _DB_PATH,
        config_path: str | Path = _CONFIG_PATH,
    ) -> None:
        self.db_path = db_path
        with open(config_path, "r") as fh:
            self.config: dict[str, Any] = yaml.safe_load(fh)
        self._universe: pd.DataFrame | None = None

    # ── Universe ──────────────────────────────────────────────────────────────

    def full_universe(self, force_reload: bool = False) -> pd.DataFrame:
        """
        Return the full scored universe (92 companies, one row each).
        Cached after first call.
        """
        if self._universe is None or force_reload:
            df = load_screener_dataframe(self.db_path)
            df = compute_composite_score(df, self.config)
            df = compute_sector_relative_score(df)
            df = df.sort_values("composite_quality_score", ascending=False)
            self._universe = df.reset_index(drop=True)
        return self._universe

    # ── Preset Runner ─────────────────────────────────────────────────────────

    def run_preset(self, preset_name: str) -> pd.DataFrame:
        """
        Apply a named preset and return the filtered, scored DataFrame.

        Raises
        ------
        KeyError if preset_name not found in config.
        """
        presets = self.config.get("presets", {})
        if preset_name not in presets:
            available = list(presets.keys())
            raise KeyError(f"Preset '{preset_name}' not found. Available: {available}")

        preset       = presets[preset_name]
        filters      = preset.get("filters", [])
        metric_cfg   = self.config.get("metrics", {})

        universe = self.full_universe()
        result   = apply_filters(universe, filters, metric_cfg)
        result   = result.sort_values("composite_quality_score", ascending=False)
        return result.reset_index(drop=True)

    def run_all_presets(self) -> dict[str, pd.DataFrame]:
        """Run all 6 presets and return a dict keyed by preset name."""
        results = {}
        for name in self.config.get("presets", {}):
            try:
                results[name] = self.run_preset(name)
                logger.info(
                    "Preset '%s': %d companies", name, len(results[name])
                )
            except Exception as exc:
                logger.error("Preset '%s' failed: %s", name, exc)
                results[name] = pd.DataFrame()
        return results

    # ── Custom Filter ─────────────────────────────────────────────────────────

    def run_custom(self, filters: list[dict[str, Any]]) -> pd.DataFrame:
        """
        Apply a custom list of filter dicts against the full universe.

        Example
        -------
        engine.run_custom([
            {"metric": "roe",            "operator": "min", "threshold": 20},
            {"metric": "debt_to_equity", "operator": "max", "threshold": 0.5},
        ])
        """
        metric_cfg = self.config.get("metrics", {})
        universe   = self.full_universe()
        result     = apply_filters(universe, filters, metric_cfg)
        return result.sort_values("composite_quality_score", ascending=False).reset_index(drop=True)
