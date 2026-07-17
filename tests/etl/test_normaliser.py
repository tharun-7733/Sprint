"""
test_normaliser.py – Comprehensive unit tests for src/etl/normaliser.py.

Coverage
--------
normalize_year()       – 22 tests
normalize_ticker()     – 16 tests
normalize_currency()   – 10 tests
normalize_percentage() – 9  tests
                         ──────
Total                  – 57 tests
"""

from __future__ import annotations

import math
import pytest

from src.etl.normaliser import (
    normalize_currency,
    normalize_percentage,
    normalize_ticker,
    normalize_year,
)


# ═══════════════════════════════════════════════════════════════════════
# normalize_year() – 22 tests
# ═══════════════════════════════════════════════════════════════════════

class TestNormalizeYear:
    """Tests for normalize_year()."""

    # ── Null / empty ──────────────────────────────────────────────────
    def test_none_returns_none(self):
        assert normalize_year(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_year("") is None

    def test_whitespace_only_returns_none(self):
        assert normalize_year("   ") is None

    def test_nan_float_returns_none(self):
        assert normalize_year(float("nan")) is None

    # ── Integer / float inputs ─────────────────────────────────────────
    def test_integer_4digit(self):
        assert normalize_year(2023) == 2023

    def test_float_4digit(self):
        assert normalize_year(2022.0) == 2022

    def test_integer_2digit(self):
        assert normalize_year(23) == 2023

    def test_out_of_range_integer_returns_none(self):
        assert normalize_year(1800) is None

    def test_future_out_of_range_returns_none(self):
        assert normalize_year(2200) is None

    # ── FY long format ─────────────────────────────────────────────────
    def test_fy_long_uppercase(self):
        assert normalize_year("FY2023") == 2023

    def test_fy_long_lowercase(self):
        assert normalize_year("fy2023") == 2023

    def test_fy_long_with_space(self):
        assert normalize_year("FY 2023") == 2023

    # ── FY short format ────────────────────────────────────────────────
    def test_fy_short(self):
        assert normalize_year("FY23") == 2023

    def test_fy_short_lowercase(self):
        assert normalize_year("fy24") == 2024

    # ── Slash / dash formats ───────────────────────────────────────────
    def test_slash_4_2(self):
        assert normalize_year("2023-24") == 2023

    def test_slash_4_4(self):
        assert normalize_year("2023/2024") == 2023

    def test_slash_2_2(self):
        assert normalize_year("23-24") == 2023

    def test_slash_2_2_forward(self):
        assert normalize_year("22/23") == 2022

    # ── Month-Year formats ─────────────────────────────────────────────
    def test_mon_dash_2digit(self):
        assert normalize_year("Mar-23") == 2023

    def test_mon_dash_4digit(self):
        assert normalize_year("Mar-2023") == 2023

    def test_mon_no_separator(self):
        assert normalize_year("Mar2023") == 2023

    def test_invalid_month_returns_none(self):
        assert normalize_year("Xyz-23") is None


# ═══════════════════════════════════════════════════════════════════════
# normalize_ticker() – 16 tests
# ═══════════════════════════════════════════════════════════════════════

class TestNormalizeTicker:
    """Tests for normalize_ticker()."""

    # ── Null / empty ──────────────────────────────────────────────────
    def test_none_returns_none(self):
        assert normalize_ticker(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_ticker("") is None

    def test_whitespace_only_returns_none(self):
        assert normalize_ticker("   ") is None

    # ── Exchange suffix stripping ──────────────────────────────────────
    def test_strips_ns_suffix(self):
        assert normalize_ticker("RELIANCE.NS") == "RELIANCE"

    def test_strips_bo_suffix(self):
        assert normalize_ticker("TCS.BO") == "TCS"

    def test_strips_nse_suffix(self):
        assert normalize_ticker("INFY.NSE") == "INFY"

    def test_strips_bse_suffix(self):
        assert normalize_ticker("WIPRO.BSE") == "WIPRO"

    def test_strips_eq_suffix(self):
        assert normalize_ticker("HDFCBANK-EQ") == "HDFCBANK"

    def test_strips_in_suffix(self):
        assert normalize_ticker("SBIN.IN") == "SBIN"

    # ── Case & whitespace ─────────────────────────────────────────────
    def test_uppercases_ticker(self):
        assert normalize_ticker("tcs") == "TCS"

    def test_strips_whitespace(self):
        assert normalize_ticker("  RELIANCE  ") == "RELIANCE"

    def test_lower_with_suffix(self):
        assert normalize_ticker("reliance.ns") == "RELIANCE"

    # ── Special characters ────────────────────────────────────────────
    def test_ampersand_ticker(self):
        assert normalize_ticker("M&M") == "M&M"

    def test_ampersand_with_suffix(self):
        assert normalize_ticker("M&M-EQ") == "M&M"

    def test_hyphen_ticker_bajaj(self):
        assert normalize_ticker("BAJAJ-AUTO") == "BAJAJ-AUTO"

    # ── Invalid ───────────────────────────────────────────────────────
    def test_invalid_chars_returns_none(self):
        assert normalize_ticker("INVALID TICKER!!") is None


# ═══════════════════════════════════════════════════════════════════════
# normalize_currency() – 10 tests
# ═══════════════════════════════════════════════════════════════════════

class TestNormalizeCurrency:
    """Tests for normalize_currency()."""

    def test_none_returns_none(self):
        assert normalize_currency(None) is None

    def test_na_string_returns_none(self):
        assert normalize_currency("N/A") is None

    def test_plain_float(self):
        assert normalize_currency(1234.56) == 1234.56

    def test_plain_integer(self):
        assert normalize_currency(5000) == 5000.0

    def test_string_with_commas(self):
        assert normalize_currency("1,234.56") == 1234.56

    def test_rupee_symbol(self):
        assert normalize_currency("₹1,234") == 1234.0

    def test_crore_scale(self):
        result = normalize_currency("₹ 100 Cr")
        assert result == pytest.approx(100 * 1e7)

    def test_lakh_scale(self):
        result = normalize_currency("50 L")
        assert result == pytest.approx(50 * 1e5)

    def test_negative_value(self):
        assert normalize_currency("-500") == -500.0

    def test_nan_float_returns_none(self):
        assert normalize_currency(float("nan")) is None


# ═══════════════════════════════════════════════════════════════════════
# normalize_percentage() – 9 tests
# ═══════════════════════════════════════════════════════════════════════

class TestNormalizePercentage:
    """Tests for normalize_percentage()."""

    def test_none_returns_none(self):
        assert normalize_percentage(None) is None

    def test_na_string_returns_none(self):
        assert normalize_percentage("N/A") is None

    def test_string_with_percent(self):
        assert normalize_percentage("12.5%") == pytest.approx(12.5)

    def test_string_without_percent(self):
        assert normalize_percentage("25.3") == pytest.approx(25.3)

    def test_decimal_form_float(self):
        # 0.125 → 12.5%
        assert normalize_percentage(0.125) == pytest.approx(12.5)

    def test_integer_form(self):
        assert normalize_percentage(25) == pytest.approx(25.0)

    def test_pct_suffix(self):
        assert normalize_percentage("18 pct") == pytest.approx(18.0)

    def test_negative_percentage(self):
        assert normalize_percentage("-5.5%") == pytest.approx(-5.5)

    def test_nan_returns_none(self):
        assert normalize_percentage(float("nan")) is None
