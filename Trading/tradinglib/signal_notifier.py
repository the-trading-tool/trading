"""
signal_notifier.py — Pushover-Benachrichtigungen für Buy- und Sell-Signale.

Liest direkt aus den DBs (kein Streamlit/MTP-Overhead):
  - trades{year}.db   → neue Buys / Sells des Tages
  - asset_simulation_all.db → stop_loss, take_profit
  - banner_notes.db   → KI-Analysetext (nur bei Buys)

Aufgerufen von:
  - tradinglib/premium/multi_transaction.py: run_notifier()
  - CLI: python -m tradinglib.signal_notifier [/force] [/date:YYYY-MM-DD]
"""
import datetime as dt
import logging
import os
import sys

import pandas as pd

from tradinglib import tools
from tradinglib.pushover_notifier import PushoverNotifier

logger = logging.getLogger(__name__)

_DB_PATH = "database"
_NOTIFIER_CACHE = "pushover_notifier.json"
_AI_TEXT_LIMIT = 300


# ── DB-Abfragen ───────────────────────────────────────────────────────────────

def _load_buys(date_str: str) -> pd.DataFrame:
    """Offene Kauf-Positionen mit buyDate = date_str."""
    year = date_str[:4]
    db = tools.Db_tools(db_path=_DB_PATH, database_name=f"trades{year}.db")
    try:
        return pd.read_sql_query(
            """SELECT ticker, longName, stockIndex, Strategy,
                      buyDate, buyPrice, buyVolume, buyValue, currency
               FROM trades
               WHERE buyDate LIKE ? AND (sellDate IS NULL OR sellVolume = 0)
               ORDER BY buyDate DESC""",
            db.conn, params=(f"{date_str}%",),
        )
    except Exception as exc:
        logger.warning("signal_notifier: buys query failed: %s", exc)
        return pd.DataFrame()
    finally:
        db.conn.close()


def _load_sells(date_str: str) -> pd.DataFrame:
    """Abgeschlossene Verkäufe mit sellDate = date_str."""
    year = date_str[:4]
    db = tools.Db_tools(db_path=_DB_PATH, database_name=f"trades{year}.db")
    try:
        return pd.read_sql_query(
            """SELECT ticker, longName, Strategy,
                      sellDate, sellPrice, sellVolume, sellValue, currency,
                      gainPct, gain
               FROM trades
               WHERE sellDate LIKE ? AND sellVolume > 0
               ORDER BY sellDate DESC""",
            db.conn, params=(f"{date_str}%",),
        )
    except Exception as exc:
        logger.warning("signal_notifier: sells query failed: %s", exc)
        return pd.DataFrame()
    finally:
        db.conn.close()


def _load_sim(ticker: str) -> dict:
    """stop_loss und take_profit aus asset_simulation_all.db."""
    db = tools.Db_tools(db_path=_DB_PATH, database_name="asset_simulation_all.db")
    try:
        df = pd.read_sql_query(
            "SELECT stop_loss, take_profit FROM asset_simulation WHERE ticker = ? ORDER BY Date DESC LIMIT 1",
            db.conn, params=(ticker,),
        )
        return df.iloc[0].to_dict() if not df.empty else {}
    except Exception as exc:
        logger.debug("signal_notifier: sim lookup failed for %s: %s", ticker, exc)
        return {}
    finally:
        db.conn.close()


def _load_ai_text(ticker: str) -> str:
    """KI-Analysetext aus banner_notes.db (leer wenn nicht vorhanden)."""
    db = tools.Db_tools(db_path=_DB_PATH, database_name="banner_notes.db")
    try:
        df = pd.read_sql_query(
            "SELECT text FROM banner_notes WHERE ticker = ? ORDER BY id DESC LIMIT 1",
            db.conn, params=(ticker,),
        )
        return str(df.iloc[0]["text"]) if not df.empty else ""
    except Exception as exc:
        logger.debug("signal_notifier: no AI text for %s: %s", ticker, exc)
        return ""
    finally:
        db.conn.close()


# ── Nachrichtenformatierung ───────────────────────────────────────────────────

def _fmt(value, decimals: int = 2) -> str:
    """Format a numeric value with thousand separators; returns '–' on failure."""
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "–"


def _build_buy_message(row: dict, sim: dict, ai_text: str, system_currency: str = "") -> str:
    """Build a multi-line Pushover message body for a buy signal.

    Includes ticker, strategy, index, price, stop-loss/take-profit, and a
    truncated AI summary when available.
    """
    ticker    = row.get("ticker", "")
    currency  = row.get("currency", "")
    cur_label = system_currency or currency

    lines = [
        f"{ticker}  {row.get('longName', ticker)}",
        f"Strategie: {row.get('Strategy', '')}",
        f"Index: {row.get('stockIndex', '')}",
        f"Kurs: {_fmt(row.get('buyPrice'))} {currency}",
        f"Volumen: {_fmt(row.get('buyVolume'), 0)}  Wert: {_fmt(row.get('buyValue'))} {currency}",
        f"Stop-Loss: {_fmt(sim.get('stop_loss'))} {cur_label}",
        f"Take-Profit: {_fmt(sim.get('take_profit'))} {cur_label}",
    ]
    if ai_text:
        summary = ai_text[:_AI_TEXT_LIMIT].rsplit(" ", 1)[0]
        if len(ai_text) > _AI_TEXT_LIMIT:
            summary += "…"
        lines += ["", summary]

    return "\n".join(lines)


def _build_sell_message(row: dict) -> str:
    """Build a multi-line Pushover message body for a sell signal, including gain/loss."""
    ticker   = row.get("ticker", "")
    currency = row.get("currency", "")
    gain_pct = row.get("gainPct")
    gain     = row.get("gain")

    sign = "+" if isinstance(gain_pct, (int, float)) and gain_pct >= 0 else ""
    return "\n".join([
        f"{ticker}  {row.get('longName', ticker)}",
        f"Strategie: {row.get('Strategy', '')}",
        f"Kurs: {_fmt(row.get('sellPrice'))} {currency}",
        f"Volumen: {_fmt(row.get('sellVolume'), 0)}  Wert: {_fmt(row.get('sellValue'))} {currency}",
        f"Ergebnis: {sign}{_fmt(gain_pct)} %  ({sign}{_fmt(gain)} {currency})",
    ])


# ── Hauptlogik ────────────────────────────────────────────────────────────────

def run(username: str = "admin", date_str: str | None = None, force: bool = False,
        system_currency: str = "") -> tuple[int, int]:
    """Alerts für Buys und Sells des Tages senden.

    Returns:
        (buy_count, sell_count) — Anzahl gesendeter Nachrichten
    """
    if date_str is None:
        date_str = dt.datetime.now().strftime("%Y-%m-%d")

    logger.info("signal_notifier: run for %s (force=%s)", date_str, force)

    storage = tools.Tools().get_path(path="", file_name=_NOTIFIER_CACHE)
    try:
        notifier = PushoverNotifier(storage_file=storage)
    except Exception as exc:
        logger.error("signal_notifier: PushoverNotifier init failed — check KSP credentials: %s", exc)
        return 0, 0

    buy_count = _send_buys(_load_buys(date_str), notifier, system_currency, force)
    sell_count = _send_sells(_load_sells(date_str), notifier, force)

    logger.info("signal_notifier: done — %d buy alert(s), %d sell alert(s) sent", buy_count, sell_count)
    return buy_count, sell_count


def _send_buys(df: pd.DataFrame, notifier: PushoverNotifier, system_currency: str, force: bool) -> int:
    """Send Pushover buy-signal alerts for each row in df; returns the number of alerts sent."""
    sent = 0
    for _, row in df.iterrows():
        ticker   = row["ticker"]
        buy_date = str(row.get("buyDate", ""))[:10]
        price    = float(row.get("buyPrice") or 0)

        if force:
            notifier.data.pop(ticker, None)

        sim     = _load_sim(ticker)
        ai_text = _load_ai_text(ticker)
        message = _build_buy_message(row.to_dict(), sim, ai_text, system_currency)

        if notifier.send_notification(ticker=ticker, price=price, date=buy_date,
                                      message=message, title="Buy-Signal"):
            sent += 1
            logger.info("signal_notifier: buy alert sent for %s", ticker)
        else:
            logger.debug("signal_notifier: buy skipped (already sent) for %s", ticker)
    return sent


def _send_sells(df: pd.DataFrame, notifier: PushoverNotifier, force: bool) -> int:
    """Send Pushover sell-signal alerts for each row in df; returns the number of alerts sent."""
    sent = 0
    for _, row in df.iterrows():
        ticker    = row["ticker"]
        sell_date = str(row.get("sellDate", ""))[:10]
        price     = float(row.get("sellPrice") or 0)

        sell_key = f"{ticker}_sell"
        if force:
            notifier.data.pop(sell_key, None)

        message = _build_sell_message(row.to_dict())

        if notifier.send_notification(ticker=sell_key, price=price, date=sell_date,
                                      message=message, title="Sell-Signal"):
            sent += 1
            logger.info("signal_notifier: sell alert sent for %s", ticker)
        else:
            logger.debug("signal_notifier: sell skipped (already sent) for %s", ticker)
    return sent


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    force    = False
    date_str = None
    username = "admin"

    for arg in sys.argv[1:]:
        a = arg.lstrip("/").lower()
        if a == "force":
            force = True
        elif a.startswith("date:"):
            date_str = a.split(":", 1)[1]
        elif a.startswith("user:"):
            username = a.split(":", 1)[1]

    os.chdir(tools.Tools().get_path(path="", file_name=""))
    run(username=username, date_str=date_str, force=force)
