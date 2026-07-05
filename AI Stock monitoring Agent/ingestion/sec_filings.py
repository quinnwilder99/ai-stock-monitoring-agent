"""
ingestion/sec_filings.py
------------------------
Pulls recent SEC filings for watchlist companies from the EDGAR full-text
search API and the company submissions endpoint. No API key required.

Public API
----------
    get_recent_filings(tickers: list[str], form_types: list[str], days_back: int)
        -> list[SecFiling]
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# SEC requires a descriptive User-Agent per their fair-use policy
_HEADERS = {
    "User-Agent": "AI-Stock-Monitor contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}
_EDGAR_BASE = "https://data.sec.gov"
_RATE_DELAY = 0.12  # 10 req/s limit per SEC fair-use guidelines


@dataclass
class SecFiling:
    ticker: str
    cik: str                       # Central Index Key (zero-padded to 10 digits)
    accession: str                 # Hyphen-formatted accession number
    form_type: str                 # e.g. "8-K", "10-Q"
    filed_date: datetime
    description: Optional[str]     # Filing description from EDGAR
    full_text_url: str             # Direct link to primary document
    raw_text: str = field(default="", repr=False)   # Fetched body text (trimmed)

    def short_summary(self) -> str:
        return (
            f"[{self.form_type}] {self.ticker} filed {self.filed_date.date()} — "
            f"{self.description or 'No description'}"
        )


# ── CIK lookup ────────────────────────────────────────────────────────────────

_TICKER_TO_CIK: dict[str, str] = {}   # in-process cache


def _resolve_cik(ticker: str) -> Optional[str]:
    if ticker in _TICKER_TO_CIK:
        return _TICKER_TO_CIK[ticker]
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for entry in data.values():
            t = entry.get("ticker", "").upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            _TICKER_TO_CIK[t] = cik
        return _TICKER_TO_CIK.get(ticker)
    except Exception as exc:
        logger.warning("CIK lookup failed for %s: %s", ticker, exc)
        return None


# ── Filing fetch ──────────────────────────────────────────────────────────────

def _get_submissions(cik: str) -> list[dict]:
    """Returns raw filing entries from EDGAR submissions endpoint."""
    url = f"{_EDGAR_BASE}/submissions/CIK{cik}.json"
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    recent = data.get("filings", {}).get("recent", {})

    entries = []
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    descs = recent.get("primaryDocument", [])
    primary_descs = recent.get("primaryDocDescription", [])

    for i in range(len(forms)):
        entries.append(
            {
                "form": forms[i],
                "date": dates[i],
                "accession": accessions[i],
                "primaryDoc": descs[i],
                "description": primary_descs[i] if i < len(primary_descs) else "",
            }
        )
    return entries


def _fetch_filing_text(cik: str, accession: str, primary_doc: str) -> tuple[str, str]:
    """Returns (full_text_url, truncated_text) for a filing."""
    acc_clean = accession.replace("-", "")
    base_url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}"
        f"/{acc_clean}/{primary_doc}"
    )
    try:
        resp = requests.get(base_url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        text = resp.text
        # Strip HTML tags simply; keep first 8 000 chars for LLM context
        from html.parser import HTMLParser

        class _Stripper(HTMLParser):
            def __init__(self):
                super().__init__()
                self._parts: list[str] = []
            def handle_data(self, data):
                self._parts.append(data)
            def get_text(self) -> str:
                return " ".join(self._parts)

        stripper = _Stripper()
        stripper.feed(text)
        plain = stripper.get_text()[:8_000]
        return base_url, plain
    except Exception as exc:
        logger.debug("Could not fetch filing text from %s: %s", base_url, exc)
        return base_url, ""


def get_recent_filings(
    tickers: list[str],
    form_types: list[str] | None = None,
    days_back: int = 3,
) -> list[SecFiling]:
    """
    Returns filings for each ticker filed within the last `days_back` days.
    Defaults to 8-K (material events) and 10-Q (quarterly earnings).
    """
    if form_types is None:
        form_types = ["8-K", "10-Q", "10-K"]

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    results: list[SecFiling] = []

    for ticker in tickers:
        cik = _resolve_cik(ticker)
        if not cik:
            logger.warning("No CIK found for %s — skipping SEC filings", ticker)
            continue

        try:
            time.sleep(_RATE_DELAY)
            entries = _get_submissions(cik)
        except Exception as exc:
            logger.error("EDGAR submissions failed for %s: %s", ticker, exc)
            continue

        for entry in entries:
            if entry["form"] not in form_types:
                continue
            try:
                filed_dt = datetime.fromisoformat(entry["date"]).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
            if filed_dt < cutoff:
                continue  # older than our window

            time.sleep(_RATE_DELAY)
            url, text = _fetch_filing_text(cik, entry["accession"], entry["primaryDoc"])

            results.append(
                SecFiling(
                    ticker=ticker,
                    cik=cik,
                    accession=entry["accession"],
                    form_type=entry["form"],
                    filed_date=filed_dt,
                    description=entry["description"],
                    full_text_url=url,
                    raw_text=text,
                )
            )
            logger.info("Fetched %s filing for %s (%s)", entry["form"], ticker, entry["date"])

    return results
