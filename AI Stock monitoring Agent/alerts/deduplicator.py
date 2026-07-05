"""
alerts/deduplicator.py
----------------------
Hash-based deduplication layer that prevents the same event from triggering
multiple alerts within the same calendar day.

Storage: a JSON file at data/seen_events.json that persists between runs.
Entries older than TTL_DAYS are automatically pruned on load.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from analysis.event_schema import MarketEvent

logger = logging.getLogger(__name__)

TTL_DAYS = 2   # keep seen-event keys for this many days


class Deduplicator:
    def __init__(self, store_path: Path):
        self._path = store_path
        self._seen: dict[str, str] = {}   # dedup_key → ISO timestamp first seen
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data: dict[str, Any] = json.loads(self._path.read_text())
            cutoff = datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)
            for key, ts_str in data.items():
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts >= cutoff:
                        self._seen[key] = ts_str
                except ValueError:
                    pass
            logger.debug("Deduplicator loaded %d seen keys", len(self._seen))
        except Exception as exc:
            logger.warning("Could not load seen_events.json: %s — starting fresh", exc)

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._seen, indent=2))
        except Exception as exc:
            logger.error("Could not save seen_events.json: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def is_duplicate(self, event: MarketEvent) -> bool:
        return event.dedup_key() in self._seen

    def mark_seen(self, event: MarketEvent) -> None:
        self._seen[event.dedup_key()] = datetime.now(timezone.utc).isoformat()
        self._save()

    def filter_new(self, events: list[MarketEvent]) -> list[MarketEvent]:
        """
        Returns only events that haven't been seen before, and marks them seen.
        Also drops events where is_novel == False.
        """
        new_events: list[MarketEvent] = []
        for event in events:
            if not event.is_novel:
                logger.debug("Skipping non-novel event: %s", event.dedup_key())
                continue
            if self.is_duplicate(event):
                logger.debug("Duplicate event suppressed: %s", event.dedup_key())
                continue
            new_events.append(event)
            self.mark_seen(event)
        return new_events
