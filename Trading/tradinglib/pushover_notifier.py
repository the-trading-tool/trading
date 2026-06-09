import requests
import json
import logging
import os
from datetime import datetime
from tradinglib import ksplib
from tradinglib import tools

logger = logging.getLogger(__name__)


class PushoverNotifier:
    def __init__(self, api: str = "tradingdesk", storage_file: str = "pushover_data.json"):
        """Load Pushover credentials from KSP and read the deduplication state file."""
        ksp = ksplib.Ksp()
        (self.user_key, self.api_token, self.url) = ksp.get_ksp(api).values()
        self.storage_file = storage_file
        self.data = self._load_data()

    def _load_data(self):
        """Load the deduplication state from the JSON storage file; returns {} when absent."""
        if os.path.exists(self.storage_file):
            with open(self.storage_file, "r") as file:
                return json.load(file)
        return {}

    def _save_data(self):
        """Persist the current deduplication state to the JSON storage file."""
        with open(self.storage_file, "w") as file:
            json.dump(self.data, file)

    def _should_send(self, ticker: str, price: float, date: str):
        """Return True when the alert for ticker has not been sent yet for this price and date."""
        last_entry = self.data.get(ticker, {})
        last_price = last_entry.get("price")
        last_sent = last_entry.get("last_sent")

        if last_sent is None:
            logger.debug("PushoverNotifier: no prior entry for %s — sending", ticker)
            return True

        if round(last_price, 3) != round(price, 3) and last_sent != date:
            logger.debug("PushoverNotifier: price change %s→%s for %s — sending", last_price, price, ticker)
            return True

        logger.debug("PushoverNotifier: no update needed for %s", ticker)
        return False

    def send_notification(self, ticker: str, price: float, date: str, message: str = "", title: str = "Trade processor"):
        """Send a Pushover push notification and persist deduplication state on success.

        Returns True when the notification was sent, False when skipped (already sent)
        or when the HTTP request failed.
        """
        if not self._should_send(ticker, price, date):
            return False
        if message == '':
            message = f"{ticker}: {price}, {date}"
        hostname = "localhost"
        try:
            hostname = os.uname()[1]
        except Exception:
            pass
        data = {
            "token": self.api_token,
            "user": self.user_key,
            "title": f"{hostname}:{title}",
            "message": message,
        }
        response = requests.post(self.url, data=data)

        if response.status_code == 200:
            logger.info("PushoverNotifier: sent alert for %s, saving state", ticker)
            self.data[ticker] = {"price": price, "last_sent": date}
            self._save_data()
            return True

        logger.warning("PushoverNotifier: HTTP %s for %s", response.status_code, ticker)
        return False
