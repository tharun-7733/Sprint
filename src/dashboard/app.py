"""
src/dashboard/app.py – Streamlit analytics dashboard (Sprint 1 stub).
Launch: make dashboard  →  streamlit run src/dashboard/app.py
"""
import sqlite3
import os
import streamlit as st
import pandas as pd

DB_PATH = os.getenv("DATABASE_URL", "nifty100.db")

st.set_page_config(page_title="Nifty 100 Analytics", page_icon="📈", layout="wide")

@st.cache_data
def query(sql: str) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(sql, conn)
    conn.close()
    return df

st.title("📈 Nifty 100 Financial Analytics – Sprint 1")
st.caption("Data Foundation Dashboard")

col1, col2, col3 = st.columns(3)
try:
    n_companies = query("SELECT COUNT(*) AS n FROM companies;")["n"].iloc[0]
    n_pl        = query("SELECT COUNT(*) AS n FROM profitandloss;")["n"].iloc[0]
    n_sp        = query("SELECT COUNT(*) AS n FROM stock_prices;")["n"].iloc[0]
    col1.metric("Companies",      n_companies)
    col2.metric("P&L Records",   n_pl)
    col3.metric("Price Records",  n_sp)
except Exception:
    st.warning("Database not found. Run `make load` first.")

st.subheader("Top 10 Companies by Revenue (Latest Year)")
try:
    df = query(
        """
        SELECT c.ticker, c.company_name, s.sector_name,
               ROUND(p.revenue/1e7, 1) AS revenue_cr,
               ROUND(p.opm, 1) AS opm_pct
        FROM profitandloss p
        JOIN companies c ON c.company_id = p.company_id
        LEFT JOIN sectors s ON s.sector_id = c.sector_id
        WHERE p.year = (SELECT MAX(year) FROM profitandloss)
        ORDER BY p.revenue DESC LIMIT 10
        """
    )
    st.dataframe(df, use_container_width=True)
    st.bar_chart(df.set_index("ticker")["revenue_cr"])
except Exception as e:
    st.info(f"No data yet: {e}")
