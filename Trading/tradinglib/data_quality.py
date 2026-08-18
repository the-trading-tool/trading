"""tradinglib/data_quality.py — OHLC-Konsistenzprüfung für asset_simulation.

Findet Zeilen, in denen ``close`` ausserhalb ``[Low, High]`` liegt — z. B. ein
unbereinigter Split (``close`` ~6× High, siehe Fujikura 5803.T) oder ein
Pence/Pfund-Mismatch bei ``.L``-Werten (~100×). Solche Bars erzeugen im Backtest
Phantom-Trades: gekauft wird zum korrupten ``close``, der Hard-Stop vergleicht
danach das korrekte ``Low`` → z. B. −83 % Phantom-Verlust.

Das Modul **erkennt und weist aus**, es fixt nichts automatisch:
  - ``scan_ohlc_issues()``  → DataFrame der betroffenen Zeilen (+ likely_cause).
  - ``write_issues_table()`` → persistiert nach ``data_quality.db`` / ``ohlc_issues``.
  - ``build_cleanup_commands()`` → Befehle zum gezielten Neu-Einlesen.
  - ``render_data_quality_warning()`` → Streamlit-Warnblock (Multi Strategies).

WICHTIG: Die Bereinigung (Neu-Fetch) hilft nur bei **Vintage-/Stale**-Fällen
(Split ist passiert, lokale/Sim-Daten sind alt, der Provider liefert *jetzt*
korrekte adjustierte Werte). Ein **echter Provider-Fehler** (Yahoo liefert selbst
falsch, oder LSE-Pence-vs-Pfund) wird durch Neu-Fetch NICHT behoben → dort ist ein
manueller Ticker-Override nötig.

CLI: ``python -m tradinglib.data_quality [/year:YYYY] [/all]``
"""
import logging
import sys
from datetime import datetime

import pandas as pd

from tradinglib.tools import Tools, open_db

logger = logging.getLogger(__name__)

# Nur GRAVIERENDE Inkonsistenzen flaggen: close > 1,5× High oder < 0,67× Low
# (= >50 % daneben). Das trifft Split-/Pence-/Katastrophenfälle (Faktor 2..100),
# die Phantom-Trades verursachen, und ignoriert harmloses 1-Bar-Rauschen
# (z. B. Live-Kerze aus /add_current, ein paar % über High). Identische Schwelle
# nutzt der PortfolioSimulator-Guard (asset_simulator.py).
_TOL_HI = 1.5
_TOL_LO = 0.67


def _sim_db_name(year: str = '', all_flag: bool = False) -> str:
    if all_flag:
        return 'asset_simulation_all.db'
    return f'asset_simulation_{year}.db'


def _likely_cause(ratio: float) -> str:
    """Grobe Ursachen-Heuristik aus dem close/High-Verhältnis."""
    if ratio <= 0:
        return 'unbekannt'
    r = ratio if ratio >= 1 else 1.0 / ratio
    if 90 <= r <= 110:
        return 'Pence/Pfund (.L)?'
    # nahe an ganzer Zahl 2..50 -> Split-Verhältnis
    nearest = round(r)
    if 2 <= nearest <= 50 and abs(r - nearest) <= 0.15:
        return f'Split ~{nearest}:1?'
    return 'unbekannt'


def scan_ohlc_issues(db_path: str = 'database', sim_db: str = 'asset_simulation_.db',
                     tickers=None) -> pd.DataFrame:
    """Zeilen mit close ausserhalb [Low, High] (bei gültigem Low/High).

    Optional auf `tickers` (Liste) beschränkt. Rückgabe leer, wenn DB/Tabelle
    fehlt oder keine Treffer.
    """
    import os
    dbf = Tools().get_path(path=db_path, file_name=sim_db)
    if not os.path.exists(dbf):
        return pd.DataFrame()
    where = (
        "Low > 0 AND High > 0 AND Low <= High AND close > 0 "
        f"AND (close > High * {_TOL_HI} OR close < Low * {_TOL_LO})"
    )
    params = ()
    if tickers:
        ph = ','.join('?' * len(tickers))
        where += f" AND ticker IN ({ph})"
        params = tuple(tickers)
    try:
        with open_db(dbf, readonly=True) as conn:
            df = pd.read_sql_query(
                f"SELECT ticker, Date, Open, High, Low, close FROM asset_simulation "
                f"WHERE {where} ORDER BY ticker, Date",
                conn, params=params,
            )
    except Exception as exc:
        logger.warning('data_quality: scan failed on %s: %s', sim_db, exc)
        return pd.DataFrame()
    if df.empty:
        return df
    df['ratio'] = (df['close'] / df['High']).round(3)
    df['likely_cause'] = df['ratio'].apply(_likely_cause)
    df['sim_db'] = sim_db
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """Ein Eintrag je Ticker: Anzahl betroffener Bars, Median-Verhältnis, Ursache."""
    if df is None or df.empty:
        return pd.DataFrame()
    g = df.groupby('ticker').agg(
        bars=('Date', 'count'),
        median_ratio=('ratio', 'median'),
        first_date=('Date', 'min'),
        last_date=('Date', 'max'),
    ).reset_index()
    g['likely_cause'] = g['median_ratio'].apply(_likely_cause)
    return g.sort_values('bars', ascending=False)


def write_issues_table(df: pd.DataFrame, db_path: str = 'database',
                       out_db: str = 'data_quality.db') -> None:
    """Treffer nach data_quality.db / ohlc_issues schreiben (drop + neu)."""
    if df is None or df.empty:
        return
    dbf = Tools().get_path(path=db_path, file_name=out_db)
    out = df.copy()
    out['scanned_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open_db(dbf) as conn:
            conn.execute('DROP TABLE IF EXISTS ohlc_issues')
            out.to_sql('ohlc_issues', conn, if_exists='replace', index=False)
            conn.commit()
        logger.info('data_quality: %d Zeilen nach %s/ohlc_issues geschrieben',
                    len(out), out_db)
    except Exception as exc:
        logger.warning('data_quality: write_issues_table failed: %s', exc)


# ── Preisreihen-Brüche (Level-Shift in yf_<TICKER>.db) ───────────────────────
# Zweite, unabhängige Fehlerklasse: NICHT eine einzelne inkonsistente Zeile
# (close ausserhalb [Low, High]), sondern ein sauberer Stufensprung der ganzen
# Reihe — alle vier OHLC-Werte springen gemeinsam um denselben Faktor. Typisch
# für einen Split, der nur ab dem Umstellungstag eingepflegt wurde: jede Bar ist
# für sich konsistent, deshalb findet `scan_ohlc_issues` davon nichts.
#
# Folge: alles, was über den Bruch hinweg rechnet, ist falsch — 52W-/Allzeithoch,
# Rekord-Fenster, Zickzack-Schübe, Renditen. Die 4PS-Methode etwa sieht einen
# Kurssturz von -75 % und blockiert den anschliessenden Aufwärtstrend jahrelang,
# weil der (nicht vergleichbare) Vor-Split-Höchststand im Fenster stehen bleibt.
_GAP_LO = 0.6      # Tagesfaktor darunter  = Sprung nach unten
_GAP_HI = 1.7      # Tagesfaktor darüber   = Sprung nach oben

# Gängige Split-Verhältnisse, gegen die der gefundene Faktor geprüft wird
_SPLIT_RATIOS = (2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 30, 50, 100)


def _gap_cause(factor: float) -> str:
    """Vermutliche Ursache eines Stufensprungs anhand des Tagesfaktors."""
    if factor <= 0:
        return 'ungueltig'
    inv = 1.0 / factor
    for n in _SPLIT_RATIOS:
        if abs(inv - n) / n < 0.06:
            return f'Split {n}:1 nicht rueckwirkend bereinigt'
        if abs(factor - n) / n < 0.06:
            return f'Reverse-Split 1:{n} bzw. Level-Shift nach oben'
    if factor < 0.02 or factor > 50:
        return 'Waehrungs-/Pence-Wechsel'
    return 'unklarer Level-Shift'


def detect_price_gaps(daily, since: str = '2015-01-01') -> list:
    """Stufensprünge in einer OHLC-Reihe (DataFrame mit Close, Datumsindex).

    Liefert eine Liste ``{date, prev_close, close, factor, cause}`` — leer, wenn
    die Reihe durchgehend ist. Reine Rechenfunktion, kein DB-Zugriff.
    """
    if daily is None or getattr(daily, 'empty', True) or 'Close' not in daily.columns:
        return []
    close = pd.to_numeric(daily['Close'], errors='coerce').dropna()
    if len(close) < 30:
        return []
    ratio = (close / close.shift(1)).dropna()
    if since:
        ratio = ratio[ratio.index >= pd.Timestamp(since)]
    hits = ratio[(ratio < _GAP_LO) | (ratio > _GAP_HI)]
    out = []
    for dt, val in hits.items():
        pos = close.index.get_loc(dt)
        out.append({'date': dt, 'prev_close': float(close.iloc[pos - 1]),
                    'close': float(close.loc[dt]), 'factor': float(val),
                    'cause': _gap_cause(float(val))})
    return out


def scan_price_gaps(tickers, db_path: str = 'database', since: str = '2015-01-01'):
    """Wie :func:`detect_price_gaps`, aber über viele Ticker aus den lokalen DBs."""
    from tradinglib import four_ps as fps          # lazy: reiner OHLC-Leser
    rows = []
    for tk in tickers or []:
        try:
            daily = fps.load_daily(tk, db_path)
        except Exception:
            continue
        for gap in detect_price_gaps(daily, since):
            rows.append({'ticker': tk, **gap})
    df = pd.DataFrame(rows)
    return df.sort_values(['date', 'ticker'], ascending=[False, True]) if not df.empty else df


def _indices_for_tickers(tickers, db_path: str = 'database') -> dict:
    """Ticker -> Liste zugehöriger Index-Namen (aus yf_tickers.db)."""
    out: dict = {}
    if not tickers:
        return out
    try:
        dbf = Tools().get_path(path=db_path, file_name='yf_tickers.db')
        ph = ','.join('?' * len(tickers))
        with open_db(dbf, readonly=True) as conn:
            rows = conn.execute(
                "SELECT s.Ticker, i.name FROM stocks s "
                "JOIN stock_indices si ON s.id = si.stock_id "
                "JOIN indices i ON si.index_id = i.id "
                f"WHERE s.Ticker IN ({ph})", tuple(tickers)).fetchall()
        for tk, idx in rows:
            out.setdefault(tk, [])
            if idx not in out[tk]:
                out[tk].append(idx)
    except Exception as exc:
        logger.debug('data_quality: index lookup failed: %s', exc)
    return out


def build_cleanup_commands(tickers, db_path: str = 'database') -> list:
    """Konkrete Bereinigungs-Befehle: OHLC neu vom Provider laden, dann Sim neu
    rechnen. Gruppiert die Sim-Neuberechnung je betroffenem Index.
    """
    tickers = sorted(set(t for t in tickers if t))
    if not tickers:
        return []
    idx_map = _indices_for_tickers(tickers, db_path)
    indices = sorted({i for lst in idx_map.values() for i in lst})
    # /tickers statt /select: der Befehl wird oft kopiert und in PowerShell/cmd
    # eingefuegt, wo `/select:'WHERE Ticker IN (...)'` am ersten Leerzeichen
    # zerbricht ("SELECT Ticker FROM stocks 'WHERE;"). /tickers:A,B,C hat weder
    # Leerzeichen noch Anfuehrungszeichen und laeuft in jeder Shell.
    cmds = [
        '# 1) OHLC gezielt neu vom aktiven Provider laden (fixt nur, wenn der Provider korrekt liefert):',
        f'python get_asset_data.py /tickers:{",".join(tickers)} 1d:max',
        '# 2) Simulation der betroffenen Indizes neu rechnen (close wird konsistent):',
    ]
    if indices:
        cmds.append(f'python asset_perf2.py /index:{",".join(indices)}')
    else:
        cmds.append('python asset_perf2.py   # kein Index gefunden -> ggf. /all')
    return cmds


def render_data_quality_warning(tickers, db_path: str = 'database',
                                sim_db: str = 'asset_simulation_.db', region=None) -> bool:
    """Streamlit-Warnblock, wenn unter `tickers` OHLC-Datenfehler stecken.

    Persistiert die Treffer immer in data_quality.db. Rückgabe True, wenn etwas
    gefunden wurde. `region` = Streamlit-Container (default st).
    """
    if region is None:
        import streamlit as st
        region = st
    df = scan_ohlc_issues(db_path=db_path, sim_db=sim_db, tickers=list(tickers) if tickers else None)
    if df.empty:
        return False
    write_issues_table(df, db_path=db_path)
    summ = summarize(df)
    region.warning(
        f"⚠️ {summ['ticker'].nunique()} Ticker mit inkonsistentem OHLC "
        f"(close ausserhalb [Low, High]) — potenzielle Datenfehler. Diese Bars "
        f"werden im Backtest übersprungen; bitte Daten bereinigen."
    )
    region.dataframe(summ, use_container_width=True, hide_index=True)
    cmds = build_cleanup_commands(summ['ticker'].tolist(), db_path=db_path)
    if cmds:
        region.caption("Bereinigungs-Befehle (Neu-Fetch hilft nur bei Vintage-/Stale-Daten, "
                       "nicht bei echten Provider-Fehlern):")
        region.code('\n'.join(cmds), language='bash')
    return True


def scan_position_anomalies(trades_df, budgets: dict, factor: float = 2.0) -> pd.DataFrame:
    """Trades, deren |buyValueEUR| das Index-Budget stark übersteigt.

    Eine einzelne Position kann nie mehr kosten als das Index-Budget (invest).
    Ist ``|buyValueEUR| > factor × invest``, stimmt die Bewertung nicht — meist ein
    **Währungs-/Einheiten-Fehler** (z. B. IHG.L als USD statt GBp/Pence → ~90× zu
    grosse Position). Fängt genau die Fälle, die der OHLC-Check verpasst, weil dort
    Open/High/Low/close untereinander konsistent, nur in der falschen Einheit sind.

    ``budgets`` = ``{strategy: {index: invest_eur}}`` (wie in multi_transaction).
    """
    if trades_df is None or trades_df.empty or not budgets:
        return pd.DataFrame()
    if not {'Strategy', 'stockIndex', 'buyValueEUR'} <= set(trades_df.columns):
        return pd.DataFrame()
    df = trades_df.copy()
    df['index_invest'] = df.apply(
        lambda r: float((budgets.get(r['Strategy'], {}) or {}).get(r['stockIndex'], 0) or 0),
        axis=1)
    df['bve'] = pd.to_numeric(df['buyValueEUR'], errors='coerce').abs()
    hit = df[(df['index_invest'] > 0) & (df['bve'] > df['index_invest'] * factor)].copy()
    if hit.empty:
        return hit
    hit['ratio'] = (hit['bve'] / hit['index_invest']).round(1)
    cols = [c for c in ['ticker', 'stockIndex', 'Strategy', 'currency', 'buyDate',
                        'buyValueEUR', 'index_invest', 'ratio'] if c in hit.columns]
    return hit[cols].sort_values('ratio', ascending=False)


def render_position_anomaly_warning(trades_df, budgets: dict, db_path: str = 'database',
                                    region=None, factor: float = 2.0) -> bool:
    """Streamlit-Warnblock für zu grosse Positionen (Budget-Überschreitung).

    Währungs-AGNOSTISCH: es wird KEINE Annahme über die „richtige" Währung eines
    Suffixes gemacht (Yahoo führt z. B. IHG.L in USD — das ist gültig). Gemeldet
    wird nur die messbare Auffälligkeit: eine Position kostet mehr als das
    Index-Budget hergibt. Rückgabe True bei Treffern.
    """
    if region is None:
        import streamlit as st
        region = st
    pos = scan_position_anomalies(trades_df, budgets, factor=factor)
    if pos.empty:
        return False
    region.warning(
        f"⚠️ {pos['ticker'].nunique()} Position(en) übersteigen ihr Index-Budget um "
        f"das >{factor:g}-fache — das ist rechnerisch unmöglich und verfälscht "
        f"Kapitalbindung UND Gewinn der Strategie."
    )
    region.dataframe(pos, use_container_width=True, hide_index=True)
    region.caption(
        "Mögliche Ursache: der Index mischt Währungen, aber die Simulation rechnet "
        "pro Index mit EINEM Wechselkurs (aus der Währung der ersten Zeile). Ein Wert "
        "in abweichender Währung (z. B. USD in einem sonst GBp/EUR-Index) wird dann "
        "falsch dimensioniert. Bitte Kurs/Währung des Tickers prüfen und die Daten neu "
        "einlesen (get_asset_data.py); ggf. Ticker-Override in config.db "
        "(_app:isin_ticker_overrides). Solange die Position drin ist, ist die "
        "Strategie-Performance verfälscht."
    )
    return True


def _cli_gaps(index_arg: str, since: str) -> None:
    """`/gaps`-Modus: Preisreihen auf Stufensprünge prüfen (yf_<TICKER>.db)."""
    from tradinglib import four_ps as fps
    indices = [i.strip() for i in (index_arg or '^SPX').split(',') if i.strip()]
    tickers = sorted({tk for idx in indices for tk in fps.index_members(idx)})
    logger.info('Scanne %d Ticker aus %s auf Preisreihen-Brueche …',
                len(tickers), ','.join(indices))
    df = scan_price_gaps(tickers, since=since)
    if df.empty:
        logger.info('Keine Bruechen gefunden. ✅')
        return
    view = df.copy()
    view['date'] = view['date'].dt.strftime('%Y-%m-%d')
    view['factor'] = view['factor'].round(3)
    print(f'\n{len(df)} Bruch/Brueche in {df["ticker"].nunique()} Tickern:\n')
    print(view[['ticker', 'date', 'prev_close', 'close', 'factor', 'cause']]
          .to_string(index=False))
    print('\nBereinigungs-Befehle:')
    print('\n'.join(build_cleanup_commands(sorted(df['ticker'].unique()))))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    year = ''
    all_flag = False
    gaps = False
    gap_index = ''
    since = '2015-01-01'
    for a in sys.argv[1:]:
        s = a.lstrip('/').lower()
        if s == 'all':
            all_flag = True
        elif s.startswith('year:'):
            year = a.split(':', 1)[1]
        elif s == 'gaps':
            gaps = True
        elif s.startswith('index:'):
            gap_index = a.split(':', 1)[1]
        elif s.startswith('since:'):
            since = a.split(':', 1)[1]
    if gaps:
        _cli_gaps(gap_index, since)
        sys.exit(0)
    sim = _sim_db_name(year, all_flag)
    logger.info('Scanne %s auf OHLC-Inkonsistenzen …', sim)
    df = scan_ohlc_issues(sim_db=sim)
    if df.empty:
        logger.info('Keine OHLC-Datenfehler gefunden. ✅')
        sys.exit(0)
    summ = summarize(df)
    print(f'\n{len(df)} betroffene Bars, {summ["ticker"].nunique()} Ticker:\n')
    print(summ.to_string(index=False))
    write_issues_table(df)
    print('\nBereinigungs-Befehle:')
    print('\n'.join(build_cleanup_commands(summ['ticker'].tolist())))
