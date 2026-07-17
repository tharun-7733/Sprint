-- ============================================================
-- exploratory_queries.sql
-- Sprint 1 – Data Foundation | Exploratory Analysis
-- Database: nifty100.db
-- Usage: sqlite3 nifty100.db < notebooks/exploratory_queries.sql
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- Query 1: Company Count by Sector
-- Answers: How many Nifty 100 companies are in each sector?
-- ────────────────────────────────────────────────────────────
.print ''
.print '=== Q1: Company Count by Sector ==='
SELECT
    s.sector_name,
    COUNT(c.company_id)  AS company_count,
    ROUND(COUNT(c.company_id) * 100.0 / (SELECT COUNT(*) FROM companies), 1) AS pct_of_total
FROM sectors s
LEFT JOIN companies c ON c.sector_id = s.sector_id
GROUP BY s.sector_name
ORDER BY company_count DESC;

-- ────────────────────────────────────────────────────────────
-- Query 2: Yearly Revenue Trends (Top 10 Companies, Latest Year)
-- Answers: Which companies generated the most revenue in FY2024?
-- ────────────────────────────────────────────────────────────
.print ''
.print '=== Q2: Top 10 Companies by Revenue (FY2024) ==='
SELECT
    c.ticker,
    c.company_name,
    s.sector_name,
    ROUND(p.revenue / 1e7, 2)     AS revenue_cr,
    ROUND(p.net_profit / 1e7, 2)  AS net_profit_cr,
    ROUND(p.opm, 2)               AS opm_pct
FROM profitandloss p
JOIN companies  c ON c.company_id = p.company_id
JOIN sectors    s ON s.sector_id  = c.sector_id
WHERE p.year = (SELECT MAX(year) FROM profitandloss)
ORDER BY p.revenue DESC
LIMIT 10;

-- ────────────────────────────────────────────────────────────
-- Query 3: Sector-Wise Average Operating Margin (Latest Year)
-- Answers: Which sectors are most profitable?
-- ────────────────────────────────────────────────────────────
.print ''
.print '=== Q3: Sector Average OPM (Latest Year) ==='
SELECT
    s.sector_name,
    COUNT(DISTINCT p.company_id)  AS companies,
    ROUND(AVG(p.opm), 2)         AS avg_opm_pct,
    ROUND(MAX(p.opm), 2)         AS max_opm_pct,
    ROUND(MIN(p.opm), 2)         AS min_opm_pct
FROM profitandloss p
JOIN companies c ON c.company_id = p.company_id
JOIN sectors   s ON s.sector_id  = c.sector_id
WHERE p.year = (SELECT MAX(year) FROM profitandloss)
  AND p.opm IS NOT NULL
GROUP BY s.sector_name
ORDER BY avg_opm_pct DESC;

-- ────────────────────────────────────────────────────────────
-- Query 4: Companies with Missing Years (< 5 Years of P&L Data)
-- Answers: Which companies have insufficient financial history?
-- ────────────────────────────────────────────────────────────
.print ''
.print '=== Q4: Companies with < 5 Years of P&L Data ==='
SELECT
    c.ticker,
    c.company_name,
    s.sector_name,
    COUNT(p.year)         AS years_of_data,
    MIN(p.year)           AS earliest_year,
    MAX(p.year)           AS latest_year
FROM companies c
LEFT JOIN profitandloss p ON p.company_id = c.company_id
LEFT JOIN sectors       s ON s.sector_id  = c.sector_id
GROUP BY c.company_id, c.ticker, c.company_name, s.sector_name
HAVING COUNT(p.year) < 5
ORDER BY years_of_data ASC, c.ticker;

-- ────────────────────────────────────────────────────────────
-- Query 5: Top 10 Companies by Revenue (5-Year CAGR)
-- Answers: Who has grown fastest over the last 5 years?
-- ────────────────────────────────────────────────────────────
.print ''
.print '=== Q5: Top 10 Revenue CAGR (5-Year) ==='
WITH latest AS (
    SELECT company_id, year AS latest_year, revenue AS latest_rev
    FROM profitandloss
    WHERE year = (SELECT MAX(year) FROM profitandloss)
),
base AS (
    SELECT company_id, year AS base_year, revenue AS base_rev
    FROM profitandloss
    WHERE year = (SELECT MAX(year) FROM profitandloss) - 5
)
SELECT
    c.ticker,
    c.company_name,
    s.sector_name,
    ROUND(l.latest_rev / 1e7, 1)   AS revenue_fy24_cr,
    ROUND(b.base_rev   / 1e7, 1)   AS revenue_fy19_cr,
    ROUND(
        (POWER(l.latest_rev / NULLIF(b.base_rev, 0), 0.2) - 1) * 100, 2
    )                               AS cagr_5yr_pct
FROM latest    l
JOIN base      b ON b.company_id = l.company_id
JOIN companies c ON c.company_id = l.company_id
JOIN sectors   s ON s.sector_id  = c.sector_id
WHERE b.base_rev > 0
ORDER BY cagr_5yr_pct DESC
LIMIT 10;

-- ────────────────────────────────────────────────────────────
-- Query 6: Stock Price Statistics (52-Week Range & Beta)
-- Answers: What is the price volatility profile of Nifty 100?
-- ────────────────────────────────────────────────────────────
.print ''
.print '=== Q6: Stock Price Statistics (Latest Year) ==='
SELECT
    c.ticker,
    s.sector_name,
    ROUND(sp.close_price, 2)   AS close_price,
    ROUND(sp.week52_high, 2)   AS week52_high,
    ROUND(sp.week52_low,  2)   AS week52_low,
    ROUND((sp.week52_high - sp.week52_low) / NULLIF(sp.week52_low, 0) * 100, 1)
                               AS price_range_pct,
    ROUND(sp.pe_ratio, 1)      AS pe_ratio,
    ROUND(sp.beta, 2)          AS beta
FROM stock_prices sp
JOIN companies c ON c.company_id = sp.company_id
JOIN sectors   s ON s.sector_id  = c.sector_id
WHERE sp.year = (SELECT MAX(year) FROM stock_prices)
ORDER BY price_range_pct DESC
LIMIT 15;

-- ────────────────────────────────────────────────────────────
-- Query 7: YoY Net Profit Growth Leaders (Latest Year)
-- Answers: Who grew profits the most year-over-year?
-- ────────────────────────────────────────────────────────────
.print ''
.print '=== Q7: Top 10 YoY Net Profit Growth (Latest Year) ==='
WITH yoy AS (
    SELECT
        p.company_id, p.year,
        p.net_profit,
        LAG(p.net_profit) OVER (PARTITION BY p.company_id ORDER BY p.year) AS prev_profit
    FROM profitandloss p
)
SELECT
    c.ticker,
    c.company_name,
    s.sector_name,
    yoy.year,
    ROUND(yoy.net_profit  / 1e7, 1) AS net_profit_cr,
    ROUND(yoy.prev_profit / 1e7, 1) AS prev_profit_cr,
    ROUND((yoy.net_profit - yoy.prev_profit) / NULLIF(ABS(yoy.prev_profit), 0) * 100, 1)
                                    AS yoy_growth_pct
FROM yoy
JOIN companies c ON c.company_id = yoy.company_id
JOIN sectors   s ON s.sector_id  = c.sector_id
WHERE yoy.year = (SELECT MAX(year) FROM profitandloss)
  AND yoy.prev_profit IS NOT NULL
  AND yoy.prev_profit > 0
ORDER BY yoy_growth_pct DESC
LIMIT 10;

-- ────────────────────────────────────────────────────────────
-- Query 8: Balance Sheet Health – Debt-to-Equity Analysis
-- Answers: Which companies carry the most / least leverage?
-- ────────────────────────────────────────────────────────────
.print ''
.print '=== Q8: Balance Sheet Health – Debt-to-Equity (Latest Year) ==='
SELECT
    c.ticker,
    c.company_name,
    s.sector_name,
    ROUND(b.total_assets      / 1e7, 1) AS assets_cr,
    ROUND(b.equity            / 1e7, 1) AS equity_cr,
    ROUND(b.long_term_debt    / 1e7, 1) AS ltd_cr,
    ROUND(COALESCE(b.long_term_debt, 0) / NULLIF(b.equity, 0), 2) AS debt_equity_ratio,
    ROUND(b.current_assets / NULLIF(b.current_liabilities, 0), 2) AS current_ratio
FROM balancesheet b
JOIN companies c ON c.company_id = b.company_id
JOIN sectors   s ON s.sector_id  = c.sector_id
WHERE b.year = (SELECT MAX(year) FROM balancesheet)
  AND b.equity > 0
ORDER BY debt_equity_ratio DESC
LIMIT 10;

-- ────────────────────────────────────────────────────────────
-- Query 9: Dividend Payers vs Non-Payers
-- Answers: What proportion of Nifty 100 pays dividends?
-- ────────────────────────────────────────────────────────────
.print ''
.print '=== Q9: Dividend Payers vs Non-Payers (Latest Year) ==='
SELECT
    CASE WHEN p.dividend > 0 THEN 'Dividend Payer' ELSE 'Non-Payer' END AS category,
    COUNT(*)                          AS company_count,
    ROUND(AVG(p.dividend / 1e7), 2)  AS avg_dividend_cr,
    ROUND(AVG(p.net_profit > 0) * 100, 1) AS profitable_pct
FROM profitandloss p
JOIN companies c ON c.company_id = p.company_id
WHERE p.year = (SELECT MAX(year) FROM profitandloss)
GROUP BY category;

-- ────────────────────────────────────────────────────────────
-- Query 10: Peer Group Comparison – IT Sector
-- Answers: How do IT companies compare on key metrics?
-- ────────────────────────────────────────────────────────────
.print ''
.print '=== Q10: IT Sector Peer Group Comparison (Latest Year) ==='
SELECT
    c.ticker,
    ROUND(p.revenue     / 1e7, 1)   AS revenue_cr,
    ROUND(p.net_profit  / 1e7, 1)   AS net_profit_cr,
    ROUND(p.opm,   2)               AS opm_pct,
    ROUND(p.npm,   2)               AS npm_pct,
    ROUND(fr.roe,  2)               AS roe_pct,
    ROUND(fr.current_ratio, 2)      AS current_ratio,
    ROUND(sp.pe_ratio, 1)           AS pe_ratio,
    fr.analyst_rating               AS rating
FROM companies c
JOIN sectors          s  ON s.sector_id  = c.sector_id
JOIN profitandloss    p  ON p.company_id = c.company_id
LEFT JOIN financial_ratios fr ON fr.company_id = c.company_id AND fr.year = p.year
LEFT JOIN stock_prices     sp ON sp.company_id = c.company_id AND sp.year = p.year
WHERE s.sector_name = 'Information Technology'
  AND p.year = (SELECT MAX(year) FROM profitandloss)
ORDER BY p.revenue DESC;
