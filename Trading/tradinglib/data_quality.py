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


# Tabellen, die ein Level-Shift betrifft, und ob sie ueblicherweise schon
# bereinigt sind. h60/min werden beim Nachladen von Yahoo frisch und bereits
# angepasst geliefert -- day/month nicht, weil dort nur angehaengt wird.
_OHLC_COLS = ('Open', 'High', 'Low', 'Close')
_ADJUST_TABLES = ('day_data', 'week_data', 'month_data', 'min_data')


def derive_split_factor(ticker: str, gap_date, db_path: str = 'database'):
    """Split-Faktor aus dem Ueberlapp von Tages- und Stundendaten ableiten.

    Warum nicht einfach das Sprungverhaeltnis nehmen: das enthaelt neben dem
    Split auch die echte Tagesbewegung. Die Stundenreihe dagegen ist nach einem
    Nachladen bereits bereinigt, also liefert ``h60_close / day_close`` auf
    denselben Vor-Split-Tagen den reinen Faktor -- gemessen an BYND ueber 16
    Tage: Median 30,22 bei 0,37 Streuung, also ein Reverse Split 1:30.

    Gibt ``(faktor, quelle, n_tage)`` zurueck; ``faktor`` ist None, wenn sich
    nichts ableiten laesst. Ein Wert nahe an einem gaengigen Verhaeltnis wird
    darauf gerundet -- Splits sind glatte Zahlen.
    """
    p = Tools().get_path(path=db_path, file_name=f'yf_{ticker}.db')
    try:
        with open_db(p, readonly=True) as conn:
            day = pd.read_sql_query(
                'SELECT DATE(Date) d, Close FROM day_data WHERE DATE(Date) < ? '
                'ORDER BY d DESC LIMIT 30', conn, params=(str(gap_date)[:10],))
            h60 = pd.read_sql_query(
                'SELECT DATE(Date) d, Close FROM h60_data WHERE DATE(Date) < ? '
                'ORDER BY Date', conn, params=(str(gap_date)[:10],))
    except Exception:
        logger.debug('derive_split_factor: %s nicht lesbar', ticker, exc_info=True)
        return None, 'keine Daten', 0
    if day.empty or h60.empty:
        return None, 'kein Stunden-Ueberlapp', 0
    last = h60.groupby('d')['Close'].last().rename('h')
    m = day.set_index('d').join(last, how='inner')
    m = m[(m['Close'] > 0) & (m['h'] > 0)]
    if len(m) < 3:
        return None, 'kein Stunden-Ueberlapp', len(m)
    raw = float((m['h'] / m['Close']).median())
    # Nur ein Faktor, der auf ein gaengiges Split-Verhaeltnis passt, wird
    # zurueckgegeben. Alles andere ist KEIN Faktor, sondern ein Hinweis, dass
    # die Methode hier nicht traegt -- und das muss der Aufrufer merken, statt
    # eine krumme Zahl anzuwenden. Gemessen ueber die betroffenen Ticker:
    # bei TBIO, JZ, MVIS, CANG liegt der Rohwert bei ~1,0 (Stunden- und
    # Tagesreihe stehen auf derselben Skala, der Sprung ist also entweder echt
    # oder beide Tabellen sind gleich falsch), bei FFAI bei 152, waehrend der
    # Tagessprung 106 sagt -- widerspruechlich, also unbrauchbar.
    if abs(raw - 1.0) < 0.1:
        return None, f'Stundendaten auf gleicher Skala (roh {raw:.3f})', len(m)
    for n in _SPLIT_RATIOS:
        if abs(raw - n) / n < 0.06:
            return float(n), f'Stundendaten ({len(m)} Tage, roh {raw:.2f})', len(m)
        if abs(raw - 1.0 / n) * n < 0.06:
            return 1.0 / n, f'Stundendaten ({len(m)} Tage, roh {raw:.4f})', len(m)
    return None, f'kein glattes Verhaeltnis (roh {raw:.2f})', len(m)


def apply_split_adjustment(ticker: str, gap_date, factor: float,
                           db_path: str = 'database', dry_run: bool = True) -> dict:
    """Kurse VOR ``gap_date`` mit ``factor`` multiplizieren, Volumen teilen.

    Fuer den Fall, dass die Quelle den Split nicht rueckwirkend einrechnet --
    bei BYND liefert Yahoo selbst auf den Vor-Split-Tagen unbereinigte Werte,
    ``Adj Close`` eingeschlossen. ``get_asset_data.py 1d:max`` kann das deshalb
    nicht heilen: es holt genau die unbereinigte Reihe erneut.

    ``dry_run=True`` (Vorgabe) zaehlt nur. Der Aufrufer muss den Faktor selbst
    bestimmen (siehe :func:`derive_split_factor`) -- die Funktion raet nicht.

    **Nicht mehrfach anwenden.** Ein zweiter Lauf wuerde erneut skalieren; die
    Absicherung ist der Aufrufer, der vorher :func:`detect_price_gaps` prueft.
    """
    if not factor or factor <= 0:
        return {'ticker': ticker, 'error': 'ungueltiger Faktor'}
    cut = str(gap_date)[:10]
    out = {'ticker': ticker, 'factor': factor, 'before': cut,
           'dry_run': dry_run, 'tables': {}}
    p = Tools().get_path(path=db_path, file_name=f'yf_{ticker}.db')
    try:
        with open_db(p) as conn:
            have = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for tbl in _ADJUST_TABLES:
                if tbl not in have:
                    continue
                cols = {r[1] for r in conn.execute(f'PRAGMA table_info({tbl})')}
                price = [c for c in _OHLC_COLS if c in cols]
                if not price:
                    continue
                n = conn.execute(
                    f'SELECT COUNT(*) FROM {tbl} WHERE DATE(Date) < ?',
                    (cut,)).fetchone()[0]
                out['tables'][tbl] = n
                if dry_run or not n:
                    continue
                sets = ", ".join(f'{c} = {c} * ?' for c in price)
                params = [factor] * len(price)
                if 'Volume' in cols:
                    sets += ', Volume = CAST(Volume / ? AS INTEGER)'
                    params.append(factor)
                conn.execute(f'UPDATE {tbl} SET {sets} WHERE DATE(Date) < ?',
                             (*params, cut))
            if not dry_run:
                conn.commit()
    except Exception as exc:
        logger.warning('apply_split_adjustment %s: %s', ticker, exc, exc_info=True)
        out['error'] = str(exc)
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


def _cli_fix_splits(tickers_arg: str, since: str, apply: bool) -> None:
    """`/fix_splits`-Modus: Level-Shifts lokal ausgleichen.

    Nur fuer den Fall, dass die QUELLE den Split nicht rueckwirkend einrechnet.
    Bei BYND liefert Yahoo selbst auf den Vor-Split-Tagen unbereinigte Werte
    (Adj Close = Close = 0,418), deshalb heilt ein erneutes `1d:max` dort nichts.

    Der Faktor kommt aus dem Ueberlapp mit den Stundendaten, nicht aus dem
    Sprungverhaeltnis -- letzteres enthaelt auch die echte Tagesbewegung. Ohne
    `/apply` wird nur gezaehlt.
    """
    from tradinglib import four_ps as fps
    tickers = [t.strip() for t in (tickers_arg or '').split(',') if t.strip()]
    if not tickers:
        logger.error('Bitte /tickers:A,B,C angeben.')
        return
    for tk in tickers:
        try:
            gaps = detect_price_gaps(fps.load_daily(tk, 'database'), since=since)
        except Exception as exc:
            print(f'{tk}: nicht lesbar ({exc})')
            continue
        if not gaps:
            print(f'{tk}: kein Stufensprung')
            continue
        gap = gaps[-1]
        factor, src, _n = derive_split_factor(tk, gap['date'])
        head = f"{tk}: Sprung {gap['date']:%Y-%m-%d} Faktor {gap['factor']:.2f}"
        if not factor:
            print(f'{head} -> KEIN Faktor ableitbar ({src}) -- nicht angefasst')
            continue
        res = apply_split_adjustment(tk, gap['date'], factor, dry_run=not apply)
        rows = sum(res.get('tables', {}).values())
        what = 'angepasst' if apply else 'wuerden angepasst'
        print(f'{head} -> Faktor {factor:g} aus {src}; {rows} Zeilen {what}')
        if apply:
            rest = detect_price_gaps(fps.load_daily(tk, 'database'), since=since)
            print(f'   Kontrolle: {"kein Sprung mehr" if not rest else rest}')
    if not apply:
        print('\nTrockenlauf. Mit /apply schreiben.')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    year = ''
    all_flag = False
    gaps = False
    gap_index = ''
    since = '2015-01-01'
    fix_splits = False
    apply_fix = False
    tickers_arg = ''
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
        elif s == 'fix_splits':
            fix_splits = True
        elif s == 'apply':
            apply_fix = True
        elif s.startswith('tickers:'):
            tickers_arg = a.split(':', 1)[1]
    if fix_splits:
        _cli_fix_splits(tickers_arg, since, apply_fix)
        sys.exit(0)
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
