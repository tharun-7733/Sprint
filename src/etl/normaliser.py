"""
normaliser.py – Data normalisation utilities for the ETL pipeline.

Provides pure-function normalizers for year, ticker, currency, and percentage
values extracted from Excel source files. All functions are idempotent, type-
annotated, and return None on unrecoverable input rather than raising.
"""

from __future__ import annotations

import re
import logging
from typing import Optional, Union

logger = logging.getLogger(__name__)

# ─── Year Normalisation ───────────────────────────────────────────────────────

# Mapping of month abbreviations → fiscal year offset rule (used for "Mar-23" style)
_MONTH_ABBR: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Pre-compiled regex patterns for year normalization
_RE_FY_LONG   = re.compile(r"^FY\s*(\d{4})$", re.IGNORECASE)          # FY2023
_RE_FY_SHORT  = re.compile(r"^FY\s*(\d{2})$", re.IGNORECASE)          # FY23
_RE_SLASH4    = re.compile(r"^(\d{4})[/-](\d{2,4})$")                  # 2023-24 or 2023/24
_RE_SLASH2    = re.compile(r"^(\d{2})[/-](\d{2})$")                    # 23-24
_RE_MON_YEAR  = re.compile(r"^([A-Za-z]{3})[- ]?(\d{2,4})$")          # Mar-23 or Mar2023
_RE_BARE4     = re.compile(r"^(\d{4})$")                               # 2023
_RE_BARE2     = re.compile(r"^(\d{2})$")                               # 23


def normalize_year(raw: Union[str, int, float, None]) -> Optional[int]:
    """
    Normalise a raw year value to a 4-digit integer representing the
    fiscal / calendar year.

    Supported formats
    -----------------
    - Integer / float : 2023, 2023.0
    - FY long         : "FY2023", "fy 2023"
    - FY short        : "FY23", "fy23"
    - Slash / dash    : "2023-24", "2023/24", "23-24", "23/24"
    - Month-Year      : "Mar-23", "Mar2023", "Mar 2023"
    - Bare 4-digit    : "2023"
    - Bare 2-digit    : "23"  (treated as 20xx)

    Returns None for null, empty, or unrecognisable input.

    Convention
    ----------
    For slash formats (e.g., 2023-24), the *first* year is returned, which
    represents the fiscal year start. "Mar-23" is treated as FY ending March
    2023, so year = 2023.

    Examples
    --------
    >>> normalize_year("FY2023")
    2023
    >>> normalize_year("2023-24")
    2023
    >>> normalize_year("Mar-23")
    2023
    >>> normalize_year(2023.0)
    2023
    >>> normalize_year(None)
    None
    """
    if raw is None:
        return None

    # Numeric types (int / float)
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and raw != raw:  # NaN check
            return None
        yr = int(raw)
        if 1900 <= yr <= 2100:
            return yr
        if 10 <= yr <= 99:  # short form
            return 2000 + yr
        logger.warning("normalize_year: out-of-range numeric year %s", raw)
        return None

    text = str(raw).strip()
    if not text:
        return None

    # FY2023
    m = _RE_FY_LONG.match(text)
    if m:
        yr = int(m.group(1))
        return yr if 1900 <= yr <= 2100 else None

    # FY23
    m = _RE_FY_SHORT.match(text)
    if m:
        return 2000 + int(m.group(1))

    # 2023-24  /  2023/24
    m = _RE_SLASH4.match(text)
    if m:
        yr = int(m.group(1))
        return yr if 1950 <= yr <= 2100 else None

    # 23-24  /  23/24
    m = _RE_SLASH2.match(text)
    if m:
        return 2000 + int(m.group(1))

    # Mar-23  /  Mar 23  /  Mar2023
    m = _RE_MON_YEAR.match(text)
    if m:
        mon_str = m.group(1).lower()
        yr_str  = m.group(2)
        if mon_str not in _MONTH_ABBR:
            logger.warning("normalize_year: unknown month abbreviation %s in %r", mon_str, raw)
            return None
        yr = int(yr_str)
        if len(yr_str) == 2:
            yr = 2000 + yr
        return yr if 1900 <= yr <= 2100 else None

    # Bare 4-digit
    m = _RE_BARE4.match(text)
    if m:
        yr = int(m.group(1))
        return yr if 1900 <= yr <= 2100 else None

    # Bare 2-digit
    m = _RE_BARE2.match(text)
    if m:
        return 2000 + int(m.group(1))

    logger.warning("normalize_year: unrecognised format %r", raw)
    return None


# ─── Ticker Normalisation ─────────────────────────────────────────────────────

# Known exchange suffixes to strip
_EXCHANGE_SUFFIXES = {
    ".NS", ".BO", ".BSE", ".NSE", ".IN", "-EQ", "-BE",
    " NS", " BO", " NSE", " BSE",
}
_RE_SUFFIX = re.compile(
    r"[.\-\s](?:NS|BO|BSE|NSE|IN|EQ|BE)$", re.IGNORECASE
)
_RE_TICKER_VALID = re.compile(r"^[A-Z0-9&][A-Z0-9&-]{0,19}$")


def normalize_ticker(raw: Union[str, None]) -> Optional[str]:
    """
    Normalise a raw ticker symbol to a clean, uppercase string.

    Operations performed
    --------------------
    1. Strip leading / trailing whitespace.
    2. Convert to uppercase.
    3. Remove known exchange suffixes (.NS, .BO, -EQ, etc.).
    4. Strip internal whitespace surrounding special characters.
    5. Validate the result matches [A-Z0-9&]{1,20}.

    Returns None for null, empty, or invalid input.

    Examples
    --------
    >>> normalize_ticker("RELIANCE.NS")
    'RELIANCE'
    >>> normalize_ticker(" tcs.bo ")
    'TCS'
    >>> normalize_ticker("M&M-EQ")
    'M&M'
    >>> normalize_ticker(None)
    None
    >>> normalize_ticker("INVALID TICKER!!")
    None
    """
    if raw is None:
        return None

    text = str(raw).strip().upper()
    if not text:
        return None

    # Remove exchange suffix
    text = _RE_SUFFIX.sub("", text).strip()

    # Remove any residual trailing dots/hyphens
    text = text.rstrip(".-")

    if not text:
        return None

    # Validate
    if not _RE_TICKER_VALID.match(text):
        logger.warning("normalize_ticker: invalid ticker after normalisation %r (original: %r)", text, raw)
        return None

    return text


# ─── Currency Normalisation ───────────────────────────────────────────────────

_RE_CURRENCY = re.compile(
    r"^\s*[₹$€£]?\s*"               # optional currency symbol
    r"([+-]?\d[\d,]*(?:\.\d+)?)"    # numeric value
    r"\s*(?:Cr|L|K|M|B|T)?\s*$",   # optional scale suffix
    re.IGNORECASE,
)
_SCALE_MAP: dict[str, float] = {
    "cr": 1e7,    # Indian Crore → absolute
    "l":  1e5,    # Indian Lakh → absolute
    "k":  1e3,
    "m":  1e6,
    "b":  1e9,
    "t":  1e12,
}


def normalize_currency(raw: Union[str, int, float, None], scale: str = "") -> Optional[float]:
    """
    Normalise a currency value, optionally applying a scale suffix.

    Parameters
    ----------
    raw   : raw value (string, int, or float)
    scale : default scale suffix if none present in raw ("Cr", "L", "M", etc.)

    Returns float in absolute units, or None on failure.

    Examples
    --------
    >>> normalize_currency("₹ 1,234.56 Cr")
    12345600000.0
    >>> normalize_currency(1234.56)
    1234.56
    >>> normalize_currency("N/A")
    None
    """
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and raw != raw:  # NaN
            return None
        val = float(raw)
        if scale:
            val *= _SCALE_MAP.get(scale.lower(), 1.0)
        return val

    text = str(raw).strip()
    if not text or text.lower() in ("n/a", "na", "-", "nil", ""):
        return None

    m = _RE_CURRENCY.match(text)
    if not m:
        logger.warning("normalize_currency: unrecognised format %r", raw)
        return None

    num_str = m.group(1).replace(",", "")
    value   = float(num_str)

    # Detect scale suffix in original string
    suffix_match = re.search(r"(Cr|L|K|M|B|T)\s*$", text, re.IGNORECASE)
    if suffix_match:
        value *= _SCALE_MAP.get(suffix_match.group(1).lower(), 1.0)
    elif scale:
        value *= _SCALE_MAP.get(scale.lower(), 1.0)

    return value


# ─── Percentage Normalisation ─────────────────────────────────────────────────

_RE_PCT = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*%?\s*$")


def normalize_percentage(raw: Union[str, int, float, None]) -> Optional[float]:
    """
    Normalise a percentage to a float in the range [typically -100, 100].

    Accepts values like "12%", "12.5 pct", 0.125 (stored as decimal), 12.5.

    Heuristic: if abs(value) <= 1.0 and raw contains no "%" character,
    the value is assumed to already be in decimal form (e.g., 0.125 → 12.5).

    Returns float percentage (e.g., 12.5 for 12.5%), or None on failure.

    Examples
    --------
    >>> normalize_percentage("12.5%")
    12.5
    >>> normalize_percentage(0.125)
    12.5
    >>> normalize_percentage("N/A")
    None
    """
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and raw != raw:  # NaN
            return None
        val = float(raw)
        # Heuristic: decimal form detection
        if abs(val) <= 1.0:
            return round(val * 100.0, 6)
        return round(val, 6)

    text = str(raw).strip().lower()
    if not text or text in ("n/a", "na", "-", "nil"):
        return None

    # Strip "pct" suffix
    text = re.sub(r"\s*pct\s*$", "", text)

    has_pct_symbol = "%" in text
    text = text.replace("%", "").strip()

    m = _RE_PCT.match(text)
    if not m:
        logger.warning("normalize_percentage: unrecognised format %r", raw)
        return None

    val = float(m.group(1))
    if not has_pct_symbol and abs(val) <= 1.0:
        val = round(val * 100.0, 6)

    return round(val, 6)
