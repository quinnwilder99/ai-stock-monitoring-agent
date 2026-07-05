"""
analysis/event_schema.py
------------------------
Pydantic v2 models that define the structured output produced by the LLM pipeline.
Every Claude response is parsed into one of these event types before being passed
downstream to deduplication and alerting.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class EventType(str, Enum):
    PRICE_SPIKE = "price_spike"
    SEC_FILING = "sec_filing"
    EARNINGS_UPDATE = "earnings_update"
    MERGER_ACQUISITION = "merger_acquisition"
    LEADERSHIP_CHANGE = "leadership_change"
    GUIDANCE_CHANGE = "guidance_change"
    OTHER_MATERIAL = "other_material"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MarketEvent(BaseModel):
    """
    Canonical structured event object produced by the LLM analyzer.
    All fields are populated by Claude; the pipeline adds id/created_at.
    """
    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Core classification
    event_type: EventType
    severity: Severity
    ticker: str = Field(..., description="Primary affected ticker symbol")
    related_tickers: list[str] = Field(
        default_factory=list,
        description="Other tickers materially affected by this event",
    )

    # Confidence & signal quality
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="LLM confidence score 0–1. Events below MIN_CONFIDENCE are discarded.",
    )
    is_novel: bool = Field(
        ...,
        description="False if this appears to be a duplicate or low-signal rehash.",
    )

    # Human-readable fields used in the alert email
    headline: str = Field(..., description="One-line headline, max 120 chars")
    summary: str = Field(
        ..., description="2–4 sentence plain-English summary of why this matters"
    )
    key_metrics: dict[str, str] = Field(
        default_factory=dict,
        description="Key quantitative signals, e.g. {'pct_change': '+7.2%', 'eps_beat': '$0.14'}",
    )
    source_url: Optional[str] = None
    raw_source_type: Literal["price_feed", "sec_filing", "combined"] = "price_feed"

    @field_validator("ticker")
    @classmethod
    def upper_ticker(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("headline")
    @classmethod
    def trim_headline(cls, v: str) -> str:
        return v[:120]

    def dedup_key(self) -> str:
        """
        Stable key for deduplication. Same ticker + event type + calendar date
        produces the same key, so we don't re-alert on the same event within a day.
        """
        date_str = self.created_at.strftime("%Y-%m-%d")
        return f"{self.ticker}:{self.event_type.value}:{date_str}"

    class Config:
        use_enum_values = True


class LLMAnalysisResult(BaseModel):
    """Wrapper returned by the LLM analyzer — may contain 0–N events."""
    events: list[MarketEvent] = Field(default_factory=list)
    raw_response: str = ""
    parse_errors: list[str] = Field(default_factory=list)
