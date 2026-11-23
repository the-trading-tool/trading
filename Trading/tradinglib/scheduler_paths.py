"""Detect and rewrite scheduler job commands copied from another operating system.

A scheduler.db copied from Windows to Linux keeps commands such as

    "C:\\...\\.venv\\Scripts\\python.exe" "C:\\...\\get_asset_data.py" "1m:2d" /index

which cannot run on the new host, and the same holds the other way round.
`plan()` lists the affected jobs together with the rewritten command for the
current host; `apply()` writes them back. Only the paths are touched;
arguments and quoting stay exactly as they were.

Rewrite rules (Windows -> POSIX, `convert`):
    python.exe / pythonw.exe       -> the venv interpreter of this installation
    powershell.exe / pwsh.exe      -> pwsh (PowerShell Core, as install.sh seeds it)
    shutdown.exe ...               -> systemctl suspend (whole command, as install.sh)
    any other C:\\... path         -> same file below this installation's root,
                                      found by its longest existing suffix

Rewrite rules (POSIX -> Windows, `convert_to_windows`):
    .../bin/python[3[.x]]          -> the venv interpreter of this installation
    pwsh (bare or as a path)       -> Windows PowerShell 5.1 (as install.ps1 seeds it)
    systemctl suspend|hibernate    -> shutdown.exe /h (whole command, as install.ps1)
    any other /... path            -> same file below this installation's root
A path whose file does not exist here stays unchanged and is reported as
unresolved, so nothing is silently pointed at a wrong location.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path, PureWindowsPath

APP_ROOT = Path(__file__).resolve().parent.parent

# Drive-letter path, either inside double quotes or as a bare token.
_QUOTED = re.compile(r'"([A-Za-z]:\\[^"]*)"')
_BARE = re.compile(r'(?<![\w"\\/])([A-Za-z]:\\[^\s"\']+)')
_WIN_PATH = re.compile(r'(?<![\w])[A-Za-z]:\\')

_PYTHON_EXE = {'python.exe', 'pythonw.exe', 'python3.exe'}
_PWSH_EXE = {'powershell.exe', 'pwsh.exe'}
POSIX_STANDBY = 'systemctl suspend'
POSIX_PWSH = 'pwsh'


def is_windows_command(command) -> bool:
    """True when the command contains a Windows drive path (C:\\...)."""
    return bool(command) and bool(_WIN_PATH.search(str(command)))


# Linux-written commands as install.sh seeds them: a quoted absolute POSIX path
# with at least two components, the venv interpreter, or the standby command.
# Bare "/flag" arguments (/index, /group:ETP) never match.
_POSIX_PATH = re.compile(r'"/[^"/\s:]+/[^"]*"|/\.venv/bin/python|^\s*systemctl\s')


def is_posix_command(command) -> bool:
    """True when the command was written for Linux (e.g. a scheduler.db copied to Windows)."""
    return bool(command) and bool(_POSIX_PATH.search(str(command)))


def needs_rewrite(command, posix: bool | None = None) -> bool:
    """True when the command was written for the other platform."""
    posix = (os.name != 'nt') if posix is None else posix
    return is_windows_command(command) if posix else is_posix_command(command)


def default_python(root: Path = APP_ROOT) -> str:
    """The venv interpreter of this installation (as install.sh writes it)."""
    venv = Path(root) / '.venv' / 'bin' / 'python'
    return str(venv) if venv.exists() else sys.executable


def _map_path(win_path: str, root: Path, python: str) -> str | None:
    """Translate one Windows path; None when no matching file exists here."""
    p = PureWindowsPath(win_path)
    name = p.name.lower()
    if name in _PYTHON_EXE:
        return python
    if name in _PWSH_EXE:
        return POSIX_PWSH
    parts = p.parts[1:]                       # drop the drive anchor 'C:\\'
    for k in range(len(parts)):               # longest suffix first
        cand = Path(root).joinpath(*parts[k:])
        if cand.exists():
            return cand.as_posix()
    return None


def convert(command: str, root: Path = APP_ROOT, python: str | None = None) -> tuple[str, list[str]]:
    """Rewrite all Windows paths in *command*.

    Returns (new_command, unresolved_paths). Unresolved paths stay unchanged.
    """
    root = Path(root)
    python = python or default_python(root)
    cmd = str(command)
    unresolved: list[str] = []

    first = _QUOTED.match(cmd.lstrip()) or _BARE.match(cmd.lstrip())
    if first and PureWindowsPath(first.group(1)).name.lower() == 'shutdown.exe':
        return POSIX_STANDBY, unresolved

    def _sub(m, quoted: bool):
        win = m.group(1)
        new = _map_path(win, root, python)
        if new is None:
            unresolved.append(win)
            return m.group(0)
        if new == POSIX_PWSH:                 # bare command name, like install.sh
            return new
        return f'"{new}"' if quoted or ' ' in new else new

    cmd = _QUOTED.sub(lambda m: _sub(m, True), cmd)
    cmd = _BARE.sub(lambda m: _sub(m, False), cmd)
    return cmd, unresolved


# --- POSIX -> Windows ------------------------------------------------------

WIN_STANDBY = r'"C:\Windows\System32\shutdown.exe" /h'
WIN_PWSH = r'"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"'

# Absolute POSIX path with at least two components; the first component holds
# no colon, so "/group:^GDAXI,..." or "/select:..." never match.
_P_QUOTED = re.compile(r'"(/[^"/\s:]+/[^"]*)"')
_P_BARE = re.compile(r'(?<![\w"\'\\/:.])(/[^\s"\'/:]+(?:/[^\s"\']+)+)')
_P_PYTHON = re.compile(r'^python(3(\.\d+)?)?$')
_P_PWSH_LEAD = re.compile(r'^(\s*)pwsh(?=\s|$)')
_P_SYSTEMCTL = re.compile(r'^\s*systemctl\s+(\S+)')
_P_STANDBY_VERBS = {'suspend', 'hibernate', 'hybrid-sleep', 'suspend-then-hibernate'}


def default_python_windows(root: Path = APP_ROOT) -> str:
    """The venv interpreter of this installation (as install.ps1 writes it)."""
    venv = Path(root) / '.venv' / 'Scripts' / 'python.exe'
    return str(venv) if venv.exists() else sys.executable


def _map_posix_path(posix_path: str, root: Path, python: str) -> str | None:
    """Translate one POSIX path to this Windows installation; None if not found."""
    parts = [p for p in posix_path.split('/') if p]
    name = parts[-1] if parts else ''
    if _P_PYTHON.match(name):
        return python
    if name == 'pwsh':
        return WIN_PWSH
    for k in range(len(parts)):               # longest suffix first
        cand = Path(root).joinpath(*parts[k:])
        if cand.exists():
            return str(cand)
    return None


def convert_to_windows(command: str, root: Path = APP_ROOT,
                       python: str | None = None) -> tuple[str, list[str]]:
    """Rewrite all POSIX paths in *command* for this Windows installation.

    Returns (new_command, unresolved). Unresolved paths stay unchanged; a
    systemctl command other than a standby verb is reported as unresolved too.
    """
    root = Path(root)
    python = python or default_python_windows(root)
    cmd = str(command)
    unresolved: list[str] = []

    m = _P_SYSTEMCTL.match(cmd)
    if m:
        if m.group(1) in _P_STANDBY_VERBS:
            return WIN_STANDBY, unresolved
        return cmd, [cmd.strip()]

    def _sub(m):
        src = m.group(1)
        new = _map_posix_path(src, root, python)
        if new is None:
            unresolved.append(src)
            return m.group(0)
        if new == WIN_PWSH:                   # already quoted
            return new
        # Always quoted: the scheduler splits commands with shlex in POSIX
        # mode, which drops the backslashes of an unquoted Windows path.
        return f'"{new}"'

    cmd = _P_QUOTED.sub(_sub, cmd)
    cmd = _P_BARE.sub(_sub, cmd)
    cmd = _P_PWSH_LEAD.sub(lambda m: m.group(1) + WIN_PWSH, cmd)
    return cmd, unresolved


def plan(rows, root: Path = APP_ROOT, python: str | None = None, posix: bool | None = None) -> list[dict]:
    """Jobs that need a rewrite for this host: [{id, job_name, old, new, unresolved}].

    *rows* are (id, job_name, command) tuples. On a POSIX host Windows commands
    are rewritten, on Windows POSIX commands.
    """
    posix = (os.name != 'nt') if posix is None else posix
    conv = convert if posix else convert_to_windows
    out = []
    for job_id, name, command in rows:
        if not needs_rewrite(command, posix=posix):
            continue
        new, unresolved = conv(command, root=root, python=python)
        out.append({'id': int(job_id), 'job_name': name, 'old': command,
                    'new': new, 'unresolved': unresolved})
    return out


def backup(db_file) -> str:
    """Copy the scheduler DB next to itself with a timestamp; returns the copy's path."""
    dst = f"{db_file}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(db_file, dst)
    return dst


def apply(conn: sqlite3.Connection, items: list[dict]) -> int:
    """Write the rewritten commands; only rows whose command actually changed."""
    n = 0
    cur = conn.cursor()
    for it in items:
        if it['new'] != it['old']:
            cur.execute('UPDATE jobs SET command = ? WHERE id = ? AND command = ?',
                        (it['new'], it['id'], it['old']))
            n += cur.rowcount
    conn.commit()
    return n
