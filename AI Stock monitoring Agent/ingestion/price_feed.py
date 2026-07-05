"""
ingestion/price_feed.py
-----------------------
Fetches current price data for the watchlist using the EODHD real-time API.

Endpoint used:
    GET https://eodhd.com/api/real-time/{TICKER}.US?api_token={KEY}&fmt=json

Free plan limits: 20 API calls/day. This module makes 1 call per ticker.
Use the DEMO key (set EODHD_API_KEY=demo in .env) to test with:
    AAPL, TSLA, AMZN, VTI, BTC-USD (crypto), EURUSD (forex)

Public API
----------
    get_price_snapshots(tickers: list[str]) -> list[PriceSnapshot]
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://eodhd.com/api/real-time"
_RATE_DELAY = 0.2   # 5 req/s to stay within EODHD fair-use limits


@dataclass
class PriceSnapshot:
    ticker: str
    price: float                        # Latest close / real-time price
    prev_close: float                   # Previous session close
    pct_change: float                   # (price - prev_close) / prev_close
    volume: int
    market_cap: Optional[float]         # Not available on real-time endpoint; always None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "eodhd"

    @property
    def is_spike(self) -> bool:
        """True when absolute % move exceeds the configured threshold."""
        from config import PRICE_SPIKE_THRESHOLD
        return abs(self.pct_change) >= PRICE_SPIKE_THRESHOLD

    def summary(self) -> str:
        direction = "▲" if self.pct_change >= 0 else "▼"
        return (
            f"{self.ticker}: ${self.price:.2f} "
            f"{direction}{abs(self.pct_change) * 100:.2f}% "
            f"(prev close ${self.prev_close:.2f})"
        )


def _fetch_realtime(ticker: str, api_key: str) -> Optional[PriceSnapshot]:
    """
    Fetches a single ticker from the EODHD real-time endpoint.

    Response fields of interest:
        close         — latest price (real-time or most recent close)
        previousClose — prior session close
        change_p      — percentage change (string like "0.52")
        volume        — today's volume
    """
    # EODHD uses {TICKER}.US suffix for US equities
    symbol = f"{ticker}.US" if "." not in ticker else ticker
    url = f"{_BASE_URL}/{symbol}"
    params = {"api_token": api_key, "fmt": "json"}

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        price = float(data.get("close") or data.get("open") or 0)
        prev_close = float(data.get("previousClose") or price)

        # EODHD returns change_p as a float percentage (e.g. 1.23 means +1.23%)
        raw_pct = data.get("change_p")
        if raw_pct is not None:
            pct_change = float(raw_pct) / 100.0
        else:
            pct_change = (price - prev_close) / prev_close if prev_close else 0.0

        volume = int(data.get("volume") or 0)

        logger.debug("EODHD real-time OK: %s → $%.2f (%.2f%%)", ticker, price, pct_change * 100)
        return PriceSnapshot(
            ticker=ticker,
            price=price,
            prev_close=prev_close,
            pct_change=pct_change,
            volume=volume,
            market_cap=None,
        )

    except requests.HTTPError as exc:
        logger.warning("EODHD HTTP error for %s: %s", ticker, exc)
    except Exception as exc:
        logger.warning("EODHD fetch failed for %s: %s", ticker, exc)

    return None


def get_price_snapshots(tickers: list[str]) -> list[PriceSnapshot]:
    """
    Main entry point. Returns a PriceSnapshot for each ticker that could be fetched.
    Skips tickers where the API call fails (logs a warning).

    Note on free plan usage: each ticker costs 1 API call. With 20 calls/day and
    a 15-minute check interval (96 runs/day), limit your watchlist to ≤1 ticker
    on the free plan, or register for a paid plan for broader coverage.
    To test without burning quota, use EODHD_API_KEY=demo with AAPL/TSLA/AMZN.
    """
    from config import EODHD_API_KEY

    snapshots: list[PriceSnapshot] = []
    for ticker in tickers:
        snap = _fetch_realtime(ticker, EODHD_API_KEY)
        if snap:
            snapshots.append(snap)
        time.sleep(_RATE_DELAY)

    return snapshots
