"""Scheduler commands copied from Windows are rewritten for a POSIX install."""
import sqlite3

import pytest

from tradinglib import scheduler_paths as sp

WIN = r'C:\Users\kurtl\trading.cloogidoo.com.V2'
PY = WIN + r'\.venv\Scripts\python.exe'


@pytest.fixture
def root(tmp_path):
    for f in ('get_asset_data.py', 'asset_perf2.py', 'catchup.ps1', 'log_edge_scores.py'):
        (tmp_path / f).write_text('')
    return tmp_path


def _conv(cmd, root):
    return sp.convert(cmd, root=root, python='/venv/bin/python')


def test_python_and_script_with_args_kept(root):
    cmd = (f'"{PY}" "{WIN}\\get_asset_data.py" "1m:2d"  '
           '"/group:^GDAXI,^MDAXI,^SDAXI,^SSMI,^IBEX,^FTSE,^STOXX50E,^ATX,^FCHI"')
    new, missing = _conv(cmd, root)
    assert new == (f'"/venv/bin/python" "{root.as_posix()}/get_asset_data.py" "1m:2d"  '
                   '"/group:^GDAXI,^MDAXI,^SDAXI,^SSMI,^IBEX,^FTSE,^STOXX50E,^ATX,^FCHI"')
    assert missing == []


def test_select_quoting_untouched(root):
    tail = "\"1d:1mo\" /select:'WHERE Ticker IN (\"TLT\",\"HYG\")'"
    new, _ = _conv(f'"{PY}" "{WIN}\\get_asset_data.py" {tail}', root)
    assert new.endswith(tail)


def test_powershell_catchup(root):
    cmd = (r'"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile '
           f'-ExecutionPolicy Bypass -File "{WIN}\\catchup.ps1" -Scope daily')
    new, missing = _conv(cmd, root)
    assert new == (f'pwsh -NoProfile -ExecutionPolicy Bypass -File '
                   f'"{root.as_posix()}/catchup.ps1" -Scope daily')
    assert missing == []


def test_standby_becomes_systemctl(root):
    assert _conv(r'"C:\Windows\System32\shutdown.exe" /h', root)[0] == 'systemctl suspend'


def test_missing_file_stays_and_is_reported(root):
    cmd = f'"{PY}" "{WIN}\\run_agent.py" --user kurt'
    new, missing = _conv(cmd, root)
    assert f'"{WIN}\\run_agent.py"' in new and missing == [f'{WIN}\\run_agent.py']
    assert new.startswith('"/venv/bin/python"')


def test_bare_unquoted_path(root):
    new, _ = _conv(f'{PY} {WIN}\\asset_perf2.py true /all', root)
    assert new == f'/venv/bin/python {root.as_posix()}/asset_perf2.py true /all'


def test_slash_flags_are_not_paths():
    assert not sp.is_windows_command('"/x/.venv/bin/python" "/x/a.py" /index /group:ETP')
    assert sp.is_windows_command(f'"{PY}" /index')


def test_plan_direction_follows_host(root):
    rows = [(1, 'A', f'"{PY}" "{WIN}\\asset_perf2.py" /all'),
            (2, 'B', f'"/venv/bin/python" "{root.as_posix()}/asset_perf2.py" /all')]
    assert [it['id'] for it in sp.plan(rows, root=root, python='/p', posix=True)] == [1]
    assert [it['id'] for it in sp.plan(rows, root=root, python='P', posix=False)] == [2]


# --- Linux -> Windows (commands as install.sh seeds them) -------------------

LX = '/home/kurt/trading.V2/Trading'
LPY = LX + '/.venv/bin/python'
WPY = r'C:\T\.venv\Scripts\python.exe'


def _win(cmd, root):
    return sp.convert_to_windows(cmd, root=root, python=WPY)


def test_linux_python_and_script_with_args_kept(root):
    tail = '"1m:2d"  "/group:^GDAXI,^MDAXI,^SDAXI" /worker:4'
    new, missing = _win(f'"{LPY}" "{LX}/get_asset_data.py" {tail}', root)
    assert new == f'"{WPY}" "{root / "get_asset_data.py"}" {tail}'
    assert missing == []


def test_linux_select_quoting_untouched(root):
    tail = "1d:1mo /select:'WHERE Ticker IN (\"TLT\",\"HYG\")'"
    new, _ = _win(f'"{LPY}" "{LX}/get_asset_data.py" {tail}', root)
    assert new.endswith(tail)


def test_linux_pwsh_catchup(root):
    new, missing = _win(f'pwsh -NoProfile -ExecutionPolicy Bypass -File "{LX}/catchup.ps1" -Scope daily', root)
    assert new == (f'{sp.WIN_PWSH} -NoProfile -ExecutionPolicy Bypass -File '
                   f'"{root / "catchup.ps1"}" -Scope daily')
    assert missing == []


def test_systemctl_suspend_becomes_hibernate(root):
    assert _win('systemctl suspend', root) == (sp.WIN_STANDBY, [])


def test_other_systemctl_is_unresolved(root):
    assert _win('systemctl restart foo', root) == ('systemctl restart foo', ['systemctl restart foo'])


def test_linux_missing_file_stays_and_is_reported(root):
    new, missing = _win(f'"{LPY}" "{LX}/run_agent.py" --user kurt', root)
    assert new == f'"{WPY}" "{LX}/run_agent.py" --user kurt'
    assert missing == [f'{LX}/run_agent.py']


def test_linux_bare_path_gets_quoted(root):
    # The scheduler splits with shlex (POSIX mode): an unquoted C:\ path would lose its backslashes.
    new, _ = _win(f'{LPY} {LX}/asset_perf2.py true /all', root)
    assert new == f'"{WPY}" "{root / "asset_perf2.py"}" true /all'
    import shlex
    assert shlex.split(new)[1] == str(root / 'asset_perf2.py')


def test_rewritten_commands_are_native(root):
    """Converted commands are not flagged again on their target host."""
    lin = f'"{LPY}" "{LX}/asset_perf2.py" /all'
    win = f'"{PY}" "{WIN}\\asset_perf2.py" /all'
    assert not sp.needs_rewrite(_win(lin, root)[0], posix=False)
    assert not sp.needs_rewrite(_conv(win, root)[0], posix=True)


def test_apply_and_backup(root, tmp_path):
    db = tmp_path / 'scheduler.db'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE jobs (id INTEGER PRIMARY KEY, job_name TEXT, command TEXT)')
    old = f'"{PY}" "{WIN}\\log_edge_scores.py" /quiet'
    con.execute('INSERT INTO jobs VALUES (36, "EDGE_SCORES_WEEKLY", ?)', (old,))
    con.commit()
    bak = sp.backup(db)
    items = sp.plan(con.execute('SELECT id, job_name, command FROM jobs').fetchall(),
                    root=root, python='/p', posix=True)
    assert sp.apply(con, items) == 1
    assert con.execute('SELECT command FROM jobs').fetchone()[0] == \
        f'"/p" "{root.as_posix()}/log_edge_scores.py" /quiet'
    assert sp.plan(con.execute('SELECT id, job_name, command FROM jobs').fetchall(),
                   root=root, python='/p', posix=True) == []
    assert sqlite3.connect(bak).execute('SELECT command FROM jobs').fetchone()[0] == old
