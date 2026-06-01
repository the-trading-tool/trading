from tradinglib import tools
from tradinglib import scheduler as sc
from tradinglib import file_explorer as fe
from tradinglib import sqlite_editor as sq
from tradinglib import excel_executor as ee
from tradinglib import ksplib
from tradinglib import system_config as sysconf
import pandas as pd
import streamlit as st
import logging
import os
import sqlite3
import json
from pathlib import Path
#import streamlit_authenticator as stauth


class Admin():

    def __init__(self, username='', region=st, scheduler_db="scheduler.db", db_path='database', authenticator=None):
        self.username = username
        self.region = region
        self.authenticator = authenticator
        self.db_path = db_path
        self.scheduler_db = scheduler_db
        self.render()

    def render(self):
        logger = logging.getLogger(__name__)
        logger.debug(f'Admin.render called by user={self.username}')

        tab_ticker, tab_db, tab_creds, tab_system = self.region.tabs(
            ["Ticker", "Database", "API Credentials", "System"]
        )

        # ── Tab: Ticker ────────────────────────────────────────────────────────
        with tab_ticker:

            add_ticker_expander = st.expander("Ticker anlegen / bearbeiten", expanded=False)
            with add_ticker_expander:
                db_name_add = tools.Tools().get_path(path=self.db_path, file_name='yf_tickers.db')
                if not os.path.exists(db_name_add):
                    st.info(f"Datenbank nicht gefunden: {db_name_add}")
                else:
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
                            "Invested (optional, EUR)", min_value=0.0,
                            value=0.0, step=100.0, key="add_ticker_invested"
                        )

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
                                    f"Invested: `{_existing_row[3] or 0:.2f} €` | "
                                    f"Indices: `{', '.join(_ex_indices) or '–'}`\n\n"
                                    "Saving will update ISIN and invested value "
                                    "and add new index links (existing ones are kept)."
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
                                        _msgs_ok.append(
                                            f"Verknüpfung mit Index **{_idx_name}** angelegt.")

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
                                st.info(
                                    f"Ticker **{new_ticker}** ist jetzt in yf_tickers.db. "
                                    "Preisdaten können mit `get_asset_data.py /select:WHERE Ticker='"
                                    f"{new_ticker}'` heruntergeladen werden."
                                )

            ticker_expander = st.expander("Manage tickers (yf_tickers.db)", expanded=False)
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
                                idx_map = {str(i[0]): i[1] for i in linked}
                                for iid, name in idx_map.items():
                                    st.write(f"- {name}")

                                selected_index_id = st.selectbox(
                                    "Select an index to remove the link from (or choose '--- Delete ticker entirely ---' below):",
                                    [""] + [f"{iid} - {name}" for iid, name in idx_map.items()],
                                    key=f"select_idx_{selected}"
                                )

                                if selected_index_id:
                                    sel_id = selected_index_id.split(" - ")[0]
                                    confirm_link = st.checkbox("I confirm removing the link between this ticker and the selected index", key=f"confirm_link_{selected}_{sel_id}")
                                    if st.button("Remove link", key=f"remove_link_{selected}_{sel_id}") and confirm_link:
                                        try:
                                            cur.execute("DELETE FROM stock_indices WHERE stock_id = ? AND index_id = ?", (stock_id, int(sel_id)))
                                            cur.execute("DELETE FROM indices WHERE id = ? AND NOT EXISTS (SELECT 1 FROM stock_indices WHERE index_id = ?)", (int(sel_id), int(sel_id)))
                                            conn.commit()
                                            st.success(f"Removed link to index '{idx_map[sel_id]}' for ticker '{selected}'. Orphan index removed if existed.")
                                        except Exception as e:
                                            conn.rollback()
                                            st.error(f"Error removing link: {e}")
                                        finally:
                                            conn.close()

                                st.markdown("---")
                                confirm = st.checkbox("I confirm deletion of this ticker and all its references (entire ticker)", key=f"confirm_del_{selected}")
                                if st.button("Delete ticker entirely", key=f"del_{selected}") and confirm:
                                    try:
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

            delisted_expander = st.expander("Delisted / Fehlgeschlagene Ticker", expanded=False)
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

                        df_failed = pd.DataFrame(failed_data)[
                            ['ticker', 'failed_intervals', 'first_seen', 'last_seen']
                        ]
                        df_failed['failed_intervals'] = df_failed['failed_intervals'].apply(
                            lambda x: ', '.join(x) if isinstance(x, list) else str(x)
                        )
                        df_failed.insert(0, 'details', df_failed['ticker'].apply(lambda t: f'/?symbol={t}&details=True'))
                        st.dataframe(df_failed, use_container_width=True,
                                     column_config={'details': st.column_config.LinkColumn('Details', display_text='View')})

                        st.markdown("---")
                        sel_failed = st.selectbox(
                            "Ticker auswählen:",
                            [""] + [d['ticker'] for d in failed_data],
                            key="sel_failed_ticker"
                        )

                        if sel_failed:
                            st.markdown(f"#### Ticker: `{sel_failed}`")

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

                                if del_price_db:
                                    try:
                                        _price_db = tools.Tools().get_path(
                                            path=self.db_path,
                                            file_name=f'yf_{sel_failed}.db')
                                        if os.path.exists(_price_db):
                                            os.remove(_price_db)
                                            msgs_ok.append(f"File yf_{sel_failed}.db deleted.")
                                        else:
                                            msgs_ok.append(
                                                f"Datei yf_{sel_failed}.db nicht gefunden.")
                                    except Exception as _e:
                                        msgs_err.append(f"yf_{sel_failed}.db: {_e}")

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

        # ── Tab: Datenbank ─────────────────────────────────────────────────────
        with tab_db:

            sql_expander = st.expander('SQL Editor', expanded=True)
            with sql_expander:
                sql = sq.SQLiteEditor()
                sql.render_editor()
                executor = ee.ExcelSQLExecutor()
                executor.run()

            explorer_expander = st.expander('File explorer', expanded=False)
            with explorer_expander:
                explorer = fe.FileExplorer()
                explorer.render()

        # ── Tab: API Credentials ───────────────────────────────────────────────
        with tab_creds:

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

                # Select existing entry to prefill the form
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
                        # Keep existing password if field was left blank
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

        # ── Tab: System ────────────────────────────────────────────────────────
        with tab_system:

            log_expander = st.expander('Log viewer', expanded=False)
            with log_expander:
                default_log = 'out.txt'
                log_file = st.text_input('Log file path (relative or absolute):', value=default_log)
                tail_lines = st.number_input('Lines to show', min_value=10, max_value=10000, value=200, step=10)
                if st.button('Refresh log'):
                    pass

                resolved = None
                try:
                    if os.path.isabs(log_file) and os.path.exists(log_file):
                        resolved = log_file
                    elif os.path.exists(log_file):
                        resolved = os.path.abspath(log_file)
                    else:
                        tpath = tools.Tools().get_path(path='', file_name=log_file)
                        if os.path.exists(tpath):
                            resolved = tpath
                except Exception:
                    resolved = None

                if not resolved:
                    st.info(f'Log file not found: {log_file}')
                    logger.warning(f'Log viewer: file not found: {log_file}')
                else:
                    try:
                        with open(resolved, 'r', encoding='utf-8', errors='replace') as f:
                            lines = f.readlines()
                        shown = ''.join(lines[-int(tail_lines):])
                        st.code(shown)
                        st.write(f'Showing last {min(len(lines), int(tail_lines))} lines from: {resolved}')
                        logger.info(f'Log viewer: showed {min(len(lines), int(tail_lines))} lines from {resolved}')
                    except Exception as e:
                        st.error(f'Error reading log file: {e}')
                        logger.exception(f'Error reading log file {resolved}: {e}')

            scheduler_expander = st.expander('Scheduler', expanded=False)
            with scheduler_expander:
                btn_shw_sch = st.button("Show scheduled tasks")
                if btn_shw_sch:
                    scheduler = sc.Scheduler(database=tools.Tools().get_path(path='', file_name=self.scheduler_db))
                    scheduler.render()

            banner_notes_expander = st.expander('Banner note', expanded=False)
            with banner_notes_expander:
                st.markdown("**Auto-Analyse via KI**")

                # ── Provider-Auswahl ─────────────────────────────────────────
                _provider_opts = {
                    'auto':   '🔄 Auto (Groq → Gemini → Ollama)',
                    'groq':   '⚡ Groq  (kostenlos, schnell)',
                    'gemini': '🔷 Gemini (Google, kostenlos)',
                    'ollama': '🖥️ Ollama (lokal, kein Limit)',
                }
                _ai_cfg = sysconf.SystemConfig(
                    db_path=self.db_path, username=self.username
                )
                # Use sentinel None so we can detect "never saved" and write the
                # default to DB — otherwise this key never appears in Copy Config.
                _saved_provider = _ai_cfg.get_value('ai_provider', None)
                if _saved_provider is None or _saved_provider not in _provider_opts:
                    _saved_provider = 'auto'
                    _ai_cfg.set_value('ai_provider', _saved_provider)  # ensure in DB

                _provider_sel = st.selectbox(
                    "KI-Provider:",
                    options=list(_provider_opts.keys()),
                    format_func=lambda k: _provider_opts[k],
                    index=list(_provider_opts.keys()).index(_saved_provider),
                    key="bn_ai_provider",
                )
                if _provider_sel != _saved_provider:
                    _ai_cfg.set_value('ai_provider', _provider_sel)

                # Kurzhinweis je Provider
                _hints = {
                    'groq':   "API-Key: https://console.groq.com → Credentials → Name: **groq**",
                    'gemini': "API-Key: https://aistudio.google.com → Credentials → Name: **gapi**",
                    'ollama': "Lokal installieren: https://ollama.com → `ollama serve` → Modell in Credentials → Name: **ollama**",
                    'auto':   "Nutzt den ersten verfügbaren Provider (Groq → Gemini → Ollama).",
                }
                st.caption(_hints.get(_provider_sel, ''))

                st.divider()
                _bn_col1, _bn_col2 = st.columns(2)
                _force_regen = _bn_col1.checkbox(
                    "Neu erzwingen (auch wenn heute schon analysiert)",
                    value=False, key="bn_ai_force"
                )
                _debug_mode = _bn_col2.checkbox(
                    "🔍 Debug: Prompt vor API-Aufruf anzeigen",
                    value=False, key="bn_ai_debug"
                )

                if _debug_mode:
                    # Debug-Ergebnis im session_state speichern, damit es
                    # Streamlit-Reruns (z.B. durch Checkbox-Klick) überlebt.
                    if st.button("🔍 Debug-Info laden (kein API-Call)", key="bn_ai_debug_run"):
                        try:
                            from tradinglib.banner_ai import BannerAiGenerator
                            with st.spinner("Daten aus lokalen DBs laden…"):
                                st.session_state['_bn_dbg'] = BannerAiGenerator(
                                    username=self.username
                                ).build_debug_info()
                        except Exception as _dbg_exc:
                            st.session_state['_bn_dbg'] = {'error': str(_dbg_exc)}

                    if st.button("🗑️ Ergebnis löschen", key="bn_ai_debug_clear"):
                        st.session_state.pop('_bn_dbg', None)

                    # Anzeige — immer sichtbar solange _bn_dbg im state ist
                    if '_bn_dbg' in st.session_state:
                        dbg = st.session_state['_bn_dbg']
                        if 'error' in dbg:
                            st.error(dbg['error'])
                        else:
                            _tr  = dbg.get('trade_row', {})
                            _sim = dbg.get('sim_row', {})
                            _sc  = dbg.get('strategy_ctx', {})

                            # ── Zusammenfassung ──────────────────────────
                            st.success(
                                f"**{dbg['ticker']}**  —  {_tr.get('longName', '')}  |  "
                                f"Index: {_tr.get('stockIndex', 'n/a')}  |  "
                                f"Strategie: {_tr.get('Strategy', 'n/a')}  |  "
                                f"Kaufdatum: {str(_tr.get('buyDate',''))[:10]}"
                            )

                            _dc1, _dc2 = st.columns(2)
                            _dc1.metric("Sortino",       round(_sim.get('sortino', 0), 3))
                            _dc2.metric("Sharpe",        round(_sim.get('sharpe', 0), 3))
                            _dc3, _dc4 = st.columns(2)
                            _dc3.metric("Trend-Score",   _sim.get('overallValueTrend', 'n/a'))
                            _dc4.metric("Markov-Regime", _sim.get('markov_regime', 'n/a'))

                            # ── Letzter Trade ────────────────────────────
                            with st.expander("🏷️ Letzter Trade (trades{year}.db)", expanded=True):
                                st.json({k: str(v) for k, v in _tr.items()})

                            # ── Strategie-Bedingungen ────────────────────
                            with st.expander("⚙️ Strategie-Bedingungen (multi_transactions)", expanded=True):
                                if _sc:
                                    st.json(_sc)
                                else:
                                    st.warning("Keine passenden Strategie-Bedingungen gefunden.")

                            # ── Asset-Info ───────────────────────────────
                            with st.expander("📋 Asset-Metadaten (asset_info.db)", expanded=False):
                                st.json(dbg['asset_info'])

                            # ── Simulation-Row ───────────────────────────
                            with st.expander(
                                f"📊 Alle {len(_sim)} Simulationskennzahlen (asset_simulation_all.db)",
                                expanded=False
                            ):
                                _sg = dbg.get('context', {}).get('sim_grouped', {})
                                if _sg:
                                    for _grp, _vals in _sg.items():
                                        st.markdown(f"**{_grp}**")
                                        st.json(_vals)
                                else:
                                    st.json({k: str(v) for k, v in _sim.items()})

                            # ── OHLC ────────────────────────────────────
                            with st.expander("📈 OHLC letzte 20 Tage", expanded=False):
                                if not dbg['ohlc_df'].empty:
                                    st.dataframe(dbg['ohlc_df'], use_container_width=True)
                                else:
                                    st.warning("Keine OHLC-Daten gefunden.")

                            # ── Bestehender Eintrag ──────────────────────
                            if dbg['existing_note']:
                                with st.expander("💾 Bereits gespeicherter Eintrag (heute)", expanded=False):
                                    st.info(dbg['existing_note'])

                            # ── Prompt ───────────────────────────────────
                            with st.expander(
                                f"📝 Fertig gebauter Prompt (~{dbg['token_estimate']} Tokens)",
                                expanded=True
                            ):
                                st.code(dbg['prompt'], language='text')

                else:
                    _prov_label = _provider_opts.get(_provider_sel, _provider_sel)
                    if st.button(f"🤖 Top-Signal analysieren  ({_prov_label})", key="bn_ai_generate"):
                        try:
                            from tradinglib.banner_ai import BannerAiGenerator
                            from tradinglib.ai_client import AiRateLimitError, AiProviderError
                            with st.spinner(f"KI analysiert das Top-Buy-Signal ({_prov_label})…"):
                                gen_ticker, gen_text = BannerAiGenerator(
                                    username=self.username
                                ).run(force=_force_regen)
                            st.success(f"Analyse für **{gen_ticker}** gespeichert.")
                            st.info(gen_text)
                        except AiProviderError as _pe:
                            st.error(
                                f"⚙️ **Provider-Konfigurationsfehler**\n\n{_pe}\n\n"
                                "API-Credentials unter **Admin → API Credentials** eintragen."
                            )
                        except AiRateLimitError as _rle:
                            st.warning(
                                "⏳ **Rate-Limit / Quota erschöpft.**\n\n"
                                "Alle konfigurierten Provider haben ihr Limit erreicht. "
                                "Alternativen:\n"
                                "- Anderen Provider wählen (z.B. Groq oder Ollama)\n"
                                "- Warten bis Quota-Reset (~1 Min für RPM, Mitternacht UTC für Tagesquote)\n\n"
                                f"Details: {_rle}"
                            )
                        except Exception as _ai_exc:
                            st.error(f"KI-Analyse fehlgeschlagen: {_ai_exc}")

                st.divider()
                st.markdown("**Manuelle Eingabe**")
                db_table = 'banner_notes'
                db = tools.Db_tools(db_path=self.db_path, database_name=f"{db_table}.db")
                try:
                    bn_df = pd.read_sql(f'select * from {db_table}', db.conn)
                except Exception:
                    bn_df = pd.DataFrame(columns=['ticker', 'text'], index=['ticker'])
                ticker = st.text_input('Ticker:', '', key="bn_ticker")
                try:
                    existing_ticker = bn_df[bn_df['ticker'] == ticker]['ticker'].iloc[0]
                    existing_text   = bn_df[bn_df['ticker'] == ticker]['text'].iloc[0]
                    entry_exists = True
                except Exception:
                    existing_ticker = ''
                    existing_text   = ''
                    entry_exists = False
                text = st.text_area('Text:', existing_text, key="bn_text")
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

            config_copy_expander = st.expander("Copy user configuration", expanded=False)
            with config_copy_expander:
                _cfg_db = tools.Tools().get_path(path=self.db_path, file_name='config.db')
                if not os.path.exists(_cfg_db):
                    st.info("config.db not found.")
                else:
                    try:
                        with sqlite3.connect(_cfg_db) as _cc:
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
                                        with sqlite3.connect(_cfg_db) as _cc:
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

            admin_expander = st.expander("Admin tasks", expanded=False)
            with admin_expander:
                try:
                    email_of_registered_user, \
                    username_of_registered_user, \
                    name_of_registered_user = self.authenticator.register_user(location='main', captcha=False)
                    if email_of_registered_user:
                        st.success(f'User registered successfully: {email_of_registered_user}, {username_of_registered_user}, {name_of_registered_user}')
                except Exception as e:
                    st.error(e)
