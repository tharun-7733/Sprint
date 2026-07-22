"""
loader.py – Excel-to-SQLite data loader for the ETL pipeline.

Provides the ExcelLoader class and per-table load functions that read from
data/raw/*.xlsx files, normalise values using normaliser.py, and insert rows
into the nifty100.db SQLite database.

Design
------
- Each load_* function returns a LoadResult (rows_inserted, rows_rejected, rejections).
- The pipeline is idempotent: tables are truncated before each load by default
  (controlled by TRUNCATE_BEFORE_LOAD env var).
- All Excel files are read through the ExcelLoader.read() helper which handles
  missing files, empty sheets, and schema mismatches gracefully.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from dotenv import load_dotenv
import os

from src.etl.normaliser import (
    normalize_currency,
    normalize_percentage,
    normalize_ticker,
    normalize_year,
)

load_dotenv()
logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────
_DATA_DIR          = Path(os.getenv("DATA_DIR", "data/raw"))
_DB_PATH           = os.getenv("DATABASE_URL", "nifty100.db")
_TRUNCATE_BEFORE   = os.getenv("TRUNCATE_BEFORE_LOAD", "true").lower() == "true"


# ─── Data Classes ─────────────────────────────────────────────────────────────
@dataclass
class Rejection:
    """Represents a single rejected row with reason."""
    table: str
    row_index: int
    ticker: Optional[str]
    year: Optional[int]
    reason: str
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoadResult:
    """Summary of a single table load operation."""
    table: str
    rows_inserted: int = 0
    rows_rejected: int = 0
    rejections: list[Rejection] = field(default_factory=list)
    load_timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def add_rejection(self, row_index: Any, ticker: Optional[str],
                      year: Optional[int], reason: str,
                      raw_data: Optional[dict] = None) -> None:
        self.rows_rejected += 1
        try:
            r_idx = int(row_index)
        except (ValueError, TypeError):
            r_idx = -1

        self.rejections.append(Rejection(
            table=self.table,
            row_index=r_idx,
            ticker=ticker,
            year=year,
            reason=reason,
            raw_data=raw_data or {},
        ))


# ─── ExcelLoader ──────────────────────────────────────────────────────────────
class ExcelLoader:
    """
    Utility class for loading Excel source files into pandas DataFrames.

    Usage
    -----
    loader = ExcelLoader(data_dir=Path("data/raw"))
    df = loader.read("companies.xlsx", sheet_name=0)
    """

    def __init__(self, data_dir: Path = _DATA_DIR) -> None:
        self.data_dir = Path(data_dir)

    def read(
        self,
        filename: str,
        sheet_name: int | str = 0,
        required_columns: Optional[list[str]] = None,
        dtype: Optional[dict] = None,
    ) -> pd.DataFrame:
        """
        Read an Excel file and return a cleaned DataFrame.

        Parameters
        ----------
        filename         : name of the Excel file (relative to data_dir)
        sheet_name       : sheet index or name (default: first sheet)
        required_columns : if provided, raise ValueError if any are missing
        dtype            : optional dtype override dict

        Returns empty DataFrame on file-not-found or read error.
        """
        filepath = self.data_dir / filename
        if not filepath.exists():
            logger.error("Excel file not found: %s", filepath)
            return pd.DataFrame()

        try:
            df = pd.read_excel(
                filepath,
                sheet_name=sheet_name,
                dtype=dtype,
                engine="openpyxl",
            )
        except Exception as exc:
            logger.error("Failed to read %s: %s", filepath, exc)
            return pd.DataFrame()

        # Drop fully-empty rows
        df = df.dropna(how="all").reset_index(drop=True)

        # Strip column names
        df.columns = [str(c).strip() for c in df.columns]

        if required_columns:
            missing = [c for c in required_columns if c not in df.columns]
            if missing:
                raise ValueError(
                    f"{filename}: missing required columns: {missing}. "
                    f"Available: {list(df.columns)}"
                )

        logger.info("Loaded %s rows from %s (sheet=%s)", len(df), filename, sheet_name)
        return df


# ─── Database Helpers ─────────────────────────────────────────────────────────
def _get_connection(db_path: str = _DB_PATH) -> sqlite3.Connection:
    """Return a SQLite connection with FK enforcement and WAL mode."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def _truncate_table(conn: sqlite3.Connection, table: str) -> None:
    """Remove all rows from a table (idempotent reset)."""
    conn.execute(f"DELETE FROM {table};")
    logger.debug("Truncated table: %s", table)


def _get_company_id_map(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {ticker: company_id} mapping from the companies table."""
    rows = conn.execute("SELECT ticker, company_id FROM companies;").fetchall()
    return {row[0]: row[1] for row in rows}


# ─── Per-Table Loaders ────────────────────────────────────────────────────────

def load_sectors(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Load sectors from sectors.xlsx → sectors table."""
    result = LoadResult(table="sectors")
    df = excel.read("sectors.xlsx")
    if df.empty:
        return result

    if _TRUNCATE_BEFORE:
        _truncate_table(conn, "sectors")

    for idx, row in df.iterrows():
        sector_name = str(row.get("sector_name", "")).strip()
        description = str(row.get("description", "")).strip()

        if not sector_name:
            result.add_rejection(idx, None, None, "Missing sector_name")
            continue

        try:
            conn.execute(
                "INSERT OR REPLACE INTO sectors (sector_name, description) VALUES (?, ?);",
                (sector_name, description or None),
            )
            result.rows_inserted += 1
        except sqlite3.IntegrityError as e:
            result.add_rejection(idx, None, None, f"IntegrityError: {e}")

    conn.commit()
    logger.info("sectors: inserted=%d rejected=%d", result.rows_inserted, result.rows_rejected)
    return result


def load_companies(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Load companies from companies.xlsx → companies table."""
    result = LoadResult(table="companies")
    df = excel.read("companies.xlsx")
    if df.empty:
        return result

    if _TRUNCATE_BEFORE:
        _truncate_table(conn, "companies")

    # Build sector_id map
    sector_rows = conn.execute("SELECT sector_id, sector_name FROM sectors;").fetchall()
    sector_map  = {name.lower(): sid for sid, name in sector_rows}

    for idx, row in df.iterrows():
        ticker    = normalize_ticker(row.get("ticker"))
        name      = str(row.get("company_name", "")).strip()
        sector_nm = str(row.get("sector_name", "")).strip().lower()
        isin      = str(row.get("isin", "")).strip() or None
        exchange  = str(row.get("exchange", "NSE")).strip()
        listing   = str(row.get("listing_date", "")).strip() or None
        market_cap = normalize_currency(row.get("market_cap"))

        if not ticker:
            result.add_rejection(idx, None, None, "Missing/invalid ticker")
            continue
        if not name:
            result.add_rejection(idx, ticker, None, "Missing company_name")
            continue

        sector_id = sector_map.get(sector_nm)

        try:
            conn.execute(
                """INSERT OR REPLACE INTO companies
                   (ticker, company_name, sector_id, isin, exchange, listing_date, market_cap)
                   VALUES (?, ?, ?, ?, ?, ?, ?);""",
                (ticker, name, sector_id, isin, exchange, listing, market_cap),
            )
            result.rows_inserted += 1
        except sqlite3.IntegrityError as e:
            result.add_rejection(idx, ticker, None, f"IntegrityError: {e}")

    conn.commit()
    logger.info("companies: inserted=%d rejected=%d", result.rows_inserted, result.rows_rejected)
    return result


def load_profit_and_loss(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Load P&L data from profit_and_loss.xlsx → profitandloss table."""
    result = LoadResult(table="profitandloss")
    df = excel.read("profit_and_loss.xlsx")
    if df.empty:
        return result

    if _TRUNCATE_BEFORE:
        _truncate_table(conn, "profitandloss")

    cid_map = _get_company_id_map(conn)

    for idx, row in df.iterrows():
        ticker = normalize_ticker(row.get("ticker"))
        year   = normalize_year(row.get("year"))

        if not ticker:
            result.add_rejection(idx, None, None, "Missing/invalid ticker")
            continue
        if year is None:
            result.add_rejection(idx, ticker, None, "Missing/invalid year")
            continue

        company_id = cid_map.get(ticker)
        if company_id is None:
            result.add_rejection(idx, ticker, year, f"Unknown ticker (FK violation): {ticker}")
            continue

        revenue          = normalize_currency(row.get("revenue"))
        cogs             = normalize_currency(row.get("cogs"))
        gross_profit     = normalize_currency(row.get("gross_profit"))
        operating_expense = normalize_currency(row.get("operating_expense"))
        ebit             = normalize_currency(row.get("ebit"))
        interest_expense = normalize_currency(row.get("interest_expense"))
        ebt              = normalize_currency(row.get("ebt"))
        tax_expense      = normalize_currency(row.get("tax_expense"))
        net_profit       = normalize_currency(row.get("net_profit"))
        eps              = normalize_currency(row.get("eps"))
        dividend         = normalize_currency(row.get("dividend"))
        opm              = normalize_percentage(row.get("opm"))
        npm              = normalize_percentage(row.get("npm"))
        tax_rate         = normalize_percentage(row.get("tax_rate"))

        if revenue is None or revenue <= 0:
            result.add_rejection(idx, ticker, year, "Revenue is null or non-positive (DQ-06)")
            continue

        try:
            conn.execute(
                """INSERT OR REPLACE INTO profitandloss
                   (company_id, year, revenue, cogs, gross_profit, operating_expense,
                    ebit, interest_expense, ebt, tax_expense, net_profit, eps,
                    dividend, opm, npm, tax_rate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (company_id, year, revenue, cogs, gross_profit, operating_expense,
                 ebit, interest_expense, ebt, tax_expense, net_profit, eps,
                 dividend, opm, npm, tax_rate),
            )
            result.rows_inserted += 1
        except sqlite3.IntegrityError as e:
            result.add_rejection(idx, ticker, year, f"IntegrityError: {e}")

    conn.commit()
    logger.info("profitandloss: inserted=%d rejected=%d", result.rows_inserted, result.rows_rejected)
    return result


def load_balance_sheet(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Load balance sheet data from balance_sheet.xlsx → balancesheet table."""
    result = LoadResult(table="balancesheet")
    df = excel.read("balance_sheet.xlsx")
    if df.empty:
        return result

    if _TRUNCATE_BEFORE:
        _truncate_table(conn, "balancesheet")

    cid_map = _get_company_id_map(conn)

    for idx, row in df.iterrows():
        ticker = normalize_ticker(row.get("ticker"))
        year   = normalize_year(row.get("year"))

        if not ticker:
            result.add_rejection(idx, None, None, "Missing/invalid ticker")
            continue
        if year is None:
            result.add_rejection(idx, ticker, None, "Missing/invalid year")
            continue

        company_id = cid_map.get(ticker)
        if company_id is None:
            result.add_rejection(idx, ticker, year, f"Unknown ticker (FK): {ticker}")
            continue

        total_assets      = normalize_currency(row.get("total_assets"))
        total_liabilities = normalize_currency(row.get("total_liabilities"))
        equity            = normalize_currency(row.get("equity"))
        current_assets    = normalize_currency(row.get("current_assets"))
        current_liabilities = normalize_currency(row.get("current_liabilities"))
        long_term_debt    = normalize_currency(row.get("long_term_debt"))
        short_term_debt   = normalize_currency(row.get("short_term_debt"))
        cash              = normalize_currency(row.get("cash"))
        receivables       = normalize_currency(row.get("receivables"))
        inventory         = normalize_currency(row.get("inventory"))
        fixed_assets      = normalize_currency(row.get("fixed_assets"))
        reserves          = normalize_currency(row.get("reserves"))
        share_capital     = normalize_currency(row.get("share_capital"))

        try:
            conn.execute(
                """INSERT OR REPLACE INTO balancesheet
                   (company_id, year, total_assets, total_liabilities, equity,
                    current_assets, current_liabilities, long_term_debt,
                    short_term_debt, cash, receivables, inventory, fixed_assets,
                    reserves, share_capital)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (company_id, year, total_assets, total_liabilities, equity,
                 current_assets, current_liabilities, long_term_debt,
                 short_term_debt, cash, receivables, inventory, fixed_assets,
                 reserves, share_capital),
            )
            result.rows_inserted += 1
        except sqlite3.IntegrityError as e:
            result.add_rejection(idx, ticker, year, f"IntegrityError: {e}")

    conn.commit()
    logger.info("balancesheet: inserted=%d rejected=%d", result.rows_inserted, result.rows_rejected)
    return result


def load_cashflow(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Load cash flow data from cash_flow.xlsx → cashflow table."""
    result = LoadResult(table="cashflow")
    df = excel.read("cash_flow.xlsx")
    if df.empty:
        return result

    if _TRUNCATE_BEFORE:
        _truncate_table(conn, "cashflow")

    cid_map = _get_company_id_map(conn)

    for idx, row in df.iterrows():
        ticker = normalize_ticker(row.get("ticker"))
        year   = normalize_year(row.get("year"))

        if not ticker:
            result.add_rejection(idx, None, None, "Missing/invalid ticker")
            continue
        if year is None:
            result.add_rejection(idx, ticker, None, "Missing/invalid year")
            continue

        company_id = cid_map.get(ticker)
        if company_id is None:
            result.add_rejection(idx, ticker, year, f"Unknown ticker (FK): {ticker}")
            continue

        cfo  = normalize_currency(row.get("operating_cashflow"))
        cfi  = normalize_currency(row.get("investing_cashflow"))
        cff  = normalize_currency(row.get("financing_cashflow"))
        capex = normalize_currency(row.get("capex"))
        fcf  = normalize_currency(row.get("free_cashflow"))
        net_cash_change = normalize_currency(row.get("net_cash_change"))
        opening_cash    = normalize_currency(row.get("opening_cash"))
        closing_cash    = normalize_currency(row.get("closing_cash"))
        depreciation    = normalize_currency(row.get("depreciation"))

        try:
            conn.execute(
                """INSERT OR REPLACE INTO cashflow
                   (company_id, year, operating_cashflow, investing_cashflow,
                    financing_cashflow, capex, free_cashflow, net_cash_change,
                    opening_cash, closing_cash, depreciation)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (company_id, year, cfo, cfi, cff, capex, fcf, net_cash_change,
                 opening_cash, closing_cash, depreciation),
            )
            result.rows_inserted += 1
        except sqlite3.IntegrityError as e:
            result.add_rejection(idx, ticker, year, f"IntegrityError: {e}")

    conn.commit()
    logger.info("cashflow: inserted=%d rejected=%d", result.rows_inserted, result.rows_rejected)
    return result


def load_stock_prices(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Load stock prices from stock_prices.xlsx → stock_prices table."""
    result = LoadResult(table="stock_prices")
    df = excel.read("stock_prices.xlsx")
    if df.empty:
        return result

    if _TRUNCATE_BEFORE:
        _truncate_table(conn, "stock_prices")

    cid_map = _get_company_id_map(conn)

    for idx, row in df.iterrows():
        ticker = normalize_ticker(row.get("ticker"))
        year   = normalize_year(row.get("year"))

        if not ticker:
            result.add_rejection(idx, None, None, "Missing/invalid ticker")
            continue
        if year is None:
            result.add_rejection(idx, ticker, None, "Missing/invalid year")
            continue

        company_id = cid_map.get(ticker)
        if company_id is None:
            result.add_rejection(idx, ticker, year, f"Unknown ticker (FK): {ticker}")
            continue

        open_price  = normalize_currency(row.get("open"))
        high_price  = normalize_currency(row.get("high"))
        low_price   = normalize_currency(row.get("low"))
        close_price = normalize_currency(row.get("close"))
        volume      = row.get("volume")
        pe_ratio    = normalize_currency(row.get("pe_ratio"))
        market_cap  = normalize_currency(row.get("market_cap"))
        week52_high = normalize_currency(row.get("week52_high"))
        week52_low  = normalize_currency(row.get("week52_low"))
        beta        = normalize_currency(row.get("beta"))

        try:
            volume_val = int(volume) if volume is not None and str(volume).strip() else None
        except (ValueError, TypeError):
            volume_val = None

        try:
            conn.execute(
                """INSERT OR REPLACE INTO stock_prices
                   (company_id, year, open_price, high_price, low_price, close_price,
                    volume, pe_ratio, market_cap, week52_high, week52_low, beta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (company_id, year, open_price, high_price, low_price, close_price,
                 volume_val, pe_ratio, market_cap, week52_high, week52_low, beta),
            )
            result.rows_inserted += 1
        except sqlite3.IntegrityError as e:
            result.add_rejection(idx, ticker, year, f"IntegrityError: {e}")

    conn.commit()
    logger.info("stock_prices: inserted=%d rejected=%d", result.rows_inserted, result.rows_rejected)
    return result


def load_analysis(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Load analyst data from analysis.xlsx → analysis table."""
    result = LoadResult(table="analysis")
    df = excel.read("analysis.xlsx")
    if df.empty:
        return result

    if _TRUNCATE_BEFORE:
        _truncate_table(conn, "analysis")

    cid_map = _get_company_id_map(conn)

    for idx, row in df.iterrows():
        ticker = normalize_ticker(row.get("ticker"))
        year   = normalize_year(row.get("year"))

        if not ticker:
            result.add_rejection(idx, None, None, "Missing/invalid ticker")
            continue
        if year is None:
            result.add_rejection(idx, ticker, None, "Missing/invalid year")
            continue

        company_id = cid_map.get(ticker)
        if company_id is None:
            result.add_rejection(idx, ticker, year, f"Unknown ticker (FK): {ticker}")
            continue

        try:
            conn.execute(
                """INSERT OR REPLACE INTO analysis
                   (company_id, year, return_on_equity, return_on_assets,
                    return_on_capital_employed, debt_to_equity, current_ratio,
                    quick_ratio, asset_turnover, inventory_turnover,
                    price_to_earnings, price_to_book, enterprise_value,
                    ev_to_ebitda, analyst_rating)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (
                    company_id, year,
                    normalize_percentage(row.get("roe")),
                    normalize_percentage(row.get("roa")),
                    normalize_percentage(row.get("roce")),
                    normalize_currency(row.get("debt_to_equity")),
                    normalize_currency(row.get("current_ratio")),
                    normalize_currency(row.get("quick_ratio")),
                    normalize_currency(row.get("asset_turnover")),
                    normalize_currency(row.get("inventory_turnover")),
                    normalize_currency(row.get("pe_ratio")),
                    normalize_currency(row.get("pb_ratio")),
                    normalize_currency(row.get("enterprise_value")),
                    normalize_currency(row.get("ev_ebitda")),
                    str(row.get("analyst_rating", "")).strip() or None,
                ),
            )
            result.rows_inserted += 1
        except sqlite3.IntegrityError as e:
            result.add_rejection(idx, ticker, year, f"IntegrityError: {e}")

    conn.commit()
    logger.info("analysis: inserted=%d rejected=%d", result.rows_inserted, result.rows_rejected)
    return result


def load_documents(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Load documents (annual reports URLs) from documents.xlsx → documents table."""
    result = LoadResult(table="documents")
    df = excel.read("documents.xlsx")
    if df.empty:
        return result

    if _TRUNCATE_BEFORE:
        _truncate_table(conn, "documents")

    cid_map = _get_company_id_map(conn)

    for idx, row in df.iterrows():
        ticker = normalize_ticker(row.get("ticker"))
        if not ticker:
            result.add_rejection(idx, None, None, "Missing/invalid ticker")
            continue

        company_id = cid_map.get(ticker)
        if company_id is None:
            result.add_rejection(idx, ticker, None, f"Unknown ticker (FK): {ticker}")
            continue

        doc_type    = str(row.get("doc_type", "Annual Report")).strip()
        doc_year    = normalize_year(row.get("year"))
        url         = str(row.get("url", "")).strip() or None
        description = str(row.get("description", "")).strip() or None

        try:
            conn.execute(
                """INSERT OR REPLACE INTO documents
                   (company_id, doc_type, year, url, description)
                   VALUES (?, ?, ?, ?, ?);""",
                (company_id, doc_type, doc_year, url, description),
            )
            result.rows_inserted += 1
        except sqlite3.IntegrityError as e:
            result.add_rejection(idx, ticker, doc_year, f"IntegrityError: {e}")

    conn.commit()
    logger.info("documents: inserted=%d rejected=%d", result.rows_inserted, result.rows_rejected)
    return result


def load_pros_cons(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Load pros & cons from pros_and_cons.xlsx → prosandcons table."""
    result = LoadResult(table="prosandcons")
    df = excel.read("pros_and_cons.xlsx")
    if df.empty:
        return result

    if _TRUNCATE_BEFORE:
        _truncate_table(conn, "prosandcons")

    cid_map = _get_company_id_map(conn)

    for idx, row in df.iterrows():
        ticker = normalize_ticker(row.get("ticker"))
        year   = normalize_year(row.get("year"))

        if not ticker:
            result.add_rejection(idx, None, None, "Missing/invalid ticker")
            continue

        company_id = cid_map.get(ticker)
        if company_id is None:
            result.add_rejection(idx, ticker, year, f"Unknown ticker (FK): {ticker}")
            continue

        item_type = str(row.get("type", "")).strip().lower()  # "pro" or "con"
        description = str(row.get("description", "")).strip()
        category   = str(row.get("category", "")).strip() or None
        severity   = str(row.get("severity", "")).strip() or None

        if not description:
            result.add_rejection(idx, ticker, year, "Missing description")
            continue

        try:
            conn.execute(
                """INSERT OR REPLACE INTO prosandcons
                   (company_id, year, item_type, description, category, severity)
                   VALUES (?, ?, ?, ?, ?, ?);""",
                (company_id, year, item_type, description, category, severity),
            )
            result.rows_inserted += 1
        except sqlite3.IntegrityError as e:
            result.add_rejection(idx, ticker, year, f"IntegrityError: {e}")

    conn.commit()
    logger.info("prosandcons: inserted=%d rejected=%d", result.rows_inserted, result.rows_rejected)
    return result


def load_financial_ratios(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Load financial ratios from financial_ratios.xlsx → financial_ratios table."""
    result = LoadResult(table="financial_ratios")
    df = excel.read("financial_ratios.xlsx")
    if df.empty:
        return result

    if _TRUNCATE_BEFORE:
        _truncate_table(conn, "financial_ratios")

    cid_map = _get_company_id_map(conn)

    for idx, row in df.iterrows():
        ticker = normalize_ticker(row.get("ticker"))
        year   = normalize_year(row.get("year"))

        if not ticker or year is None:
            result.add_rejection(idx, ticker, year, "Missing ticker or year")
            continue

        company_id = cid_map.get(ticker)
        if company_id is None:
            result.add_rejection(idx, ticker, year, f"Unknown ticker (FK): {ticker}")
            continue

        try:
            conn.execute(
                """INSERT OR REPLACE INTO financial_ratios
                   (company_id, year, gross_margin, operating_margin, net_margin,
                    roe, roa, roce, debt_to_equity, current_ratio, quick_ratio,
                    interest_coverage, asset_turnover, inventory_turnover,
                    receivable_days, payable_days, cash_conversion_cycle,
                    dividend_yield, payout_ratio, book_value_per_share)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (
                    company_id, year,
                    normalize_percentage(row.get("gross_margin")),
                    normalize_percentage(row.get("operating_margin")),
                    normalize_percentage(row.get("net_margin")),
                    normalize_percentage(row.get("roe")),
                    normalize_percentage(row.get("roa")),
                    normalize_percentage(row.get("roce")),
                    normalize_currency(row.get("debt_to_equity")),
                    normalize_currency(row.get("current_ratio")),
                    normalize_currency(row.get("quick_ratio")),
                    normalize_currency(row.get("interest_coverage")),
                    normalize_currency(row.get("asset_turnover")),
                    normalize_currency(row.get("inventory_turnover")),
                    normalize_currency(row.get("receivable_days")),
                    normalize_currency(row.get("payable_days")),
                    normalize_currency(row.get("cash_conversion_cycle")),
                    normalize_percentage(row.get("dividend_yield")),
                    normalize_percentage(row.get("payout_ratio")),
                    normalize_currency(row.get("book_value_per_share")),
                ),
            )
            result.rows_inserted += 1
        except sqlite3.IntegrityError as e:
            result.add_rejection(idx, ticker, year, f"IntegrityError: {e}")

    conn.commit()
    logger.info("financial_ratios: inserted=%d rejected=%d", result.rows_inserted, result.rows_rejected)
    return result


def load_peer_groups(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Load peer group mappings from peer_groups.xlsx → peer_groups table."""
    result = LoadResult(table="peer_groups")
    df = excel.read("peer_groups.xlsx")
    if df.empty:
        return result

    if _TRUNCATE_BEFORE:
        _truncate_table(conn, "peer_groups")

    sector_rows = conn.execute("SELECT sector_id, sector_name FROM sectors;").fetchall()
    sector_map  = {name.lower(): sid for sid, name in sector_rows}
    cid_map     = _get_company_id_map(conn)

    for idx, row in df.iterrows():
        group_name  = str(row.get("group_name", "")).strip()
        sector_nm   = str(row.get("sector_name", "")).strip().lower()
        ticker      = normalize_ticker(row.get("ticker"))
        description = str(row.get("description", "")).strip() or None

        if not group_name:
            result.add_rejection(idx, ticker, None, "Missing group_name")
            continue

        sector_id  = sector_map.get(sector_nm)
        company_id = cid_map.get(ticker) if ticker else None

        try:
            conn.execute(
                """INSERT OR REPLACE INTO peer_groups
                   (group_name, sector_id, company_id, description)
                   VALUES (?, ?, ?, ?);""",
                (group_name, sector_id, company_id, description),
            )
            result.rows_inserted += 1
        except sqlite3.IntegrityError as e:
            result.add_rejection(idx, ticker, None, f"IntegrityError: {e}")

    conn.commit()
    logger.info("peer_groups: inserted=%d rejected=%d", result.rows_inserted, result.rows_rejected)
    return result


def load_company_overview(conn: sqlite3.Connection, excel: ExcelLoader) -> LoadResult:
    """Supplement companies with overview data from company_overview.xlsx."""
    result = LoadResult(table="companies (overview update)")
    df = excel.read("company_overview.xlsx")
    if df.empty:
        return result

    cid_map = _get_company_id_map(conn)

    for idx, row in df.iterrows():
        ticker = normalize_ticker(row.get("ticker"))
        if not ticker:
            result.add_rejection(idx, None, None, "Missing/invalid ticker")
            continue

        company_id = cid_map.get(ticker)
        if company_id is None:
            result.add_rejection(idx, ticker, None, f"Unknown ticker (FK): {ticker}")
            continue

        description  = str(row.get("description", "")).strip() or None
        founded_year = normalize_year(row.get("founded_year"))
        headquarters = str(row.get("headquarters", "")).strip() or None
        website      = str(row.get("website", "")).strip() or None
        employees    = row.get("employees")

        try:
            emp_val = int(employees) if employees is not None and str(employees).strip() else None
        except (ValueError, TypeError):
            emp_val = None

        try:
            conn.execute(
                """UPDATE companies SET
                   description=?, founded_year=?, headquarters=?,
                   website=?, employees=?
                   WHERE company_id=?;""",
                (description, founded_year, headquarters, website, emp_val, company_id),
            )
            result.rows_inserted += 1
        except sqlite3.Error as e:
            result.add_rejection(idx, ticker, None, f"Update error: {e}")

    conn.commit()
    logger.info("company_overview update: rows=%d rejected=%d",
                result.rows_inserted, result.rows_rejected)
    return result
