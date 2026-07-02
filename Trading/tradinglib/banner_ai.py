"""
banner_ai.py — Automatische Gemini-Analyse für das zuletzt empfohlene Asset.

Ablauf:
  1. Letzter Kauf aus trades{year}.db  → ticker, Strategy, stockIndex
  2. Vollständige Simulationszeile aus asset_simulation_all.db (85 Spalten)
  3. Passende Strategie-Bedingungen aus multi_transactions (sys_conf)
  4. Metadaten aus asset_info.db
  5. Letzte 20 OHLC-Zeilen aus yf_<ticker>.db
  6. Gemini-Analyse generieren
  7. Ergebnis in banner_notes.db speichern
"""
import ast
import datetime as dt
import logging

import pandas as pd

from tradinglib import tools
from tradinglib.ai_client import AiClient, AiRateLimitError, AiProviderError
from tradinglib import system_config as sysconf

logger = logging.getLogger(__name__)

_DB_PATH = 'database'

# Spalten aus asset_simulation die NICHT in den Prompt sollen (redundant/intern)
_SIM_SKIP_COLS = {'ticker', 'Date', 'currency'}

# Gruppen der Simulation-Kennzahlen für den Prompt
_SIM_GROUPS = {
    'Trend':       ['overallValueTrend', 'overallTrend', 'dTrend', 'wkTrend', 'moTrend',
                    'trendDirection', 'buySell', 'ewo_trend_day', 'ewo_trend_wk', 'ewo_trend_mo',
                    'macd_trend', 'macd_trend_wk', 'macd_trend_mo'],
    'Momentum':    ['rsi', 'rsi_ema', 'cci', 'momentum', 'momentum_ema', 'momentum_ema_angle',
                    'adx', 'adx_angle', 'adx_plus', 'adx_minus'],
    'MACD/EWO':    ['macd', 'macd_signal', 'ewo', 'ewo_ema', 'ewo_angle',
                    'ewo_wk', 'ewo_mo'],
    'Heikin Ashi': ['ha_close', 'ha_open', 'ha_ema_high', 'ha_ema_low'],
    'DEMA':        ['dema_ema_fast', 'dema_ema_slow', 'dema_buy', 'dema_sell'],
    'Preis':       ['close', 'Open', 'High', 'Low', 'ath', 'take_profit', 'stop_loss',
                    'sup_resistance', 'sup_resistance_wk', 'sup_support', 'sup_support_wk',
                    'sma20', 'sma50', 'sma200', 'ema9', 'ema21', 'ema50',
                    'predictedLow', 'predictedHigh', 'pctTargetHighPrice'],
    'Risiko':      ['sortino', 'sharpe', 'vola', 'semiVola', 'logVola', 'atr',
                    'markov_regime', 'hor_val', 'hor_threshold'],
    'Volumen':     ['relvol_ratio', 'relvol_ratio_wk', 'relvol_ratio_mo'],
}


class BannerAiGenerator:

    def __init__(self, username: str = 'admin'):
        """Set up the generator with a user-scoped SystemConfig for strategy lookups."""
        self.username = username
        self.sys_conf = sysconf.SystemConfig(username=username)
        self._t = tools.Tools()

    # ── public ───────────────────────────────────────────────────────────────

    def run(self, force: bool = False) -> tuple[str, str]:
        """Analyse durchführen und in banner_notes.db speichern.

        Args:
            force: Wenn True, auch dann neu analysieren wenn heute schon ein
                   Eintrag für diesen Ticker existiert.

        Returns:
            (ticker, analyse_text)
        """
        trade_row = self._get_latest_trade()
        if not trade_row:
            raise RuntimeError(
                f"Kein Kauf-Eintrag in trades{dt.datetime.now().year}.db gefunden."
            )
        ticker = trade_row['ticker']

        if not force:
            existing = self._get_existing_note(ticker)
            if existing:
                logger.info("BannerAiGenerator: %s bereits heute analysiert — übersprungen", ticker)
                return ticker, existing

        context = self._build_context(ticker, trade_row)
        text = AiClient(username=self.username).analyze_asset(ticker, context)
        self._save_to_banner_notes(ticker, text)
        logger.info("BannerAiGenerator: saved analysis for %s", ticker)
        return ticker, text

    def build_debug_info(self) -> dict:
        """Alle Daten sammeln und Prompt bauen — ohne API-Call.

        Returns:
            dict mit: ticker, trade_row, sim_row, strategy_ctx, asset_info,
                      ohlc_df, context, prompt, existing_note, token_estimate
        """
        trade_row = self._get_latest_trade()
        if not trade_row:
            return {'error': f"Kein Kauf-Eintrag in trades{dt.datetime.now().year}.db gefunden."}

        ticker    = trade_row['ticker']
        sim_row   = self._load_sim_row(ticker)
        strat_ctx = self._get_strategy_context(trade_row.get('stockIndex', ''))
        asset_info = self._load_asset_info(ticker)
        ohlc_df    = self._load_recent_ohlc(ticker)
        existing   = self._get_existing_note(ticker)

        context = self._build_context(ticker, trade_row)
        from tradinglib.ai_client import _build_asset_prompt
        prompt  = _build_asset_prompt(ticker, context)

        return {
            'ticker':         ticker,
            'trade_row':      trade_row,
            'sim_row':        sim_row,
            'strategy_ctx':   strat_ctx,
            'asset_info':     asset_info,
            'ohlc_df':        ohlc_df,
            'context':        context,
            'prompt':         prompt,
            'existing_note':  existing,
            'token_estimate': len(prompt) // 4,
        }

    # ── context builder ───────────────────────────────────────────────────────

    def _build_context(self, ticker: str, trade_row: dict) -> dict:
        """Vollständigen Kontext-Dict für GeminiApi.analyze_asset() zusammenbauen."""
        sim_row    = self._load_sim_row(ticker)
        strat_ctx  = self._get_strategy_context(trade_row.get('stockIndex', ''))
        asset_info = self._load_asset_info(ticker)
        ohlc_df    = self._load_recent_ohlc(ticker)

        # Simulation-Spalten gruppiert aufbereiten
        sim_grouped = {}
        for group, cols in _SIM_GROUPS.items():
            entries = {}
            for c in cols:
                v = sim_row.get(c)
                if v is not None:
                    entries[c] = round(v, 4) if isinstance(v, float) else v
            if entries:
                sim_grouped[group] = entries

        ohlc_display = (
            ohlc_df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']].tail(20)
            if not ohlc_df.empty else ''
        )

        return {
            # Basis-Infos
            **asset_info,
            'ticker':       ticker,
            'stockIndex':   trade_row.get('stockIndex', ''),
            'strategy_name': trade_row.get('Strategy', ''),
            'buy_date':     trade_row.get('buyDate', ''),
            'buy_price':    trade_row.get('buyPrice', ''),

            # Strategie-Bedingungen
            'buy_query':    strat_ctx.get('buy', self.sys_conf.get_value('buy_query', '')),
            'sell_query':   strat_ctx.get('sell', ''),

            # Simulationsdaten (gruppiert)
            'sim_grouped':  sim_grouped,

            # Rohe Sortino/Sharpe für Kompatibilität mit _build_asset_prompt
            'sortino':      sim_row.get('sortino'),
            'sharpe':       sim_row.get('sharpe'),
            'overallValueTrend': sim_row.get('overallValueTrend'),

            # OHLC
            'recent_ohlc':  ohlc_display,
            'indicator_values': pd.DataFrame(),   # aus sim_grouped abgelöst
        }

    # ── data helpers ─────────────────────────────────────────────────────────

    def _get_latest_trade(self) -> dict | None:
        """Letzten Kauf aus trades{year}.db zurückgeben."""
        year = dt.datetime.now().year
        db = tools.Db_tools(db_path=_DB_PATH, database_name=f'trades{year}.db')
        try:
            df = pd.read_sql_query(
                """SELECT ticker, longName, stockIndex, Strategy,
                          buyDate, buyPrice, buyValue, currency
                   FROM trades
                   ORDER BY buyDate DESC
                   LIMIT 1""",
                db.conn,
            )
        except Exception as exc:
            logger.warning("BannerAiGenerator: could not query trades DB: %s", exc)
            return None
        finally:
            db.conn.close()

        if df.empty:
            return None
        return df.iloc[0].to_dict()

    def _load_sim_row(self, ticker: str) -> dict:
        """Vollständige Simulationszeile für ticker aus asset_simulation_all.db."""
        db = tools.Db_tools(db_path=_DB_PATH, database_name='asset_simulation_all.db')
        try:
            df = pd.read_sql_query(
                "SELECT * FROM asset_simulation WHERE ticker = ? ORDER BY Date DESC LIMIT 1",
                db.conn,
                params=(ticker,),
            )
        except Exception as exc:
            logger.warning("BannerAiGenerator: sim_row load failed for %s: %s", ticker, exc)
            return {}
        finally:
            db.conn.close()

        if df.empty:
            return {}
        return {k: v for k, v in df.iloc[0].to_dict().items() if k not in _SIM_SKIP_COLS}

    def _get_strategy_context(self, stock_index: str) -> dict:
        """Buy/Sell-Bedingungen für stock_index aus multi_transactions."""
        try:
            raw = self.sys_conf.get_value('multi_transactions', self.sys_conf.transactions)
            transactions = ast.literal_eval(raw) if isinstance(raw, str) else raw
            # stock_index z.B. "^MDAXI" → key "MDAXI"
            key = stock_index.lstrip('^')
            if key in transactions:
                return transactions[key]
            # Fallback: partieller Match
            for k, v in transactions.items():
                if k in stock_index or stock_index in k:
                    return v
        except Exception as exc:
            logger.warning("BannerAiGenerator: strategy context lookup failed: %s", exc)
        return {}

    def _get_existing_note(self, ticker: str) -> str:
        """Vorhandenen Text zurückgeben wenn ticker heute schon analysiert wurde."""
        today = dt.datetime.now().strftime('%Y-%m-%d')
        db = tools.Db_tools(db_path=_DB_PATH, database_name='banner_notes.db')
        try:
            # buyDate könnte fehlen wenn Tabelle mit alter Schema angelegt wurde
            try:
                df = pd.read_sql_query(
                    "SELECT text, buyDate FROM banner_notes WHERE ticker = ? ORDER BY id DESC LIMIT 1",
                    db.conn, params=(ticker,),
                )
                if not df.empty and str(df.iloc[0].get('buyDate', '')).startswith(today):
                    return str(df.iloc[0]['text'])
            except Exception:
                # Fallback ohne buyDate — kein Heute-Check möglich
                df = pd.read_sql_query(
                    "SELECT text FROM banner_notes WHERE ticker = ? ORDER BY id DESC LIMIT 1",
                    db.conn, params=(ticker,),
                )
                if not df.empty:
                    return str(df.iloc[0]['text'])
        except Exception:
            pass
        finally:
            db.conn.close()
        return ''

    def _load_asset_info(self, ticker: str) -> dict:
        """Load longName, sector, and industry for ticker from asset_info.db."""
        info = {'longName': ticker, 'sector': '', 'industry': ''}
        db = tools.Db_tools(db_path=_DB_PATH, database_name='asset_info.db')
        try:
            df = pd.read_sql_query(
                "SELECT longName, sector, industry FROM asset_info WHERE ticker = ? LIMIT 1",
                db.conn,
                params=(ticker,),
            )
            if not df.empty:
                info.update(df.iloc[0].to_dict())
        except Exception as exc:
            logger.warning("BannerAiGenerator: asset_info lookup failed for %s: %s", ticker, exc)
        finally:
            db.conn.close()
        return info

    def _load_recent_ohlc(self, ticker: str) -> pd.DataFrame:
        """Load the last 20 OHLCV candles for ticker using the configured default interval."""
        db_name = f'yf_{ticker}.db'
        interval = self.sys_conf.get_value('interval', '1d') or '1d'
        table_map = {
            '1d': 'day_data',  '3d': 'day_data',
            '1wk': 'week_data', '1mo': 'month_data', '2mo': 'month_data',
        }
        table = table_map.get(interval, 'day_data')
        db = tools.Db_tools(db_path=_DB_PATH, database_name=db_name)
        try:
            df = pd.read_sql_query(
                f"SELECT Date, Open, High, Low, Close, Volume FROM {table} ORDER BY Date DESC LIMIT 20",
                db.conn,
            )
            df = df.sort_values('Date')
            return df
        except Exception as exc:
            logger.warning("BannerAiGenerator: OHLC load failed for %s: %s", ticker, exc)
            return pd.DataFrame()
        finally:
            db.conn.close()

    # ── persistence ──────────────────────────────────────────────────────────

    def _save_to_banner_notes(self, ticker: str, text: str):
        """Upsert the analysis text for ticker into banner_notes.db (old entries are replaced)."""
        db_table = 'banner_notes'
        today    = dt.datetime.now().strftime('%Y-%m-%d')
        db = tools.Db_tools(db_path=_DB_PATH, database_name=f'{db_table}.db')
        try:
            db.conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {db_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT, text TEXT, buyDate TEXT
                )
            """)
            # Spalte buyDate nachrüsten falls Tabelle ohne sie angelegt wurde
            try:
                db.conn.execute(f"ALTER TABLE {db_table} ADD COLUMN buyDate TEXT")
                db.conn.commit()
                logger.info("banner_notes: buyDate-Spalte ergänzt")
            except Exception:
                pass  # Spalte existiert bereits → ignorieren
            db.conn.execute(f"DELETE FROM {db_table} WHERE ticker = ?", (ticker,))
            db.conn.execute(
                f"INSERT INTO {db_table} (ticker, text, buyDate) VALUES (?, ?, ?)",
                (ticker, text, today),
            )
            db.conn.commit()
        except Exception as exc:
            logger.exception("BannerAiGenerator: could not save to banner_notes: %s", exc)
            raise
        finally:
            db.conn.close()
