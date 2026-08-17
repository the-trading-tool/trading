"""Die min_data-Umrechnung aus repair_intraday_tz zuruecknehmen.

Warum: die Referenz-Startzeit wurde aus ALLEN Tagen vor dem Bruch gebildet.
Bei Tickern, deren Sammel-Zeitfenster sich frueher einmal geaendert hat, ergab
das eine unpassende Referenz -- CALX etwa bekam 04:00 statt 08:00, und die
August-Tage wurden dadurch in die falsche Richtung verschoben (08:00 UTC, also
04:00 New York vorboerslich, wurde zu 04:00 UTC = Mitternacht New York).
Gemessen an den frisch geholten Stundendaten betraf das rund ein Drittel der
Ticker.

Zurueckgenommen werden nur die Verschiebungen (action='shift'). Die verworfenen
Tage (action='drop') bleiben verworfen -- sie waren gemischt und werden von den
laufenden Jobs sauber nachgeholt; sie zurueckzuholen brachte die falschen
Zeilen wieder herein.

    python rollback_intraday_tz.py                 # Trockenlauf
    python rollback_intraday_tz.py /apply
"""
import glob
import logging
import os
import sys

import pandas as pd

from tradinglib import cli, logging_config
from tradinglib.tools import Tools, open_db
from repair_intraday_tz import _tz_map, _offset_hours

logger = logging.getLogger(__name__)


def main():
    args = cli.parse_args()
    logging_config.configure_logging(to_console=args.get('log_to_console', True),
                                     level=args.get('log_level', 'INFO'),
                                     logfile=args.get('log_file', None))
    apply = bool(args.get('apply'))

    db_dir = os.path.dirname(Tools().get_path(path='database',
                                              file_name='asset_info.db'))
    tzmap = _tz_map()
    backups = sorted(glob.glob(os.path.join(db_dir, 'intraday_tz_backup_*.db')))
    if not backups:
        print("keine Sicherung gefunden")
        return
    print("Sicherungen:", ", ".join(os.path.basename(b) for b in backups))

    # Alle Sicherungen einlesen; spaetere gewinnen bei gleichem Schluessel.
    rows = {}
    for path in backups:
        with open_db(path, readonly=True) as conn:
            for r in conn.execute(
                    "SELECT ticker, tbl, Date, Open, High, Low, Close, Volume "
                    "FROM backup WHERE action='shift'"):
                rows[(r[0], r[1], r[2])] = r

    by_ticker = {}
    for (tk, tbl, _d), r in rows.items():
        by_ticker.setdefault((tk, tbl), []).append(r)

    print(f"zurueckzunehmende Zeilen: {len(rows):,} bei {len(by_ticker)} "
          f"Ticker/Tabelle-Paaren")
    if not apply:
        print("\nTrockenlauf — nichts geaendert. Mit /apply ausfuehren.")
        return

    restored = removed = 0
    for (tk, tbl), items in by_ticker.items():
        safe = "".join(ch if ch.isalnum() or ch in "-_.^=" else "_" for ch in tk)
        path = os.path.join(db_dir, f"yf_{safe}.db")
        if not os.path.exists(path):
            continue
        tz_name = tzmap.get(tk)
        conn = open_db(path)
        try:
            # ZWEI getrennte Durchgaenge, und zwar zwingend in dieser Reihenfolge.
            # Loeschen und Einfuegen je Zeile abwechselnd zerstoert den Bestand:
            # die verschobene Marke einer Zeile ist das Original einer anderen
            # (09:02 minus 2h = 07:02, und 07:02 ist selbst ein Original), das
            # Loeschen holt also gerade wiederhergestellte Zeilen wieder weg.
            moved_dates = []
            for r in items:
                orig = r[2]
                off = _offset_hours(tz_name, orig[:10]) if tz_name else 0
                if not off:
                    continue
                try:
                    moved_dates.append(
                        (pd.Timestamp(orig) - pd.Timedelta(hours=off)
                         ).strftime('%Y-%m-%d %H:%M:%S'))
                except Exception:
                    pass
            for i in range(0, len(moved_dates), 500):
                chunk = moved_dates[i:i + 500]
                conn.execute(
                    f"DELETE FROM {tbl} WHERE Date IN "
                    f"({','.join('?' * len(chunk))})", chunk)
            removed += len(moved_dates)

            conn.executemany(
                f"INSERT OR REPLACE INTO {tbl} "
                f"(Date, Open, High, Low, Close, Volume) VALUES (?,?,?,?,?,?)",
                [(r[2], r[3], r[4], r[5], r[6], r[7]) for r in items])
            restored += len(items)
            conn.commit()
        finally:
            conn.close()

    print(f"\nwiederhergestellt: {restored:,} Zeilen")
    print(f"entfernt (falsch verschoben): {removed:,} Zeilen")


if __name__ == '__main__':
    main()
