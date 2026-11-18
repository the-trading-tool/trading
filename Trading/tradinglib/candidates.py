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
darf Spalten verwenden, die es nur live gibt (Indikatoren ohne Eintrag in
``asset_simulation``). Und die Marker sind
positionsabhaengig (ein Verkaufssignal gilt nur, wenn man haelt). Deshalb der
getrennte, sim-taugliche Vorfilter hier und die massgebliche Signalpruefung
ueber denselben Pfad, den auch der Chart nimmt.
"""
import logging
import re

import pandas as pd

from tradinglib.tools import Tools, open_db, attach_db, split_conditions
from tradinglib.utils import display_name_sql

logger = logging.getLogger(__name__)

# Vorfilter gegen asset_simulation. Bewusst NICHT die Buy-Formel des Nutzers:
# die referenziert Live-Only-Spalten und liefe hier auf einen SQL-Fehler.
# Gemeint ist ein Trichter, kein Signal -- grosszuegig genug, dass nichts
# Interessantes vorher rausfaellt.
#
# Der Vorfilter ist jetzt bewusst **richtungsneutral** und schmal: die Richtung
# kommt aus dem Trendschritt und dem Level-Test, jeder Schritt traegt genau eine
# Aussage. Warum hier kein Momentum steht: es widerspricht dem Ruecksetzer.
# Gemessen: `macd_trend_wk > 0` liess von 201 Support-Tests 4 uebrig, zusammen
# mit `sma50 > sma200` keinen.
#
# Was bleibt, ist eine Qualitaetsschwelle: handelbares Volumen und ein Band
# zwischen Unterstuetzung und Widerstand, das breit genug ist, damit ein Test
# des Levels ueberhaupt etwas bedeutet.
DEFAULT_PREFILTER = ("(relvol_ratio > 0.5) "
                     "& (sup_resistance / sup_support > 1.03)")

# Richtungen. 'short' spiegelt jeden richtungsabhaengigen Schritt: schwaechste
# statt staerkste Sektoren, Rueckstand statt Vorsprung, Widerstand statt
# Unterstuetzung.
DIRECTIONS = ('long', 'short')

# Wie der Wochentrend geprueft wird.
#   fps       -- 4PS-Phase 3/4: Einstieg und Halten nur ueber einem STEIGENDEN
#                40-Wochen-Durchschnitt, ueberlebt einen Ruecksetzer.
#   structure -- sma50 gegen sma200, das breitere strukturelle Mass.
#   off       -- kein Trendfilter.
# Welche 4PS-Phasen ein Modus zulaesst. Die Phasen sind: 0 = nichts,
# 1 = bewaehrte Trendhistorie, 2 = enge Basis nahe dem Rekordhoch,
# 3 = Ausbruch, 4 = bestaetigter Trend.
#
# Warum drei Zuschnitte statt einem: 3/4 verlangt den vollzogenen Ausbruch und
# schliesst damit genau die Ruecksetzer aus, die man kaufen will -- COP stand am
# Umkehrtag in Phase 2, MDT in Phase 1, beide fielen durch. 2/3/4 nimmt die
# Basis dazu, 1/2 sucht ausschliesslich das, was den Ausbruch noch vor sich hat.
FPS_PHASES = {
    'fps':    (3, 4),
    'fps234': (2, 3, 4),
    'fps12':  (1, 2),
}

TREND_MODES = ('fps', 'fps234', 'fps12', 'structure', 'off')

DEFAULTS = {
    'universe': [],            # leer = alle Ticker mit Simulationsdaten
    'direction': 'long',
    'prefilter': DEFAULT_PREFILTER,
    # 1 = alle Bedingungen auf demselben (juengsten) Balken, wie bisher.
    # Groesser = jede Zeile der Formel darf innerhalb dieser Zahl von Bars
    # erfuellt worden sein; das Signal traegt den Tag der letzten.
    'prefilter_window': 1,
    'trend_mode': 'fps',
    'retest': True,            # Unterstuetzung/Widerstand wird getestet
    'retest_window': 20,       # Bars, ueber die der Test gesucht wird
    'retest_tol': 1.5,         # % Naehe, die als "am Level" zaehlt
    'retest_lift': 3.0,        # % Abstand, den der Kurs vorher hatte
    'use_rotation': True,      # nur Sektoren mit Zufluss
    'min_sector_rsc': 0.0,     # Mansfield-RSC des Sektor-ETF
    # Standardmaessig AUS, und das ist gemessen: ein Wert, der seine
    # Unterstuetzung testet, liegt fast zwangslaeufig hinter seinem Sektor. In
    # zwei Durchlaeufen raeumte der Schritt die Liste restlos ab (10 -> 0 und
    # 5 -> 0). Fuer eine Momentum-Auswahl ist er der nuetzlichste der Kette,
    # fuer eine Ruecksetzer-Auswahl der schaedlichste.
    'use_rsc': False,          # Relativstaerke ggue. dem eigenen Sektor pruefen
    'min_rsc': 0.0,            # Outperformance ggue. dem eigenen Sektor-ETF
    'require_isin': False,
    # Risikoprofil aus tradinglib/risk_profile.py. '' = aus, 'user' = das auf der
    # Profilseite eingestellte, sonst der Name eines Presets. Der Schritt sitzt
    # bewusst VOR dem Trendfilter: gemessen verdoppelte sich der Vorteil einer
    # Trendauswahl, sobald zuerst auf das ruhige Ende des Universums geschnitten
    # wurde -- andersherum bleibt davon nichts.
    'risk_profile': '',
    # Qualitaets-Score (tradinglib/quality_score.py): 'off', 'exclude' (Werte
    # mit Warnzeichen raus) oder 'top' (nur das obere quality_top_pct-Prozent
    # nach sektor-relativem Rang, ohne Warnzeichen). Bei Short gespiegelt. Der
    # Score ist eine Momentaufnahme -- fuer die heutige Auswahl, nicht gemessen.
    'quality_mode': 'off',
    'quality_top_pct': 15,
    # Mindest-Trendwert (Naehe zum eigenen Rekordhoch, 0..100). 0 = aus. Bei
    # 'short' gespiegelt: dort zaehlt der Abstand zum Hoch, nicht die Naehe.
    'min_trend_score': 0,
    # Standard deviation channel (indicator sdc): long keeps closes at or below
    # sdc_long sigma from the regression line, short at or above sdc_short.
    # sdc_years is the regression length; 10 reads the stored columns, any other
    # length is fitted live on the rows left at that point.
    'use_sdc': False,
    'sdc_years': 10,
    'sdc_long': -3.0,
    'sdc_short': 1.0,
    # Quality gate of the channel step: long keeps quality_score > x, short
    # (mirrored) quality_score < 100 - x; names without a score drop out.
    # 0 = off. Runs before the channel fit, so a live fit has fewer names.
    'sdc_min_quality': 0,
    'rank_col': 'sortino',
    'pool_n': 60,              # so viele gehen in die Relativstaerke-Messung
    'max_per_sector': 3,       # 0 = ohne Begrenzung
    'top_n': 15,
    'with_signal': True,
    'only_add': False,         # nur Werte, deren letzter Marker ein Einstieg war
}

# Spalten, nach denen vorsortiert werden darf. Whitelist, weil der Wert direkt
# in die ORDER-BY-Stelle geht.
RANK_COLUMNS = ('sortino', 'sharpe',
                'ewo', 'relvol_ratio', 'fps_rs', 'trendScore', 'riskScore',
                'quality_pct')

QUALITY_MODES = ('off', 'exclude', 'top', 'elite')
QUALITY_COLUMNS = ('quality_score', 'quality_tier', 'quality_sector', 'quality_pct',
                   'quality_flags', 'quality_n_flags', 'quality_avail')

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


def available_columns(db_path: str = 'database') -> list[str]:
    """Spalten, die der Vorfilter verwenden darf.

    Gedacht fuer die Fehlermeldung: nennt SQLite eine unbekannte Spalte, ist die
    Antwort fast immer "die gibt es nur live". Mit der echten Liste kann die
    Seite gleich die naechstliegenden Treffer vorschlagen, statt den Nutzer
    raten zu lassen.
    """
    for db_name in ('asset_simulation_all', 'asset_simulation_', 'asset_simulation'):
        try:
            p = Tools().get_path(path=db_path, file_name=f'{db_name}.db')
            with open_db(p, readonly=True) as conn:
                cols = [r[1] for r in conn.execute(
                    'PRAGMA table_info(asset_simulation)')]
            if cols:
                return sorted(cols)
        except Exception:
            continue
    return []


def _prefilter_window(prefilter, window, db_path='database', tickers=None):
    """Bedingungen duerfen auf VERSCHIEDENEN Tagen erfuellt sein.

    Jede Zeile der Formel ist eine eigene Bedingung. Erfuellt heisst: irgendwann
    innerhalb des Fensters wahr. Das Signal traegt den Tag, an dem die letzte
    fehlende Bedingung dazukam.

    Der Grund fuer den Umbau steckt in den Daten: bei COP lag der Test der
    Unterstuetzung am 6. Juli, die EWO-Kreuzung am 7.; bei MDT verteilten sich
    Support, RSI und EWO auf drei Tage. Auf einen einzigen Balken gezwungen,
    treffen solche Bedingungen fast nie zusammen -- und je mehr man
    hinzunimmt, desto unwahrscheinlicher wird es.

    **Bewusste Lockerung:** eine Bedingung zaehlt weiter, auch wenn sie spaeter
    wieder falsch wird. Gemeint ist "Setup ist passiert, dann kam der
    Ausloeser", nicht "alles gilt gleichzeitig".

    Gibt (``{ticker: Signaldatum}``, ``[(Bedingung, Trefferzahl)]``) zurueck.
    """
    from tradinglib.tools import split_conditions
    lines = split_conditions(prefilter)
    if not lines:
        return None, []
    since = (pd.Timestamp.today() - pd.Timedelta(days=int(window) * 2 + 14)
             ).strftime('%Y-%m-%d')
    for db_name in ('asset_simulation_all', 'asset_simulation_', 'asset_simulation'):
        p = Tools().get_path(path=db_path, file_name=f'{db_name}.db')
        try:
            conn = open_db(p, readonly=True)
        except Exception:
            continue
        try:
            # Handelstage des Fensters. Das Fenster zaehlt Bars, nicht
            # Kalendertage -- und "Tag" heisst hier: ein Termin mit echter
            # Abdeckung. Wochenenden stehen mit 11 bis 36 Zeilen in der Tabelle
            # (Krypto und Devisen handeln durch), Handelstage mit 4000 bis 5500.
            # Ohne diese Huerde verschenkt ein Fenster von 5 Bars zwei Plaetze
            # ans Wochenende -- gemessen lieferten 3 und 5 Bars fast dieselbe
            # Menge, weil dazwischen nur Samstag und Sonntag lagen.
            counts = conn.execute(
                'SELECT DATE(Date) d, COUNT(*) n FROM asset_simulation '
                'WHERE DATE(Date) >= ? GROUP BY d ORDER BY d DESC',
                (since,)).fetchall()
            if not counts:
                continue
            busy = sorted(n for _d, n in counts)[len(counts) // 2] * 0.25
            days = [d for d, n in counts if n >= busy][:int(window)]
            if not days:
                continue
            first = min(days)
            per_cond, stats = [], []
            if needs_pandas(prefilter):
                # Same rule, evaluated per ticker in pandas: load enough bars
                # before the window for every shift/rolling to be defined.
                bars = _history_bars(prefilter, window)
                since_hist = (pd.Timestamp(first) - pd.Timedelta(days=int(bars * 1.6) + 14)
                              ).strftime('%Y-%m-%d')
                panel = _load_panel(conn, prefilter, since_hist, tickers)
                in_win = panel['d'] >= first
                for cond, m in zip(lines, _line_masks(panel, lines)):
                    sel = panel.loc[in_win & m, ['ticker', 'd']]
                    d = sel.groupby('ticker')['d'].max()
                    per_cond.append(d.to_dict())
                    stats.append((cond, len(d)))
                lines = []
            for cond in lines:
                d = pd.read_sql_query(
                    'SELECT ticker, MAX(DATE(Date)) AS d FROM asset_simulation '
                    f'WHERE DATE(Date) >= ? AND ({cond}) GROUP BY ticker',
                    conn, params=(first,))
                per_cond.append(dict(zip(d['ticker'], d['d'])))
                stats.append((cond, len(d)))
            ok = set(per_cond[0])
            for m in per_cond[1:]:
                ok &= set(m)
            hits = {t: max(m[t] for m in per_cond) for t in ok}
            return hits, stats
        except Exception as exc:
            logger.warning("candidates: Fensterprüfung fehlgeschlagen", exc_info=True)
            raise RuntimeError(str(exc)) from exc
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return None, []


# Time-series syntax SQLite cannot run: pandas methods (x.shift(1),
# x.rolling(20).max()) and indexed access (x[-1]). Such a prefilter is
# evaluated per ticker in pandas instead, with the evaluator the Strategy Finder
# and the chart use -- otherwise SQLite answers 'near "(": syntax error'.
_PANDAS_SYNTAX_RE = re.compile(r'\.\s*[A-Za-z_]\w*\s*\(|\w\s*\[\s*-?\d+\s*\]')
# Integer arguments of rolling/shift; their sum bounds the history a line needs.
_SPAN_RE = re.compile(r'\.\s*(?:rolling|shift)\s*\(\s*(?:window\s*=\s*)?(-?\d+)')


def needs_pandas(prefilter) -> bool:
    """True when the prefilter uses time-series syntax SQLite cannot evaluate."""
    return bool(_PANDAS_SYNTAX_RE.search(str(prefilter or '')))


def _history_bars(prefilter, window) -> int:
    """Bars of history to load: spans of every rolling/shift plus the window."""
    spans = sum(abs(int(n)) for n in _SPAN_RE.findall(str(prefilter or '')))
    return spans + int(window) + 5


def _load_panel(conn, prefilter, since, tickers=None):
    """Rows since *since* for the columns the prefilter uses, time-ordered per ticker.

    Names that match a column only case-insensitively (SQLite accepted
    ``close > open`` because ``open`` is ``Open`` there) get an alias column,
    so the pandas path accepts what the SQL path accepted. An unknown name
    raises with SQLite's wording, which the page turns into a hint.
    """
    from tradinglib.tools import formula_names
    cols = [r[1] for r in conn.execute('PRAGMA table_info(asset_simulation)')]
    by_lower = {c.lower(): c for c in cols}
    load, alias = [], {}
    for name in formula_names(prefilter):
        real = name if name in cols else by_lower.get(name.lower())
        if real is None:
            raise RuntimeError(f'no such column: {name}')
        if real not in load:
            load.append(real)
        if real != name:
            alias[name] = real
    sel = ', '.join(f'"{c}"' for c in load if c.lower() not in ('ticker', 'date'))
    df = pd.read_sql_query(
        f'SELECT ticker, DATE(Date) AS d{", " + sel if sel else ""} '
        'FROM asset_simulation WHERE DATE(Date) >= ? ORDER BY ticker, Date',
        conn, params=(since,))
    if tickers is not None:
        # The universe step drops everything else anyway; evaluating per ticker
        # is the expensive part (~1 ms each), so skip the rest up front.
        df = df[df['ticker'].isin(tickers)]
    # One row per ticker and day; a rerun can leave two (keep the newer).
    df = df.drop_duplicates(['ticker', 'd'], keep='last').reset_index(drop=True)
    for c in load:
        if c not in ('ticker', 'd') and df[c].dtype == object:
            df[c] = pd.to_numeric(df[c], errors='coerce')   # TEXT affinity
    for name, real in alias.items():
        df[name] = df[real]
    return df


def _line_masks(panel, lines):
    """Per-line boolean masks, evaluated per ticker (shift/rolling stay inside one)."""
    from tradinglib.tools import compute_signal_mask
    out = []
    for line in lines:
        try:
            m = compute_signal_mask(panel, line, group_col='ticker', window=1)
        except Exception as exc:
            raise RuntimeError(f'{line}: {exc}') from exc
        out.append(m.fillna(False).astype(bool))
    return out


def _prefilter_pandas_latest(prefilter, db_path='database', lookback_days=LOOKBACK_DAYS,
                             tickers=None):
    """Window 1 for a time-series prefilter: does each ticker's latest row qualify?

    Same rule as the SQL path (latest row within *lookback_days* of the newest
    date, all conditions on that row), only evaluated in pandas. Returns
    ``{ticker: date}`` of the qualifying tickers.
    """
    lines = split_conditions(prefilter)
    if not lines:
        return None
    for db_name in ('asset_simulation_all', 'asset_simulation_', 'asset_simulation'):
        p = Tools().get_path(path=db_path, file_name=f'{db_name}.db')
        try:
            conn = open_db(p, readonly=True)
        except Exception:
            continue
        try:
            row = conn.execute('SELECT DATE(MAX(Date)) FROM asset_simulation').fetchone()
            if not row or not row[0]:
                continue
            newest = pd.Timestamp(row[0])
            cutoff = (newest - pd.Timedelta(days=lookback_days)).strftime('%Y-%m-%d')
            bars = _history_bars(prefilter, 1)
            since = (newest - pd.Timedelta(days=lookback_days + int(bars * 1.6) + 14)
                     ).strftime('%Y-%m-%d')
            panel = _load_panel(conn, prefilter, since, tickers)
            if panel.empty:
                continue
            ok = pd.Series(True, index=panel.index)
            for m in _line_masks(panel, lines):
                ok &= m
            last = panel.groupby('ticker').tail(1)
            last = last[(last['d'] >= cutoff) & ok.loc[last.index]]
            return dict(zip(last['ticker'], last['d']))
        except Exception as exc:
            logger.warning("candidates: prefilter (pandas) failed", exc_info=True)
            raise RuntimeError(str(exc)) from exc
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return None


def _apply_quality(df, opt, direction, db_path, step):
    """Merge the stored quality scores and filter by ``quality_mode``.

    Long: 'exclude' drops red-flagged equities and keeps rows without a score
    (ETFs, indices, no data) -- the step removes known problems, it does not
    demand proof. 'top' keeps ``quality_pct >= 100 - quality_top_pct`` without
    flags; unscored rows go. 'elite' keeps tier 1 (score >= 85) without flags,
    an absolute cut instead of a rank. Short mirrors it: 'exclude' drops tier 1
    (the compounders), 'top' keeps the bottom ``quality_top_pct`` percent,
    'elite' keeps tier 4.
    """
    try:
        from tradinglib import quality_score as qs
        scores = qs.load(db_path)
    except Exception:
        logger.warning("candidates: quality scores not readable", exc_info=True)
        scores = pd.DataFrame()
    mode = opt.get('quality_mode') if opt.get('quality_mode') in QUALITY_MODES else 'off'
    if scores.empty:
        if mode != 'off':
            step('quality', 'Qualität', len(df), len(df),
                 'keine Qualitätsdaten — übersprungen', 'note_quality_none')
        return df
    keep = ['ticker'] + [c for c in QUALITY_COLUMNS if c in scores.columns]
    df = df.drop(columns=[c for c in QUALITY_COLUMNS if c in df.columns])
    df = df.merge(scores[keep], on='ticker', how='left')
    if mode == 'off':
        return df
    before = len(df)
    pct = float(opt.get('quality_top_pct') or 15)
    score = pd.to_numeric(df['quality_score'], errors='coerce')
    flags = pd.to_numeric(df['quality_n_flags'], errors='coerce').fillna(0)
    rank = pd.to_numeric(df['quality_pct'], errors='coerce')
    tier = pd.to_numeric(df['quality_tier'], errors='coerce')
    unscored = int(score.isna().sum())
    if direction == 'long':
        if mode == 'exclude':
            mask = flags == 0
            note = (f"{int((flags > 0).sum())} mit Warnzeichen entfernt, "
                    f"{unscored} ohne Score behalten")
            nkey, args = 'note_quality_exclude', {'n': int((flags > 0).sum()), 'u': unscored}
        elif mode == 'elite':
            mask = (tier == 1) & (flags == 0)
            n_flagged = int(((tier == 1) & (flags > 0)).sum())
            note = f"nur Elite (Stufe 1), {n_flagged} Elite-Werte mit Warnzeichen entfernt"
            nkey, args = 'note_quality_elite', {'f': n_flagged}
        else:
            mask = (rank >= 100 - pct) & (flags == 0)
            note = f"obere {pct:.0f} % nach Sektor-Rang, ohne Warnzeichen"
            nkey, args = 'note_quality_top', {'pct': int(pct)}
    else:
        if mode == 'exclude':
            mask = tier != 1
            note = f"{int((tier == 1).sum())} Elite-Werte (Stufe 1) entfernt"
            nkey, args = 'note_quality_exclude_short', {'n': int((tier == 1).sum())}
        elif mode == 'elite':
            mask = tier == 4
            note = "nur Stufe 4 (Risiko)"
            nkey, args = 'note_quality_elite_short', {}
        else:
            mask = rank <= pct
            note = f"untere {pct:.0f} % nach Sektor-Rang"
            nkey, args = 'note_quality_bottom', {'pct': int(pct)}
    df = df[mask.fillna(False)]
    step('quality', 'Qualität', before, len(df), note, nkey, **args)
    return df


SDC_WORKERS = 8


def _sdc_live(tickers, years, db_path='database'):
    """``{ticker: (sdc_pos, sdc_std)}`` from the channel over the last *years*.

    Same fit as the chart (``sdc.fit_last`` on the full local daily closes), so
    a ticker without *years* of history gets no entry. Reads only the window
    plus a margin; threads because the time goes into opening the files.
    """
    import os
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    import numpy as np
    from tradinglib.indicator import sdc

    since = (pd.Timestamp.today().normalize()
             - sdc.window(years) - pd.Timedelta(days=40)).strftime('%Y-%m-%d')

    def one(ticker):
        path = Tools().get_path(path=db_path, file_name=f'yf_{ticker}.db')
        if not os.path.exists(path):
            return ticker, None
        try:
            con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
            try:
                rows = con.execute(
                    "SELECT substr(Date, 1, 10), Close FROM day_data "
                    "WHERE Close IS NOT NULL AND Date >= ? ORDER BY Date",
                    (since,)).fetchall()
            finally:
                con.close()
            if not rows:
                return ticker, None
            dates, closes = zip(*rows)
            close = pd.Series(pd.to_numeric(pd.Series(closes), errors='coerce').to_numpy(),
                              index=pd.DatetimeIndex(np.asarray(dates, dtype='datetime64[D]')))
            close = close[~close.index.duplicated(keep='last')].dropna()
            fit = sdc.fit_last(close, years)
            if fit is None or not fit[1] > 0:
                return ticker, None
            mid, std, _r = fit
            return ticker, ((float(close.iloc[-1]) - float(mid.iloc[-1])) / std, std)
        except Exception:
            logger.debug("candidates: sdc for %s failed", ticker, exc_info=True)
            return ticker, None

    with ThreadPoolExecutor(max_workers=SDC_WORKERS) as ex:
        return {t: v for t, v in ex.map(one, tickers) if v is not None}


def _apply_sdc_quality(df, opt, direction, step):
    """Quality gate of the channel step (``sdc_min_quality``, 0 = off).

    Long keeps ``quality_score > x``, short ``quality_score < 100 - x``; rows
    without a score (ETFs, indices, no data) drop out. The score columns come
    from _apply_quality, which merges them whenever scores exist; without any
    score data the gate is skipped, as the quality step does.
    """
    x = float(opt.get('sdc_min_quality') or 0)
    if x <= 0:
        return df
    before = len(df)
    if 'quality_score' not in df.columns:
        step('sdc_quality', 'Kanal-Qualität', before, before,
             'keine Qualitätsdaten — übersprungen', 'note_quality_none')
        return df
    score = pd.to_numeric(df['quality_score'], errors='coerce')
    unscored = int(score.isna().sum())
    if direction == 'long':
        op, lim = '>', x
        mask = score > lim
    else:
        op, lim = '<', 100 - x
        mask = score < lim
    df = df[mask.fillna(False)]
    step('sdc_quality', 'Kanal-Qualität', before, len(df),
         f"quality_score {op} {lim:g}, {unscored} ohne Score entfernt",
         'note_sdc_quality', op=op, v=f"{lim:g}", u=unscored)
    return df


def _apply_sdc(df, opt, direction, db_path, step):
    """Keep closes beyond the chosen sigma line of the regression channel.

    Long: ``sdc_pos <= sdc_long`` (default -3, the lower band), short:
    ``sdc_pos >= sdc_short`` (default +1). With the stored length (10 years) the
    columns from asset_simulation are used; they need the full window, NaN is
    stored as 0, so only ``sdc_std > 0`` counts. Any other length -- or a sim DB
    without the columns -- is fitted live on the remaining tickers. The optional
    quality gate (``sdc_min_quality``) runs first.
    """
    from tradinglib.indicator import sdc
    df = _apply_sdc_quality(df, opt, direction, step)
    if df.empty:
        return df
    years = int(opt.get('sdc_years') or sdc.DEFAULT_YEARS)
    thr = float(opt['sdc_long'] if direction == 'long' else opt['sdc_short'])
    before = len(df)
    std = (pd.to_numeric(df['sdc_std'], errors='coerce')
           if 'sdc_std' in df.columns else pd.Series(float('nan'), index=df.index))
    stored = (years == sdc.DEFAULT_YEARS and 'sdc_pos' in df.columns
              and bool((std > 0).any()))
    if stored:
        pos = pd.to_numeric(df['sdc_pos'], errors='coerce').where(std > 0)
        source, src_key = 'gespeichert', 'stored'
    else:
        live = _sdc_live(df['ticker'].astype(str).tolist(), years, db_path)
        pos = df['ticker'].map(lambda t: (live.get(t) or (float('nan'),))[0])
        source, src_key = 'live', 'live'
    df = df.assign(sdc_pos=pos.astype(float))
    mask = (df['sdc_pos'] <= thr) if direction == 'long' else (df['sdc_pos'] >= thr)
    n_channel = int(df['sdc_pos'].notna().sum())
    df = df[mask.fillna(False)]
    op = '≤' if direction == 'long' else '≥'
    step('sdc', 'Regressionskanal', before, len(df),
         f"sdc_pos {op} {thr:+g}σ, {years} J. ({source}), "
         f"{n_channel} von {before} mit Kanal",
         f'note_sdc_{src_key}', op=op, thr=f"{thr:+g}", years=years,
         n=n_channel, total=before)
    return df


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
            newest = (str(df['Date'].max())[:10]
                      if not df.empty and 'Date' in df.columns else max_date)
            return df, ('source', {'db': f'{db_name}.db', 'date': newest}), n_total
        except Exception as exc:
            return pd.DataFrame(), ('error', {'db': f'{db_name}.db', 'error': exc}), 0
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return pd.DataFrame(), ('no_db', {}), 0


def _level_test(tickers, direction, db_path='database', window=20,
                tol=1.5, lift=3.0):
    """Wer testet gerade sein Level -- und kommt er von der richtigen Seite?

    Gibt ``{ticker: ('test'|'retest', Abstand in %)}`` zurueck. Long prueft die
    Unterstuetzung, Short den Widerstand.

    Unterschieden wird zweierlei:
      * **test**   -- der Kurs laeuft das Level von der richtigen Seite an und
                      war im Fenster deutlich weiter weg (sonst klebt er nur
                      seit Wochen daran, was keine Gelegenheit ist),
      * **retest** -- der Kurs war schon durch das Level hindurch und ist
                      zurueckgekommen.

    Gelesen wird aus ``asset_simulation`` und nicht aus den Kursreihen: dort
    stehen ``close`` und ``sup_support``/``sup_resistance`` bereits als
    Tagesreihe nebeneinander, in einer Abfrage fuer alle Ticker. Damit stammen
    Level und Kurs aus derselben Quelle wie der Vorfilter -- ein Download je
    Titel waere teurer und koennte den beiden widersprechen.
    """
    out = {}
    tickers = [t for t in dict.fromkeys(tickers) if t]
    if not tickers:
        return out
    level_col = 'sup_support' if direction == 'long' else 'sup_resistance'
    # Kalendertage grosszuegiger als Bars, weil die Simulationstabelle Luecken
    # hat (Wochenenden, unvollstaendige Laeufe).
    since = (pd.Timestamp.today() - pd.Timedelta(days=int(window) * 2 + 14)
             ).strftime('%Y-%m-%d')
    for db_name in ('asset_simulation_all', 'asset_simulation_', 'asset_simulation'):
        p = Tools().get_path(path=db_path, file_name=f'{db_name}.db')
        try:
            conn = open_db(p, readonly=True)
        except Exception:
            continue
        try:
            ph = ','.join('?' * len(tickers))
            df = pd.read_sql_query(
                f"""SELECT ticker, DATE(Date) AS d, close, {level_col} AS lvl
                    FROM asset_simulation
                    WHERE ticker IN ({ph}) AND DATE(Date) >= ?
                    ORDER BY ticker, d""",
                conn, params=(*tickers, since))
        except Exception:
            logger.warning("candidates: Level-Test nicht lesbar", exc_info=True)
            return out
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if df.empty:
            continue

        df = df[(df['lvl'] > 0) & df['close'].notna()]
        # Abstand zum Level in Prozent, vorzeichenbehaftet: positiv = auf der
        # "sicheren" Seite (long ueber der Unterstuetzung, short unter dem
        # Widerstand), negativ = durchbrochen.
        sign = 1.0 if direction == 'long' else -1.0
        for tk, g in df.groupby('ticker'):
            g = g.tail(int(window))
            if len(g) < 3:
                continue
            lvl_now = float(g['lvl'].iloc[-1])
            lvl_then = float(g['lvl'].iloc[0])
            if lvl_now <= 0 or lvl_then <= 0:
                continue
            gap = sign * (g['close'] / g['lvl'] - 1.0) * 100.0
            now = float(gap.iloc[-1])
            if abs(now) > tol:
                continue                      # steht nicht am laufenden Level

            # Beide Zweige werden gegen das EINGEFRORENE Niveau vom
            # Fensteranfang geprueft, nicht gegen das mitlaufende. Der Grund
            # steckt in den Daten: ueber ~150.000 Zeilen liegt der Schlusskurs
            # nur in zwei unter `sup_support`, nie tiefer als 1,21 % -- die
            # Spalte zieht mit dem Kurs nach. Am laufenden Niveau gemessen sieht
            # deshalb JEDER stetig fallende Wert aus wie einer, der "seine
            # Unterstuetzung testet" (nachgerechnet an ABEV: durch 3,00
            # gefallen, weiter bis -6,7 %, Unterstuetzung von 3,00 auf 2,76
            # nachgezogen, laufender Abstand +1,45 %). Fuer einen Einstieg ist
            # das das Gegenteil eines Tests.
            frozen = sign * (g['close'] / lvl_then - 1.0) * 100.0
            # Haelt das Level ueberhaupt? Rutscht es im Fenster mit dem Kurs
            # davon, war es keine Unterstuetzung, sondern eine Spur.
            level_held = sign * (lvl_now / lvl_then - 1.0) * 100.0 >= -tol

            if float(frozen.iloc[:-1].min()) < -tol:
                # War durch das damalige Niveau -- zaehlt nur, wenn er auch
                # wieder dort steht, sonst faellt er bloss weiter.
                if abs(float(frozen.iloc[-1])) <= tol:
                    out[tk] = ('retest', round(now, 2))
            elif level_held and float(gap.iloc[:-1].max()) >= lift:
                out[tk] = ('test', round(now, 2))
        return out
    return out


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
    def step(key, label, before, after, note='', note_key='', **note_args):
        # Die deutsche Notiz bleibt als Rueckfallebene fuer CLI- und Testlaeufe,
        # die keine i18n-Schicht haben; die Seite bevorzugt note_key/note_args.
        steps.append({'key': key, 'label': label, 'before': before,
                      'after': after, 'note': note,
                      'note_key': note_key, 'note_args': note_args})

    # Richtung. `sign` dreht jedes richtungsabhaengige Mass um, statt an jeder
    # Stelle den Vergleich zu spiegeln -- so bleibt "Schwelle" ueberall
    # "mindestens so viel davon", nur eben Rueckstand statt Vorsprung.
    direction = opt['direction'] if opt['direction'] in DIRECTIONS else 'long'
    sign = 1.0 if direction == 'long' else -1.0

    # 1 — Ausgangsmenge, Vorfilter, Universum (jeweils eigene Zeile im Trichter)
    uni = _universe_tickers(opt['universe'], db_path)
    win = int(opt.get('prefilter_window') or 1)
    lines = split_conditions(opt['prefilter'])
    hits = cond_stats = None
    if win > 1 and lines:
        # Fensterpfad: erst die Ticker bestimmen, dann ihre juengsten Zeilen
        # holen. Der Vorfilter darf dabei nicht noch einmal als WHERE laufen --
        # er wurde ja gerade ueber mehrere Tage geprueft.
        try:
            hits, cond_stats = _prefilter_window(opt['prefilter'], win, db_path, uni)
        except RuntimeError as exc:
            steps.append({'key': 'base', 'label': 'Werte mit Simulationsdaten',
                          'before': 0, 'after': 0, 'note': str(exc),
                          'note_key': 'note_error',
                          'note_args': {'db': 'asset_simulation', 'error': exc}})
            return pd.DataFrame(), steps
        df, note, n_total = _latest_rows('', db_path)
    elif lines and needs_pandas(opt['prefilter']):
        # shift/rolling cannot run in SQL: qualify the tickers in pandas, then
        # fetch their latest rows without the prefilter.
        try:
            latest_hits = _prefilter_pandas_latest(opt['prefilter'], db_path, tickers=uni)
        except RuntimeError as exc:
            steps.append({'key': 'base', 'label': 'Werte mit Simulationsdaten',
                          'before': 0, 'after': 0, 'note': str(exc),
                          'note_key': 'note_error',
                          'note_args': {'db': 'asset_simulation', 'error': exc}})
            return pd.DataFrame(), steps
        df, note, n_total = _latest_rows('', db_path)
        if latest_hits is not None and not df.empty:
            df = df[df['ticker'].isin(latest_hits)].copy()
    else:
        # One condition per line, all on the same row: join the lines, since a
        # raw multi-line text is no valid WHERE clause ("(a)\n(b)").
        where = " AND ".join(f"({ln})" for ln in lines) if lines else opt['prefilter']
        df, note, n_total = _latest_rows(where, db_path)
    nk, na = note if isinstance(note, tuple) else ('', {})
    _fb = {'source': '{db}, Stand {date}', 'error': '{db}: {error}',
           'no_db': 'keine Simulationsdatenbank gefunden'}.get(nk, '')
    step('base', 'Werte mit Simulationsdaten', n_total, n_total,
         _fb.format(**na) if _fb else '', f'note_{nk}' if nk else '', **na)
    if hits is not None:
        before = len(df)
        df = df[df['ticker'].isin(hits)].copy()
        df['signal_date'] = df['ticker'].map(hits)
        detail = "/".join(str(n) for _c, n in cond_stats)
        step('prefilter', 'Vorfilter', before, len(df),
             f"{len(lines)} Bedingungen über {win} Bars (je {detail})",
             'note_window', n=len(lines), win=win, detail=detail)
    else:
        step('prefilter', 'Vorfilter', n_total, len(df))
    if df.empty:
        return df.drop(columns=['_rank', '_sec', '_second'], errors='ignore'), steps
    if uni is not None:
        before = len(df)
        df = df[df['ticker'].isin(uni)]
        step('universe', 'Universum', before, len(df),
             ", ".join(opt['universe']))
    if df.empty:
        return df.drop(columns=['_rank', '_sec', '_second'], errors='ignore'), steps

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

    # 3a — Qualitaets-Score. Momentaufnahme aus asset_info (woechentlich im
    # get_asset_info-Lauf gerechnet); die Spalten kommen immer dazu, damit die
    # Tabelle und die Vorsortierung sie zeigen koennen, gefiltert wird nur auf
    # Wunsch.
    if not df.empty:
        df = _apply_quality(df, opt, direction, db_path, step)

    # 3b — Risikoprofil. Erst zuschneiden, dann nach Trend suchen: der Vorteil
    # einer Trendauswahl verdoppelte sich in der Messung (2020-2026), sobald das
    # Universum vorher auf das ruhige Ende beschraenkt war.
    prof_key = str(opt.get('risk_profile') or '')
    if prof_key and not df.empty:
        try:
            from tradinglib import risk_profile as rp
            profile = (rp.resolve(username) if prof_key == 'user'
                       else rp.resolve(name=prof_key))
            before = len(df)
            df = df[rp.apply(df, profile)]
            cut = profile.get('max_atr_pct')
            name = profile['name']
            note = (f"{name}: ATR ≤ {cut * 100:.1f} % vom Kurs" if cut is not None
                    else f"{name}: kein Risikoschnitt")
            step('risk_profile', 'Risikoprofil', before, len(df), note,
                 'note_risk_profile', profile=name,
                 atr=(round(cut * 100, 1) if cut is not None else 0))
        except Exception:
            logger.warning("candidates: Risikoprofil uebersprungen", exc_info=True)

    # 3c — Trendwert. Naehe zum eigenen Rekordhoch, gespiegelt fuer Short.
    min_trend = float(opt.get('min_trend_score') or 0)
    if min_trend > 0 and not df.empty:
        before = len(df)
        if 'trendScore' in df.columns:
            score = pd.to_numeric(df['trendScore'], errors='coerce')
            # 0 heisst "nicht berechnet", nicht "am Boden" -- solche Zeilen
            # duerfen weder durchrutschen noch die Liste leerraeumen.
            known = score.notna() & (score > 0)
            mask = ((score >= min_trend) if direction == 'long'
                    else (score <= 100 - min_trend))
            df = df[known & mask]
            step('trend_score', 'Trendwert', before, len(df),
                 f"trendScore {'≥' if direction == 'long' else '≤'} "
                 f"{min_trend if direction == 'long' else 100 - min_trend:.0f}",
                 'note_trend_score', v=int(min_trend if direction == 'long'
                                           else 100 - min_trend),
                 op='≥' if direction == 'long' else '≤')
        else:
            step('trend_score', 'Trendwert', before, len(df),
                 'Spalte fehlt — übersprungen', 'note_cols_missing')

    # 3d — Wochentrend. Strukturell, nicht als Momentum.
    #
    # 4PS-Phase 3/4 heisst: eingestiegen und gehalten ueber einem STEIGENDEN
    # 40-Wochen-Durchschnitt -- die Phase ueberlebt einen Ruecksetzer, ein
    # Momentum-Mass nicht. Fuer Short gibt es dazu kein Gegenstueck: `fps_phase
    # = 0` bedeutet "keine bewaehrte Historie" und nicht "faellt". Deshalb faellt
    # Short auf das strukturelle Mass zurueck, sichtbar im Trichter.
    mode = opt['trend_mode'] if opt['trend_mode'] in TREND_MODES else 'fps'
    if mode != 'off' and not df.empty:
        note = ''
        nkey = ''
        if mode in FPS_PHASES and direction == 'short':
            mode = 'structure'
            note, nkey = ('4PS kennt keinen fallenden Trend → Struktur',
                          'note_no_short_fps')
        if mode in FPS_PHASES and 'fps_phase' in df.columns:
            want = FPS_PHASES[mode]
            mask = pd.to_numeric(df['fps_phase'], errors='coerce').isin(want)
            label = '4PS-Phase ' + "/".join(str(x) for x in want)
            note, nkey = note or label, nkey or f'note_trend_{mode}'
        elif {'sma50', 'sma200'} <= set(df.columns):
            a = pd.to_numeric(df['sma50'], errors='coerce')
            b = pd.to_numeric(df['sma200'], errors='coerce')
            mask = (a > b) if direction == 'long' else (a < b)
            # Ein Spaltenvergleich liest sich in jeder Sprache gleich -- nur die
            # Vorbemerkung fuer Short braucht eine Uebersetzung.
            note = note or ('sma50 > sma200' if direction == 'long'
                            else 'sma50 < sma200')
        else:
            mask = None
            note, nkey = 'Spalten fehlen — übersprungen', 'note_cols_missing'
        before = len(df)
        if mask is not None:
            df = df[mask.fillna(False)]
        step('trend', 'Wochentrend passt zur Richtung', before, len(df),
             note, nkey)

    # 3c — Testet das Level? Zeitreihe, aber aus derselben Datenbank.
    #
    # Bewusst frueh: der Schritt ist billig (eine Abfrage) und sehr selektiv --
    # gemessen 201 von 5513. Damit schrumpft die Menge, bevor der teure
    # Relativstaerke-Schritt Kursreihen laedt.
    df['level_test'] = ''
    df['level_gap'] = float('nan')
    if opt['retest'] and not df.empty:
        hits = _level_test(df['ticker'].tolist(), direction, db_path,
                           window=int(opt['retest_window']),
                           tol=float(opt['retest_tol']),
                           lift=float(opt['retest_lift']))
        before = len(df)
        df['level_test'] = df['ticker'].map(lambda t: (hits.get(t) or ('', 0))[0])
        df['level_gap'] = df['ticker'].map(
            lambda t: (hits.get(t) or ('', float('nan')))[1])
        df = df[df['level_test'] != '']
        n_re = int((df['level_test'] == 'retest').sum())
        step('retest', 'Testet Unterstützung/Widerstand', before, len(df),
             f"{n_re}× Re-Test, {len(df) - n_re}× erster Test", 'note_retest',
             retest=n_re, test=len(df) - n_re)
    elif not opt['retest']:
        # Abgeschaltet stehenlassen statt die Zeile verschwinden zu lassen --
        # sonst sieht der Trichter aus, als haette es den Schritt nie gegeben.
        step('retest', 'Testet Unterstützung/Widerstand', len(df), len(df),
             'abgeschaltet', 'note_off')

    # 3e — Regressionskanal (sdc). After the cheap filters on purpose: with a
    # length other than the stored 10 years the channel is fitted live per
    # ticker, so it should see as few rows as possible.
    if opt.get('use_sdc') and not df.empty:
        df = _apply_sdc(df, opt, direction, db_path, step)

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
            # sign dreht das Mass: long sucht Zufluss, short Abfluss.
            keep = {etf for etf, v in strength.items()
                    if sign * v >= opt['min_sector_rsc']}
            df = df[df['sector_etf'].isin(keep)]
            step('sector', 'Sektor mit Zufluss', before, len(df),
                 f"{len(keep)} von {len(strength)} Sektoren", 'note_sectors',
                 n=len(keep), total=len(strength))
        else:
            step('sector', 'Sektor mit Zufluss', len(df), len(df),
                 'keine Sektordaten', 'note_no_sector_data')
    df['sector_rsc'] = df['sector_etf'].map(strength)

    # 5 — Vorauswahl nach Staerke, BEVOR es teuer wird.
    #
    # Der naechste Schritt laedt Kursreihen je Titel. Ohne diese Kappung liefe er
    # ueber alles, was der Vorfilter durchlaesst -- ein paar tausend Downloads
    # fuer eine Liste, von der am Ende fuenfzehn Zeilen uebrig bleiben.
    rank_col = opt['rank_col'] if opt['rank_col'] in RANK_COLUMNS else 'sortino'
    if rank_col not in df.columns:
        rank_col = 'sortino'
    pool = int(opt['pool_n'] or 0)
    if not df.empty and rank_col in df.columns:
        df['_rank'] = sign * pd.to_numeric(df[rank_col], errors='coerce')
        df['_sec'] = sign * pd.to_numeric(df['sector_rsc'], errors='coerce')
        df = df.sort_values(['_sec', '_rank'], ascending=[False, False],
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
             f"sortiert nach {rank_col}", 'note_sorted_by', col=rank_col)

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
            df = df[sign * df['RSC_vs_ETF'] >= opt['min_rsc']]
            step('rsc', 'Schlägt den eigenen Sektor', before, len(df),
                 '' if direction == 'long' else 'gesucht wird Rückstand',
                 '' if direction == 'long' else 'note_short_lag')
        except Exception:
            logger.warning("candidates: RSC-Anreicherung fehlgeschlagen", exc_info=True)
    elif not opt['use_rsc']:
        step('rsc', 'Schlägt den eigenen Sektor', len(df), len(df),
             'abgeschaltet', 'note_off')

    # 7 — Bestenliste: erst der Sektor, dann der Titel darin.
    # Ohne Relativstaerke ist die zweite Sortierstufe leer -- dann entscheidet
    # innerhalb des Sektors die gewaehlte Kennzahl.
    if not df.empty:
        df['_rank'] = sign * pd.to_numeric(df[rank_col], errors='coerce')
        df['_sec'] = sign * pd.to_numeric(df['sector_rsc'], errors='coerce')
        df['_second'] = (sign * pd.to_numeric(df['RSC_vs_ETF'], errors='coerce')
                         if opt['use_rsc'] else df['_rank'])
        df = df.sort_values(['_sec', '_second'],
                            ascending=[False, False], na_position='last')
        before = len(df)
        mps = int(opt['max_per_sector'] or 0)
        note, nkey = '', ''
        if mps:
            df = df.groupby('sector', group_keys=False, sort=False).head(mps)
            df = df.sort_values(['_sec', '_second'],
                                ascending=[False, False], na_position='last')
            note, nkey = f"höchstens {mps} je Sektor", 'note_max_per_sector'
        if opt['top_n']:
            df = df.head(int(opt['top_n']))
        step('best', 'Bestenliste', before, len(df), note, nkey, n=mps)

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
                # Der Signalpfad ist long-only: ein Verkaufsmarker entsteht nur,
                # solange eine Long-Position offen ist. Fuer Short gibt es
                # deshalb kein eigenes Einstiegssignal -- als Zustimmung zaehlt,
                # dass die Long-Strategie ausgestiegen ist ('reduce'). Das ist
                # ein schwaecheres Argument als ein echtes Short-Signal, und es
                # steht so im Trichter.
                want = 'add' if direction == 'long' else 'reduce'
                before = len(df)
                n_hit = int((df['last_signal'] == want).sum())
                if opt['only_add']:
                    df = df[df['last_signal'] == want]
                label = ('Letztes Signal war ein Einstieg' if direction == 'long'
                         else 'Long-Strategie ist ausgestiegen')
                step('signal', label, before, len(df),
                     f"Zeitebene {tf}, {n_hit} von {before}", 'note_signal',
                     tf=tf, n=n_hit, total=before)
        except Exception:
            logger.warning("candidates: Signalberechnung fehlgeschlagen", exc_info=True)

    return df.drop(columns=['_rank', '_sec', '_second'], errors='ignore'), steps
