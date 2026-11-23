"""install_check: what install.sh / install.ps1 report after setup."""
import sqlite3

import pytest

from tradinglib import install_check as ic
from tradinglib import license_manager as lm
from tradinglib import scheduler_paths as sp

WIN_CMD = r'"C:\X\.venv\Scripts\python.exe" "C:\X\get_asset_data.py" "1m:2d" /index'
LINUX_CMDS = [
    '"/home/kurt/T/.venv/bin/python" "/home/kurt/T/get_asset_data.py" "1m:2d" /index',
    'systemctl suspend',
    'pwsh -NoProfile -ExecutionPolicy Bypass -File "/home/kurt/T/catchup.ps1" -Scope daily',
]


@pytest.fixture
def tree(tmp_path, monkeypatch):
    (tmp_path / 'tradinglib' / 'premium').mkdir(parents=True)
    (tmp_path / 'db').mkdir()
    monkeypatch.setattr(lm, '_license_path', lambda: tmp_path / 'license.json')
    monkeypatch.delenv('TRADING_EDITION', raising=False)
    lm.reset_cache()
    yield tmp_path
    lm.reset_cache()


def _premium(root, files=ic.PREMIUM_FILES):
    for f in files:
        (root / 'tradinglib' / 'premium' / f).write_text('')


def _sched(root, commands):
    con = sqlite3.connect(root / 'db' / 'scheduler.db')
    con.execute('CREATE TABLE jobs (id INTEGER PRIMARY KEY, job_name TEXT, command TEXT)')
    con.executemany('INSERT INTO jobs (job_name, command) VALUES (?, ?)',
                    [(f'J{i}', c) for i, c in enumerate(commands)])
    con.commit()
    con.close()


def _levels(out):
    return [lvl for lvl, _ in out]


def test_fresh_clone_reports_free_edition(tree):
    out = ic.check(tree, tree / 'db', posix=True)
    assert out[0][0] == 'WRN' and 'fehlen' in out[0][1]
    assert 'HINT' in _levels(out)
    assert ('INFO', 'Keine license.json -- freie Version') in out


def test_partial_premium_names_missing_files(tree):
    _premium(tree, ['asset_simulator.py', 'multi_transaction.py'])
    msg = ic.check(tree, tree / 'db', posix=True)[0][1]
    assert 'trading_page.py' in msg and 'asset_simulator.py' not in msg


def test_premium_without_license_warns(tree):
    _premium(tree)
    out = ic.check(tree, tree / 'db', posix=True)
    assert out[0][0] == 'OK'
    assert ('WRN', 'license.json fehlt -- Premium-Seiten bleiben ausgeblendet') in out
    assert 'HINT' in _levels(out)


def test_tampered_license_warns(tree):
    _premium(tree)
    (tree / 'license.json').write_text('{"payload": {"tier": "Full"}, "signature": "AAAA"}')
    out = ic.check(tree, tree / 'db', posix=True)
    assert any(lvl == 'WRN' and 'ungueltig' in msg for lvl, msg in out)
    assert 'HINT' in _levels(out)


def test_scalable_edition_noted(tree, monkeypatch):
    monkeypatch.setenv('TRADING_EDITION', 'scalable')
    assert any('scalable' in msg for _, msg in ic.check(tree, tree / 'db', posix=True))


def test_windows_scheduler_on_linux(tree):
    _sched(tree, [WIN_CMD, LINUX_CMDS[0]])
    msgs = [m for _, m in ic.check(tree, tree / 'db', posix=True)]
    assert '1 Scheduler-Jobs mit Windows-Pfaden (kopierte scheduler.db)' in msgs


def test_linux_scheduler_on_windows(tree):
    _sched(tree, LINUX_CMDS + [WIN_CMD])
    msgs = [m for _, m in ic.check(tree, tree / 'db', posix=False)]
    assert '3 Scheduler-Jobs mit Linux-Pfaden (kopierte scheduler.db)' in msgs
    assert 'In der App umstellen: Admin -> Scheduler -> "Pfade umstellen"' in msgs


def test_native_scheduler_is_quiet(tree):
    _sched(tree, [WIN_CMD, r'"C:\Windows\System32\shutdown.exe" /h'])
    assert not any('Scheduler' in m for _, m in ic.check(tree, tree / 'db', posix=False))


@pytest.mark.parametrize('cmd', [
    WIN_CMD,
    '"C:\\X\\python.exe" "C:\\X\\get_asset_data.py" "/group:^GDAXI,^MDAXI" /worker:4',
    "\"C:\\X\\python.exe\" \"C:\\X\\a.py\" \"1d:1mo\" /select:'WHERE Ticker IN (\"TLT\",\"HYG\")'",
    '"/index"',
])
def test_windows_commands_are_not_posix(cmd):
    assert not sp.is_posix_command(cmd)


@pytest.mark.parametrize('cmd', LINUX_CMDS)
def test_linux_commands_are_posix(cmd):
    assert sp.is_posix_command(cmd)


def test_main_never_fails(tree, capsys):
    assert ic.main([str(tree / 'does-not-exist')]) == 0
    assert capsys.readouterr().out.strip()
