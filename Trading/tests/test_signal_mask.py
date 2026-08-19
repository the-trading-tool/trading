"""Tests fuer die geteilte Signal-Evaluierung (tools.compute_signal_mask) und die
Aequivalenz von BuySellSignalGenerator (Strategy Finder) sowie der Multi-Strategies-
Markierung zur jeweils urspruenglichen Logik.

Self-contained: synthetische Daten, keine DB-Abhaengigkeit.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from tradinglib.tools import compute_signal_mask, BuySellSignalGenerator, ExpressionEvaluator, split_conditions


def make_df():
    """3 Ticker x 6 Bars mit ewo_angle-Indikator + tp/sl-Spalten."""
    ewo = {
        "AAA": [25, 10, -25, 5, 30, -30],
        "BBB": [-5, 22, 22, -22, -10, 15],
        "CCC": [21, -21, 21, -21, 21, -21],
    }
    rows = []
    for tk, vals in ewo.items():
        for d, v in enumerate(vals):
            rows.append({
                "ticker": tk,
                "Date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=d),
                "Close": 100.0 + d,
                "ewo_angle": float(v),
                "take_profit": np.nan,
                "stop_loss": np.nan,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- compute_signal_mask

def test_mask_row_wise_matches_direct():
    df = make_df()
    got = compute_signal_mask(df, "ewo_angle > 20")
    exp = (df["ewo_angle"] > 20)
    assert got.tolist() == exp.tolist()


def test_mask_empty_condition_all_false():
    df = make_df()
    assert compute_signal_mask(df, "").sum() == 0


def test_mask_shift_is_per_ticker():
    """rolling/shift muss innerhalb eines Tickers bleiben (kein Cross-Ticker-Bleed)."""
    df = make_df()
    got = compute_signal_mask(df, "ewo_angle > ewo_angle.shift(1)")
    # Erwartung: shift pro Ticker -> erste Zeile jeder Gruppe ist False (NaN-Vergleich)
    exp = df["ewo_angle"] > df.groupby("ticker")["ewo_angle"].shift(1)
    exp = exp.fillna(False)
    assert got.tolist() == exp.tolist()
    # Boundary-Check: erste Zeile von BBB/CCC darf NICHT vom Vorgaenger-Ticker abhaengen
    first_idx = df.groupby("ticker").head(1).index
    assert not got.loc[first_idx].any()


# ---------------------------------------------------------------- BuySellSignalGenerator

def _reference_apply(df, buy, sell, delay):
    """Originalgetreue Pro-Zeilen-Logik (vor der Vektorisierung)."""
    df = df.copy()
    if "close" not in df.columns:
        df["close"] = df["Close"]
    df = df.sort_values(["ticker", "Date"]).reset_index(drop=True)
    df["buySell"] = 0
    delayed = []
    for _tk, group in df.groupby("ticker"):
        group = group.reset_index()
        ev = ExpressionEvaluator(group, dataframe_name="c")
        bm = eval(ev.validate_and_transform(buy), {}, {"c": group})
        for idx in group[bm].index:
            di = idx + delay
            if di < len(group):
                delayed.append(group.loc[di, "index"])
    df.loc[delayed, "buySell"] = 1
    op = {}
    for i, row in df.iterrows():
        tk = row["ticker"]
        if tk in op:
            r = pd.DataFrame([row])
            ev = ExpressionEvaluator(r, dataframe_name="r")
            res = eval(ev.validate_and_transform(sell), {}, {"r": r})
            if bool(res.iloc[0] if isinstance(res, pd.Series) else res):
                df.at[i, "buySell"] = -1
                del op[tk]
                continue
        if df.at[i, "buySell"] == 1 and tk not in op:
            op[tk] = 1
    return df.sort_values(["ticker", "Date"])["buySell"].to_numpy()


def test_buysell_equivalent_to_reference():
    df = make_df()
    buy, sell, delay = "(ewo_angle>20)", "(ewo_angle<-20)", 1
    ref = _reference_apply(df, buy, sell, delay)
    new = BuySellSignalGenerator(df.copy(), buy, sell, buy_delay_days=delay).apply_signals()
    got = new.sort_values(["ticker", "Date"])["buySell"].to_numpy()
    assert np.array_equal(ref, got)


def test_buysell_tp_sl_fallback_runs():
    """Sell-Bedingung mit entry-relativem sl nutzt den Pro-Zeilen-Fallback ohne Fehler."""
    df = make_df()
    df["stop_loss"] = 90.0
    out = BuySellSignalGenerator(df.copy(), "(ewo_angle>20)", "(Close <= sl)",
                                 buy_delay_days=0).apply_signals()
    assert "buySell" in out.columns
    assert set(out["buySell"].unique()).issubset({-1, 0, 1})


# ---------------------------------------------------------------- Multi-Strategies marking

def _old_marking(df, buy, sell):
    """Urspruengliche Whole-df-Markierung (np.where + eval)."""
    df = df.copy()
    df["buySell"] = 0
    ev = ExpressionEvaluator(df, dataframe_name="combined_df")
    combined_df = df  # noqa: F841 (vom transformierten Ausdruck referenziert)
    df["buySell"] = np.where(eval(ev.validate_and_transform(buy)), 1, df["buySell"])
    df["buySell"] = np.where(eval(ev.validate_and_transform(sell)), -1, df["buySell"])
    return df["buySell"].to_numpy()


def _new_marking(df, buy, sell):
    bm = compute_signal_mask(df, buy)
    sm = compute_signal_mask(df, sell)
    return np.where(sm.values, -1, np.where(bm.values, 1, 0))


def test_multistrategies_marking_equivalent_rowwise():
    df = make_df()
    buy, sell = "(ewo_angle >= 20)", "(ewo_angle <= -20)"
    assert np.array_equal(_old_marking(df, buy, sell), _new_marking(df, buy, sell))


# ---------------------------------------------------------------- Signalfenster
def _win_df():
    """Zwei Ticker, je 6 Bars. Bedingung A trifft frueh, B spaet -- nie am
    selben Balken. Genau der Fall aus der Praxis (COP/MDT)."""
    return pd.DataFrame({
        'ticker': ['X'] * 6 + ['Y'] * 6,
        'Date': list(pd.date_range('2026-01-01', periods=6)) * 2,
        'a': [1, 0, 0, 0, 0, 0] + [0, 0, 0, 0, 0, 0],
        'b': [0, 0, 1, 0, 0, 0] + [0, 0, 1, 0, 0, 0],
    })


def test_window_1_unveraendert():
    """Fenster 1 verknuepft die Zeilen mit & -- identisch zur Einzeiler-Form."""
    df = _win_df()
    multi = compute_signal_mask(df, "a > 0\nb > 0", window=1)
    single = compute_signal_mask(df, "(a > 0) & (b > 0)")
    assert multi.tolist() == single.tolist()
    assert not multi.any()          # treffen nie auf demselben Balken zusammen


def test_window_verbindet_verschiedene_balken():
    """Mit Fenster 3 feuert X genau dort, wo beide Bedingungen im Fenster liegen.

    Das Fenster laeuft mit: a trifft auf Bar 0, b auf Bar 2 -- auf Bar 2 deckt
    das Dreier-Fenster die Bars 0-2 ab, beide sind drin. Auf Bar 3 deckt es die
    Bars 1-3 ab, a faellt heraus, das Signal endet. Ein Signal haelt also nur so
    lange, wie alle Bedingungen noch im Fenster liegen.
    """
    got = compute_signal_mask(_win_df(), "a > 0\nb > 0", window=3)
    assert got.tolist()[:6] == [False, False, True, False, False, False]
    # Y: a nie erfuellt -> kein Signal
    assert not any(got.tolist()[6:])


def test_window_leuchtet_nicht_in_anderen_ticker():
    """Das Nachleuchten darf die Ticker-Grenze nicht ueberschreiten."""
    df = _win_df()
    df.loc[df.ticker == 'Y', 'a'] = [0, 0, 0, 0, 0, 0]
    got = compute_signal_mask(df, "a > 0\nb > 0", window=6)
    assert not any(got[df.ticker == 'Y'])


def test_window_einzeilig_bleibt_einzeilig():
    """Eine einzeilige Bedingung ignoriert das Fenster (nichts nachzuleuchten)."""
    df = _win_df()
    assert (compute_signal_mask(df, "a > 0", window=5).tolist()
            == compute_signal_mask(df, "a > 0", window=1).tolist())


# ---------------------------------------------------------------- Formel-Zerlegung
def test_split_echte_umbrueche():
    got = split_conditions("(a > 0)\n(b > 0)\n\n  (c > 0)  ")
    assert got == ["(a > 0)", "(b > 0)", "(c > 0)"]


def test_split_literales_n_wird_umbruch():
    """Aus dem JSON kopiert: die zwei Zeichen Backslash+n statt eines Umbruchs."""
    assert split_conditions("(a > 0)\n(b > 0)") == ["(a > 0)", "(b > 0)"]


def test_split_verknuepfung_am_zeilenanfang_haengt_an():
    """'& (b)' am Zeilenanfang gehoert zur vorigen Zeile, ist allein kein Ausdruck."""
    assert split_conditions("(a > 0)\n& (b > 0)\n(c > 0)") == ["(a > 0) & (b > 0)", "(c > 0)"]


def test_split_verknuepfung_am_zeilenende_haengt_an():
    assert split_conditions("(a > 0) &\n(b > 0)\n(c > 0)") == ["(a > 0) & (b > 0)", "(c > 0)"]


def test_split_haengende_verknuepfung_am_ende_faellt_weg():
    assert split_conditions("(a > 0)\n(b > 0) &") == ["(a > 0)", "(b > 0)"]


def test_split_leer():
    assert split_conditions("") == [] and split_conditions(None) == []


def test_split_einzeilig_bleibt_eine_bedingung():
    q = "(a > 0) & (b > 0) & (c > 0)"
    assert split_conditions(q) == [q]
