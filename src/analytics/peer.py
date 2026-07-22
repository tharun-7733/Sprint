"""
peer.py — Peer Percentile Rankings Engine (Sprint 3, Day 18).

Responsibilities
----------------
1. Load peer groups from the peer_groups table in nifty100.db.
2. For each peer group, compute PERCENT_RANK for 10 metrics:
     ROE, ROCE, Net Profit Margin, D/E (inverted), FCF,
     PAT CAGR 5yr, Revenue CAGR 5yr, EPS CAGR 5yr,
     Interest Coverage, Asset Turnover
3. Persist results to the peer_percentiles SQLite table
   (upsert on UNIQUE constraint).
4. For companies not in any peer group: return a descriptive
   message — no exception raised.

D/E ranking is inverted: lower D/E → higher percentile rank.
Debt-free companies (D/E == 0) are ranked at the top.

Usage
-----
    from src.analytics.peer import PeerEngine
    engine = PeerEngine()
    result = engine.compute_and_persist()   # runs + writes to DB
    print(result.head())
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Pull CAGR helpers from screener engine
from src.screener.engine import load_screener_dataframe

load_dotenv()
logger = logging.getLogger(__name__)

_DB_PATH = os.getenv("DATABASE_URL", "nifty100.db")

# ─── 10 ranking metrics and their config ──────────────────────────────────────
#   (metric_key, column_in_screener_df, inverted)
_RANK_METRICS: list[tuple[str, str, bool]] = [
    ("ROE",              "roe",              False),
    ("ROCE",             "roce",             False),
    ("Net_Profit_Margin","npm",              False),
    ("DE_Ratio",         "debt_to_equity",   True),   # lower is better
    ("FCF",              "free_cashflow",    False),
    ("PAT_CAGR_5yr",     "pat_cagr_5yr",     False),
    ("Revenue_CAGR_5yr", "revenue_cagr_5yr", False),
    ("EPS_CAGR_5yr",     "eps_cagr_5yr",     False),
    ("Interest_Coverage","interest_coverage",False),
    ("Asset_Turnover",   "asset_turnover",   False),
]


def _percent_rank(series: pd.Series) -> pd.Series:
    """
    Compute PERCENT_RANK for a pandas Series (ignoring NaN).

    PERCENT_RANK(x) = (rank - 1) / (N - 1)
    Returns values in [0, 1].  NaN inputs get NaN output.
    """
    ranked = series.rank(method="average", na_option="keep")
    n      = series.notna().sum()
    if n <= 1:
        return pd.Series(1.0 if n == 1 else float("nan"), index=series.index)
    return (ranked - 1) / (n - 1)


# ─── Data Loading ─────────────────────────────────────────────────────────────

def load_peer_groups(db_path: str = _DB_PATH) -> pd.DataFrame:
    """
    Load the peer_groups table joined with company metadata.

    Returns a DataFrame with columns:
        peer_group_name, company_id, ticker, company_name, sector_name
    """
    sql = """
    SELECT
        pg.group_name,
        pg.company_id,
        c.ticker,
        c.company_name,
        s.sector_name
    FROM peer_groups pg
    JOIN companies c ON c.company_id = pg.company_id
    LEFT JOIN sectors s ON s.sector_id = c.sector_id
    ORDER BY pg.group_name, c.ticker
    """
    conn = sqlite3.connect(db_path)
    df   = pd.read_sql_query(sql, conn)
    conn.close()
    # Normalise column name for downstream use
    df = df.rename(columns={"group_name": "peer_group_name"})
    return df


def _ensure_peer_percentiles_table(conn: sqlite3.Connection) -> None:
    """Create peer_percentiles table if it doesn't exist (idempotent)."""
    conn.executescript("""
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS peer_percentiles (
        pp_id            INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id       INTEGER NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
        peer_group_name  TEXT    NOT NULL,
        metric           TEXT    NOT NULL,
        value            REAL,
        percentile_rank  REAL,
        year             INTEGER NOT NULL,
        created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (company_id, peer_group_name, metric, year)
    );
    CREATE INDEX IF NOT EXISTS idx_pp_company   ON peer_percentiles (company_id);
    CREATE INDEX IF NOT EXISTS idx_pp_group     ON peer_percentiles (peer_group_name);
    CREATE INDEX IF NOT EXISTS idx_pp_metric    ON peer_percentiles (metric);
    CREATE INDEX IF NOT EXISTS idx_pp_group_met ON peer_percentiles (peer_group_name, metric);
    """)
    conn.commit()


# ─── Core Computation ─────────────────────────────────────────────────────────

def compute_peer_percentiles(
    universe_df: pd.DataFrame,
    peer_groups_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute PERCENT_RANK for each company within its peer group(s).

    Parameters
    ----------
    universe_df   : full scored screener DataFrame (from ScreenerEngine or
                    load_screener_dataframe())
    peer_groups_df: peer group assignments (from load_peer_groups())

    Returns
    -------
    DataFrame with columns:
        company_id, company_name, ticker, peer_group_name,
        metric, value, percentile_rank, year
    """
    # Merge peer assignment into universe
    merged = peer_groups_df.merge(
        universe_df[["company_id", "year"] + [col for _, col, _ in _RANK_METRICS if col in universe_df.columns]],
        on="company_id",
        how="left",
    )

    records: list[dict] = []

    for group_name, grp in merged.groupby("peer_group_name"):
        grp = grp.copy()

        for metric_key, col, inverted in _RANK_METRICS:
            if col not in grp.columns:
                logger.warning("Column '%s' missing — skipping metric '%s'", col, metric_key)
                continue

            series = grp[col].copy()

            # Invert for D/E: negate so that lower D/E ranks higher
            if inverted:
                series = -series

            prank = _percent_rank(series)

            for i, row in grp.iterrows():
                value = row.get(col, None)
                pr    = prank.get(i, float("nan"))

                records.append({
                    "company_id":      int(row["company_id"]),
                    "company_name":    row.get("company_name", ""),
                    "ticker":          row.get("ticker", ""),
                    "peer_group_name": group_name,
                    "metric":          metric_key,
                    "value":           float(value) if pd.notna(value) else None,
                    "percentile_rank": round(float(pr), 4) if pd.notna(pr) else None,
                    "year":            int(row.get("year", 0)) if pd.notna(row.get("year")) else 0,
                })

    result_df = pd.DataFrame(records)
    logger.info(
        "Computed %d percentile records across %d peer groups",
        len(result_df),
        result_df["peer_group_name"].nunique() if not result_df.empty else 0,
    )
    return result_df


def find_unassigned_companies(
    universe_df: pd.DataFrame,
    peer_groups_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Return companies that are not in any peer group.

    Returns a DataFrame with company_id, ticker, company_name and a
    'status' column containing 'No peer group assigned'.
    No exception is raised.
    """
    assigned_ids   = set(peer_groups_df["company_id"].unique())
    all_ids        = set(universe_df["company_id"].unique())
    unassigned_ids = all_ids - assigned_ids

    if not unassigned_ids:
        return pd.DataFrame(columns=["company_id", "ticker", "company_name", "status"])

    mask = universe_df["company_id"].isin(unassigned_ids)
    unassigned = universe_df.loc[mask, ["company_id", "ticker", "company_name"]].copy()
    unassigned["status"] = "No peer group assigned"
    return unassigned.reset_index(drop=True)


# ─── Persistence ──────────────────────────────────────────────────────────────

def persist_peer_percentiles(
    conn: sqlite3.Connection,
    percentile_df: pd.DataFrame,
) -> int:
    """
    Upsert peer percentile records into the peer_percentiles table.

    Returns the number of rows upserted.
    """
    _ensure_peer_percentiles_table(conn)

    if percentile_df.empty:
        return 0

    rows_upserted = 0
    upsert_sql = """
    INSERT INTO peer_percentiles
        (company_id, peer_group_name, metric, value, percentile_rank, year)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT (company_id, peer_group_name, metric, year)
    DO UPDATE SET
        value           = excluded.value,
        percentile_rank = excluded.percentile_rank,
        created_at      = datetime('now')
    """
    for _, row in percentile_df.iterrows():
        conn.execute(upsert_sql, (
            row["company_id"],
            row["peer_group_name"],
            row["metric"],
            row["value"],
            row["percentile_rank"],
            row["year"],
        ))
        rows_upserted += 1

    conn.commit()
    logger.info("Upserted %d peer_percentiles rows", rows_upserted)
    return rows_upserted


# ─── Top-level Engine Class ───────────────────────────────────────────────────

class PeerEngine:
    """
    Orchestrates peer group data loading, percentile computation, and DB write.

    Parameters
    ----------
    db_path : path to nifty100.db
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self.db_path = db_path

    def compute_and_persist(self) -> pd.DataFrame:
        """
        Full run: load data → compute percentiles → persist → return result DF.

        Also logs companies with no peer group assignment (no exception raised).
        """
        universe_df  = load_screener_dataframe(self.db_path)
        peer_df      = load_peer_groups(self.db_path)

        # Log unassigned companies (graceful — no exception)
        unassigned = find_unassigned_companies(universe_df, peer_df)
        if not unassigned.empty:
            for _, row in unassigned.iterrows():
                logger.info(
                    "Company %s (%s): No peer group assigned",
                    row["ticker"], row["company_name"],
                )

        percentile_df = compute_peer_percentiles(universe_df, peer_df)

        conn = sqlite3.connect(self.db_path)
        try:
            persist_peer_percentiles(conn, percentile_df)
        finally:
            conn.close()

        return percentile_df

    def load_from_db(self, group_name: Optional[str] = None) -> pd.DataFrame:
        """
        Read already-persisted peer_percentiles records from the DB.

        Parameters
        ----------
        group_name : if provided, filter to a single peer group.
        """
        sql = "SELECT * FROM peer_percentiles"
        params: list = []
        if group_name:
            sql    += " WHERE peer_group_name = ?"
            params  = [group_name]
        sql += " ORDER BY peer_group_name, metric, percentile_rank DESC"

        conn = sqlite3.connect(self.db_path)
        df   = pd.read_sql_query(sql, conn, params=params)
        conn.close()
        return df

    def get_peer_groups(self) -> list[str]:
        """Return a sorted list of all peer group names present in the DB."""
        conn = sqlite3.connect(self.db_path)
        cur  = conn.execute("SELECT DISTINCT group_name FROM peer_groups ORDER BY group_name")
        groups = [row[0] for row in cur.fetchall()]
        conn.close()
        return groups
