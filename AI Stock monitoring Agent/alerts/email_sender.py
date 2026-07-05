"""
alerts/email_sender.py
----------------------
Sends structured HTML alert emails via SMTP (TLS).
Supports Gmail App Passwords and most SMTP providers.

Public API
----------
    send_alert_digest(events: list[MarketEvent]) -> bool
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from analysis.event_schema import MarketEvent, Severity

logger = logging.getLogger(__name__)

# ── Severity → visual style mapping ──────────────────────────────────────────

_SEVERITY_STYLE: dict[str, dict[str, str]] = {
    Severity.CRITICAL: {"color": "#dc2626", "badge": "#fef2f2", "label": "CRITICAL"},
    Severity.HIGH:     {"color": "#ea580c", "badge": "#fff7ed", "label": "HIGH"},
    Severity.MEDIUM:   {"color": "#ca8a04", "badge": "#fefce8", "label": "MEDIUM"},
    Severity.LOW:      {"color": "#16a34a", "badge": "#f0fdf4", "label": "LOW"},
}


def _severity_style(severity: str) -> dict[str, str]:
    return _SEVERITY_STYLE.get(severity, _SEVERITY_STYLE[Severity.MEDIUM])


# ── HTML template ─────────────────────────────────────────────────────────────

def _render_event_card(event: MarketEvent) -> str:
    style = _severity_style(event.severity)
    metrics_rows = "".join(
        f"<tr><td style='padding:2px 8px;color:#555;'>{k}</td>"
        f"<td style='padding:2px 8px;font-weight:600;'>{v}</td></tr>"
        for k, v in event.key_metrics.items()
    )
    metrics_table = (
        f"<table style='margin-top:8px;border-collapse:collapse;'>{metrics_rows}</table>"
        if metrics_rows else ""
    )
    source_link = (
        f"<p style='margin-top:10px;'><a href='{event.source_url}' "
        f"style='color:#2563eb;'>View source →</a></p>"
        if event.source_url else ""
    )
    return f"""
    <div style="border-left:4px solid {style['color']};background:{style['badge']};
                padding:14px 18px;margin-bottom:16px;border-radius:4px;">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
        <span style="font-size:18px;font-weight:700;">{event.ticker}</span>
        <span style="background:{style['color']};color:#fff;padding:2px 8px;
                     border-radius:12px;font-size:11px;font-weight:600;">
          {style['label']}
        </span>
        <span style="color:#777;font-size:12px;">{event.event_type.replace('_', ' ').title()}</span>
        <span style="margin-left:auto;color:#888;font-size:11px;">
          Confidence: {event.confidence:.0%}
        </span>
      </div>
      <p style="margin:4px 0;font-weight:600;">{event.headline}</p>
      <p style="margin:6px 0;color:#444;font-size:14px;">{event.summary}</p>
      {metrics_table}
      {source_link}
    </div>
    """


def _render_email(events: list[MarketEvent]) -> tuple[str, str]:
    """Returns (subject, html_body)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tickers = ", ".join(sorted({e.ticker for e in events}))

    # Sort: critical first, then by confidence desc
    severity_order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
    sorted_events = sorted(
        events,
        key=lambda e: (severity_order.get(e.severity, 9), -e.confidence),
    )

    cards = "".join(_render_event_card(e) for e in sorted_events)
    count = len(events)
    subject = (
        f"[Stock Alert] {count} material event{'s' if count != 1 else ''} — "
        f"{tickers} — {now}"
    )

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Stock Monitor Alert</title></head>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                 background:#f9fafb;margin:0;padding:24px;">
      <div style="max-width:680px;margin:0 auto;background:#fff;border-radius:8px;
                  box-shadow:0 1px 4px rgba(0,0,0,.1);padding:28px;">
        <h2 style="margin-top:0;color:#111;">
          📈 AI Stock Monitor Alert
          <span style="font-size:14px;font-weight:400;color:#666;margin-left:10px;">{now}</span>
        </h2>
        <p style="color:#555;margin-bottom:20px;">
          {count} material event{'s' if count != 1 else ''} detected across your watchlist.
        </p>
        {cards}
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">
        <p style="color:#9ca3af;font-size:12px;margin:0;">
          Powered by Claude API (Anthropic) · AI Stock Monitoring Agent
        </p>
      </div>
    </body>
    </html>
    """
    return subject, html


# ── Send logic ────────────────────────────────────────────────────────────────

def send_alert_digest(
    events: list[MarketEvent],
    recipients: Optional[list[str]] = None,
) -> bool:
    """
    Sends an HTML digest email for all provided events.
    Returns True on success, False on failure (never raises).
    """
    from config import ALERT_RECIPIENTS, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER

    recipients = recipients or ALERT_RECIPIENTS
    if not recipients:
        logger.warning("No alert recipients configured — skipping email send")
        return False
    if not events:
        logger.debug("No events to send — skipping email")
        return False

    subject, html_body = _render_email(events)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, recipients, msg.as_string())
        logger.info(
            "Alert email sent to %s (%d event(s))", ", ".join(recipients), len(events)
        )
        return True
    except Exception as exc:
        logger.error("Failed to send alert email: %s", exc)
        return False
