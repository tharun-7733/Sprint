"""
run_sprint3.py — Sprint 3 orchestration script.

Runs the full Sprint 3 pipeline in order:
  1. Apply db/schema_sprint3.sql (create peer_percentiles table)
  2. Run ScreenerEngine — compute composite scores for all 92 companies
  3. Run all 6 preset screeners — verify each returns 5–50 companies
  4. Export output/screener_output.xlsx (6 colour-coded sheets)
  5. Run PeerEngine — compute PERCENT_RANK for 10 metrics × all peer groups
  6. Persist peer_percentiles to SQLite
  7. Export output/peer_comparison.xlsx (one sheet per peer group)
  8. Generate reports/radar_charts/*.png for all companies

Usage
-----
    python scripts/run_sprint3.py          # full run
    python scripts/run_sprint3.py --step screener   # screener only
    python scripts/run_sprint3.py --step peer       # peer only
    python scripts/run_sprint3.py --step radar      # radar only
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

# ── Ensure project root is on sys.path ────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "sprint3.log", mode="a"),
    ],
)
logger = logging.getLogger("sprint3")

_DB_PATH     = os.getenv("DATABASE_URL", "nifty100.db")
_OUTPUT_DIR  = Path(os.getenv("OUTPUT_DIR", "output"))
_RADAR_DIR   = Path("reports/radar_charts")
_SCHEMA_S3   = ROOT / "db" / "schema_sprint3.sql"


# ─── Step 0: Apply Sprint 3 DDL ───────────────────────────────────────────────

def step_schema() -> None:
    logger.info("── Step 0: Applying Sprint 3 schema...")
    ddl = _SCHEMA_S3.read_text()
    conn = sqlite3.connect(_DB_PATH)
    conn.executescript(ddl)
    conn.commit()
    conn.close()
    logger.info("   ✅ peer_percentiles table created (idempotent)")


# ─── Step 1: Screener ─────────────────────────────────────────────────────────

def step_screener() -> dict:
    from src.screener.engine import ScreenerEngine
    from src.screener.exporter import export_screener_output

    logger.info("── Step 1: Running screener engine...")
    t0 = time.perf_counter()

    engine  = ScreenerEngine(db_path=_DB_PATH)
    results = engine.run_all_presets()

    # ── Preset summary ────────────────────────────────────────────────────────
    print("\n  📊 Preset Results:")
    print(f"  {'Preset':<26} {'Companies':>10}  Status")
    print(f"  {'─'*26} {'─'*10}  {'─'*20}")

    exit_ok = True
    for preset_name, df in results.items():
        n      = len(df)
        ok     = 5 <= n <= 50
        status = "✅ PASS" if ok else f"⚠️  OUT OF RANGE ({n})"
        label  = engine.config["presets"][preset_name]["label"]
        print(f"  {label:<26} {n:>10}  {status}")
        if not ok:
            exit_ok = False

    elapsed = time.perf_counter() - t0
    logger.info("   Screener computed in %.1fs", elapsed)

    # ── Export screener_output.xlsx ───────────────────────────────────────────
    logger.info("── Exporting screener_output.xlsx...")
    out_path = export_screener_output(
        results,
        engine.config,
        output_path=_OUTPUT_DIR / "screener_output.xlsx",
    )
    print(f"\n  📁 screener_output.xlsx → {out_path}")

    if not exit_ok:
        logger.warning("One or more presets returned outside 5–50 range")

    return results


# ─── Step 2: Peer Percentiles ─────────────────────────────────────────────────

def step_peer(universe_df=None) -> "pd.DataFrame":
    import pandas as pd
    from src.analytics.peer import PeerEngine
    from src.analytics.peer_exporter import export_peer_comparison
    from src.screener.engine import ScreenerEngine

    logger.info("── Step 2: Computing peer percentile rankings...")
    t0 = time.perf_counter()

    peer_engine = PeerEngine(db_path=_DB_PATH)
    pct_df      = peer_engine.compute_and_persist()

    n_groups  = pct_df["peer_group_name"].nunique()
    n_records = len(pct_df)
    elapsed   = time.perf_counter() - t0

    print(f"\n  📊 Peer Percentiles:")
    print(f"     Peer groups : {n_groups}")
    print(f"     Records     : {n_records:,}")
    print(f"     Elapsed     : {elapsed:.1f}s")

    # ── Export peer_comparison.xlsx ───────────────────────────────────────────
    logger.info("── Exporting peer_comparison.xlsx...")
    screener_engine = ScreenerEngine(db_path=_DB_PATH)
    univ            = screener_engine.full_universe()

    out_path = export_peer_comparison(
        pct_df,
        univ,
        output_path=_OUTPUT_DIR / "peer_comparison.xlsx",
    )
    print(f"\n  📁 peer_comparison.xlsx → {out_path}  ({n_groups} sheets)")

    return pct_df


# ─── Step 3: Radar Charts ─────────────────────────────────────────────────────

def step_radar() -> None:
    from src.analytics.radar import RadarChartGenerator

    logger.info("── Step 3: Generating radar charts...")
    t0  = time.perf_counter()
    gen = RadarChartGenerator(db_path=_DB_PATH, output_dir=_RADAR_DIR)
    results = gen.generate_all()

    elapsed = time.perf_counter() - t0
    print(f"\n  📊 Radar Charts:")
    print(f"     Charts generated : {len(results)}")
    print(f"     Output directory : {_RADAR_DIR}")
    print(f"     Elapsed          : {elapsed:.1f}s")


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint 3 pipeline orchestrator")
    parser.add_argument(
        "--step",
        choices=["schema", "screener", "peer", "radar", "all"],
        default="all",
        help="Which step to run (default: all)",
    )
    args = parser.parse_args()

    print("\n" + "═" * 60)
    print("  🚀  Sprint 3 — Screener + Peer Engine Pipeline")
    print("═" * 60)

    step = args.step

    if step in ("schema", "all"):
        step_schema()

    if step in ("screener", "all"):
        step_screener()

    if step in ("peer", "all"):
        step_peer()

    if step in ("radar", "all"):
        step_radar()

    print("\n" + "═" * 60)
    print("  ✅  Sprint 3 pipeline complete!")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
