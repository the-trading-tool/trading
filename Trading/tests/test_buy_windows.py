"""Buy windows: periods within each year in which buys may happen."""
import numpy as np
import pandas as pd

from tradinglib import strategy_spec as ss
from tradinglib import tools

WINDOWS = [['03-15', '08-01'], ['11-01', '12-20']]


def test_in_windows_bounds_and_gaps():
    d = ['2024-03-14', '2024-03-15', '2024-08-01', '2024-08-02', '2024-10-31',
         '2024-11-01', '2024-12-20', '2024-12-21']
    assert ss.in_windows(d, WINDOWS).tolist() == [False, True, True, False, False,
                                                  True, True, False]


def test_in_windows_over_new_year_and_leap_day():
    d = ['2024-11-14', '2024-11-15', '2025-01-05', '2025-02-10', '2025-02-11']
    assert ss.in_windows(d, [['11-15', '02-10']]).tolist() == [False, True, True, True, False]
    assert ss.in_windows(['2024-02-29'], [['02-29', '02-29']]).tolist() == [True]


def test_in_windows_without_windows_is_all_year_and_nat_is_outside():
    assert ss.in_windows(['2024-01-01', None], []).tolist() == [True, True]
    assert ss.in_windows(['2024-04-01', None], WINDOWS).tolist() == [True, False]


def test_normalize_windows_accepts_german_dates_and_drops_invalid():
    got = ss.normalize_windows([['15.03.', '01.08.'], ['11-01', '12-20'], ['02-30', '03-01'],
                                ['x'], ['11-01', '12-20']])
    assert got == WINDOWS
    assert ss.windows_label(got) == '15.03.–01.08., 01.11.–20.12.'


def test_spec_carries_windows_to_multi_and_back():
    s = ss.normalize({'entry': {'buy': '(rsi < 30)', 'windows': WINDOWS},
                      'exit': {'sell': '(rsi > 70)'}})
    assert ss.is_classic(s)
    entry = ss.to_ms_entry(s)
    assert entry['buy_windows'] == WINDOWS
    back = ss.from_ms_entry('S', '^GDAXI', entry)
    assert back['entry']['windows'] == WINDOWS
    # A plain entry without windows writes no field.
    assert 'buy_windows' not in ss.to_ms_entry(ss.normalize({'entry': {'buy': 'a'}}))


def test_reference_entry_can_override_windows_per_index():
    saved = {'S': ss.normalize({'entry': {'buy': '(rsi < 30)', 'windows': WINDOWS},
                                'exit': {'sell': '(rsi > 70)'}})}
    tx = {'Strat': {'^GDAXI': {'strategy': 'S'},
                    '^MDAXI': {'strategy': 'S', 'buy_windows': [['01-01', '06-30']]}}}
    live = ss.expand_references(tx, saved, live=True)['Strat']
    assert live['^GDAXI']['buy_windows'] == WINDOWS
    assert live['^MDAXI']['buy_windows'] == [['01-01', '06-30']]
    bt = ss.expand_references(tx, saved)['Strat']
    assert bt['^MDAXI']['_spec']['entry']['windows'] == [['01-01', '06-30']]


def test_signature_changes_with_windows():
    a = ss.normalize({'entry': {'buy': 'a'}})
    b = ss.normalize({'entry': {'buy': 'a', 'windows': WINDOWS}})
    assert ss.signature(a) != ss.signature(b)
    # All year leaves the text as it was, so earlier runs keep matching their strategy.
    assert '"windows"' not in ss.signature(a)


def _bars(dates, buy_on, sell_on=()):
    return pd.DataFrame({'ticker': 'T', 'Date': pd.to_datetime(dates), 'close': 100.0,
                         'b': [int(d in buy_on) for d in dates],
                         's': [int(d in sell_on) for d in dates]})


def test_generator_drops_buys_outside_windows_on_the_buy_bar():
    dates = ['2024-03-13', '2024-03-14', '2024-03-15', '2024-03-18']
    df = _bars(dates, buy_on={'2024-03-13', '2024-03-14'})
    # Delay 1: the signal of 14 Mar buys on 15 Mar (inside), that of 13 Mar on 14 Mar (outside).
    out = tools.BuySellSignalGenerator(df, '(b > 0)', '(s > 0)', buy_delay_days=1,
                                       buy_windows=[['03-15', '08-01']]).apply_signals()
    buys = out.loc[out['buySell'] == 1, 'Date'].dt.strftime('%m-%d').tolist()
    assert buys == ['03-15']


def test_generator_left_out_buy_opens_no_position():
    dates = ['2024-02-01', '2024-02-02', '2024-03-20', '2024-03-21']
    df = _bars(dates, buy_on={'2024-02-01', '2024-03-20'}, sell_on={'2024-02-02', '2024-03-21'})
    out = tools.BuySellSignalGenerator(df, '(b > 0)', '(s > 0)',
                                       buy_windows=[['03-15', '08-01']]).apply_signals()
    got = dict(zip(out['Date'].dt.strftime('%m-%d'), out['buySell']))
    # The February buy is outside -> no position -> its sell does not fire either.
    assert got == {'02-01': 0, '02-02': 0, '03-20': 1, '03-21': -1}


def test_generator_without_windows_is_unchanged():
    dates = ['2024-02-01', '2024-02-02', '2024-03-20', '2024-03-21']
    df = _bars(dates, buy_on={'2024-02-01', '2024-03-20'}, sell_on={'2024-02-02', '2024-03-21'})
    a = tools.BuySellSignalGenerator(df, '(b > 0)', '(s > 0)').apply_signals()
    b = tools.BuySellSignalGenerator(df, '(b > 0)', '(s > 0)', buy_windows=[]).apply_signals()
    assert a['buySell'].tolist() == b['buySell'].tolist() == [1, -1, 1, -1]


def test_random_entries_respect_windows():
    dates = pd.bdate_range('2024-01-01', '2024-12-31')
    sig = pd.DataFrame({'ticker': 'T', 'Date': dates, 'buySell': 0})
    out = ss.random_entry_signals(sig, 0.5, seed=1, windows=WINDOWS)
    bought = out.loc[out['buySell'] == 1, 'Date']
    assert len(bought) > 0
    assert ss.in_windows(bought.to_numpy(), WINDOWS).all()
    assert np.isclose(len(bought) / ss.in_windows(dates, WINDOWS).sum(), 0.5, atol=0.1)
