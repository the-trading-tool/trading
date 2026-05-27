import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from tradinglib.tools import Tools

logger = logging.getLogger(__name__)


@dataclass
class BrokerSymbols:
    yahoo_ticker: str
    yf_isin: Optional[str]
    alpaca_symbol: Optional[str]   # None = not tradeable on Alpaca
    ibkr_symbol: Optional[str]
    ibkr_exchange: Optional[str]
    ibkr_currency: Optional[str]
    source: str                     # 'suffix_rule' | 'isin_lookup' | 'manual' | 'unresolved'


class TickerResolver(Tools):
    """Maps Yahoo Finance tickers to broker-specific symbols.

    Resolution order:
      1. Manual override table  (config.db — user-editable, always wins)
      2. Local cache            (asset_info.db — refreshed every 30 days)
      3. Suffix rules           (instant, zero API calls, ~95% coverage)
      4. ISIN via yfinance      (one-time network call, result is cached)
    """

    # Yahoo suffix → (ibkr_exchange, ibkr_currency, alpaca_tradeable)
    SUFFIX_MAP: dict[str, tuple[str, str, bool]] = {
        '.DE': ('XETRA', 'EUR', False),
        '.L':  ('LSE',   'GBP', False),
        '.SW': ('EBS',   'CHF', False),
        '.PA': ('SBF',   'EUR', False),
        '.MI': ('BVME',  'EUR', False),
        '.MC': ('BM',    'EUR', False),
        '.AS': ('AEB',   'EUR', False),
        '.TO': ('TSX',   'CAD', False),
        '.AX': ('ASX',   'AUD', False),
        '.HK': ('SEHK',  'HKD', False),
        '.T':  ('TSEJ',  'JPY', False),
        '.ST': ('SFB',   'SEK', False),
        '.OL': ('OSE',   'NOK', False),
        '.CO': ('CPH',   'DKK', False),
        '.HE': ('HEX',   'EUR', False),
        '.VI': ('VSE',   'EUR', False),
        '.BR': ('ENEXT', 'EUR', False),
        '.LS': ('ENEXT', 'EUR', False),
    }

    CACHE_MAX_AGE_DAYS = 30

    def __init__(self, db_path: str = 'database'):
        self.db_path = db_path
        self._info_db = self.get_path(path=db_path, file_name='asset_info.db')
        self._config_db = self.get_path(path=db_path, file_name='config.db')
        self._ensure_tables()

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def resolve(self, yahoo_ticker: str) -> BrokerSymbols:
        t = yahoo_ticker.strip().upper()

        override = self._get_override(t)
        if override:
            return override

        cached = self._get_cache(t)
        if cached:
            return cached

        result = self._apply_suffix_rule(t) or self._resolve_via_isin(t)

        if result is None:
            result = BrokerSymbols(
                yahoo_ticker=t, yf_isin=None,
                alpaca_symbol=None, ibkr_symbol=None,
                ibkr_exchange=None, ibkr_currency=None,
                source='unresolved',
            )

        self._save_cache(result)
        return result

    def resolve_for_broker(self, yahoo_ticker: str, broker_id: str) -> Optional[str]:
        sym = self.resolve(yahoo_ticker)
        if broker_id == 'alpaca':
            return sym.alpaca_symbol
        if broker_id == 'ibkr':
            return sym.ibkr_symbol
        return None

    def resolve_batch(self, tickers: list[str]) -> dict[str, BrokerSymbols]:
        return {t: self.resolve(t) for t in tickers}

    def get_all_overrides(self) -> list[dict]:
        try:
            with sqlite3.connect(self._config_db) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM broker_symbol_override").fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def save_override(
        self,
        yahoo_ticker: str,
        alpaca_symbol: Optional[str],
        ibkr_symbol: Optional[str],
        ibkr_exchange: Optional[str],
        ibkr_currency: Optional[str],
        note: str = '',
    ):
        with sqlite3.connect(self._config_db) as conn:
            conn.execute("""
                INSERT INTO broker_symbol_override
                    (yahoo_ticker, alpaca_symbol, ibkr_symbol, ibkr_exchange, ibkr_currency, note)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(yahoo_ticker) DO UPDATE SET
                    alpaca_symbol=excluded.alpaca_symbol,
                    ibkr_symbol=excluded.ibkr_symbol,
                    ibkr_exchange=excluded.ibkr_exchange,
                    ibkr_currency=excluded.ibkr_currency,
                    note=excluded.note
            """, (yahoo_ticker.upper(), alpaca_symbol, ibkr_symbol,
                  ibkr_exchange, ibkr_currency, note))
        # Invalidate cache entry so next resolve() picks up the override
        self._delete_cache(yahoo_ticker.upper())

    def delete_override(self, yahoo_ticker: str):
        with sqlite3.connect(self._config_db) as conn:
            conn.execute(
                "DELETE FROM broker_symbol_override WHERE yahoo_ticker=?",
                (yahoo_ticker.upper(),),
            )

    # ------------------------------------------------------------------ #
    #  Private: table setup                                                #
    # ------------------------------------------------------------------ #

    def _ensure_tables(self):
        try:
            with sqlite3.connect(self._info_db) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS broker_symbol_cache (
                        yahoo_ticker   TEXT PRIMARY KEY,
                        yf_isin        TEXT,
                        alpaca_symbol  TEXT,
                        ibkr_symbol    TEXT,
                        ibkr_exchange  TEXT,
                        ibkr_currency  TEXT,
                        resolved_at    TEXT,
                        source         TEXT
                    )
                """)
        except Exception as e:
            logger.debug(f"broker_symbol_cache table setup skipped: {e}")

        try:
            with sqlite3.connect(self._config_db) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS broker_symbol_override (
                        yahoo_ticker   TEXT PRIMARY KEY,
                        alpaca_symbol  TEXT,
                        ibkr_symbol    TEXT,
                        ibkr_exchange  TEXT,
                        ibkr_currency  TEXT,
                        note           TEXT
                    )
                """)
        except Exception as e:
            logger.debug(f"broker_symbol_override table setup skipped: {e}")

    # ------------------------------------------------------------------ #
    #  Private: override                                                   #
    # ------------------------------------------------------------------ #

    def _get_override(self, ticker: str) -> Optional[BrokerSymbols]:
        try:
            with sqlite3.connect(self._config_db) as conn:
                row = conn.execute(
                    "SELECT alpaca_symbol, ibkr_symbol, ibkr_exchange, ibkr_currency "
                    "FROM broker_symbol_override WHERE yahoo_ticker=?",
                    (ticker,),
                ).fetchone()
            if row:
                return BrokerSymbols(
                    yahoo_ticker=ticker, yf_isin=None,
                    alpaca_symbol=row[0], ibkr_symbol=row[1],
                    ibkr_exchange=row[2], ibkr_currency=row[3],
                    source='manual',
                )
        except Exception as e:
            logger.debug(f"Override lookup failed for {ticker}: {e}")
        return None

    # ------------------------------------------------------------------ #
    #  Private: cache                                                      #
    # ------------------------------------------------------------------ #

    def _get_cache(self, ticker: str) -> Optional[BrokerSymbols]:
        try:
            with sqlite3.connect(self._info_db) as conn:
                row = conn.execute(
                    "SELECT yahoo_ticker, yf_isin, alpaca_symbol, ibkr_symbol, "
                    "ibkr_exchange, ibkr_currency, resolved_at, source "
                    "FROM broker_symbol_cache WHERE yahoo_ticker=?",
                    (ticker,),
                ).fetchone()
            if row:
                resolved_at = datetime.fromisoformat(row[6]) if row[6] else datetime.min
                if datetime.now() - resolved_at < timedelta(days=self.CACHE_MAX_AGE_DAYS):
                    return BrokerSymbols(
                        yahoo_ticker=row[0], yf_isin=row[1],
                        alpaca_symbol=row[2], ibkr_symbol=row[3],
                        ibkr_exchange=row[4], ibkr_currency=row[5],
                        source=row[7] or 'cache',
                    )
        except Exception as e:
            logger.debug(f"Cache lookup failed for {ticker}: {e}")
        return None

    def _save_cache(self, sym: BrokerSymbols):
        try:
            with sqlite3.connect(self._info_db) as conn:
                conn.execute("""
                    INSERT INTO broker_symbol_cache
                        (yahoo_ticker, yf_isin, alpaca_symbol, ibkr_symbol,
                         ibkr_exchange, ibkr_currency, resolved_at, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(yahoo_ticker) DO UPDATE SET
                        yf_isin=excluded.yf_isin,
                        alpaca_symbol=excluded.alpaca_symbol,
                        ibkr_symbol=excluded.ibkr_symbol,
                        ibkr_exchange=excluded.ibkr_exchange,
                        ibkr_currency=excluded.ibkr_currency,
                        resolved_at=excluded.resolved_at,
                        source=excluded.source
                """, (
                    sym.yahoo_ticker, sym.yf_isin, sym.alpaca_symbol,
                    sym.ibkr_symbol, sym.ibkr_exchange, sym.ibkr_currency,
                    datetime.now().isoformat(), sym.source,
                ))
        except Exception as e:
            logger.debug(f"Cache save failed for {sym.yahoo_ticker}: {e}")

    def _delete_cache(self, ticker: str):
        try:
            with sqlite3.connect(self._info_db) as conn:
                conn.execute(
                    "DELETE FROM broker_symbol_cache WHERE yahoo_ticker=?", (ticker,)
                )
        except Exception as e:
            logger.debug(f"Cache delete failed for {ticker}: {e}")

    # ------------------------------------------------------------------ #
    #  Private: resolution strategies                                      #
    # ------------------------------------------------------------------ #

    def _apply_suffix_rule(self, ticker: str) -> Optional[BrokerSymbols]:
        # Known non-US suffixes
        for suffix, (exchange, currency, alpaca_ok) in self.SUFFIX_MAP.items():
            if ticker.endswith(suffix):
                base = ticker[: -len(suffix)]
                return BrokerSymbols(
                    yahoo_ticker=ticker, yf_isin=None,
                    alpaca_symbol=ticker if alpaca_ok else None,
                    ibkr_symbol=base,
                    ibkr_exchange=exchange,
                    ibkr_currency=currency,
                    source='suffix_rule',
                )
        # No suffix and no dot → US stock
        if '.' not in ticker and '^' not in ticker:
            return BrokerSymbols(
                yahoo_ticker=ticker, yf_isin=None,
                alpaca_symbol=ticker,
                ibkr_symbol=ticker,
                ibkr_exchange='SMART',
                ibkr_currency='USD',
                source='suffix_rule',
            )
        return None

    def _resolve_via_isin(self, ticker: str) -> Optional[BrokerSymbols]:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            isin = t.isin
            if not isin or isin == '-':
                return None
            country = isin[:2]
            alpaca_sym = ticker if country == 'US' else None
            base = ticker.split('.')[0]
            exchange = 'SMART' if country == 'US' else 'SMART'
            currency = 'USD' if country == 'US' else None
            return BrokerSymbols(
                yahoo_ticker=ticker, yf_isin=isin,
                alpaca_symbol=alpaca_sym,
                ibkr_symbol=base,
                ibkr_exchange=exchange,
                ibkr_currency=currency,
                source='isin_lookup',
            )
        except Exception as e:
            logger.debug(f"ISIN lookup failed for {ticker}: {e}")
        return None
