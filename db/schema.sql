-- ============================================================
-- nifty100.db  |  Sprint 1 – Data Foundation  |  Schema DDL
-- ============================================================
-- Run via: sqlite3 nifty100.db < db/schema.sql
-- Or called programmatically by src/etl/pipeline.py
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

-- ────────────────────────────────────────────────────────────
-- 1. SECTORS
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sectors (
    sector_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    sector_name TEXT    NOT NULL UNIQUE,
    description TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sectors_name ON sectors (sector_name);

-- ────────────────────────────────────────────────────────────
-- 2. COMPANIES
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS companies (
    company_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT    NOT NULL UNIQUE,
    company_name  TEXT    NOT NULL,
    sector_id     INTEGER REFERENCES sectors (sector_id) ON DELETE SET NULL,
    isin          TEXT    UNIQUE,
    exchange      TEXT    NOT NULL DEFAULT 'NSE',
    listing_date  TEXT,
    market_cap    REAL,
    description   TEXT,
    founded_year  INTEGER,
    headquarters  TEXT,
    website       TEXT,
    employees     INTEGER,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_companies_ticker    ON companies (ticker);
CREATE INDEX IF NOT EXISTS idx_companies_sector_id ON companies (sector_id);
CREATE INDEX IF NOT EXISTS idx_companies_isin      ON companies (isin);

-- ────────────────────────────────────────────────────────────
-- 3. PROFIT AND LOSS
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profitandloss (
    pl_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id         INTEGER NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    year               INTEGER NOT NULL
                           CHECK (year BETWEEN 1990 AND 2100),
    revenue            REAL    NOT NULL CHECK (revenue > 0),
    cogs               REAL,
    gross_profit       REAL,
    operating_expense  REAL,
    ebit               REAL,
    interest_expense   REAL,
    ebt                REAL,
    tax_expense        REAL,
    net_profit         REAL,
    eps                REAL,
    dividend           REAL    CHECK (dividend IS NULL OR dividend >= 0),
    opm                REAL,   -- Operating Profit Margin %
    npm                REAL,   -- Net Profit Margin %
    tax_rate           REAL    CHECK (tax_rate IS NULL OR (tax_rate >= 0 AND tax_rate <= 100)),
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, year)
);

CREATE INDEX IF NOT EXISTS idx_pl_company_year ON profitandloss (company_id, year);
CREATE INDEX IF NOT EXISTS idx_pl_year         ON profitandloss (year);

-- ────────────────────────────────────────────────────────────
-- 4. BALANCE SHEET
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS balancesheet (
    bs_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    year                INTEGER NOT NULL
                            CHECK (year BETWEEN 1990 AND 2100),
    total_assets        REAL,
    total_liabilities   REAL,
    equity              REAL,
    current_assets      REAL,
    current_liabilities REAL,
    long_term_debt      REAL,
    short_term_debt     REAL,
    cash                REAL,
    receivables         REAL,
    inventory           REAL,
    fixed_assets        REAL,
    reserves            REAL,
    share_capital       REAL,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, year)
);

CREATE INDEX IF NOT EXISTS idx_bs_company_year ON balancesheet (company_id, year);

-- ────────────────────────────────────────────────────────────
-- 5. CASH FLOW
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cashflow (
    cf_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    year                INTEGER NOT NULL
                            CHECK (year BETWEEN 1990 AND 2100),
    operating_cashflow  REAL,
    investing_cashflow  REAL,
    financing_cashflow  REAL,
    capex               REAL,
    free_cashflow       REAL,
    net_cash_change     REAL,
    opening_cash        REAL,
    closing_cash        REAL,
    depreciation        REAL,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, year)
);

CREATE INDEX IF NOT EXISTS idx_cf_company_year ON cashflow (company_id, year);

-- ────────────────────────────────────────────────────────────
-- 6. ANALYSIS (Analyst Ratios per Year)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analysis (
    analysis_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id               INTEGER NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    year                     INTEGER NOT NULL
                                 CHECK (year BETWEEN 1990 AND 2100),
    return_on_equity         REAL,
    return_on_assets         REAL,
    return_on_capital_employed REAL,
    debt_to_equity           REAL,
    current_ratio            REAL,
    quick_ratio              REAL,
    asset_turnover           REAL,
    inventory_turnover       REAL,
    price_to_earnings        REAL,
    price_to_book            REAL,
    enterprise_value         REAL,
    ev_to_ebitda             REAL,
    analyst_rating           TEXT,
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, year)
);

CREATE INDEX IF NOT EXISTS idx_analysis_company_year ON analysis (company_id, year);

-- ────────────────────────────────────────────────────────────
-- 7. DOCUMENTS (Annual Report URLs)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    doc_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    doc_type    TEXT    NOT NULL DEFAULT 'Annual Report',
    year        INTEGER,
    url         TEXT,
    description TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_docs_company ON documents (company_id);

-- ────────────────────────────────────────────────────────────
-- 8. PROS AND CONS
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prosandcons (
    pc_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    year        INTEGER,
    item_type   TEXT    NOT NULL CHECK (item_type IN ('pro', 'con')),
    description TEXT    NOT NULL,
    category    TEXT,
    severity    TEXT    CHECK (severity IS NULL OR severity IN ('low', 'medium', 'high')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pc_company ON prosandcons (company_id);
CREATE INDEX IF NOT EXISTS idx_pc_type    ON prosandcons (item_type);

-- ────────────────────────────────────────────────────────────
-- 9. STOCK PRICES (Annual Summary)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_prices (
    price_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    year        INTEGER NOT NULL
                    CHECK (year BETWEEN 1990 AND 2100),
    open_price  REAL,
    high_price  REAL,
    low_price   REAL,
    close_price REAL    NOT NULL,
    volume      INTEGER,
    pe_ratio    REAL,
    market_cap  REAL,
    week52_high REAL,
    week52_low  REAL,
    beta        REAL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, year)
);

CREATE INDEX IF NOT EXISTS idx_sp_company_year ON stock_prices (company_id, year);

-- ────────────────────────────────────────────────────────────
-- 10. FINANCIAL RATIOS (Computed)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS financial_ratios (
    ratio_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id            INTEGER NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    year                  INTEGER NOT NULL
                              CHECK (year BETWEEN 1990 AND 2100),
    gross_margin          REAL,
    operating_margin      REAL,
    net_margin            REAL,
    roe                   REAL,
    roa                   REAL,
    roce                  REAL,
    debt_to_equity        REAL,
    current_ratio         REAL,
    quick_ratio           REAL,
    interest_coverage     REAL,
    asset_turnover        REAL,
    inventory_turnover    REAL,
    receivable_days       REAL,
    payable_days          REAL,
    cash_conversion_cycle REAL,
    dividend_yield        REAL,
    payout_ratio          REAL,
    book_value_per_share  REAL,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, year)
);

CREATE INDEX IF NOT EXISTS idx_fr_company_year ON financial_ratios (company_id, year);

-- ────────────────────────────────────────────────────────────
-- 11. PEER GROUPS
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS peer_groups (
    group_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name  TEXT    NOT NULL,
    sector_id   INTEGER REFERENCES sectors (sector_id) ON DELETE SET NULL,
    company_id  INTEGER REFERENCES companies (company_id) ON DELETE CASCADE,
    description TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (group_name, company_id)
);

CREATE INDEX IF NOT EXISTS idx_pg_group  ON peer_groups (group_name);
CREATE INDEX IF NOT EXISTS idx_pg_sector ON peer_groups (sector_id);
