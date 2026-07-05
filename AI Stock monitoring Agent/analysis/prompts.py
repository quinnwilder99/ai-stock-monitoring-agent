"""
analysis/prompts.py
-------------------
Constrained prompt templates for the Claude API.
Each function returns a ready-to-send message list.

Design principles
-----------------
* Output is ALWAYS valid JSON — enforced by schema + post-parse validation.
* Confidence scoring forces the model to self-assess signal quality.
* Explicit dedup instruction reduces alert fatigue.
* Minimal tokens in the system prompt; all context goes in the user turn.
"""
from __future__ import annotations

import json
from textwrap import dedent

from analysis.event_schema import EventType, Severity

# ── JSON schema injected into every prompt ────────────────────────────────────

_EVENT_SCHEMA = {
    "type": "object",
    "required": [
        "event_type", "severity", "ticker", "related_tickers",
        "confidence", "is_novel", "headline", "summary",
        "key_metrics", "source_url", "raw_source_type",
    ],
    "properties": {
        "event_type": {"type": "string", "enum": [e.value for e in EventType]},
        "severity": {"type": "string", "enum": [e.value for e in Severity]},
        "ticker": {"type": "string"},
        "related_tickers": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "is_novel": {"type": "boolean"},
        "headline": {"type": "string", "maxLength": 120},
        "summary": {"type": "string"},
        "key_metrics": {"type": "object"},
        "source_url": {"type": ["string", "null"]},
        "raw_source_type": {
            "type": "string",
            "enum": ["price_feed", "sec_filing", "combined"],
        },
    },
}

_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["events"],
    "properties": {
        "events": {
            "type": "array",
            "items": _EVENT_SCHEMA,
            "description": "Zero or more material events detected. Empty list if no signal.",
        }
    },
}

SCHEMA_STR = json.dumps(_RESPONSE_SCHEMA, indent=2)

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = dedent("""
    You are a quantitative financial analyst and market surveillance AI.
    Your job is to analyze raw market data and regulatory filings, then
    extract only MATERIAL events worth alerting a fund manager about.

    Output rules — non-negotiable:
    1. Respond ONLY with valid JSON matching the provided schema. No prose before or after.
    2. Set confidence = 0–1 reflecting how certain you are this is a real, material signal.
       Use < 0.5 for ambiguous/low-signal events.
    3. Set is_novel = false if the event is a duplicate, rumour without confirmation,
       or an immaterial routine update.
    4. Include an empty events array [] if there is nothing material to report.
    5. Be conservative: a missed signal is better than a false alert.
    6. key_metrics must contain only verified numbers from the source text.
       Never fabricate figures.

    Output schema:
""").strip() + "\n\n" + SCHEMA_STR


# ── User-turn builders ────────────────────────────────────────────────────────

def build_price_alert_prompt(snapshots: list[dict]) -> list[dict]:
    """
    snapshots: list of dicts with keys:
        ticker, price, prev_close, pct_change, volume, market_cap, source
    """
    body = json.dumps(snapshots, indent=2)
    user_content = dedent(f"""
        Analyze the following real-time price snapshots for material events
        (unusual moves, volume anomalies, potential news catalysts).

        Price data:
        {body}

        Flag a price_spike event when |pct_change| ≥ 5%.
        Report other anomalies only if confidence ≥ 0.7.
        Return JSON only.
    """).strip()
    return [{"role": "user", "content": user_content}]


def build_filing_analysis_prompt(
    ticker: str,
    form_type: str,
    filed_date: str,
    description: str,
    text_excerpt: str,
    source_url: str,
) -> list[dict]:
    """
    Constructs the user turn for an SEC filing analysis.
    """
    user_content = dedent(f"""
        Analyze the following SEC {form_type} filing for {ticker}.
        Filed: {filed_date}
        Description: {description}
        Source: {source_url}

        Filing excerpt (first 8,000 characters):
        ---
        {text_excerpt}
        ---

        Extract any material events (earnings surprises, guidance changes,
        leadership changes, M&A activity, legal/regulatory risks, or other
        items a fund manager must know).

        Set raw_source_type = "sec_filing".
        Return JSON only.
    """).strip()
    return [{"role": "user", "content": user_content}]


def build_combined_prompt(
    ticker: str,
    price_data: dict,
    filing_summary: str,
) -> list[dict]:
    """
    For tickers where we have both a price move AND a new filing — lets the
    model reason about whether the price action is explained by the filing.
    """
    user_content = dedent(f"""
        You have both a price event AND a new SEC filing for {ticker}.
        Determine whether the price action is explained by the filing,
        and produce a single combined event if so.

        Price data:
        {json.dumps(price_data, indent=2)}

        Filing summary:
        {filing_summary}

        Set raw_source_type = "combined".
        Return JSON only.
    """).strip()
    return [{"role": "user", "content": user_content}]
