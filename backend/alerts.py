"""
Error alerts to a chat channel (Slack, Discord or similar incoming webhook) via ALERT_WEBHOOK_URL.
Each kind of alert is sent at most once every 10 minutes so an outage doesn't flood the channel.
"""
import logging
import threading
import time

import httpx

from backend import settings

logger = logging.getLogger(__name__)

THROTTLE_SECONDS = 600
_last_sent: dict[str, float] = {}
_lock = threading.Lock()


def _post(text: str) -> None:
    try:
        # "text" is read by Slack-style webhooks, "content" by Discord.
        httpx.post(settings.ALERT_WEBHOOK_URL, json={"text": text, "content": text}, timeout=10)
    except Exception:
        logger.warning("Could not send alert", exc_info=True)


def notify(kind: str, message: str) -> bool:
    """Send an alert unless the same kind was sent recently. Returns True if one was sent."""
    if not settings.ALERT_WEBHOOK_URL:
        return False
    now = time.time()
    with _lock:
        if now - _last_sent.get(kind, 0) < THROTTLE_SECONDS:
            return False
        _last_sent[kind] = now
    text = f"NoteScanner alert ({kind}): {message[:1500]}"
    threading.Thread(target=_post, args=(text,), name="alert", daemon=True).start()
    return True
