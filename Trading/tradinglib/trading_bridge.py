import sqlite3
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Optional

from tradinglib.tools import Tools, ExpressionEvaluatorNew as ExpressionEvaluator
from tradinglib.broker_base import TradingBroker
from tradinglib.broker_alpaca import AlpacaBroker
from tradinglib.broker_ibkr import IBKRBroker
from tradinglib.system_config import SystemConfig

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Order log                                                           #
# ------------------------------------------------------------------ #

class OrderLog(Tools):
    """Persists every submitted order in trading.db, independent of broker."""

    def __init__(self, db_path: str = 'database'):
        self._db = self.get_path(path=db_path, file_name='trading.db')
        self._ensure_table()

    def _ensure_table(self):
        with sqlite3.connect(self._db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS broker_orders (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    mode            TEXT,
                    broker          TEXT,
                    strategy        TEXT,
                    ticker          TEXT,
                    broker_symbol   TEXT,
                    action          TEXT,
                    qty             REAL,
                    signal_price    REAL,
                    order_id        TEXT,
                    status          TEXT,
                    signal_date     TEXT,
                    submitted_at    TEXT,
                    filled_at       TEXT,
                    fill_price      REAL,
                    error_msg       TEXT
                )
            """)

    def save(
        self,
        *,
        mode: str,
        broker: str,
        strategy: str,
        ticker: str,
        broker_symbol: str,
        action: str,
        qty: float,
        signal_price: float,
        order_id: str,
        status: str,
        signal_date: str,
        error_msg: str = '',
    ):
        now = datetime.now().isoformat()
        with sqlite3.connect(self._db) as conn:
            conn.execute("""
                INSERT INTO broker_orders
                    (mode, broker, strategy, ticker, broker_symbol, action, qty,
                     signal_price, order_id, status, signal_date, submitted_at, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (mode, broker, strategy, ticker, broker_symbol, action, qty,
                  signal_price, order_id, status, signal_date, now, error_msg))

    def update_fill(self, order_id: str, fill_price: float):
        now = datetime.now().isoformat()
        with sqlite3.connect(self._db) as conn:
            conn.execute("""
                UPDATE broker_orders
                SET status='filled', fill_price=?, filled_at=?
                WHERE order_id=?
            """, (fill_price, now, order_id))

    def get_orders_df(
        self,
        mode: Optional[str] = None,
        broker: Optional[str] = None,
        strategy: Optional[str] = None,
    ) -> pd.DataFrame:
        query = "SELECT * FROM broker_orders WHERE 1=1"
        params: list = []
        if mode:
            query += " AND mode=?"
            params.append(mode)
        if broker:
            query += " AND broker=?"
            params.append(broker)
        if strategy:
            query += " AND strategy=?"
            params.append(strategy)
        query += " ORDER BY submitted_at DESC"
        try:
            with sqlite3.connect(self._db) as conn:
                return pd.read_sql_query(query, conn, params=params)
        except Exception as e:
            logger.error(f"OrderLog.get_orders_df failed: {e}")
            return pd.DataFrame()

    # ------------------------------------------------------------------ #
    #  Backtest result persistence                                         #
    # ------------------------------------------------------------------ #

    def save_backtest(self, df: pd.DataFrame, username: str = '') -> None:
        """Persist a MultiTransactionProcessor trades_df to trading.db.

        Replaces any previous backtest for the same username.
        Only the columns needed by _tab_compare() are stored; extra columns
        are silently dropped so schema changes in multi_transaction.py don't break this.
        """
        keep = [c for c in ['sellDate', 'buyDate', 'ticker', 'strategy',
                             'gain', 'Strategy', 'cumulative_gain']
                if c in df.columns]
        if not keep:
            return
        store = df[keep].copy()
        # normalise: prefer 'strategy' column name (lower-case) if present
        if 'Strategy' in store.columns and 'strategy' not in store.columns:
            store = store.rename(columns={'Strategy': 'strategy'})
        store['username'] = username
        store['saved_at'] = datetime.now().isoformat()
        try:
            with sqlite3.connect(self._db) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS backtest_trades ("
                             "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                             "username TEXT, saved_at TEXT,"
                             "sellDate TEXT, buyDate TEXT, ticker TEXT,"
                             "strategy TEXT, gain REAL, cumulative_gain REAL)")
                conn.execute("DELETE FROM backtest_trades WHERE username=?",
                             (username,))
                store.to_sql('backtest_trades', conn,
                             if_exists='append', index=False)
            logger.info("Backtest saved: %d rows for user '%s'", len(store), username)
        except Exception as e:
            logger.error("save_backtest failed: %s", e)

    def get_backtest_df(self, username: str = '') -> pd.DataFrame:
        """Load the most recently saved backtest for a user."""
        try:
            with sqlite3.connect(self._db) as conn:
                return pd.read_sql_query(
                    "SELECT * FROM backtest_trades WHERE username=? "
                    "ORDER BY sellDate",
                    conn, params=(username,),
                )
        except Exception as e:
            logger.debug("get_backtest_df: %s", e)
            return pd.DataFrame()

    def get_open_tickers(self, strategy: str, mode: str, broker: str) -> set[str]:
        """Returns tickers that have an open buy with no matching sell."""
        try:
            with sqlite3.connect(self._db) as conn:
                rows = conn.execute("""
                    SELECT ticker, action FROM broker_orders
                    WHERE strategy=? AND mode=? AND broker=?
                    AND status IN ('submitted', 'filled')
                    ORDER BY submitted_at
                """, (strategy, mode, broker)).fetchall()
        except Exception as e:
            logger.error(f"get_open_tickers failed: {e}")
            return set()
        open_pos: set[str] = set()
        for ticker, action in rows:
            if action == 'buy':
                open_pos.add(ticker)
            elif action == 'sell':
                open_pos.discard(ticker)
        return open_pos


# ------------------------------------------------------------------ #
#  Signal evaluator                                                    #
# ------------------------------------------------------------------ #

class SignalEvaluator(Tools):
    """Evaluates today's buy/sell signals for a strategy using the same
    expression engine as MultiTransactionProcessor, but on the latest date only."""

    def __init__(self, username: str, db_path: str = 'database'):
        self.username = username
        self.db_path = db_path

    @staticmethod
    def _best_sim_db(db_path: str) -> str:
        """Return the simulation DB filename that contains the most recent data.

        Priority order (per project naming convention):
          1. asset_simulation_.db  — PRIMARY: written by  asset_perf2.py  (no year
             argument); always contains the most up-to-date data.
          2. asset_simulation_{year}.db — FALLBACK: per-year archives written with
             the /year:YYYY argument; walk back up to 4 years.
          3. asset_simulation_all.db — weekly all-ticker run (last resort).

        The OLD  asset_simulation.db  (no underscore suffix) is intentionally NOT
        probed here — it belongs to the pre-refactor version of the app.
        """
        import datetime, os
        current_year = datetime.datetime.now().year
        candidates = (
            ["asset_simulation_.db"]
            + [f"asset_simulation_{y}.db" for y in range(current_year, current_year - 4, -1)]
            + ["asset_simulation_all.db"]
        )
        for fname in candidates:
            full = Tools().get_path(path=db_path, file_name=fname)
            if os.path.exists(full) and os.path.getsize(full) > 4096:  # >4 KB → not empty
                return fname
        return "asset_simulation_.db"  # absolute fallback

    def get_signals(
        self,
        strategy_name: str,
        index_name: str,
        strategy_config: dict,
    ) -> tuple[list[dict], str]:
        """Evaluate today's signals for one (strategy, index) combination.

        Parameters
        ----------
        strategy_name : str
            Human-readable name of the strategy group, e.g.
            "Support/Resistance Strategy".  Stored in each returned signal
            so the UI can group / filter by strategy.
        index_name : str
            The market index key as stored in yf_tickers.db, e.g. "^SPX".
            Used for the simulation-DB query.
        strategy_config : dict
            The per-index config block:
            {buy, sell, num_assets, invest, order_by, [currency]}.

        Returns ``(signals, error_msg)``.  ``error_msg`` is empty on success.
        """
        from tradinglib import asset_simulator as ass
        from tradinglib import make_query as mq

        try:
            sim_db = self._best_sim_db(self.db_path)
            logger.debug(
                "SignalEvaluator: %s for strategy '%s' / index '%s'",
                sim_db, strategy_name, index_name,
            )

            simulator = ass.AssetSimulator(
                "yf_tickers.db", sim_db, "asset_info.db",
                index_name,                       # 4th arg = index for DB lookup
                db_path=self.db_path,
                username=self.username,
            )
            simulator.index_column = index_name

            order_by = strategy_config.get('order_by', 'sortino')
            currency = strategy_config.get('currency', 'ANY')

            # Resolve the index name in yf_tickers.db.
            # The JSON already contains ^ prefixes (e.g. '^SPX'), but keep the
            # resolution as a safety fallback for configs that omit the caret.
            resolved_index = index_name
            try:
                conn = simulator.ticker_conn
                exact = conn.execute(
                    "SELECT COUNT(*) FROM indices WHERE name = ?", (index_name,)
                ).fetchone()[0]
                if exact == 0:
                    caret = conn.execute(
                        "SELECT COUNT(*) FROM indices WHERE name = ?",
                        (f'^{index_name}',)
                    ).fetchone()[0]
                    if caret > 0:
                        resolved_index = f'^{index_name}'
                    else:
                        like = conn.execute(
                            "SELECT name FROM indices WHERE name LIKE ? LIMIT 1",
                            (f'%{index_name}',)
                        ).fetchone()
                        if like:
                            resolved_index = like[0]
            except Exception as exc:
                logger.debug(
                    "Index resolution failed for '%s': %s", index_name, exc
                )

            if resolved_index != index_name:
                logger.debug(
                    "SignalEvaluator: resolved index '%s' → '%s'",
                    index_name, resolved_index,
                )

            q_ext = ''
            if currency != 'ANY':
                q_ext += f' AND ai.currency = "{currency}"'
            if order_by:
                q_ext += f' ORDER BY {order_by} DESC'

            query = mq.make_query(
                'asset_simulation', resolved_index, 1,
                q=1, q_ext=q_ext, conn=simulator.ticker_conn,
            )
            combined_df = pd.read_sql_query(query, simulator.ticker_conn)
            if combined_df is not None:
                combined_df['stockIndex'] = resolved_index

            if combined_df is None or combined_df.empty:
                resolved_note = (
                    f" (aufgelöst zu '{resolved_index}')"
                    if resolved_index != index_name else ""
                )
                return [], (
                    f"Keine Daten für Index '{index_name}'{resolved_note}. "
                    f"Mögliche Ursachen:\n"
                    f"  1. '{index_name}' existiert nicht in yf_tickers.db "
                    f"(Tabelle 'indices', Spalte 'name').\n"
                    f"  2. asset_simulation_.db enthält noch keine Daten für diesen Index "
                    f"— bitte 'python asset_perf2.py' ausführen.\n"
                    f"  (Verwendete DB: {sim_db})"
                )

            buy_raw  = strategy_config.get('buy', '')
            sell_raw = strategy_config.get('sell', '')
            evaluator = ExpressionEvaluator(combined_df, dataframe_name='combined_df')

            buy_err = sell_err = ''
            try:
                buy_expr = evaluator.validate_and_transform(buy_raw)
            except Exception as exc:
                buy_expr = ''
                buy_err  = str(exc)

            try:
                sell_expr = evaluator.validate_and_transform(sell_raw)
            except Exception as exc:
                sell_expr = ''
                sell_err  = str(exc)

            combined_df = combined_df.copy()
            combined_df['_signal'] = 0

            if buy_expr:
                try:
                    combined_df['_signal'] = np.where(
                        eval(buy_expr), 1, combined_df['_signal']  # noqa: S307
                    )
                except Exception as exc:
                    buy_err = f"buy eval: {exc}"

            if sell_expr:
                try:
                    combined_df['_signal'] = np.where(
                        eval(sell_expr), -1, combined_df['_signal']  # noqa: S307
                    )
                except Exception as exc:
                    sell_err = f"sell eval: {exc}"

            latest_date = combined_df['Date'].max()
            triggered = combined_df[
                (combined_df['Date'] == latest_date) & (combined_df['_signal'] != 0)
            ]

            signals: list[dict] = []
            for _, row in triggered.iterrows():
                price = float(row.get('Close', 0))

                # ATR: try several column names from different indicator modules
                atr_raw = (
                    row.get('atr') or
                    row.get('bos_atr') or
                    row.get('mmm_atr') or
                    0.0
                )
                atr = float(atr_raw) if atr_raw else 0.0
                if atr <= 0:
                    # Fallback: approximate daily ATR from annualised volatility
                    vola_ann = float(row.get('vola', 0) or 0)
                    if vola_ann > 0 and price > 0:
                        atr = round(price * vola_ann / (252 ** 0.5), 4)

                signals.append({
                    'strategy':  strategy_name,      # e.g. "Support/Resistance Strategy"
                    'index':     resolved_index,      # e.g. "^SPX"
                    'ticker':    row['ticker'],
                    'longName':  row.get('longName', row['ticker']),
                    'signal':    'buy' if row['_signal'] == 1 else 'sell',
                    'price':     price,
                    'currency':  row.get('currency', 'USD'),
                    'date':      str(latest_date)[:10],
                    'score':     float(row.get(order_by, 0)),
                    'vola':      max(float(row.get('vola', 1.0) or 1.0), 1e-6),
                    'atr':       round(atr, 4),
                })

            expr_errors = '  |  '.join(e for e in [buy_err, sell_err] if e)
            return signals, expr_errors

        except Exception as exc:
            logger.error(
                "Signal evaluation failed for '%s' / '%s': %s",
                strategy_name, index_name, exc,
            )
            return [], str(exc)


# ------------------------------------------------------------------ #
#  Broker factory                                                      #
# ------------------------------------------------------------------ #

class BrokerFactory:
    @staticmethod
    def create(broker_id: str, config: dict) -> TradingBroker:
        if broker_id == 'alpaca':
            return AlpacaBroker(
                api_key=config.get('alpaca_key') or None,
                secret_key=config.get('alpaca_secret') or None,
                paper=True,
            )
        if broker_id == 'ibkr':
            return IBKRBroker(
                host=config.get('ibkr_host', '127.0.0.1'),
                port=int(config.get('ibkr_port', 7497)),
            )
        raise ValueError(f"Unknown broker_id: {broker_id!r}")


# ------------------------------------------------------------------ #
#  Order sizing                                                        #
# ------------------------------------------------------------------ #

def calc_qty(budget_per_position: float, price: float, x_rate: float = 1.0) -> float:
    """Returns integer share count from a per-position budget and current price."""
    if price <= 0:
        return 0.0
    return max(1.0, float(int(budget_per_position * x_rate / price)))
