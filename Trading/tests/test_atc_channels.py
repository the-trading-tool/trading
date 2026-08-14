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


def test_width_percent_relates_to_the_middle_line(df):
    a = Atc(df=df.copy())
    for name in ('high', 'low', 'zero'):
        mid = a.df[f'atc_mid_{name}'].dropna().iloc[-1]
        width = a.df[f'atc_width_{name}'].dropna().iloc[-1]
        pct = a.df[f'atc_width_pct_{name}'].dropna().iloc[-1]
        assert pct == pytest.approx(width / mid * 100.0)


def test_absolute_width_is_constant_but_percent_is_not(df):
    """2 x dev_multi x stdev haengt nicht von der Position ab -- der
    Prozentwert schon, denn er bezieht sich auf die wandernde Mittellinie.

    Deshalb nimmt die Beschriftung ihren Wert vom letzten Balken, auch wenn
    sie weiter links steht.
    """
    a = Atc(df=df.copy())
    w = a.df['atc_width_high'].dropna()
    assert w.max() - w.min() == pytest.approx(0.0, abs=1e-9)
    p = a.df['atc_width_pct_high'].dropna()
    assert p.max() - p.min() > 0.0


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
        row = a.df[a.df[f'atc_top_{name}'].notna()]
        y = float(t.y[0])
        assert row[f'atc_bot_{name}'].min() <= y <= row[f'atc_top_{name}'].max()
        assert t.text[0].endswith('%')


def test_width_label_can_be_switched_off(df):
    assert labels(Atc(df=df.copy(), show_width=False)) == []


def test_short_frame_does_not_produce_a_degenerate_channel(df):
    """Mindestfenster: eine 2-Balken-Regression kippt die Raender weg."""
    a = Atc(df=df.head(30).copy())
    for name in ('high', 'low', 'zero'):
        assert a.df[f'atc_mid_{name}'].notna().sum() >= 10
