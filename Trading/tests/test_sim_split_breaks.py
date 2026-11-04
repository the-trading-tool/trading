"""Unadjusted splits in the simulation data (data_quality.sim_split_breaks)."""
import pandas as pd

from tradinglib import data_quality as dq


def _sim(ticker, closes, start='2026-09-07'):
    d = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({'ticker': ticker, 'Date': d.strftime('%Y-%m-%d 00:00:00'), 'close': closes})


def _daily(closes, start='2026-09-07'):
    return pd.DataFrame({'Close': closes}, index=pd.bdate_range(start, periods=len(closes)))


def test_reverse_split_found_real_crash_kept():
    # GOSS-like: stored 0.16 -> 11.41 (1:80 reverse split), adjusted series calm.
    goss = _sim('GOSS', [0.15, 0.16, 0.16, 11.41, 11.0])
    adj = {'GOSS': _daily([12.0, 12.8, 12.8, 11.41, 11.0]),
           # CRASH: -80 % in both series -> a real move, no break
           'CRASH': _daily([10.0, 10.0, 2.0, 2.1, 2.0])}
    crash = _sim('CRASH', [10.0, 10.0, 2.0, 2.1, 2.0])
    df = pd.concat([goss, crash])
    b = dq.sim_split_breaks(df, load_daily=lambda tk: adj[tk])
    assert b['ticker'].tolist() == ['GOSS']
    r = b.iloc[0]
    assert str(r['date'])[:10] == '2026-09-10' and round(r['sim_factor'], 1) == 71.3
    assert 0.8 < r['adjusted_move'] < 1.0


def test_flip_flop_and_drop_before_last_break():
    # SKIN-like: rows alternate between the old and the new scale.
    skin = _sim('SKIN', [0.70, 14.0, 0.68, 13.6, 13.5, 13.4])
    calm = _daily([14.0, 14.0, 13.6, 13.6, 13.5, 13.4])
    other = _sim('OK', [5.0, 5.1, 5.2, 5.1, 5.0, 5.2])
    df = pd.concat([skin, other], ignore_index=True)
    b = dq.sim_split_breaks(df, load_daily=lambda tk: calm if tk == 'SKIN' else _daily([5.0] * 6))
    assert b['ticker'].unique().tolist() == ['SKIN'] and len(b) == 3
    kept = dq.drop_before_breaks(df, b)
    assert kept[kept.ticker == 'SKIN']['close'].tolist() == [13.6, 13.5, 13.4]
    assert len(kept[kept.ticker == 'OK']) == 6
    assert 'SKIN' in dq.describe_breaks(b) and '(×20' in dq.describe_breaks(b)


def test_no_price_series_no_claim_and_empty_inputs():
    df = _sim('X', [1.0, 10.0, 10.0])
    assert dq.sim_split_breaks(df, load_daily=lambda tk: pd.DataFrame()).empty
    assert dq.sim_split_breaks(None).empty
    assert dq.drop_before_breaks(df, dq.sim_split_breaks(None)) is df
    assert dq.describe_breaks(dq.sim_split_breaks(None)) == ''
