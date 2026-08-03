"""tradinglib/ohlc_repair.py — NULL-Close-Reparatur für die per-Ticker OHLC-DBs.

Problemklasse: In ``yf_<TICKER>.db`` / ``day_data`` kann eine Tageskerze mit
``Open/High/Low/Volume``, aber **``Close = NULL``** landen — typisch die zuletzt
gefetchte (noch nicht geschlossene) Kerze eines Handelstags, die nie mit dem
Settlement-Close überschrieben wurde. Am Folgetag hängt der Fetch eine neue,
komplette Kerze an, repariert die alte aber **nicht** → sie bleibt dauerhaft NULL.

Folge in der App: ``market_map.day_change()`` verlangt die zwei jüngsten
Schlusskurse beide gefüllt; ein NULL-Close direkt vor der aktuellen Kerze liefert
``None`` → in der Treemap zu ``0.0`` aufgefüllt (Kachel „ohne aktuellen Wert").

Reparatur: Der korrekte Schluss steht praktisch immer noch in ``h60_data`` (die
letzte Stundenkerze desselben Tages ≈ Session-Close). Diese Funktionen füllen die
NULL-Closes **nur auffüllend** aus ``h60_data`` — ein bereits vorhandener Close
wird nie überschrieben.

  - ``scan_null_closes()``   → DataFrame der betroffenen Ticker/Bars (+ recoverable).
  - ``repair_null_closes()`` → füllt die NULL-Closes aus h60_data; gibt Report zurück.
  - ``render_repair_ui()``   → Streamlit-Block (Admin ▸ Database).

Jede Änderung wird nach ``data_quality.db`` / ``close_repair_log`` protokolliert
(ticker, date, new_close, source_ts) → auditierbar und trivial rücksetzbar
(alter Wert war NULL).

CLI:
    python -m tradinglib.ohlc_repair /scan
    python -m tradinglib.ohlc_repair /repair            # alle NULL-Closes
    python -m tradinglib.ohlc_repair /repair /date:2026-07-31
    python -m tradinglib.ohlc_repair /repair /dry
"""
import glob
import logging
import os
import sys
from datetime import datetime

import pandas as pd

from tradinglib.tools import Tools, open_db

logger = logging.getLogger(__name__)


def _yf_glob(db_path: str) -> list:
    """Absolute Pfade aller yf_<TICKER>.db unter db_path."""
    base = Tools().get_path(path=db_path, file_name='yf_.db')  # nur um den Ordner aufzulösen
    folder = os.path.dirname(base)
    return sorted(glob.glob(os.path.join(folder, 'yf_*.db')))


def _ticker_from_path(p: str) -> str:
    """yf_SAP.DE.db -> SAP.DE"""
    return os.path.basename(p)[3:-3]


def _has_table(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _null_close_rows(conn, date_prefix: str = '') -> list:
    """day_data-Zeilen mit Close IS NULL (optional auf ein Datum eingeschränkt)."""
    if not _has_table(conn, 'day_data'):
        return []
    sql = "SELECT Date FROM day_data WHERE Close IS NULL"
    params: tuple = ()
    if date_prefix:
        sql += " AND Date LIKE ?"
        params = (f'{date_prefix}%',)
    sql += " ORDER BY Date"
    return [r[0] for r in conn.execute(sql, params).fetchall()]


def _h60_close_for(conn, date_str: str):
    """Letzter (spätester) h60_data-Close desselben Kalendertags, oder None.

    ``date_str`` ist der day_data-Datumswert (z. B. '2026-07-31 00:00:00'); es
    zählt nur der Datumsanteil. Die letzte Stundenkerze des Tages ≈ Session-Close.
    """
    if not _has_table(conn, 'h60_data'):
        return None
    day = str(date_str)[:10]
    row = conn.execute(
        "SELECT Date, Close FROM h60_data "
        "WHERE Date LIKE ? AND Close IS NOT NULL ORDER BY Date DESC LIMIT 1",
        (f'{day}%',),
    ).fetchone()
    return row if row else None


def scan_null_closes(db_path: str = 'database', tickers=None,
                     date_prefix: str = '') -> pd.DataFrame:
    """Scanne alle (oder ``tickers``) yf_-DBs auf NULL-Closes in day_data.

    Rückgabe: DataFrame [ticker, date, recoverable, source_ts, source_close].
    ``recoverable`` = ob sich der Close aus h60_data rekonstruieren lässt.
    ``date_prefix`` schränkt auf ein Datum ein (z. B. '2026-07-31').
    """
    if tickers:
        paths = []
        for tk in tickers:
            p = Tools().get_path(path=db_path, file_name=f'yf_{tk}.db')
            if os.path.exists(p):
                paths.append(p)
    else:
        paths = _yf_glob(db_path)

    out = []
    for p in paths:
        tk = _ticker_from_path(p)
        try:
            with open_db(p, readonly=True) as conn:
                for d in _null_close_rows(conn, date_prefix):
                    src = _h60_close_for(conn, d)
                    out.append({
                        'ticker': tk,
                        'date': str(d)[:10],
                        'recoverable': src is not None,
                        'source_ts': src[0] if src else None,
                        'source_close': src[1] if src else None,
                    })
        except Exception as exc:
            logger.debug('ohlc_repair: scan failed on %s: %s', tk, exc)
    return pd.DataFrame(out, columns=['ticker', 'date', 'recoverable',
                                      'source_ts', 'source_close'])


def _log_repairs(rows: list, db_path: str = 'database',
                 out_db: str = 'data_quality.db') -> None:
    """Reparatur-Protokoll nach data_quality.db / close_repair_log anhängen."""
    if not rows:
        return
    df = pd.DataFrame(rows)
    df['repaired_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        dbf = Tools().get_path(path=db_path, file_name=out_db)
        with open_db(dbf) as conn:
            df.to_sql('close_repair_log', conn, if_exists='append', index=False)
            conn.commit()
    except Exception as exc:
        logger.warning('ohlc_repair: log write failed: %s', exc)


def repair_null_closes(db_path: str = 'database', tickers=None,
                       date_prefix: str = '', dry_run: bool = False) -> dict:
    """Fülle NULL-Closes in day_data aus h60_data (nur auffüllend).

    Ein vorhandener Close wird **nie** überschrieben (WHERE Close IS NULL). Jede
    Änderung wird protokolliert. Rückgabe:
        {'fixed': int, 'unrecoverable': int, 'tickers_touched': int,
         'details': [ {ticker, date, new_close, source_ts}, ... ]}
    """
    if tickers:
        paths = []
        for tk in tickers:
            p = Tools().get_path(path=db_path, file_name=f'yf_{tk}.db')
            if os.path.exists(p):
                paths.append(p)
    else:
        paths = _yf_glob(db_path)

    fixed = 0
    unrec = 0
    touched = set()
    details = []

    for p in paths:
        tk = _ticker_from_path(p)
        try:
            # readonly-Scan zuerst, um Schreib-Locks kurz zu halten
            with open_db(p, readonly=True) as rconn:
                null_dates = _null_close_rows(rconn, date_prefix)
                repairs = []
                for d in null_dates:
                    src = _h60_close_for(rconn, d)
                    if src is None:
                        unrec += 1
                    else:
                        repairs.append((d, float(src[1]), src[0]))
            if not repairs:
                continue
            if not dry_run:
                with open_db(p) as wconn:
                    for d, close, _src_ts in repairs:
                        # Doppelt abgesichert: nur füllen, nie überschreiben.
                        wconn.execute(
                            "UPDATE day_data SET Close=? WHERE Date=? AND Close IS NULL",
                            (close, d),
                        )
                    wconn.commit()
            touched.add(tk)
            for d, close, src_ts in repairs:
                fixed += 1
                details.append({'ticker': tk, 'date': str(d)[:10],
                                'new_close': close, 'source_ts': src_ts})
        except Exception as exc:
            logger.warning('ohlc_repair: repair failed on %s: %s', tk, exc)

    if not dry_run:
        _log_repairs(details, db_path=db_path)

    return {'fixed': fixed, 'unrecoverable': unrec,
            'tickers_touched': len(touched), 'details': details}


def render_repair_ui(db_path: str = 'database', region=None) -> None:
    """Streamlit-Block für Admin ▸ Database: NULL-Close scannen & reparieren."""
    if region is None:
        import streamlit as st
        region = st
    else:
        import streamlit as st  # für Widgets

    region.caption(
        "Füllt fehlende Tages-Schlusskurse (Close = NULL in day_data) aus der "
        "letzten Stundenkerze (h60_data) desselben Tages. Reine Auffüllung — "
        "vorhandene Kurse werden nie überschrieben. Behebt Kacheln ohne aktuellen "
        "Tageswert (0,0) in der Market Map."
    )

    default_date = st.session_state.get('_close_repair_date', '')
    date_prefix = st.text_input(
        "Nur dieses Datum (YYYY-MM-DD, leer = alle NULL-Closes)",
        value=default_date, key='close_repair_date',
        placeholder='2026-07-31',
    ).strip()

    c_scan, c_fix = st.columns(2)

    if c_scan.button("🔎 NULL-Closes scannen", key='close_repair_scan'):
        with st.spinner("Scanne yf_*.db …"):
            df = scan_null_closes(db_path=db_path, date_prefix=date_prefix)
        st.session_state['_close_repair_scan_df'] = df

    df = st.session_state.get('_close_repair_scan_df')
    if df is not None:
        if df.empty:
            st.success("Keine NULL-Closes gefunden. ✅")
        else:
            rec = int(df['recoverable'].sum())
            st.warning(
                f"{df['ticker'].nunique()} Ticker · {len(df)} Bars mit NULL-Close "
                f"— davon {rec} aus h60_data reparierbar, {len(df) - rec} nicht."
            )
            st.dataframe(df, use_container_width=True, hide_index=True)

    if c_fix.button("🔧 Reparieren (aus h60)", key='close_repair_fix', type='primary'):
        with st.spinner("Repariere day_data-Closes …"):
            res = repair_null_closes(db_path=db_path, date_prefix=date_prefix)
        st.success(
            f"Fertig: {res['fixed']} Bars in {res['tickers_touched']} Tickern gefüllt. "
            + (f"{res['unrecoverable']} nicht reparierbar (keine h60-Kerze)."
               if res['unrecoverable'] else "")
        )
        st.session_state.pop('_close_repair_scan_df', None)


def _parse_args(argv):
    opts = {'scan': False, 'repair': False, 'dry': False, 'date': ''}
    for a in argv:
        s = a.lstrip('/')
        key = s.split(':', 1)[0].lower()
        if key in ('scan', 'repair', 'dry'):
            opts[key] = True
        elif key == 'date':
            opts['date'] = a.split(':', 1)[1] if ':' in a else ''
    return opts


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    opts = _parse_args(sys.argv[1:])
    if not opts['scan'] and not opts['repair']:
        opts['scan'] = True  # Default: nur berichten

    if opts['scan']:
        df = scan_null_closes(date_prefix=opts['date'])
        if df.empty:
            print('Keine NULL-Closes gefunden. ✅')
        else:
            rec = int(df['recoverable'].sum())
            print(f"{df['ticker'].nunique()} Ticker, {len(df)} Bars mit NULL-Close "
                  f"({rec} reparierbar, {len(df) - rec} nicht).")
            by_date = df.groupby('date').size().sort_index(ascending=False)
            print('\nNULL-Closes je Datum:')
            print(by_date.to_string())

    if opts['repair']:
        res = repair_null_closes(date_prefix=opts['date'], dry_run=opts['dry'])
        tag = ' (DRY-RUN, nichts geschrieben)' if opts['dry'] else ''
        print(f"\nRepariert{tag}: {res['fixed']} Bars in {res['tickers_touched']} "
              f"Tickern, {res['unrecoverable']} nicht reparierbar.")
