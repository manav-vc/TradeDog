"""
Telegram notifier for TradeDog.

Uses the Telegram Bot API directly via HTTP (requests library).
No heavy SDK dependency. Just POST to sendMessage.

Setup:
  1. Create a bot via @BotFather on Telegram
  2. Get your chat_id by messaging the bot and hitting
     https://api.telegram.org/bot<TOKEN>/getUpdates
  3. Store TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env

No tradingagents/ modifications.
"""

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """Send messages to a Telegram chat via Bot API."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        timeout: int = 10,
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.timeout = timeout
        self._enabled = bool(self.bot_token and self.chat_id)

        if not self._enabled:
            logger.warning(
                "Telegram not configured — set TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_CHAT_ID in .env to enable notifications"
            )

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def send_message(self, text: str) -> bool:
        """
        Send a text message to the configured Telegram chat.

        Returns True if sent successfully, False otherwise.
        Does NOT raise on failure — alerts should never crash the engine.
        """
        if not self._enabled:
            logger.debug("Telegram disabled — skipping message")
            return False

        url = _TELEGRAM_API.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            if resp.status_code == 200 and resp.json().get("ok"):
                logger.debug("Telegram message sent successfully")
                return True
            else:
                logger.warning(
                    f"Telegram API error: {resp.status_code} — {resp.text}"
                )
                return False
        except requests.RequestException as e:
            logger.warning(f"Telegram send failed: {e}")
            return False
