"""Kandidaten-Trichter: vom Gesamtuniversum zu einer kurzen Prüfliste.

Die Vorauswahl gab es bisher nur verteilt über fünf Seiten -- Marktlage im
Dashboard, Rotation unter Market, Formelfilter in All Assets, Signal im Own-
Trades-Tab. Dieses Modul haengt dieselben Bausteine in eine Kette und zaehlt
mit, wieviel jeder Schritt wegnimmt.

Reihenfolge ist Absicht: teuer wird es erst hinten. Die gespeicherten
Simulationsdaten tragen die breiten Filter (ein Datenbankdurchgang ueber
Tausende Ticker), die Relativstaerke braucht Kursreihen je Titel, und der
Live-Signalpfad laeuft erst auf den letzten paar Werten.

Warum der Signalschritt nicht einfach die Buy-Formel in SQL ist: die Formel
darf Spalten verwenden, die es nur live gibt -- ``atc_mid_zero`` und
``ovtEma9`` etwa stehen nicht in ``asset_simulation``. Und die Marker sind
positionsabhaengig (ein Verkaufssignal gilt nur, wenn man haelt). Deshalb der
getrennte, sim-taugliche Vorfilter hier und die massgebliche Signalpruefung
ueber denselben Pfad, den auch der Chart nimmt.
"""
import logging

import pandas as pd

from tradinglib.tools import Tools, open_db, attach_db
from tradinglib.utils import display_name_sql

logger = logging.getLogger(__name__)

# Vorfilter gegen asset_simulation. Bewusst NICHT die Buy-Formel des Nutzers:
# die referenziert Live-Only-Spalten und liefe hier auf einen SQL-Fehler.
# Gemeint ist ein Trichter, kein Signal -- grosszuegig genug, dass nichts
# Interessantes vorher rausfaellt.
DEFAULT_PREFILTER = ("(overallValueTrend > 0) & (ewo > ewo_ema) "
                     "& (relvol_ratio > 0.5)")

DEFAULTS = {
    'universe': [],            # leer = alle Ticker mit Simulationsdaten
    'prefilter': DEFAULT_PREFILTER,
    'use_rotation': True,      # nur Sektoren mit Zufluss
    'min_sector_rsc': 0.0,     # Mansfield-RSC des Sektor-ETF
    'use_rsc': True,           # Relativstaerke ggue. dem eigenen Sektor pruefen
    'min_rsc': 0.0,            # Outperformance ggue. dem eigenen Sektor-ETF
    'require_isin': False,
    'rank_col': 'overallValueTrend',
    'pool_n': 60,              # so viele gehen in die Relativstaerke-Messung
    'max_per_sector': 3,       # 0 = ohne Begrenzung
    'top_n': 15,
    'with_signal': True,
    'only_add': False,         # nur Werte, deren letzter Marker ein Einstieg war
}

# Spalten, nach denen vorsortiert werden darf. Whitelist, weil der Wert direkt
# in die ORDER-BY-Stelle geht.
RANK_COLUMNS = ('overallValueTrend', 'overallTrend', 'sharpe', 'sortino',
                'ewo', 'relvol_ratio', 'fps_rs')

_ISIN_RE = r'^[A-Z]{2}[A-Z0-9]{9}[0-9]$'

# Fenster, aus dem die letzte Zeile je Ticker geholt wird. Gross genug fuer ein
# langes Wochenende plus Feiertag, klein genug, dass nichts Verwaistes mitkommt.
LOOKBACK_DAYS = 10


def settings(username: str) -> dict:
    """Gespeicherte Einstellungen, mit den Vorgaben aufgefuellt."""
    out = dict(DEFAULTS)
    try:
        from tradinglib import system_config as sysconf
        cfg = sysconf.SystemConfig(username=username)
        stored = cfg.get_value('candidates', None)
        if isinstance(stored, dict):
            out.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except Exception:
        logger.debug("candidates: Einstellungen nicht lesbar", exc_info=True)
    return out


def save_settings(username: str, values: dict) -> None:
    try:
        from tradinglib import system_config as sysconf
        sysconf.SystemConfig(username=username).set_value(
            'candidates', {k: v for k, v in values.items() if k in DEFAULTS})
    except Exception:
        logger.warning("candidates: Einstellungen nicht speicherbar", exc_info=True)


def universe_options(db_path: str = 'database') -> list[str]:
    """Waehlbare Gruppen aus yf_tickers.db -- Indizes zuerst."""
    try:
        p = Tools().get_path(path=db_path, file_name='yf_tickers.db')
        with open_db(p, readonly=True) as conn:
            names = [r[0] for r in conn.execute(
                'SELECT i.name FROM indices i '
                'JOIN stock_indices si ON si.index_id = i.id '
                'GROUP BY i.name ORDER BY COUNT(*) DESC')]
        return sorted(names, key=lambda n: (not n.startswith('^'), n))
    except Exception:
        logger.debug("candidates: Gruppenliste nicht lesbar", exc_info=True)
        return []


def _universe_tickers(groups, db_path='database'):
    if not groups:
        return None                      # kein Filter
    p = Tools().get_path(path=db_path, file_name='yf_tickers.db')
    ph = ','.join('?' * len(groups))
    with open_db(p, readonly=True) as conn:
        return {r[0] for r in conn.execute(
            'SELECT s.Ticker FROM stocks s '
            'JOIN stock_indices si ON s.id = si.stock_id '
            'JOIN indices i ON si.index_id = i.id '
            f'WHERE UPPER(i.name) IN ({ph})',
            [g.upper() for g in groups])}


def _latest_rows(prefilter, db_path='database', lookback_days=LOOKBACK_DAYS):
    """Jüngste Simulationszeile je Ticker, plus Name/Sektor/ISIN.

    Gibt (DataFrame, Hinweis) zurueck. Ein fehlerhafter Vorfilter liefert einen
    leeren Frame und die SQLite-Meldung -- die nennt die fehlende Spalte beim
    Namen, was bei Live-Only-Spalten die eigentliche Auskunft ist.

    **Nicht** am globalen ``MAX(Date)`` verankert. Der jüngste Tag ist in der
    Praxis nur teilbefuellt -- am 2026-08-18 standen dort 9 von rund 5500
    Tickern, weil ein Lauf noch nicht durch war. Ein Trichter, der darauf
    aufsetzt, startet mit einer Handvoll Werte und sieht trotzdem plausibel aus.
    Deshalb je Ticker die letzte Zeile innerhalb eines Fensters, und der
    Vorfilter erst darauf (sonst zoege er eine aeltere, passende Zeile hoch).
    """
    where = f"({prefilter})" if prefilter and prefilter.strip() else "1=1"
    for db_name in ('asset_simulation_all', 'asset_simulation_', 'asset_simulation'):
        sim = Tools().get_path(path=db_path, file_name=f'{db_name}.db')
        info = Tools().get_path(path=db_path, file_name='asset_info.db')
        tick = Tools().get_path(path=db_path, file_name='yf_tickers.db')
        try:
            conn = open_db(sim, readonly=True)
        except Exception:
            continue
        try:
            attach_db(conn, info, 'info_db')
            # ISIN steht nicht in asset_info, sondern in yf_tickers.db/stocks
            # (backfill_isin.py schreibt dorthin). LEFT JOIN, damit ein fehlender
            # Eintrag den Wert nicht aus der Liste wirft -- gefiltert wird erst
            # spaeter und nur auf Wunsch.
            attach_db(conn, tick, 'tick_db')
            row = conn.execute('SELECT DATE(MAX(Date)) FROM asset_simulation').fetchone()
            if not row or not row[0]:
                continue
            max_date = row[0]
            since = (pd.Timestamp(max_date) - pd.Timedelta(days=lookback_days)
                     ).strftime('%Y-%m-%d')
            q = f"""
                WITH latest AS (
                    SELECT ap.*, ai.sector, ai.currency, st.ISIN AS isin,
                           {display_name_sql('ai')},
                           ROW_NUMBER() OVER (PARTITION BY ap.ticker
                                              ORDER BY ap.Date DESC) AS _rn
                    FROM asset_simulation AS ap
                    INNER JOIN info_db.asset_info AS ai ON ap.ticker = ai.ticker
                    LEFT JOIN tick_db.stocks AS st ON st.Ticker = ap.ticker
                    WHERE DATE(ap.Date) >= ?
                )
                SELECT * FROM latest WHERE _rn = 1 AND {where}
            """
            # Ausgangsmenge getrennt zaehlen, damit der Trichter zeigen kann,
            # wieviel der Vorfilter wirklich wegnimmt -- sonst startet die erste
            # Zeile schon mit dem Ergebnis.
            n_total = conn.execute(
                'SELECT COUNT(DISTINCT ap.ticker) FROM asset_simulation AS ap '
                'INNER JOIN info_db.asset_info AS ai ON ap.ticker = ai.ticker '
                'WHERE DATE(ap.Date) >= ?', (since,)).fetchone()[0]
            if not n_total:
                continue
            df = pd.read_sql_query(q, conn, params=(since,))
            df = df.drop(columns=['_rn'], errors='ignore')
            newest = str(df['Date'].max())[:10] if not df.empty and 'Date' in df.columns                 else max_date
            return df, f"{db_name}.db, Stand {newest}", n_total
        except Exception as exc:
            return pd.DataFrame(), f"{db_name}.db: {exc}", 0
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return pd.DataFrame(), "keine Simulationsdatenbank gefunden", 0


def find(username: str, db_path: str = 'database', **kw):
    """Trichter durchlaufen. Gibt (DataFrame, Schritte) zurueck.

    Schritte ist eine Liste aus dicts mit label/before/after/note -- damit die
    Seite zeigen kann, wo wieviel wegfaellt.
    """
    opt = dict(DEFAULTS)
    opt.update(settings(username))
    opt.update({k: v for k, v in kw.items() if v is not None})
    steps = []

    # Schluessel + deutsche Beschriftung nebeneinander: der Schluessel ist fuer
    # die Seite (uebersetzbar), die Beschriftung fuer den CLI-/Testlauf, der
    # keine i18n-Schicht hat.
    def step(key, label, before, after, note=''):
        steps.append({'key': key, 'label': label,
                      'before': before, 'after': after, 'note': note})

    # 1 — Ausgangsmenge, Vorfilter, Universum (jeweils eigene Zeile im Trichter)
    uni = _universe_tickers(opt['universe'], db_path)
    df, note, n_total = _latest_rows(opt['prefilter'], db_path)
    step('base', 'Werte mit Simulationsdaten', n_total, n_total, note)
    step('prefilter', 'Vorfilter', n_total, len(df))
    if df.empty:
        return df, steps
    if uni is not None:
        before = len(df)
        df = df[df['ticker'].isin(uni)]
        step('universe', 'Universum', before, len(df),
             ", ".join(opt['universe']))
    if df.empty:
        return df, steps

    # 2 — delistete/umbenannte raus
    try:
        from tradinglib import asset_status
        inactive = asset_status.inactive_tickers()
        before = len(df)
        df = df[~df['ticker'].isin(inactive)]
        step('tradable', 'Handelbar (nicht delistet)', before, len(df))
    except Exception:
        logger.debug("candidates: asset_status uebersprungen", exc_info=True)

    # 3 — ISIN, optional
    if opt['require_isin'] and 'isin' in df.columns:
        before = len(df)
        df = df[df['isin'].astype(str).str.match(_ISIN_RE, na=False)]
        step('isin', 'ISIN vorhanden', before, len(df))

    # 4 — Sektor-Rotation: nur Sektoren mit Zufluss
    from tradinglib.sector_stocks import SECTOR_ETF_MAP
    df['sector_etf'] = df['sector'].map(SECTOR_ETF_MAP)
    strength = {}
    if opt['use_rotation']:
        try:
            from tradinglib.market_assessment import _sector_strength
            strength = _sector_strength(db_path)
        except Exception:
            logger.warning("candidates: Sektorstaerke nicht ermittelbar", exc_info=True)
        if strength:
            before = len(df)
            keep = {etf for etf, v in strength.items() if v >= opt['min_sector_rsc']}
            df = df[df['sector_etf'].isin(keep)]
            step('sector', 'Sektor mit Zufluss', before, len(df),
                 f"{len(keep)} von {len(strength)} Sektoren")
        else:
            step('sector', 'Sektor mit Zufluss', len(df), len(df), 'keine Sektordaten')
    df['sector_rsc'] = df['sector_etf'].map(strength)

    # 5 — Vorauswahl nach Staerke, BEVOR es teuer wird.
    #
    # Der naechste Schritt laedt Kursreihen je Titel. Ohne diese Kappung liefe er
    # ueber alles, was der Vorfilter durchlaesst -- ein paar tausend Downloads
    # fuer eine Liste, von der am Ende fuenfzehn Zeilen uebrig bleiben.
    rank_col = opt['rank_col'] if opt['rank_col'] in RANK_COLUMNS else 'overallValueTrend'
    if rank_col not in df.columns:
        rank_col = 'overallValueTrend'
    pool = int(opt['pool_n'] or 0)
    if not df.empty and rank_col in df.columns:
        df = df.sort_values(['sector_rsc', rank_col], ascending=[False, False],
                            na_position='last')
        before = len(df)
        if pool:
            # Quote je Sektor statt schlicht head(pool). Global abgeschnitten
            # fuellt der staerkste Sektor den Pool allein -- gemessen: 15 von 15
            # Treffern aus Technology, die uebrigen zugelassenen Sektoren kamen
            # gar nicht erst in die Messung.
            n_sec = max(df['sector'].nunique(), 1)
            quota = max(pool // n_sec, 1)
            df = (df.groupby('sector', group_keys=False, sort=False)
                    .head(quota).head(max(pool, quota)))
        step('pool', 'Vorauswahl nach Stärke', before, len(df),
             f"sortiert nach {rank_col}")

    # 6 — Relativstaerke gegen den eigenen Sektor-ETF (braucht Kursreihen).
    #
    # Ganz abschaltbar, nicht nur ueber die Schwelle: eine sehr negative Schwelle
    # laesst zwar jeden durch, wirft aber weiterhin alles ohne ladbare Kursreihe
    # raus und laedt trotzdem. Aus ist hier also wirklich aus -- und der mit
    # Abstand teuerste Schritt entfaellt.
    df['RSC_vs_ETF'] = float('nan')
    if opt['use_rsc'] and not df.empty:
        try:
            from tradinglib import sector_stocks as ss
            before = len(df)
            df = ss.enrich_with_rsc_multi(df, sector_col='sector_etf', weeks=4)
            df = df.dropna(subset=['RSC_vs_ETF'])
            df = df[df['RSC_vs_ETF'] >= opt['min_rsc']]
            step('rsc', 'Schlägt den eigenen Sektor', before, len(df))
        except Exception:
            logger.warning("candidates: RSC-Anreicherung fehlgeschlagen", exc_info=True)
    elif not opt['use_rsc']:
        step('rsc', 'Schlägt den eigenen Sektor', len(df), len(df), 'abgeschaltet')

    # 7 — Bestenliste: erst der Sektor, dann der Titel darin.
    # Ohne Relativstaerke ist die zweite Sortierstufe leer -- dann entscheidet
    # innerhalb des Sektors die gewaehlte Kennzahl.
    if not df.empty:
        second = 'RSC_vs_ETF' if opt['use_rsc'] else rank_col
        df = df.sort_values(['sector_rsc', second],
                            ascending=[False, False], na_position='last')
        before = len(df)
        mps = int(opt['max_per_sector'] or 0)
        note = ''
        if mps:
            df = df.groupby('sector', group_keys=False, sort=False).head(mps)
            df = df.sort_values(['sector_rsc', second],
                                ascending=[False, False], na_position='last')
            note = f"höchstens {mps} je Sektor"
        if opt['top_n']:
            df = df.head(int(opt['top_n']))
        step('best', 'Bestenliste', before, len(df), note)

    # 8 — Signal ueber den Live-Pfad, nur noch auf der kurzen Liste
    df['signal'] = None
    if opt['with_signal'] and not df.empty:
        try:
            from tradinglib import system_config as sysconf
            from tradinglib.portfolio_analysis import (
                _compute_position_signals, SIGNAL_TIMEFRAMES, signal_timeframe)
            cfg = sysconf.SystemConfig(username=username)
            buy_q = cfg.get_value('buy_query', '')
            sell_q = cfg.get_value('sell_query', '')
            if buy_q or sell_q:
                tf = signal_timeframe(username)
                iv, pe = SIGNAL_TIMEFRAMES[tf]
                sig, _w = _compute_position_signals(
                    df['ticker'].tolist(), buy_q, sell_q, db_path, username, iv, pe)
                for col, key in (('signal', 'action'),
                                 ('last_signal', 'last_type'),
                                 ('last_signal_date', 'last_date')):
                    df[col] = df['ticker'].map(
                        lambda t, k=key: (sig.get(t) or {}).get(k))
                # Vokabular von portfolio_analysis: 'add' = letzter Marker war
                # ein Einstieg (Strategie ist long), 'reduce' = ausgestiegen
                # oder gerade flach. Fuer eine Einstiegsliste ist 'reduce' ein
                # Gegenargument, kein Signal.
                before = len(df)
                n_add = int((df['last_signal'] == 'add').sum())
                if opt['only_add']:
                    df = df[df['last_signal'] == 'add']
                step('signal', 'Letztes Signal war ein Einstieg', before, len(df),
                     f"Zeitebene {tf}, {n_add} von {before}")
        except Exception:
            logger.warning("candidates: Signalberechnung fehlgeschlagen", exc_info=True)

    return df, steps
