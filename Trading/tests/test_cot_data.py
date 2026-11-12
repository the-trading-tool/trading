"""COT Edge Scorecard: Kennzahlen und Scoring ohne Netz/DB."""
import datetime as dt

import pandas as pd
import pytest

from tradinglib import cot_data as c


def _frame(longs, shorts, start="2026-01-06"):
    dates = pd.date_range(start, periods=len(longs), freq="7D").strftime("%Y-%m-%d")
    n = len(longs)
    return pd.DataFrame({
        "report_date": dates, "nc_long": longs, "nc_short": shorts,
        "comm_long": [0] * n, "comm_short": [0] * n,
    })


def test_enrich_matches_edgefinder_columns():
    # USD-Index-Werte aus dem CFTC-Report 13.10. / 20.10.2026
    df = c.enrich(_frame([32596, 32422], [19677, 17378]))
    last = df.iloc[-1]
    assert last["d_long"] == -174
    assert last["d_short"] == -2299
    assert last["net"] == 15044
    assert last["long_pct"] == pytest.approx(65.10, abs=0.01)
    assert last["d_long_pct"] == pytest.approx(2.75, abs=0.01)
    assert last["release_date"].date() == dt.date(2026, 1, 16)


@pytest.mark.parametrize("rank,score", [
    (100, 2), (80, 2), (79.9, 1), (60, 1), (59.9, 0), (50, 0),
    (40.1, 0), (40, -1), (20.1, -1), (20, -2), (0, -2), (float("nan"), 0),
])
def test_positioning_score_thresholds(rank, score):
    assert c.positioning_score(rank) == score


def test_rolling_rank_is_causal_and_bounded():
    s = pd.Series(range(1, 61), dtype=float)                 # stetig steigend
    r = c.rolling_rank(s, window=10, min_periods=5)
    assert r.iloc[:4].isna().all()
    assert r.iloc[-1] == pytest.approx(100)
    # Zukunft abschneiden aendert keinen vergangenen Wert
    pd.testing.assert_series_equal(c.rolling_rank(s.iloc[:30], 10, 5), r.iloc[:30])


def test_structurally_long_market_scores_bearish_when_long_share_is_low_for_it():
    """Silber-Fall 11/2026: 70 % long ist absolut hoch, fuer Silber aber niedrig."""
    longs = [80] * 60 + [70]                                # 80 % long ueber Monate, dann 70 %
    shorts = [20] * 60 + [30]
    last = c.enrich(_frame(longs, shorts)).iloc[-1]
    assert last["long_pct"] == pytest.approx(70)
    assert last["long_rank"] < 20
    assert last["score_pos"] == -2


@pytest.mark.parametrize("delta,score", [(1.01, 1), (1.0, 0), (-1.0, 0), (-1.01, -1), (float("nan"), 0)])
def test_momentum_score(delta, score):
    assert c.momentum_score(delta) == score


def test_cot_index_spans_0_to_100():
    longs = list(range(100, 100 + 30 * 10, 10))       # steigende Netto-Position
    df = c.enrich(_frame(longs, [100] * 30))
    assert df["cot_index"].iloc[-1] == pytest.approx(100)
    df = c.enrich(_frame(longs[::-1], [100] * 30))
    assert df["cot_index"].iloc[-1] == pytest.approx(0)
    # Vor 26 Wochen kein Index (zu wenig Historie)
    assert df["cot_index"].iloc[:25].isna().all()


def test_pair_bias_market_convention_and_sign():
    card = pd.DataFrame({"key": ["USD", "EUR", "JPY", "GBP"], "score": [2, -2, 3, 0]})
    pairs = c.pair_bias(card).set_index("pair")
    assert set(pairs.index) == {"EURGBP", "EURUSD", "EURJPY", "GBPUSD", "GBPJPY", "USDJPY"}
    assert pairs.loc["EURUSD", "score"] == -4
    assert pairs.loc["EURUSD", "bias"] == "strong_bear"
    assert pairs.loc["USDJPY", "score"] == -1
    assert pairs.loc["USDJPY", "bias"] == "neutral"
    # nach Betrag sortiert
    assert pairs["score"].abs().is_monotonic_decreasing


def test_is_stale_respects_release_and_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "_DB_DIR", str(tmp_path))
    assert c.is_stale() is True                                 # leere DB
    con = c._connect()
    con.execute("INSERT INTO cot_weekly (code, report_date) VALUES ('098662', '2026-11-03')")
    con.commit()
    con.close()
    # nächster Report (10.11.) erscheint am 13.11.
    assert c.is_stale(today=dt.date(2026, 11, 12)) is False
    assert c.is_stale(today=dt.date(2026, 11, 13)) is True
    con = c._connect()
    c._set_meta(con, "last_attempt", dt.datetime.now().isoformat())
    con.commit()
    con.close()
    assert c.is_stale(today=dt.date(2026, 11, 13)) is False     # gerade erst versucht


def test_edge_scores_card_resolution():
    from tradinglib import edge_scores_page as e
    assert e.resolve_card(None, None) == "cot"
    assert e.resolve_card("cot", None) == "cot"
    assert e.resolve_card("macro", None) == "macro"
    assert e.resolve_card("trend", None) == "trend"
    assert e.resolve_card("total", None) == "total"
    assert e.resolve_card("bogus", "cot") == "cot"
    # Sitzungszustand (Klick im Untermenue) schlaegt den URL-Parameter
    assert e.resolve_card("cot", "cot") == "cot"


def test_edge_scores_locale_keys_exist():
    import json
    from tradinglib import edge_scores_page as e
    keys = ["nav.edge_scores", "page.edge_scores", "error.load_edge_scores",
            "cfg.start_page_opt.edge_scores", "edge.menu_hint", "edge.planned",
            *[c[1] for c in e.CARDS]]
    for lang in ("de", "en"):
        d = json.load(open(f"locales/{lang}.json", encoding="utf-8"))
        assert not [k for k in keys if k not in d], lang
