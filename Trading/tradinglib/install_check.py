"""Post-install check shared by install.sh and install.ps1.

    python -m tradinglib.install_check <db_dir>

Prints one finding per line as ``LEVEL|message`` (LEVEL = OK, WRN, INFO,
HINT); the installers map the levels onto their own output helpers. HINT
carries no text and tells the installer to print the copy instructions for
the premium files at the end. Only reports, never changes anything, and
always exits 0 so an installer never stops here.

Checks:
  * tradinglib/premium/ and license.json -- both gitignored, so a git clone
    silently yields the free edition;
  * TRADING_EDITION=scalable (premium pages shown locked);
  * scheduler.db commands written for the other operating system.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# trading_bridge.py is tracked in git, so it does not count as "premium present".
PREMIUM_FILES = ('asset_simulator.py', 'multi_transaction.py', 'trading_page.py',
                 'seasonality.py', 'index_sync_fn.py')


def check(root, db_dir, posix: bool | None = None) -> list[tuple[str, str]]:
    root, db_dir = Path(root), Path(db_dir)
    posix = (os.name != 'nt') if posix is None else posix
    out: list[tuple[str, str]] = []

    pdir = root / 'tradinglib' / 'premium'
    have = [f for f in PREMIUM_FILES if (pdir / f).exists()]
    missing = [f for f in PREMIUM_FILES if f not in have]
    if not have:
        out += [('WRN', 'Premium-Module fehlen (tradinglib/premium/) -- es laeuft die freie Version'),
                ('HINT', '')]
    elif missing:
        out += [('WRN', 'Premium-Module unvollstaendig, es fehlen: ' + ', '.join(missing)),
                ('HINT', '')]
    else:
        out.append(('OK', 'Premium-Module vorhanden (tradinglib/premium/)'))

    if not (root / 'license.json').exists():
        if have:
            out += [('WRN', 'license.json fehlt -- Premium-Seiten bleiben ausgeblendet'), ('HINT', '')]
        else:
            out.append(('INFO', 'Keine license.json -- freie Version'))
    else:
        try:
            from tradinglib import license_manager as lm
            lm.reset_cache()
            lic = lm.get_license_info()
            if lic.get('error'):
                out += [('WRN', 'license.json ungueltig: %s' % lic['error']), ('HINT', '')]
            else:
                exp = (' bis %s' % lic['expires']) if lic.get('expires') else ''
                out.append(('OK', 'Lizenz %s%s: %s' % (lic['tier'], exp, ', '.join(lic['features']))))
        except Exception as e:                       # e.g. cryptography missing
            out.append(('WRN', 'Lizenz nicht pruefbar: %s' % e))

    if (os.environ.get('TRADING_EDITION') or '').strip().lower() == 'scalable':
        out.append(('INFO', 'TRADING_EDITION=scalable -- Premium-Seiten erscheinen nur mit Schloss'))

    db = db_dir / 'scheduler.db'
    if db.exists():
        try:
            from tradinglib import scheduler_paths as sp
            con = sqlite3.connect('file:%s?mode=ro' % db.as_posix(), uri=True)
            try:
                rows = con.execute('SELECT id, job_name, command FROM jobs').fetchall()
            finally:
                con.close()
            if posix:
                n = len(sp.plan(rows, posix=True))
                if n:
                    out += [('WRN', '%d Scheduler-Jobs mit Windows-Pfaden (kopierte scheduler.db)' % n),
                            ('WRN', 'In der App umstellen: Admin -> Scheduler -> "Pfade umstellen"')]
            else:
                n = sum(1 for _, _, cmd in rows if sp.is_posix_command(cmd))
                if n:
                    out += [('WRN', '%d Scheduler-Jobs mit Linux-Pfaden (kopierte scheduler.db)' % n),
                            ('WRN', 'Von Hand anpassen: Admin -> Scheduler, Spalte "Kommando"')]
        except Exception as e:
            out.append(('WRN', 'scheduler.db nicht pruefbar: %s' % e))
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parent.parent
    db_dir = Path(argv[0]) if argv else Path(os.environ.get('TradingDB') or root / 'database')
    try:
        for level, msg in check(root, db_dir):
            print('%s|%s' % (level, msg))
    except Exception as e:
        print('WRN|Pruefung abgebrochen: %s' % e)
    return 0


if __name__ == '__main__':
    sys.exit(main())
