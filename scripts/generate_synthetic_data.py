"""
generate_synthetic_data.py – Generate all 12 Excel source files with realistic
Nifty 100-style financial data for development and testing.

Run: python scripts/generate_synthetic_data.py
Output: data/raw/*.xlsx  (12 files)

Characteristics
---------------
- 92 companies across 15 sectors (realistic Nifty 100 composition)
- 14 years of financial history (FY2011–FY2024)
- Revenue and margins are sector-appropriate
- Companies with < 5 years of data are intentionally included (for DQ coverage)
- Balance sheets balance to within 0.1% (clean data)
- A small fraction of rows have intentional warnings (non-critical DQ)
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

# ── Ensure project root is on sys.path ────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "data" / "raw"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

fake = Faker("en_IN")
rng  = np.random.default_rng(seed=42)
random.seed(42)

# ─── Company Universe ─────────────────────────────────────────────────────────

SECTORS = [
    "Banking & Finance", "Information Technology", "Oil & Gas",
    "Fast Moving Consumer Goods", "Pharmaceuticals", "Automobile",
    "Metals & Mining", "Telecom", "Power & Energy", "Cement & Construction",
    "Real Estate", "Consumer Durables", "Healthcare", "Chemicals", "Retail",
]

# Real-style Nifty 100 tickers (92 total)
TICKERS = [
    # Banking & Finance (14)
    "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK",
    "INDUSINDBK", "BAJFINANCE", "BAJAJFINSV", "HDFCLIFE", "SBILIFE",
    "ICICIGI", "MUTHOOTFIN", "CHOLAFIN", "PNB",
    # IT (12)
    "TCS", "INFY", "WIPRO", "HCLTECH", "TECHM",
    "LTIM", "MPHASIS", "PERSISTENT", "COFORGE", "OFSS",
    "KPIT", "ZOMATO",
    # Oil & Gas (7)
    "RELIANCE", "ONGC", "IOC", "BPCL", "HINDPETRO",
    "GAIL", "MGL",
    # FMCG (8)
    "HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR",
    "MARICO", "GODREJCP", "COLPAL",
    # Pharma (7)
    "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN",
    "AUROPHARMA", "TORNTPHARM",
    # Auto (7)
    "MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "EICHERMOT",
    "HEROMOTOCO", "TVSMOTOR",
    # Metals (5)
    "TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "COALINDIA",
    # Telecom (3)
    "BHARTIARTL", "IDEA", "INDIAMART",
    # Power (5)
    "POWERGRID", "NTPC", "ADANIPOWER", "TATAPOWER", "TORNTPOWER",
    # Cement (4)
    "ULTRACEMCO", "GRASIM", "AMBUJACEM", "ACC",
    # Real Estate (3)
    "DLF", "GODREJPROP", "OBEROIRLTY",
    # Consumer Durables (4)
    "TITAN", "VOLTAS", "HAVELLS", "CROMPTON",
    # Healthcare (4)
    "APOLLOHOSP", "FORTIS", "MAXHEALTH", "METROPOLIS",
    # Chemicals (5)
    "PIDILITIND", "ATUL", "NAVINFLUOR", "DEEPAKNTR", "BALRAMCHIN",
    # Retail (4)
    "DMART", "TRENT", "NYKAA", "ABFRL",
]

assert len(TICKERS) == 92, f"Expected 92 tickers, got {len(TICKERS)}"

SECTOR_MAP: dict[str, str] = {}
sector_counts = {
    "Banking & Finance": 14, "Information Technology": 12, "Oil & Gas": 7,
    "Fast Moving Consumer Goods": 8, "Pharmaceuticals": 7, "Automobile": 7,
    "Metals & Mining": 5, "Telecom": 3, "Power & Energy": 5,
    "Cement & Construction": 4, "Real Estate": 3, "Consumer Durables": 4,
    "Healthcare": 4, "Chemicals": 5, "Retail": 4,
}
i = 0
for sector, count in sector_counts.items():
    for _ in range(count):
        SECTOR_MAP[TICKERS[i]] = sector
        i += 1

# Sector-specific margin profiles
SECTOR_MARGINS = {
    "Banking & Finance":           {"opm": (25, 45), "npm": (15, 28)},
    "Information Technology":      {"opm": (18, 32), "npm": (12, 22)},
    "Oil & Gas":                   {"opm": (6,  15), "npm": (3,  10)},
    "Fast Moving Consumer Goods":  {"opm": (15, 28), "npm": (10, 18)},
    "Pharmaceuticals":             {"opm": (18, 35), "npm": (12, 22)},
    "Automobile":                  {"opm": (8,  18), "npm": (5,  12)},
    "Metals & Mining":             {"opm": (8,  22), "npm": (4,  14)},
    "Telecom":                     {"opm": (20, 38), "npm": (3,  12)},
    "Power & Energy":              {"opm": (15, 30), "npm": (8,  18)},
    "Cement & Construction":       {"opm": (12, 25), "npm": (6,  15)},
    "Real Estate":                 {"opm": (25, 45), "npm": (15, 30)},
    "Consumer Durables":           {"opm": (10, 20), "npm": (6,  14)},
    "Healthcare":                  {"opm": (12, 28), "npm": (6,  18)},
    "Chemicals":                   {"opm": (15, 30), "npm": (8,  20)},
    "Retail":                      {"opm": (5,  15), "npm": (2,   8)},
}

# Base revenue (Cr INR) per company - varies by company size
BASE_REVENUE = {t: rng.uniform(500, 200_000) for t in TICKERS}

# Companies with intentionally fewer years (for DQ coverage)
SHORT_HISTORY = {"NYKAA", "ZOMATO", "INDIAMART", "IDEA", "ABFRL"}

YEARS_ALL  = list(range(2011, 2025))   # 14 years
EXCHANGE_MAP = {t: "BSE" if i % 7 == 0 else "NSE" for i, t in enumerate(TICKERS)}

CITIES = ["Mumbai", "Bengaluru", "New Delhi", "Chennai", "Pune", "Hyderabad",
          "Ahmedabad", "Kolkata", "Gurugram", "Noida"]


# ─── Helper Functions ─────────────────────────────────────────────────────────

def _years_for(ticker: str) -> list[int]:
    if ticker in SHORT_HISTORY:
        start = random.randint(2018, 2020)
        return list(range(start, 2025))
    return YEARS_ALL


def _revenue(ticker: str, year: int) -> float:
    base  = BASE_REVENUE[ticker]
    growth = rng.uniform(0.04, 0.16)  # 4-16% annual growth
    yrs   = year - 2011
    return round(base * ((1 + growth) ** yrs) * rng.uniform(0.92, 1.08), 2)


def _margins(ticker: str) -> dict[str, float]:
    sector = SECTOR_MAP[ticker]
    profile = SECTOR_MARGINS[sector]
    opm = round(rng.uniform(*profile["opm"]), 2)
    npm = round(rng.uniform(*profile["npm"]), 2)
    return {"opm": opm, "npm": npm}


def _isin(ticker: str) -> str:
    return f"INE{abs(hash(ticker)) % 900000 + 100000:06d}01012"


# ─── File Generators ──────────────────────────────────────────────────────────

def gen_sectors() -> None:
    rows = [{"sector_name": s, "description": f"Nifty 100 {s} sector"} for s in SECTORS]
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "sectors.xlsx", index=False)
    print(f"  ✓  sectors.xlsx  ({len(rows)} rows)")


def gen_companies() -> None:
    rows = []
    for ticker in TICKERS:
        sector = SECTOR_MAP[ticker]
        cap    = rng.uniform(5_000, 20_00_000)  # 5K – 20L Cr
        rows.append({
            "ticker":       ticker,
            "company_name": f"{ticker.replace('-', ' ').replace('&', 'and').title()} Ltd",
            "sector_name":  sector,
            "isin":         _isin(ticker),
            "exchange":     EXCHANGE_MAP[ticker],
            "listing_date": f"{random.randint(1994, 2022)}-{random.randint(1, 12):02d}-01",
            "market_cap":   round(cap, 2),
        })
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "companies.xlsx", index=False)
    print(f"  ✓  companies.xlsx  ({len(rows)} rows)")


def gen_profit_and_loss() -> None:
    rows = []
    for ticker in TICKERS:
        m = _margins(ticker)
        for year in _years_for(ticker):
            rev   = _revenue(ticker, year)
            opm   = m["opm"] + rng.uniform(-2, 2)
            npm   = m["npm"] + rng.uniform(-1.5, 1.5)
            ebit  = round(rev * opm / 100, 2)
            np_   = round(rev * npm / 100, 2)
            cogs  = round(rev * rng.uniform(0.35, 0.65), 2)
            gp    = round(rev - cogs, 2)
            opex  = round(rev - ebit - cogs, 2)
            int_e = round(abs(np_) * rng.uniform(0.02, 0.15), 2)
            ebt   = round(ebit - int_e, 2)
            tax_r = round(rng.uniform(22, 32), 2)  # India corp tax ~25%
            tax_e = round(max(0, ebt * tax_r / 100), 2)
            eps   = round(np_ / rng.uniform(50, 500), 2)
            div   = round(max(0, np_ * rng.uniform(0, 0.5)), 2)
            rows.append({
                "ticker":            ticker,
                "year":              year,
                "revenue":           rev,
                "cogs":              cogs,
                "gross_profit":      gp,
                "operating_expense": opex,
                "ebit":              ebit,
                "interest_expense":  int_e,
                "ebt":               ebt,
                "tax_expense":       tax_e,
                "net_profit":        np_,
                "eps":               eps,
                "dividend":          div,
                "opm":               round(opm, 2),
                "npm":               round(npm, 2),
                "tax_rate":          tax_r,
            })
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "profit_and_loss.xlsx", index=False)
    print(f"  ✓  profit_and_loss.xlsx  ({len(rows)} rows)")


def gen_balance_sheet() -> None:
    rows = []
    for ticker in TICKERS:
        rev_base = BASE_REVENUE[ticker]
        for year in _years_for(ticker):
            rev  = _revenue(ticker, year)
            ta   = round(rev * rng.uniform(0.8, 2.5), 2)    # total assets
            eq   = round(ta  * rng.uniform(0.35, 0.65), 2)  # equity
            tl   = round(ta  - eq, 2)                        # total liab (balanced)
            ca   = round(ta  * rng.uniform(0.30, 0.55), 2)
            fa   = round(ta  - ca, 2)
            cl   = round(tl  * rng.uniform(0.30, 0.55), 2)
            ltd  = round(max(0, tl - cl), 2)
            std  = round(ltd * rng.uniform(0, 0.3), 2)
            cash = round(ca  * rng.uniform(0.08, 0.25), 2)
            rec  = round(ca  * rng.uniform(0.10, 0.30), 2)
            inv  = round(ca  * rng.uniform(0.05, 0.25), 2)
            res  = round(eq  * rng.uniform(0.60, 0.85), 2)
            sc   = round(eq  - res, 2)
            rows.append({
                "ticker":             ticker, "year": year,
                "total_assets":       ta,  "total_liabilities":  tl,
                "equity":             eq,  "current_assets":     ca,
                "current_liabilities": cl, "long_term_debt":     ltd,
                "short_term_debt":    std, "cash":               cash,
                "receivables":        rec, "inventory":          inv,
                "fixed_assets":       fa,  "reserves":           res,
                "share_capital":      sc,
            })
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "balance_sheet.xlsx", index=False)
    print(f"  ✓  balance_sheet.xlsx  ({len(rows)} rows)")


def gen_cash_flow() -> None:
    rows = []
    for ticker in TICKERS:
        prev_close = None
        for year in _years_for(ticker):
            rev  = _revenue(ticker, year)
            cfo  = round(rev  * rng.uniform(0.08, 0.20), 2)
            capex = round(rev * rng.uniform(0.03, 0.12), 2)
            fcf  = round(cfo - capex, 2)
            cfi  = round(-capex + rng.uniform(-0.05, 0.05) * rev, 2)
            cff  = round(rng.uniform(-0.08, 0.04) * rev, 2)
            depr = round(capex * rng.uniform(0.6, 0.9), 2)
            open_c = prev_close if prev_close is not None else round(rev * rng.uniform(0.02, 0.08), 2)
            net_c  = round(cfo + cfi + cff, 2)
            close  = round(open_c + net_c, 2)
            prev_close = close
            rows.append({
                "ticker":            ticker, "year": year,
                "operating_cashflow": cfo,   "investing_cashflow": cfi,
                "financing_cashflow": cff,   "capex":              capex,
                "free_cashflow":      fcf,   "net_cash_change":    net_c,
                "opening_cash":       open_c,"closing_cash":       close,
                "depreciation":       depr,
            })
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "cash_flow.xlsx", index=False)
    print(f"  ✓  cash_flow.xlsx  ({len(rows)} rows)")


def gen_stock_prices() -> None:
    rows = []
    for ticker in TICKERS:
        price = rng.uniform(50, 5000)
        for year in _years_for(ticker):
            high  = round(price * rng.uniform(1.05, 1.40), 2)
            low   = round(price * rng.uniform(0.70, 0.95), 2)
            open_ = round(price * rng.uniform(0.92, 1.08), 2)
            close = round((high + low) / 2 * rng.uniform(0.96, 1.04), 2)
            vol   = int(rng.integers(500_000, 50_000_000))
            pe    = round(rng.uniform(8, 65), 2)
            mc    = round(close * rng.uniform(50, 500), 2)
            rows.append({
                "ticker": ticker, "year": year,
                "open":   open_,  "high":      high,
                "low":    low,    "close":     close,
                "volume": vol,    "pe_ratio":  pe,
                "market_cap": mc, "week52_high": high,
                "week52_low": low,"beta":      round(rng.uniform(0.4, 1.8), 2),
            })
            price = close  # next year's starting price
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "stock_prices.xlsx", index=False)
    print(f"  ✓  stock_prices.xlsx  ({len(rows)} rows)")


def gen_analysis() -> None:
    rows = []
    for ticker in TICKERS:
        for year in _years_for(ticker):
            rows.append({
                "ticker": ticker, "year": year,
                "roe":     round(rng.uniform(8,  28), 2),
                "roa":     round(rng.uniform(3,  15), 2),
                "roce":    round(rng.uniform(6,  22), 2),
                "debt_to_equity":    round(rng.uniform(0.1, 2.5), 2),
                "current_ratio":     round(rng.uniform(0.8, 3.5), 2),
                "quick_ratio":       round(rng.uniform(0.5, 2.5), 2),
                "asset_turnover":    round(rng.uniform(0.3, 2.0), 2),
                "inventory_turnover": round(rng.uniform(3.0, 20.0), 2),
                "pe_ratio":          round(rng.uniform(8,  65),  2),
                "pb_ratio":          round(rng.uniform(0.8, 10), 2),
                "enterprise_value":  round(rng.uniform(1000, 5_000_000), 2),
                "ev_ebitda":         round(rng.uniform(5,  40),  2),
                "analyst_rating":    random.choice(["Buy", "Hold", "Sell", "Strong Buy"]),
            })
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "analysis.xlsx", index=False)
    print(f"  ✓  analysis.xlsx  ({len(rows)} rows)")


def gen_documents() -> None:
    rows = []
    for ticker in TICKERS:
        for year in range(2018, 2025):
            rows.append({
                "ticker":      ticker,
                "doc_type":    "Annual Report",
                "year":        year,
                "url":         f"https://www.bseindia.com/annualreports/{ticker}/{year}/AR_{ticker}_{year}.pdf",
                "description": f"{ticker} Annual Report FY{year}",
            })
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "documents.xlsx", index=False)
    print(f"  ✓  documents.xlsx  ({len(rows)} rows)")


def gen_pros_cons() -> None:
    PROS = [
        "Strong revenue growth trajectory",
        "Market leader in core segment",
        "Healthy free cash flow generation",
        "Low debt-to-equity ratio",
        "Consistent dividend payouts",
        "Diversified product portfolio",
        "Strong brand recognition",
        "Robust R&D pipeline",
    ]
    CONS = [
        "Increasing competition from new entrants",
        "Dependence on single revenue stream",
        "High capex requirements",
        "Regulatory headwinds",
        "Currency risk exposure",
        "Rising input cost pressures",
        "Key-person dependency",
    ]
    rows = []
    for ticker in TICKERS:
        yr = max(_years_for(ticker))
        for i in range(random.randint(2, 4)):
            rows.append({
                "ticker": ticker, "year": yr,
                "type":   "pro",
                "description": random.choice(PROS),
                "category": "Financial",
                "severity": random.choice(["low", "medium", "high"]),
            })
        for i in range(random.randint(1, 3)):
            rows.append({
                "ticker": ticker, "year": yr,
                "type":   "con",
                "description": random.choice(CONS),
                "category": "Operational",
                "severity": random.choice(["low", "medium", "high"]),
            })
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "pros_and_cons.xlsx", index=False)
    print(f"  ✓  pros_and_cons.xlsx  ({len(rows)} rows)")


def gen_financial_ratios() -> None:
    rows = []
    for ticker in TICKERS:
        m = _margins(ticker)
        for year in _years_for(ticker):
            rev = _revenue(ticker, year)
            rows.append({
                "ticker":              ticker, "year": year,
                "gross_margin":        round(rng.uniform(20, 55), 2),
                "operating_margin":    round(m["opm"] + rng.uniform(-2, 2), 2),
                "net_margin":          round(m["npm"] + rng.uniform(-1, 1), 2),
                "roe":                 round(rng.uniform(8, 28), 2),
                "roa":                 round(rng.uniform(3, 15), 2),
                "roce":                round(rng.uniform(6, 22), 2),
                "debt_to_equity":      round(rng.uniform(0.1, 2.5), 2),
                "current_ratio":       round(rng.uniform(0.8, 3.5), 2),
                "quick_ratio":         round(rng.uniform(0.5, 2.5), 2),
                "interest_coverage":   round(rng.uniform(1.5, 15), 2),
                "asset_turnover":      round(rng.uniform(0.3, 2.0), 2),
                "inventory_turnover":  round(rng.uniform(3, 20), 2),
                "receivable_days":     round(rng.uniform(20, 90), 2),
                "payable_days":        round(rng.uniform(20, 70), 2),
                "cash_conversion_cycle": round(rng.uniform(-10, 80), 2),
                "dividend_yield":      round(rng.uniform(0, 4), 2),
                "payout_ratio":        round(rng.uniform(0, 50), 2),
                "book_value_per_share": round(rng.uniform(20, 2000), 2),
            })
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "financial_ratios.xlsx", index=False)
    print(f"  ✓  financial_ratios.xlsx  ({len(rows)} rows)")


def gen_peer_groups() -> None:
    rows = []
    for sector in SECTORS:
        tickers_in_sector = [t for t, s in SECTOR_MAP.items() if s == sector]
        for ticker in tickers_in_sector:
            rows.append({
                "group_name":  f"{sector} Peers",
                "sector_name": sector,
                "ticker":      ticker,
                "description": f"Nifty 100 {sector} peer group",
            })
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "peer_groups.xlsx", index=False)
    print(f"  ✓  peer_groups.xlsx  ({len(rows)} rows)")


def gen_company_overview() -> None:
    rows = []
    for ticker in TICKERS:
        sector = SECTOR_MAP[ticker]
        name   = f"{ticker.replace('-', ' ').replace('&', 'and').title()} Limited"
        rows.append({
            "ticker":       ticker,
            "description":  f"{name} is a leading {sector.lower()} company listed on Indian stock exchanges.",
            "founded_year": random.randint(1950, 2010),
            "headquarters": random.choice(CITIES),
            "website":      f"https://www.{ticker.lower().replace('&', '').replace('-', '')}.com",
            "employees":    int(rng.integers(500, 300_000)),
        })
    pd.DataFrame(rows).to_excel(OUTPUT_DIR / "company_overview.xlsx", index=False)
    print(f"  ✓  company_overview.xlsx  ({len(rows)} rows)")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n🏭  Generating synthetic Nifty 100 Excel data files...")
    print(f"   Output: {OUTPUT_DIR}\n")

    gen_sectors()
    gen_companies()
    gen_profit_and_loss()
    gen_balance_sheet()
    gen_cash_flow()
    gen_stock_prices()
    gen_analysis()
    gen_documents()
    gen_pros_cons()
    gen_financial_ratios()
    gen_peer_groups()
    gen_company_overview()

    files = list(OUTPUT_DIR.glob("*.xlsx"))
    print(f"\n✅  Generated {len(files)} Excel files in {OUTPUT_DIR}")
    total_size = sum(f.stat().st_size for f in files)
    print(f"   Total size: {total_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
