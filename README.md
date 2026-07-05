# AI Stock Monitoring Agent

An event-driven market surveillance system that uses the Claude API (Anthropic) to analyze real-time stock prices and SEC filings, then delivers structured alerts via email when material events are detected.

## Architecture

```
main.py (scheduler)
    └─► agents/monitor_agent.py  (orchestrator)
            ├─► ingestion/price_feed.py    → yfinance + Polygon.io
            ├─► ingestion/sec_filings.py   → SEC EDGAR API (no key needed)
            ├─► analysis/llm_analyzer.py   → Claude API (claude-sonnet-4-6)
            │       ├─ analysis/prompts.py        (constrained JSON templates)
            │       └─ analysis/event_schema.py   (Pydantic v2 models)
            ├─► alerts/deduplicator.py     → hash-based seen-events store
            └─► alerts/email_sender.py     → HTML digest via SMTP
```

**Pipeline per cycle:**
1. Fetch price snapshots for all watchlist tickers
2. Fetch SEC filings (8-K, 10-Q, 10-K) filed in the last N days
3. For tickers with both a price spike and a new filing → combined LLM analysis
4. Remaining spikes and filings analyzed independently
5. Events below `MIN_CONFIDENCE` are discarded; duplicates suppressed by dedup store
6. Email digest sent if any new material events remain

## Setup

### 1. Clone & install

```bash
git clone <your-repo>
cd ai-stock-monitor
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Get at [console.anthropic.com](https://console.anthropic.com) |
| `SMTP_USER` | Yes | Your sending email address |
| `SMTP_PASSWORD` | Yes | Gmail: use an [App Password](https://support.google.com/accounts/answer/185833), not your account password |
| `ALERT_RECIPIENTS` | Yes | Comma-separated list of email addresses to notify |
| `POLYGON_API_KEY` | No | Enables real-time price data; free tier at [polygon.io](https://polygon.io) |
| `WATCHLIST` | No | Defaults to `AAPL,MSFT,NVDA` |

### 3. Run

**Single cycle (good for testing):**
```bash
python main.py --once --dry-run        # no email sent
python main.py --once                  # full run with email
python main.py --once TSLA NVDA        # specific tickers
```

**Continuous scheduler (production):**
```bash
python main.py                         # runs every CHECK_INTERVAL_MINUTES
```

Logs are written to `agent.log` and stdout.

## Event Types

The LLM pipeline extracts and classifies events into these categories:

- `price_spike` — absolute move ≥ `PRICE_SPIKE_THRESHOLD` (default 5%)
- `sec_filing` — material 8-K, 10-Q, or 10-K content
- `earnings_update` — earnings surprise or guidance change
- `merger_acquisition` — M&A announcements
- `leadership_change` — C-suite or board changes
- `guidance_change` — forward guidance revision
- `other_material` — catch-all for material events that don't fit above

Each event carries a `confidence` score (0–1) and is discarded if below `MIN_CONFIDENCE`.

## Key Design Decisions

**Hybrid rule + LLM pipeline:** Price spikes are detected by a deterministic threshold rule. The LLM is invoked only when there's a signal worth analyzing, keeping API costs proportional to market activity.

**Constrained prompts with JSON schema:** Every Claude call includes the full Pydantic output schema in the system prompt. Responses that fail JSON validation are captured in `parse_errors` and logged without crashing the pipeline.

**Deduplication:** Events are keyed by `ticker:event_type:date`. The seen-events store (`data/seen_events.json`) persists between runs and auto-expires entries after 2 days.

**Combined analysis:** When a ticker has both a price spike and a new filing in the same cycle, a single prompt asks Claude to reason about whether they're related — reducing duplicate alerts and improving signal quality.

## Project Structure

```
.
├── main.py                   # Scheduler entry point
├── config.py                 # Environment config loader
├── requirements.txt
├── .env.example
├── agents/
│   └── monitor_agent.py      # Orchestration loop
├── ingestion/
│   ├── price_feed.py         # yfinance + Polygon.io
│   └── sec_filings.py        # SEC EDGAR API
├── analysis/
│   ├── event_schema.py       # Pydantic event models
│   ├── prompts.py            # Constrained Claude prompt templates
│   └── llm_analyzer.py       # Claude API calls + response parsing
├── alerts/
│   ├── deduplicator.py       # Hash-based dedup store
│   └── email_sender.py       # HTML email via SMTP
├── scheduler/                # (reserved for future queue workers)
└── data/
    └── seen_events.json      # Auto-created; persists dedup state
```

## Getting a Claude API Key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Sign up or log in
3. Navigate to **API Keys** → **Create Key**
4. Copy the key into your `.env` as `ANTHROPIC_API_KEY`

Free-tier usage is sufficient for light monitoring (a few hundred API calls/day).
