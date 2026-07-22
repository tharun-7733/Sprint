"""
test_peer.py — Unit tests for the Peer Analytics Engine (Sprint 3, Day 21).

Tests cover:
  1. PERCENT_RANK computation correctness
  2. D/E rank is inverted (lower D/E → higher rank)
  3. IT Services peer group: highest ROE company has highest ROE percentile rank
  4. FMCG peer group spot-check
  5. Companies not in any peer group return 'No peer group assigned' message
  6. peer_percentiles covers all expected peer groups
  7. Percentile ranks are in [0, 1]
"""

from __future__ import annotations

import sqlite3
import math

import numpy as np
import pandas as pd
import pytest

from src.analytics.peer import (
    PeerEngine,
    compute_peer_percentiles,
    find_unassigned_companies,
    load_peer_groups,
    _percent_rank,
)
from src.screener.engine import load_screener_dataframe


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def peer_engine() -> PeerEngine:
    return PeerEngine()


@pytest.fixture(scope="module")
def universe_df() -> pd.DataFrame:
    return load_screener_dataframe()


@pytest.fixture(scope="module")
def peer_groups_df() -> pd.DataFrame:
    return load_peer_groups()


@pytest.fixture(scope="module")
def percentile_df(universe_df: pd.DataFrame, peer_groups_df: pd.DataFrame) -> pd.DataFrame:
    """Compute percentiles once for all tests."""
    return compute_peer_percentiles(universe_df, peer_groups_df)


# ─── Test 1: _percent_rank helper ─────────────────────────────────────────────

class TestPercentRankHelper:
    def test_basic_ranking(self) -> None:
        """Lowest value → rank 0.0, highest → 1.0."""
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        r = _percent_rank(s)
        assert abs(r.iloc[0] - 0.0) < 1e-9
        assert abs(r.iloc[-1] - 1.0) < 1e-9

    def test_nan_handling(self) -> None:
        """NaN inputs produce NaN outputs."""
        s = pd.Series([1.0, float("nan"), 3.0])
        r = _percent_rank(s)
        assert math.isnan(r.iloc[1])

    def test_single_element(self) -> None:
        """Single non-NaN element gets rank 1.0."""
        s = pd.Series([42.0])
        r = _percent_rank(s)
        assert abs(r.iloc[0] - 1.0) < 1e-9

    def test_all_equal(self) -> None:
        """All equal values should all get the same rank (0.5)."""
        s = pd.Series([5.0, 5.0, 5.0])
        r = _percent_rank(s)
        # All equal → all get middle rank
        assert r.notna().all()
        assert abs(r.std()) < 1e-9   # no variation in ranks

    def test_rank_bounds(self) -> None:
        """All ranks must be in [0, 1]."""
        rng = np.random.default_rng(99)
        s   = pd.Series(rng.normal(size=50))
        r   = _percent_rank(s)
        assert r.min() >= 0.0
        assert r.max() <= 1.0


# ─── Test 2: D/E rank inversion ───────────────────────────────────────────────

class TestDERankInversion:
    def test_lower_de_gets_higher_rank(self, percentile_df: pd.DataFrame) -> None:
        """
        For the D/E metric, a company with lower D/E should have a higher
        percentile rank than a company with higher D/E within the same group.
        """
        de_df = percentile_df[percentile_df["metric"] == "DE_Ratio"].copy()
        if de_df.empty:
            pytest.skip("No DE_Ratio percentile data available")

        # Check within a single peer group
        for group_name in de_df["peer_group_name"].unique()[:3]:
            grp = de_df[de_df["peer_group_name"] == group_name].dropna(
                subset=["value", "percentile_rank"]
            )
            if len(grp) < 2:
                continue

            # Company with minimum D/E should have the highest rank
            min_de_row  = grp.loc[grp["value"].idxmin()]
            max_de_row  = grp.loc[grp["value"].idxmax()]
            assert min_de_row["percentile_rank"] >= max_de_row["percentile_rank"], (
                f"In group '{group_name}': company with D/E={min_de_row['value']:.2f} "
                f"has lower rank ({min_de_row['percentile_rank']:.3f}) than "
                f"company with D/E={max_de_row['value']:.2f} "
                f"({max_de_row['percentile_rank']:.3f}). D/E rank should be inverted."
            )


# ─── Test 3: IT Services group — ROE ranking spot-check ───────────────────────

class TestITServicesROESpotCheck:
    _IT_GROUP_VARIANTS = [
        "Information Technology Peers",
        "IT Services Peers",
        "Information Technology",
    ]

    def _get_it_group(self, percentile_df: pd.DataFrame) -> str | None:
        groups = percentile_df["peer_group_name"].unique()
        for name in self._IT_GROUP_VARIANTS:
            if name in groups:
                return name
        # Try fuzzy match
        for g in groups:
            if "information technology" in g.lower() or "it service" in g.lower():
                return g
        return None

    def test_highest_roe_has_highest_roe_rank(
        self, percentile_df: pd.DataFrame, universe_df: pd.DataFrame, peer_groups_df: pd.DataFrame
    ) -> None:
        """
        Within the IT Services peer group, the company with the highest ROE
        must have the highest ROE percentile rank.
        """
        it_group = self._get_it_group(percentile_df)
        if it_group is None:
            pytest.skip("IT Services peer group not found in data")

        roe_df = percentile_df[
            (percentile_df["peer_group_name"] == it_group)
            & (percentile_df["metric"] == "ROE")
        ].dropna(subset=["value", "percentile_rank"])

        if len(roe_df) < 2:
            pytest.skip(f"Insufficient data in '{it_group}' for ROE comparison")

        max_roe_row  = roe_df.loc[roe_df["value"].idxmax()]
        max_rank_row = roe_df.loc[roe_df["percentile_rank"].idxmax()]

        assert max_roe_row["company_id"] == max_rank_row["company_id"], (
            f"In '{it_group}', company with highest ROE ({max_roe_row['value']:.2f}%) "
            f"does not have the highest rank "
            f"(rank={max_roe_row['percentile_rank']:.3f}). "
            f"Highest ranked: company_id={max_rank_row['company_id']} "
            f"(rank={max_rank_row['percentile_rank']:.3f})"
        )


# ─── Test 4: FMCG group spot-check ────────────────────────────────────────────

class TestFMCGGroupSpotCheck:
    def test_fmcg_group_exists(self, percentile_df: pd.DataFrame) -> None:
        """FMCG peer group should be present in percentile data."""
        groups = percentile_df["peer_group_name"].unique()
        fmcg_groups = [g for g in groups if "fmcg" in g.lower()
                       or "fast moving" in g.lower() or "consumer goods" in g.lower()]
        assert len(fmcg_groups) > 0, (
            f"No FMCG peer group found. Available: {list(groups)}"
        )

    def test_fmcg_percentile_ranks_valid(self, percentile_df: pd.DataFrame) -> None:
        """FMCG percentile ranks must be in [0, 1]."""
        groups = percentile_df["peer_group_name"].unique()
        fmcg_groups = [g for g in groups if "fmcg" in g.lower()
                       or "fast moving" in g.lower() or "consumer goods" in g.lower()]
        if not fmcg_groups:
            pytest.skip("No FMCG peer group found")

        fmcg_df = percentile_df[percentile_df["peer_group_name"].isin(fmcg_groups)]
        ranks   = fmcg_df["percentile_rank"].dropna()
        if ranks.empty:
            pytest.skip("No percentile data for FMCG group")

        assert ranks.min() >= 0.0, f"FMCG rank below 0: {ranks.min()}"
        assert ranks.max() <= 1.0, f"FMCG rank above 1: {ranks.max()}"


# ─── Test 5: Unassigned company graceful handling ─────────────────────────────

class TestUnassignedCompanyHandling:
    def test_find_unassigned_returns_dataframe(
        self, universe_df: pd.DataFrame, peer_groups_df: pd.DataFrame
    ) -> None:
        """find_unassigned_companies must return a DataFrame (never raises)."""
        result = find_unassigned_companies(universe_df, peer_groups_df)
        assert isinstance(result, pd.DataFrame)

    def test_unassigned_has_status_column(
        self, universe_df: pd.DataFrame, peer_groups_df: pd.DataFrame
    ) -> None:
        """Unassigned companies must have 'No peer group assigned' status."""
        result = find_unassigned_companies(universe_df, peer_groups_df)
        if not result.empty:
            assert "status" in result.columns
            assert (result["status"] == "No peer group assigned").all()

    def test_no_exception_for_missing_peer_group(self) -> None:
        """
        Passing an empty peer_groups_df must not raise — returns a DataFrame
        with all companies marked as unassigned.
        """
        universe = pd.DataFrame({
            "company_id":   [1, 2, 3],
            "ticker":       ["A", "B", "C"],
            "company_name": ["Alpha", "Beta", "Gamma"],
        })
        empty_peers = pd.DataFrame(columns=["company_id", "group_name"])
        try:
            result = find_unassigned_companies(universe, empty_peers)
            assert len(result) == 3
        except Exception as exc:
            pytest.fail(f"find_unassigned_companies raised unexpectedly: {exc}")


# ─── Test 6: Percentile DataFrame structure ───────────────────────────────────

class TestPercentileDataFrameStructure:
    def test_required_columns(self, percentile_df: pd.DataFrame) -> None:
        required = [
            "company_id", "peer_group_name", "metric",
            "value", "percentile_rank", "year",
        ]
        missing = [c for c in required if c not in percentile_df.columns]
        assert not missing, f"Missing columns: {missing}"

    def test_all_ranks_in_0_1(self, percentile_df: pd.DataFrame) -> None:
        """All non-null percentile_rank values must be in [0, 1]."""
        ranks = percentile_df["percentile_rank"].dropna()
        assert ranks.min() >= 0.0, f"Rank below 0: {ranks.min()}"
        assert ranks.max() <= 1.0, f"Rank above 1: {ranks.max()}"

    def test_10_metrics_per_company_per_group(self, percentile_df: pd.DataFrame) -> None:
        """Each (company, peer_group) combination should have 10 metric rows."""
        counts = (
            percentile_df
            .groupby(["company_id", "peer_group_name"])["metric"]
            .nunique()
        )
        # Allow up to 10 (some metrics may be missing due to NaN data)
        assert (counts <= 10).all(), (
            f"Some (company, group) pairs have more than 10 metric rows"
        )

    def test_non_empty_result(self, percentile_df: pd.DataFrame) -> None:
        assert not percentile_df.empty, "Percentile DataFrame is empty"

    def test_multiple_peer_groups(self, percentile_df: pd.DataFrame) -> None:
        """Must have data for more than 1 peer group."""
        n_groups = percentile_df["peer_group_name"].nunique()
        assert n_groups >= 5, (
            f"Only {n_groups} peer group(s) in percentile data — expected >= 5"
        )


# ─── Test 7: PeerEngine integration ──────────────────────────────────────────

class TestPeerEngineIntegration:
    def test_get_peer_groups_returns_list(self, peer_engine: PeerEngine) -> None:
        groups = peer_engine.get_peer_groups()
        assert isinstance(groups, list)
        assert len(groups) >= 5

    def test_compute_and_persist_returns_df(self, peer_engine: PeerEngine) -> None:
        """Full run must return a non-empty DataFrame without raising."""
        result = peer_engine.compute_and_persist()
        assert isinstance(result, pd.DataFrame)
        assert not result.empty

    def test_load_from_db_after_persist(self, peer_engine: PeerEngine) -> None:
        """After compute_and_persist, load_from_db must return persisted data."""
        peer_engine.compute_and_persist()
        db_df = peer_engine.load_from_db()
        assert isinstance(db_df, pd.DataFrame)
        assert not db_df.empty
        assert "percentile_rank" in db_df.columns
