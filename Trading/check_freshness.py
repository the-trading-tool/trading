"""Frische-Pruefung der lokalen OHLC-Daten.

Meldet, wenn die juengsten Kerzen ungewoehnlich alt sind — etwa weil Yahoo
den Intraday-Feed verzoegert oder ein Download-Job stillschweigend nichts
mehr liefert. Die Schwelle kalibriert sich je Ticker aus dessen eigener
Historie, damit Wochenenden und Handelspausen keinen Fehlalarm ausloesen
(Details in :mod:`tradinglib.data_freshness`).

Verwendung:
    python check_freshness.py                  # Bericht auf der Konsole
    python check_freshness.py /quiet           # nur bei Auffaelligkeiten
    python check_freshness.py /notify          # zusaetzlich Pushover
    python check_freshness.py /limit:80        # groessere Stichprobe
    python check_freshness.py /interval:60m    # nur ein Intervall

Rueckgabewert: 0 = alles frisch, 1 = Verzug, 2 = veraltet. Damit laesst sich
der Job auch ausserhalb der App auswerten.
"""
import logging
import sys

from tradinglib import data_freshness as df

logger = logging.getLogger("check_freshness")

_EXIT = {'ok': 0, 'unknown': 0, 'delayed': 1, 'stale': 2}


def main(argv=None) -> int:
    argv = argv or sys.argv
    args = argv[1:]

    quiet = any(a.lower() in ('/quiet', '--quiet') for a in args)
    notify = any(a.lower() in ('/notify', '--notify') for a in args)
    limit, intervals = 40, list(df.INTERVALS)
    for a in args:
        low = a.lower()
        if low.startswith('/limit:') or low.startswith('--limit='):
            try:
                limit = int(a.split(':', 1)[-1].split('=', 1)[-1])
            except ValueError:
                pass
        elif low.startswith('/interval:') or low.startswith('--interval='):
            raw = a.split(':', 1)[-1] if ':' in a else a.split('=', 1)[-1]
            intervals = [s.strip() for s in raw.split(',') if s.strip()]

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    report = df.check(intervals=intervals, limit=limit)
    verdict = df.worst_verdict(report)
    text = df.format_report(report)

    # Im Scheduler laeuft der Job staendig — bei /quiet nur melden, wenn etwas ist.
    if not quiet or verdict in ('delayed', 'stale'):
        print(text)

    if notify and verdict in ('delayed', 'stale'):
        try:
            from tradinglib.pushover_notifier import PushoverNotifier
            PushoverNotifier(storage_file='pushover_freshness.json').send_notification(
                ticker='DATEN', price=0, date=report.get('checked_at', ''),
                message=text, title='Datenfrische')
            logger.info("Pushover-Meldung gesendet (%s).", verdict)
        except Exception as exc:
            logger.warning("Pushover-Meldung fehlgeschlagen: %s", exc)

    return _EXIT.get(verdict, 0)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.exit(main())
