"""
Scalable Capital CSV-Import-Modul.

Herausgelöst aus own_trades_analysis.py.
Öffentliche API:
  parse_scalable_csv(df_raw)  → dict mit kategorisierten Sub-DataFrames
  render_scalable_import(region, db_path, system_currency)
"""
import datetime as dt
import logging
import uuid

import numpy as np
import pandas as pd
import streamlit as st

from tradinglib import tools

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Konstanten
# ─────────────────────────────────────────────────────────────────────────────

_SCALABLE_COLS = {'date', 'time', 'status', 'reference', 'description',
                  'assettype', 'type', 'isin', 'shares', 'price',
                  'amount', 'fee', 'tax', 'currency'}

_SCALABLE_ACTION_MAP = {
    'buy':               'buy',
    'sell':              'sell',
    'distribution':      'dividend',
    'interest':          'interest',
    'fee':               'fee',
    'cash transfer out': 'transfer_out',
    'cash transfer in':  'transfer_in',
}


# ─────────────────────────────────────────────────────────────────────────────
# Helfer
# ─────────────────────────────────────────────────────────────────────────────

def _is_scalable_csv(df: pd.DataFrame) -> bool:
    """Return True if df looks like a Scalable Capital export."""
    cols = {c.lower().strip() for c in df.columns}
    return _SCALABLE_COLS.issubset(cols)


def _parse_de_number(val) -> float:
    """Parse German-format number string → float. Returns 0.0 on failure."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return 0.0
    s = str(val).strip()
    if not s:
        return 0.0
    try:
        # Remove thousands separator, replace decimal comma
        s = s.replace('.', '').replace(',', '.')
        return float(s)
    except Exception:
        return 0.0


def _resolve_isin_to_ticker(isin: str, db_path: str = 'database') -> str:
    """
    Look up Yahoo Finance ticker for an ISIN.

    Resolution order:
      1. yf_tickers.db → stocks table  (primary local source; ISIN column)
      2. yfinance: yf.Ticker(isin).info['symbol']  (network fallback)
         → result is written back to yf_tickers.db/stocks for future lookups

    Fallback: return isin unchanged.
    """
    import sqlite3 as _sq

    if not isin or not isinstance(isin, str):
        return isin or ''
    isin = isin.strip().upper()

    tickers_db = tools.Tools().get_path(path=db_path, file_name='yf_tickers.db')

    # ── 1. yf_tickers.db / stocks ─────────────────────────────────────────
    try:
        with _sq.connect(tickers_db) as conn:
            # Ensure ISIN column exists (added by ensure_isin_column below if needed)
            row = conn.execute(
                "SELECT Ticker FROM stocks WHERE UPPER(ISIN)=? AND Ticker IS NOT NULL LIMIT 1",
                (isin,),
            ).fetchone()
        if row and row[0]:
            return str(row[0]).strip().upper()
    except Exception:
        pass

    # ── 2. yfinance network lookup ─────────────────────────────────────────
    try:
        import yfinance as yf
        info = yf.Ticker(isin).info
        symbol = (info.get('symbol') or '').strip().upper()
        if symbol and symbol != isin:
            # Write back to yf_tickers.db so next lookup is local
            try:
                with _sq.connect(tickers_db) as conn:
                    # Ensure table + ISIN column exist
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS stocks (
                            id      INTEGER PRIMARY KEY AUTOINCREMENT,
                            Ticker  TEXT,
                            Date    TEXT,
                            INVESTED REAL,
                            ISIN    TEXT
                        )
                    """)
                    try:
                        conn.execute("ALTER TABLE stocks ADD COLUMN ISIN TEXT")
                    except Exception:
                        pass  # column already exists
                    # Update existing row or insert new one
                    existing = conn.execute(
                        "SELECT id FROM stocks WHERE Ticker=? LIMIT 1", (symbol,)
                    ).fetchone()
                    if existing:
                        conn.execute(
                            "UPDATE stocks SET ISIN=? WHERE id=?",
                            (isin, existing[0]),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO stocks (Ticker, ISIN, Date) VALUES (?, ?, ?)",
                            (symbol, isin, dt.datetime.now().strftime('%Y-%m-%d')),
                        )
            except Exception as e:
                logger.debug(f'Could not write ISIN→ticker to yf_tickers.db: {e}')
            return symbol.upper()
    except Exception:
        pass

    return isin  # fallback: use ISIN as ticker


def _resolve_tickers_for_df(df: pd.DataFrame, db_path: str, status_placeholder=None) -> pd.DataFrame:
    """
    Add a 'ticker' column to a parsed sub-frame by resolving each ISIN.
    Updates status_placeholder (st.empty) with progress if provided.
    """
    if df.empty or 'isin' not in df.columns:
        return df
    df = df.copy()
    isins = df['isin'].dropna().unique().tolist()
    isins = [i for i in isins if i and i != 'NAN' and len(i) > 3]
    isin_ticker_map = {}
    for idx, isin in enumerate(isins):
        if status_placeholder:
            try:
                status_placeholder.caption(f'Resolving {isin} ({idx+1}/{len(isins)}) …')
            except Exception:
                pass
        isin_ticker_map[isin] = _resolve_isin_to_ticker(isin, db_path)
    if status_placeholder:
        try:
            status_placeholder.empty()
        except Exception:
            pass
    df['ticker'] = df['isin'].map(isin_ticker_map).fillna(df['isin'])
    return df


def _show_scalable_df(df: pd.DataFrame, col_cfg: dict):
    """Helper: show a parsed Scalable sub-frame with appropriate column config."""
    disp_cols = [c for c in ['timestamp', 'action', 'ticker', 'isin', 'longname',
                              'shares', 'price', 'amount', 'fee', 'tax', 'currency']
                 if c in df.columns]
    st.dataframe(
        df[disp_cols],
        hide_index=True,
        use_container_width=True,
        column_config={k: v for k, v in col_cfg.items() if k in disp_cols},
    )


def _insert_scalable_rows(df: pd.DataFrame, db_path: str = 'database') -> int:
    """Insert parsed Scalable Capital rows into trades.db. Returns inserted count."""
    dbt = tools.Db_tools(db_path=db_path, database_name='trades.db')
    inserted = 0
    try:
        for _, row in df.iterrows():
            ts_val = row.get('timestamp')
            if hasattr(ts_val, 'strftime'):
                ts_str = ts_val.strftime('%Y-%m-%d %H:%M:%S')
            else:
                ts_str = str(ts_val) if ts_val else ''

            rd = {
                'uuid':      uuid.uuid4().hex,
                'timestamp': ts_str,
                'action':    str(row.get('action', '')),
                'ticker':    str(row.get('ticker', row.get('isin', ''))),
                'isin':      str(row.get('isin', '')),
                'longName':  str(row.get('longname', '')),
                'shares':    float(row.get('shares', 0) or 0),
                'price':     float(row.get('price', 0) or 0),
                'value':     abs(float(row.get('amount', 0) or 0)),
                'fee':       float(row.get('fee', 0) or 0),
                'tax':       float(row.get('tax', 0) or 0),
                'currency':  str(row.get('currency', 'EUR')),
                'broker':    'ScalableCapital',
            }
            # Clean None/nan strings
            for k, v in rd.items():
                if isinstance(v, str) and v.lower() in ('nan', 'none', ''):
                    rd[k] = None if k not in ('uuid', 'action', 'currency', 'broker') else rd[k]

            try:
                dbt.ensure_table_and_columns(
                    keys=list(rd.keys()), row_dict=rd, database_name='trades'
                )
                dbt.insert_data(
                    keys=list(rd.keys()), row_dict=rd,
                    database_name='trades', replace=False,
                )
                inserted += 1
            except Exception as e:
                logger.warning(f'Scalable import: row skipped: {e}')

        dbt.conn.commit()
    finally:
        try:
            dbt.close()
        except Exception:
            pass
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_scalable_csv(df_raw: pd.DataFrame) -> dict:
    """
    Parse a raw Scalable Capital CSV DataFrame into categorised sub-frames.

    Returns a dict with keys:
      trades      – Buy/Sell rows mapped to the internal trades schema
      dividends   – Distribution rows
      interest    – Interest rows
      fees        – Fee rows
      transfers   – Cash Transfer rows
      skipped     – Cancelled / unrecognised rows
    """
    df = df_raw.copy()
    df.columns = [c.strip() for c in df.columns]

    # Normalise column names to lowercase for reliable access
    col_lower = {c.lower(): c for c in df.columns}

    def _col(name: str) -> pd.Series:
        orig = col_lower.get(name.lower())
        return df[orig] if orig else pd.Series([''] * len(df), index=df.index)

    # ── Filter: keep only Executed rows ───────────────────────────────────
    status = _col('status').astype(str).str.strip().str.lower()
    df = df[status == 'executed'].copy()
    skipped_count = len(df_raw) - len(df)

    if df.empty:
        empty = pd.DataFrame()
        return dict(trades=empty, dividends=empty, interest=empty,
                    fees=empty, transfers=empty, skipped=skipped_count)

    # Re-derive col_lower after filter
    col_lower = {c.lower(): c for c in df.columns}

    def _s(name):
        orig = col_lower.get(name.lower())
        return df[orig].copy() if orig else pd.Series([''] * len(df), index=df.index)

    # ── timestamp ─────────────────────────────────────────────────────────
    date_s = _s('date').astype(str).str.strip()
    time_s = _s('time').astype(str).str.strip()
    combined = date_s + ' ' + time_s
    # Try strict format first, fall back to dayfirst inference
    df['_timestamp'] = pd.to_datetime(
        combined, format='%d.%m.%Y %H:%M:%S', errors='coerce'
    )
    still_nat = df['_timestamp'].isna()
    if still_nat.any():
        df.loc[still_nat, '_timestamp'] = pd.to_datetime(
            combined[still_nat], dayfirst=True, errors='coerce'
        )

    # ── numeric columns ───────────────────────────────────────────────────
    for src, dst in [('shares', '_shares'), ('price', '_price'),
                     ('amount', '_amount'), ('fee', '_fee'), ('tax', '_tax')]:
        df[dst] = _s(src).apply(_parse_de_number)

    # ── string columns ────────────────────────────────────────────────────
    df['_type']     = _s('type').astype(str).str.strip().str.lower()
    df['_action']   = df['_type'].map(_SCALABLE_ACTION_MAP).fillna('other')
    df['_isin']     = _s('isin').astype(str).str.strip().str.upper().replace('NAN', '')
    df['_longname'] = _s('description').astype(str).str.strip()
    df['_currency'] = _s('currency').astype(str).str.strip().str.upper()
    df['_ref']      = _s('reference').astype(str).str.strip()

    # ── split by category ─────────────────────────────────────────────────
    is_trade    = df['_action'].isin(['buy', 'sell'])
    is_dividend = df['_action'] == 'dividend'
    is_interest = df['_action'] == 'interest'
    is_fee      = df['_action'] == 'fee'
    is_transfer = df['_action'].isin(['transfer_out', 'transfer_in'])

    def _make_display(mask, extra_cols=None):
        sub = df[mask].copy()
        cols = ['_timestamp', '_action', '_isin', '_longname',
                '_shares', '_price', '_amount', '_fee', '_tax', '_currency', '_ref']
        if extra_cols:
            cols += extra_cols
        sub = sub[[c for c in cols if c in sub.columns]]
        sub.columns = [c.lstrip('_') for c in sub.columns]
        return sub.reset_index(drop=True)

    trades_raw    = _make_display(is_trade)
    dividends_raw = _make_display(is_dividend)
    interest_raw  = _make_display(is_interest)
    fees_raw      = _make_display(is_fee)
    transfers_raw = _make_display(is_transfer)

    return dict(
        trades=trades_raw,
        dividends=dividends_raw,
        interest=interest_raw,
        fees=fees_raw,
        transfers=transfers_raw,
        skipped=skipped_count,
        raw_executed=df,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Render-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def render_scalable_import(region=st, db_path: str = 'database', system_currency: str = 'EUR'):
    """
    Full Scalable Capital CSV import UI.

    Parses, resolves tickers, shows categorised preview tabs,
    and writes Buy/Sell (+ optionally Dividends) into trades.db.
    """
    r = region
    r.markdown('### Scalable Capital — CSV Import')
    r.caption(
        'Export: Scalable Capital App → Konto → Transaktionen → CSV exportieren. '
        'Nur Zeilen mit Status **Executed** werden verarbeitet.'
    )

    uploaded = r.file_uploader(
        'Scalable Capital CSV hochladen',
        type=['csv'],
        key='scalable_upload',
    )
    if uploaded is None:
        return

    # ── Parse ─────────────────────────────────────────────────────────────
    try:
        df_raw = pd.read_csv(
            uploaded,
            sep=';',
            dtype=str,
            encoding='utf-8-sig',   # handles UTF-8 BOM from European apps
            skip_blank_lines=True,
        ).dropna(how='all')
    except Exception as e:
        r.error(f'CSV konnte nicht gelesen werden: {e}')
        return

    if not _is_scalable_csv(df_raw):
        r.error(
            'Die Datei sieht nicht wie ein Scalable Capital Export aus. '
            f'Gefundene Spalten: {list(df_raw.columns)}'
        )
        return

    parsed = parse_scalable_csv(df_raw)
    trades_df    = parsed['trades']
    dividends_df = parsed['dividends']
    interest_df  = parsed['interest']
    fees_df      = parsed['fees']
    transfers_df = parsed['transfers']
    skipped      = parsed['skipped']

    total_executed = sum(
        len(v) for k, v in parsed.items()
        if isinstance(v, pd.DataFrame)
    )
    r.success(
        f'**{total_executed}** ausgeführte Transaktionen gefunden '
        f'({skipped} Cancelled-Einträge ignoriert).'
    )

    # ── Ticker-Auflösung ──────────────────────────────────────────────────
    resolve_now = r.checkbox(
        'ISIN → Ticker auflösen (benötigt Internet, dauert einen Moment)',
        value=True,
        key='scalable_resolve_tickers',
    )
    status_ph = r.empty()
    if resolve_now:
        with r.spinner('Ticker werden aufgelöst …'):
            trades_df    = _resolve_tickers_for_df(trades_df,    db_path, status_ph)
            dividends_df = _resolve_tickers_for_df(dividends_df, db_path, status_ph)
            interest_df  = _resolve_tickers_for_df(interest_df,  db_path, status_ph)
    else:
        for df_ in [trades_df, dividends_df, interest_df]:
            if not df_.empty and 'isin' in df_.columns:
                df_['ticker'] = df_['isin']

    # ── Preview tabs ──────────────────────────────────────────────────────
    tab_t, tab_d, tab_i, tab_f, tab_tf = r.tabs([
        f'Trades ({len(trades_df)})',
        f'Dividenden ({len(dividends_df)})',
        f'Zinsen ({len(interest_df)})',
        f'Gebühren ({len(fees_df)})',
        f'Transfers ({len(transfers_df)})',
    ])

    _NUM_FMT = dict(
        shares   = st.column_config.NumberColumn('Stück',    format='%.4f'),
        price    = st.column_config.NumberColumn('Kurs',     format='%.4f'),
        amount   = st.column_config.NumberColumn(f'Betrag ({system_currency})', format='%.2f'),
        fee      = st.column_config.NumberColumn('Gebühr',   format='%.2f'),
        tax      = st.column_config.NumberColumn('Steuer',   format='%.2f'),
        timestamp= st.column_config.DatetimeColumn('Datum/Zeit', format='YYYY-MM-DD HH:mm'),
    )

    with tab_t:
        if trades_df.empty:
            st.info('Keine Trades gefunden.')
        else:
            _show_scalable_df(trades_df, _NUM_FMT)
            # Summary
            buys  = trades_df[trades_df['action'] == 'buy' ]['amount'].apply(abs).sum()
            sells = trades_df[trades_df['action'] == 'sell']['amount'].sum()
            tax_t = trades_df['tax'].sum()
            c1, c2, c3 = st.columns(3)
            c1.metric('Käufe (investiert)', f'{buys:,.2f} {system_currency}')
            c2.metric('Verkäufe (Erlöse)',  f'{sells:,.2f} {system_currency}')
            c3.metric('Steuern (Trades)',   f'{tax_t:,.2f} {system_currency}')

    with tab_d:
        if dividends_df.empty:
            st.info('Keine Dividenden gefunden.')
        else:
            _show_scalable_df(dividends_df, _NUM_FMT)
            total_div = dividends_df['amount'].sum()
            total_tax = dividends_df['tax'].sum()
            c1, c2 = st.columns(2)
            c1.metric('Brutto-Dividenden', f'{total_div:,.2f} {system_currency}')
            c2.metric('Einbehaltene Steuer', f'{total_tax:,.2f} {system_currency}')

    with tab_i:
        if interest_df.empty:
            st.info('Keine Zinsen gefunden.')
        else:
            _show_scalable_df(interest_df, _NUM_FMT)
            st.metric('Zinsen gesamt', f'{interest_df["amount"].sum():,.2f} {system_currency}')

    with tab_f:
        if fees_df.empty:
            st.info('Keine Gebühren gefunden.')
        else:
            _show_scalable_df(fees_df, _NUM_FMT)
            st.metric('Gebühren gesamt', f'{fees_df["amount"].sum():,.2f} {system_currency}')

    with tab_tf:
        if transfers_df.empty:
            st.info('Keine Transfers gefunden.')
        else:
            _show_scalable_df(transfers_df, _NUM_FMT)
            st.metric('Transfers gesamt', f'{transfers_df["amount"].sum():,.2f} {system_currency}')

    # ── Cashflow-Zusammenfassung ──────────────────────────────────────────
    r.markdown('---')
    r.markdown('#### Cashflow-Übersicht')
    inv    = trades_df[trades_df['action'] == 'buy' ]['amount'].apply(abs).sum() if not trades_df.empty else 0
    rev    = trades_df[trades_df['action'] == 'sell']['amount'].sum()              if not trades_df.empty else 0
    div    = dividends_df['amount'].sum()  if not dividends_df.empty else 0
    intr   = interest_df['amount'].sum()   if not interest_df.empty else 0
    fee_s  = abs(fees_df['amount'].sum())  if not fees_df.empty else 0
    tax_s  = sum([
        trades_df['tax'].sum()    if not trades_df.empty    else 0,
        dividends_df['tax'].sum() if not dividends_df.empty else 0,
    ])
    net_cash = rev + div + intr - inv - fee_s - tax_s

    cf1, cf2, cf3, cf4, cf5, cf6 = r.columns(6)
    cf1.metric('Investiert',  f'{inv:,.2f} {system_currency}')
    cf2.metric('Erlöse',      f'{rev:,.2f} {system_currency}')
    cf3.metric('Dividenden',  f'{div:,.2f} {system_currency}')
    cf4.metric('Zinsen',      f'{intr:,.2f} {system_currency}')
    cf5.metric('Gebühren',    f'-{fee_s:,.2f} {system_currency}')
    cf6.metric('Steuern',     f'-{tax_s:,.2f} {system_currency}')
    r.metric('Netto-Cashflow', f'{net_cash:+,.2f} {system_currency}',
             delta=f'{net_cash:+,.2f}')

    # ── Import-Button ─────────────────────────────────────────────────────
    r.markdown('---')
    import_dividends = r.checkbox(
        'Dividenden und Zinsen ebenfalls importieren',
        value=True,
        key='scalable_import_dividends',
    )
    import_fees = r.checkbox(
        'Gebühren ebenfalls importieren',
        value=False,
        key='scalable_import_fees',
    )

    if r.button('In trades.db importieren', type='primary', key='scalable_do_import'):
        frames_to_import = [trades_df]
        if import_dividends:
            frames_to_import += [dividends_df, interest_df]
        if import_fees:
            frames_to_import.append(fees_df)

        all_rows = pd.concat([f for f in frames_to_import if not f.empty], ignore_index=True)

        if all_rows.empty:
            r.warning('Keine Zeilen zum Importieren.')
            return

        inserted = _insert_scalable_rows(all_rows, db_path=db_path)
        r.success(f'{inserted} Zeilen erfolgreich in trades.db importiert.')
