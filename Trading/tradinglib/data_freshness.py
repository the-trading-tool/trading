"""Frische-Pruefung der lokalen OHLC-Daten.

Beantwortet eine Frage, die sonst erst auffaellt, wenn die Charts komisch
aussehen: **wie alt ist die juengste Kerze je Intervall — und ist das
ungewoehnlich?**

Das Schwierige daran ist nicht das Messen, sondern das Vergleichen. Eine
60m-Kerze von SAP.DE ist sonntagnachts voellig zu Recht 40 Stunden alt, eine
BTC-Kerze nach drei Stunden dagegen schon auffaellig. Feste Schwellen wuerden
entweder jedes Wochenende Fehlalarm schlagen oder echte Ausfaelle verschlafen.

Deshalb **kalibriert sich die Schwelle aus den Daten selbst**: Fuer jeden
Ticker wird aus den letzten Wochen die Verteilung der Abstaende zwischen
aufeinanderfolgenden Kerzen bestimmt — inklusive der Nacht- und
Wochenendluecken. Das 95. Perzentil dieser Abstaende ist die groesste Pause,
die fuer dieses Papier normal ist. Erst wenn die aktuelle Pause diese Marke
deutlich uebersteigt, gilt der Ticker als veraltet. Damit sind Handelszeiten,
Feiertage und Zeitzonen automatisch beruecksichtigt, ohne einen einzigen
Boersenkalender zu pflegen.

Bewertet wird die **Stichprobe als Ganzes** (Median), nicht der Einzelticker:
ein einzelnes totes Papier ist Rauschen, ein Feed-Ausfall trifft alle.

CLI: siehe ``check_freshness.py`` im Projektwurzelverzeichnis.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

import numpy as np
import pandas as pd

from tradinglib import tools as ts
from tradinglib.tools import open_db
from tradinglib.utils import DataUtils

logger = logging.getLogger(__name__)

# Intervalle, die geprueft werden -> Tabelle in yf_<ticker>.db
INTERVALS = ('1d', '60m')

# Wie viel Historie fuer die Kalibrierung herangezogen wird.
_CALIB_BARS = 400
# Perzentil der normalen Abstaende, das als "groesste uebliche Pause" gilt.
# 99 statt 95, weil die Wochenendluecke sonst herausfaellt: bei ~9 Kerzen pro
# Handelstag liegen die Wochenenden in den obersten ~2 % der Abstaende. Mit 95
# ergaben sich fuer Aktien Schwellen um 24 h -- jeder Sonntag haette Alarm
# geschlagen. Gemessen (60m): SAP.DE p95 15,5 h gegenueber p99 63,5 h; Krypto
# bleibt bei 1,0 h und damit der empfindliche Zeuge.
_CALIB_PCT = 99
# Sicherheitsfaktor darauf, damit normale Schwankung keinen Alarm ausloest.
_SLACK = 1.5
# Untergrenzen, damit die Schwelle bei sehr dichten Daten nicht absurd klein wird.
_MIN_THRESHOLD_H = {'1d': 36.0, '60m': 4.0}


def _table(interval: str) -> str:
    return DataUtils.get_table_name(interval)


def _load_dates(ticker: str, interval: str, limit: int = _CALIB_BARS) -> pd.Series:
    """Zeitstempel der juengsten *limit* Kerzen, aufsteigend. Leer bei Problemen."""
    path = ts.Tools().get_path(path='database', file_name=f'yf_{ticker}.db')
    if not os.path.exists(path):
        return pd.Series(dtype='datetime64[ns]')
    tbl = _table(interval)
    try:
        with open_db(path, readonly=True) as conn:
            rows = conn.execute(
                f'SELECT Date FROM {tbl} ORDER BY Date DESC LIMIT ?', (limit,)
            ).fetchall()
    except Exception as exc:
        logger.debug('freshness %s/%s: %s', ticker, interval, exc)
        return pd.Series(dtype='datetime64[ns]')
    s = pd.to_datetime(pd.Series([r[0] for r in rows]), errors='coerce').dropna()
    return s.sort_values().reset_index(drop=True)


def _normal_gap_hours(dates: pd.Series) -> float | None:
    """Groesste *uebliche* Pause zwischen zwei Kerzen, in Stunden.

    Das 95. Perzentil der beobachteten Abstaende — es enthaelt die Nacht- und
    Wochenendluecken und ist damit genau das, was fuer dieses Papier normal
    ist. None, wenn zu wenig Historie vorliegt.
    """
    if len(dates) < 20:
        return None
    gaps = dates.diff().dropna().dt.total_seconds() / 3600.0
    gaps = gaps[gaps > 0]
    if gaps.empty:
        return None
    return float(np.percentile(gaps, _CALIB_PCT))


# Nennlaenge einer Kerze in Stunden — Basis fuer die Einordnung durchgehend
# vs. sitzungsgebunden.
_BAR_HOURS = {'1d': 24.0, '60m': 1.0, '1h': 1.0, '4h': 4.0, '1m': 1 / 60}
# Ein Papier gilt als durchgehend handelbar, wenn seine groesste uebliche
# Pause kaum ueber einer Kerzenlaenge liegt (Krypto). Sitzungsgebundene Werte
# haben dort die Uebernacht-Luecke stehen und liegen weit darueber.
_CONTINUOUS_FACTOR = 2.5


def is_continuous(normal_gap_h: float, interval: str) -> bool:
    """Handelt das Papier durchgehend (24/7)? Aus den Daten erkannt, nicht geraten."""
    return normal_gap_h <= _BAR_HOURS.get(interval, 1.0) * _CONTINUOUS_FACTOR


def check_ticker(ticker: str, interval: str, now: dt.datetime | None = None) -> dict | None:
    """Frische eines Tickers in einem Intervall. None, wenn nicht bewertbar."""
    now = now or dt.datetime.now()
    dates = _load_dates(ticker, interval)
    if dates.empty:
        return None
    last = dates.iloc[-1]
    age_h = (now - last.to_pydatetime()).total_seconds() / 3600.0
    normal = _normal_gap_hours(dates)
    if normal is None:
        return None
    threshold = max(normal * _SLACK, _MIN_THRESHOLD_H.get(interval, 4.0))
    return {
        'ticker': ticker,
        'interval': interval,
        'last': last,
        'age_h': round(age_h, 1),
        'normal_gap_h': round(normal, 1),
        'threshold_h': round(threshold, 1),
        'stale': age_h > threshold,
        'continuous': is_continuous(normal, interval),
        'bars': len(dates),
    }


def sample_tickers(limit: int = 40) -> list:
    """Repraesentative Stichprobe: Mitglieder echter Boersenindizes plus Krypto.

    Krypto ist bewusst dabei — es handelt 24/7 und unterscheidet damit einen
    echten Feed-Ausfall von blossen Handelspausen.
    """
    out: list = []
    try:
        db = ts.Tools().get_path(path='database', file_name='yf_tickers.db')
        with open_db(db, readonly=True) as conn:
            rows = conn.execute("""
                SELECT s.Ticker FROM stocks s
                JOIN stock_indices si ON si.stock_id = s.id
                JOIN indices i ON i.id = si.index_id
                WHERE i.name LIKE '^%'
                GROUP BY s.Ticker ORDER BY s.Ticker LIMIT ?
            """, (limit,)).fetchall()
        out = [r[0] for r in rows if r and r[0]]
    except Exception as exc:
        logger.debug('freshness: Ticker-Stichprobe nicht ladbar: %s', exc)
    for extra in ('BTC-EUR', 'ETH-EUR'):
        if extra not in out:
            out.append(extra)
    return out


def check(tickers: list | None = None, intervals=INTERVALS,
          limit: int = 40, now: dt.datetime | None = None) -> dict:
    """Frische-Bericht ueber eine Stichprobe.

    Rueckgabe je Intervall:
      n, median_age_h, max_age_h, stale (Anzahl), stale_pct, verdict, rows
    ``verdict``: 'ok' | 'delayed' | 'stale'. Bewertet wird die Stichprobe --
    ein einzelner toter Ticker faellt nicht ins Gewicht, ein Feed-Ausfall schon.
    """
    now = now or dt.datetime.now()
    tickers = tickers or sample_tickers(limit)
    report: dict = {'checked_at': now.isoformat(timespec='seconds'),
                    'n_tickers': len(tickers), 'intervals': {}}

    for interval in intervals:
        rows = [r for r in (check_ticker(t, interval, now) for t in tickers) if r]
        if not rows:
            report['intervals'][interval] = {
                'n': 0, 'verdict': 'unknown',
                'note': 'keine bewertbaren Ticker (keine Daten oder zu kurze Historie)'}
            continue
        ages = pd.Series([r['age_h'] for r in rows])
        n_stale = sum(1 for r in rows if r['stale'])
        pct = n_stale / len(rows) * 100

        # Die beiden Gruppen taugen fuer verschiedene Fragen und duerfen
        # deshalb nicht in einen Topf:
        #
        #   durchgehend (Krypto) -- empfindlicher Feed-Zeuge. Ohne Handelspausen
        #     liegt die Schwelle bei wenigen Stunden, eine Verzoegerung faellt
        #     sofort auf. Wenige Ticker, aber die aussagekraeftigen.
        #   sitzungsgebunden (Aktien) -- ihre Schwelle enthaelt die Uebernacht-
        #     luecke und liegt bei ~24 h. Sie schlagen erst bei mehrtaegigen
        #     Ausfaellen an, also wenn ein Job ganz stillsteht.
        #
        # Nach Anteilen ueber alle Ticker zu urteilen wuerde den Feed-Zeugen
        # ueberstimmen: 2 veraltete Krypto-Werte unter 42 sind 5 % und saehen
        # nach Rauschen aus -- sind aber genau das Signal.
        cont = [r for r in rows if r['continuous']]
        sess = [r for r in rows if not r['continuous']]
        cont_stale = sum(1 for r in cont if r['stale'])
        sess_stale = sum(1 for r in sess if r['stale'])
        sess_pct = (sess_stale / len(sess) * 100) if sess else 0.0

        verdict = 'ok'
        reason = ''
        if sess and sess_pct >= 50:
            verdict, reason = 'stale', (
                f'{sess_stale}/{len(sess)} sitzungsgebundene Werte ueber ihrer '
                f'Schwelle — sieht nach stehendem Download-Job aus')
        elif cont and cont_stale >= max(1, len(cont) // 2):
            worst = max((r['age_h'] for r in cont if r['stale']), default=0)
            verdict, reason = 'delayed', (
                f'durchgehend handelbare Werte {worst:.1f} h alt '
                f'(normal bis {cont[0]["threshold_h"]:.1f} h) — Feed haengt nach')
        elif sess and sess_pct >= 20:
            verdict, reason = 'delayed', (
                f'{sess_stale}/{len(sess)} sitzungsgebundene Werte ueber ihrer Schwelle')

        report['intervals'][interval] = {
            'n': len(rows),
            'median_age_h': round(float(ages.median()), 1),
            'max_age_h': round(float(ages.max()), 1),
            'stale': n_stale,
            'stale_pct': round(pct, 1),
            'continuous_n': len(cont), 'continuous_stale': cont_stale,
            'session_n': len(sess), 'session_stale': sess_stale,
            'verdict': verdict,
            'reason': reason,
            'rows': sorted(rows, key=lambda r: -r['age_h']),
        }
    return report


def format_report(report: dict, top: int = 5) -> str:
    """Kurzer Textbericht fuer Konsole, Log oder Benachrichtigung."""
    lines = [f"Datenfrische — geprueft {report.get('checked_at')} "
             f"({report.get('n_tickers')} Ticker)"]
    for interval, r in report.get('intervals', {}).items():
        if not r.get('n'):
            lines.append(f"  {interval:<4} — {r.get('note', 'keine Daten')}")
            continue
        mark = {'ok': 'OK', 'delayed': 'VERZUG', 'stale': 'VERALTET'}.get(
            r['verdict'], '?')
        lines.append(
            f"  {interval:<4} {mark:<9} Median {r['median_age_h']:>6.1f} h · "
            f"durchgehend {r['continuous_stale']}/{r['continuous_n']} alt · "
            f"sitzungsgebunden {r['session_stale']}/{r['session_n']} alt")
        if r.get('reason'):
            lines.append(f"        -> {r['reason']}")
        for row in r['rows'][:top]:
            if not row['stale']:
                break
            lines.append(f"        {row['ticker']:<10} {row['age_h']:>6.1f} h "
                         f"(normal bis {row['threshold_h']:.1f} h, "
                         f"letzte {row['last']:%Y-%m-%d %H:%M})")
    return "\n".join(lines)


def worst_verdict(report: dict) -> str:
    """Schlechtestes Urteil ueber alle Intervalle."""
    order = {'ok': 0, 'unknown': 1, 'delayed': 2, 'stale': 3}
    worst = 'ok'
    for r in report.get('intervals', {}).values():
        v = r.get('verdict', 'unknown')
        if order.get(v, 0) > order.get(worst, 0):
            worst = v
    return worst
