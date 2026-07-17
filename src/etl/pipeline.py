"""
pipeline.py – Full ETL pipeline orchestrator for Sprint 1 – Data Foundation.

Execution order (dependency-safe)
----------------------------------
1.  sectors          (no FK dependencies)
2.  companies        (FK → sectors)
3.  profitandloss    (FK → companies)
4.  balancesheet     (FK → companies)
5.  cashflow         (FK → companies)
6.  analysis         (FK → companies)
7.  documents        (FK → companies)
8.  prosandcons      (FK → companies)
9.  stock_prices     (FK → companies)
10. financial_ratios (FK → companies)
11. peer_groups      (FK → sectors + companies)
12. company_overview (UPDATE → companies)

Outputs
-------
- nifty100.db         — populated SQLite database
- output/load_audit.csv — per-table load statistics

Usage
-----
    python -m src.etl.pipeline          # full run
    # or via Makefile:
    make load
"""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/pipeline.log", mode="a"),
    ],
)
logger = logging.getLogger("pipeline")

# ── Paths ─────────────────────────────────────────────────────────────────────
_DB_PATH    = os.getenv("DATABASE_URL", "nifty100.db")
_DATA_DIR   = Path(os.getenv("DATA_DIR", "data/raw"))
_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
_SCHEMA_SQL = Path("db/schema.sql")

# ── Imports ───────────────────────────────────────────────────────────────────
from src.etl.loader import (
    ExcelLoader,
    LoadResult,
    _get_connection,
    load_analysis,
    load_balance_sheet,
    load_cashflow,
    load_companies,
    load_company_overview,
    load_documents,
    load_financial_ratios,
    load_peer_groups,
    load_profit_and_loss,
    load_pros_cons,
    load_sectors,
    load_stock_prices,
)
from src.etl.validator import run_all_checks, write_failures_csv


# ─── Schema Initialisation ────────────────────────────────────────────────────

def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables from db/schema.sql if they don't already exist."""
    if not _SCHEMA_SQL.exists():
        raise FileNotFoundError(f"Schema file not found: {_SCHEMA_SQL}")
    sql = _SCHEMA_SQL.read_text()
    conn.executescript(sql)
    conn.commit()
    logger.info("Schema initialised from %s", _SCHEMA_SQL)


# ─── Audit Log ────────────────────────────────────────────────────────────────

def write_audit_csv(results: list[LoadResult], output_dir: Path = _OUTPUT_DIR) -> Path:
    """Write load audit to output/load_audit.csv."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "load_audit.csv"

    fieldnames = [
        "table_name", "rows_inserted", "rows_rejected",
        "rejection_reason", "load_timestamp",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            # Write one summary row per table
            reasons = "; ".join({rej.reason for rej in r.rejections}) if r.rejections else ""
            writer.writerow({
                "table_name":       r.table,
                "rows_inserted":    r.rows_inserted,
                "rows_rejected":    r.rows_rejected,
                "rejection_reason": reasons,
                "load_timestamp":   r.load_timestamp,
            })
            # Write individual rejection rows
            for rej in r.rejections:
                writer.writerow({
                    "table_name":       f"{r.table}:row_{rej.row_index}",
                    "rows_inserted":    0,
                    "rows_rejected":    1,
                    "rejection_reason": rej.reason,
                    "load_timestamp":   r.load_timestamp,
                })

    logger.info("Audit written to %s", out_path)
    return out_path


# ─── Ratio Computation ────────────────────────────────────────────────────────

def compute_ratios(db_path: str = _DB_PATH) -> None:
    """
    Recompute financial_ratios from raw P&L and Balance Sheet data.
    Called by `make ratios`.
    """
    conn = _get_connection(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO financial_ratios
            (company_id, year, gross_margin, operating_margin, net_margin,
             roe, debt_to_equity)
        SELECT
            p.company_id, p.year,
            ROUND((p.gross_profit / p.revenue) * 100, 2) AS gross_margin,
            ROUND((p.ebit        / p.revenue) * 100, 2) AS operating_margin,
            ROUND((p.net_profit  / p.revenue) * 100, 2) AS net_margin,
            ROUND(p.net_profit / NULLIF(b.equity, 0) * 100, 2) AS roe,
            ROUND(COALESCE(b.long_term_debt, 0) / NULLIF(b.equity, 0), 2) AS debt_to_equity
        FROM profitandloss p
        LEFT JOIN balancesheet b
               ON b.company_id = p.company_id AND b.year = p.year
        WHERE p.revenue > 0;
        """
    )
    conn.commit()
    conn.close()
    logger.info("Financial ratios recomputed successfully.")


# ─── Pipeline ─────────────────────────────────────────────────────────────────

def run_pipeline(
    db_path: str = _DB_PATH,
    data_dir: Path = _DATA_DIR,
    run_dq: bool = True,
) -> dict:
    """
    Execute the full ETL pipeline.

    Parameters
    ----------
    db_path  : path to the SQLite database
    data_dir : directory containing Excel source files
    run_dq   : whether to run DQ validation after load

    Returns
    -------
    dict with keys: results, failures, audit_path, dq_path
    """
    start_time = datetime.utcnow()
    logger.info("=" * 60)
    logger.info("  Sprint 1 ETL Pipeline starting — %s", start_time.isoformat())
    logger.info("  Database : %s", db_path)
    logger.info("  Data dir : %s", data_dir)
    logger.info("=" * 60)

    # Ensure logs directory exists
    Path("logs").mkdir(exist_ok=True)

    conn   = _get_connection(db_path)
    excel  = ExcelLoader(data_dir=data_dir)
    results: list[LoadResult] = []

    # 1. Init schema
    init_schema(conn)

    # 2. Load tables in dependency order
    load_steps = [
        ("sectors",          lambda: load_sectors(conn, excel)),
        ("companies",        lambda: load_companies(conn, excel)),
        ("profitandloss",    lambda: load_profit_and_loss(conn, excel)),
        ("balancesheet",     lambda: load_balance_sheet(conn, excel)),
        ("cashflow",         lambda: load_cashflow(conn, excel)),
        ("analysis",         lambda: load_analysis(conn, excel)),
        ("documents",        lambda: load_documents(conn, excel)),
        ("prosandcons",      lambda: load_pros_cons(conn, excel)),
        ("stock_prices",     lambda: load_stock_prices(conn, excel)),
        ("financial_ratios", lambda: load_financial_ratios(conn, excel)),
        ("peer_groups",      lambda: load_peer_groups(conn, excel)),
        ("company_overview", lambda: load_company_overview(conn, excel)),
    ]

    for step_name, step_fn in load_steps:
        logger.info("── Loading: %s ─────────────────────────────────", step_name)
        try:
            result = step_fn()
            results.append(result)
            status = "✅" if result.rows_rejected == 0 else "⚠️ "
            logger.info(
                "%s %s: inserted=%d, rejected=%d",
                status, step_name, result.rows_inserted, result.rows_rejected,
            )
        except Exception as exc:
            logger.error("❌  FAILED %s: %s", step_name, exc, exc_info=True)
            # Create a placeholder result so audit is complete
            results.append(LoadResult(
                table=step_name,
                rows_inserted=0,
                rows_rejected=-1,
                rejections=[],
            ))

    conn.close()

    # 3. Write audit
    audit_path = write_audit_csv(results)

    # 4. Verify FK constraints
    conn2 = _get_connection(db_path)
    fk_violations = conn2.execute("PRAGMA foreign_key_check;").fetchall()
    conn2.close()

    if fk_violations:
        logger.error("❌  FK violations detected: %d", len(fk_violations))
    else:
        logger.info("✅  PRAGMA foreign_key_check: 0 violations")

    # 5. Summary stats
    conn3 = _get_connection(db_path)
    company_count = conn3.execute("SELECT COUNT(*) FROM companies;").fetchone()[0]
    conn3.close()
    logger.info("SELECT COUNT(*) FROM companies → %d", company_count)

    # 6. Run DQ validation
    dq_path = None
    failures = []
    if run_dq:
        logger.info("── Running DQ Validation ────────────────────────────────")
        failures = run_all_checks(db_path)
        dq_path  = write_failures_csv(failures)
        critical = [f for f in failures if f.severity == "CRITICAL"]
        logger.info(
            "DQ: %d total (%d CRITICAL, %d WARNING)",
            len(failures),
            len(critical),
            len(failures) - len(critical),
        )

    # 7. Final report
    elapsed = (datetime.utcnow() - start_time).total_seconds()
    total_inserted = sum(r.rows_inserted for r in results)
    total_rejected = sum(r.rows_rejected for r in results if r.rows_rejected > 0)

    logger.info("=" * 60)
    logger.info("  Pipeline complete in %.1f seconds", elapsed)
    logger.info("  Total rows inserted : %d", total_inserted)
    logger.info("  Total rows rejected : %d", total_rejected)
    logger.info("  Companies loaded    : %d", company_count)
    logger.info("  FK violations       : %d", len(fk_violations))
    logger.info("  Audit CSV           : %s", audit_path)
    if dq_path:
        logger.info("  DQ report           : %s", dq_path)
    logger.info("=" * 60)

    return {
        "results":    results,
        "failures":   failures,
        "audit_path": audit_path,
        "dq_path":    dq_path,
        "company_count": company_count,
        "fk_violations": len(fk_violations),
    }


# ─── CLI Entry ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = run_pipeline()
    critical = [f for f in result["failures"] if f.severity == "CRITICAL"]
    sys.exit(1 if critical else 0)
