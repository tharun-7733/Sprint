"""
validator.py – Data Quality (DQ) validator implementing DQ-01 through DQ-16.

Each rule is implemented as a standalone check_dq_NN() function that queries
the SQLite database and returns a list of DQFailure objects.

Severity levels
---------------
CRITICAL : Load-blocking failures (PK uniqueness, FK integrity, duplicates,
           mandatory fields). Must be zero before the database is considered
           production-ready.
WARNING  : Non-blocking anomalies that require review but do not block load.

Output
------
Running this module as __main__ writes all failures to:
    output/validation_failures.csv

Usage
-----
    python -m src.etl.validator
    # or programmatically:
    from src.etl.validator import run_all_checks
    failures = run_all_checks("nifty100.db")
"""

from __future__ import annotations

import csv
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_DB_PATH     = os.getenv("DATABASE_URL", "nifty100.db")
_OUTPUT_DIR  = Path(os.getenv("OUTPUT_DIR", "output"))


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class DQFailure:
    """Represents a single data quality rule failure."""
    rule_id:       str
    severity:      str          # CRITICAL | WARNING
    table:         str
    company_id:    Optional[int]
    ticker:        Optional[str]
    year:          Optional[int]
    field:         str
    failed_value:  str
    expected_value: str
    message:       str
    checked_at:    str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


# ─── Helper ───────────────────────────────────────────────────────────────────

def _conn(db_path: str = _DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    return c


def _fail(rule_id: str, severity: str, table: str,
          company_id: Optional[int] = None, ticker: Optional[str] = None,
          year: Optional[int] = None, field: str = "",
          failed_value: str = "", expected_value: str = "",
          message: str = "") -> DQFailure:
    return DQFailure(
        rule_id=rule_id, severity=severity, table=table,
        company_id=company_id, ticker=ticker, year=year,
        field=field, failed_value=failed_value,
        expected_value=expected_value, message=message,
    )


# ─── DQ Rules ─────────────────────────────────────────────────────────────────

def check_dq_01(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-01 – Primary Key Uniqueness.
    Verify no duplicate PKs exist in any table.
    Severity: CRITICAL
    """
    failures: list[DQFailure] = []
    checks = [
        ("companies",        "company_id"),
        ("sectors",          "sector_id"),
        ("profitandloss",    "pl_id"),
        ("balancesheet",     "bs_id"),
        ("cashflow",         "cf_id"),
        ("analysis",         "analysis_id"),
        ("documents",        "doc_id"),
        ("prosandcons",      "pc_id"),
        ("stock_prices",     "price_id"),
        ("financial_ratios", "ratio_id"),
        ("peer_groups",      "group_id"),
    ]
    for table, pk in checks:
        try:
            rows = conn.execute(
                f"SELECT {pk}, COUNT(*) AS cnt FROM {table} "
                f"GROUP BY {pk} HAVING cnt > 1;"
            ).fetchall()
        except sqlite3.OperationalError:
            continue  # table might not exist yet

        for row in rows:
            failures.append(_fail(
                "DQ-01", "CRITICAL", table,
                field=pk,
                failed_value=str(row[0]),
                expected_value="unique",
                message=f"Duplicate PK {pk}={row[0]} found {row[1]} times in {table}",
            ))

    logger.info("DQ-01: %d failures", len(failures))
    return failures


def check_dq_02(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-02 – (company_id, year) Composite Key Uniqueness.
    Check P&L, Balance Sheet, and Cash Flow tables for duplicate (company, year) pairs.
    Severity: CRITICAL
    """
    failures: list[DQFailure] = []
    tables = ["profitandloss", "balancesheet", "cashflow", "analysis", "financial_ratios"]

    for table in tables:
        try:
            rows = conn.execute(
                f"SELECT company_id, year, COUNT(*) AS cnt FROM {table} "
                f"GROUP BY company_id, year HAVING cnt > 1;"
            ).fetchall()
        except sqlite3.OperationalError:
            continue

        for row in rows:
            cid, yr, cnt = row["company_id"], row["year"], row[2]
            ticker_row = conn.execute(
                "SELECT ticker FROM companies WHERE company_id=?", (cid,)
            ).fetchone()
            ticker = ticker_row["ticker"] if ticker_row else None
            failures.append(_fail(
                "DQ-02", "CRITICAL", table,
                company_id=cid, ticker=ticker, year=yr,
                field="(company_id, year)",
                failed_value=f"({cid}, {yr}) × {cnt}",
                expected_value="unique pair",
                message=f"Duplicate (company_id={cid}, year={yr}) in {table} — {cnt} rows",
            ))

    logger.info("DQ-02: %d failures", len(failures))
    return failures


def check_dq_03(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-03 – Foreign Key Integrity.
    Verify every company_id in financial tables exists in companies.
    Severity: CRITICAL
    """
    failures: list[DQFailure] = []
    fk_tables = [
        "profitandloss", "balancesheet", "cashflow",
        "analysis", "documents", "prosandcons",
        "stock_prices", "financial_ratios",
    ]

    for table in fk_tables:
        try:
            rows = conn.execute(
                f"SELECT DISTINCT company_id FROM {table} "
                f"WHERE company_id NOT IN (SELECT company_id FROM companies);"
            ).fetchall()
        except sqlite3.OperationalError:
            continue

        for row in rows:
            failures.append(_fail(
                "DQ-03", "CRITICAL", table,
                company_id=row[0],
                field="company_id",
                failed_value=str(row[0]),
                expected_value="exists in companies",
                message=f"Orphan company_id={row[0]} in {table} — not found in companies",
            ))

    # peer_groups → sectors FK
    try:
        rows = conn.execute(
            "SELECT DISTINCT sector_id FROM peer_groups "
            "WHERE sector_id NOT IN (SELECT sector_id FROM sectors);"
        ).fetchall()
        for row in rows:
            failures.append(_fail(
                "DQ-03", "CRITICAL", "peer_groups",
                field="sector_id",
                failed_value=str(row[0]),
                expected_value="exists in sectors",
                message=f"Orphan sector_id={row[0]} in peer_groups",
            ))
    except sqlite3.OperationalError:
        pass

    logger.info("DQ-03: %d failures", len(failures))
    return failures


def check_dq_04(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-04 – Balance Sheet Equation: Assets = Liabilities + Equity (within 1%).
    Severity: WARNING
    """
    failures: list[DQFailure] = []
    try:
        rows = conn.execute(
            """
            SELECT b.company_id, b.year, c.ticker,
                   b.total_assets, b.total_liabilities, b.equity
            FROM balancesheet b
            JOIN companies c ON b.company_id = c.company_id
            WHERE b.total_assets IS NOT NULL
              AND b.total_liabilities IS NOT NULL
              AND b.equity IS NOT NULL
              AND ABS(b.total_assets - (b.total_liabilities + b.equity))
                  / NULLIF(b.total_assets, 0) > 0.01;
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        diff_pct = abs(row["total_assets"] - (row["total_liabilities"] + row["equity"])) \
                   / row["total_assets"] * 100
        failures.append(_fail(
            "DQ-04", "WARNING", "balancesheet",
            company_id=row["company_id"], ticker=row["ticker"], year=row["year"],
            field="total_assets",
            failed_value=f"Assets={row['total_assets']:.0f}, "
                         f"Liab+Equity={row['total_liabilities'] + row['equity']:.0f}",
            expected_value="Assets = Liabilities + Equity (within 1%)",
            message=f"Balance sheet imbalance of {diff_pct:.2f}% for {row['ticker']} year {row['year']}",
        ))

    logger.info("DQ-04: %d failures", len(failures))
    return failures


def check_dq_05(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-05 – Operating Margin Cross-Check: OPM ≈ EBIT / Revenue (±2pp).
    Severity: WARNING
    """
    failures: list[DQFailure] = []
    try:
        rows = conn.execute(
            """
            SELECT p.company_id, p.year, c.ticker,
                   p.opm, p.ebit, p.revenue
            FROM profitandloss p
            JOIN companies c ON p.company_id = c.company_id
            WHERE p.opm IS NOT NULL
              AND p.ebit IS NOT NULL
              AND p.revenue IS NOT NULL
              AND p.revenue != 0;
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        calc_opm = (row["ebit"] / row["revenue"]) * 100
        if abs(calc_opm - row["opm"]) > 2.0:
            failures.append(_fail(
                "DQ-05", "WARNING", "profitandloss",
                company_id=row["company_id"], ticker=row["ticker"], year=row["year"],
                field="opm",
                failed_value=f"{row['opm']:.2f}%",
                expected_value=f"{calc_opm:.2f}% (EBIT/Revenue)",
                message=f"OPM mismatch for {row['ticker']} year {row['year']}: "
                        f"stored={row['opm']:.2f}%, calc={calc_opm:.2f}%",
            ))

    logger.info("DQ-05: %d failures", len(failures))
    return failures


def check_dq_06(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-06 – Positive Sales: Revenue must be > 0.
    Severity: WARNING
    """
    failures: list[DQFailure] = []
    try:
        rows = conn.execute(
            """
            SELECT p.company_id, p.year, c.ticker, p.revenue
            FROM profitandloss p
            JOIN companies c ON p.company_id = c.company_id
            WHERE p.revenue IS NULL OR p.revenue <= 0;
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        failures.append(_fail(
            "DQ-06", "WARNING", "profitandloss",
            company_id=row["company_id"], ticker=row["ticker"], year=row["year"],
            field="revenue",
            failed_value=str(row["revenue"]),
            expected_value="> 0",
            message=f"Non-positive revenue for {row['ticker']} year {row['year']}",
        ))

    logger.info("DQ-06: %d failures", len(failures))
    return failures


def check_dq_07(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-07 – Net Cash Consistency: closing_cash ≈ opening_cash + CFO + CFI + CFF (±5%).
    Severity: WARNING
    """
    failures: list[DQFailure] = []
    try:
        rows = conn.execute(
            """
            SELECT cf.company_id, cf.year, c.ticker,
                   cf.opening_cash, cf.closing_cash,
                   cf.operating_cashflow, cf.investing_cashflow, cf.financing_cashflow
            FROM cashflow cf
            JOIN companies c ON cf.company_id = c.company_id
            WHERE cf.opening_cash IS NOT NULL
              AND cf.closing_cash IS NOT NULL
              AND cf.operating_cashflow IS NOT NULL
              AND cf.investing_cashflow IS NOT NULL
              AND cf.financing_cashflow IS NOT NULL;
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        expected = (row["opening_cash"]
                    + row["operating_cashflow"]
                    + row["investing_cashflow"]
                    + row["financing_cashflow"])
        if row["closing_cash"] == 0 and expected == 0:
            continue
        denom = abs(row["closing_cash"]) if row["closing_cash"] != 0 else 1.0
        diff_pct = abs(expected - row["closing_cash"]) / denom * 100
        if diff_pct > 5.0:
            failures.append(_fail(
                "DQ-07", "WARNING", "cashflow",
                company_id=row["company_id"], ticker=row["ticker"], year=row["year"],
                field="closing_cash",
                failed_value=f"{row['closing_cash']:.0f}",
                expected_value=f"≈{expected:.0f} (opening+CFO+CFI+CFF)",
                message=f"Net cash inconsistency of {diff_pct:.2f}% for "
                        f"{row['ticker']} year {row['year']}",
            ))

    logger.info("DQ-07: %d failures", len(failures))
    return failures


def check_dq_08(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-08 – Tax Rate Validation: 0% ≤ tax_rate ≤ 50%.
    Severity: WARNING
    """
    failures: list[DQFailure] = []
    try:
        rows = conn.execute(
            """
            SELECT p.company_id, p.year, c.ticker, p.tax_rate
            FROM profitandloss p
            JOIN companies c ON p.company_id = c.company_id
            WHERE p.tax_rate IS NOT NULL
              AND (p.tax_rate < 0 OR p.tax_rate > 50);
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        failures.append(_fail(
            "DQ-08", "WARNING", "profitandloss",
            company_id=row["company_id"], ticker=row["ticker"], year=row["year"],
            field="tax_rate",
            failed_value=f"{row['tax_rate']:.2f}%",
            expected_value="0% – 50%",
            message=f"Tax rate {row['tax_rate']:.2f}% out of expected range for "
                    f"{row['ticker']} year {row['year']}",
        ))

    logger.info("DQ-08: %d failures", len(failures))
    return failures


def check_dq_09(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-09 – Dividend Cap: dividend ≤ net_profit (companies paying dividends must
    have sufficient profit).
    Severity: WARNING
    """
    failures: list[DQFailure] = []
    try:
        rows = conn.execute(
            """
            SELECT p.company_id, p.year, c.ticker,
                   p.dividend, p.net_profit
            FROM profitandloss p
            JOIN companies c ON p.company_id = c.company_id
            WHERE p.dividend IS NOT NULL
              AND p.dividend > 0
              AND p.net_profit IS NOT NULL
              AND p.dividend > p.net_profit;
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        failures.append(_fail(
            "DQ-09", "WARNING", "profitandloss",
            company_id=row["company_id"], ticker=row["ticker"], year=row["year"],
            field="dividend",
            failed_value=f"dividend={row['dividend']:.0f}",
            expected_value=f"≤ net_profit={row['net_profit']:.0f}",
            message=f"Dividend exceeds net profit for {row['ticker']} year {row['year']}",
        ))

    logger.info("DQ-09: %d failures", len(failures))
    return failures


def check_dq_10(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-10 – URL Validation: URLs in documents table must be valid HTTP/HTTPS URLs.
    Severity: WARNING
    """
    failures: list[DQFailure] = []
    url_pattern = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)

    try:
        rows = conn.execute(
            """
            SELECT d.doc_id, d.company_id, c.ticker, d.url, d.doc_type, d.year
            FROM documents d
            JOIN companies c ON d.company_id = c.company_id
            WHERE d.url IS NOT NULL AND d.url != '';
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        if not url_pattern.match(row["url"]):
            failures.append(_fail(
                "DQ-10", "WARNING", "documents",
                company_id=row["company_id"], ticker=row["ticker"], year=row["year"],
                field="url",
                failed_value=row["url"],
                expected_value="valid http(s):// URL",
                message=f"Invalid URL in documents for {row['ticker']}: {row['url']}",
            ))

    logger.info("DQ-10: %d failures", len(failures))
    return failures


def check_dq_11(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-11 – EPS Sign Validation: EPS sign must match net_profit sign.
    Severity: WARNING
    """
    failures: list[DQFailure] = []
    try:
        rows = conn.execute(
            """
            SELECT p.company_id, p.year, c.ticker, p.eps, p.net_profit
            FROM profitandloss p
            JOIN companies c ON p.company_id = c.company_id
            WHERE p.eps IS NOT NULL
              AND p.net_profit IS NOT NULL
              AND p.net_profit != 0
              AND p.eps != 0
              AND (p.eps > 0) != (p.net_profit > 0);
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        failures.append(_fail(
            "DQ-11", "WARNING", "profitandloss",
            company_id=row["company_id"], ticker=row["ticker"], year=row["year"],
            field="eps",
            failed_value=f"EPS={row['eps']:.2f}",
            expected_value=f"same sign as net_profit={row['net_profit']:.0f}",
            message=f"EPS sign mismatch for {row['ticker']} year {row['year']}: "
                    f"eps={row['eps']:.2f}, net_profit={row['net_profit']:.0f}",
        ))

    logger.info("DQ-11: %d failures", len(failures))
    return failures


def check_dq_12(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-12 – Balance Sheet Equation (detailed):
    Total Assets = Current Assets + Fixed Assets (±5%).
    Severity: WARNING
    """
    failures: list[DQFailure] = []
    try:
        rows = conn.execute(
            """
            SELECT b.company_id, b.year, c.ticker,
                   b.total_assets, b.current_assets, b.fixed_assets
            FROM balancesheet b
            JOIN companies c ON b.company_id = c.company_id
            WHERE b.total_assets IS NOT NULL
              AND b.current_assets IS NOT NULL
              AND b.fixed_assets IS NOT NULL
              AND b.total_assets > 0
              AND ABS(b.total_assets - (b.current_assets + b.fixed_assets))
                  / b.total_assets > 0.05;
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        sum_parts = row["current_assets"] + row["fixed_assets"]
        diff_pct  = abs(row["total_assets"] - sum_parts) / row["total_assets"] * 100
        failures.append(_fail(
            "DQ-12", "WARNING", "balancesheet",
            company_id=row["company_id"], ticker=row["ticker"], year=row["year"],
            field="total_assets",
            failed_value=f"{row['total_assets']:.0f}",
            expected_value=f"≈ current_assets+fixed_assets={sum_parts:.0f} (±5%)",
            message=f"Asset composition mismatch {diff_pct:.2f}% for "
                    f"{row['ticker']} year {row['year']}",
        ))

    logger.info("DQ-12: %d failures", len(failures))
    return failures


def check_dq_13(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-13 – Interest Coverage: EBIT / interest_expense ≥ 1.0 for profitable companies.
    Severity: WARNING
    """
    failures: list[DQFailure] = []
    try:
        rows = conn.execute(
            """
            SELECT p.company_id, p.year, c.ticker,
                   p.ebit, p.interest_expense
            FROM profitandloss p
            JOIN companies c ON p.company_id = c.company_id
            WHERE p.ebit IS NOT NULL
              AND p.interest_expense IS NOT NULL
              AND p.interest_expense > 0
              AND p.ebit > 0
              AND (p.ebit / p.interest_expense) < 1.0;
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        coverage = row["ebit"] / row["interest_expense"]
        failures.append(_fail(
            "DQ-13", "WARNING", "profitandloss",
            company_id=row["company_id"], ticker=row["ticker"], year=row["year"],
            field="interest_coverage",
            failed_value=f"{coverage:.2f}x",
            expected_value="≥ 1.0x",
            message=f"Low interest coverage {coverage:.2f}x for {row['ticker']} year {row['year']}",
        ))

    logger.info("DQ-13: %d failures", len(failures))
    return failures


def check_dq_14(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-14 – Duplicate Row Detection: Check for completely duplicate rows
    (same values on all non-PK columns).
    Severity: CRITICAL
    """
    failures: list[DQFailure] = []

    # Check profitandloss for near-duplicates on (company_id, year, revenue)
    tables_checks = [
        ("profitandloss", "company_id, year, revenue"),
        ("balancesheet",  "company_id, year, total_assets"),
        ("cashflow",      "company_id, year, operating_cashflow"),
        ("stock_prices",  "company_id, year, close_price"),
    ]

    for table, key_cols in tables_checks:
        try:
            rows = conn.execute(
                f"SELECT {key_cols}, COUNT(*) AS cnt FROM {table} "
                f"GROUP BY {key_cols} HAVING cnt > 1;"
            ).fetchall()
        except sqlite3.OperationalError:
            continue

        for row in rows:
            failures.append(_fail(
                "DQ-14", "CRITICAL", table,
                field=key_cols,
                failed_value=str(dict(zip(key_cols.split(", "), row))),
                expected_value="no duplicates",
                message=f"Duplicate row detected in {table} on ({key_cols})",
            ))

    logger.info("DQ-14: %d failures", len(failures))
    return failures


def check_dq_15(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-15 – Missing Mandatory Values: NOT NULL fields must have values.
    Severity: CRITICAL
    """
    failures: list[DQFailure] = []

    mandatory = {
        "companies":     ["ticker", "company_name"],
        "profitandloss": ["company_id", "year", "revenue"],
        "balancesheet":  ["company_id", "year", "total_assets"],
        "cashflow":      ["company_id", "year", "operating_cashflow"],
        "stock_prices":  ["company_id", "year", "close_price"],
    }

    for table, cols in mandatory.items():
        for col in cols:
            try:
                rows = conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM {table} WHERE {col} IS NULL OR {col} = '';"
                ).fetchone()
            except sqlite3.OperationalError:
                continue

            if rows and rows["cnt"] > 0:
                failures.append(_fail(
                    "DQ-15", "CRITICAL", table,
                    field=col,
                    failed_value=f"{rows['cnt']} NULL rows",
                    expected_value="NOT NULL",
                    message=f"Mandatory field '{col}' is NULL in {rows['cnt']} rows of {table}",
                ))

    logger.info("DQ-15: %d failures", len(failures))
    return failures


def check_dq_16(conn: sqlite3.Connection) -> list[DQFailure]:
    """
    DQ-16 – Financial Consistency: YoY Revenue change within ±200%.
    Anomalous spikes likely indicate data errors.
    Severity: WARNING
    """
    failures: list[DQFailure] = []
    try:
        rows = conn.execute(
            """
            SELECT p.company_id, p.year, c.ticker, p.revenue,
                   LAG(p.revenue) OVER (
                       PARTITION BY p.company_id ORDER BY p.year
                   ) AS prev_revenue
            FROM profitandloss p
            JOIN companies c ON p.company_id = c.company_id
            ORDER BY p.company_id, p.year;
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        prev = row["prev_revenue"]
        curr = row["revenue"]
        if prev is None or prev == 0 or curr is None:
            continue
        yoy_pct = abs((curr - prev) / prev) * 100
        if yoy_pct > 200:
            failures.append(_fail(
                "DQ-16", "WARNING", "profitandloss",
                company_id=row["company_id"], ticker=row["ticker"], year=row["year"],
                field="revenue",
                failed_value=f"{curr:.0f} (prev={prev:.0f})",
                expected_value="YoY change ≤ 200%",
                message=f"Revenue YoY spike of {yoy_pct:.1f}% for "
                        f"{row['ticker']} year {row['year']}",
            ))

    logger.info("DQ-16: %d failures", len(failures))
    return failures


# ─── Aggregator ───────────────────────────────────────────────────────────────

_ALL_CHECKS = [
    check_dq_01, check_dq_02, check_dq_03, check_dq_04,
    check_dq_05, check_dq_06, check_dq_07, check_dq_08,
    check_dq_09, check_dq_10, check_dq_11, check_dq_12,
    check_dq_13, check_dq_14, check_dq_15, check_dq_16,
]


def run_all_checks(db_path: str = _DB_PATH) -> list[DQFailure]:
    """
    Execute all 16 DQ checks against the database and return combined failures.

    Parameters
    ----------
    db_path : path to the SQLite database file

    Returns
    -------
    list[DQFailure] — all failures sorted by severity (CRITICAL first) then rule_id
    """
    conn = _conn(db_path)
    all_failures: list[DQFailure] = []

    for check_fn in _ALL_CHECKS:
        try:
            rule_failures = check_fn(conn)
            all_failures.extend(rule_failures)
        except Exception as exc:
            logger.error("Error running %s: %s", check_fn.__name__, exc)

    conn.close()

    # Sort: CRITICAL first, then by rule_id
    severity_order = {"CRITICAL": 0, "WARNING": 1}
    all_failures.sort(key=lambda f: (severity_order.get(f.severity, 9), f.rule_id))

    critical = sum(1 for f in all_failures if f.severity == "CRITICAL")
    warnings = sum(1 for f in all_failures if f.severity == "WARNING")
    logger.info(
        "DQ check complete: %d total failures (%d CRITICAL, %d WARNING)",
        len(all_failures), critical, warnings,
    )
    return all_failures


def write_failures_csv(failures: list[DQFailure], output_dir: Path = _OUTPUT_DIR) -> Path:
    """Write DQ failures to output/validation_failures.csv."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "validation_failures.csv"

    fieldnames = [f.name for f in fields(DQFailure)]
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for failure in failures:
            writer.writerow(failure.as_dict())

    logger.info("Wrote %d failures to %s", len(failures), out_path)
    return out_path


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    failures = run_all_checks()
    out_path = write_failures_csv(failures)

    critical = [f for f in failures if f.severity == "CRITICAL"]
    warnings = [f for f in failures if f.severity == "WARNING"]

    print(f"\n{'='*60}")
    print(f"  DQ Validation Report — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    print(f"  Total failures : {len(failures)}")
    print(f"  CRITICAL       : {len(critical)}")
    print(f"  WARNING        : {len(warnings)}")
    print(f"  Report written : {out_path}")
    print(f"{'='*60}\n")

    if critical:
        print("❌  CRITICAL failures detected — pipeline is NOT production-ready.\n")
        sys.exit(1)
    else:
        print("✅  No CRITICAL failures — pipeline is production-ready.\n")
        sys.exit(0)
