"""Run history of saved strategies: one record per run, kept per user and strategy.

Every Strategy Finder run of a saved strategy leaves its key figures here --
result, CAGR, drawdown, comparison with the equally weighted universe, the
signal-check lights and the matched-random percentile once those were run. The
strategy card shows the history, and ``comparison`` lines up all saved
strategies by their latest run.

A page rerun (any widget click recomputes the run) must not add a record each
time: a run with the same settings on the same day updates its record instead
(``upsert``), so the signal check run after the calculation lands on the same
line. Different settings or another day give a new line.

Separate SQLite file (``strategy_reports.db``) so config.db does not grow with
every run.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3

DB_FILE = 'strategy_reports.db'
MAX_PER_STRATEGY = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user     TEXT NOT NULL,
    strategy TEXT NOT NULL,
    sig      TEXT NOT NULL,
    day      TEXT NOT NULL,
    ts       TEXT NOT NULL,
    payload  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_user_strategy ON reports (user, strategy, id);
"""


def _path(db_path: str = 'database') -> str:
    """The report DB: inside *db_path* when that is a directory, else via the app's DB path."""
    if db_path and os.path.isdir(db_path) and os.path.isabs(db_path):
        return os.path.join(db_path, DB_FILE)
    from tradinglib import tools
    return tools.Tools().get_path(path=db_path, file_name=DB_FILE)


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(_path(db_path), timeout=5.0)
    con.executescript(_SCHEMA)
    return con


def sig_hash(signature: str) -> str:
    return hashlib.sha1(str(signature).encode('utf-8')).hexdigest()[:12]


def upsert(user: str, strategy: str, signature: str, fields: dict, *,
           db_path: str = 'database', now: dt.datetime | None = None) -> int:
    """Record a run; same settings on the same day update the existing record.

    *fields* None values do not overwrite what an earlier call stored (the
    signal check may run after the calculation, or not at all).
    """
    now = now or dt.datetime.now()
    day, ts, sig = now.date().isoformat(), now.isoformat(timespec='seconds'), sig_hash(signature)
    clean = {k: v for k, v in (fields or {}).items() if v is not None}
    con = _connect(db_path)
    try:
        row = con.execute('SELECT id, sig, day, payload FROM reports WHERE user = ? AND strategy = ? '
                          'ORDER BY id DESC LIMIT 1', (user, strategy)).fetchone()
        if row and row[1] == sig and row[2] == day:
            payload = {**json.loads(row[3]), **clean}
            con.execute('UPDATE reports SET ts = ?, payload = ? WHERE id = ?',
                        (ts, json.dumps(payload, ensure_ascii=False), row[0]))
            rid = row[0]
        else:
            cur = con.execute('INSERT INTO reports (user, strategy, sig, day, ts, payload) '
                              'VALUES (?, ?, ?, ?, ?, ?)',
                              (user, strategy, sig, day, ts, json.dumps(clean, ensure_ascii=False)))
            rid = cur.lastrowid
            con.execute('DELETE FROM reports WHERE user = ? AND strategy = ? AND id NOT IN '
                        '(SELECT id FROM reports WHERE user = ? AND strategy = ? '
                        ' ORDER BY id DESC LIMIT ?)',
                        (user, strategy, user, strategy, MAX_PER_STRATEGY))
        con.commit()
        return int(rid)
    finally:
        con.close()


def delete(user: str, strategies, *, db_path: str = 'database') -> int:
    """Remove the recorded runs of *strategies*; returns the number of records removed."""
    names = [str(s) for s in strategies or []]
    if not names or not os.path.exists(_path(db_path)):
        return 0
    con = _connect(db_path)
    try:
        cur = con.execute(f'DELETE FROM reports WHERE user = ? AND strategy IN '
                          f'({",".join("?" * len(names))})', (user, *names))
        con.commit()
        return int(cur.rowcount)
    finally:
        con.close()


def rename(user: str, old: str, new: str, *, db_path: str = 'database') -> int:
    """Move the recorded runs of *old* to *new*; returns the number of records moved.

    Records already under *new* belong to a deleted strategy of that name (a
    single delete keeps the history) and would be shown as runs of the renamed
    one -- they are removed first.
    """
    if old == new or not os.path.exists(_path(db_path)):
        return 0
    con = _connect(db_path)
    try:
        con.execute('DELETE FROM reports WHERE user = ? AND strategy = ?', (user, new))
        cur = con.execute('UPDATE reports SET strategy = ? WHERE user = ? AND strategy = ?',
                          (new, user, old))
        con.commit()
        return int(cur.rowcount)
    finally:
        con.close()


def history(user: str, strategy: str, *, db_path: str = 'database', limit: int = 50) -> list[dict]:
    """Records of one strategy, newest first: payload plus 'ts' and 'sig'."""
    if not os.path.exists(_path(db_path)):
        return []
    con = _connect(db_path)
    try:
        rows = con.execute('SELECT ts, sig, payload FROM reports WHERE user = ? AND strategy = ? '
                           'ORDER BY id DESC LIMIT ?', (user, strategy, limit)).fetchall()
    finally:
        con.close()
    return [{**json.loads(p), 'ts': ts, 'sig': sig} for ts, sig, p in rows]


_LIGHT = {'green': '🟢', 'yellow': '🟡', 'red': '🔴', 'grey': '⚪'}
_VERDICT_ORDER = ('buy', 'sell', 'control', 'concentration')


def lights(verdicts) -> str:
    """Signal-check verdicts as four lights (buy, sell, control, concentration)."""
    if not isinstance(verdicts, dict) or not verdicts:
        return ''
    return ''.join(_LIGHT.get(verdicts.get(k), '') for k in _VERDICT_ORDER)


def table(rows: list[dict], *, comparison: bool = False, current_sig: str | None = None):
    """Display frame for history (default) or comparison rows, columns in the UI language."""
    import pandas as pd
    from tradinglib.i18n import t as _t

    def num(r, k):
        v = r.get(k)
        return float(v) if isinstance(v, (int, float)) else None

    out = []
    for r in rows:
        line = {}
        if comparison:
            line[_t('sbs.col_strategy')] = r.get('name', '')
            line[_t('sbs.col_status')] = _t(f"sbs.status_{r.get('status', 'idea')}")
            line[_t('sbs.col_runs')] = r.get('runs', 0)
        line[_t('sbs.col_run')] = str(r.get('ts', '') or '').replace('T', ' ')[:16]
        line[_t('sbs.col_universe')] = r.get('universe', '')
        line[_t('sbs.col_years')] = r.get('years', '')
        for key, col in (('total', 'sbs.col_total'), ('cagr', 'sbs.col_cagr'),
                         ('max_dd', 'sbs.col_maxdd'), ('ret_vol', 'sbs.col_retvol'),
                         ('invested', 'sbs.col_invested'), ('universe_cagr', 'sbs.col_univ_cagr'),
                         ('vs_universe', 'sbs.col_vs_univ')):
            line[_t(col)] = num(r, key)
        line[_t('sbs.col_trades')] = r.get('trades')
        line[_t('sbs.col_lights')] = lights(r.get('verdicts'))
        line[_t('sbs.col_matched')] = num(r, 'matched_pct')
        if comparison:
            line[_t('sbs.col_state')] = (_t('sbs.state_current') if r.get('current')
                                         else (_t('sbs.state_other') if r.get('runs') else '–'))
        elif current_sig is not None:
            line[_t('sbs.col_state')] = (_t('sbs.state_current') if r.get('sig') == current_sig
                                         else _t('sbs.state_other'))
        out.append(line)
    return pd.DataFrame(out)


def comparison(user: str, specs: dict, *, db_path: str = 'database') -> list[dict]:
    """One line per saved strategy: its latest run of the saved settings.

    If the saved settings were never run, the latest run of any settings stands
    in, marked ``current=False``; no run at all gives a line with the
    description only.
    """
    from tradinglib import strategy_spec as ss
    out = []
    for name, spec in sorted(specs.items(), key=lambda x: x[0].lower()):
        hist = history(user, name, db_path=db_path, limit=MAX_PER_STRATEGY)
        own = sig_hash(ss.signature(spec))
        rec = next((r for r in hist if r['sig'] == own), None)
        current = rec is not None
        rec = rec or (hist[0] if hist else {})
        out.append({'name': name, 'status': spec.get('status', 'idea'), 'idea': spec.get('idea', ''),
                    'universe': ss.bundle_label(spec), 'current': current, 'runs': len(hist), **rec})
    return out
