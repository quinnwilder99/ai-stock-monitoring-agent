"""
analysis/llm_analyzer.py
------------------------
Sends prompts to the Google Gemini API and parses structured JSON responses
into validated MarketEvent objects.

Model used: gemini-1.5-flash (free tier — 1,500 req/day, 15 RPM)
Get a key at: https://aistudio.google.com  →  Get API key

Key design choices
------------------
* JSON output is enforced via the system prompt schema + response_mime_type hint.
* Parse failures are captured in LLMAnalysisResult.parse_errors — bad parses
  are logged and skipped without crashing the pipeline.
* Retries once on quota/transient errors before giving up on a batch.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from google import genai
from google.genai import types

from analysis.event_schema import LLMAnalysisResult, MarketEvent
from analysis.prompts import (
    SYSTEM_PROMPT,
    build_combined_prompt,
    build_filing_analysis_prompt,
    build_price_alert_prompt,
)
from ingestion.price_feed import PriceSnapshot
from ingestion.sec_filings import SecFiling

logger = logging.getLogger(__name__)

_RETRY_DELAY = 10.0  # seconds before one retry (Gemini free tier: 15 RPM)


def _get_client() -> tuple[genai.Client, str]:
    from config import GEMINI_API_KEY, GEMINI_MODEL
    client = genai.Client(api_key=GEMINI_API_KEY)
    return client, GEMINI_MODEL


def _call_gemini(user_message: str) -> str:
    """Single Gemini API call with one retry on transient/quota failure."""
    client, model_name = _get_client()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        temperature=0.1,
        max_output_tokens=2048,
    )
    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_message,
                config=config,
            )
            return response.text
        except Exception as exc:
            err = str(exc).lower()
            if attempt == 0 and any(k in err for k in ("quota", "429", "resource", "timeout")):
                logger.warning("Gemini transient error (%s), retrying in %.0fs…", exc, _RETRY_DELAY)
                time.sleep(_RETRY_DELAY)
            else:
                raise


def _messages_to_text(messages: list[dict]) -> str:
    """
    The prompt builders return OpenAI-style message lists for portability.
    Gemini's generate_content takes a plain string — collapse the user turn.
    """
    parts = []
    for msg in messages:
        if msg.get("role") == "user":
            parts.append(msg["content"])
    return "\n\n".join(parts)


def _parse_response(raw: str) -> tuple[list[MarketEvent], list[str]]:
    """
    Parse Gemini's JSON response into MarketEvent objects.
    Returns (valid_events, parse_error_messages).
    """
    events: list[MarketEvent] = []
    errors: list[str] = []

    # Strip markdown code fences if Gemini adds them despite response_mime_type
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.startswith("```")).strip()

    try:
        data: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append(f"JSON parse failed: {exc} | raw={raw[:200]}")
        return events, errors

    raw_events = data.get("events", [])
    if not isinstance(raw_events, list):
        errors.append("'events' field is not a list")
        return events, errors

    for i, ev_data in enumerate(raw_events):
        try:
            event = MarketEvent(**ev_data)
            events.append(event)
        except Exception as exc:
            errors.append(f"Event[{i}] validation error: {exc}")

    return events, errors


# ── Public analysis functions ─────────────────────────────────────────────────

def analyze_price_snapshots(
    snapshots: list[PriceSnapshot],
    model: str | None = None,
) -> LLMAnalysisResult:
    """
    Sends all price spike snapshots in a single prompt and returns extracted events.
    Only called when at least one snapshot has is_spike == True to save API quota.
    """
    from config import MIN_CONFIDENCE

    spike_snapshots = [s for s in snapshots if s.is_spike]
    if not spike_snapshots:
        return LLMAnalysisResult()

    payload = [
        {
            "ticker": s.ticker,
            "price": round(s.price, 4),
            "prev_close": round(s.prev_close, 4),
            "pct_change": round(s.pct_change, 6),
            "volume": s.volume,
            "source": s.source,
        }
        for s in spike_snapshots
    ]

    logger.info("Analyzing %d price spike(s) with Gemini…", len(spike_snapshots))
    messages = build_price_alert_prompt(payload)
    raw = _call_gemini(_messages_to_text(messages))
    events, errors = _parse_response(raw)

    filtered = [e for e in events if e.confidence >= MIN_CONFIDENCE]
    logger.info(
        "Price analysis: %d events extracted, %d above confidence threshold",
        len(events), len(filtered),
    )
    return LLMAnalysisResult(events=filtered, raw_response=raw, parse_errors=errors)


def analyze_sec_filing(
    filing: SecFiling,
    model: str | None = None,
) -> LLMAnalysisResult:
    """Analyzes a single SEC filing and returns extracted events."""
    from config import MIN_CONFIDENCE

    if not filing.raw_text:
        logger.debug("Empty text for %s %s — skipping LLM call", filing.ticker, filing.form_type)
        return LLMAnalysisResult()

    logger.info("Analyzing %s %s filing for %s…", filing.form_type, filing.accession, filing.ticker)
    messages = build_filing_analysis_prompt(
        ticker=filing.ticker,
        form_type=filing.form_type,
        filed_date=filing.filed_date.strftime("%Y-%m-%d"),
        description=filing.description or "",
        text_excerpt=filing.raw_text,
        source_url=filing.full_text_url,
    )
    raw = _call_gemini(_messages_to_text(messages))
    events, errors = _parse_response(raw)

    filtered = [e for e in events if e.confidence >= MIN_CONFIDENCE]
    return LLMAnalysisResult(events=filtered, raw_response=raw, parse_errors=errors)


def analyze_combined(
    ticker: str,
    snapshot: PriceSnapshot,
    filing: SecFiling,
    model: str | None = None,
) -> LLMAnalysisResult:
    """
    When a ticker has both a price spike and a new filing on the same run,
    ask Gemini to reason about whether they're related.
    """
    from config import MIN_CONFIDENCE

    price_data = {
        "ticker": snapshot.ticker,
        "price": round(snapshot.price, 4),
        "prev_close": round(snapshot.prev_close, 4),
        "pct_change": round(snapshot.pct_change, 6),
    }
    filing_summary = filing.description or filing.raw_text[:500]

    messages = build_combined_prompt(ticker, price_data, filing_summary)
    raw = _call_gemini(_messages_to_text(messages))
    events, errors = _parse_response(raw)

    filtered = [e for e in events if e.confidence >= MIN_CONFIDENCE]
    return LLMAnalysisResult(events=filtered, raw_response=raw, parse_errors=errors)
