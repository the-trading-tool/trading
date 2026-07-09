import sqlite3
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Optional

from tradinglib.tools import Tools, ExpressionEvaluator
from tradinglib.premium.broker_base import TradingBroker
from tradinglib.premium.broker_alpaca import AlpacaBroker
from tradinglib.premium.broker_ibkr import IBKRBroker
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
                    error_msg       TEXT,
                    submit_after    TEXT
                )
            """)
            try:
                conn.execute("ALTER TABLE broker_orders ADD COLUMN submit_after TEXT")
            except Exception:
                pass  # column already exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS position_trails (
                    ticker          TEXT,
                    broker_symbol   TEXT,
                    strategy        TEXT,
                    mode            TEXT,
                    broker          TEXT,
                    entry_price     REAL,
                    high_water_mark REAL,
                    trail_stop      REAL,
                    atr             REAL,
                    atr_mult        REAL,
                    last_price      REAL,
                    breached        INTEGER DEFAULT 0,
                    updated_at      TEXT,
                    PRIMARY KEY (ticker, mode, broker)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS broker_activities (
                    id               TEXT PRIMARY KEY,
                    broker           TEXT,
                    mode             TEXT,
                    activity_type    TEXT,
                    transaction_time TEXT,
                    symbol           TEXT,
                    side             TEXT,
                    qty              REAL,
                    price            REAL,
                    cum_qty          REAL,
                    leaves_qty       REAL,
                    order_id         TEXT,
                    order_status     TEXT,
                    net_amount       REAL,
                    description      TEXT,
                    imported_at      TEXT
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

    def update_fill(self, order_id: str, fill_price: float, filled_at: str = ''):
        ts = filled_at or datetime.now().isoformat()
        with sqlite3.connect(self._db) as conn:
            conn.execute("""
                UPDATE broker_orders
                SET status='filled', fill_price=?, filled_at=?
                WHERE order_id=?
            """, (fill_price, ts, order_id))

    def has_buy_for_date(
        self,
        ticker:      str,
        mode:        str,
        broker:      str,
        signal_date: str,
        exclude_id:  'int | None' = None,
    ) -> bool:
        """Return True if a non-error buy for *ticker* already exists for *signal_date*.

        *exclude_id* skips a specific row by primary key — used when auto-submitting
        a scheduled order so the row itself is not counted as a duplicate.
        """
        query = """
            SELECT 1 FROM broker_orders
            WHERE ticker=? AND action='buy' AND mode=? AND broker=?
            AND signal_date=?
            AND status NOT IN ('error', 'canceled', 'cancelled', 'expired')
        """
        params: list = [ticker, mode, broker, signal_date]
        if exclude_id is not None:
            query += " AND id != ?"
            params.append(exclude_id)
        query += " LIMIT 1"
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(query, params).fetchone()
        return row is not None

    # ------------------------------------------------------------------ #
    #  Queued (staged) manual orders — review & send later                #
    # ------------------------------------------------------------------ #

    def save_queued(
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
        signal_date: str,
        submit_after: 'str | None' = None,
    ) -> None:
        """Stage a manual or scheduled order (status='queued') without contacting the broker.

        When *submit_after* is set (ISO timestamp), the order is considered
        scheduled: the Positions tab auto-submits it once that time has passed.
        Without *submit_after* the order is manual and waits for explicit user action.
        """
        now = datetime.now().isoformat()
        with sqlite3.connect(self._db) as conn:
            conn.execute("""
                INSERT INTO broker_orders
                    (mode, broker, strategy, ticker, broker_symbol, action, qty,
                     signal_price, order_id, status, signal_date, submitted_at,
                     error_msg, submit_after)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 'queued', ?, ?, '', ?)
            """, (mode, broker, strategy, ticker, broker_symbol, action, qty,
                  signal_price, signal_date, now, submit_after))

    def get_queued_orders(self, mode: str | None = None, broker: str | None = None) -> 'pd.DataFrame':
        """Return staged-but-not-yet-sent orders (status='queued'), newest first."""
        query = "SELECT * FROM broker_orders WHERE status='queued'"
        params: list = []
        if mode:
            query += " AND mode=?"
            params.append(mode)
        if broker:
            query += " AND broker=?"
            params.append(broker)
        query += " ORDER BY id DESC"
        try:
            with sqlite3.connect(self._db) as conn:
                return pd.read_sql_query(query, conn, params=params)
        except Exception as e:
            logger.error('get_queued_orders failed: %s', e)
            return pd.DataFrame()

    def get_overdue_scheduled(
        self,
        mode:   'str | None' = None,
        broker: 'str | None' = None,
    ) -> 'pd.DataFrame':
        """Return queued orders whose submit_after time has passed (ready to auto-submit).

        Only includes rows with a non-NULL submit_after that is <= now.
        Manual queued orders (submit_after IS NULL) are never returned here.
        """
        now = datetime.now().isoformat()
        query = (
            "SELECT * FROM broker_orders "
            "WHERE status='queued' AND submit_after IS NOT NULL AND submit_after <= ?"
        )
        params: list = [now]
        if mode:
            query += " AND mode=?"
            params.append(mode)
        if broker:
            query += " AND broker=?"
            params.append(broker)
        query += " ORDER BY submit_after"
        try:
            with sqlite3.connect(self._db) as conn:
                return pd.read_sql_query(query, conn, params=params)
        except Exception as e:
            logger.error('get_overdue_scheduled failed: %s', e)
            return pd.DataFrame()

    def mark_queued_sent(self, row_id: int, order_id: str, status: str, error_msg: str = ''):
        """Update a queued row after submitting it to the broker (sent or error)."""
        now = datetime.now().isoformat()
        with sqlite3.connect(self._db) as conn:
            conn.execute("""
                UPDATE broker_orders
                SET order_id=?, status=?, submitted_at=?, error_msg=?
                WHERE id=?
            """, (order_id, status, now, error_msg, row_id))

    def delete_queued_order(self, row_id: int):
        """Discard a staged order that hasn't been sent yet."""
        with sqlite3.connect(self._db) as conn:
            conn.execute("DELETE FROM broker_orders WHERE id=? AND status='queued'", (row_id,))

    def clear_orders(self, mode: str | None = None, broker: str | None = None):
        """Clear order *history* (optionally filtered by mode/broker).

        Staged manual orders (status='queued') are preserved: they are not part of
        the broker/Alpaca history, they live in the "Queued Orders" list until the
        user explicitly sends or discards them. Clearing the history must not throw
        them away — only sent/synced rows are removed.
        """
        query = "DELETE FROM broker_orders WHERE status != 'queued'"
        params: list = []
        if mode:
            query += " AND mode=?"
            params.append(mode)
        if broker:
            query += " AND broker=?"
            params.append(broker)
        with sqlite3.connect(self._db) as conn:
            conn.execute(query, params)

    # ------------------------------------------------------------------ #
    #  Activity log                                                        #
    # ------------------------------------------------------------------ #

    def sync_activities(
        self,
        broker,          # BrokerBase instance
        broker_id: str,
        mode: str,
        activity_types: list[str] | None = None,
        after: str | None = None,
    ) -> dict:
        """Import Alpaca account activities (fills, fees, …) into broker_activities.

        Returns {'imported': N, 'errors': [...]}
        """
        summary = {'imported': 0, 'errors': []}
        try:
            items = broker.get_activities(
                activity_types=activity_types or ['FILL', 'FEE'],
                after=after,
            )
        except Exception as e:
            summary['errors'].append(f'get_activities failed: {e}')
            return summary

        now = datetime.now().isoformat()
        for item in items:
            try:
                with sqlite3.connect(self._db) as conn:
                    conn.execute("""
                        INSERT OR IGNORE INTO broker_activities
                            (id, broker, mode, activity_type, transaction_time,
                             symbol, side, qty, price, cum_qty, leaves_qty,
                             order_id, order_status, net_amount, description, imported_at)
                        VALUES (?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?)
                    """, (
                        item['id'], broker_id, mode,
                        item['activity_type'], item['transaction_time'],
                        item['symbol'], item['side'],
                        item['qty'], item['price'],
                        item['cum_qty'], item['leaves_qty'],
                        item['order_id'], item['order_status'],
                        item['net_amount'], item['description'],
                        now,
                    ))
                    if conn.execute('SELECT changes()').fetchone()[0] > 0:
                        summary['imported'] += 1
            except Exception as e:
                summary['errors'].append(f"activity {item.get('id','?')[:8]}: {e}")

        logger.info('sync_activities: imported=%d errors=%d',
                    summary['imported'], len(summary['errors']))
        return summary

    def get_activities_df(
        self,
        broker: str | None = None,
        mode: str | None = None,
        activity_type: str | None = None,
    ) -> 'pd.DataFrame':
        query = "SELECT * FROM broker_activities WHERE 1=1"
        params: list = []
        if broker:
            query += " AND broker=?"
            params.append(broker)
        if mode:
            query += " AND mode=?"
            params.append(mode)
        if activity_type:
            query += " AND activity_type=?"
            params.append(activity_type)
        query += " ORDER BY transaction_time DESC"
        try:
            with sqlite3.connect(self._db) as conn:
                return pd.read_sql_query(query, conn, params=params)
        except Exception as e:
            logger.error(f'get_activities_df failed: {e}')
            return pd.DataFrame()

    def clear_activities(self, broker: str | None = None, mode: str | None = None):
        query = "DELETE FROM broker_activities WHERE 1=1"
        params: list = []
        if broker:
            query += " AND broker=?"
            params.append(broker)
        if mode:
            query += " AND mode=?"
            params.append(mode)
        with sqlite3.connect(self._db) as conn:
            conn.execute(query, params)

    # ------------------------------------------------------------------ #
    #  Broker sync                                                         #
    # ------------------------------------------------------------------ #

    def sync_from_broker(
        self,
        broker,
        mode: str,
        broker_id: str,
    ) -> dict:
        """Pull all Alpaca orders into the local log and update fill data.

        Steps:
          0. Import every Alpaca order not yet in the DB  (works even on empty DB)
          1. Update pending local orders with real fill price / filled_at
          2. Detect positions closed outside the app (stop-loss, manual Alpaca)

        Returns summary dict with keys:
          imported, fills_updated, external_closes, errors
        """
        summary = {'imported': 0, 'fills_updated': 0, 'external_closes': 0, 'errors': []}

        # ── Fetch all Alpaca orders once ─────────────────────────────────
        try:
            alpaca_orders = broker.get_orders(status='all')
        except Exception as e:
            summary['errors'].append(f'get_orders failed: {e}')
            return summary

        alpaca_by_id = {o['id']: o for o in alpaca_orders}

        # ── Step 0: Import orders not yet in DB ──────────────────────────
        try:
            with sqlite3.connect(self._db) as conn:
                known_ids = {
                    r[0] for r in conn.execute(
                        "SELECT order_id FROM broker_orders WHERE broker=? AND mode=?",
                        (broker_id, mode),
                    ).fetchall()
                }
        except Exception as e:
            summary['errors'].append(f'known_ids query failed: {e}')
            known_ids = set()

        for o in alpaca_orders:
            oid = o['id']
            if oid in known_ids:
                continue
            raw_fp     = o.get('filled_avg_price')
            fill_price = float(raw_fp) if raw_fp else None
            filled_at  = o.get('filled_at') or None
            created_at = o.get('created_at') or datetime.now().isoformat()
            symbol     = o.get('symbol', '')
            side       = 'buy' if 'buy' in str(o.get('side', '')).lower() else 'sell'
            qty        = float(o.get('qty') or 0) or None
            status     = str(o.get('status', 'unknown'))
            signal_date = (filled_at or created_at)[:10]
            try:
                with sqlite3.connect(self._db) as conn:
                    conn.execute("""
                        INSERT INTO broker_orders
                            (mode, broker, strategy, ticker, broker_symbol,
                             action, qty, signal_price, order_id, status,
                             signal_date, submitted_at, filled_at, fill_price,
                             error_msg)
                        VALUES (?, ?, '', ?, ?,
                                ?, ?, NULL, ?, ?,
                                ?, ?, ?, ?,
                                'alpaca_import')
                    """, (
                        mode, broker_id, symbol, symbol,
                        side, qty, oid, status,
                        signal_date, created_at, filled_at, fill_price,
                    ))
                summary['imported'] += 1
            except Exception as e:
                summary['errors'].append(f'import {oid[:8]}: {e}')

        # ── Step 1: Update pending local orders with real fill data ──────
        try:
            with sqlite3.connect(self._db) as conn:
                pending = conn.execute("""
                    SELECT order_id FROM broker_orders
                    WHERE mode=? AND broker=?
                    AND status IN ('submitted', 'accepted', 'pending', 'new')
                    AND order_id IS NOT NULL AND order_id != ''
                    AND (error_msg IS NULL OR error_msg != 'alpaca_import')
                """, (mode, broker_id)).fetchall()
        except Exception as e:
            summary['errors'].append(f'pending query failed: {e}')
            pending = []

        for (oid,) in pending:
            alpaca_ord = alpaca_by_id.get(oid)
            if not alpaca_ord:
                continue
            if alpaca_ord.get('status', '') in (
                'filled', 'partially_filled', 'canceled', 'expired', 'replaced'
            ):
                new_status = alpaca_ord['status']
                raw_price  = alpaca_ord.get('filled_avg_price')
                fill_price = float(raw_price) if raw_price else None
                filled_at  = alpaca_ord.get('filled_at') or None
                try:
                    with sqlite3.connect(self._db) as conn:
                        conn.execute("""
                            UPDATE broker_orders
                            SET status=?, fill_price=?, filled_at=?
                            WHERE order_id=? AND (error_msg IS NULL OR error_msg != 'alpaca_import')
                        """, (new_status, fill_price, filled_at, oid))
                    summary['fills_updated'] += 1
                except Exception as e:
                    summary['errors'].append(f'update fill {oid[:8]}: {e}')

        # ── Step 2: Detect externally-closed positions ───────────────────
        try:
            alpaca_positions = {p.broker_symbol for p in broker.get_positions()}
        except Exception as e:
            summary['errors'].append(f'get_positions failed: {e}')
            alpaca_positions = set()

        try:
            with sqlite3.connect(self._db) as conn:
                strategies = [
                    r[0] for r in conn.execute(
                        "SELECT DISTINCT strategy FROM broker_orders "
                        "WHERE mode=? AND broker=? AND strategy != ''",
                        (mode, broker_id),
                    ).fetchall()
                ]
        except Exception as e:
            summary['errors'].append(f'strategies query failed: {e}')
            strategies = []

        for strat in strategies:
            locally_open = self.get_open_tickers(strat, mode, broker_id)
            for ticker in locally_open - alpaca_positions:

                # ── Guard: only act when there is at least one FILLED buy
                # (pending/submitted buys are not yet open positions)
                try:
                    with sqlite3.connect(self._db) as conn:
                        filled_buy = conn.execute("""
                            SELECT qty, signal_price, fill_price FROM broker_orders
                            WHERE mode=? AND broker=? AND ticker=?
                            AND action='buy' AND status='filled'
                            ORDER BY submitted_at DESC LIMIT 1
                        """, (mode, broker_id, ticker)).fetchone()
                except Exception:
                    filled_buy = None

                if not filled_buy:
                    continue  # only pending buys → not an open position yet

                # ── Find matching filled sell on Alpaca ───────────────────
                sell_ord = next(
                    (o for o in sorted(
                        alpaca_orders,
                        key=lambda x: x.get('filled_at') or '',
                        reverse=True,
                    )
                     if o['symbol'] == ticker
                     and 'sell' in str(o.get('side', '')).lower()
                     and o.get('status', '') == 'filled'),
                    None,
                )

                # ── Skip if this sell order was already imported in step 0 ─
                if sell_ord:
                    try:
                        with sqlite3.connect(self._db) as conn:
                            already = conn.execute(
                                "SELECT 1 FROM broker_orders WHERE order_id=? AND action='sell'",
                                (sell_ord['id'],)
                            ).fetchone()
                    except Exception:
                        already = None
                    if already:
                        # The sell was already imported — just tag it as external_close
                        try:
                            with sqlite3.connect(self._db) as conn:
                                conn.execute(
                                    "UPDATE broker_orders SET error_msg='external_close'"
                                    " WHERE order_id=? AND error_msg IN ('alpaca_import', '')",
                                    (sell_ord['id'],)
                                )
                            summary['external_closes'] += 1
                        except Exception as e:
                            summary['errors'].append(f'tag ext_close {ticker}: {e}')
                        continue  # don't insert a duplicate row

                raw_fp     = sell_ord.get('filled_avg_price') if sell_ord else None
                fill_price = float(raw_fp) if raw_fp else None
                filled_at  = (sell_ord.get('filled_at') or None) if sell_ord else None
                sell_qty   = float(sell_ord['qty']) if sell_ord else None
                order_id   = sell_ord['id'] if sell_ord else f'ext_{ticker}_{mode}'

                entry_qty   = sell_qty or (float(filled_buy[0]) if filled_buy[0] else None)
                entry_price = (
                    (float(filled_buy[2]) if filled_buy[2] else None)
                    or (float(filled_buy[1]) if filled_buy[1] else None)
                )

                try:
                    with sqlite3.connect(self._db) as conn:
                        exists = conn.execute(
                            "SELECT 1 FROM broker_orders WHERE order_id=?", (order_id,)
                        ).fetchone()
                        if not exists:
                            submitted_ts = filled_at or datetime.now().isoformat()
                            signal_date  = (
                                filled_at[:10] if filled_at and len(filled_at) >= 10
                                else datetime.now().strftime('%Y-%m-%d')
                            )
                            conn.execute("""
                                INSERT INTO broker_orders
                                    (mode, broker, strategy, ticker, broker_symbol,
                                     action, qty, signal_price, order_id, status,
                                     signal_date, submitted_at, filled_at, fill_price,
                                     error_msg)
                                VALUES (?, ?, ?, ?, ?,
                                        'sell', ?, ?, ?, 'filled',
                                        ?, ?, ?, ?,
                                        'external_close')
                            """, (
                                mode, broker_id, strat, ticker, ticker,
                                entry_qty, entry_price,
                                order_id,
                                signal_date, submitted_ts,
                                filled_at, fill_price,
                            ))
                            summary['external_closes'] += 1
                except Exception as e:
                    summary['errors'].append(f'ext_close {ticker}: {e}')

        logger.info(
            'sync_from_broker: imported=%d fills_updated=%d external_closes=%d errors=%d',
            summary['imported'], summary['fills_updated'],
            summary['external_closes'], len(summary['errors']),
        )
        return summary

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
        """Returns tickers that have an open buy with no matching sell.

        Buys are scoped to the given strategy.
        Sells are matched strategy-agnostically (strategy='' from Alpaca imports
        or external closes also close out a named-strategy buy).
        """
        try:
            with sqlite3.connect(self._db) as conn:
                # Buys: only for this strategy
                buy_rows = conn.execute("""
                    SELECT ticker FROM broker_orders
                    WHERE strategy=? AND mode=? AND broker=?
                    AND action='buy'
                    AND status IN ('submitted', 'filled')
                    ORDER BY submitted_at
                """, (strategy, mode, broker)).fetchall()

                # Sells: ANY strategy for same mode/broker (Alpaca is position-level)
                sell_rows = conn.execute("""
                    SELECT ticker FROM broker_orders
                    WHERE mode=? AND broker=?
                    AND action='sell'
                    AND status IN ('filled', 'submitted', 'canceled', 'cancelled')
                    ORDER BY submitted_at
                """, (mode, broker)).fetchall()
        except Exception as e:
            logger.error(f"get_open_tickers failed: {e}")
            return set()

        open_pos: set[str] = {r[0] for r in buy_rows}
        for (ticker,) in sell_rows:
            open_pos.discard(ticker)
        return open_pos

    def get_open_position_strategies(self, mode: str, broker: str) -> dict:
        """Map each open position to the strategy under which it was bought.

        FIFO-aware: per ticker the still-open buy (after matching sells by
        quantity, strategy-agnostic) determines the strategy. Returns a dict
        keyed by BOTH the ticker and the broker_symbol so callers can look up
        by either. Values are the recorded strategy verbatim — '' for
        external/imported buys, 'quick' for manual quick orders, or a named
        strategy. Unlike get_open_tickers this covers ALL strategies, so
        positions opened outside a named strategy are still attributed.
        """
        out: dict[str, str] = {}
        try:
            with sqlite3.connect(self._db) as conn:
                rows = conn.execute("""
                    SELECT ticker, broker_symbol, strategy, action, status, qty, submitted_at
                    FROM broker_orders
                    WHERE mode=? AND broker=?
                    ORDER BY submitted_at
                """, (mode, broker)).fetchall()
        except Exception as e:
            logger.error(f"get_open_position_strategies failed: {e}")
            return out

        df = pd.DataFrame(
            rows,
            columns=['ticker', 'broker_symbol', 'strategy', 'action',
                     'status', 'qty', 'submitted_at'],
        )
        if df.empty:
            return out

        for tk, grp in df.groupby('ticker'):
            buys  = grp[(grp['action'] == 'buy')
                        & (grp['status'].isin(['submitted', 'filled']))].copy()
            sells = grp[(grp['action'] == 'sell')
                        & (grp['status'].isin(['filled', 'submitted', 'canceled', 'cancelled']))]
            if buys.empty:
                continue
            sold = float(pd.to_numeric(sells['qty'], errors='coerce').fillna(0).sum())
            buys['_ts'] = pd.to_datetime(buys['submitted_at'], errors='coerce')
            buys = buys.sort_values('_ts')
            rem = sold
            for _, b in buys.iterrows():
                bq = float(pd.to_numeric(b['qty'], errors='coerce') or 0)
                if rem >= bq:
                    rem -= bq          # this buy is fully covered by earlier sells
                    continue
                strat = str(b['strategy'] or '')
                out[str(tk)] = strat
                bsym = str(b['broker_symbol'] or '')
                if bsym:
                    out[bsym] = strat
                break
        return out


# ------------------------------------------------------------------ #
#  Signal evaluator                                                    #
# ------------------------------------------------------------------ #

class SignalEvaluator(Tools):
    """Evaluates current signals for a strategy using the *exact* same
    PortfolioSimulator that MultiTransactionProcessor uses.

    Algorithm (identical to multi_transaction.py):
      1. Load ~400 days of history for all tickers in the index (q=3).
      2. Evaluate buy/sell conditions on the full DataFrame (vectorised).
      3. Sort by [Date asc, order_by desc] — same ordering PortfolioSimulator sees.
      4. Run PortfolioSimulator (enforces num_assets cap + inv-vola sizing).
      5. portfolio.portfolio.keys() → 'buy' (currently held) signals.
      6. Sells from history within sell_lookback_days → 'sell' signals.
    """

    def __init__(self, username: str, db_path: str = 'database',
                 sim_db_override: str | None = None):
        self.username = username
        self.db_path = db_path
        self.sim_db_override = sim_db_override  # None → auto-select via _best_sim_db()

    @staticmethod
    def available_sim_dbs(db_path: str = 'database') -> list[str]:
        """Return all asset_simulation_*.db files that exist and are non-empty."""
        import os, glob
        base = Tools().get_path(path=db_path, file_name='')
        pattern = os.path.join(base, 'asset_simulation_*.db')
        found = []
        for path in sorted(glob.glob(pattern)):
            if os.path.getsize(path) > 4096:
                found.append(os.path.basename(path))
        return found or ['asset_simulation_.db']

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
        sell_lookback_days: int = 30,
    ) -> tuple[list[dict], str]:
        """Evaluate current signals for one (strategy, index) combination.

        Uses the exact same PortfolioSimulator as multi_transaction.py so that
        the num_assets cap, inv-vola sizing, and portfolio-full logic are all
        reproduced faithfully.

        Parameters
        ----------
        strategy_name : str
            Human-readable strategy name, e.g. "Value Trend Strategy".
        index_name : str
            Index key in yf_tickers.db, e.g. "^SPX".
        strategy_config : dict
            Per-index config: {buy, sell, num_assets, invest, order_by, [currency]}.
        sell_lookback_days : int
            How many calendar days back to include *closed* positions (sell signals).
            Open (currently held) positions are always included.

        Returns ``(signals, error_msg)``.  ``error_msg`` is empty on success.
        """
        import datetime as _dt
        from tradinglib.premium import asset_simulator as ass
        from tradinglib import make_query as mq

        try:
            sim_db = self.sim_db_override or self._best_sim_db(self.db_path)
            logger.debug(
                "SignalEvaluator: %s for strategy '%s' / index '%s'",
                sim_db, strategy_name, index_name,
            )

            simulator = ass.AssetSimulator(
                "yf_tickers.db", sim_db, "asset_info.db",
                index_name,
                db_path=self.db_path,
                username=self.username,
            )
            simulator.index_column = index_name

            order_by  = strategy_config.get('order_by', 'sortino')
            currency  = strategy_config.get('currency', 'ANY')
            num_assets = int(strategy_config.get('num_assets', 6))
            invest     = float(strategy_config.get('invest', 10000))

            # ── Resolve index name ───────────────────────────────────────
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
                logger.debug("Index resolution failed for '%s': %s", index_name, exc)

            if resolved_index != index_name:
                logger.debug(
                    "SignalEvaluator: resolved '%s' → '%s'", index_name, resolved_index
                )
            simulator.index_column = resolved_index

            # ── Load historical data (q=3, last 400 days) ───────────────
            # 400 days covers all realistic open-position lookback periods
            # while keeping the query fast.
            q_ext = ''
            if currency != 'ANY':
                q_ext += f' AND ai.currency = "{currency}"'
            q_ext += " AND ap.Date >= date('now', '-400 days')"
            # No ORDER BY here — we sort in Python to match multi_transaction.py

            query = mq.make_query(
                'asset_simulation', resolved_index, 1,
                q=3, q_ext=q_ext, conn=simulator.ticker_conn,
            )
            combined_df = pd.read_sql_query(query, simulator.ticker_conn)

            if combined_df is None or combined_df.empty:
                note = (
                    f" (resolved to '{resolved_index}')"
                    if resolved_index != index_name else ""
                )
                return [], (
                    f"No data for index '{index_name}'{note}. "
                    f"Possible causes:\n"
                    f"  1. '{index_name}' not found in yf_tickers.db.\n"
                    f"  2. asset_simulation_.db has no data — run 'python asset_perf2.py'.\n"
                    f"  (DB used: {sim_db})"
                )

            combined_df['stockIndex'] = resolved_index
            combined_df = combined_df.drop_duplicates(
                subset=['Date', 'ticker'], keep='last'
            )

            # ── Ensure 'close' column for PortfolioSimulator ─────────────
            # PortfolioSimulator accesses row['close'] (lowercase).
            # The simulation DB stores Close (uppercase) from yfinance.
            if 'close' not in combined_df.columns:
                if 'Close' in combined_df.columns:
                    combined_df = combined_df.copy()
                    combined_df['close'] = combined_df['Close']
                else:
                    combined_df = combined_df.copy()
                    combined_df['close'] = 0.0

            # ── Sort exactly as multi_transaction.py does ────────────────
            # Date asc, then order_by desc so higher-scored tickers fill
            # portfolio slots first on any given date.
            sort_cols = ['Date']
            if order_by and order_by in combined_df.columns:
                sort_cols.append(order_by)
                combined_df = combined_df.sort_values(
                    sort_cols, ascending=[True, False]
                )
            else:
                combined_df = combined_df.sort_values('Date', ascending=True)

            # ── Evaluate buy / sell conditions (vectorised) ──────────────
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
            combined_df['buySell'] = 0

            if buy_expr:
                try:
                    combined_df['buySell'] = np.where(
                        eval(buy_expr), 1, combined_df['buySell']  # noqa: S307
                    )
                except Exception as exc:
                    buy_err = f"buy eval: {exc}"

            if sell_expr:
                try:
                    combined_df['buySell'] = np.where(
                        eval(sell_expr), -1, combined_df['buySell']  # noqa: S307
                    )
                except Exception as exc:
                    sell_err = f"sell eval: {exc}"

            # ── Run PortfolioSimulator (identical to multi_transaction.py) ─
            portfolio_sim = ass.PortfolioSimulator(
                data=combined_df,
                initial_cash=invest,
                max_assets=num_assets,
                username=self.username,
            )
            portfolio_sim.simulate()

            # ── Derive latest date and build a lookup for latest rows ─────
            latest_date     = combined_df['Date'].max()
            latest_date_str = str(latest_date)[:10]
            cutoff_date     = (
                _dt.date.today() - _dt.timedelta(days=sell_lookback_days)
            ).isoformat()

            latest_df = combined_df[combined_df['Date'] == latest_date]
            latest_by_ticker: dict = {
                row['ticker']: row
                for _, row in latest_df.iterrows()
            }

            # ── Extract open positions (buy signals) ─────────────────────
            open_tickers = set(portfolio_sim.portfolio.keys())

            # Most recent buy-history entry per open ticker gives the entry date/price
            entry_info: dict = {}  # ticker → {entry_date, entry_price}
            for h in portfolio_sim.history:
                if h['action'] == 'buy' and h['ticker'] in open_tickers:
                    entry_info[h['ticker']] = {
                        'entry_date':  str(h['timestamp'])[:10],
                        'entry_price': float(h.get('price', 0)),
                    }

            # ── Extract recent sell events (sell signals) ─────────────────
            # Key: ticker → most recent sell event within the lookback window.
            # If a ticker was sold and re-bought, we want the most recent sell.
            sell_events: dict = {}  # ticker → {sell_date, price}
            for h in portfolio_sim.history:
                if h['action'] == 'sell':
                    sell_date_str = str(h['timestamp'])[:10]
                    if sell_date_str >= cutoff_date:
                        # Keep latest sell within window
                        prev = sell_events.get(h['ticker'])
                        if prev is None or sell_date_str >= prev['sell_date']:
                            sell_events[h['ticker']] = {
                                'sell_date': sell_date_str,
                                'price':     float(h.get('price', 0)),
                            }

            # Exclude tickers that are currently open (re-bought after the sell)
            sell_events = {
                t: v for t, v in sell_events.items()
                if t not in open_tickers
            }

            # ── Helper: price and ATR from latest row ────────────────────
            def _row_price(row: pd.Series) -> float:
                for col in ('Close', 'close'):
                    v = row.get(col, None)
                    if v is not None and float(v) > 0:
                        return float(v)
                return 0.0

            def _row_atr(row: pd.Series, price: float) -> float:
                atr_raw = (
                    row.get('atr') or row.get('bos_atr') or row.get('mmm_atr') or 0.0
                )
                atr = float(atr_raw) if atr_raw else 0.0
                if atr <= 0:
                    vola_ann = float(row.get('vola', 0) or 0)
                    if vola_ann > 0 and price > 0:
                        atr = round(price * vola_ann / (252 ** 0.5), 4)
                return atr

            def _build(
                row: pd.Series,
                sig_type: str,
                entry_date: Optional[str] = None,
                sell_date:  Optional[str] = None,
                price_override: Optional[float] = None,
            ) -> dict:
                price = price_override if price_override else _row_price(row)
                return {
                    'strategy':   strategy_name,
                    'index':      resolved_index,
                    'ticker':     row['ticker'],
                    'longName':   row.get('longName', row['ticker']),
                    'signal':     sig_type,
                    'price':      price,
                    'currency':   row.get('currency', 'USD'),
                    'date':       latest_date_str,
                    'entry_date': entry_date,
                    'sell_date':  sell_date,
                    'score':      float(row.get(order_by, 0)),
                    'vola':       max(float(row.get('vola', 1.0) or 1.0), 1e-6),
                    'atr':        round(_row_atr(row, price), 4),
                }

            # ── Assemble signals ─────────────────────────────────────────
            signals: list[dict] = []

            for ticker in open_tickers:
                row = latest_by_ticker.get(ticker)
                if row is None:
                    continue
                info = entry_info.get(ticker, {})
                signals.append(_build(
                    row, 'buy',
                    entry_date=info.get('entry_date'),
                ))

            for ticker, ev in sell_events.items():
                row = latest_by_ticker.get(ticker)
                if row is None:
                    continue
                signals.append(_build(
                    row, 'sell',
                    sell_date=ev['sell_date'],
                    price_override=ev['price'] if ev['price'] > 0 else None,
                ))

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
    def _resolve_config(config) -> dict:
        """Accept either a plain dict or a SystemConfig instance."""
        if isinstance(config, dict):
            return config
        # SystemConfig (or any object with get_value)
        cfg: dict = {}
        try:
            from tradinglib import ksplib
            alpaca_ksp = config.get_value('alpaca_ksp_name', 'av-paper')
            db_path    = getattr(config, '_db_path', 'database')
            # strip trailing config.db filename if present
            import os
            if db_path.endswith('.db'):
                db_path = os.path.dirname(db_path)
            ksp   = ksplib.Ksp(storage_path=db_path, secrets_path=db_path)
            creds = ksp.get_ksp(alpaca_ksp)
            if isinstance(creds, dict):
                cfg['alpaca_key']    = creds.get('user', '')
                cfg['alpaca_secret'] = creds.get('password', '')
        except Exception as e:
            logger.debug('BrokerFactory._resolve_config: ksplib: %s', e)
        cfg.setdefault('alpaca_key',    '')
        cfg.setdefault('alpaca_secret', '')
        cfg['ibkr_host'] = config.get_value('ibkr_host', '127.0.0.1')
        cfg['ibkr_port'] = config.get_value('ibkr_port', '7497')
        return cfg

    @staticmethod
    def create(broker_id: str, config) -> TradingBroker:
        """Create a broker.  *config* may be a dict or a SystemConfig instance."""
        cfg = BrokerFactory._resolve_config(config)
        if broker_id == 'alpaca':
            return AlpacaBroker(
                api_key=cfg.get('alpaca_key') or None,
                secret_key=cfg.get('alpaca_secret') or None,
                paper=True,
            )
        if broker_id == 'ibkr':
            return IBKRBroker(
                host=cfg.get('ibkr_host', '127.0.0.1'),
                port=int(cfg.get('ibkr_port', 7497)),
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


# ------------------------------------------------------------------ #
#  Stop-Loss Monitor                                                   #
# ------------------------------------------------------------------ #

class StopLossMonitor(Tools):
    """Periodically checks open positions against an ATR-based stop-loss level.

    Usage
    -----
    From the scheduler or a CLI script::

        from tradinglib.premium.trading_bridge import StopLossMonitor, OrderLog, BrokerFactory
        log  = OrderLog()
        cfg  = SystemConfig(username='admin')
        broker = BrokerFactory.create('alpaca', cfg)
        mon  = StopLossMonitor(log, db_path='database')
        triggered = mon.check(broker, mode='paper', broker_id='alpaca', atr_mult=2.0)

    From the Streamlit UI: call ``check()`` on demand and display the result.

    The method returns a list of dicts for each position that breached the
    stop level::

        [{'ticker': 'MU', 'broker_symbol': 'MU',
          'entry_price': 942.8, 'current_price': 880.0,
          'atr': 34.0, 'stop_price': 874.8,
          'qty': 1.0, 'strategy': 'Value Trend Strategy',
          'action': 'close_submitted' | 'alert_only'}]
    """

    def __init__(self, order_log: 'OrderLog', db_path: str = 'database'):
        self._log     = order_log
        self._db_path = db_path

    # ── ATR lookup ──────────────────────────────────────────────────
    def _get_atr(self, ticker: str, sim_db: str | None = None) -> float:
        """Return the most recent ATR value for *ticker* from the simulation DB."""
        import os
        if sim_db is None:
            sim_db = SignalEvaluator._best_sim_db(self._db_path)
        db_file = Tools().get_path(path=self._db_path, file_name=sim_db)
        if not os.path.exists(db_file):
            return 0.0
        # The table inside every asset_simulation_*.db is always named
        # 'asset_simulation'. Deriving it from the filename (e.g. dropping '.db'
        # off 'asset_simulation_.db' → 'asset_simulation_') yielded a non-existent
        # table, so the query always raised and _get_atr silently returned 0.0 —
        # which forced every stop onto the 8%-below-HWM fallback instead of
        # ATR × atr_mult. Use the fixed table name.
        table = 'asset_simulation'
        try:
            with sqlite3.connect(db_file) as conn:
                row = conn.execute(
                    f"SELECT atr FROM [{table}] WHERE ticker=? "
                    "ORDER BY date DESC LIMIT 1",
                    (ticker,)
                ).fetchone()
            return float(row[0]) if row and row[0] else 0.0
        except Exception as e:
            logger.debug('StopLossMonitor._get_atr(%s): %s', ticker, e)
            return 0.0

    # ── Open positions from order log ────────────────────────────────
    def _open_positions(
        self, mode: str, broker_id: str, broker=None
    ) -> list[dict]:
        """FIFO-matched open positions from the local order log.

        When *broker* is provided, the FIFO result is intersected with the
        positions the broker actually holds. This guards against phantom
        positions caused by a closing sell that was logged with a wrong
        quantity (e.g. ``qty=0``) or never recorded at all — without the
        intersection, such a position keeps triggering trailing-stop alerts
        forever even though it has long been closed at the broker.

        If the broker lookup fails (transient network/API error) the FIFO
        result is returned unfiltered, so a temporary broker outage never
        silently suppresses stop-loss monitoring.
        """
        try:
            df = self._log.get_orders_df(mode=mode, broker=broker_id)
        except Exception:
            return []
        if df.empty:
            return []

        result: list[dict] = []
        for ticker in df['ticker'].dropna().unique():
            tdf   = df[df['ticker'] == ticker]
            buys  = tdf[(tdf['action'] == 'buy')  & (tdf['status'] == 'filled')].copy()
            sells = tdf[(tdf['action'] == 'sell') & (tdf['status'] == 'filled')]

            if buys.empty:
                continue

            sold_qty = float(
                pd.to_numeric(sells['qty'], errors='coerce').fillna(0).sum()
            )
            buys['_fp'] = pd.to_numeric(buys['fill_price'], errors='coerce').fillna(0)
            buys['_sp'] = pd.to_numeric(buys['signal_price'], errors='coerce').fillna(0)
            buys['_qty'] = pd.to_numeric(buys['qty'], errors='coerce').fillna(0)
            buys['_ts'] = pd.to_datetime(
                buys.get('filled_at', pd.Series(dtype=str)), errors='coerce'
            )
            buys = buys.sort_values('_ts')

            rem = sold_qty
            for _, brow in buys.iterrows():
                bq = float(brow['_qty'])
                if rem >= bq:
                    rem -= bq
                    continue
                # This buy is (partially) open
                entry_price = float(brow['_fp']) or float(brow['_sp']) or 0.0
                strategy    = str(brow.get('strategy', '') or '')
                broker_sym  = str(brow.get('broker_symbol', '') or ticker)
                result.append({
                    'ticker':        ticker,
                    'broker_symbol': broker_sym,
                    'strategy':      strategy,
                    'qty':           bq - rem,
                    'entry_price':   entry_price,
                })
                break

        # ── Cross-check against positions actually held at the broker ──
        # Drops phantom positions (e.g. a close logged with qty=0) that the
        # FIFO matcher still believes are open. On broker error, keep FIFO.
        if broker is not None and result:
            try:
                held = {p.broker_symbol for p in broker.get_positions()}
            except Exception as e:
                logger.warning(
                    '_open_positions: get_positions failed (%s); '
                    'skipping broker reconciliation', e,
                )
                held = None
            if held is not None:
                dropped = [r['ticker'] for r in result
                           if r['broker_symbol'] not in held]
                if dropped:
                    logger.info(
                        '_open_positions: dropping %d phantom position(s) '
                        'not held at broker: %s',
                        len(dropped), ', '.join(dropped),
                    )
                result = [r for r in result if r['broker_symbol'] in held]

        return result

    # ── Main check ───────────────────────────────────────────────────
    def check(
        self,
        broker,
        mode:       str,
        broker_id:  str,
        atr_mult:   float = 2.0,
        dry_run:    bool  = True,
        sim_db:     str | None = None,
    ) -> dict:
        """Check all open positions against their ATR stop-loss level.

        Parameters
        ----------
        broker      : connected BrokerBase instance
        mode        : 'paper' or 'live'
        broker_id   : 'alpaca' or 'ibkr'
        atr_mult    : multiplier for ATR (stop = entry - atr_mult × ATR)
        dry_run     : if True → log alert only; if False → submit sell order
        sim_db      : override simulation DB filename

        Returns
        -------
        dict with keys:
          triggered : list of position dicts that breached the stop level
          executed  : number of sell orders submitted (0 in dry_run mode)
          errors    : list of error strings
        """
        result = {'triggered': [], 'executed': 0, 'errors': []}

        positions = self._open_positions(mode, broker_id, broker=broker)
        if not positions:
            return result

        # Get live prices for all symbols at once
        symbols = list({p['broker_symbol'] for p in positions})
        try:
            prices = broker.get_latest_prices(symbols)
        except Exception as e:
            result['errors'].append(f'get_latest_prices failed: {e}')
            prices = {}

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

        for pos in positions:
            sym          = pos['broker_symbol']
            ticker       = pos['ticker']
            entry        = pos['entry_price']
            current      = float(prices.get(sym, 0))
            atr          = self._get_atr(ticker, sim_db)

            if entry <= 0 or current <= 0:
                continue   # insufficient data

            # Prefer the HWM-based trailing stop when one has been persisted,
            # otherwise fall back to the static entry-based stop (needs ATR).
            try:
                with sqlite3.connect(self._log._db) as _tc:
                    _tr = _tc.execute(
                        "SELECT trail_stop FROM position_trails "
                        "WHERE ticker=? AND mode=? AND broker=?",
                        (ticker, mode, broker_id),
                    ).fetchone()
                stop = float(_tr[0]) if _tr and _tr[0] else None
            except Exception:
                stop = None

            if stop is None:
                if atr <= 0:
                    continue   # no trail stop and no ATR — skip
                stop = round(entry - atr_mult * atr, 4)

            if current > stop:
                continue   # still above stop level

            # ── Stop-loss breached ────────────────────────────────────
            pos_info = {
                **pos,
                'current_price': current,
                'atr':           atr,
                'stop_price':    stop,
                'atr_mult':      atr_mult,
                'checked_at':    now_str,
                'action':        'alert_only',
            }

            logger.warning(
                'StopLossMonitor: %s breached stop %.4f (entry=%.4f, '
                'current=%.4f, atr=%.4f, mult=%.1f)',
                ticker, stop, entry, current, atr, atr_mult,
            )

            if not dry_run:
                try:
                    close_result = broker.close_position(sym)
                    if close_result.status != 'error':
                        # Log the stop-triggered sell in order log
                        self._log.save(
                            mode=mode, broker=broker_id,
                            strategy=pos['strategy'],
                            ticker=ticker,
                            broker_symbol=sym,
                            action='sell',
                            qty=pos['qty'],
                            signal_price=current,
                            order_id=close_result.order_id,
                            status=close_result.status,
                            signal_date=datetime.now().strftime('%Y-%m-%d'),
                            error_msg='stop_loss_triggered',
                        )
                        pos_info['action']   = 'close_submitted'
                        pos_info['order_id'] = close_result.order_id
                        result['executed']  += 1
                    else:
                        result['errors'].append(
                            f'close_position {sym}: {close_result.error_msg}'
                        )
                except Exception as e:
                    result['errors'].append(f'close {sym}: {e}')

            result['triggered'].append(pos_info)

        logger.info(
            'StopLossMonitor.check: %d triggered, %d executed, %d errors',
            len(result['triggered']), result['executed'], len(result['errors']),
        )
        return result

    # ── Trailing stop ────────────────────────────────────────────────

    def update_trails(
        self,
        broker,
        mode:      str,
        broker_id: str,
        atr_mult:  float = 2.5,
        execute:   bool  = False,
        sim_db:    str | None = None,
    ) -> list[dict]:
        """Update trailing stop levels for all open positions.

        - Raises ``high_water_mark`` whenever the current price sets a new high.
        - Recalculates ``trail_stop = high_water_mark - atr_mult × ATR``.
        - The stop NEVER moves down — only upward as the position gains.
        - When *execute* is True, submits a market sell order for every position
          whose current price is at or below ``trail_stop``.

        The trail state is persisted in the ``position_trails`` table so that
        the high-water-mark survives between runs.
        """
        positions = self._open_positions(mode, broker_id, broker=broker)
        if not positions:
            self._prune_trails(mode, broker_id, keep=set())
            return []

        symbols = list({p['broker_symbol'] for p in positions})
        try:
            prices = broker.get_latest_prices(symbols)
        except Exception as e:
            logger.error('update_trails: get_latest_prices failed: %s', e)
            prices = {}

        now_ts  = datetime.now().isoformat()
        results = []

        for pos in positions:
            sym     = pos['broker_symbol']
            ticker  = pos['ticker']
            entry   = pos['entry_price']
            current = float(prices.get(sym, 0))
            atr     = self._get_atr(ticker, sim_db)

            if current <= 0:
                continue

            # Load existing trail state (if any)
            try:
                with sqlite3.connect(self._log._db) as conn:
                    row = conn.execute(
                        "SELECT high_water_mark, trail_stop, atr_mult "
                        "FROM position_trails WHERE ticker=? AND mode=? AND broker=?",
                        (ticker, mode, broker_id)
                    ).fetchone()
            except Exception:
                row = None

            prev_hwm  = float(row[0]) if row and row[0] else entry
            new_hwm   = max(prev_hwm, current)     # only moves up

            if atr > 0:
                trail_stop = round(new_hwm - atr_mult * atr, 4)
            else:
                # Fallback: 8 % below HWM when no ATR available
                trail_stop = round(new_hwm * 0.92, 4)

            breached = 1 if current <= trail_stop else 0

            # ── Auto-close when execute=True and stop breached ───────
            order_id_trail: str | None = None
            if breached and execute:
                try:
                    close_result = broker.close_position(sym)
                    if close_result.status != 'error':
                        self._log.save(
                            mode=mode, broker=broker_id,
                            strategy=pos['strategy'],
                            ticker=ticker,
                            broker_symbol=sym,
                            action='sell',
                            qty=pos['qty'],
                            signal_price=current,
                            order_id=close_result.order_id,
                            status=close_result.status,
                            signal_date=datetime.now().strftime('%Y-%m-%d'),
                            error_msg='trail_stop_triggered',
                        )
                        order_id_trail = close_result.order_id
                        logger.warning(
                            'update_trails: %s trail stop breached '
                            '(current=%.4f ≤ trail=%.4f), close submitted order=%s',
                            ticker, current, trail_stop, order_id_trail,
                        )
                    else:
                        logger.error(
                            'update_trails: close_position %s failed: %s',
                            sym, close_result.error_msg,
                        )
                except Exception as e:
                    logger.error('update_trails: execute close %s: %s', sym, e)

            # Persist
            try:
                with sqlite3.connect(self._log._db) as conn:
                    conn.execute("""
                        INSERT INTO position_trails
                            (ticker, broker_symbol, strategy, mode, broker,
                             entry_price, high_water_mark, trail_stop,
                             atr, atr_mult, last_price, breached, updated_at)
                        VALUES (?, ?, ?, ?, ?,
                                ?, ?, ?,
                                ?, ?, ?, ?, ?)
                        ON CONFLICT(ticker, mode, broker) DO UPDATE SET
                            broker_symbol   = excluded.broker_symbol,
                            strategy        = excluded.strategy,
                            entry_price     = excluded.entry_price,
                            high_water_mark = excluded.high_water_mark,
                            trail_stop      = excluded.trail_stop,
                            atr             = excluded.atr,
                            atr_mult        = excluded.atr_mult,
                            last_price      = excluded.last_price,
                            breached        = excluded.breached,
                            updated_at      = excluded.updated_at
                    """, (
                        ticker, sym, pos['strategy'], mode, broker_id,
                        entry, new_hwm, trail_stop,
                        atr, atr_mult, current, breached, now_ts,
                    ))
            except Exception as e:
                logger.error('update_trails: persist %s: %s', ticker, e)

            gain_pct = round((current / entry - 1) * 100, 2) if entry > 0 else 0.0
            hwm_pct  = round((new_hwm  / entry - 1) * 100, 2) if entry > 0 else 0.0
            dist_pct = round((current / trail_stop - 1) * 100, 2) if trail_stop > 0 else 0.0

            results.append({
                'ticker':          ticker,
                'broker_symbol':   sym,
                'strategy':        pos['strategy'],
                'entry_price':     entry,
                'current_price':   current,
                'high_water_mark': new_hwm,
                'trail_stop':      trail_stop,
                'atr':             atr,
                'atr_mult':        atr_mult,
                'gain_pct':        gain_pct,
                'hwm_pct':         hwm_pct,
                'dist_to_trail':   dist_pct,   # % current is above trail stop
                'breached':        bool(breached),
                'order_id':        order_id_trail,
                'updated_at':      now_ts,
            })

        # Drop persisted trail rows for positions that are no longer open,
        # so the Trailing Stops table doesn't keep showing stale tickers.
        self._prune_trails(mode, broker_id, keep={r['ticker'] for r in results})

        logger.info(
            'update_trails: %d positions, %d breached',
            len(results), sum(1 for r in results if r['breached']),
        )
        return results

    def _prune_trails(self, mode: str, broker_id: str, keep: set[str]) -> None:
        """Delete persisted ``position_trails`` rows for tickers not in *keep*."""
        try:
            with sqlite3.connect(self._log._db) as conn:
                rows = conn.execute(
                    "SELECT ticker FROM position_trails WHERE mode=? AND broker=?",
                    (mode, broker_id),
                ).fetchall()
                stale = [t for (t,) in rows if t not in keep]
                if stale:
                    conn.executemany(
                        "DELETE FROM position_trails "
                        "WHERE ticker=? AND mode=? AND broker=?",
                        [(t, mode, broker_id) for t in stale],
                    )
                    logger.info(
                        'update_trails: pruned %d stale trail row(s): %s',
                        len(stale), ', '.join(stale),
                    )
        except Exception as e:
            logger.error('update_trails: prune trails failed: %s', e)

    def get_trails_df(self, mode: str, broker_id: str) -> 'pd.DataFrame':
        """Load persisted trail state from DB."""
        try:
            with sqlite3.connect(self._log._db) as conn:
                return pd.read_sql_query(
                    "SELECT * FROM position_trails WHERE mode=? AND broker=? "
                    "ORDER BY updated_at DESC",
                    conn, params=(mode, broker_id),
                )
        except Exception as e:
            logger.error('get_trails_df: %s', e)
            return pd.DataFrame()

    def clear_trail(self, ticker: str, mode: str, broker_id: str):
        """Remove trail state for a single ticker (e.g. after closing position)."""
        try:
            with sqlite3.connect(self._log._db) as conn:
                conn.execute(
                    "DELETE FROM position_trails WHERE ticker=? AND mode=? AND broker=?",
                    (ticker, mode, broker_id)
                )
        except Exception as e:
            logger.error('clear_trail %s: %s', ticker, e)
