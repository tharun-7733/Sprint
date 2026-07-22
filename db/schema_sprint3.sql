-- ============================================================
-- schema_sprint3.sql  |  Sprint 3 — Screener + Peer Engine
-- ============================================================
-- Adds the peer_percentiles table used by src/analytics/peer.py.
-- Run once against nifty100.db:
--   sqlite3 nifty100.db < db/schema_sprint3.sql
-- ============================================================

PRAGMA foreign_keys = ON;

-- ────────────────────────────────────────────────────────────
-- 12. PEER PERCENTILES
--     Stores PERCENT_RANK for each company/group/metric/year.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS peer_percentiles (
    pp_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       INTEGER NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    peer_group_name  TEXT    NOT NULL,
    metric           TEXT    NOT NULL,
    value            REAL,
    percentile_rank  REAL    CHECK (percentile_rank IS NULL
                                    OR (percentile_rank >= 0 AND percentile_rank <= 1)),
    year             INTEGER NOT NULL CHECK (year BETWEEN 1990 AND 2100),
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, peer_group_name, metric, year)
);

CREATE INDEX IF NOT EXISTS idx_pp_company    ON peer_percentiles (company_id);
CREATE INDEX IF NOT EXISTS idx_pp_group      ON peer_percentiles (peer_group_name);
CREATE INDEX IF NOT EXISTS idx_pp_metric     ON peer_percentiles (metric);
CREATE INDEX IF NOT EXISTS idx_pp_group_met  ON peer_percentiles (peer_group_name, metric);
