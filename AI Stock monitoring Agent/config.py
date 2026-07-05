"""
config.py — centralised settings loaded from environment / .env
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (one level up from this file if run as module,
# or the current working directory when run directly).
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"Missing required environment variable: {key}\n"
            f"Copy .env.example → .env and fill in your credentials."
        )
    return val


def _csv_list(key: str, default: str = "") -> list[str]:
    raw = os.getenv(key, default)
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


# ── Google Gemini ──────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = _require("GEMINI_API_KEY")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# ── EODHD ──────────────────────────────────────────────────────────────────────
EODHD_API_KEY: str = os.getenv("EODHD_API_KEY", "demo")

# ── Email ──────────────────────────────────────────────────────────────────────
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = _require("SMTP_USER")
SMTP_PASSWORD: str = _require("SMTP_PASSWORD")
ALERT_RECIPIENTS: list[str] = _csv_list("ALERT_RECIPIENTS")

# ── Monitoring ─────────────────────────────────────────────────────────────────
WATCHLIST: list[str] = _csv_list("WATCHLIST", "AAPL,TSLA,AMZN")
PRICE_SPIKE_THRESHOLD: float = float(os.getenv("PRICE_SPIKE_THRESHOLD", "0.05"))
MIN_CONFIDENCE: float = float(os.getenv("MIN_CONFIDENCE", "0.7"))
CHECK_INTERVAL_MINUTES: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "15"))

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT_DIR: Path = Path(__file__).parent
DATA_DIR: Path = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
SEEN_EVENTS_PATH: Path = DATA_DIR / "seen_events.json"
