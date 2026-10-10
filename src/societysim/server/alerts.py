"""Discord alerts for the play server (#309): spend passing 50% and 90% of the daily cap,
the kill switch flipping, and a failed start.

The webhook URL is the PLAY_ALERT_WEBHOOK_URL env var (a Space secret). Unset, every alert is
a no-op. A failed post is logged by exception type only, since the message can hold the URL.
Each spend threshold fires once per UTC day.
"""

import asyncio
import json
import logging
import os
import urllib.request
from datetime import UTC, datetime

log = logging.getLogger(__name__)

ENV = "PLAY_ALERT_WEBHOOK_URL"
THRESHOLDS = (0.5, 0.9)
TIMEOUT = 5.0


def _post(url: str, text: str) -> None:
    req = urllib.request.Request(
        url,
        json.dumps({"content": text[:1900]}).encode(),
        {"Content-Type": "application/json", "User-Agent": "societysim-play-alerts"},
    )
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT).close()
    except Exception as exc:  # never the message: it can hold the URL
        log.warning("play alert not sent (%s)", type(exc).__name__)


def send(text: str) -> None:
    """Post `text` to the webhook, blocking up to TIMEOUT. No-op if no webhook is set."""
    if url := os.environ.get(ENV):
        _post(url, f"[societysim play] {text}")


async def asend(text: str) -> None:
    await asyncio.to_thread(send, text)


class Alerts:
    """State so each alert fires once: spend thresholds per day, the kill switch per change."""

    def __init__(self) -> None:
        self._day = datetime.now(UTC).date()
        self._fired: set[float] = set()
        self._enabled: bool | None = None

    async def spend(self, spent_usd: float, budget_usd: float) -> None:
        if budget_usd <= 0:
            return
        if (today := datetime.now(UTC).date()) != self._day:
            self._day, self._fired = today, set()
        for t in THRESHOLDS:
            if spent_usd >= t * budget_usd and t not in self._fired:
                self._fired.add(t)
                await asend(
                    f"Daily spend passed {t:.0%} of the cap: ${spent_usd:.2f} of ${budget_usd:.2f}"
                )

    async def kill_switch(self, enabled: bool) -> None:
        """Alert when play_enabled differs from the last seen value. The first call (startup)
        alerts only if play is off."""
        last, self._enabled = self._enabled, enabled
        if enabled == (True if last is None else last):
            return
        await asend(
            "Kill switch OFF: visitor play is enabled"
            if enabled
            else "Kill switch ON: visitor play is disabled"
        )
