# ──────────────────────────────────────────────────────────────────────────────
# Sprint 1 – Data Foundation  |  Makefile
# ──────────────────────────────────────────────────────────────────────────────
PYTHON      := venv/bin/python
PIP         := venv/bin/pip
PYTEST      := venv/bin/pytest
STREAMLIT   := venv/bin/streamlit
UVICORN     := venv/bin/uvicorn
DB          := nifty100.db

.DEFAULT_GOAL := help

.PHONY: help setup load ratios test report dashboard api clean lint format

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── Environment ───────────────────────────────────────────────────────────────
setup: venv/bin/activate  ## Create venv and install all dependencies
venv/bin/activate: requirements.txt
	python3 -m venv venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@touch venv/bin/activate
	@echo "✅  Virtual environment ready.  Run: source venv/bin/activate"

# ── Synthetic Data ────────────────────────────────────────────────────────────
data/raw/.generated: setup
	@echo "⚙️   Generating synthetic Excel data..."
	$(PYTHON) scripts/generate_synthetic_data.py
	@touch data/raw/.generated
	@echo "✅  Synthetic data generated in data/raw/"

# ── ETL Pipeline ──────────────────────────────────────────────────────────────
load: setup data/raw/.generated  ## Run the full ETL pipeline → nifty100.db
	@echo "🚀  Starting ETL pipeline..."
	$(PYTHON) -m src.etl.pipeline
	@echo "✅  Load complete. See output/load_audit.csv"

# ── Financial Ratios ──────────────────────────────────────────────────────────
ratios: load  ## Compute and persist financial_ratios table
	@echo "📊  Computing financial ratios..."
	$(PYTHON) -c "from src.etl.pipeline import compute_ratios; compute_ratios()"
	@echo "✅  Ratios computed."

# ── Tests ─────────────────────────────────────────────────────────────────────
test: setup  ## Run all unit tests with coverage
	@echo "🧪  Running test suite..."
	$(PYTEST) tests/ -v --cov=src --cov-report=term-missing
	@echo "✅  Tests complete."

# ── DQ Report ─────────────────────────────────────────────────────────────────
report: setup  ## Run DQ validator → output/validation_failures.csv
	@echo "🔍  Running data quality validation..."
	$(PYTHON) -m src.etl.validator
	@echo "✅  Report written to output/validation_failures.csv"

# ── Dashboard ─────────────────────────────────────────────────────────────────
dashboard: setup load  ## Launch the Streamlit analytics dashboard
	@echo "📈  Launching Streamlit dashboard..."
	$(STREAMLIT) run src/dashboard/app.py

# ── API ───────────────────────────────────────────────────────────────────────
api: setup load  ## Launch the FastAPI REST API (port 8000)
	@echo "🌐  Starting FastAPI server on http://localhost:8000 ..."
	$(UVICORN) src.api.main:app --reload --port 8000

# ── Code Quality ──────────────────────────────────────────────────────────────
lint: setup  ## Run ruff linter
	venv/bin/ruff check src/ tests/

format: setup  ## Format code with black
	venv/bin/black src/ tests/ scripts/

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:  ## Remove database, generated outputs, and cache
	@echo "🧹  Cleaning generated artifacts..."
	rm -f $(DB)
	rm -f output/*.csv output/*.html output/*.json
	rm -f data/raw/.generated
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✅  Clean complete."
