"""Entfernt Stundenbalken, die als Kopie in Boersen-Ortszeit gespeichert wurden.

Hintergrund ist derselbe Fehler, den repair_intraday_tz.py behandelt:
``market_data.download`` uebersprang die Zeitzonen-Normalisierung, ab yfinance
1.5.2 lieferte dieselbe Funktion Boersen-Ortszeit, und ``save_ohlc_to_sql``
verliert per strftime die tz-Angabe.

WARUM DIESES ZWEITE SKRIPT
repair_intraday_tz.py setzt ``CLEAN_BEFORE = '2026-08-01'`` -- alles davor gelte
als sicher UTC. Das trifft nicht zu: der Samstagsjob YAHOO_ALL holt ``60m:2y``,
also zwei Jahre Stundendaten auf einmal. Lief er im defekten Fenster, hat er die
gesamte Historie mit ortszeit-gestempelten Balken ergaenzt. Gemessen reichen die
Kopien bis August 2024 zurueck (Haeufungspunkt: 16 von 48 Tickern einer
Stichprobe beginnen dort, genau zwei Jahre vor dem defekten Fenster).

WORAN SIE ERKANNT WERDEN
Ein Balken b ist eine Kopie, wenn es einen Balken a gibt mit
  * identischem OHLC,
  * b.Date = a.Date + tz-Versatz(a.Date) -- der Versatz kommt aus
    asset_info.exchangeTimezoneName und wird JE DATUM bestimmt, traegt also
    Sommerzeit und jede Boerse mit (Berlin +1/+2, New York -4/-5),
  * b hat eine echte Spanne (High > Low), und
  * a unterscheidet sich von seinem eigenen Vorgaenger.
Die letzten beiden Bedingungen schliessen illiquide Werte aus, bei denen ueber
Stunden identische OHLC stehen und ein Treffer rein zufaellig entstuende.

Verifiziert an ^GDAXI: 317 Kopien mit +1h, ausschliesslich Okt-Maerz, und 993
mit +2h, ausschliesslich Maerz-Okt -- exakt komplementaer zur Sommerzeit.

WAS NICHT GELOESCHT WIRD
Nur b, nie a. Der echte UTC-Balken bleibt also immer stehen; die Loeschung ist
per Konstruktion verlustfrei. Zusaetzlich wird jede geloeschte Zeile in
h60_local_copies_removed.db gesichert -- 60m-Daten liefert Yahoo nur 60 Tage
zurueck, ein Fehlgriff waere sonst unwiederbringlich.

GRENZEN -- VOR DEM SCHREIBEN LESEN
* Der Trockenlauf ueber den ganzen Bestand meldet 18,4 Mio. Zeilen in 8.528
  Tickern, also rund ein Drittel aller Stundendaten. Das ist weit mehr als ein
  begrenztes Schadensfenster und bei US-Werten reicht es bis 2023-05 zurueck --
  bis zum Beginn der Tabelle, lange vor dem yfinance-Upgrade. Woher die Kopien
  dort stammen, ist NICHT geklaert.
* Die Regel ist UNVOLLSTAENDIG. Sie verlangt identisches OHLC; wo sich die
  beiden Fassungen unterscheiden (z. B. weil die Schlussauktion in einem der
  Abrufe anders eingerechnet wurde), bleibt die Ortszeit-Zeile stehen. Geprueft
  an SAP.DE: nach der Bereinigung steht dort weiterhin ein Balken um 17:30 UTC
  (= 19:30 Berlin), also nach dem Xetra-Schluss.
* Verifiziert wurde, dass die verbleibende Reihe stimmig ist: AAPL behaelt
  Vorboerse (08:00-13:00 UTC), regulaere Sitzung (13:30-19:30) und Nachboerse
  (20:00-23:00), im Winter um eine Stunde verschoben. Das gilt fuer die
  geprueften Tage, nicht bewiesen fuer alle.

Deshalb: erst einzelne Ticker bereinigen und das Ergebnis im Chart ansehen,
nicht den ganzen Bestand auf einmal.

    python repair_h60_local_copies.py                       # Trockenlauf, alle
    python repair_h60_local_copies.py /tickers:^GDAXI,SAP.DE
    python repair_h60_local_copies.py /apply                # schreiben
"""
import glob
import logging
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tradinglib import cli, logging_config
from tradinglib.tools import Tools, open_db

logger = logging.getLogger(__name__)

TABLE = 'h60_data'
UNDO_DB = 'h60_local_copies_removed.db'
# Kopien mit weniger als so vielen Treffern je Ticker werden ignoriert: einzelne
# Zufallstreffer gibt es auch bei sauberen Reihen (gemessen: Ausreisser mit
# genau 1 Treffer in 2021-2023, lange vor dem defekten Fenster).
MIN_HITS = 5


def _tz_map():
    """Boersen-Zeitzone je Ticker aus asset_info."""
    path = Tools().get_path(path='database', file_name='asset_info.db')
    out = {}
    try:
        with open_db(path, readonly=True) as conn:
            for tk, tz in conn.execute(
                    "SELECT ticker, exchangeTimezoneName FROM asset_info "
                    "WHERE exchangeTimezoneName IS NOT NULL "
                    "AND TRIM(exchangeTimezoneName) <> ''"):
                out[tk] = tz
    except Exception as e:
        logger.error('asset_info nicht lesbar: %s', e)
    return out


def _offset_hours(zone, when):
    """tz-Versatz in Stunden fuer genau dieses Datum (traegt Sommerzeit mit)."""
    try:
        return ZoneInfo(zone).utcoffset(when).total_seconds() / 3600.0
    except Exception:
        return None


def find_copies(rows, zone):
    """Kopien in einer nach Datum sortierten Zeilenliste finden.

    rows: [(Date, Open, High, Low, Close, Volume)] aufsteigend.
    Rueckgabe: Liste der Zeilen, die zu loeschen sind.
    """
    nach_datum = {}
    for r in rows:
        try:
            nach_datum[datetime.strptime(r[0][:19], '%Y-%m-%d %H:%M:%S')] = r
        except (ValueError, TypeError):
            continue
    treffer = []
    for ts, b in nach_datum.items():
        if b[2] is None or b[3] is None or not (b[2] > b[3]):
            continue                      # keine echte Spanne
        off = _offset_hours(zone, ts)
        if not off:                       # None oder 0 -> nichts zu tun
            continue
        a_ts = ts - timedelta(hours=off)
        a = nach_datum.get(a_ts)
        if a is None or a[1:5] != b[1:5]:
            continue
        # a darf nicht Teil einer Flachstrecke sein
        vor = nach_datum.get(a_ts - timedelta(hours=1))
        if vor is not None and vor[1:5] == a[1:5]:
            continue
        treffer.append(b)
    return treffer


def _undo_conn(db_dir):
    path = os.path.join(db_dir, UNDO_DB)
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE IF NOT EXISTS removed ('
                 'ticker TEXT, Date TEXT, Open REAL, High REAL, Low REAL, '
                 'Close REAL, Volume INTEGER, removed_at TEXT)')
    return conn, path


def main():
    args = cli.parse_args(sys.argv)
    logging_config.configure_logging(to_console=args.get('log_to_console', True))
    apply = bool(args.get('apply'))
    nur = [t.strip() for t in str(args.get('tickers') or '').split(',') if t.strip()]

    db_dir = os.path.dirname(Tools().get_path(path='database', file_name='asset_info.db'))
    tzmap = _tz_map()
    dateien = sorted(glob.glob(os.path.join(db_dir, 'yf_*.db')))
    if nur:
        dateien = [os.path.join(db_dir, f'yf_{t}.db') for t in nur]

    undo = undo_path = None
    if apply:
        undo, undo_path = _undo_conn(db_dir)

    gesamt = Counter()
    betroffen = []
    for i, p in enumerate(dateien, 1):
        tk = os.path.basename(p)[3:-3]
        zone = tzmap.get(tk)
        if not zone:
            gesamt['ohne_zeitzone'] += 1
            continue
        if not os.path.exists(p):
            gesamt['datei_fehlt'] += 1
            continue
        try:
            conn = sqlite3.connect(p)
            cur = conn.cursor()
            if not cur.execute("SELECT name FROM sqlite_master WHERE type='table' "
                               "AND name=?", (TABLE,)).fetchone():
                conn.close(); gesamt['ohne_h60'] += 1; continue
            rows = cur.execute(f'SELECT Date, Open, High, Low, Close, Volume '
                               f'FROM {TABLE} ORDER BY Date').fetchall()
            treffer = find_copies(rows, zone)
            if len(treffer) < MIN_HITS:
                conn.close()
                if treffer:
                    gesamt['unter_schwelle'] += 1
                continue
            gesamt['ticker'] += 1
            gesamt['zeilen'] += len(treffer)
            betroffen.append((tk, len(rows), len(treffer),
                              min(t[0] for t in treffer)[:7],
                              max(t[0] for t in treffer)[:7]))
            if apply:
                jetzt = datetime.now().isoformat(timespec='seconds')
                undo.executemany(
                    'INSERT INTO removed (ticker, Date, Open, High, Low, Close, '
                    'Volume, removed_at) VALUES (?,?,?,?,?,?,?,?)',
                    [(tk, *t, jetzt) for t in treffer])
                undo.commit()
                cur.executemany(f'DELETE FROM {TABLE} WHERE Date = ?',
                                [(t[0],) for t in treffer])
                conn.commit()
            conn.close()
        except Exception as e:
            logger.warning('%s: %s', tk, e)
            gesamt['fehler'] += 1
        if i % 500 == 0:
            logger.info('%d/%d geprueft, %d Ticker betroffen, %d Zeilen',
                        i, len(dateien), gesamt['ticker'], gesamt['zeilen'])

    if undo:
        undo.close()

    modus = 'GESCHRIEBEN' if apply else 'TROCKENLAUF (nichts geaendert)'
    print(f'\n=== {modus} ===')
    print(f'geprueft            : {len(dateien)} Dateien')
    print(f'ohne Zeitzone       : {gesamt["ohne_zeitzone"]}')
    print(f'ohne h60_data       : {gesamt["ohne_h60"]}')
    print(f'unter Schwelle ({MIN_HITS}) : {gesamt["unter_schwelle"]}')
    print(f'Fehler              : {gesamt["fehler"]}')
    print(f'BETROFFEN           : {gesamt["ticker"]} Ticker, {gesamt["zeilen"]:,} Zeilen')
    if apply:
        print(f'Sicherung           : {undo_path}')
    else:
        print('\nMit /apply schreiben. Jede geloeschte Zeile wird vorher in '
              f'{UNDO_DB} gesichert.')
    if betroffen:
        print(f'\n{"Ticker":14} {"h60":>7} {"Kopien":>7} {"Anteil":>7}  Zeitraum')
        for tk, n, k, a, b in sorted(betroffen, key=lambda x: -x[2])[:25]:
            print(f'{tk:14} {n:>7} {k:>7} {k/n*100:>6.1f}%  {a} .. {b}')
        if len(betroffen) > 25:
            print(f'... und {len(betroffen)-25} weitere')


if __name__ == '__main__':
    main()
