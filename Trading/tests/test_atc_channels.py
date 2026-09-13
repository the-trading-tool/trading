"""Atc: Kanalbreite und konfigurierbare Linien (2026-08).

Der Indikator zeichnete fuer jeden der drei Anker (high/low/zero) alle drei
Linien -- neun Geraden gleichzeitig. Deren Raender liegen oft weit ausserhalb
des Kursbereichs und kreuzen sich am rechten Rand, was den Chart unlesbar
machte.

Jetzt zeichnet jeder Kanal nur noch die Linie, auf der sein Anker sitzt; alles
weitere ist zuschaltbar. Dazu wird die Kanalbreite -- der Abstand der beiden
Parallelen -- als Zahl zwischen die Linien geschrieben.

``atl.py`` war eine Kopie desselben Indikators mit fest verdrahteter Auswahl
und ist damit entfallen.

Run: .venv/Scripts/python.exe -m pytest tests/ -q
"""
import numpy as np
import pandas as pd
import pytest

from tradinglib.indicator.atc import Atc


@pytest.fixture
def df():
    """Aufwaertsbewegung mit Rauschen — liefert drei unterscheidbare Anker."""
    n = 120
    rng = np.random.default_rng(7)
    close = np.linspace(100, 130, n) + rng.normal(0, 1.5, n)
    idx = pd.date_range('2026-01-01', periods=n, freq='D').strftime('%Y-%m-%d %H:%M:%S')
    # Der Index muss 'Date' heissen: add_fig sucht die Datumsspalte nach
    # reset_index() genau unter diesem Namen (so kommt der Frame auch aus
    # fetch_data).
    idx = pd.Index(idx, name='Date')
    return pd.DataFrame({'Open': close, 'High': close + 1.0,
                         'Low': close - 1.0, 'Close': close,
                         'Volume': [1000] * n}, index=idx)


def lines(inst):
    inst.add_fig()
    return sorted(t.name for t in inst.fig.data if t.mode != 'text')


def labels(inst):
    inst.add_fig()
    return [t for t in inst.fig.data if t.mode == 'text']


def test_width_is_the_distance_between_the_parallels(df):
    a = Atc(df=df.copy())
    for name in ('high', 'low', 'zero'):
        top = a.df[f'atc_top_{name}'].dropna()
        bot = a.df[f'atc_bot_{name}'].dropna()
        width = a.df[f'atc_width_{name}'].dropna()
        assert width.iloc[-1] == pytest.approx(top.iloc[-1] - bot.iloc[-1])
        assert (width > 0).all()


def test_width_percent_relates_to_the_price(df):
    a = Atc(df=df.copy())
    close = a.df['Close'].iloc[-1]
    for name in ('high', 'low', 'zero'):
        width = a.df[f'atc_width_{name}'].dropna().iloc[-1]
        pct = a.df[f'atc_width_pct_{name}'].dropna().iloc[-1]
        assert pct == pytest.approx(width / close * 100.0)


def test_narrower_channel_never_shows_the_bigger_number(df):
    """Der Vergleich, an dem die alte Definition scheiterte.

    Bezugsgroesse war die Mittellinie des jeweiligen Kanals. Die liegen weit
    auseinander (KOSPI: 6.063 beim high-Kanal gegen 8.030 beim low-Kanal), also
    bekam der sichtbar schmalere Kanal die groessere Prozentzahl -- 2.171
    Punkte als 35,8 %, 2.846 Punkte als 35,4 %. Mit dem Kurs als gemeinsamem
    Nenner kann das nicht mehr passieren.
    """
    a = Atc(df=df.copy())
    by_abs = sorted(('high', 'low', 'zero'),
                    key=lambda n: a.df[f'atc_width_{n}'].dropna().iloc[-1])
    by_pct = sorted(('high', 'low', 'zero'),
                    key=lambda n: a.df[f'atc_width_pct_{n}'].dropna().iloc[-1])
    assert by_abs == by_pct, 'Reihenfolge muss der sichtbaren Breite folgen'


def test_drawn_channel_has_parallel_edges(df):
    """Der GEZEICHNETE Kanal ist eine Regression: 2 x dev_multi x stdev haengt
    nicht von der Position ab.

    Frueher stand diese Eigenschaft auf den df-Spalten -- sie waren genau diese
    eine Gerade. Seit ATC kausal ist, traegt jede Spalte je Balken ihren eigenen
    Fit (siehe test_columns_are_causal_per_bar); konstant ist nur noch der Kanal,
    den der Chart als heutigen zeichnet.
    """
    a = Atc(df=df.copy())
    seg = a.channels['high']
    width = seg['top'] - seg['bot']
    assert width.max() - width.min() == pytest.approx(0.0, abs=1e-9)


def test_label_reports_the_value_of_the_last_bar(df):
    a = Atc(df=df.copy())
    for t in labels(a):
        name = t.name.replace('atc_width_', '').replace('_label', '')
        expected = a.df[f'atc_width_pct_{name}'].dropna().iloc[-1]
        want = f'{expected:.2f} %' if abs(expected) < 1 else f'{expected:.1f} %'
        assert t.text[0] == want


def test_narrow_channels_keep_two_decimals(df):
    """Im Minutenchart liegen alle drei Kanaele um 0,3 % -- auf eine Stelle
    gerundet waeren sie nicht mehr unterscheidbar."""
    a = Atc(df=df.copy(), dev_multi=0.02)   # kuenstlich schmal
    for t in labels(a):
        assert len(t.text[0].split('.')[1].split(' ')[0]) == 2


def test_labels_do_not_share_one_x_position(df):
    """Sonst liegen die drei Zahlen uebereinander, wenn die Kanaele
    aehnlich breit sind."""
    xs = [t.x[0] for t in labels(Atc(df=df.copy()))]
    assert len(set(xs)) == len(xs)


def test_labels_are_readable(df):
    """Fett, groesser und mit Kontrastschatten -- sie stehen ueber Kerzen."""
    for t in labels(Atc(df=df.copy())):
        assert t.textfont.size >= 14
        assert t.textfont.weight == 'bold'
        assert t.textfont.shadow


def test_wider_deviation_multiplier_widens_the_channel(df):
    narrow = Atc(df=df.copy(), dev_multi=1.0).df['atc_width_high'].dropna().iloc[-1]
    wide = Atc(df=df.copy(), dev_multi=3.0).df['atc_width_high'].dropna().iloc[-1]
    assert wide == pytest.approx(narrow * 3.0, rel=1e-6)


def test_default_draws_only_the_anchor_lines(df):
    """high -> obere, low -> untere, zero -> beide. Vier statt neun Linien."""
    assert lines(Atc(df=df.copy())) == [
        'atc_bot_low', 'atc_bot_zero', 'atc_top_high', 'atc_top_zero']


def test_every_optional_line_can_be_switched_on(df):
    got = lines(Atc(df=df.copy(), show_high_mid=True, show_high_bot=True,
                    show_zero_mid=True, show_low_mid=True, show_low_top=True))
    assert len(got) == 9, 'alle drei Linien je Kanal'


@pytest.mark.parametrize('flag, expected', [
    ('show_high_mid', 'atc_mid_high'),
    ('show_high_bot', 'atc_bot_high'),
    ('show_zero_mid', 'atc_mid_zero'),
    ('show_low_mid',  'atc_mid_low'),
    ('show_low_top',  'atc_top_low'),
])
def test_single_toggle_adds_exactly_that_line(df, flag, expected):
    base = lines(Atc(df=df.copy()))
    got = lines(Atc(df=df.copy(), **{flag: True}))
    assert set(got) - set(base) == {expected}


def test_anchor_lines_are_not_switchable(df):
    """Ohne sie waere der Kanal nicht mehr erkennbar -- kein Schalter dafuer."""
    a = Atc(df=df.copy())
    assert a.draws('high', 'top') and a.draws('low', 'bot')
    assert a.draws('zero', 'top') and a.draws('zero', 'bot')
    for key in ('show_high_top', 'show_low_bot', 'show_zero_top', 'show_zero_bot'):
        assert key not in Atc.params


def test_width_label_sits_between_the_lines(df):
    a = Atc(df=df.copy())
    lbl = labels(a)
    assert len(lbl) == 3, 'je Kanal eine Zahl'
    for t in lbl:
        name = t.name.replace('atc_width_', '').replace('_label', '')
        seg = a.channels[name]
        y = float(t.y[0])
        assert seg['bot'].min() <= y <= seg['top'].max()
        assert t.text[0].endswith('%')


def test_width_label_can_be_switched_off(df):
    assert labels(Atc(df=df.copy(), show_width=False)) == []


def test_short_frame_does_not_produce_a_degenerate_channel(df):
    """Mindestfenster: eine 2-Balken-Regression kippt die Raender weg."""
    a = Atc(df=df.head(30).copy())
    for name in ('high', 'low', 'zero'):
        assert a.df[f'atc_mid_{name}'].notna().sum() >= 10



# ── Kausalitaet (2026-09-13) ────────────────────────────────────────────────
#
# Der Kanal wurde frueher EINMAL ueber den ganzen Frame gelegt und seine Gerade
# in die Spalten geschrieben. Jeder vergangene Balken bekam damit einen Wert aus
# einem Fit, der die spaeteren Balken schon kannte. Gemessen: 98,8-99,6 % der
# Schritte in atc_top_high waren exakt geradlinig, der Wert wich vom damals
# tatsaechlich sichtbaren Kanal im Median um 3-40 % ab, und eine Sell-Regel am
# oberen Rand zeigte im Chart 0 Signale, waehrend sie im Tagesbetrieb 16-mal
# ausloeste.

def _long_frame(n=400, seed=11):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.05, 1.2, n))
    idx = pd.Index(pd.date_range('2024-01-01', periods=n, freq='B')
                   .strftime('%Y-%m-%d %H:%M:%S'), name='Date')
    return pd.DataFrame({'Open': close, 'High': close + rng.uniform(0.2, 1.5, n),
                         'Low': close - rng.uniform(0.2, 1.5, n), 'Close': close,
                         'Volume': 1000}, index=idx)


def test_columns_are_causal_per_bar():
    """Die Zukunft abschneiden darf keinen einzigen vergangenen Wert aendern."""
    frame = _long_frame()
    full = Atc(df=frame.copy(), lookback=120).df
    cut = Atc(df=frame.iloc[:300].copy(), lookback=120).df
    for col in ('atc_top_high', 'atc_mid_high', 'atc_bot_low', 'atc_mid_zero',
                'atc_width_pct_zero'):
        assert np.allclose(full[col].iloc[:300].fillna(-1),
                           cut[col].fillna(-1), rtol=1e-9, atol=1e-9), col


def test_each_bar_matches_a_fresh_fit_on_its_own_trailing_window():
    """Referenz ist die unveraenderte sklearn-Regression auf dem Fenster bis
    zu diesem Balken -- genau das, was ein Chart an diesem Tag gezeigt haette."""
    frame = _long_frame()
    lookback = 120
    causal = Atc(df=frame.copy(), lookback=lookback).df
    for end in (150, 233, 399):
        window = frame.iloc[end - lookback + 1:end + 1].copy()
        fresh = Atc(df=window, lookback=lookback)
        for name in ('high', 'low', 'zero'):
            seg = fresh.channels[name]
            assert causal[f'atc_top_{name}'].iloc[end] == pytest.approx(
                seg['top'].iloc[-1], rel=1e-7), (name, end)
            assert causal[f'atc_mid_{name}'].iloc[end] == pytest.approx(
                seg['mid'].iloc[-1], rel=1e-7), (name, end)


def test_closed_form_matches_sklearn_regression():
    """Die Praefixsummen muessen dieselbe Gerade liefern wie LinearRegression."""
    frame = _long_frame(n=200)
    a = Atc(df=frame.copy(), lookback=200)
    close = frame['Close'].astype(float)
    for length in (10, 57, 200):
        mid, top, bot, *_ = a.calc_regression_channel(close, length)
        y = close.to_numpy() - close.mean()
        m, sd = a._channel_end(len(y) - 1, length, *a._prefix(y))
        assert m + close.mean() == pytest.approx(mid[-1], rel=1e-9)
        assert m + close.mean() + a.dev_multi * sd == pytest.approx(top[-1], rel=1e-9)


def test_the_last_bar_is_unchanged_by_the_switch(df):
    """Heute zeigt der Chart dieselbe Zahl wie vorher -- nur die Historie aendert
    sich. Der Frame (120 Balken) liegt innerhalb des Lookbacks."""
    a = Atc(df=df.copy())
    for name in ('high', 'low', 'zero'):
        seg = a.channels[name]
        assert a.df[f'atc_top_{name}'].iloc[-1] == pytest.approx(seg['top'].iloc[-1])


def test_causal_columns_are_not_one_straight_line():
    frame = _long_frame()
    top = Atc(df=frame.copy(), lookback=120).df['atc_top_high'].dropna()
    second_difference = top.diff().diff().abs().dropna()
    straight = (second_difference < 1e-9 * top.abs().mean()).mean()
    assert straight < 0.5, 'kausale Werte stammen aus je eigenem Fit'


def test_lookback_bounds_how_far_a_channel_reaches_back():
    frame = _long_frame()
    a = Atc(df=frame.copy(), lookback=60)
    for seg in a.channels.values():
        assert len(seg) <= 60


def test_intraday_frames_are_channelled_on_their_own_bars():
    """Nur Tageskurse laden die volle Historie nach -- ein Stundenchart bleibt
    ein Stundenkanal."""
    frame = _long_frame(n=150)
    frame.index = pd.Index(pd.date_range('2026-01-05 09:00', periods=150, freq='h')
                           .strftime('%Y-%m-%d %H:%M:%S'), name='Date')
    a = Atc(df=frame.copy(), symbol='DOES-NOT-EXIST', lookback=100)
    assert a.df['atc_top_high'].notna().sum() > 100
