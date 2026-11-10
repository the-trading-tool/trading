"""Run history of saved strategies (strategy_reports)."""
import datetime as dt
import json
import re
from pathlib import Path

from tradinglib import strategy_reports as sr
from tradinglib import strategy_spec as ss

ROOT = Path(__file__).resolve().parent.parent
DAY1 = dt.datetime(2026, 11, 1, 10, 0)
DAY2 = dt.datetime(2026, 11, 2, 9, 30)


def test_delete_removes_only_the_named_strategies_of_the_user(tmp_path):
    db = str(tmp_path)
    assert sr.delete('kurt', ['A'], db_path=db) == 0          # no DB yet: nothing to do
    for user, strat in (('kurt', 'A'), ('kurt', 'B'), ('kurt', 'C'), ('admin', 'A')):
        sr.upsert(user, strat, 'sig', {'total': 1.0}, db_path=db, now=DAY1)
    sr.upsert('kurt', 'A', 'sig2', {'total': 2.0}, db_path=db, now=DAY2)
    assert sr.delete('kurt', ['A', 'B'], db_path=db) == 3
    assert sr.history('kurt', 'A', db_path=db) == [] and sr.history('kurt', 'B', db_path=db) == []
    assert len(sr.history('kurt', 'C', db_path=db)) == 1
    assert len(sr.history('admin', 'A', db_path=db)) == 1


def test_rename_moves_history_and_drops_orphans_of_the_new_name(tmp_path):
    db = str(tmp_path)
    sr.upsert('kurt', 'Old', 'sig1', {'total': 1.0}, db_path=db, now=DAY1)
    sr.upsert('kurt', 'New', 'sig9', {'total': 9.0}, db_path=db, now=DAY1)   # deleted strategy's runs
    sr.upsert('anna', 'Old', 'sig1', {'total': 5.0}, db_path=db, now=DAY1)
    assert sr.rename('kurt', 'Old', 'New', db_path=db) == 1
    assert [r['total'] for r in sr.history('kurt', 'New', db_path=db)] == [1.0]
    assert sr.history('kurt', 'Old', db_path=db) == []
    assert [r['total'] for r in sr.history('anna', 'Old', db_path=db)] == [5.0]


def test_same_settings_same_day_is_one_record(tmp_path):
    db = str(tmp_path)
    sr.upsert('kurt', 'Dip', 'sigA', {'total': 10.0, 'trades': 5}, db_path=db, now=DAY1)
    # page rerun after the signal check: verdicts added, total kept
    sr.upsert('kurt', 'Dip', 'sigA', {'total': None, 'verdicts': {'buy': 'green'}}, db_path=db,
              now=DAY1 + dt.timedelta(minutes=3))
    h = sr.history('kurt', 'Dip', db_path=db)
    assert len(h) == 1 and h[0]['total'] == 10.0 and h[0]['verdicts'] == {'buy': 'green'}
    assert h[0]['ts'].endswith('10:03:00')


def test_other_settings_or_other_day_is_a_new_record(tmp_path):
    db = str(tmp_path)
    sr.upsert('kurt', 'Dip', 'sigA', {'total': 1.0}, db_path=db, now=DAY1)
    sr.upsert('kurt', 'Dip', 'sigB', {'total': 2.0}, db_path=db, now=DAY1)
    sr.upsert('kurt', 'Dip', 'sigB', {'total': 3.0}, db_path=db, now=DAY2)
    assert [r['total'] for r in sr.history('kurt', 'Dip', db_path=db)] == [3.0, 2.0, 1.0]
    assert sr.history('admin', 'Dip', db_path=db) == []           # per user


def test_history_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, 'MAX_PER_STRATEGY', 3)
    for i in range(5):
        sr.upsert('kurt', 'Dip', f'sig{i}', {'total': float(i)}, db_path=str(tmp_path), now=DAY1)
    assert [r['total'] for r in sr.history('kurt', 'Dip', db_path=str(tmp_path))] == [4.0, 3.0, 2.0]


def test_comparison_prefers_the_run_of_the_saved_settings(tmp_path):
    db = str(tmp_path)
    saved = ss.normalize({'name': 'Dip', 'entry': {'buy': 'a'}, 'status': 'paper'})
    other = ss.normalize({'name': 'Dip', 'entry': {'buy': 'b'}})
    sr.upsert('kurt', 'Dip', ss.signature(saved), {'total': 7.0}, db_path=db, now=DAY1)
    sr.upsert('kurt', 'Dip', ss.signature(other), {'total': 99.0}, db_path=db, now=DAY2)
    lonely = ss.normalize({'name': 'Neu', 'entry': {'buy': 'c'}})
    rows = {r['name']: r for r in sr.comparison('kurt', {'Dip': saved, 'Neu': lonely}, db_path=db)}
    assert rows['Dip']['total'] == 7.0 and rows['Dip']['current'] and rows['Dip']['runs'] == 2
    assert rows['Neu']['runs'] == 0 and 'total' not in rows['Neu']


def test_lights_and_table():
    assert sr.lights({'buy': 'green', 'sell': 'red', 'control': 'yellow',
                      'concentration': 'grey'}) == '🟢🔴🟡⚪'
    assert sr.lights(None) == ''
    rows = [{'ts': '2026-11-01T10:00:00', 'sig': 'x', 'total': 5.0, 'cagr': 2.0, 'trades': 3,
             'verdicts': {'buy': 'green'}, 'matched_pct': 80.0}]
    t = sr.table(rows, current_sig='x')
    assert len(t) == 1 and len(t.columns) >= 12
    c = sr.table([{'name': 'Dip', 'status': 'live', 'runs': 1, 'current': True, **rows[0]}],
                 comparison=True)
    assert len(c.columns) == len(t.columns) + 3


def test_locale_keys():
    src = (ROOT / 'tradinglib' / 'strategy_reports.py').read_text(encoding='utf-8')
    keys = set(re.findall(r"'(sbs\.[a-z_.]+)'", src))
    keys |= {'sbs.hist_btn', 'sbs.cmp_btn', 'sbs.hist_title', 'sbs.hist_empty', 'sbs.hist_help',
             'sbs.cmp_title', 'sbs.cmp_empty', 'sbs.cmp_help'}
    for lang in ('de', 'en'):
        d = json.loads((ROOT / 'locales' / f'{lang}.json').read_text(encoding='utf-8'))
        assert not sorted(k for k in keys if k not in d), lang
