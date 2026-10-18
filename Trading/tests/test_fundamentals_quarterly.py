"""Quarterly results, point in time (Minervini groundwork)."""
import os
import sqlite3
import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib import fundamentals_quarterly as fq


def _statement(ends, revenue, eps):
    cols = [pd.Timestamp(e) for e in ends]
    return pd.DataFrame({c: {'Total Revenue': r, 'Diluted EPS': e, 'Net Income': r / 10}
                         for c, r, e in zip(cols, revenue, eps)})


def _earnings(rows):
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz='America/New_York') for d, *_ in rows], name='Earnings Date')
    return pd.DataFrame({'EPS Estimate': [r[1] for r in rows], 'Reported EPS': [r[2] for r in rows],
                         'Surprise(%)': [r[3] for r in rows]}, index=idx)


def test_statement_und_meldungen_werden_je_quartal_zusammengefuehrt():
    """GS, October 2026: the statement ends with Q2, the Q3 release is already out."""
    st = _statement(['2026-06-30', '2026-03-31'], [15e9, 14e9], [10.9, 14.1])
    er = _earnings([('2027-01-19 07:00', 15.5, None, None),
                    ('2026-10-13 07:00', 13.09, 17.01, 29.98),
                    ('2026-07-14 07:00', 14.54, 20.98, 44.27),
                    ('2026-04-13 07:00', 16.24, 17.55, 8.09)])
    rows, nxt = fq.parse(st, er, today=date(2026, 10, 18))
    by = {r['period_end']: r for r in rows}
    assert nxt == '2027-01-19'
    assert by['2026-09-30']['eps_reported'] == 17.01                  # Q3 only from the release
    assert by['2026-09-30']['report_date'] == '2026-10-13'
    assert by['2026-06-30']['revenue'] == 15e9 and by['2026-06-30']['report_date'] == '2026-07-14'
    assert by['2026-03-31']['report_date_source'] == 'earnings_dates'


def test_ohne_meldung_konservativ_geschaetzt():
    st = _statement(['2025-12-31'], [5e9], [1.2])
    rows, _ = fq.parse(st, None, today=date(2026, 10, 18))
    assert rows[0]['report_date'] == '2026-03-16'                     # + 75 days
    assert rows[0]['report_date_source'] == 'estimated'


def test_abweichendes_geschaeftsjahr():
    """Fiscal quarters ending Jan/Apr/Jul/Oct (e.g. retailers)."""
    st = _statement(['2026-07-31', '2026-04-30'], [3e9, 2.9e9], [1.0, 0.9])
    er = _earnings([('2026-08-27 16:00', 0.95, 1.01, 6.3)])
    rows, _ = fq.parse(st, er, today=date(2026, 10, 18))
    assert {r['period_end'] for r in rows} == {'2026-07-31', '2026-04-30'}
    assert next(r for r in rows if r['period_end'] == '2026-07-31')['eps_reported'] == 1.01


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / 'asset_info.db')
    yield c
    c.close()


def test_erster_wert_gilt_und_leeres_wird_gefuellt(conn):
    fq.record(conn, 'GS', [{'period_end': '2026-06-30', 'report_date': '2026-09-13',
                            'report_date_source': 'estimated', 'revenue': 15e9}],
              checked_at='2026-09-20')
    # later fetch: restated revenue, EPS added, real report date known
    fq.record(conn, 'GS', [{'period_end': '2026-06-30', 'report_date': '2026-07-14',
                            'report_date_source': 'earnings_dates', 'revenue': 15.4e9,
                            'eps_reported': 20.98}], checked_at='2026-10-18')
    row = conn.execute("SELECT revenue, eps_reported, report_date, report_date_source "
                       "FROM fundamentals_quarterly").fetchone()
    assert row == (15e9, 20.98, '2026-07-14', 'earnings_dates')


def test_fehler_mitten_im_ticker_hinterlaesst_nichts(conn):
    """A failure after the first insert must not leak rows into the next commit."""
    rows = [{'period_end': '2026-06-30', 'report_date': '2026-07-14',
             'report_date_source': 'earnings_dates', 'revenue': 1e9},
            {'report_date': '2026-10-13'}]                            # no period_end -> KeyError
    assert fq.record(conn, 'FDS', rows) == 0
    fq.record(conn, 'GS', [{'period_end': '2026-06-30', 'report_date': '2026-07-14',
                            'report_date_source': 'earnings_dates'}])
    assert conn.execute("SELECT ticker FROM fundamentals_quarterly").fetchall() == [('GS',)]
    assert conn.execute("SELECT ticker FROM fundamentals_quarterly_meta").fetchall() == [('GS',)]


def test_faellig_nur_nach_meldetermin_oder_nach_30_tagen(conn):
    fq.ensure_tables(conn)
    conn.executemany("INSERT INTO fundamentals_quarterly_meta VALUES (?, ?, ?)", [
        ('A', '2026-10-13', '2026-10-01'),     # release passed since the check -> due
        ('B', '2026-11-05', '2026-10-11'),     # release still ahead, checked recently
        ('C', None, '2026-09-01'),             # stale
    ])
    assert fq.due_tickers(conn, ['A', 'B', 'C', 'D'], today=date(2026, 10, 18)) == ['A', 'C', 'D']


def test_asof_kennt_nur_veroeffentlichte_quartale(conn, monkeypatch, tmp_path):
    fq.record(conn, 'GS', [{'period_end': '2026-06-30', 'report_date': '2026-07-14',
                            'report_date_source': 'earnings_dates', 'eps_reported': 20.98},
                           {'period_end': '2026-09-30', 'report_date': '2026-10-13',
                            'report_date_source': 'earnings_dates', 'eps_reported': 17.01}])
    monkeypatch.setattr(fq, '_db_path', lambda *a, **k: str(tmp_path / 'asset_info.db'))
    assert fq.asof_frame(['GS'], '2026-10-12')['period_end'].tolist() == ['2026-06-30']
    assert len(fq.asof_frame(['GS'], '2026-10-13')) == 2


def test_vergangener_termin_ohne_eps_datiert_das_quartal():
    """WMT: Yahoo lists the 2026-08-20 release without reported EPS."""
    st = _statement(['2026-07-31', '2026-04-30'], [187.9e9, 177.8e9], [0.8, 0.67])
    er = _earnings([('2026-11-19 07:00', 0.64, None, None),
                    ('2026-08-20 08:00', 0.74, None, None),
                    ('2026-05-21 06:00', 0.66, 0.66, 0.19)])
    rows, nxt = fq.parse(st, er, today=date(2026, 10, 18))
    q2 = next(r for r in rows if r['period_end'] == '2026-07-31')
    assert (q2['report_date'], q2['report_date_source']) == ('2026-08-20', 'earnings_dates')
    assert q2.get('eps_reported') is None and q2['revenue'] == 187.9e9
    assert nxt == '2026-11-19'
