from tradinglib import tools
from tradinglib.tools import open_db
from tradinglib import scheduler as sc
from tradinglib import file_explorer as fe
from tradinglib import sqlite_editor as sq
from tradinglib import excel_executor as ee
from tradinglib import ksplib
from tradinglib import system_config as sysconf
from tradinglib import market_data
from tradinglib.i18n import t
from tradinglib import ticker_tools as tt
from tradinglib.utils import DataUtils
import pandas as pd
import streamlit as st
import logging
import os
import sqlite3
import json
from pathlib import Path

logger = logging.getLogger(__name__)


def _fetch_single_ticker(ticker: str, db_path: str = 'database') -> tuple[list, list]:
    """Fetch asset info and price data (1m, 1d, 1mo) for a single ticker.
    Returns (ok_messages, error_messages)."""
    msgs_ok, msgs_err = [], []

    # 1. Asset info → asset_info.db
    try:
        info_db_path = tools.Tools().get_path(path=db_path, file_name='asset_info.db')
        conn_info = open_db(info_db_path, timeout=10)
        info = market_data.ticker_info(ticker, use_cache=False)
        try:
            info['incomeSheet'] = market_data.ticker_financials(ticker, use_cache=False).to_json(orient='split')
        except Exception:
            pass
        try:
            info['balanceSheet'] = market_data.ticker_balance_sheet(ticker, use_cache=False).to_json(orient='split')
        except Exception:
            pass
        row_map = {'ticker': ticker}
        for key in info.keys():
            col = f"_{key}" if key[0].isdigit() else key
            try:
                value = info.get(key)
                if isinstance(value, (list, dict)):
                    value = json.dumps(value)
            except Exception:
                value = None
            row_map[col] = value
        DataUtils.bulk_upsert_dicts(conn_info, 'asset_info', [row_map])
        conn_info.commit()
        conn_info.close()
        msgs_ok.append(t('admin.info_saved', ticker=ticker))
    except Exception as _e:
        msgs_err.append(t('admin.info_error', error=_e))
        logger.exception("Error fetching asset info for %s", ticker)

    # 2. Price data → yf_{ticker}.db (1m/7d, 1d/max, 1mo/max)
    try:
        saver = tt.StockDataSaver(ticker)
        failed = saver.save_all_intervals(
            intervals=['1m', '1d', '1mo'],
            periods=['7d', 'max', 'max'],
            force_remote=True
        )
        try:
            saver.close_connection()
        except Exception:
            pass
        if failed:
            msgs_err.append(t('admin.price_failed_intervals', intervals=', '.join(failed)))
        else:
            msgs_ok.append(t('admin.price_saved', ticker=ticker))
    except Exception as _e:
        msgs_err.append(t('admin.price_error', error=_e))
        logger.exception("Error fetching price data for %s", ticker)

    return msgs_ok, msgs_err


def _has_price_data(ticker: str, db_path: str = 'database') -> bool:
    """Return True if yf_<ticker>.db exists and its day_data table has at least one row."""
    try:
        path = tools.Tools().get_path(path=db_path, file_name=f'yf_{ticker}.db')
        if not os.path.exists(path):
            return False
        with open_db(path, readonly=True) as conn:
            return conn.execute("SELECT 1 FROM day_data LIMIT 1").fetchone() is not None
    except Exception:
        return False


def _tab_overlay(label: str) -> str:
    """Full-viewport loading overlay — must be injected into an st.empty() that lives
    OUTSIDE any tab/expander context so position:fixed covers the real viewport."""
    return (
        '<style>@keyframes _tt_spin{to{transform:rotate(360deg)}}</style>'
        '<div style="position:fixed;top:0;left:0;right:0;bottom:0;'
        'width:100vw;height:100vh;background:rgba(10,12,20,0.55);'
        'z-index:99999;display:flex;align-items:center;justify-content:center;">'
        '<div style="display:flex;flex-direction:column;align-items:center;'
        'gap:2rem;padding:3rem 4rem;border-radius:1.5rem;'
        'background:rgba(255,255,255,0.08);backdrop-filter:blur(12px);'
        '-webkit-backdrop-filter:blur(12px);">'
        '<div style="width:80px;height:80px;border-radius:50%;flex-shrink:0;'
        'border:6px solid rgba(255,255,255,0.15);border-top-color:#5b9cf6;'
        'animation:_tt_spin 0.85s linear infinite;"></div>'
        f'<p style="color:#dce4ef;font-size:1.4rem;font-weight:500;margin:0;'
        f'letter-spacing:0.04em;">'
        f'<span style="opacity:0.5;font-size:0.8em;font-weight:400;'
        f'letter-spacing:0.1em;text-transform:uppercase;">Loading: </span>'
        f'{label} …</p>'
        '</div></div>'
    )


class Admin():

    def __init__(self, username='', region=st, scheduler_db="scheduler.db", db_path='database', authenticator=None, section=None):
        """Initialize the admin panel and immediately render it into region."""
        self.username = username
        self.region = region
        self.authenticator = authenticator
        self.db_path = db_path
        self.scheduler_db = scheduler_db
        self.section = section
        self.render()

    def render(self):
        """Route to the active admin section — section mode (no tabs) or native tab mode."""
        logger = logging.getLogger(__name__)
        logger.debug('Admin.render called by user=%s', self.username)

        _section_keys   = ['ticker', 'database', 'credentials', 'system', 'scheduler', 'pine']
        _section_labels = ["Ticker", "Database", "API Credentials", "System", "Scheduler", "Pine Script"]
        _spin = st.empty()

        if self.section and self.section in _section_keys:
            # Section mode: no tabs, render only the requested section directly
            _spin.markdown(_tab_overlay(self.section.title()), unsafe_allow_html=True)
            _dispatch = {
                'ticker':      self._render_ticker,
                'database':    self._render_database,
                'credentials': self._render_credentials,
                'system':      self._render_system,
                'scheduler':   self._render_scheduler,
                'pine':        self._render_pine,
            }
            _dispatch[self.section](_spin)
        else:
            # Tab mode: native Streamlit tabs (direct /admin access without section param)
            tab_ticker, tab_db, tab_creds, tab_system, tab_scheduler, tab_pine = self.region.tabs(_section_labels)
            with tab_ticker:
                _spin.markdown(_tab_overlay("Ticker"), unsafe_allow_html=True)
                self._render_ticker(_spin)
            with tab_db:
                _spin.markdown(_tab_overlay("Database"), unsafe_allow_html=True)
                self._render_database(_spin)
            with tab_creds:
                _spin.markdown(_tab_overlay("API Credentials"), unsafe_allow_html=True)
                self._render_credentials(_spin)
            with tab_system:
                _spin.markdown(_tab_overlay("System"), unsafe_allow_html=True)
                self._render_system(_spin)
            with tab_scheduler:
                _spin.markdown(_tab_overlay("Scheduler"), unsafe_allow_html=True)
                self._render_scheduler(_spin)
            with tab_pine:
                _spin.markdown(_tab_overlay("Pine Script"), unsafe_allow_html=True)
                self._render_pine(_spin)

    # ── Section methods ────────────────────────────────────────────────────────

    def _render_ticker(self, _spin):
        add_ticker_expander = st.expander(t('admin.ticker_expander'), expanded=False)
        with add_ticker_expander:
            _spin.markdown(_tab_overlay(t('admin.ticker_expander')), unsafe_allow_html=True)
            db_name_add = tools.Tools().get_path(path=self.db_path, file_name='yf_tickers.db')
            if not os.path.exists(db_name_add):
                st.info(t('admin.db_not_found', path=db_name_add))
            else:
                col_l, col_r = st.columns(2)
                with col_l:
                    new_ticker = st.text_input(
                        t('admin.ticker_symbol'), placeholder=t('admin.ticker_symbol_ph'),
                        key="add_ticker_symbol"
                    ).strip().upper()
                    new_isin = st.text_input(
                        t('admin.ticker_isin'), placeholder=t('admin.ticker_isin_ph'),
                        key="add_ticker_isin"
                    ).strip()
                with col_r:
                    new_invested = st.number_input(
                        t('admin.ticker_invested'), min_value=0.0,
                        value=0.0, step=100.0, key="add_ticker_invested"
                    )

                _all_indices: list[str] = []
                try:
                    _c_idx = open_db(db_name_add, readonly=True)
                    _cur_idx = _c_idx.cursor()
                    _cur_idx.execute("SELECT name FROM indices ORDER BY name")
                    _all_indices = [r[0] for r in _cur_idx.fetchall()]
                    _c_idx.close()
                except Exception:
                    pass

                st.markdown(t('admin.index_section'))
                col_idx, col_newidx = st.columns(2)
                with col_idx:
                    sel_indices = st.multiselect(
                        t('admin.select_indices'),
                        options=_all_indices,
                        key="add_ticker_indices"
                    )
                with col_newidx:
                    new_index_name = st.text_input(
                        t('admin.new_index'),
                        placeholder=t('admin.new_index_ph'),
                        key="add_ticker_new_index"
                    ).strip()

                if new_ticker:
                    try:
                        _c_prev = open_db(db_name_add, readonly=True)
                        _cur_prev = _c_prev.cursor()
                        _cur_prev.execute(
                            "SELECT id, Ticker, Date, INVESTED, ISIN FROM stocks WHERE Ticker = ?",
                            (new_ticker,)
                        )
                        _existing_row = _cur_prev.fetchone()
                        if _existing_row:
                            _cur_prev.execute("""
                                SELECT i.name FROM indices i
                                JOIN stock_indices si ON i.id = si.index_id
                                WHERE si.stock_id = ?
                            """, (_existing_row[0],))
                            _ex_indices = [r[0] for r in _cur_prev.fetchall()]
                            st.info(t(
                                'admin.ticker_exists',
                                ticker=new_ticker,
                                isin=_existing_row[4] or '–',
                                invested=f"{_existing_row[3] or 0:.2f} €",
                                indices=', '.join(_ex_indices) or '–',
                            ))
                        _c_prev.close()
                    except Exception:
                        pass

                st.markdown("---")
                fetch_after_save = st.checkbox(
                    t('admin.fetch_after_save'),
                    key="add_ticker_fetch",
                    help=t('admin.fetch_after_save_help'),
                )
                if st.button(t('admin.save_ticker'), key="add_ticker_save", type="primary"):
                    if not new_ticker:
                        st.error(t('admin.ticker_required'))
                    else:
                        _msgs_ok, _msgs_err = [], []
                        try:
                            from datetime import datetime as _dt
                            _conn = open_db(db_name_add)
                            _cur  = _conn.cursor()

                            _cur.executescript("""
                                CREATE TABLE IF NOT EXISTS stocks (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    Ticker TEXT, Date TEXT, INVESTED REAL, ISIN TEXT
                                );
                                CREATE TABLE IF NOT EXISTS indices (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    name TEXT UNIQUE
                                );
                                CREATE TABLE IF NOT EXISTS stock_indices (
                                    stock_id INTEGER,
                                    index_id INTEGER,
                                    FOREIGN KEY(stock_id) REFERENCES stocks(id),
                                    FOREIGN KEY(index_id) REFERENCES indices(id)
                                );
                            """)

                            _cur.execute(
                                "SELECT id FROM stocks WHERE Ticker = ?", (new_ticker,))
                            _ex = _cur.fetchone()
                            if _ex:
                                _stock_id = _ex[0]
                                _cur.execute(
                                    "UPDATE stocks SET INVESTED = ?, ISIN = ? WHERE id = ?",
                                    (new_invested or None, new_isin or None, _stock_id)
                                )
                                _msgs_ok.append(t('admin.ticker_updated', ticker=new_ticker))
                            else:
                                _date = _dt.now().strftime("%Y-%m-%d")
                                _cur.execute(
                                    "INSERT INTO stocks (Ticker, Date, INVESTED, ISIN) "
                                    "VALUES (?, ?, ?, ?)",
                                    (new_ticker, _date, new_invested or None, new_isin or None)
                                )
                                _stock_id = _cur.lastrowid
                                _msgs_ok.append(t('admin.ticker_created', ticker=new_ticker))

                            _indices_to_link = list(sel_indices)
                            if new_index_name:
                                _indices_to_link.append(new_index_name)

                            for _idx_name in _indices_to_link:
                                _cur.execute(
                                    "INSERT OR IGNORE INTO indices (name) VALUES (?)",
                                    (_idx_name,)
                                )
                                _cur.execute(
                                    "SELECT id FROM indices WHERE name = ?", (_idx_name,))
                                _idx_id = _cur.fetchone()[0]
                                _cur.execute(
                                    "SELECT 1 FROM stock_indices "
                                    "WHERE stock_id = ? AND index_id = ?",
                                    (_stock_id, _idx_id)
                                )
                                if not _cur.fetchone():
                                    _cur.execute(
                                        "INSERT INTO stock_indices (stock_id, index_id) "
                                        "VALUES (?, ?)",
                                        (_stock_id, _idx_id)
                                    )
                                    _msgs_ok.append(t('admin.index_linked', index=_idx_name))

                            _conn.commit()
                            _conn.close()

                        except Exception as _e:
                            _msgs_err.append(f"Error saving: {_e}")
                            logger.exception("Error creating ticker %s", new_ticker)

                        for _m in _msgs_ok:
                            st.success(_m)
                        for _m in _msgs_err:
                            st.error(_m)
                        if _msgs_ok and not _msgs_err:
                            if not fetch_after_save:
                                st.info(t('admin.ticker_saved_info', ticker=new_ticker))
                        if _msgs_ok and fetch_after_save:
                            with st.spinner(t('admin.fetching_data', ticker=new_ticker)):
                                _fetch_ok, _fetch_err = _fetch_single_ticker(
                                    new_ticker, self.db_path)
                            for _m in _fetch_ok:
                                st.success(_m)
                            for _m in _fetch_err:
                                st.error(_m)
            _spin.empty()

        bulk_expander = st.expander(t('admin.bulk_expander'), expanded=False)
        with bulk_expander:
            st.markdown(t('admin.bulk_hint'))
            bulk_tickers_raw = st.text_area(
                t('admin.bulk_symbols'),
                placeholder="NG=F, RB=F, HO=F\nZW=F, ZC=F, ZS=F",
                height=120,
                key="bulk_ticker_input"
            )
            _bulk_all_indices: list[str] = []
            try:
                _db_bulk = tools.Tools().get_path(path=self.db_path, file_name='yf_tickers.db')
                _c_bulk = open_db(_db_bulk, readonly=True)
                _bulk_all_indices = [r[0] for r in _c_bulk.cursor().execute(
                    "SELECT name FROM indices ORDER BY name").fetchall()]
                _c_bulk.close()
            except Exception:
                pass
            col_bidx, col_bnewidx, col_bfetch = st.columns([2, 2, 1])
            with col_bidx:
                bulk_index_sel = st.selectbox(
                    t('admin.bulk_select_index'),
                    options=[""] + _bulk_all_indices,
                    key="bulk_ticker_index_sel"
                )
            with col_bnewidx:
                bulk_index_new = st.text_input(
                    t('admin.bulk_new_index'),
                    placeholder=t('admin.bulk_new_index_ph'),
                    key="bulk_ticker_index_new"
                ).strip()
            with col_bfetch:
                bulk_fetch = st.checkbox(
                    t('admin.bulk_fetch'),
                    key="bulk_ticker_fetch",
                    help=t('admin.bulk_fetch_help'),
                )
            bulk_index = bulk_index_new or bulk_index_sel or ""
            if st.button(t('admin.bulk_save'), key="bulk_ticker_save", type="primary"):
                raw_symbols = [
                    s.strip().upper()
                    for s in bulk_tickers_raw.replace('\n', ',').split(',')
                    if s.strip()
                ]
                if not raw_symbols:
                    st.error(t('admin.bulk_required'))
                else:
                    db_name_bulk = tools.Tools().get_path(path=self.db_path, file_name='yf_tickers.db')
                    _b_ok, _b_err = [], []
                    try:
                        from datetime import datetime as _dt
                        _bc = open_db(db_name_bulk)
                        _bcu = _bc.cursor()
                        _bcu.executescript("""
                            CREATE TABLE IF NOT EXISTS stocks (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                Ticker TEXT, Date TEXT, INVESTED REAL, ISIN TEXT
                            );
                            CREATE TABLE IF NOT EXISTS indices (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                name TEXT UNIQUE
                            );
                            CREATE TABLE IF NOT EXISTS stock_indices (
                                stock_id INTEGER, index_id INTEGER,
                                FOREIGN KEY(stock_id) REFERENCES stocks(id),
                                FOREIGN KEY(index_id) REFERENCES indices(id)
                            );
                        """)
                        _idx_id = None
                        if bulk_index:
                            _bcu.execute("INSERT OR IGNORE INTO indices (name) VALUES (?)", (bulk_index,))
                            _bcu.execute("SELECT id FROM indices WHERE name = ?", (bulk_index,))
                            _idx_id = _bcu.fetchone()[0]
                        for _sym in raw_symbols:
                            try:
                                _bcu.execute("SELECT id FROM stocks WHERE Ticker = ?", (_sym,))
                                _ex = _bcu.fetchone()
                                if _ex:
                                    _sid = _ex[0]
                                    _b_ok.append(t('admin.bulk_ticker_exists', ticker=_sym))
                                else:
                                    _bcu.execute(
                                        "INSERT INTO stocks (Ticker, Date) VALUES (?, ?)",
                                        (_sym, _dt.now().strftime("%Y-%m-%d"))
                                    )
                                    _sid = _bcu.lastrowid
                                    _b_ok.append(t('admin.bulk_ticker_created', ticker=_sym))
                                if _idx_id:
                                    _bcu.execute(
                                        "SELECT 1 FROM stock_indices WHERE stock_id=? AND index_id=?",
                                        (_sid, _idx_id)
                                    )
                                    if not _bcu.fetchone():
                                        _bcu.execute(
                                            "INSERT INTO stock_indices (stock_id, index_id) VALUES (?,?)",
                                            (_sid, _idx_id)
                                        )
                            except Exception as _se:
                                _b_err.append(f"**{_sym}**: {_se}")
                        _bc.commit()
                        _bc.close()
                    except Exception as _be:
                        _b_err.append(t('admin.bulk_db_error', error=_be))
                        logger.exception("Bulk-ticker save error")
                    for _m in _b_ok:
                        st.success(_m)
                    for _m in _b_err:
                        st.error(_m)
                    if bulk_fetch and raw_symbols and not _b_err:
                        for _sym in raw_symbols:
                            with st.spinner(t('admin.bulk_fetching', ticker=_sym)):
                                _fok, _ferr = _fetch_single_ticker(_sym, self.db_path)
                            for _m in _fok:
                                st.success(_m)
                            for _m in _ferr:
                                st.error(_m)

        index_mgmt_expander = st.expander(t('admin.index_mgmt_expander'), expanded=False)
        with index_mgmt_expander:
            _spin.markdown(_tab_overlay(t('admin.index_mgmt_expander')), unsafe_allow_html=True)
            _im_db = tools.Tools().get_path(path=self.db_path, file_name='yf_tickers.db')
            st.caption(t('admin.index_mgmt_hint'))

            _im_indices: list[tuple[int, str, int]] = []
            try:
                with open_db(_im_db, readonly=True) as _imc:
                    _im_indices = _imc.execute("""
                        SELECT i.id, i.name, COUNT(si.stock_id) AS cnt
                        FROM indices i
                        LEFT JOIN stock_indices si ON i.id = si.index_id
                        GROUP BY i.id, i.name
                        ORDER BY i.name
                    """).fetchall()
            except Exception as _ie:
                st.error(f"Error loading indices: {_ie}")

            if _im_indices:
                _im_options = [""] + [f"{name}  ({cnt})" for _, name, cnt in _im_indices]
                _im_sel_label = st.selectbox(
                    t('admin.index_mgmt_select'),
                    options=_im_options,
                    key="im_sel_index",
                )
                _im_sel_name = _im_sel_label.rsplit("  (", 1)[0] if _im_sel_label else ""
                _im_sel_id   = next((iid for iid, n, _ in _im_indices if n == _im_sel_name), None)

                if _im_sel_id is not None:
                    _im_members: list[tuple[str, int]] = []
                    try:
                        with open_db(_im_db, readonly=True) as _imc:
                            _im_members = _imc.execute("""
                                SELECT s.Ticker,
                                       (SELECT COUNT(*) FROM stock_indices si2
                                        WHERE si2.stock_id = s.id) AS idx_count
                                FROM stocks s
                                JOIN stock_indices si ON s.id = si.stock_id
                                WHERE si.index_id = ?
                                ORDER BY s.Ticker
                            """, (_im_sel_id,)).fetchall()
                    except Exception:
                        pass

                    if _im_members:
                        st.markdown(t('admin.index_mgmt_members',
                                      count=len(_im_members), name=_im_sel_name))
                        _im_df = pd.DataFrame(_im_members, columns=["Ticker", "# Indices"])
                        _im_df["Note"] = _im_df["# Indices"].apply(
                            lambda n: t('admin.index_mgmt_only_here') if n == 1 else "")
                        st.dataframe(_im_df, use_container_width=True, hide_index=True)
                    else:
                        st.info(t('admin.index_mgmt_no_members'))

                    st.markdown("---")
                    _only_here = [tk for tk, cnt in _im_members if cnt == 1]
                    _im_also_remove = False
                    if _only_here:
                        _im_also_remove = st.checkbox(
                            t('admin.index_mgmt_also_remove'),
                            key="im_also_remove",
                            help=f"{len(_only_here)} ticker(s): {', '.join(_only_here[:10])}"
                                 + (" …" if len(_only_here) > 10 else ""),
                        )

                    _im_confirm = st.checkbox(
                        t('admin.index_mgmt_confirm', name=_im_sel_name),
                        key=f"im_confirm_{_im_sel_id}",
                    )
                    if st.button(t('admin.index_mgmt_btn'),
                                 key="im_delete_btn", type="primary") and _im_confirm:
                        try:
                            with open_db(_im_db) as _imc:
                                _excl_ids = [
                                    _imc.execute(
                                        "SELECT id FROM stocks WHERE Ticker = ?", (tk,)
                                    ).fetchone()[0]
                                    for tk in _only_here
                                ] if _im_also_remove else []
                                _imc.execute(
                                    "DELETE FROM stock_indices WHERE index_id = ?",
                                    (_im_sel_id,)
                                )
                                link_count = _imc.total_changes
                                _imc.execute(
                                    "DELETE FROM indices WHERE id = ?", (_im_sel_id,))
                                removed_tickers = 0
                                for _sid in _excl_ids:
                                    _imc.execute(
                                        "DELETE FROM stock_indices WHERE stock_id = ?",
                                        (_sid,))
                                    _imc.execute(
                                        "DELETE FROM stocks WHERE id = ?", (_sid,))
                                    removed_tickers += 1

                            st.success(t('admin.index_mgmt_ok',
                                         name=_im_sel_name, count=link_count))
                            if removed_tickers:
                                st.info(t('admin.index_mgmt_tickers_removed',
                                          count=removed_tickers))
                            st.info(t('admin.done_reload'))
                        except Exception as _ime:
                            st.error(t('admin.index_mgmt_error', error=_ime))
                            logger.exception("Error deleting index %s", _im_sel_name)
            _spin.empty()

        del_ticker_expander = st.expander(t('admin.del_ticker_expander'), expanded=False)
        with del_ticker_expander:
            _spin.markdown(_tab_overlay(t('admin.del_ticker_expander')), unsafe_allow_html=True)
            _dt_db = tools.Tools().get_path(path=self.db_path, file_name='yf_tickers.db')

            _dt_tickers: list[str] = []
            try:
                with open_db(_dt_db, readonly=True) as _dtc:
                    _dt_tickers = [r[0] for r in _dtc.execute(
                        "SELECT Ticker FROM stocks ORDER BY Ticker"
                    ).fetchall()]
            except Exception as _dte:
                st.error(f"Error loading tickers: {_dte}")

            if _dt_tickers:
                _dt_sel = st.selectbox(
                    t('admin.del_ticker_select'),
                    options=[""] + _dt_tickers,
                    key="dt_sel_ticker",
                )

                if _dt_sel:
                    try:
                        with open_db(_dt_db, readonly=True) as _dtc:
                            _dt_indices = [r[0] for r in _dtc.execute("""
                                SELECT i.name FROM indices i
                                JOIN stock_indices si ON i.id = si.index_id
                                JOIN stocks s ON s.id = si.stock_id
                                WHERE s.Ticker = ?
                                ORDER BY i.name
                            """, (_dt_sel,)).fetchall()]
                    except Exception:
                        _dt_indices = []

                    if _dt_indices:
                        st.info(t('admin.del_ticker_info', indices=', '.join(_dt_indices)))
                    else:
                        st.info(t('admin.del_ticker_no_indices'))

                    st.markdown("---")
                    _dt_col1, _dt_col2 = st.columns(2)
                    with _dt_col1:
                        _dt_del_yf   = st.checkbox(t('admin.del_ticker_yf_db'),
                                                    value=True,  key="dt_del_yf")
                        _dt_del_info = st.checkbox(t('admin.del_ticker_info_db'),
                                                    value=True,  key="dt_del_info")
                    with _dt_col2:
                        _dt_del_price = st.checkbox(
                            t('admin.del_ticker_price_db', ticker=_dt_sel),
                            value=False, key="dt_del_price")

                    _dt_confirm = st.checkbox(
                        t('admin.del_ticker_confirm', ticker=_dt_sel),
                        key=f"dt_confirm_{_dt_sel}",
                    )

                    if st.button(t('admin.del_ticker_btn'),
                                 key="dt_delete_btn", type="primary") and _dt_confirm:
                        _dt_ok, _dt_err = [], []

                        if _dt_del_yf:
                            try:
                                with open_db(_dt_db) as _dtc:
                                    _row = _dtc.execute(
                                        "SELECT id FROM stocks WHERE Ticker = ?",
                                        (_dt_sel,)).fetchone()
                                    if _row:
                                        _dtc.execute(
                                            "DELETE FROM stock_indices WHERE stock_id = ?",
                                            (_row[0],))
                                        _dtc.execute(
                                            "DELETE FROM stocks WHERE id = ?", (_row[0],))
                                        _dtc.execute(
                                            "DELETE FROM indices WHERE id NOT IN "
                                            "(SELECT DISTINCT index_id FROM stock_indices)")
                                        _dt_ok.append(t('admin.del_ticker_yf_ok',
                                                        ticker=_dt_sel))
                                    else:
                                        _dt_ok.append(t('admin.del_ticker_yf_not_found',
                                                        ticker=_dt_sel))
                            except Exception as _e:
                                _dt_err.append(f"yf_tickers.db: {_e}")

                        if _dt_del_info:
                            try:
                                _info_db = tools.Tools().get_path(
                                    path=self.db_path, file_name='asset_info.db')
                                if os.path.exists(_info_db):
                                    with open_db(_info_db) as _dtc:
                                        _tables = [r[0] for r in _dtc.execute(
                                            "SELECT name FROM sqlite_master "
                                            "WHERE type='table'").fetchall()]
                                        _deleted_from = []
                                        for _tbl in _tables:
                                            try:
                                                _dtc.execute(
                                                    f"DELETE FROM {_tbl} WHERE ticker = ?",
                                                    (_dt_sel,))
                                                if _dtc.total_changes:
                                                    _deleted_from.append(_tbl)
                                            except Exception:
                                                pass
                                    if _deleted_from:
                                        _dt_ok.append(t('admin.del_ticker_info_ok',
                                                        tables=', '.join(_deleted_from)))
                                    else:
                                        _dt_ok.append(t('admin.del_ticker_info_not_found',
                                                        ticker=_dt_sel))
                                else:
                                    _dt_ok.append(t('admin.asset_info_not_found'))
                            except Exception as _e:
                                _dt_err.append(f"asset_info.db: {_e}")

                        if _dt_del_price:
                            try:
                                _price_path = tools.Tools().get_path(
                                    path=self.db_path,
                                    file_name=f'yf_{_dt_sel}.db')
                                if os.path.exists(_price_path):
                                    os.remove(_price_path)
                                    _dt_ok.append(t('admin.del_ticker_price_ok',
                                                    ticker=_dt_sel))
                                else:
                                    _dt_ok.append(t('admin.del_ticker_price_not_found',
                                                    ticker=_dt_sel))
                            except Exception as _e:
                                _dt_err.append(f"yf_{_dt_sel}.db: {_e}")

                        for _m in _dt_ok:
                            st.success(_m)
                        for _m in _dt_err:
                            st.error(_m)
                        if _dt_ok and not _dt_err:
                            st.info(t('admin.done_reload'))
            _spin.empty()

        missing_info_expander = st.expander(t('admin.missing_info_expander'), expanded=False)
        with missing_info_expander:
            _spin.markdown(_tab_overlay(t('admin.missing_info_expander')), unsafe_allow_html=True)
            st.caption(t('admin.missing_info_hint'))
            _mi_yf_db = tools.Tools().get_path(path=self.db_path, file_name='yf_tickers.db')
            _mi_info_db = tools.Tools().get_path(path=self.db_path, file_name='asset_info.db')

            _mi_stocks: list[tuple[str, str]] = []
            _mi_info_map: dict[str, tuple[str, str]] = {}
            try:
                with open_db(_mi_yf_db, readonly=True) as _mic:
                    _mi_stocks = _mic.execute("""
                        SELECT s.Ticker,
                               COALESCE(GROUP_CONCAT(i.name, ', '), '')
                        FROM stocks s
                        LEFT JOIN stock_indices si ON s.id = si.stock_id
                        LEFT JOIN indices i ON i.id = si.index_id
                        GROUP BY s.id, s.Ticker
                        ORDER BY s.Ticker
                    """).fetchall()

                if os.path.exists(_mi_info_db):
                    with open_db(_mi_info_db, readonly=True) as _mic2:
                        try:
                            for _r in _mic2.execute(
                                "SELECT ticker, shortName, longName FROM asset_info"
                            ).fetchall():
                                _mi_info_map[_r[0]] = (_r[1] or '', _r[2] or '')
                        except Exception:
                            for _r in _mic2.execute(
                                "SELECT ticker, longName FROM asset_info"
                            ).fetchall():
                                _mi_info_map[_r[0]] = ('', _r[1] or '')
            except Exception as _mie:
                st.error(f"Error reading databases: {_mie}")

            if st.button(t('admin.missing_info_check_price_btn'), key='mi_check_price_btn'):
                _mi_total = len(_mi_stocks)
                _mi_price_missing: set[str] = set()
                if _mi_total:
                    _mi_progress = st.progress(
                        0.0, text=t('admin.missing_info_progress', current=0, total=_mi_total))
                    _mi_step = max(1, _mi_total // 100)
                    for _i, (_tk, _) in enumerate(_mi_stocks):
                        if not _has_price_data(_tk, self.db_path):
                            _mi_price_missing.add(_tk)
                        if _i % _mi_step == 0 or _i == _mi_total - 1:
                            _mi_progress.progress(
                                (_i + 1) / _mi_total,
                                text=t('admin.missing_info_progress',
                                       current=_i + 1, total=_mi_total))
                    _mi_progress.empty()
                st.session_state['mi_price_missing'] = _mi_price_missing
                st.session_state['mi_price_checked'] = True

            _mi_price_checked = st.session_state.get('mi_price_checked', False)
            _mi_price_missing = st.session_state.get('mi_price_missing', set())
            if _mi_price_checked:
                st.caption(t('admin.missing_info_price_checked', count=len(_mi_price_missing)))

            _mi_combined: list[tuple[str, str, str, str, str, str]] = []
            for _tk, _idx in _mi_stocks:
                _short, _long = _mi_info_map.get(_tk, ('', ''))
                _info_ok = bool(_long.strip()) and bool(_short.strip())
                _price_ok = (_tk not in _mi_price_missing) if _mi_price_checked else None
                if not _info_ok or _price_ok is False:
                    _mi_combined.append((
                        _tk, _idx, _short, _long,
                        '✅' if _info_ok else '❌',
                        ('✅' if _price_ok else '❌') if _mi_price_checked else '–',
                    ))

            if not _mi_combined:
                st.success(t('admin.missing_info_none'))
            else:
                st.warning(t('admin.missing_info_count', count=len(_mi_combined)))

                _mi_df = pd.DataFrame(
                    _mi_combined,
                    columns=['Ticker', 'Indices',
                             t('admin.missing_info_col_short_name'),
                             t('admin.missing_info_col_long_name'),
                             t('admin.missing_info_col_info'),
                             t('admin.missing_info_col_price')]
                )
                _mi_df.insert(0, 'Select', False)

                _mi_sel_col1, _mi_sel_col2, _mi_sel_col3 = st.columns(3)
                with _mi_sel_col1:
                    _mi_sel_all = st.checkbox(t('admin.missing_info_select_all'), key='mi_select_all')
                with _mi_sel_col2:
                    _mi_sel_no_info = st.checkbox(
                        t('admin.missing_info_select_no_info'), key='mi_select_no_info')
                with _mi_sel_col3:
                    _mi_sel_no_price = st.checkbox(
                        t('admin.missing_info_select_no_price'), key='mi_select_no_price',
                        disabled=not _mi_price_checked)

                if _mi_sel_all:
                    _mi_df['Select'] = True
                if _mi_sel_no_info:
                    _mi_df.loc[
                        (_mi_df[t('admin.missing_info_col_short_name')].str.strip() == '') &
                        (_mi_df[t('admin.missing_info_col_long_name')].str.strip() == ''),
                        'Select'
                    ] = True
                if _mi_sel_no_price:
                    _mi_df.loc[_mi_df[t('admin.missing_info_col_price')] == '❌', 'Select'] = True

                _mi_edited = st.data_editor(
                    _mi_df,
                    hide_index=True,
                    use_container_width=True,
                    column_config={"Select": st.column_config.CheckboxColumn(required=False)},
                    key="mi_data_editor",
                )

                _mi_selected = _mi_edited[_mi_edited['Select']]['Ticker'].tolist()

                if _mi_selected:
                    st.markdown("---")
                    _mi_col1, _mi_col2 = st.columns(2)
                    with _mi_col1:
                        _mi_del_info = st.checkbox(
                            t('admin.missing_info_del_info'),
                            value=False, key="mi_del_info"
                        )
                    with _mi_col2:
                        _mi_del_price = st.checkbox(
                            t('admin.missing_info_del_price'),
                            value=False, key="mi_del_price"
                        )
                    _mi_confirm = st.checkbox(
                        t('admin.missing_info_confirm', count=len(_mi_selected)),
                        key="mi_confirm"
                    )
                    if st.button(t('admin.missing_info_btn'),
                                  key="mi_delete_btn", type="primary") and _mi_confirm:
                        _mi_ok, _mi_err = [], []

                        try:
                            with open_db(_mi_yf_db) as _mic:
                                for _tk in _mi_selected:
                                    _row = _mic.execute(
                                        "SELECT id FROM stocks WHERE Ticker = ?", (_tk,)).fetchone()
                                    if _row:
                                        _mic.execute(
                                            "DELETE FROM stock_indices WHERE stock_id = ?", (_row[0],))
                                        _mic.execute(
                                            "DELETE FROM stocks WHERE id = ?", (_row[0],))
                                _mic.execute(
                                    "DELETE FROM indices WHERE id NOT IN "
                                    "(SELECT DISTINCT index_id FROM stock_indices)")
                            _mi_ok.append(t('admin.missing_info_yf_ok', count=len(_mi_selected)))
                        except Exception as _e:
                            _mi_err.append(f"yf_tickers.db: {_e}")

                        if _mi_del_info and os.path.exists(_mi_info_db):
                            try:
                                with open_db(_mi_info_db) as _mic:
                                    _tables = [r[0] for r in _mic.execute(
                                        "SELECT name FROM sqlite_master "
                                        "WHERE type='table'").fetchall()]
                                    for _tk in _mi_selected:
                                        for _tbl in _tables:
                                            try:
                                                _mic.execute(
                                                    f"DELETE FROM {_tbl} WHERE ticker = ?", (_tk,))
                                            except Exception:
                                                pass
                                _mi_ok.append(t('admin.missing_info_info_ok',
                                                 tickers=', '.join(_mi_selected)))
                            except Exception as _e:
                                _mi_err.append(f"asset_info.db: {_e}")

                        if _mi_del_price:
                            _deleted_price = []
                            for _tk in _mi_selected:
                                try:
                                    _price_path = tools.Tools().get_path(
                                        path=self.db_path, file_name=f'yf_{_tk}.db')
                                    if os.path.exists(_price_path):
                                        os.remove(_price_path)
                                        _deleted_price.append(_tk)
                                except Exception as _e:
                                    _mi_err.append(f"yf_{_tk}.db: {_e}")
                            if _deleted_price:
                                _mi_ok.append(t('admin.missing_info_price_ok',
                                                 tickers=', '.join(_deleted_price)))

                        for _m in _mi_ok:
                            st.success(_m)
                        for _m in _mi_err:
                            st.error(_m)
                        if _mi_ok and not _mi_err:
                            st.session_state.pop('mi_price_checked', None)
                            st.session_state.pop('mi_price_missing', None)
                            st.info(t('admin.done_reload'))
            _spin.empty()

        delisted_expander = st.expander(t('admin.delisted_expander'), expanded=False)
        with delisted_expander:
            _spin.markdown(_tab_overlay(t('admin.delisted_expander')), unsafe_allow_html=True)
            failed_file = tools.Tools().get_path(path=self.db_path, file_name='failed_tickers.json')

            if not os.path.exists(failed_file):
                st.info(t('admin.no_failed_tickers'))
            else:
                failed_data = []
                try:
                    with open(failed_file, 'r', encoding='utf-8') as _f:
                        failed_data = json.load(_f)
                except Exception as _e:
                    st.error(t('admin.failed_read_error', error=_e))

                if not failed_data:
                    st.success(t('admin.no_failed_ok'))
                else:
                    display_data = [
                        d for d in failed_data
                        if d.get('first_seen') != d.get('last_seen')
                    ]
                    hidden = len(failed_data) - len(display_data)
                    hidden_note = t('admin.failed_hidden', count=hidden) if hidden else ""
                    st.warning(t('admin.failed_warning', count=len(display_data), note=hidden_note))

                    if display_data:
                        df_failed = pd.DataFrame(display_data)[
                            ['ticker', 'failed_intervals', 'first_seen', 'last_seen']
                        ]
                        df_failed['failed_intervals'] = df_failed['failed_intervals'].apply(
                            lambda x: ', '.join(x) if isinstance(x, list) else str(x)
                        )
                        df_failed.insert(0, 'details', df_failed['ticker'].apply(lambda t: f'/?symbol={t}&details=True'))
                        st.dataframe(df_failed, use_container_width=True,
                                     column_config={'details': st.column_config.LinkColumn('Details', display_text='View')})
                    else:
                        st.success(t('admin.no_repeated_failed'))

                    st.markdown("---")
                    sel_failed = st.selectbox(
                        t('admin.select_ticker'),
                        [""] + [d['ticker'] for d in display_data],
                        key="sel_failed_ticker"
                    )

                    if sel_failed:
                        st.markdown(f"#### Ticker: `{sel_failed}`")

                        if st.button(t('admin.check_yf'), key=f"check_yf_{sel_failed}"):
                            with st.spinner(t('admin.fetching')):
                                try:
                                    from tradinglib import market_data as _md
                                    test = _md.download(
                                        tickers=sel_failed,
                                        period='5d',
                                        interval='1d',
                                        progress=False,
                                        auto_adjust=False,
                                        actions=False,
                                    )
                                    if test is not None and not test.empty:
                                        st.success(t('admin.yf_ok', ticker=sel_failed))
                                    else:
                                        st.error(t('admin.yf_delisted', ticker=sel_failed))
                                except Exception as _e:
                                    st.error(t('admin.yf_error', error=_e))

                        st.markdown("---")
                        st.markdown(t('admin.rename_title'))

                        ren_col1, ren_col2 = st.columns([2, 1])
                        with ren_col1:
                            new_ticker_name = st.text_input(
                                t('admin.rename_new_name'),
                                placeholder=t('admin.rename_ph'),
                                key=f"ren_new_{sel_failed}"
                            ).strip().upper()
                        with ren_col2:
                            st.markdown("<br>", unsafe_allow_html=True)

                        if new_ticker_name:
                            ren_col_a, ren_col_b = st.columns(2)
                            with ren_col_a:
                                ren_yf_db    = st.checkbox(t('admin.rename_yf_db'),    value=True, key=f"ren_yf_{sel_failed}")
                                ren_info_db  = st.checkbox(t('admin.rename_info_db'),  value=True, key=f"ren_info_{sel_failed}")
                            with ren_col_b:
                                ren_price_db = st.checkbox(t('admin.rename_price_db', old=sel_failed, new=new_ticker_name), value=True, key=f"ren_price_{sel_failed}")
                                ren_failed   = st.checkbox(t('admin.rename_failed_list'), value=True, key=f"ren_list_{sel_failed}")

                            confirm_ren = st.checkbox(
                                t('admin.rename_confirm', old=sel_failed, new=new_ticker_name),
                                key=f"confirm_ren_{sel_failed}"
                            )

                            if st.button(t('admin.rename_btn'), key=f"rename_{sel_failed}") and confirm_ren:
                                ren_msgs_ok, ren_msgs_err = [], []

                                if ren_yf_db:
                                    try:
                                        _db = tools.Tools().get_path(path=self.db_path, file_name='yf_tickers.db')
                                        _c = open_db(_db)
                                        _cur = _c.cursor()
                                        _cur.execute("UPDATE stocks SET Ticker = ? WHERE Ticker = ?", (new_ticker_name, sel_failed))
                                        if _cur.rowcount > 0:
                                            _c.commit()
                                            ren_msgs_ok.append(t('admin.rename_yf_ok', old=sel_failed, new=new_ticker_name))
                                        else:
                                            ren_msgs_ok.append(t('admin.rename_yf_not_found', ticker=sel_failed))
                                        _c.close()
                                    except Exception as _e:
                                        ren_msgs_err.append(f"yf_tickers.db: {_e}")

                                if ren_info_db:
                                    try:
                                        _db = tools.Tools().get_path(path=self.db_path, file_name='asset_info.db')
                                        if os.path.exists(_db):
                                            _c = open_db(_db)
                                            _cur = _c.cursor()
                                            _cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                                            _tables = [r[0] for r in _cur.fetchall()]
                                            _renamed_in = []
                                            for _tbl in _tables:
                                                try:
                                                    _cur.execute(f"UPDATE {_tbl} SET Ticker = ? WHERE Ticker = ?", (new_ticker_name, sel_failed))
                                                    if _cur.rowcount > 0:
                                                        _renamed_in.append(_tbl)
                                                except Exception:
                                                    pass
                                            _c.commit()
                                            _c.close()
                                            if _renamed_in:
                                                ren_msgs_ok.append(t('admin.rename_info_ok', tables=', '.join(_renamed_in)))
                                            else:
                                                ren_msgs_ok.append(t('admin.rename_info_not_found', ticker=sel_failed))
                                        else:
                                            ren_msgs_ok.append(t('admin.asset_info_not_found'))
                                    except Exception as _e:
                                        ren_msgs_err.append(f"asset_info.db: {_e}")

                                if ren_price_db:
                                    try:
                                        _old_path = tools.Tools().get_path(path=self.db_path, file_name=f'yf_{sel_failed}.db')
                                        _new_path = tools.Tools().get_path(path=self.db_path, file_name=f'yf_{new_ticker_name}.db')
                                        if os.path.exists(_old_path):
                                            os.rename(_old_path, _new_path)
                                            ren_msgs_ok.append(t('admin.rename_price_ok', old=sel_failed, new=new_ticker_name))
                                        else:
                                            ren_msgs_ok.append(t('admin.rename_price_not_found', ticker=sel_failed))
                                    except Exception as _e:
                                        ren_msgs_err.append(f"Preisdatei: {_e}")

                                if ren_failed:
                                    try:
                                        _updated = []
                                        for _d in failed_data:
                                            if _d['ticker'] == sel_failed:
                                                _d = dict(_d)
                                                _d['ticker'] = new_ticker_name
                                            _updated.append(_d)
                                        with open(failed_file, 'w', encoding='utf-8') as _f:
                                            json.dump(_updated, _f, indent=2, ensure_ascii=False)
                                        ren_msgs_ok.append(t('admin.rename_list_ok', old=sel_failed, new=new_ticker_name))
                                    except Exception as _e:
                                        ren_msgs_err.append(f"Failed-Liste: {_e}")

                                for _m in ren_msgs_ok:
                                    st.success(_m)
                                for _m in ren_msgs_err:
                                    st.error(_m)
                                if ren_msgs_ok and not ren_msgs_err:
                                    st.info(t('admin.done_reload'))

                        st.markdown("---")
                        st.markdown(t('admin.delete_title'))

                        col1, col2 = st.columns(2)
                        with col1:
                            del_yf_db  = st.checkbox(t('admin.delete_yf_db'),
                                                      value=True,  key=f"del_yf_{sel_failed}")
                            del_info_db = st.checkbox("asset_info.db",
                                                      value=True,  key=f"del_info_{sel_failed}")
                        with col2:
                            del_price_db = st.checkbox(
                                t('admin.delete_price_db', ticker=sel_failed),
                                value=False, key=f"del_price_{sel_failed}"
                            )
                            del_failed_list = st.checkbox(
                                t('admin.delete_failed_list'),
                                value=True, key=f"del_list_{sel_failed}"
                            )

                        confirm_del = st.checkbox(
                            t('admin.delete_confirm', ticker=sel_failed),
                            key=f"confirm_failed_{sel_failed}"
                        )

                        if st.button(t('admin.delete_btn'), key=f"remove_failed_{sel_failed}") and confirm_del:
                            msgs_ok, msgs_err = [], []

                            if del_yf_db:
                                try:
                                    _db = tools.Tools().get_path(
                                        path=self.db_path, file_name='yf_tickers.db')
                                    _c = open_db(_db)
                                    _cur = _c.cursor()
                                    _cur.execute("SELECT id FROM stocks WHERE Ticker = ?",
                                                 (sel_failed,))
                                    _row = _cur.fetchone()
                                    if _row:
                                        _sid = _row[0]
                                        _cur.execute(
                                            "DELETE FROM stock_indices WHERE stock_id = ?", (_sid,))
                                        _cur.execute("DELETE FROM stocks WHERE id = ?", (_sid,))
                                        _cur.execute(
                                            "DELETE FROM indices WHERE id NOT IN "
                                            "(SELECT DISTINCT index_id FROM stock_indices)"
                                        )
                                        _c.commit()
                                        msgs_ok.append(t('admin.delete_yf_ok', ticker=sel_failed))
                                    else:
                                        msgs_ok.append(t('admin.delete_yf_not_found', ticker=sel_failed))
                                    _c.close()
                                except Exception as _e:
                                    msgs_err.append(f"yf_tickers.db: {_e}")

                            if del_info_db:
                                try:
                                    _db = tools.Tools().get_path(
                                        path=self.db_path, file_name='asset_info.db')
                                    if os.path.exists(_db):
                                        _c = open_db(_db)
                                        _cur = _c.cursor()
                                        _cur.execute(
                                            "SELECT name FROM sqlite_master WHERE type='table'")
                                        _tables = [r[0] for r in _cur.fetchall()]
                                        _deleted_from = []
                                        for _tbl in _tables:
                                            try:
                                                _cur.execute(
                                                    f"DELETE FROM {_tbl} WHERE Ticker = ?",
                                                    (sel_failed,))
                                                if _cur.rowcount > 0:
                                                    _deleted_from.append(_tbl)
                                            except Exception:
                                                pass
                                        _c.commit()
                                        _c.close()
                                        if _deleted_from:
                                            msgs_ok.append(t('admin.delete_info_ok', tables=', '.join(_deleted_from)))
                                        else:
                                            msgs_ok.append(t('admin.delete_info_not_found', ticker=sel_failed))
                                    else:
                                        msgs_ok.append(t('admin.asset_info_not_found'))
                                except Exception as _e:
                                    msgs_err.append(f"asset_info.db: {_e}")

                            if del_price_db:
                                try:
                                    _price_db = tools.Tools().get_path(
                                        path=self.db_path,
                                        file_name=f'yf_{sel_failed}.db')
                                    if os.path.exists(_price_db):
                                        os.remove(_price_db)
                                        msgs_ok.append(t('admin.delete_yf_ok', ticker=sel_failed))
                                    else:
                                        msgs_ok.append(t('admin.delete_price_not_found', ticker=sel_failed))
                                except Exception as _e:
                                    msgs_err.append(f"yf_{sel_failed}.db: {_e}")

                            if del_failed_list:
                                try:
                                    _updated = [d for d in failed_data
                                                if d['ticker'] != sel_failed]
                                    with open(failed_file, 'w', encoding='utf-8') as _f:
                                        json.dump(_updated, _f, indent=2, ensure_ascii=False)
                                    msgs_ok.append(t('admin.delete_list_ok', ticker=sel_failed))
                                except Exception as _e:
                                    msgs_err.append(f"Failed-Liste: {_e}")

                            for _m in msgs_ok:
                                st.success(_m)
                            for _m in msgs_err:
                                st.error(_m)
                            if msgs_ok and not msgs_err:
                                st.info(t('admin.done_reload'))

                    st.markdown("---")
                    st.markdown(t('admin.reset_failed_title'))
                    confirm_reset = st.checkbox(
                        t('admin.reset_failed_confirm'),
                        key="confirm_reset_failed_json"
                    )
                    if st.button(t('admin.reset_failed_btn'), key="delete_failed_json") and confirm_reset:
                        try:
                            os.remove(failed_file)
                            st.success(t('admin.reset_failed_ok'))
                            st.info(t('admin.refresh_hint'))
                        except Exception as _e:
                            st.error(t('admin.delete_error', error=_e))
            _spin.empty()
        _spin.empty()

    def _render_database(self, _spin):
        sql_expander = st.expander('SQL Editor', expanded=True)
        with sql_expander:
            _spin.markdown(_tab_overlay("SQL Editor"), unsafe_allow_html=True)
            sql = sq.SQLiteEditor()
            sql.render_editor()
            executor = ee.ExcelSQLExecutor()
            executor.run()
            _spin.empty()

        explorer_expander = st.expander('File explorer', expanded=False)
        with explorer_expander:
            _spin.markdown(_tab_overlay("File explorer"), unsafe_allow_html=True)
            explorer = fe.FileExplorer()
            explorer.render()
            _spin.empty()
        _spin.empty()

    def _render_credentials(self, _spin):
        st.subheader("API Credentials")
        try:
            ksp = ksplib.Ksp(storage_path=self.db_path, secrets_path=self.db_path)
        except Exception as e:
            st.error(f"Error loading credentials: {e}")
            ksp = None

        if ksp is not None:
            all_creds = ksp._get_all_credentials()

            if all_creds and isinstance(all_creds, dict):
                rows_display = [
                    {"API": api, "User / Key": v.get("user", ""), "URL": v.get("url", "")}
                    for api, v in all_creds.items()
                ]
                st.dataframe(rows_display, use_container_width=True)
            else:
                st.info("No credentials stored yet.")

            st.markdown("---")
            st.markdown("**Add / edit entry**")

            edit_options = ["— New entry —"] + list(all_creds.keys() if all_creds else [])
            ksp_edit_sel = st.selectbox("Edit existing entry:", edit_options, key="ksp_edit_sel")

            existing = {}
            if ksp_edit_sel and ksp_edit_sel != "— New entry —":
                existing = all_creds.get(ksp_edit_sel, {})

            col1, col2 = st.columns(2)
            with col1:
                ksp_api = st.text_input(
                    "API name (e.g. gapi, av-paper)",
                    value=ksp_edit_sel if ksp_edit_sel != "— New entry —" else "",
                    key="ksp_api",
                )
                ksp_user = st.text_input(
                    "User / API Key",
                    value=existing.get("user", ""),
                    key="ksp_user",
                )
            with col2:
                ksp_pw = st.text_input(
                    "Password / Token  (leave blank = keep existing)",
                    type="password",
                    key="ksp_pw",
                    help="Leave blank to keep the existing password.",
                )
                ksp_url = st.text_input(
                    "URL (optional)",
                    value=existing.get("url", ""),
                    key="ksp_url",
                )

            if st.button("Save", type="primary", key="ksp_save"):
                if ksp_api and ksp_user:
                    password_to_save = ksp_pw if ksp_pw else existing.get("password", "")
                    ksp.add_ksp(ksp_api, ksp_user, password_to_save, ksp_url)
                    st.success(f"Credentials for '{ksp_api}' saved.")
                    st.rerun()
                else:
                    st.error("API name and User / Key are required.")

            if all_creds and isinstance(all_creds, dict):
                st.markdown("---")
                st.markdown("**Delete entry**")
                ksp_del = st.selectbox(
                    "Select entry", [""] + list(all_creds.keys()), key="ksp_del_select"
                )
                if ksp_del:
                    ksp_confirm = st.checkbox(
                        f"Confirm deletion of '{ksp_del}'", key=f"ksp_confirm_{ksp_del}"
                    )
                    if st.button("Delete", key="ksp_del_btn") and ksp_confirm:
                        ksp.delete_ksp(ksp_del)
                        st.success(f"'{ksp_del}' deleted.")
                        st.rerun()
        _spin.empty()

    def _render_system(self, _spin):
        log_expander = st.expander('Log viewer', expanded=False)
        with log_expander:
            _spin.markdown(_tab_overlay("Log viewer"), unsafe_allow_html=True)

            root_logger = logging.getLogger()
            file_handlers = [h for h in root_logger.handlers if isinstance(h, logging.FileHandler)]

            if not file_handlers:
                st.info(
                    'No file handler is attached to the logger. Enable file logging '
                    '(⚙ config dialog → Logging) to write logs to disk.'
                )
            else:
                log_files: list[str] = []
                for _fh in file_handlers:
                    base_path = os.path.abspath(_fh.baseFilename)
                    if os.path.exists(base_path):
                        log_files.append(base_path)
                    base_dir, base_name = os.path.split(base_path)
                    try:
                        rotated = sorted(
                            (f for f in os.listdir(base_dir)
                             if f.startswith(base_name + '.') and f.rsplit('.', 1)[-1].isdigit()),
                            key=lambda f: int(f.rsplit('.', 1)[-1])
                        )
                    except FileNotFoundError:
                        rotated = []
                    log_files.extend(os.path.join(base_dir, f) for f in rotated)

                seen: set[str] = set()
                log_files = [p for p in log_files if not (p in seen or seen.add(p))]

                if not log_files:
                    st.warning(
                        f"Logger reports {len(file_handlers)} file handler(s), "
                        "but no log file exists on disk yet."
                    )
                else:
                    log_file = st.selectbox('Log file', options=log_files, index=0, key='logviewer_file_sel')
                    tail_lines = st.number_input(
                        'Lines to show', min_value=10, max_value=10000, value=200, step=10,
                        key='logviewer_lines'
                    )
                    if st.button('Refresh log', key='logviewer_refresh'):
                        pass

                    try:
                        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                            lines = f.readlines()
                        shown = ''.join(lines[-int(tail_lines):])
                        st.code(shown)
                        st.caption(f'Showing last {min(len(lines), int(tail_lines))} lines from: {log_file}')
                        logger.info(f'Log viewer: showed {min(len(lines), int(tail_lines))} lines from {log_file}')
                    except Exception as e:
                        st.error(f'Error reading log file: {e}')
                        logger.exception(f'Error reading log file {log_file}: {e}')
            _spin.empty()

        banner_notes_expander = st.expander('Banner note', expanded=False)
        with banner_notes_expander:
            _spin.markdown(_tab_overlay("Banner note"), unsafe_allow_html=True)

            _bn_cfg = sysconf.SystemConfig(db_path=self.db_path, username='admin')
            _bn_enabled = _bn_cfg.get_value('banner_note_enabled', True)
            if isinstance(_bn_enabled, str):
                _bn_enabled = _bn_enabled.lower() not in ('false', '0', 'no')
            _bn_toggle = st.toggle(
                'Show banner note on login page',
                value=bool(_bn_enabled),
                key='bn_enabled_toggle',
            )
            if bool(_bn_toggle) != bool(_bn_enabled):
                _bn_cfg.set_value('banner_note_enabled', str(_bn_toggle))
                st.toast('Banner note ' + ('enabled' if _bn_toggle else 'disabled'))

            st.divider()
            st.markdown(t('admin.ai_analysis_title'))

            _provider_opts = {
                'auto':   t('admin.ai_provider_auto'),
                'groq':   t('admin.ai_provider_groq'),
                'gemini': t('admin.ai_provider_gemini'),
                'ollama': t('admin.ai_provider_ollama'),
            }
            _ai_cfg = sysconf.SystemConfig(
                db_path=self.db_path, username=self.username
            )
            _saved_provider = _ai_cfg.get_value('ai_provider', None)
            if _saved_provider is None or _saved_provider not in _provider_opts:
                _saved_provider = 'auto'
                _ai_cfg.set_value('ai_provider', _saved_provider)

            _provider_sel = st.selectbox(
                t('admin.ai_provider'),
                options=list(_provider_opts.keys()),
                format_func=lambda k: _provider_opts[k],
                index=list(_provider_opts.keys()).index(_saved_provider),
                key="bn_ai_provider",
            )
            if _provider_sel != _saved_provider:
                _ai_cfg.set_value('ai_provider', _provider_sel)

            _hints = {
                'groq':   t('admin.ai_hint_groq'),
                'gemini': t('admin.ai_hint_gemini'),
                'ollama': t('admin.ai_hint_ollama'),
                'auto':   t('admin.ai_hint_auto'),
            }
            st.caption(_hints.get(_provider_sel, ''))

            st.divider()
            _bn_col1, _bn_col2 = st.columns(2)
            _force_regen = _bn_col1.checkbox(
                t('admin.ai_force'),
                value=False, key="bn_ai_force"
            )
            _debug_mode = _bn_col2.checkbox(
                t('admin.ai_debug'),
                value=False, key="bn_ai_debug"
            )

            if _debug_mode:
                if st.button(t('admin.ai_debug_run'), key="bn_ai_debug_run"):
                    try:
                        from tradinglib.banner_ai import BannerAiGenerator
                        with st.spinner(t('admin.fetching')):
                            st.session_state['_bn_dbg'] = BannerAiGenerator(
                                username=self.username
                            ).build_debug_info()
                    except Exception as _dbg_exc:
                        st.session_state['_bn_dbg'] = {'error': str(_dbg_exc)}

                if st.button(t('admin.ai_debug_clear'), key="bn_ai_debug_clear"):
                    st.session_state.pop('_bn_dbg', None)

                if '_bn_dbg' in st.session_state:
                    dbg = st.session_state['_bn_dbg']
                    if 'error' in dbg:
                        st.error(dbg['error'])
                    else:
                        _tr  = dbg.get('trade_row', {})
                        _sim = dbg.get('sim_row', {})
                        _sc  = dbg.get('strategy_ctx', {})

                        st.success(
                            f"**{dbg['ticker']}**  —  {_tr.get('longName', '')}  |  "
                            + t('admin.ai_summary_label',
                                index=_tr.get('stockIndex', 'n/a'),
                                strategy=_tr.get('Strategy', 'n/a'),
                                date=str(_tr.get('buyDate', ''))[:10])
                        )

                        _dc1, _dc2 = st.columns(2)
                        _dc1.metric("Sortino",       round(_sim.get('sortino', 0), 3))
                        _dc2.metric("Sharpe",        round(_sim.get('sharpe', 0), 3))
                        _dc3, _dc4 = st.columns(2)
                        _dc3.metric("Trend Score",   _sim.get('overallValueTrend', 'n/a'))
                        _dc4.metric("Markov Regime", _sim.get('markov_regime', 'n/a'))

                        with st.expander(t('admin.ai_last_trade', year=''), expanded=True):
                            st.json({k: str(v) for k, v in _tr.items()})

                        with st.expander(t('admin.ai_strategy_cond'), expanded=True):
                            if _sc:
                                st.json(_sc)
                            else:
                                st.warning(t('admin.ai_no_strategy'))

                        with st.expander(t('admin.ai_asset_meta'), expanded=False):
                            st.json(dbg['asset_info'])

                        with st.expander(
                            t('admin.ai_sim_metrics', count=len(_sim)),
                            expanded=False
                        ):
                            _sg = dbg.get('context', {}).get('sim_grouped', {})
                            if _sg:
                                for _grp, _vals in _sg.items():
                                    st.markdown(f"**{_grp}**")
                                    st.json(_vals)
                            else:
                                st.json({k: str(v) for k, v in _sim.items()})

                        with st.expander(t('admin.ai_ohlc'), expanded=False):
                            if not dbg['ohlc_df'].empty:
                                st.dataframe(dbg['ohlc_df'], use_container_width=True)
                            else:
                                st.warning(t('admin.ai_no_ohlc'))

                        if dbg['existing_note']:
                            with st.expander(t('admin.ai_existing_note'), expanded=False):
                                st.info(dbg['existing_note'])

                        with st.expander(
                            t('admin.ai_prompt', tokens=dbg['token_estimate']),
                            expanded=True
                        ):
                            st.code(dbg['prompt'], language='text')

            else:
                _prov_label = _provider_opts.get(_provider_sel, _provider_sel)
                if st.button(t('admin.ai_generate_btn', provider=_prov_label), key="bn_ai_generate"):
                    try:
                        from tradinglib.banner_ai import BannerAiGenerator
                        from tradinglib.ai_client import AiRateLimitError, AiProviderError
                        with st.spinner(t('admin.ai_generating', provider=_prov_label)):
                            gen_ticker, gen_text = BannerAiGenerator(
                                username=self.username
                            ).run(force=_force_regen)
                        st.success(t('admin.ai_saved', ticker=gen_ticker))
                        st.info(gen_text)
                    except AiProviderError as _pe:
                        st.error(t('admin.ai_provider_error', error=_pe))
                    except AiRateLimitError as _rle:
                        st.warning(t('admin.ai_rate_limit') + f"\n\nDetails: {_rle}")
                    except Exception as _ai_exc:
                        st.error(t('admin.ai_failed', error=_ai_exc))

            st.divider()
            st.markdown(t('admin.ai_manual_title'))
            db_table = 'banner_notes'
            db = tools.Db_tools(db_path=self.db_path, database_name=f"{db_table}.db")
            try:
                bn_df = pd.read_sql(f'select * from {db_table}', db.conn)
            except Exception:
                bn_df = pd.DataFrame(columns=['ticker', 'text'], index=['ticker'])
            ticker = st.text_input(t('admin.ai_ticker_label'), '', key="bn_ticker")
            try:
                existing_ticker = bn_df[bn_df['ticker'] == ticker]['ticker'].iloc[0]
                existing_text   = bn_df[bn_df['ticker'] == ticker]['text'].iloc[0]
                entry_exists = True
            except Exception:
                existing_ticker = ''
                existing_text   = ''
                entry_exists = False
            text = st.text_area(t('admin.ai_text_label'), existing_text, key="bn_text")
            if ticker and text != existing_text:
                if entry_exists:
                    index = bn_df[bn_df['ticker'] == ticker].index[0]
                    bn_df.loc[index, 'text'] = text
                else:
                    bn_df = pd.concat([bn_df, pd.DataFrame({'ticker': [ticker], 'text': [text]})])
                bn_df.to_sql('banner_notes', db.conn, if_exists='replace', index=False)
                db.conn.commit()
                db.conn.close()
            else:
                st.write(f"Error, no change to: {existing_ticker}")
            _spin.empty()

        config_copy_expander = st.expander("Copy user configuration", expanded=False)
        with config_copy_expander:
            _spin.markdown(_tab_overlay("Copy user configuration"), unsafe_allow_html=True)
            _cfg_db = tools.Tools().get_path(path=self.db_path, file_name='config.db')
            if not os.path.exists(_cfg_db):
                st.info("config.db not found.")
            else:
                try:
                    with open_db(_cfg_db, readonly=True) as _cc:
                        _all_rows = _cc.execute(
                            "SELECT key, value FROM config ORDER BY key"
                        ).fetchall()
                except Exception as _ce:
                    st.error(f"Could not read config.db: {_ce}")
                    _all_rows = []

                if _all_rows:
                    _users_set: set[str] = set()
                    for _rk, _ in _all_rows:
                        _users_set.add(_rk.split(':', 1)[0])
                    _user_list = sorted(_users_set)

                    def _user_label(u: str) -> str:
                        return f'(anonymous / system)' if u == '' else u

                    col_src, col_dst = st.columns(2)
                    with col_src:
                        _src = st.selectbox(
                            "Source user",
                            _user_list,
                            format_func=_user_label,
                            key="cfg_copy_src",
                        )
                    with col_dst:
                        _dst_opts = _user_list + ([""] if "" not in _user_list else [])
                        _dst = st.selectbox(
                            "Target user (existing)",
                            _dst_opts,
                            format_func=_user_label,
                            key="cfg_copy_dst",
                        )
                        _dst_custom = st.text_input(
                            "… or enter new username",
                            value="",
                            key="cfg_copy_dst_custom",
                            help="Overrides the selectbox above when not empty.",
                        ).strip()
                    _target_user = _dst_custom if _dst_custom else _dst

                    _src_prefix = f"{_src}:"
                    _src_entries = [
                        (rk.split(':', 1)[1], rv)
                        for rk, rv in _all_rows
                        if rk.startswith(_src_prefix)
                    ]

                    if not _src_entries:
                        st.info(f"No configuration found for user '{_user_label(_src)}'.")
                    else:
                        _key_names = [k for k, _ in _src_entries]
                        _sel_keys = st.multiselect(
                            "Keys to copy  (empty = copy all)",
                            options=_key_names,
                            default=[],
                            key="cfg_copy_keys",
                        )
                        _to_copy = [
                            (k, v) for k, v in _src_entries
                            if not _sel_keys or k in _sel_keys
                        ]

                        _preview = pd.DataFrame([
                            {
                                "key": k,
                                "value preview": v[:80] + ("…" if len(v) > 80 else ""),
                            }
                            for k, v in _to_copy
                        ])
                        st.dataframe(_preview, use_container_width=True, hide_index=True)

                        if _src == _target_user and not _dst_custom:
                            st.warning("Source and target user are identical — nothing to do.")
                        else:
                            _copy_label = (
                                f"Copy {len(_to_copy)} key(s) from "
                                f"'{_user_label(_src)}' → '{_user_label(_target_user)}'"
                            )
                            _copy_confirm = st.checkbox(
                                _copy_label, key="cfg_copy_confirm"
                            )
                            if (
                                st.button("Copy", type="primary", key="cfg_copy_btn")
                                and _copy_confirm
                            ):
                                try:
                                    with open_db(_cfg_db) as _cc:
                                        for _k, _v in _to_copy:
                                            _cc.execute(
                                                "INSERT INTO config (key, value) VALUES (?, ?)"
                                                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                                (f"{_target_user}:{_k}", _v),
                                            )
                                    st.success(
                                        f"Copied {len(_to_copy)} key(s) to "
                                        f"'{_user_label(_target_user)}'."
                                    )
                                    st.rerun()
                                except Exception as _ce:
                                    st.error(f"Copy failed: {_ce}")
                else:
                    st.info("No configuration data found in config.db.")
            _spin.empty()

        admin_expander = st.expander("Admin tasks", expanded=False)
        with admin_expander:
            _spin.markdown(_tab_overlay("Admin tasks"), unsafe_allow_html=True)
            try:
                email_of_registered_user, \
                username_of_registered_user, \
                name_of_registered_user = self.authenticator.register_user(location='main', captcha=False)
                if email_of_registered_user:
                    st.success(f'User registered successfully: {email_of_registered_user}, {username_of_registered_user}, {name_of_registered_user}')
            except Exception as e:
                logger.exception("User registration failed")
                st.error(e)

            st.divider()
            st.markdown("**Reset user password**")
            try:
                _live_credentials = self.authenticator.authentication_controller.authentication_model.credentials or {}
                _reset_usernames = list(_live_credentials.get('usernames', {}).keys())
                if _reset_usernames:
                    _reset_user = st.selectbox("Username", _reset_usernames, key="admin_reset_pw_user")
                    if st.button("Reset password", key="admin_reset_pw_btn"):
                        _u, _email, _new_pw = self.authenticator.authentication_controller.forgot_password(
                            _reset_user, captcha=False
                        )
                        if _u:
                            st.success(f"Password for '{_u}' reset. New password: `{_new_pw}`")
                            st.caption("Copy this password now and share it with the user — it will not be shown again.")
                        else:
                            st.warning(f"User '{_reset_user}' not found.")
                else:
                    st.info("No users found in config.yaml.")
            except Exception as e:
                logger.exception("Password reset failed")
                st.error(e)
            _spin.empty()
        _spin.empty()

    def _render_scheduler(self, _spin):
        _SCHED_VER = '4'
        if st.session_state.get('_scheduler_ver') != _SCHED_VER:
            st.session_state.pop('_scheduler', None)
            st.session_state['_scheduler_ver'] = _SCHED_VER
        if '_scheduler' not in st.session_state:
            st.session_state['_scheduler'] = sc.Scheduler(
                database=self.scheduler_db,
                run_in_browser=True,
            )
        st.session_state['_scheduler'].render_tabs()
        _spin.empty()

    def _render_pine(self, _spin):
        import os, json, ast as _ast
        from tradinglib.indicator import indicator as _ind_mod
        from tradinglib.pine_exporter import render_export_buttons

        _spin.empty()

        # Load all available indicators from the indicator package
        _loader = _ind_mod.IndicatorLoader(
            os.path.dirname(os.path.abspath(_ind_mod.__file__))
        )
        _ovl_raw = _loader.get_overlay_indicators()   # ["atc - Auto Trend Channels", ...]
        _osc_raw = _loader.get_oszilator_indicators()  # ["adx - ADX", ...]
        _ovl_map = {s.split(' - ')[0]: s for s in _ovl_raw}
        _osc_map = {s.split(' - ')[0]: s for s in _osc_raw}
        _all_ovl = sorted(_ovl_map.keys())
        _all_osc = sorted(_osc_map.keys())

        # Load stored defaults from user config
        _cfg = sysconf.SystemConfig(db_path=self.db_path, username=self.username)

        def _parse_stored(raw):
            if not raw:
                return []
            if isinstance(raw, list):
                return raw
            try:
                return json.loads(raw)
            except Exception:
                pass
            try:
                return _ast.literal_eval(raw)
            except Exception:
                return []

        _default_ovl = [n for n in _parse_stored(_cfg.get_value('overlay',   None)) if n in _ovl_map]
        _default_osc = [n for n in _parse_stored(_cfg.get_value('oszilator', None)) if n in _osc_map]

        st.markdown(f"### {t('pine.admin_title')}")
        st.caption(t("pine.admin_caption"))

        col_l, col_r = st.columns(2)
        with col_l:
            sel_ovl = st.multiselect(
                t("pine.admin_overlays_label"),
                options=_all_ovl,
                default=_default_ovl,
                format_func=lambda k: _ovl_map.get(k, k),
                key="pine_admin_ovl",
            )
        with col_r:
            sel_osc = st.multiselect(
                t("pine.admin_oscillators_label"),
                options=_all_osc,
                default=_default_osc,
                format_func=lambda k: _osc_map.get(k, k),
                key="pine_admin_osc",
            )

        if sel_ovl or sel_osc:
            st.divider()
            render_export_buttons(sel_ovl, sel_osc, _cfg)
        else:
            st.info(t("pine.admin_select_hint"))

        # ── Strategie-Export: Buy/Sell-Formeln → handelbares strategy() ──────
        st.divider()
        st.markdown(f"### {t('pine.admin_legacy_strategy_title')}")
        st.caption(t("pine.admin_legacy_strategy_caption"))
        try:
            from tradinglib import pine_strategy_export as _pse
            import re as _re2
            _exp = _pse.export_from_config(_pse._load_transactions(self.username))
            _bq = _pse._load_query(self.username, 'buy_query')
            _sq = _pse._load_query(self.username, 'sell_query')
            if _bq or _sq:
                try:
                    _exp['buy_query / sell_query'] = {'pine': _pse.export_strategy('buy_query', _bq, _sq)}
                except _pse.StrategyExportError as _e:
                    _exp['buy_query / sell_query'] = {'error': str(_e)}
            if not _exp:
                st.info(t("pine.admin_no_strategies"))
            for _name, _res in _exp.items():
                _slug = _re2.sub(r'[^A-Za-z0-9]+', '_', _name).strip('_')
                if 'pine' in _res:
                    st.download_button(
                        f"⬇ {_name}.pine",
                        data=_res['pine'],
                        file_name=f'{_slug}.pine',
                        mime='text/plain',
                        key=f'pine_strat_dl_{_slug}',
                        use_container_width=True,
                    )
                else:
                    st.caption(f"⛔ {_name}: {_res['error']}")
        except Exception as _sx:
            st.error(t("pine.admin_legacy_strategy_failed", err=_sx))
