"""Repair of simulation rows on a stale price scale (tradinglib.sim_repair)."""
import datetime as dt
import os
import sqlite3

import pandas as pd
import pytest

from tradinglib import sim_repair as sr


def _price_db(path, day, month=None, h60=None):
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE day_data (Date TEXT, Open REAL, High REAL, Low REAL, Close REAL, Volume INTEGER)')
    con.executemany('INSERT INTO day_data VALUES (?, ?, ?, ?, ?, ?)',
                    [(d + ' 00:00:00', c, c, c, c, 1000) for d, c in day])
    for name, rows in (('month_data', month), ('h60_data', h60)):
        if rows is None:
            continue
        con.execute(f'CREATE TABLE {name} (Date TEXT, Open REAL, High REAL, Low REAL, Close REAL, Volume INTEGER)')
        con.executemany(f'INSERT INTO {name} VALUES (?, ?, ?, ?, ?, ?)',
                        [(d, c, c, c, c, 8000) for d, c in rows])
    con.commit()
    con.close()


DAYS = [('2025-05-01', 80.0), ('2025-05-02', 84.0), ('2025-05-30', 88.0), ('2025-06-02', 90.0)]


def test_stale_rows_and_clean_ratio():
    ref = pd.Series([80.0, 84.0, 90.0], index=pd.to_datetime(['2025-05-01', '2025-05-02', '2025-06-02']))
    sim = pd.DataFrame({'Date': ['2025-05-01 00:00:00', '2025-05-02 00:00:00', '2025-06-02 00:00:00',
                                 '2025-06-03 00:00:00'],
                        'close': [1.0, 84.0, 1.125, 2.0]})
    bad = sr.stale_rows(sim, ref)
    assert bad['Date'].str[:10].tolist() == ['2025-05-01', '2025-06-02']   # no ref on 06-03
    assert bad['factor'].round(1).tolist() == [80.0, 80.0]
    assert sr.clean_ratio(79.0) == 80 and sr.clean_ratio(0.251) == 0.25 and sr.clean_ratio(13.3) is None


def test_align_price_tables_is_per_bar_and_idempotent(tmp_path):
    p = str(tmp_path / 'yf_T.db')
    # month: May on the old scale (1.10 vs month-end 88), June already adjusted;
    # h60: mixed like GOSS (old scale on three days, the rest adjusted).
    _price_db(p, DAYS, month=[('2025-05-01 00:00:00', 1.10), ('2025-06-01 00:00:00', 90.0)],
              h60=[('2025-05-01 10:00:00', 1.0), ('2025-05-02 10:00:00', 1.04),
                   ('2025-05-30 10:00:00', 1.1), ('2025-05-30 15:00:00', 87.5),
                   ('2025-06-02 15:00:00', 90.0)])
    assert sr.align_price_tables(p, apply=False) == {'month_data': (1, 80.0), 'h60_data': (3, 80.0)}
    sr.align_price_tables(p, apply=True)
    con = sqlite3.connect(p)
    month = con.execute('SELECT Close, Volume FROM month_data ORDER BY Date').fetchall()
    h60 = [r[0] for r in con.execute('SELECT Close FROM h60_data ORDER BY Date')]
    con.close()
    assert month[0] == (pytest.approx(88.0), 100) and month[1] == (90.0, 8000)
    assert h60 == [80.0, pytest.approx(83.2), 88.0, 87.5, 90.0]
    assert sr.align_price_tables(p, apply=False) == {}          # nothing left to do


def test_align_leaves_a_single_crash_day():
    """Intraday bars before the news of a crash sit at the old level -- one day, not a scale."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'yf_T.db')
        _price_db(p, [('2026-02-20', 2.1), ('2026-02-23', 0.42), ('2026-02-24', 0.40)],
                  h60=[('2026-02-23 14:00:00', 2.08), ('2026-02-23 15:00:00', 2.05),
                       ('2026-02-23 16:00:00', 0.45), ('2026-02-24 15:00:00', 0.40)])
        assert sr.align_price_tables(p, apply=False) == {}


def _sim_db(path, rows, extra=True):
    con = sqlite3.connect(path)
    cols = 'ticker TEXT, Date TEXT, close REAL, sma200 REAL' + (', mtt_rs REAL' if extra else '')
    con.execute(f'CREATE TABLE asset_simulation ({cols})')
    for r in rows:
        con.execute(f'INSERT INTO asset_simulation VALUES ({",".join("?" * len(r))})', r)
    con.commit()
    con.close()


def test_scan_and_merge_swap_rows_with_backup(tmp_path, monkeypatch):
    folder = str(tmp_path)
    _price_db(os.path.join(folder, 'yf_T.db'), DAYS)
    _price_db(os.path.join(folder, 'yf_OK.db'), [('2025-05-01', 10.0), ('2025-05-02', 10.5)])
    _sim_db(os.path.join(folder, 'asset_simulation_2025.db'), [
        ('T', '2025-05-01 00:00:00', 1.0, 0.9, 7.0), ('T', '2025-05-02 00:00:00', 1.05, 0.9, 7.0),
        ('T', '2025-06-03 00:00:00', 92.0, 85.0, 7.0),          # added by a nightly run later
        ('OK', '2025-05-01 00:00:00', 10.0, 9.0, 5.0), ('OK', '2025-05-02 00:00:00', 10.5, 9.1, 5.0)])
    plan = sr.scan(folder)
    assert plan[['db', 'ticker', 'year', 'stale']].values.tolist() == [['asset_simulation_2025.db', 'T', 2025, 2]]

    # A staged recomputation as compute() leaves it.
    con = sqlite3.connect(sr._stage_db(folder, 'asset_simulation_2025.db'))
    sr._ensure_stage(con, ['close', 'sma200', 'new_col'])
    con.executemany('INSERT INTO asset_simulation (ticker, Date, close, sma200, new_col) VALUES (?, ?, ?, ?, ?)',
                    [('T', '2025-05-01 00:00:00', 80.0, 72.0, 1.0), ('T', '2025-05-02 00:00:00', 84.0, 72.5, 1.0),
                     ('T', '2025-05-30 00:00:00', 88.0, 73.0, 1.0),     # production never had this day
                     ('BAD', '2025-05-01 00:00:00', 50.0, 40.0, 1.0)])
    con.execute("INSERT INTO repair_items VALUES ('asset_simulation_2025.db', 'T', 2025, 3, 'now', NULL)")
    con.execute("INSERT INTO repair_items VALUES ('asset_simulation_2025.db', 'BAD', 2025, 1, 'now', NULL)")
    con.commit()
    con.close()
    os.makedirs(os.path.join(sr.stage_dir(folder), 'yf'), exist_ok=True)
    _price_db(os.path.join(sr.stage_dir(folder), 'yf', 'yf_T.db'), DAYS)
    # BAD's reloaded copy jumps between two levels (two listings mixed) -> rejected at merge.
    _price_db(os.path.join(sr.stage_dir(folder), 'yf', 'yf_BAD.db'),
              [(d.strftime('%Y-%m-%d'), 50.0 if i % 3 == 1 else 1.0)
               for i, d in enumerate(pd.bdate_range('2025-05-01', periods=12))])
    monkeypatch.setattr(sr, 'yahoo_splits', lambda t: pd.Series(dtype=float))
    monkeypatch.setattr(sr, 'running_perf', lambda: [])
    monkeypatch.setattr('tradinglib.minervini.update_rs', lambda *a, **k: 0)

    rep = sr.merge(folder)
    assert list(rep['rejected']) == ['BAD'] and 'spikes' in rep['rejected']['BAD'][0]
    assert rep['merged'] and rep['dbs']['asset_simulation_2025.db']['rows'] == 2
    assert rep['dbs']['asset_simulation_2025.db']['stale_after'] == 0
    assert rep['dbs']['asset_simulation_2025.db']['not_recomputed'] == ['mtt_rs']
    con = sqlite3.connect(os.path.join(folder, 'asset_simulation_2025.db'))
    t = con.execute("SELECT Date, close, sma200, new_col FROM asset_simulation WHERE ticker='T' ORDER BY Date").fetchall()
    ok = con.execute("SELECT COUNT(*) FROM asset_simulation WHERE ticker='OK'").fetchone()[0]
    con.close()
    assert [r[1] for r in t] == [80.0, 84.0, 92.0] and t[0][3] == 1.0      # later day kept, 05-30 not added
    assert ok == 2
    bak = sqlite3.connect(os.path.join(rep['backup'], 'rows_asset_simulation_2025.db'))
    assert bak.execute('SELECT COUNT(*) FROM asset_simulation').fetchone()[0] == 3
    bak.close()
    assert sr.staged(folder).empty and sr.merge(folder)['reason'] == 'nothing staged'


def test_split_seams_only_on_reported_split_days():
    idx = pd.bdate_range('2026-02-16', periods=8)
    # adjusted series: a real -80 % crash on 02-23, no reported split that day
    adjusted = pd.Series([2.0, 2.1, 2.1, 2.0, 2.1, 0.42, 0.4, 0.41], index=idx)
    splits = pd.Series([0.0125], index=pd.DatetimeIndex(['2026-02-25']))
    assert sr.split_seams(adjusted, splits) == []
    # unadjusted: the 1:80 reverse split shows as x80 on the split day
    raw = pd.Series([0.30, 0.31, 0.30, 0.31, 0.30, 0.31, 24.6, 24.8], index=idx)
    seams = sr.split_seams(raw, pd.Series([0.0125], index=pd.DatetimeIndex([idx[6]])))
    assert len(seams) == 1 and round(seams[0]['move']) == 79


def test_split_seams_where_the_provider_stopped_adjusting():
    """Yahoo adjusted 2026 reverse splits only back to Jan 2: the jump sits there."""
    idx = pd.bdate_range('2025-12-24', periods=10)
    raw = pd.Series([2.0, 2.0, 2.0, 1.99, 2.0, 2.0, 2.0, 20.1, 20.2, 20.0], index=idx)   # idx[7] = Jan 2
    seams = sr.split_seams(raw, pd.Series([0.1], index=pd.DatetimeIndex(['2026-09-30'])))
    assert len(seams) == 1 and str(seams[0]['date'])[:10] == '2026-01-02'
    # a 2:1 split is not looked for elsewhere: a -50 % day proves nothing
    assert sr.split_seams(raw.iloc[::-1].reset_index(drop=True).set_axis(idx),
                          pd.Series([2.0], index=pd.DatetimeIndex(['2026-09-30']))) == []


def test_price_glitches():
    idx = pd.bdate_range('2025-01-01', periods=30)
    flat = [10.0] * 30
    spike = flat[:10] + [400.0] + flat[11:]                   # one squeeze or bad print: tolerated
    mixed = [400.0 if i in (5, 12, 20) else 10.0 for i in range(30)]   # two listings mixed
    shift = flat[:10] + [55.0] * 20                            # x5.5 that stays
    crash = flat[:10] + [2.0] * 20                             # -80 % that stays: real
    assert sr.price_glitches(pd.Series(flat, index=idx)) == []
    assert sr.price_glitches(pd.Series(spike, index=idx)) == []
    assert sr.price_glitches(pd.Series(mixed, index=idx)) == ['3 spikes from 2025-01-08']
    assert sr.price_glitches(pd.Series(shift, index=idx)) == ['level shift 2025-01-15 x5.5']
    assert sr.price_glitches(pd.Series(crash, index=idx)) == []


def test_transfer_prices_replaces_range_keeps_newer(tmp_path):
    folder = str(tmp_path)
    _price_db(os.path.join(folder, 'yf_T.db'),
              [('2025-05-01', 1.0), ('2025-05-02', 1.05), ('2025-06-02', 90.0), ('2025-06-03', 91.0)])
    os.makedirs(os.path.join(sr.stage_dir(folder), 'yf'), exist_ok=True)
    _price_db(os.path.join(sr.stage_dir(folder), 'yf', 'yf_T.db'),
              [('2025-05-01', 80.0), ('2025-05-02', 84.0), ('2025-06-02', 90.0)])
    pc = sr._prices_db(folder)
    pc.execute("INSERT INTO repair_prices VALUES ('T', 3, 0, '{}', '[]', 'now', NULL)")
    pc.commit()
    pc.close()
    assert sr.transfer_prices(folder, 'T') == {'day_data': 3}
    con = sqlite3.connect(os.path.join(folder, 'yf_T.db'))
    closes = [r[0] for r in con.execute('SELECT Close FROM day_data ORDER BY Date')]
    con.close()
    assert closes == [80.0, 84.0, 90.0, 91.0]                  # 06-03 came later, kept
    assert sr.transfer_prices(folder, 'T') == {}                # only once


def test_merge_window(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / 'scheduler.db')
    con.execute('CREATE TABLE jobs (id, time, command, frequency, job_name, end_time, time_range, '
                'allowed_days, enabled, last_run)')
    con.execute("INSERT INTO jobs VALUES (1, '16:00', 'python asset_perf2.py true', "
                "'monday,tuesday,wednesday,thursday,friday', 'P', NULL, NULL, NULL, 1, NULL)")
    con.execute("INSERT INTO jobs VALUES (2, '15:50', 'python get_asset_data.py', "
                "'monday,tuesday', 'D', NULL, NULL, NULL, 1, NULL)")
    con.commit()
    con.close()
    monkeypatch.setattr(sr, 'running_perf', lambda: [])
    tue = dt.datetime(2026, 11, 3, 15, 45)
    assert sr.merge_window(str(tmp_path), now=tue) == (False, 'asset_perf2 starts at 16:00')
    assert sr.merge_window(str(tmp_path), now=tue.replace(hour=12))[0]
    assert sr.merge_window(str(tmp_path), now=dt.datetime(2026, 11, 7, 15, 45))[0]   # Saturday
    monkeypatch.setattr(sr, 'running_perf', lambda: [123])
    assert sr.merge_window(str(tmp_path), now=tue.replace(hour=12))[0] is False
