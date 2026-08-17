"""Intraday-Zeitstempel reparieren, die in Boersen-Ortszeit gespeichert wurden.

Hintergrund: ``market_data.download`` hat die Zeitzonen-Normalisierung wegen
eines verdeckten UnboundLocalError uebersprungen (siehe Modulkopf dort). Solange
yfinance aus ``download()`` UTC lieferte, blieb das folgenlos; ab Version 1.5.2
gibt dieselbe Funktion Boersen-Ortszeit zurueck. ``save_ohlc_to_sql`` schreibt
den Zeitstempel per strftime und verliert dabei die tz-Angabe -- seither steht
Ortszeit in ``min_data`` und ``h60_data``, und die Anzeige rechnet die Zeitzone
ein zweites Mal drauf.

Geloescht wird nichts, was sich nicht wiederbeschaffen laesst: Yahoo liefert 1m
nur sieben Tage und 60m nur 60 Tage zurueck, die Tabellen reichen aber Jahre
zurueck (^GDAXI: 370k Minutenzeilen ab 2024-10, 9.8k Stundenzeilen ab 2023-05).
Betroffene Tage werden darum an Ort und Stelle umgerechnet.

Erkennung datengetrieben je Ticker und Tabelle: aus sauberen Tagen VOR dem Bruch
ergibt sich die uebliche Startzeit. Weicht ein Tag genau um den
Zeitzonen-Versatz seines Datums ab, ist es ein Ortszeit-Tag. Die Zone kommt aus
``asset_info.exchangeTimezoneName``, der Versatz wird je Datum bestimmt --
damit traegt es Sommerzeit und jede Boerse mit.

Sonderfall der letzten Tage: seit dem Fix schreiben die laufenden Jobs wieder
UTC, ohne die alten Zeilen zu ersetzen. Solche Tage enthalten beide Sorten
gemischt. Sie liegen innerhalb des von Yahoo lieferbaren Fensters und werden
darum verworfen statt umgerechnet -- die Jobs fuellen sie sauber nach.

    python repair_intraday_tz.py                      # Trockenlauf, alle Ticker
    python repair_intraday_tz.py /tickers:^GDAXI,AAPL # gezielt
    python repair_intraday_tz.py /apply               # schreiben
"""
import glob
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd

from tradinglib import cli, logging_config
from tradinglib.tools import Tools, open_db

logger = logging.getLogger(__name__)

# Tabelle -> wie weit zurueck die Quelle sie noch liefert. Nur innerhalb dieses
# Fensters darf ein Tag verworfen statt umgerechnet werden.
TABLES = {'min_data': 7, 'h60_data': 60}

# Tabellen, deren betroffenes Fenster komplett verworfen und neu geholt wird.
# Fuer 60m reicht die Quelle 60 Tage zurueck, der Schaden aber nur elf -- eine
# Erkennung ist dort ueberfluessig und waere sogar unsicher: bei US-Werten mit
# vorboerslichen Daten faellt die UTC-Startzeit zufaellig mit der lokalen
# zusammen (^DJI: Juli 09:30 UTC = 05:30 New York, August 09:30 New York).
# NICHT als Vorgabe: das Verwerfen ist ein EINMALIGER Schritt. Laeuft es bei
# jedem Aufruf mit, zerstoert es genau den Anker, den die min_data-Erkennung
# braucht -- und legt das Loch neu an, das der Nachlauf gerade gefuellt hat.
# Nur mit /purge_h60 einschalten.
PURGE_TABLES = set()

# Vor diesem Tag ist die Ablage sicher in UTC -- das yfinance-Upgrade lag am
# 6./7. August. Der Puffer haelt die Referenz sauber.
CLEAN_BEFORE = '2026-08-01'
# Tage, die von den laufenden Jobs ohnehin neu geholt werden. Fuer 1m sind es
# sieben Tage, fuer 60m sechzig -- die kleinere Zahl ist die sichere.
REFETCHABLE_DAYS = 3
# Ab hier sind die Stundendaten frisch geholt und damit als Anker brauchbar.
H60_ANCHOR_FROM = '2026-08-10'
# Zulaessige Abweichung der ersten Tagesuhrzeit gegen die Referenz, in Minuten.
TOLERANCE_MIN = 30


def _tz_map():
    """Boersen-Zeitzone je Ticker aus asset_info."""
    path = Tools().get_path(path='database', file_name='asset_info.db')
    out = {}
    with open_db(path, readonly=True) as conn:
        for tk, tz in conn.execute(
                "SELECT ticker, exchangeTimezoneName FROM asset_info "
                "WHERE exchangeTimezoneName IS NOT NULL "
                "AND TRIM(exchangeTimezoneName) <> ''"):
            out[tk] = str(tz).strip()
    return out


def _priority_map():
    """Bearbeitungsreihenfolge: Indizes zuerst, ETPs zuletzt.

    Die Index-Ticker sind die sichtbarsten Werte der App, die 3.200 ETPs die
    unwichtigsten -- bei einem Lauf ueber alle Kurs-Datenbanken soll das
    Wesentliche zuerst richtig sein.
    """
    path = Tools().get_path(path='database', file_name='yf_tickers.db')
    prio = {}
    try:
        with open_db(path, readonly=True) as conn:
            for group, rank in (('INDEX', 0), ('ETP', 2)):
                for (tk,) in conn.execute(
                        "SELECT s.Ticker FROM stocks s "
                        "JOIN stock_indices si ON s.id = si.stock_id "
                        "JOIN indices i ON si.index_id = i.id "
                        "WHERE UPPER(i.name) = ?", (group,)):
                    # INDEX gewinnt, falls ein Ticker in beiden Gruppen steht.
                    prio[tk] = min(prio.get(tk, rank), rank)
    except Exception:
        logger.debug("Reihenfolge nicht bestimmbar", exc_info=True)
    return prio


def _offset_hours(tz_name, day):
    """Versatz der Boersenzone gegen UTC an diesem Tag, in Stunden."""
    try:
        ts = pd.Timestamp(f'{day} 12:00:00').tz_localize(tz_name)
        return int(round(ts.utcoffset().total_seconds() / 3600.0))
    except Exception:
        return 0


def _day_stats(conn, table):
    """(Tag -> erste Uhrzeit, letzte Uhrzeit, Anzahl) fuer eine Tabelle."""
    rows = conn.execute(
        f"SELECT substr(Date,1,10) d, MIN(substr(Date,12,8)), "
        f"MAX(substr(Date,12,8)), COUNT(*) FROM {table} "
        f"WHERE Date IS NOT NULL AND length(Date) >= 19 GROUP BY d").fetchall()
    # Zeilen ohne verwertbares Datum kommen vor (NULL, abgeschnittene Werte) --
    # sie fliegen hier raus, statt spaeter beim Vergleich zu stolpern.
    return {r[0]: (r[1], r[2], r[3]) for r in rows
            if r[0] and r[1] and r[2]}


def h60_anchor(conn):
    """Korrekte Sitzungs-Startzeit in UTC, aus den frisch geholten Stundendaten.

    Das ist der belastbare Anker. Die frueher benutzte Referenz -- die uebliche
    Startzeit aus der eigenen min_data-Historie -- war es nicht: bei Tickern,
    deren Sammel-Zeitfenster sich einmal geaendert hat, kam ein unpassender Wert
    heraus und die Verschiebung ging in die falsche Richtung. h60_data wurde
    dagegen komplett verworfen und neu geladen, steht also nachweislich in UTC.
    """
    rows = [r[0] for r in conn.execute(
        "SELECT MIN(substr(Date,12,8)) FROM h60_data "
        "WHERE Date >= ? GROUP BY substr(Date,1,10)", (H60_ANCHOR_FROM,))]
    rows = [r for r in rows if r]
    if len(rows) < 2:
        return None
    return _minutes(Counter(rows).most_common(1)[0][0])


def classify(stats, tz_name, today, refetch_days, anchor):
    """Tage einteilen: unveraendert, umzurechnen, verwerfen oder ausklammern.

    Rueckgabe (shift_days, drop_days, skipped_mixed, ref_first). ref_first ist
    None, wenn keine saubere Referenz vorliegt -- dann wird nichts angefasst.

    ``refetch_days`` ist das Fenster, in dem die Quelle den Tag noch liefern
    kann (1m sieben Tage, 60m sechzig). Nur innerhalb davon darf verworfen
    werden.
    """
    if anchor is None:
        return [], [], [], None             # kein Anker -> Finger weg
    ref_first = anchor
    counts = sorted(v[2] for d, v in stats.items() if d < CLEAN_BEFORE)
    if not counts:
        # Ticker ohne Historie vor dem Bruch -- etwa die heute neu aufgenommenen
        # Nikkei-/KOSPI-/Hongkong-Werte. Der Anker traegt trotzdem, nur die
        # Dubletten-Schwelle braucht dann alle vorhandenen Tage als Bezug.
        counts = sorted(v[2] for v in stats.values())
    if not counts:
        return [], [], [], None
    ref_count = counts[len(counts) // 2]

    # Die juengsten Tage werden nicht eingestuft, sondern verworfen. Seit dem
    # Fix schreiben die laufenden Jobs wieder UTC, ohne die alten Zeilen zu
    # ersetzen -- diese Tage enthalten beide Sorten gemischt, und aus einem
    # Zeitstempel allein ist im Ueberlappungsbereich nicht mehr zu erkennen,
    # welche Sorte er ist. Sie liegen innerhalb des von Yahoo lieferbaren
    # Fensters (1m sieben Tage, 60m sechzig), sind also gefahrlos ersetzbar.
    cutoff = (today - timedelta(days=REFETCHABLE_DAYS)).isoformat()
    horizon = (today - timedelta(days=refetch_days)).isoformat()
    shift, drop, mixed = [], [], []
    for day, (first, _last, n) in stats.items():
        if day < CLEAN_BEFORE:
            continue
        if day >= cutoff:
            drop.append((day, 0))
            continue
        off = _offset_hours(tz_name, day)
        if off == 0:
            continue                        # Zone == UTC, nichts zu tun
        # Deutlich mehr Zeilen als ueblich heisst: der Tag enthaelt BEIDE
        # Sorten. Ein Lauf vor der Umstellung hat UTC geschrieben, einer danach
        # Ortszeit -- beide Saetze stehen nebeneinander (^DJI am 3.8.: 652 statt
        # 412 Zeilen). Solche Tage duerfen nicht verschoben werden, das wuerde
        # die korrekten Zeilen darin zerreissen.
        if n > ref_count * 1.3:
            (drop if day >= horizon else mixed).append((day, off))
            continue
        # Nur die Startzeit als Signal. Das Sitzungs-ENDE taugt nicht: es haengt
        # davon ab, wann der Sammler zuletzt lief, und liess saubere Tage als
        # "gemischt" erscheinen -- die waeren dann geloescht worden, obwohl 1m
        # aelter als sieben Tage nicht mehr zu beschaffen ist.
        #
        # Auf die Minute genau zu vergleichen greift zu kurz: der erste Balken
        # schwankt um ein paar Minuten (09:00 vs 09:02). Die Toleranz ist klein
        # gegen jeden Zeitzonen-Versatz (>= 60 Minuten), beide Faelle bleiben
        # also unterscheidbar.
        if abs(_minutes(first) - (ref_first + off * 60)) <= TOLERANCE_MIN:
            shift.append((day, off))
    return shift, drop, mixed, ref_first


def _minutes(hhmmss):
    h, m, s = (list(map(int, str(hhmmss).split(':'))) + [0, 0, 0])[:3]
    return h * 60 + m


def open_backup(db_dir):
    """Sicherung nur der angefassten Zeilen -- die Kurs-Datenbanken selbst zu
    kopieren waeren 9.555 Dateien."""
    path = os.path.join(db_dir, f"intraday_tz_backup_"
                                f"{datetime.now():%Y%m%d-%H%M%S}.db")
    conn = open_db(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS backup (
        ticker TEXT, tbl TEXT, Date TEXT, Open REAL, High REAL,
        Low REAL, Close REAL, Volume REAL, action TEXT)""")
    return conn, path


def repair_table(conn, table, shift, drop, apply=False, backup=None, ticker=''):
    """Umrechnen bzw. verwerfen. Gibt (verschobene, verworfene) Zeilen zurueck."""
    moved = dropped = 0
    for day, off in shift:
        rows = conn.execute(
            f"SELECT Date, Open, High, Low, Close, Volume FROM {table} "
            f"WHERE Date LIKE ?", (day + '%',)).fetchall()
        if not rows:
            continue
        if apply and backup is not None:
            backup.executemany(
                "INSERT INTO backup (ticker,tbl,Date,Open,High,Low,Close,Volume,action) "
                "VALUES (?,?,?,?,?,?,?,?,'shift')",
                [(ticker, table, *r) for r in rows])
        new_rows = []
        for r in rows:
            try:
                ts = pd.Timestamp(r[0]) - pd.Timedelta(hours=off)
            except Exception:
                continue
            new_rows.append((ts.strftime('%Y-%m-%d %H:%M:%S'), *r[1:]))
        if apply and new_rows:
            conn.execute(f"DELETE FROM {table} WHERE Date LIKE ?", (day + '%',))
            # IGNORE: trifft eine umgerechnete Zeile auf eine bereits korrekte,
            # behaelt die bestehende den Vorrang.
            conn.executemany(
                f"INSERT OR IGNORE INTO {table} (Date, Open, High, Low, Close, Volume) "
                f"VALUES (?,?,?,?,?,?)", new_rows)
        moved += len(new_rows)
    for day, _off in drop:
        rows = conn.execute(
            f"SELECT Date, Open, High, Low, Close, Volume FROM {table} "
            f"WHERE Date LIKE ?", (day + '%',)).fetchall()
        if apply and rows:
            if backup is not None:
                backup.executemany(
                    "INSERT INTO backup (ticker,tbl,Date,Open,High,Low,Close,Volume,action) "
                    "VALUES (?,?,?,?,?,?,?,?,'drop')",
                    [(ticker, table, *r) for r in rows])
            conn.execute(f"DELETE FROM {table} WHERE Date LIKE ?", (day + '%',))
        dropped += len(rows)
    return moved, dropped


def main():
    args = cli.parse_args()
    logging_config.configure_logging(to_console=args.get('log_to_console', True),
                                     level=args.get('log_level', 'INFO'),
                                     logfile=args.get('log_file', None))
    apply = bool(args.get('apply'))
    if args.get('purge_h60'):
        PURGE_TABLES.add('h60_data')
    only = args.get('tickers')
    only = {t.strip() for t in str(only).split(',')} if only else None

    db_dir = os.path.dirname(Tools().get_path(path='database',
                                              file_name='asset_info.db'))
    tzmap = _tz_map()
    today = datetime.now().date()

    prio = _priority_map()
    files = sorted(glob.glob(os.path.join(db_dir, 'yf_*.db')),
                   key=lambda p: (prio.get(os.path.basename(p)[3:-3], 1),
                                  os.path.basename(p)))
    tot_moved = tot_dropped = 0
    touched = skipped_no_tz = skipped_no_ref = 0
    samples = []
    tot_mixed = [0]
    mixed_tickers = set()
    backup, backup_path = (open_backup(db_dir) if apply else (None, None))

    for path in files:
        ticker = os.path.basename(path)[3:-3]
        if only and ticker not in only:
            continue
        tz_name = tzmap.get(ticker)
        if not tz_name:
            skipped_no_tz += 1
            continue
        try:
            conn = open_db(path)
        except Exception:
            continue
        try:
            tabs = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            moved = dropped = 0
            no_ref = 0
            for table, refetch_days in TABLES.items():
                if table not in tabs:
                    continue
                stats = _day_stats(conn, table)
                anchor = h60_anchor(conn) if 'h60_data' in tabs else None
                if table in PURGE_TABLES:
                    # Alles ab dem Bruch weg -- die Jobs holen es sauber nach.
                    shift, drop, mixed, ref = [], [
                        (d, 0) for d in stats if d >= CLEAN_BEFORE], [], 0
                else:
                    shift, drop, mixed, ref = classify(stats, tz_name, today,
                                                       refetch_days, anchor)
                if ref is None:
                    no_ref += 1
                    continue
                if mixed:
                    # Zu alt zum Nachladen und nicht sicher trennbar -> gar
                    # nicht anfassen, aber melden.
                    tot_mixed[0] += len(mixed)
                    mixed_tickers.add(ticker)
                m, d = repair_table(conn, table, shift, drop, apply=apply,
                                    backup=backup, ticker=ticker)
                moved += m
                dropped += d
                if (m or d) and len(samples) < 12:
                    samples.append((ticker, table, tz_name, len(shift), len(drop), m, d))
            if apply and (moved or dropped):
                conn.commit()
            if no_ref and not moved and not dropped:
                skipped_no_ref += 1
            if moved or dropped:
                touched += 1
            tot_moved += moved
            tot_dropped += dropped
        finally:
            conn.close()

    if backup is not None:
        backup.commit()
        backup.close()

    print()
    print(f"{'ANGEWENDET' if apply else 'TROCKENLAUF'} — {len(files)} Kurs-Datenbanken geprueft")
    if backup_path:
        print(f"   Sicherung: {backup_path}")
    print(f"   betroffene Ticker          : {touched}")
    print(f"   umgerechnete Zeilen        : {tot_moved:,}")
    print(f"   verworfene Zeilen (holbar) : {tot_dropped:,}")
    print(f"   ohne Boersenzone in asset_info: {skipped_no_tz}")
    print(f"   ohne saubere Referenz         : {skipped_no_ref}")
    print(f"   gemischte Alt-Tage uebersprungen: {tot_mixed[0]} "
          f"(bei {len(mixed_tickers)} Tickern) — enthalten beide Zeitsorten, "
          f"nicht mehr nachladbar, daher unangetastet")
    if samples:
        print("\n   Stichprobe:")
        for tk, tb, tz, ns, nd, m, d in samples:
            print(f"      {tk:<10} {tb:<9} {tz:<20} {ns} Tage umrechnen ({m} Zeilen), "
                  f"{nd} Tage verwerfen ({d} Zeilen)")
    if not apply:
        print("\n   Nichts geaendert. Mit /apply ausfuehren.")


if __name__ == '__main__':
    main()
