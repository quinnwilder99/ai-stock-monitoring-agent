"""
agents/monitor_agent.py
-----------------------
Main orchestration layer — ties ingestion → analysis → dedup → alert.

One call to MonitorAgent.run() completes a full monitoring cycle:
1. Fetch price snapshots for the watchlist
2. Fetch new SEC filings (last N days)
3. Identify tickers with both a price spike AND a new filing → combined analysis
4. Analyze remaining spikes and filings independently
5. Deduplicate across the session
6. Send email digest for any new material events

Designed to be called by the scheduler, but also runnable standalone for
testing with: python -m agents.monitor_agent
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from alerts.deduplicator import Deduplicator
from alerts.email_sender import send_alert_digest
from analysis.event_schema import MarketEvent
from analysis.llm_analyzer import (
    analyze_combined,
    analyze_price_snapshots,
    analyze_sec_filing,
)
from ingestion.price_feed import PriceSnapshot, get_price_snapshots
from ingestion.sec_filings import SecFiling, get_recent_filings

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    tickers_checked: int = 0
    price_spikes_found: int = 0
    filings_found: int = 0
    events_extracted: int = 0
    events_after_dedup: int = 0
    email_sent: bool = False
    errors: list[str] = field(default_factory=list)

    def log(self) -> None:
        duration = (
            (self.finished_at - self.started_at).total_seconds()
            if self.finished_at else "—"
        )
        logger.info(
            "Run complete | tickers=%d spikes=%d filings=%d events=%d "
            "new=%d email=%s duration=%.1fs",
            self.tickers_checked, self.price_spikes_found, self.filings_found,
            self.events_extracted, self.events_after_dedup, self.email_sent,
            duration if isinstance(duration, float) else 0,
        )


class MonitorAgent:
    def __init__(
        self,
        watchlist: list[str] | None = None,
        sec_days_back: int = 2,
        dry_run: bool = False,
    ):
        """
        Parameters
        ----------
        watchlist    : override config.WATCHLIST (useful for testing)
        sec_days_back: how many days of SEC filings to pull each cycle
        dry_run      : if True, logs events but does not send email
        """
        from config import SEEN_EVENTS_PATH, WATCHLIST

        self.watchlist = watchlist or WATCHLIST
        self.sec_days_back = sec_days_back
        self.dry_run = dry_run
        self._dedup = Deduplicator(SEEN_EVENTS_PATH)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _analyze_all(
        self,
        snapshots: list[PriceSnapshot],
        filings: list[SecFiling],
        summary: RunSummary,
    ) -> list[MarketEvent]:
        all_events: list[MarketEvent] = []

        spike_tickers = {s.ticker for s in snapshots if s.is_spike}
        filing_tickers = {f.ticker for f in filings}

        # Tickers with BOTH a spike and a filing → combined analysis
        combined_tickers = spike_tickers & filing_tickers
        for ticker in combined_tickers:
            snap = next(s for s in snapshots if s.ticker == ticker)
            filing = next(f for f in filings if f.ticker == ticker)
            try:
                result = analyze_combined(ticker, snap, filing)
                all_events.extend(result.events)
                if result.parse_errors:
                    summary.errors.extend(result.parse_errors)
            except Exception as exc:
                logger.error("Combined analysis failed for %s: %s", ticker, exc)
                summary.errors.append(f"combined:{ticker}:{exc}")

        # Price-only spikes (no corresponding filing)
        price_only = [s for s in snapshots if s.ticker not in combined_tickers]
        if any(s.is_spike for s in price_only):
            try:
                result = analyze_price_snapshots(price_only)
                all_events.extend(result.events)
                if result.parse_errors:
                    summary.errors.extend(result.parse_errors)
            except Exception as exc:
                logger.error("Price analysis failed: %s", exc)
                summary.errors.append(f"price_analysis:{exc}")

        # Filing-only (no corresponding spike)
        for filing in filings:
            if filing.ticker in combined_tickers:
                continue
            try:
                result = analyze_sec_filing(filing)
                all_events.extend(result.events)
                if result.parse_errors:
                    summary.errors.extend(result.parse_errors)
            except Exception as exc:
                logger.error("Filing analysis failed for %s: %s", filing.ticker, exc)
                summary.errors.append(f"filing:{filing.ticker}:{exc}")

        return all_events

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self) -> RunSummary:
        summary = RunSummary()
        logger.info(
            "MonitorAgent starting — watchlist=%s dry_run=%s",
            self.watchlist, self.dry_run,
        )

        # 1. Fetch price data
        try:
            snapshots = get_price_snapshots(self.watchlist)
            summary.tickers_checked = len(snapshots)
            summary.price_spikes_found = sum(1 for s in snapshots if s.is_spike)
            for s in snapshots:
                logger.info("Price: %s", s.summary())
        except Exception as exc:
            logger.error("Price ingestion failed: %s", exc)
            summary.errors.append(f"price_ingestion:{exc}")
            snapshots = []

        # 2. Fetch SEC filings
        try:
            filings = get_recent_filings(self.watchlist, days_back=self.sec_days_back)
            summary.filings_found = len(filings)
            for f in filings:
                logger.info("Filing: %s", f.short_summary())
        except Exception as exc:
            logger.error("SEC ingestion failed: %s", exc)
            summary.errors.append(f"sec_ingestion:{exc}")
            filings = []

        # 3. LLM analysis
        if snapshots or filings:
            raw_events = self._analyze_all(snapshots, filings, summary)
            summary.events_extracted = len(raw_events)
        else:
            raw_events = []

        # 4. Deduplication
        new_events = self._dedup.filter_new(raw_events)
        summary.events_after_dedup = len(new_events)

        # 5. Alert
        if new_events:
            if self.dry_run:
                logger.info("DRY RUN — would send %d event(s):", len(new_events))
                for e in new_events:
                    logger.info("  [%s] %s", e.severity.upper(), e.headline)
            else:
                summary.email_sent = send_alert_digest(new_events)
        else:
            logger.info("No new material events — no alert sent")

        summary.finished_at = datetime.now(timezone.utc)
        summary.log()
        return summary


# ── Standalone test run ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    dry = "--dry-run" in sys.argv
    tickers = [a.upper() for a in sys.argv[1:] if not a.startswith("-")] or None
    agent = MonitorAgent(watchlist=tickers, dry_run=dry)
    agent.run()
