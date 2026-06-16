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
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import streamlit as st

from tradinglib import tools
from tradinglib import tiny_chart as tc
from tradinglib import system_config as sysconf
from tradinglib.portfolio_analysis import _fetch_close_series_for_portfolio

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


def _write_isin_ticker(tickers_db: str, symbol: str, isin: str) -> None:
    """Persist an ISIN→ticker mapping in yf_tickers.db/stocks for future local lookups."""
    import sqlite3 as _sq
    try:
        with _sq.connect(tickers_db) as conn:
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
            existing = conn.execute(
                "SELECT id FROM stocks WHERE Ticker=? LIMIT 1", (symbol,)
            ).fetchone()
            if existing:
                conn.execute("UPDATE stocks SET ISIN=? WHERE id=?", (isin, existing[0]))
            else:
                conn.execute(
                    "INSERT INTO stocks (Ticker, ISIN, Date) VALUES (?, ?, ?)",
                    (symbol, isin, dt.datetime.now().strftime('%Y-%m-%d')),
                )
    except Exception as e:
        logger.debug(f'Could not write ISIN→ticker to yf_tickers.db: {e}')


def _resolve_isin_to_ticker(isin: str, db_path: str = 'database') -> str:
    """
    Look up Yahoo Finance ticker for an ISIN.

    Resolution order:
      1.  yf_tickers.db → stocks table  (primary local source; ISIN column)
      1b. FMP ISIN search               (network; better for EU ISINs than Yahoo)
      2.  yfinance: yf.Ticker(isin).info['symbol']  (network fallback)
    Resolved tickers (1b/2) are written back to yf_tickers.db for future lookups.

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
            row = conn.execute(
                "SELECT Ticker FROM stocks WHERE UPPER(ISIN)=? AND Ticker IS NOT NULL LIMIT 1",
                (isin,),
            ).fetchone()
        if row and row[0]:
            return str(row[0]).strip().upper()
    except Exception:
        pass

    # ── 1b. FMP ISIN search (only when an FMP key is configured) ───────────
    try:
        from tradinglib.providers import get_fmp_provider
        fmp = get_fmp_provider(db_path)
        if fmp is not None:
            symbol = (fmp.search_isin(isin) or '').strip().upper()
            if symbol and symbol != isin:
                _write_isin_ticker(tickers_db, symbol, isin)
                return symbol
    except Exception as e:
        logger.debug(f'FMP ISIN lookup failed for {isin}: {e}')

    # ── 2. yfinance network lookup ─────────────────────────────────────────
    try:
        import yfinance as yf
        info = yf.Ticker(isin).info
        symbol = (info.get('symbol') or '').strip().upper()
        if symbol and symbol != isin:
            _write_isin_ticker(tickers_db, symbol, isin)
            return symbol
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


def _show_scalable_trades_df(df: pd.DataFrame, col_cfg: dict, key: str):
    """Like _show_scalable_df, but with single-row selection enabled. Returns the selection event."""
    disp_cols = [c for c in ['timestamp', 'action', 'ticker', 'isin', 'longname',
                              'shares', 'price', 'amount', 'fee', 'tax', 'currency']
                 if c in df.columns]
    return st.dataframe(
        df[disp_cols],
        hide_index=True,
        use_container_width=True,
        column_config={k: v for k, v in col_cfg.items() if k in disp_cols},
        on_select='rerun',
        selection_mode='single-row',
        key=key,
    )


def _compute_open_positions(trades_df: pd.DataFrame, db_path: str = 'database') -> pd.DataFrame:
    """
    Aggregate open positions (net shares > 0) from a parsed Scalable trades sub-frame,
    enriched with current price and performance since purchase. FIFO-sorted by first buy date.
    """
    if trades_df.empty or 'ticker' not in trades_df.columns:
        return pd.DataFrame()

    df = trades_df.copy()
    df['ticker'] = df['ticker'].astype(str).str.upper().str.strip()

    buys  = df[df['action'] == 'buy']
    sells = df[df['action'] == 'sell']
    if buys.empty:
        return pd.DataFrame()

    buy_agg = buys.groupby('ticker', as_index=False).agg(
        longname   = ('longname', 'first'),
        currency   = ('currency', 'first'),
        buy_shares = ('shares', 'sum'),
        buy_value  = ('amount', lambda x: x.abs().sum()),
        first_buy  = ('timestamp', 'min'),
    )
    sell_agg = sells.groupby('ticker', as_index=False)['shares'].sum().rename(columns={'shares': 'sold_shares'})

    pos = buy_agg.merge(sell_agg, on='ticker', how='left')
    pos['sold_shares'] = pos['sold_shares'].fillna(0)
    pos['shares'] = pos['buy_shares'] - pos['sold_shares']
    pos = pos[pos['shares'] > 0.0001].reset_index(drop=True)
    if pos.empty:
        return pos

    # avg_price = Gesamtkaufwert / Gesamtkaufstückzahl (NICHT durch Netto-Restbestand teilen)
    pos['avg_price'] = np.where(pos['buy_shares'] > 0, pos['buy_value'] / pos['buy_shares'], 0.0)
    pos = pos.drop(columns=['sold_shares', 'buy_shares'])

    tickers = pos['ticker'].tolist()
    with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as ex:
        futs = {ex.submit(_fetch_close_series_for_portfolio, t, db_path): t for t in tickers}
        prices = {}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                s = fut.result()
                prices[t] = float(s.iloc[-1]) if s is not None and not s.empty else 0.0
            except Exception:
                prices[t] = 0.0

    pos['current_price']  = pos['ticker'].map(prices).fillna(0.0)
    pos['current_value']  = pos['shares'] * pos['current_price']
    # Unrealized P&L nur auf den offenen Restbestand (shares * avg_price als Einstandswert)
    pos['cost_basis']     = pos['shares'] * pos['avg_price']
    pos['unrealized_pnl'] = pos['current_value'] - pos['cost_basis']
    pos['pnl_pct'] = np.where(pos['avg_price'] > 0, (pos['current_price'] / pos['avg_price'] - 1) * 100, 0.0)
    total_value = pos['current_value'].sum()
    pos['weight_pct'] = np.where(total_value > 0, pos['current_value'] / total_value * 100, 0.0)

    # FIFO-Reihenfolge: älteste Position zuerst
    return pos.sort_values('first_buy', ascending=True).reset_index(drop=True)


@st.dialog('Chart', width='large')
def _overlay_price_chart(ticker: str, longname: str, lines: list, purchase_price: float = 0, username: str = ''):
    """Show a tiny_chart overlay for a selected trade/position, with buy/sell price lines."""
    sys_conf = sysconf.SystemConfig(username=username)
    (interval, period, overlays, oszilators) = sys_conf.get_selectors()
    with st.spinner('Lade Chart …'):
        t_chart = tc.tiny_chart(
            symbol=ticker,
            longname=longname or ticker,
            interval=interval,
            period=period,
            add_overlays=overlays,
            add_sub_plots=oszilators,
            range_breaks=True,
            purchase_price=purchase_price,
            username=username,
        )
    if t_chart.fig:
        for price, text, color in lines:
            if price and price > 0:
                t_chart._add_hline_outside(y=price, text=text, line_color=color, line_dash='dash')
        st.plotly_chart(t_chart.fig, use_container_width=True)
    else:
        st.warning(f'Keine Chart-Daten verfügbar für {ticker}.')


def _migrate_trades_pk(dbt) -> bool:
    """
    Entferne einen problematischen `timestamp`-PRIMARY-KEY aus der trades-Tabelle.

    Beim Rebalancing teilen sich viele Trades exakt denselben Timestamp
    (z. B. mehrere Orders um 18:54:44). Ist `timestamp` der Primärschlüssel,
    verwirft `INSERT` die kollidierenden Zeilen stillschweigend — aus 27 Zeilen
    überleben dann nur die mit eindeutigem Timestamp. Diese Migration baut die
    Tabelle ohne expliziten PK (rowid) neu auf, sodass jede Zeile eindeutig ist.
    Gibt True zurück, wenn migriert wurde.
    """
    cur = dbt.cursor
    cols = cur.execute("PRAGMA table_info('trades')").fetchall()
    if not cols:
        return False  # Tabelle existiert noch nicht
    pk_cols = [c[1] for c in cols if c[5] > 0]  # c[5] = pk-Position (0 = kein PK)
    if pk_cols != ['timestamp']:
        return False  # kein problematischer timestamp-PK
    col_names = [c[1] for c in cols]
    col_defs  = ', '.join(f'"{c[1]}" {c[2] or "TEXT"}' for c in cols)
    col_list  = ', '.join(f'"{n}"' for n in col_names)
    cur.execute(f'CREATE TABLE trades_new ({col_defs})')
    cur.execute(f'INSERT INTO trades_new ({col_list}) SELECT {col_list} FROM trades')
    cur.execute('DROP TABLE trades')
    cur.execute('ALTER TABLE trades_new RENAME TO trades')
    dbt.conn.commit()
    logger.info('trades-Tabelle migriert: problematischer timestamp-PRIMARY-KEY entfernt.')
    return True


def _insert_scalable_rows(df: pd.DataFrame, db_path: str = 'database') -> int:
    """Insert parsed Scalable Capital rows into trades.db. Returns inserted count."""
    dbt = tools.Db_tools(db_path=db_path, database_name='trades.db')
    inserted = 0
    try:
        # Selbstheilung: alten timestamp-PK entfernen, sonst gehen Zeilen mit
        # identischem Timestamp (Rebalancing) beim Insert verloren.
        try:
            _migrate_trades_pk(dbt)
        except Exception as e:
            logger.warning(f'Scalable import: trades-PK-Migration fehlgeschlagen: {e}')

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
                # primary_key=False: frische trades-DBs ohne expliziten PK anlegen
                # (rowid) — verhindert denselben Zeilenverlust wie der alte timestamp-PK.
                dbt.ensure_table_and_columns(
                    keys=list(rd.keys()), row_dict=rd, database_name='trades',
                    primary_key=False,
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
    # Fuzzy-Fallback: Typen die 'sell'/'buy' enthalten aber nicht exakt gemappt wurden
    _unmatched = df['_action'] == 'other'
    if _unmatched.any():
        df.loc[_unmatched & df['_type'].str.contains('sell', case=False, na=False), '_action'] = 'sell'
        df.loc[_unmatched & df['_type'].str.contains('buy',  case=False, na=False), '_action'] = 'buy'
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
    is_unknown  = ~(is_trade | is_dividend | is_interest | is_fee | is_transfer)

    def _make_display(mask, extra_cols=None):
        sub = df[mask].copy()
        cols = ['_timestamp', '_action', '_isin', '_longname',
                '_shares', '_price', '_amount', '_fee', '_tax', '_currency', '_ref']
        if extra_cols:
            cols += extra_cols
        sub = sub[[c for c in cols if c in sub.columns]]
        sub.columns = [c.lstrip('_') for c in sub.columns]
        return sub.reset_index(drop=True)

    def _make_unknown_display(mask):
        sub = df[mask].copy()
        # Include the raw type so the user can see what wasn't recognized
        cols = ['_timestamp', '_type', '_isin', '_longname', '_amount', '_currency', '_ref']
        sub = sub[[c for c in cols if c in sub.columns]]
        sub.columns = [c.lstrip('_') for c in sub.columns]
        return sub.reset_index(drop=True)

    trades_raw    = _make_display(is_trade)
    dividends_raw = _make_display(is_dividend)
    interest_raw  = _make_display(is_interest)
    fees_raw      = _make_display(is_fee)
    transfers_raw = _make_display(is_transfer)
    unknown_raw   = _make_unknown_display(is_unknown)

    return dict(
        trades=trades_raw,
        dividends=dividends_raw,
        interest=interest_raw,
        fees=fees_raw,
        transfers=transfers_raw,
        unknown=unknown_raw,
        skipped=skipped_count,
        raw_executed=df,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Render-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def render_scalable_import(region=st, db_path: str = 'database', system_currency: str = 'EUR', username: str = ''):
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
    unknown_df   = parsed.get('unknown', pd.DataFrame())
    skipped      = parsed['skipped']

    total_executed = sum(
        len(v) for k, v in parsed.items()
        if k != 'unknown' and isinstance(v, pd.DataFrame)
    )
    r.success(
        f'**{total_executed}** ausgeführte Transaktionen gefunden '
        f'({skipped} Cancelled-Einträge ignoriert).'
    )
    if not unknown_df.empty:
        with r.expander(
            f'⚠️ {len(unknown_df)} Zeile(n) mit unbekanntem Typ – werden nicht importiert',
            expanded=True,
        ):
            r.caption(
                'Diese Zeilen haben einen Typ, der keiner bekannten Kategorie '
                '(Kauf, Verkauf, Dividende, Zinsen, Gebühr, Transfer) zugeordnet '
                'werden konnte. Prüfe den "type"-Wert und ergänze ggf. den Parser.'
            )
            st.dataframe(unknown_df, hide_index=True, use_container_width=True)

    # ── Ticker-Auflösung ──────────────────────────────────────────────────
    # In der Scalable-Edition ist die Auflösung verpflichtend: die ISIN liegt
    # immer vor, und nur mit echtem Ticker sind die Positionen in trades.db
    # später chart-/analysefähig. Die Checkbox entfällt dort.
    try:
        from tradinglib import app_edition as _appedition
        _force_resolve = _appedition.IS_SCALABLE
    except Exception:
        _force_resolve = False

    if _force_resolve:
        resolve_now = True
        r.caption('ISINs werden automatisch in Börsenticker aufgelöst.')
    else:
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

    # ── Offene Positionen (mit aktuellem Kurs) vorab berechnen ─────────────
    with r.spinner('Lade aktuelle Kurse für offene Positionen …'):
        open_pos_df = _compute_open_positions(trades_df, db_path)

    # ── Preview tabs ──────────────────────────────────────────────────────
    tab_op, tab_t, tab_d, tab_i, tab_f, tab_tf = r.tabs([
        f'Offene Positionen ({len(open_pos_df)})',
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

    with tab_op:
        if open_pos_df.empty:
            st.info('Keine offenen Positionen gefunden.')
        else:
            _OPEN_POS_FMT = dict(
                first_buy      = st.column_config.DatetimeColumn('Kauf ab', format='YYYY-MM-DD'),
                ticker         = st.column_config.TextColumn('Ticker'),
                longname       = st.column_config.TextColumn('Name'),
                shares         = st.column_config.NumberColumn('Stück', format='%.4f'),
                avg_price      = st.column_config.NumberColumn('Ø Kaufkurs', format='%.4f'),
                current_price  = st.column_config.NumberColumn('Aktueller Kurs', format='%.4f'),
                pnl_pct        = st.column_config.NumberColumn('Perf. %', format='%+.2f'),
                cost_basis     = st.column_config.NumberColumn(f'Einstand ({system_currency})', format='%.2f'),
                current_value  = st.column_config.NumberColumn(f'Marktwert ({system_currency})', format='%.2f'),
                unrealized_pnl = st.column_config.NumberColumn(f'G/V ({system_currency})', format='%+.2f'),
                weight_pct     = st.column_config.NumberColumn('Gewicht %', format='%.1f'),
            )
            disp_cols_op = ['first_buy', 'ticker', 'longname', 'shares', 'avg_price', 'current_price',
                            'pnl_pct', 'cost_basis', 'current_value', 'unrealized_pnl', 'weight_pct']
            event = st.dataframe(
                open_pos_df[disp_cols_op],
                hide_index=True,
                use_container_width=True,
                column_config=_OPEN_POS_FMT,
                on_select='rerun',
                selection_mode='single-row',
                key='scalable_open_pos_table',
            )
            if event.selection.rows:
                sel = open_pos_df.iloc[event.selection.rows[0]]
                if st.session_state.get('scalable_open_pos_last_shown') != sel['ticker']:
                    st.session_state['scalable_open_pos_last_shown'] = sel['ticker']
                    _overlay_price_chart(
                        ticker=sel['ticker'],
                        longname=sel.get('longname') or sel['ticker'],
                        lines=[(sel['avg_price'], f"Ø Kauf: {round(sel['avg_price'], 2)}", 'blue')],
                        purchase_price=sel['avg_price'],
                        username=username,
                    )
            else:
                st.session_state.pop('scalable_open_pos_last_shown', None)

            total_value = open_pos_df['current_value'].sum()
            total_cost  = open_pos_df['cost_basis'].sum()
            total_pnl   = open_pos_df['unrealized_pnl'].sum()
            total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0
            c1, c2, c3 = st.columns(3)
            c1.metric('Marktwert',     f'{total_value:,.2f} {system_currency}')
            c2.metric('Einstand',      f'{total_cost:,.2f} {system_currency}')
            c3.metric('Unreal. G/V',   f'{total_pnl:+,.2f} {system_currency}', delta=f'{total_pnl_pct:+.1f}%')

    with tab_t:
        if trades_df.empty:
            st.info('Keine Trades gefunden.')
        else:
            event = _show_scalable_trades_df(trades_df, _NUM_FMT, key='scalable_trades_table')
            if event.selection.rows:
                sel = trades_df.iloc[event.selection.rows[0]]
                ts = sel.get('timestamp')
                ts_label = ts.strftime('%Y-%m-%d') if hasattr(ts, 'strftime') else str(ts)
                sel_key = f"{sel['ticker']}_{ts_label}_{sel['action']}"
                if st.session_state.get('scalable_trade_last_shown') != sel_key:
                    st.session_state['scalable_trade_last_shown'] = sel_key
                    is_buy = sel['action'] == 'buy'
                    action_label = 'Kauf' if is_buy else 'Verkauf'
                    color = 'darkcyan' if is_buy else 'darkred'
                    _overlay_price_chart(
                        ticker=sel['ticker'],
                        longname=sel.get('longname') or sel['ticker'],
                        lines=[(sel['price'], f"{action_label}: {round(sel['price'], 2)} ({ts_label})", color)],
                        purchase_price=sel['price'] if is_buy else 0,
                        username=username,
                    )
            else:
                st.session_state.pop('scalable_trade_last_shown', None)
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

        # ── On-Demand: Kurs- und Stammdaten für die importierten Ticker nachladen ──
        # Nur in der Scalable-Edition: dort gibt es keine vorbefüllte Bulk-Pipeline,
        # also werden genau die hochgeladenen Titel bei Bedarf nachgeladen.
        try:
            from tradinglib import app_edition as _appedition
            if _appedition.IS_SCALABLE:
                # Scalable liefert die ISIN immer mit → (ticker, isin)-Paare übergeben,
                # damit der Loader auch ohne vorab aufgelösten Ticker laden kann.
                cols = [c for c in ('ticker', 'isin') if c in all_rows.columns]
                assets = (all_rows[cols].dropna(how='all').drop_duplicates()
                          .to_dict('records')) if cols else []
                if assets:
                    from tradinglib.on_demand_loader import ensure_assets_loaded
                    prog = r.progress(0.0, text='Lade Kurs- und Stammdaten …')

                    def _cb(done, total, label):
                        frac = (done / total) if total else 1.0
                        try:
                            prog.progress(min(frac, 1.0),
                                          text=f'Lade {label or "…"} ({done}/{total})')
                        except Exception:
                            pass

                    stats = ensure_assets_loaded(assets, db_path=db_path, progress=_cb)
                    try:
                        prog.empty()
                    except Exception:
                        pass
                    r.info(
                        f"Nachgeladen: {stats['ohlc_loaded']} Kursreihen, "
                        f"{stats['info_loaded']} Stammdatensätze "
                        f"({stats['tickers']} Ticker geprüft"
                        + (f", {stats['resolved_from_isin']} per ISIN aufgelöst"
                           if stats['resolved_from_isin'] else "")
                        + (f", {stats['unresolved']} nicht auflösbar"
                           if stats['unresolved'] else "")
                        + ")."
                    )
        except Exception as _e:
            logger.warning("On-demand asset load after Scalable import failed: %s", _e)

    # ── Danger Zone: Trades löschen ─────────────────────────────────────────
    r.markdown('---')
    with r.expander('⚠️ Trades löschen (z. B. doppelte Importe entfernen)'):
        r.caption(
            'Jeder Import fügt neue Zeilen hinzu, ohne auf bereits vorhandene Trades zu prüfen. '
            'Wird dieselbe CSV mehrfach importiert, entstehen dadurch Duplikate in trades.db. '
            'Hier können betroffene Zeilen wieder entfernt werden. Diese Aktion kann nicht '
            'widerrufen werden.'
        )
        del_c1, del_c2 = r.columns(2)

        with del_c1:
            r.markdown('**Nur ScalableCapital-Importe löschen**')
            confirm_sc = r.text_input(
                'Zum Bestätigen "DELETE" eingeben', key='scalable_delete_confirm_sc',
            )
            if r.button('ScalableCapital-Trades löschen', key='scalable_delete_sc_btn'):
                if confirm_sc == 'DELETE':
                    try:
                        dbt = tools.Db_tools(db_path=db_path, database_name='trades.db')
                        try:
                            dbt.cursor.execute("DELETE FROM trades WHERE broker = 'ScalableCapital'")
                            deleted = dbt.cursor.rowcount
                            dbt.conn.commit()
                            r.success(f'{deleted} ScalableCapital-Trades gelöscht.')
                        finally:
                            dbt.close()
                    except Exception as e:
                        r.error(f'Fehler beim Löschen: {e}')
                else:
                    r.warning('Bitte "DELETE" eingeben, um zu bestätigen.')

        with del_c2:
            r.markdown('**Alle Trades löschen (komplette trades.db)**')
            confirm_all = r.text_input(
                'Zum Bestätigen "DELETE" eingeben', key='scalable_delete_confirm_all',
            )
            if r.button('Alle Trades löschen', key='scalable_delete_all_btn', type='primary'):
                if confirm_all == 'DELETE':
                    try:
                        dbt = tools.Db_tools(db_path=db_path, database_name='trades.db')
                        try:
                            dbt.cursor.execute('DELETE FROM trades')
                            deleted = dbt.cursor.rowcount
                            dbt.conn.commit()
                            r.success(f'{deleted} Trades gelöscht.')
                        finally:
                            dbt.close()
                    except Exception as e:
                        r.error(f'Fehler beim Löschen: {e}')
                else:
                    r.warning('Bitte "DELETE" eingeben, um zu bestätigen.')
