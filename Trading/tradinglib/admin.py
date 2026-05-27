from tradinglib import tools
from tradinglib import scheduler as sc
from tradinglib import file_explorer as fe
from tradinglib import sqlite_editor as sq
from tradinglib import excel_executor as ee
import pandas as pd
import streamlit as st
import logging
import os
import sqlite3
import json
from pathlib import Path
#import streamlit_authenticator as stauth


class Admin():
    
    def __init__(self, username = '', region = st, scheduler_db = "scheduler.db", db_path = 'database', authenticator = None):
        self.username = username
        self.region = region
        self.authenticator = authenticator
        self.db_path = db_path
        self.scheduler_db = scheduler_db    

        self.render()
        
            
    def render(self):

        logger = logging.getLogger(__name__)
        logger.debug(f'Admin.render called by user={self.username}')
            
        banner_notes_expander = self.region.expander(f'Banner note', expanded = False)
        with banner_notes_expander:
            db_table = 'banner_notes'
            db = tools.Db_tools(db_path=self.db_path, database_name=f"{db_table}.db")
            try:
                bn_df = pd.read_sql(f'select * from {db_table}', db.conn)
            except Exception:
                bn_df = pd.DataFrame(columns=['ticker','text'], index=['ticker'])
                pass
            ticker = st.text_input('Ticker:','')
            try:
                existing_ticker = bn_df[bn_df['ticker']==ticker]['ticker'].iloc[0]
                existing_text = bn_df[bn_df['ticker']==ticker]['text'].iloc[0]
                entry_exists = True
            except Exception:
                existing_ticker = ''
                existing_text = ''
                entry_exists = False
                pass
            text = st.text_area('Text:',existing_text)
            if not ticker == None and not ticker == '' and not text == existing_text:                
                if entry_exists:
                    index = bn_df[bn_df['ticker']==ticker].index[0]
                    bn_df.loc[index,'text'] = text
                else:
                    bn_df = pd.concat([bn_df,pd.DataFrame({'ticker':[ticker], 'text':[text]})])
#                    bn_df = pd.DataFrame(columns=['ticker','text'], index=['ticker'])
                bn_df.to_sql('banner_notes', db.conn, if_exists='replace', index=False) # - writes the pd.df to SQLIte DB
                db.conn.commit()
                db.conn.close()
            else:
                st.write(f"Error, no change to: {existing_ticker}")

        sql_expander = self.region.expander(f'SQL Editor', expanded = True)
        with sql_expander:
            sql = sq.SQLiteEditor()
            sql.render_editor()
            executor = ee.ExcelSQLExecutor()
            executor.run()

        explorer_expander = self.region.expander(f'File explorer', expanded = False)
        with explorer_expander:
            explorer = fe.FileExplorer()
            explorer.render()

        # Log viewer - show tail of a chosen log file
        log_expander = self.region.expander('Log viewer', expanded=False)
        with log_expander:
            default_log = 'out.txt'
            log_file = st.text_input('Log file path (relative or absolute):', value=default_log)
            tail_lines = st.number_input('Lines to show', min_value=10, max_value=10000, value=200, step=10)
            if st.button('Refresh log'):
                # Button press will trigger a rerun; no explicit call to experimental_rerun()
                pass

            # Resolve file path: accept absolute, relative, or use Tools.get_path fallback
            resolved = None
            try:
                if os.path.isabs(log_file) and os.path.exists(log_file):
                    resolved = log_file
                elif os.path.exists(log_file):
                    resolved = os.path.abspath(log_file)
                else:
                    # Try to find it using the Tools path helper
                    tpath = tools.Tools().get_path(path = '', file_name=log_file)
                    if os.path.exists(tpath):
                        resolved = tpath
            except Exception:
                resolved = None

            if not resolved:
                st.info(f'Log file not found: {log_file}')
                logger.warning(f'Log viewer: file not found: {log_file}')
            else:
                try:
                    # Read tail lines (simple approach)
                    with open(resolved, 'r', encoding='utf-8', errors='replace') as f:
                        lines = f.readlines()
                    shown = ''.join(lines[-int(tail_lines):])
                    st.code(shown)
                    st.write(f'Showing last {min(len(lines), int(tail_lines))} lines from: {resolved}')
                    logger.info(f'Log viewer: showed {min(len(lines), int(tail_lines))} lines from {resolved}')
                except Exception as e:
                    st.error(f'Error reading log file: {e}')
                    logger.exception(f'Error reading log file {resolved}: {e}')

#            excel_executor = self.region.expander('Excel Executor', expanded= False)
#            with excel_executor:
            
#            tm_expander = self.region.expander('Taskmanager')
#            with tm_expander:

#                if "watcher" not in st.session_state:
#                    st.session_state.watcher = start_background_watcher()

                # Liste laufender Tasks
#                tasks = manager.get_status()
#                for script, pid, status in tasks:
#                    st.write(f"📜 `{script}` - 🆔 PID: {pid} - 🟢 Status: {status}")

                # Starten eines neuen Tasks
#                script_name = st.text_input("Python-Skript starten (Pfad):")
#                if st.button("Starten"):
#                    result = manager.start_task(script_name)
#                    st.success(result)

                # Stoppen eines Tasks
#                stop_script = st.text_input("Python-Skript stoppen (Pfad):")
#                if st.button("Stoppen"):
#                    result = manager.stop_task(stop_script)
#                    st.warning(result)
               
        admin_expander = self.region.expander("Admin tasks")
        with admin_expander:
            try:
                email_of_registered_user, \
                username_of_registered_user, \
                name_of_registered_user = self.authenticator.register_user(location='main', captcha=False )
                #authenticator.register_user(pre_authorized=config['pre-authorized']['emails'])
                if email_of_registered_user:
                    st.success(f'User registered successfully: {email_of_registered_user}, {username_of_registered_user}, {name_of_registered_user}')
                    # Save the Hashed Credentials to our config file
#                        with open(self.config_file, 'w') as file:
#                            yaml.dump(self.config, file, default_flow_style=False)
            except Exception as e:
                st.error(e)
            # ticker-management moved to its own expander (see below)

        # --- Ticker management (separate top-level expander) ---
        # This expander is intentionally placed outside/after the Admin tasks block
        ticker_expander = self.region.expander("Manage tickers (yf_tickers.db)", expanded=False)
        with ticker_expander:
            db_name = tools.Tools().get_path(path=self.db_path, file_name='yf_tickers.db')
            if not os.path.exists(db_name):
                st.info(f"Database not found: {db_name}")
            else:
                conn = None
                try:
                    conn = sqlite3.connect(db_name)
                    cur = conn.cursor()
                    cur.execute("SELECT id, Ticker FROM stocks ORDER BY Ticker")
                    rows = cur.fetchall()
                    tickers = [r[1] for r in rows]
                except Exception as e:
                    st.error(f"Could not read database: {e}")
                    if conn:
                        conn.close()
                    conn = None

                if conn:
                    selected = st.selectbox("Select ticker to remove", [""] + tickers)
                    if selected:
                        stock_id = next((r[0] for r in rows if r[1] == selected), None)
                        # show linked indices
                        try:
                            cur.execute("""
                                SELECT i.id, i.name FROM indices i
                                JOIN stock_indices si ON i.id = si.index_id
                                WHERE si.stock_id = ?
                            """, (stock_id,))
                            linked = cur.fetchall()
                        except Exception:
                            linked = []

                        if linked:
                            st.write("Linked indices:")
                            # build mapping id -> name
                            idx_map = {str(i[0]): i[1] for i in linked}
                            for iid, name in idx_map.items():
                                st.write(f"- {name}")

                            # Allow selecting a specific index to remove the link from
                            selected_index_id = st.selectbox(
                                "Select an index to remove the link from (or choose '--- Delete ticker entirely ---' below):",
                                [""] + [f"{iid} - {name}" for iid, name in idx_map.items()],
                                key=f"select_idx_{selected}"
                            )

                            # Action: remove link to selected index
                            if selected_index_id:
                                # parse id
                                sel_id = selected_index_id.split(" - ")[0]
                                confirm_link = st.checkbox("I confirm removing the link between this ticker and the selected index", key=f"confirm_link_{selected}_{sel_id}")
                                if st.button("Remove link", key=f"remove_link_{selected}_{sel_id}") and confirm_link:
                                    try:
                                        cur.execute("DELETE FROM stock_indices WHERE stock_id = ? AND index_id = ?", (stock_id, int(sel_id)))
                                        # remove index if orphaned
                                        cur.execute("DELETE FROM indices WHERE id = ? AND NOT EXISTS (SELECT 1 FROM stock_indices WHERE index_id = ?)", (int(sel_id), int(sel_id)))
                                        conn.commit()
                                        st.success(f"Removed link to index '{idx_map[sel_id]}' for ticker '{selected}'. Orphan index removed if existed.")
                                    except Exception as e:
                                        conn.rollback()
                                        st.error(f"Error removing link: {e}")
                                    finally:
                                        conn.close()

                            # Full-ticker deletion option
                            st.markdown("---")
                            confirm = st.checkbox("I confirm deletion of this ticker and all its references (entire ticker)", key=f"confirm_del_{selected}")
                            if st.button("Delete ticker entirely", key=f"del_{selected}") and confirm:
                                try:
                                    # Remove links, the stock row, then cleanup orphan indices
                                    cur.execute("DELETE FROM stock_indices WHERE stock_id = ?", (stock_id,))
                                    cur.execute("DELETE FROM stocks WHERE id = ?", (stock_id,))
                                    cur.execute("DELETE FROM indices WHERE id IN (SELECT i.id FROM indices i LEFT JOIN stock_indices si ON i.id = si.index_id WHERE si.index_id IS NULL)")
                                    conn.commit()
                                    st.success(f"Ticker '{selected}' deleted and orphan indices cleaned.")
                                except Exception as e:
                                    conn.rollback()
                                    st.error(f"Error deleting ticker: {e}")
                                finally:
                                    conn.close()
                        else:
                            st.write("No linked indices found.")

        # --- Ticker anlegen / bearbeiten ---
        add_ticker_expander = self.region.expander("Ticker anlegen / bearbeiten", expanded=False)
        with add_ticker_expander:
            db_name_add = tools.Tools().get_path(path=self.db_path, file_name='yf_tickers.db')
            if not os.path.exists(db_name_add):
                st.info(f"Datenbank nicht gefunden: {db_name_add}")
            else:
                # ── Formular ──────────────────────────────────────────────
                col_l, col_r = st.columns(2)
                with col_l:
                    new_ticker = st.text_input(
                        "Ticker Symbol *", placeholder="z. B. AAPL oder BMW.DE",
                        key="add_ticker_symbol"
                    ).strip().upper()
                    new_isin = st.text_input(
                        "ISIN (optional)", placeholder="z. B. US0378331005",
                        key="add_ticker_isin"
                    ).strip()
                with col_r:
                    new_invested = st.number_input(
                        "Investiert (optional, EUR)", min_value=0.0,
                        value=0.0, step=100.0, key="add_ticker_invested"
                    )

                # Vorhandene Indices aus der DB laden
                _all_indices: list[str] = []
                try:
                    _c_idx = sqlite3.connect(db_name_add)
                    _cur_idx = _c_idx.cursor()
                    _cur_idx.execute("SELECT name FROM indices ORDER BY name")
                    _all_indices = [r[0] for r in _cur_idx.fetchall()]
                    _c_idx.close()
                except Exception:
                    pass

                st.markdown("**Index-Verknüpfung**")
                col_idx, col_newidx = st.columns(2)
                with col_idx:
                    sel_indices = st.multiselect(
                        "Vorhandene Indices auswählen",
                        options=_all_indices,
                        key="add_ticker_indices"
                    )
                with col_newidx:
                    new_index_name = st.text_input(
                        "Neuen Index anlegen (optional)",
                        placeholder="z. B. DAX oder MDAX",
                        key="add_ticker_new_index"
                    ).strip()

                # ── Vorschau: existiert der Ticker bereits? ───────────────
                if new_ticker:
                    try:
                        _c_prev = sqlite3.connect(db_name_add)
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
                            st.info(
                                f"Ticker **{new_ticker}** ist bereits vorhanden – "
                                f"ISIN: `{_existing_row[4] or '–'}` | "
                                f"Investiert: `{_existing_row[3] or 0:.2f} €` | "
                                f"Indices: `{', '.join(_ex_indices) or '–'}`\n\n"
                                "Beim Speichern werden ISIN und Investiert-Wert aktualisiert "
                                "und neue Indexverknüpfungen ergänzt (bestehende bleiben erhalten)."
                            )
                        _c_prev.close()
                    except Exception:
                        pass

                st.markdown("---")
                if st.button("Ticker speichern", key="add_ticker_save", type="primary"):
                    if not new_ticker:
                        st.error("Bitte ein Ticker-Symbol eingeben.")
                    else:
                        _msgs_ok, _msgs_err = [], []
                        try:
                            from datetime import datetime as _dt
                            _conn = sqlite3.connect(db_name_add)
                            _cur  = _conn.cursor()

                            # Sicherstellen, dass die Tabellen existieren
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

                            # Ticker anlegen oder aktualisieren
                            _cur.execute(
                                "SELECT id FROM stocks WHERE Ticker = ?", (new_ticker,))
                            _ex = _cur.fetchone()
                            if _ex:
                                _stock_id = _ex[0]
                                _cur.execute(
                                    "UPDATE stocks SET INVESTED = ?, ISIN = ? WHERE id = ?",
                                    (new_invested or None, new_isin or None, _stock_id)
                                )
                                _msgs_ok.append(f"Ticker **{new_ticker}** aktualisiert.")
                            else:
                                _date = _dt.now().strftime("%Y-%m-%d")
                                _cur.execute(
                                    "INSERT INTO stocks (Ticker, Date, INVESTED, ISIN) "
                                    "VALUES (?, ?, ?, ?)",
                                    (new_ticker, _date, new_invested or None, new_isin or None)
                                )
                                _stock_id = _cur.lastrowid
                                _msgs_ok.append(f"Ticker **{new_ticker}** neu angelegt.")

                            # Alle gewählten + neuen Indices verknüpfen
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
                                # Verknüpfung nur einfügen, wenn noch nicht vorhanden
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
                                    _msgs_ok.append(
                                        f"Verknüpfung mit Index **{_idx_name}** angelegt.")

                            _conn.commit()
                            _conn.close()

                        except Exception as _e:
                            _msgs_err.append(f"Fehler beim Speichern: {_e}")
                            logger.exception("Fehler beim Anlegen von Ticker %s", new_ticker)

                        for _m in _msgs_ok:
                            st.success(_m)
                        for _m in _msgs_err:
                            st.error(_m)
                        if _msgs_ok and not _msgs_err:
                            st.info(
                                f"Ticker **{new_ticker}** ist jetzt in yf_tickers.db. "
                                "Preisdaten können mit `get_asset_data.py /select:WHERE Ticker='"
                                f"{new_ticker}'` heruntergeladen werden."
                            )

        # --- Delisted / Fehlgeschlagene Ticker ---
        delisted_expander = self.region.expander("Delisted / Fehlgeschlagene Ticker", expanded=False)
        with delisted_expander:
            failed_file = tools.Tools().get_path(path=self.db_path, file_name='failed_tickers.json')

            if not os.path.exists(failed_file):
                st.info(
                    "Noch keine fehlgeschlagenen Ticker vorhanden. "
                    "(Datei 'failed_tickers.json' wurde noch nicht angelegt – "
                    "sie entsteht automatisch beim nächsten Lauf von get_asset_data.py)"
                )
            else:
                failed_data = []
                try:
                    with open(failed_file, 'r', encoding='utf-8') as _f:
                        failed_data = json.load(_f)
                except Exception as _e:
                    st.error(f"Fehler beim Lesen von failed_tickers.json: {_e}")

                if not failed_data:
                    st.success("Keine fehlgeschlagenen Ticker – alles in Ordnung.")
                else:
                    st.warning(f"**{len(failed_data)} Ticker** mit fehlgeschlagenen Downloads:")

                    # Übersichtstabelle
                    df_failed = pd.DataFrame(failed_data)[
                        ['ticker', 'failed_intervals', 'first_seen', 'last_seen']
                    ]
                    df_failed['failed_intervals'] = df_failed['failed_intervals'].apply(
                        lambda x: ', '.join(x) if isinstance(x, list) else str(x)
                    )
                    st.dataframe(df_failed, use_container_width=True)

                    st.markdown("---")
                    sel_failed = st.selectbox(
                        "Ticker auswählen:",
                        [""] + [d['ticker'] for d in failed_data],
                        key="sel_failed_ticker"
                    )

                    if sel_failed:
                        st.markdown(f"#### Ticker: `{sel_failed}`")

                        # ── Yahoo-Finance-Prüfung ──────────────────────────────
                        if st.button("Yahoo Finance jetzt prüfen", key=f"check_yf_{sel_failed}"):
                            with st.spinner("Rufe Daten ab …"):
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
                                        st.success(
                                            f"Yahoo Finance liefert Daten für **{sel_failed}**. "
                                            "Ticker scheint aktiv zu sein."
                                        )
                                    else:
                                        st.error(
                                            f"Keine aktuellen Daten für **{sel_failed}** "
                                            "– Ticker ist wahrscheinlich delisted."
                                        )
                                except Exception as _e:
                                    st.error(f"Fehler bei Yahoo-Abfrage: {_e}")

                        st.markdown("---")
                        st.markdown("**Aus Datenbank entfernen:**")

                        col1, col2 = st.columns(2)
                        with col1:
                            del_yf_db  = st.checkbox("yf_tickers.db (stocks + indices)",
                                                      value=True,  key=f"del_yf_{sel_failed}")
                            del_info_db = st.checkbox("asset_info.db",
                                                      value=True,  key=f"del_info_{sel_failed}")
                        with col2:
                            del_price_db = st.checkbox(
                                f"Preisdatei yf_{sel_failed}.db löschen",
                                value=False, key=f"del_price_{sel_failed}"
                            )
                            del_failed_list = st.checkbox(
                                "Aus Failed-Liste entfernen",
                                value=True, key=f"del_list_{sel_failed}"
                            )

                        confirm_del = st.checkbox(
                            f"Ich bestätige die Löschung von **{sel_failed}**",
                            key=f"confirm_failed_{sel_failed}"
                        )

                        if st.button("Jetzt entfernen", key=f"remove_failed_{sel_failed}") and confirm_del:
                            msgs_ok, msgs_err = [], []

                            # 1) yf_tickers.db
                            if del_yf_db:
                                try:
                                    _db = tools.Tools().get_path(
                                        path=self.db_path, file_name='yf_tickers.db')
                                    _c = sqlite3.connect(_db)
                                    _cur = _c.cursor()
                                    _cur.execute("SELECT id FROM stocks WHERE Ticker = ?",
                                                 (sel_failed,))
                                    _row = _cur.fetchone()
                                    if _row:
                                        _sid = _row[0]
                                        _cur.execute(
                                            "DELETE FROM stock_indices WHERE stock_id = ?", (_sid,))
                                        _cur.execute("DELETE FROM stocks WHERE id = ?", (_sid,))
                                        # Verwaiste Indices bereinigen
                                        _cur.execute(
                                            "DELETE FROM indices WHERE id NOT IN "
                                            "(SELECT DISTINCT index_id FROM stock_indices)"
                                        )
                                        _c.commit()
                                        msgs_ok.append(
                                            f"'{sel_failed}' aus yf_tickers.db entfernt "
                                            "(incl. Indexverknüpfungen).")
                                    else:
                                        msgs_ok.append(
                                            f"'{sel_failed}' nicht in yf_tickers.db gefunden.")
                                    _c.close()
                                except Exception as _e:
                                    msgs_err.append(f"yf_tickers.db: {_e}")

                            # 2) asset_info.db
                            if del_info_db:
                                try:
                                    _db = tools.Tools().get_path(
                                        path=self.db_path, file_name='asset_info.db')
                                    if os.path.exists(_db):
                                        _c = sqlite3.connect(_db)
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
                                            msgs_ok.append(
                                                f"Aus asset_info.db entfernt "
                                                f"(Tabellen: {', '.join(_deleted_from)}).")
                                        else:
                                            msgs_ok.append(
                                                f"'{sel_failed}' nicht in asset_info.db gefunden.")
                                    else:
                                        msgs_ok.append("asset_info.db nicht gefunden – übersprungen.")
                                except Exception as _e:
                                    msgs_err.append(f"asset_info.db: {_e}")

                            # 3) yf_<TICKER>.db Preisdatei
                            if del_price_db:
                                try:
                                    _price_db = tools.Tools().get_path(
                                        path=self.db_path,
                                        file_name=f'yf_{sel_failed}.db')
                                    if os.path.exists(_price_db):
                                        os.remove(_price_db)
                                        msgs_ok.append(f"Datei yf_{sel_failed}.db gelöscht.")
                                    else:
                                        msgs_ok.append(
                                            f"Datei yf_{sel_failed}.db nicht gefunden.")
                                except Exception as _e:
                                    msgs_err.append(f"yf_{sel_failed}.db: {_e}")

                            # 4) Aus Failed-Liste entfernen
                            if del_failed_list:
                                try:
                                    _updated = [d for d in failed_data
                                                if d['ticker'] != sel_failed]
                                    with open(failed_file, 'w', encoding='utf-8') as _f:
                                        json.dump(_updated, _f, indent=2, ensure_ascii=False)
                                    msgs_ok.append(
                                        f"'{sel_failed}' aus der Failed-Liste entfernt.")
                                except Exception as _e:
                                    msgs_err.append(f"Failed-Liste: {_e}")

                            for _m in msgs_ok:
                                st.success(_m)
                            for _m in msgs_err:
                                st.error(_m)
                            if msgs_ok and not msgs_err:
                                st.info("Fertig. Seite neu laden um die Liste zu aktualisieren.")

        scheduler_expander = self.region.expander(f'Scheduler', expanded = False)
        with scheduler_expander:
            btn_shw_sch = st.button("Show scheduled tasks")
            if btn_shw_sch:
                scheduler = sc.Scheduler(database=tools.Tools().get_path(path = '', file_name=self.scheduler_db))
#                    scheduler = sc.Scheduler(database=self.scheduler_db)
                scheduler.render()
