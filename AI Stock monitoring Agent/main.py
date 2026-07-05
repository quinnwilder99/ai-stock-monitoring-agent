"""
main.py — entry point for the AI Stock Monitoring Agent
---------------------------------------------------------
Starts APScheduler to run MonitorAgent on a cron-based interval.

Usage
-----
    python main.py                  # run scheduler (blocks)
    python main.py --once           # single run and exit
    python main.py --once --dry-run # single run, no email
    python main.py --once TSLA NVDA # single run for specific tickers

Environment
-----------
Set CHECK_INTERVAL_MINUTES in .env to control schedule frequency.
All credentials (ANTHROPIC_API_KEY, SMTP_*, POLYGON_API_KEY) must be
set in .env before running.
"""
from __future__ import annotations

import logging
import signal
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def _parse_args() -> tuple[bool, bool, list[str] | None]:
    """Returns (once, dry_run, tickers_override)."""
    args = sys.argv[1:]
    once = "--once" in args
    dry = "--dry-run" in args
    tickers = [a.upper() for a in args if not a.startswith("-")] or None
    return once, dry, tickers


def _run_once(dry_run: bool, tickers: list[str] | None) -> None:
    from agents.monitor_agent import MonitorAgent
    agent = MonitorAgent(watchlist=tickers, dry_run=dry_run)
    agent.run()


def _run_scheduler(dry_run: bool) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore
    from apscheduler.triggers.interval import IntervalTrigger  # type: ignore

    from agents.monitor_agent import MonitorAgent
    from config import CHECK_INTERVAL_MINUTES, WATCHLIST

    scheduler = BlockingScheduler(timezone="UTC")

    def job():
        logger.info("Scheduled run started at %s", datetime.now(timezone.utc).isoformat())
        agent = MonitorAgent(dry_run=dry_run)
        agent.run()

    scheduler.add_job(
        job,
        trigger=IntervalTrigger(minutes=CHECK_INTERVAL_MINUTES),
        id="monitor_agent",
        name="AI Stock Monitor",
        max_instances=1,           # prevent overlapping runs
        misfire_grace_time=60,
        replace_existing=True,
    )

    logger.info(
        "Scheduler started — checking every %d min for tickers: %s",
        CHECK_INTERVAL_MINUTES, WATCHLIST,
    )

    # Run once immediately on startup
    logger.info("Running initial cycle…")
    job()

    # Graceful shutdown on SIGINT / SIGTERM
    def _stop(sig, frame):
        logger.info("Shutdown signal received — stopping scheduler")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    scheduler.start()


if __name__ == "__main__":
    once, dry_run, tickers = _parse_args()

    if once:
        logger.info("Single-run mode%s", " (dry)" if dry_run else "")
        _run_once(dry_run=dry_run, tickers=tickers)
    else:
        logger.info("Scheduler mode%s", " (dry)" if dry_run else "")
        _run_scheduler(dry_run=dry_run)
