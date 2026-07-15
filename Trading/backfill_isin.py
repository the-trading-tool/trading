"""Backfill fehlende ISINs in yf_tickers.db (stocks.ISIN).

Liest alle Ticker ohne ISIN, fragt yfinance ab und schreibt die
Ergebnisse zurück. Indices (^...) und Tickers ohne ISIN-Rückgabe
werden übersprungen.

Verwendung:
    python backfill_isin.py               # alle fehlenden
    python backfill_isin.py /limit:50     # nur die ersten 50 (Test)
    python backfill_isin.py /delay:500    # 500 ms zwischen Requests
    python backfill_isin.py /dry          # nur anzeigen, nicht schreiben
    python backfill_isin.py /log:DEBUG    # verbose Logging
"""

import sqlite3
import sys
import time
import logging

import yfinance as yf

from tradinglib import logging_config
from tradinglib.tools import Tools, open_db

logger = logging.getLogger(__name__)

# ── Stille yfinance-eigene Log-Ausgaben ────────────────────────────────────
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

BATCH_SIZE  = 50    # Commit-Intervall
DEFAULT_DELAY_MS = 300  # Millisekunden zwischen API-Calls


# ── CLI-Parsing ────────────────────────────────────────────────────────────

def parse_args(argv=None):
    """Parse /pref-style CLI options into a configuration dict."""
    if argv is None:
        argv = sys.argv

    opts = {
        'limit':    None,
        'delay_ms': DEFAULT_DELAY_MS,
        'dry':      False,
        'log_to_console': True,
        'log_level': 'INFO',
        'log_file':  None,
    }

    for arg in argv[1:]:
        if not arg.startswith('/'):
            continue
        pref = arg[1:].lower()
        key, _, val = pref.partition(':')

        if key == 'limit' and val:
            try:
                opts['limit'] = int(val)
            except ValueError:
                pass
        elif key == 'delay' and val:
            try:
                opts['delay_ms'] = int(val)
            except ValueError:
                pass
        elif key == 'dry':
            opts['dry'] = True
        elif key == 'log':
            opts['log_level'] = val.upper() if val else 'DEBUG'
        elif key == 'logfile' and val:
            opts['log_file'] = val

    return opts


# ── Hilfsfunktionen ────────────────────────────────────────────────────────

_ISIN_PROVIDER = None
_ISIN_PROVIDER_TRIED = False


def _get_isin_provider():
    """Lazily build the ISIN-resolving provider from KSP (None if unavailable).

    Uses whichever keyed provider (FMP or EODHD) has an API key configured.
    """
    global _ISIN_PROVIDER, _ISIN_PROVIDER_TRIED
    if _ISIN_PROVIDER_TRIED:
        return _ISIN_PROVIDER
    _ISIN_PROVIDER_TRIED = True
    try:
        from tradinglib.providers import get_isin_resolver
        _ISIN_PROVIDER = get_isin_resolver()
    except Exception as e:
        logger.debug(f"ISIN-Provider-Fallback nicht verfügbar: {e}")
        _ISIN_PROVIDER = None
    return _ISIN_PROVIDER


def fetch_isin(ticker: str, use_provider: bool = True) -> str | None:
    """Return the ISIN for ticker, or None when unavailable.

    Tries yfinance first; falls back to the configured data provider (FMP or
    EODHD), which covers many young US small-caps that yfinance returns no
    ISIN for.
    """
    try:
        isin = yf.Ticker(ticker).isin
        if isin and isin != '-':
            return isin
    except Exception as e:
        logger.debug(f"{ticker}: yfinance-Fehler — {e}")

    if use_provider:
        provider = _get_isin_provider()
        if provider is not None:
            try:
                isin = provider.profile_isin(ticker)
                if isin:
                    logger.debug(f"{ticker}: ISIN via {provider.name} — {isin}")
                    return isin
            except Exception as e:
                logger.debug(f"{ticker}: {provider.name}-Fehler — {e}")
    return None


def load_missing(conn: sqlite3.Connection, limit: int | None) -> list[str]:
    """Return all tickers from the stocks table that have no ISIN value."""
    sql = "SELECT Ticker FROM stocks WHERE ISIN IS NULL OR ISIN = ''"
    if limit:
        sql += f" LIMIT {limit}"
    rows = conn.execute(sql).fetchall()
    return [r[0] for r in rows]


def update_isin(conn: sqlite3.Connection, ticker: str, isin: str):
    """Write isin into the stocks table for ticker (does not commit)."""
    conn.execute(
        "UPDATE stocks SET ISIN = ? WHERE Ticker = ?",
        (isin, ticker),
    )


# ── Hauptprogramm ──────────────────────────────────────────────────────────

def main():
    """Iterate over tickers without ISINs, fetch from yfinance, and write results to DB."""
    opts = parse_args()
    logging_config.configure_logging(
        to_console=opts['log_to_console'],
        level=opts['log_level'],
        logfile=opts['log_file'],
    )

    db_path = Tools().get_path(path='database', file_name='yf_tickers.db')
    delay   = opts['delay_ms'] / 1000.0
    dry_run = opts['dry']

    if dry_run:
        logger.info("*** DRY-RUN — keine Änderungen werden gespeichert ***")

    with open_db(db_path) as conn:
        tickers = load_missing(conn, opts['limit'])
        total   = len(tickers)

        if total == 0:
            logger.info("Alle Ticker haben bereits eine ISIN — nichts zu tun.")
            return

        logger.info(f"{total} Ticker ohne ISIN gefunden (delay={opts['delay_ms']} ms).")

        found = skipped = errors = 0

        for i, ticker in enumerate(tickers, start=1):
            # Indices (^GSPC) und Währungspaare (EURUSD=X) haben keine ISIN
            if ticker.startswith('^') or ticker.endswith('=X'):
                logger.debug(f"[{i:>5}/{total}] {ticker:<20} -> uebersprungen (Index/FX)")
                skipped += 1
                continue

            isin = fetch_isin(ticker)

            if isin:
                logger.info(f"[{i:>5}/{total}] {ticker:<20} -> {isin}")
                if not dry_run:
                    update_isin(conn, ticker, isin)
                found += 1
            else:
                logger.debug(f"[{i:>5}/{total}] {ticker:<20} -> kein ISIN")
                skipped += 1

            # Batch-Commit
            if not dry_run and i % BATCH_SIZE == 0:
                conn.commit()
                logger.info(f"  Commit nach {i} Eintraegen")

            # Rate-Limit
            if i < total:
                time.sleep(delay)

        # Abschluss-Commit
        if not dry_run:
            conn.commit()

    logger.info(
        f"Fertig — gefunden: {found} | übersprungen: {skipped} | Fehler: {errors}"
    )


if __name__ == '__main__':
    main()
