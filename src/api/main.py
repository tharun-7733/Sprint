"""
src/api/main.py – FastAPI REST API stub (Sprint 1).
Launch: make api  →  uvicorn src.api.main:app --reload --port 8000
Docs:   http://localhost:8000/docs
"""
import sqlite3, os
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

DB_PATH = os.getenv("DATABASE_URL", "nifty100.db")

app = FastAPI(
    title="Nifty 100 Financial Analytics API",
    description="Sprint 1 – Data Foundation REST API",
    version="1.0.0",
)

def _query(sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/companies")
def list_companies(sector: str = None, limit: int = 50):
    if sector:
        return _query(
            "SELECT c.*, s.sector_name FROM companies c "
            "LEFT JOIN sectors s ON s.sector_id=c.sector_id "
            "WHERE s.sector_name LIKE ? LIMIT ?",
            (f"%{sector}%", limit),
        )
    return _query(
        "SELECT c.*, s.sector_name FROM companies c "
        "LEFT JOIN sectors s ON s.sector_id=c.sector_id LIMIT ?", (limit,)
    )

@app.get("/companies/{ticker}")
def get_company(ticker: str):
    rows = _query(
        "SELECT c.*, s.sector_name FROM companies c "
        "LEFT JOIN sectors s ON s.sector_id=c.sector_id WHERE c.ticker=?",
        (ticker.upper(),),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Company {ticker} not found")
    return rows[0]

@app.get("/companies/{ticker}/financials")
def get_financials(ticker: str):
    return _query(
        "SELECT p.* FROM profitandloss p "
        "JOIN companies c ON c.company_id=p.company_id "
        "WHERE c.ticker=? ORDER BY p.year", (ticker.upper(),)
    )

@app.get("/sectors")
def list_sectors():
    return _query("SELECT * FROM sectors ORDER BY sector_name")
