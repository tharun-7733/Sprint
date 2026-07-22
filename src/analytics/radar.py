"""
radar.py — Radar / Polar Chart Generator (Sprint 3, Day 19).

For each company in a peer group, generates a radar (spider) chart with:
  - 8 axes: ROE, ROCE, NPM, D/E score, FCF score, PAT CAGR 5yr,
            Revenue CAGR 5yr, Composite Score
  - Company values as a filled polygon
  - Peer group average as a dashed outline overlay
  - Exported as PNG to reports/radar_charts/<company_id>_radar.png

For companies with no peer group, generates a standalone chart using the
Nifty 100 universe average as the reference line.

Font size is >= 10pt for readability at standard viewing size.

Usage
-----
    from src.analytics.radar import RadarChartGenerator
    gen = RadarChartGenerator()
    gen.generate_all()   # generates all company charts
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")          # non-interactive backend — safe for servers
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
from dotenv import load_dotenv

from src.screener.engine import load_screener_dataframe, compute_composite_score
from src.analytics.peer import load_peer_groups

load_dotenv()
logger = logging.getLogger(__name__)

_DB_PATH      = os.getenv("DATABASE_URL", "nifty100.db")
_OUTPUT_DIR   = Path(os.getenv("RADAR_OUTPUT_DIR", "reports/radar_charts"))

# ─── 8 radar axes ─────────────────────────────────────────────────────────────
_AXES: list[tuple[str, str, bool]] = [
    # (column_in_df, display_label, inverted_for_score?)
    ("roe",              "ROE",            False),
    ("roce",             "ROCE",           False),
    ("npm",              "NPM",            False),
    ("debt_to_equity",   "D/E Score",      True),   # inverted: lower = better
    ("free_cashflow",    "FCF Score",      False),
    ("pat_cagr_5yr",     "PAT\nCAGR 5yr", False),
    ("revenue_cagr_5yr", "Rev\nCAGR 5yr", False),
    ("composite_quality_score", "Quality\nScore", False),
]

_N_AXES = len(_AXES)


# ─── Normalisation ────────────────────────────────────────────────────────────

def _norm_series(s: pd.Series, invert: bool = False) -> pd.Series:
    """Min-max normalise to [0, 1], optionally inverting."""
    mn, mx = s.min(), s.max()
    if mx == mn:
        return pd.Series(0.5, index=s.index)
    normed = (s - mn) / (mx - mn)
    return 1 - normed if invert else normed


def _build_normalised(universe_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a DataFrame of normalised [0,1] scores for all 8 axes.
    Missing values are replaced with 0.
    """
    norm_df = pd.DataFrame(index=universe_df.index)
    norm_df["company_id"] = universe_df["company_id"]

    for col, _, invert in _AXES:
        if col not in universe_df.columns:
            norm_df[col] = 0.5
            continue
        s = pd.to_numeric(universe_df[col], errors="coerce")
        s = s.fillna(s.median() if s.notna().any() else 0.0)
        norm_df[col] = _norm_series(s, invert=invert).values

    return norm_df


# ─── Chart Rendering ──────────────────────────────────────────────────────────

def _radar_plot(
    ax: plt.Axes,
    angles: np.ndarray,
    values: np.ndarray,
    color: str,
    alpha: float,
    linestyle: str,
    label: str,
    linewidth: float = 2.0,
) -> None:
    """Draw one radar polygon on a polar axes."""
    vals = np.concatenate([values, [values[0]]])           # close the loop
    angs = np.concatenate([angles, [angles[0]]])
    ax.plot(angs, vals, color=color, linestyle=linestyle, linewidth=linewidth, label=label)
    ax.fill(angs, vals, color=color, alpha=alpha)


def _make_chart(
    company_row: pd.Series,
    norm_row: pd.Series,
    peer_avg_norm: pd.Series,
    peer_label: str,
    output_path: Path,
) -> None:
    """
    Render and save one radar chart PNG.

    Parameters
    ----------
    company_row   : raw metric values for the company (for annotation)
    norm_row      : normalised [0,1] values for the company (8 axes)
    peer_avg_norm : normalised [0,1] peer group average values (8 axes)
    peer_label    : display label for the peer group (or 'Nifty 100 Avg')
    output_path   : where to save the .png file
    """
    angles = np.linspace(0, 2 * np.pi, _N_AXES, endpoint=False)

    company_vals = np.array([float(norm_row.get(col, 0.5)) for col, _, _ in _AXES])
    peer_vals    = np.array([float(peer_avg_norm.get(col, 0.5)) for col, _, _ in _AXES])
    labels       = [label for _, label, _ in _AXES]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw={"projection": "polar"})
    fig.patch.set_facecolor("#0F1117")
    ax.set_facecolor("#1A1D27")

    # Draw polygons
    _radar_plot(ax, angles, company_vals, "#00D4FF", 0.25, "-",  "Company", 2.5)
    _radar_plot(ax, angles, peer_vals,    "#FF6B35", 0.08, "--", peer_label, 1.8)

    # Grid and spines
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["25", "50", "75", "100"], color="#AAAAAA", fontsize=8)
    ax.yaxis.set_tick_params(labelsize=8)
    ax.grid(color="#333344", linewidth=0.8, linestyle="--")
    ax.spines["polar"].set_color("#333344")

    # Axis labels
    ax.set_xticks(angles)
    ax.set_xticklabels(labels, fontsize=10, color="#FFFFFF", fontweight="bold")
    ax.tick_params(axis="x", pad=14)

    # Title
    ticker      = company_row.get("ticker", "")
    cname       = company_row.get("company_name", "")
    score       = company_row.get("composite_quality_score", "–")
    sector      = company_row.get("sector_name", "")
    score_str   = f"{float(score):.1f}" if pd.notna(score) else "–"

    ax.set_title(
        f"{ticker} — {cname}\n{sector}  |  Quality Score: {score_str}",
        pad=26, fontsize=12, color="#FFFFFF", fontweight="bold",
    )

    # Legend
    legend = ax.legend(
        loc="upper right",
        bbox_to_anchor=(1.35, 1.15),
        fontsize=10,
        facecolor="#1A1D27",
        edgecolor="#444444",
        labelcolor="#FFFFFF",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


# ─── Generator Class ──────────────────────────────────────────────────────────

class RadarChartGenerator:
    """
    Generates radar charts for all companies, grouped by peer group.

    Parameters
    ----------
    db_path    : path to nifty100.db
    output_dir : output directory for PNG files
    """

    def __init__(
        self,
        db_path: str = _DB_PATH,
        output_dir: str | Path = _OUTPUT_DIR,
    ) -> None:
        self.db_path    = db_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _load_data(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Load universe, peer groups, and normalised scores."""
        from src.screener.engine import ScreenerEngine
        engine = ScreenerEngine(db_path=self.db_path)
        universe   = engine.full_universe()
        peer_df    = load_peer_groups(self.db_path)
        norm_df    = _build_normalised(universe)
        return universe, peer_df, norm_df

    def generate_for_company(
        self,
        company_id: int,
        universe: pd.DataFrame,
        peer_df: pd.DataFrame,
        norm_df: pd.DataFrame,
        nifty_avg_norm: pd.Series,
    ) -> Optional[Path]:
        """
        Generate radar chart PNG for a single company.

        Returns the output Path, or None on failure.
        """
        co_rows = universe[universe["company_id"] == company_id]
        if co_rows.empty:
            logger.warning("Company %d not found in universe", company_id)
            return None

        co_row  = co_rows.iloc[0]
        co_norm = norm_df[norm_df["company_id"] == company_id]
        if co_norm.empty:
            return None
        norm_vals = co_norm.iloc[0]

        # Check peer group membership
        co_peers = peer_df[peer_df["company_id"] == company_id]

        if co_peers.empty:
            # No peer group: use Nifty 100 average
            peer_label    = "Nifty 100 Avg"
            peer_avg_norm = nifty_avg_norm
        else:
            group_name = co_peers.iloc[0]["peer_group_name"]
            peer_label = group_name

            # Peer group average (normalised)
            peer_ids        = peer_df[peer_df["peer_group_name"] == group_name]["company_id"]
            peer_norm_rows  = norm_df[norm_df["company_id"].isin(peer_ids)]
            axis_cols       = [col for col, _, _ in _AXES]
            existing_cols   = [c for c in axis_cols if c in peer_norm_rows.columns]
            peer_avg_norm   = peer_norm_rows[existing_cols].mean()

        output_path = self.output_dir / f"{company_id}_radar.png"
        try:
            _make_chart(co_row, norm_vals, peer_avg_norm, peer_label, output_path)
        except Exception as exc:
            logger.error("Chart failed for company %d: %s", company_id, exc)
            return None

        return output_path

    def generate_all(self) -> dict[int, Path]:
        """
        Generate radar charts for every company in the universe.

        Returns
        -------
        Dict mapping company_id → output PNG path for successful charts.
        """
        logger.info("Generating radar charts...")
        universe, peer_df, norm_df = self._load_data()

        # Nifty 100 universe average (normalised) — used for unassigned companies
        axis_cols       = [col for col, _, _ in _AXES]
        existing_cols   = [c for c in axis_cols if c in norm_df.columns]
        nifty_avg_norm  = norm_df[existing_cols].mean()

        results: dict[int, Path] = {}
        company_ids = universe["company_id"].unique().tolist()

        for cid in company_ids:
            path = self.generate_for_company(
                int(cid), universe, peer_df, norm_df, nifty_avg_norm
            )
            if path:
                results[int(cid)] = path

        logger.info(
            "Radar charts generated: %d/%d companies → %s",
            len(results), len(company_ids), self.output_dir,
        )
        return results
