"""
test_engine.py — Unit tests for the Screener Engine (Sprint 3, Day 21).

Tests cover:
  1. Each of the 6 presets returns between 5 and 50 companies
  2. D/E max filter skips Banking & Finance companies
  3. ICR min filter always passes debt-free (D/E=0) companies
  4. Composite quality score is between 0 and 100 for all companies
  5. Custom filter returns expected subset
  6. Quality Compounder results — top 5 all have ROE > 15% and D/E < 1
"""

from __future__ import annotations

import sqlite3
import math

import numpy as np
import pandas as pd
import pytest
import yaml

from src.screener.engine import (
    ScreenerEngine,
    apply_filters,
    compute_composite_score,
    compute_sector_relative_score,
    load_screener_dataframe,
    _apply_single_filter,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine() -> ScreenerEngine:
    """Shared ScreenerEngine instance for all tests."""
    return ScreenerEngine()


@pytest.fixture(scope="module")
def universe(engine: ScreenerEngine) -> pd.DataFrame:
    """Full 92-company scored universe."""
    return engine.full_universe()


@pytest.fixture(scope="module")
def config(engine: ScreenerEngine) -> dict:
    return engine.config


# ─── Test 1: Universe loads correctly ─────────────────────────────────────────

class TestUniverseLoad:
    def test_universe_row_count(self, universe: pd.DataFrame) -> None:
        """Universe must contain between 80 and 100 companies."""
        assert 80 <= len(universe) <= 100, (
            f"Expected 80–100 companies, got {len(universe)}"
        )

    def test_universe_has_required_columns(self, universe: pd.DataFrame) -> None:
        required = [
            "company_id", "ticker", "company_name", "sector_name",
            "roe", "roce", "debt_to_equity", "interest_coverage",
            "free_cashflow", "revenue", "net_profit",
            "composite_quality_score", "sector_relative_score",
        ]
        missing = [c for c in required if c not in universe.columns]
        assert not missing, f"Missing columns: {missing}"

    def test_composite_score_range(self, universe: pd.DataFrame) -> None:
        """All composite scores must be in [0, 100]."""
        scores = universe["composite_quality_score"].dropna()
        assert not scores.empty, "No composite scores computed"
        assert scores.min() >= 0, f"Score below 0: {scores.min()}"
        assert scores.max() <= 100, f"Score above 100: {scores.max()}"

    def test_sector_relative_score_range(self, universe: pd.DataFrame) -> None:
        """Sector-relative scores must be in [0, 100]."""
        scores = universe["sector_relative_score"].dropna()
        assert scores.min() >= 0
        assert scores.max() <= 100

    def test_one_row_per_company(self, universe: pd.DataFrame) -> None:
        """No duplicate company_id rows in the universe."""
        dupes = universe["company_id"].duplicated().sum()
        assert dupes == 0, f"{dupes} duplicate company_id rows found"


# ─── Test 2: 6 Presets return 5–50 companies ──────────────────────────────────

class TestPresetBoundaries:
    PRESETS = [
        "quality_compounder",
        "value_pick",
        "growth_accelerator",
        "dividend_champion",
        "debt_free_blue_chip",
        "turnaround_watch",
    ]

    @pytest.mark.parametrize("preset_name", PRESETS)
    def test_preset_returns_between_5_and_50(
        self, preset_name: str, engine: ScreenerEngine
    ) -> None:
        result = engine.run_preset(preset_name)
        n = len(result)
        assert 5 <= n <= 50, (
            f"Preset '{preset_name}' returned {n} companies "
            f"(expected 5–50)"
        )

    @pytest.mark.parametrize("preset_name", PRESETS)
    def test_preset_has_composite_score(
        self, preset_name: str, engine: ScreenerEngine
    ) -> None:
        result = engine.run_preset(preset_name)
        assert "composite_quality_score" in result.columns
        assert result["composite_quality_score"].notna().any()

    @pytest.mark.parametrize("preset_name", PRESETS)
    def test_preset_sorted_descending(
        self, preset_name: str, engine: ScreenerEngine
    ) -> None:
        result = engine.run_preset(preset_name)
        scores = result["composite_quality_score"].dropna().tolist()
        assert scores == sorted(scores, reverse=True), (
            f"Preset '{preset_name}' not sorted descending by composite score"
        )

    def test_invalid_preset_raises_key_error(self, engine: ScreenerEngine) -> None:
        with pytest.raises(KeyError):
            engine.run_preset("nonexistent_preset_xyz")


# ─── Test 3: Quality Compounder — business logic verification ─────────────────

class TestQualityCompounderVerification:
    def test_top5_roe_above_15(self, engine: ScreenerEngine) -> None:
        """Top 5 Quality Compounder results must all have ROE > 15%."""
        result = engine.run_preset("quality_compounder")
        top5   = result.head(5)
        for _, row in top5.iterrows():
            roe = row.get("roe", None)
            assert roe is not None and not math.isnan(float(roe)), (
                f"{row['ticker']}: ROE is null"
            )
            assert float(roe) >= 15.0, (
                f"{row['ticker']} ROE={roe:.2f}% is below 15% threshold"
            )

    def test_top5_de_below_1(self, engine: ScreenerEngine) -> None:
        """
        Top 5 Quality Compounder results must have D/E < 1.0
        (financial sector companies are exempt and should not appear in top 5
        unless naturally qualifying).
        """
        result = engine.run_preset("quality_compounder")
        top5   = result.head(5)
        for _, row in top5.iterrows():
            sector = row.get("sector_name", "")
            de     = row.get("debt_to_equity", None)
            if sector in ("Banking & Finance",):
                continue  # financial sector is exempt from D/E filter
            assert de is not None and not math.isnan(float(de)), (
                f"{row['ticker']}: D/E is null"
            )
            assert float(de) <= 1.0, (
                f"{row['ticker']} D/E={de:.2f} exceeds 1.0 threshold"
            )

    def test_quality_compounder_fcf_positive(self, engine: ScreenerEngine) -> None:
        """All Quality Compounder companies must have FCF > 0."""
        result = engine.run_preset("quality_compounder")
        fcf_vals = result["free_cashflow"].fillna(0)
        below_zero = (fcf_vals <= 0).sum()
        assert below_zero == 0, (
            f"{below_zero} companies in Quality Compounder have FCF <= 0"
        )


# ─── Test 4: D/E Filter — Financial Sector Exemption ─────────────────────────

class TestDebtToEquityFilterExemption:
    def test_banking_finance_not_excluded_by_de_filter(
        self, universe: pd.DataFrame, config: dict
    ) -> None:
        """
        Applying a D/E max filter must NOT remove Banking & Finance companies.
        """
        de_filter = [{"metric": "debt_to_equity", "operator": "max", "threshold": 1.0}]
        metric_cfg = config.get("metrics", {})
        result = apply_filters(universe, de_filter, metric_cfg)

        banking_in_universe = universe[universe["sector_name"] == "Banking & Finance"]
        banking_in_result   = result[result["sector_name"] == "Banking & Finance"]

        if not banking_in_universe.empty:
            assert len(banking_in_result) == len(banking_in_universe), (
                "Banking & Finance companies were incorrectly removed by D/E max filter"
            )

    def test_non_financial_de_filter_applied(
        self, universe: pd.DataFrame, config: dict
    ) -> None:
        """Non-financial companies with D/E > threshold must be excluded."""
        threshold  = 1.0
        de_filter  = [{"metric": "debt_to_equity", "operator": "max", "threshold": threshold}]
        metric_cfg = config.get("metrics", {})
        result = apply_filters(universe, de_filter, metric_cfg)

        non_fin = result[~result["sector_name"].isin({"Banking & Finance"})]
        high_de = non_fin[non_fin["debt_to_equity"].fillna(0) > threshold]
        assert high_de.empty, (
            f"{len(high_de)} non-financial companies with D/E > {threshold} "
            f"survived the filter: {high_de['ticker'].tolist()}"
        )


# ─── Test 5: ICR Filter — Debt-Free Infinity Logic ───────────────────────────

class TestICRDebtFreeLogic:
    def test_debt_free_passes_icr_filter(self, universe: pd.DataFrame, config: dict) -> None:
        """Companies with D/E == 0 must always pass any ICR minimum filter."""
        icr_filter = [{"metric": "interest_coverage", "operator": "min", "threshold": 999}]
        metric_cfg = config.get("metrics", {})
        result     = apply_filters(universe, icr_filter, metric_cfg)

        debt_free_universe = universe[universe["debt_to_equity"].fillna(1) == 0]
        debt_free_result   = result[result["debt_to_equity"].fillna(1) == 0]

        if not debt_free_universe.empty:
            assert len(debt_free_result) == len(debt_free_universe), (
                "Debt-free companies were incorrectly removed by ICR filter"
            )

    def test_non_debt_free_icr_filtered_correctly(
        self, universe: pd.DataFrame, config: dict
    ) -> None:
        """Companies with D/E > 0 and low ICR must be excluded."""
        threshold  = 5.0
        icr_filter = [{"metric": "interest_coverage", "operator": "min", "threshold": threshold}]
        metric_cfg = config.get("metrics", {})
        result     = apply_filters(universe, icr_filter, metric_cfg)

        non_debt_free = result[result["debt_to_equity"].fillna(1) != 0]
        low_icr       = non_debt_free[non_debt_free["interest_coverage"].fillna(0) < threshold]
        assert low_icr.empty, (
            f"{len(low_icr)} non-debt-free companies with ICR < {threshold} "
            f"survived the filter"
        )


# ─── Test 6: Custom Filter ────────────────────────────────────────────────────

class TestCustomFilter:
    def test_custom_filter_returns_dataframe(self, engine: ScreenerEngine) -> None:
        result = engine.run_custom([
            {"metric": "roe", "operator": "min", "threshold": 18.0},
        ])
        assert isinstance(result, pd.DataFrame)
        if not result.empty:
            assert (result["roe"].fillna(0) >= 18.0).all()

    def test_custom_roe_de_combo(self, engine: ScreenerEngine) -> None:
        result = engine.run_custom([
            {"metric": "roe",            "operator": "min", "threshold": 15.0},
            {"metric": "debt_to_equity", "operator": "max", "threshold": 0.5},
        ])
        if not result.empty:
            non_fin = result[~result["sector_name"].isin({"Banking & Finance"})]
            assert (non_fin["debt_to_equity"].fillna(0) <= 0.5).all()


# ─── Test 7: Composite Score Computation (unit-level) ─────────────────────────

class TestCompositeScoreUnit:
    def test_composite_score_no_nulls(self, universe: pd.DataFrame) -> None:
        """Composite score must be non-null for all companies in universe."""
        null_count = universe["composite_quality_score"].isna().sum()
        assert null_count == 0, f"{null_count} companies have null composite score"

    def test_composite_score_variation(self, universe: pd.DataFrame) -> None:
        """Scores should not all be identical (requires real differentiation)."""
        std = universe["composite_quality_score"].std()
        assert std > 1.0, f"Composite scores have very low variance (std={std:.2f})"
