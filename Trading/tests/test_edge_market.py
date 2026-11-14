"""Marktumfeld je Titel: Freitags-Score kausal auf Tage, Index-Prioritaet."""
import sqlite3

import pandas as pd

from tradinglib import edge_market as em

W = pd.DataFrame({"date": pd.to_datetime(["2026-10-30", "2026-11-06", "2026-11-13"]),
                  "key": "DAX", "total": [2.5, -5.0, 7.5]})


def test_series_uses_last_friday_on_or_before_day():
    d = pd.DatetimeIndex(["2026-10-29", "2026-10-30", "2026-11-05", "2026-11-06", "2026-11-12", "2026-11-16"])
    s = em.series_for(d, "DAX", W)
    assert pd.isna(s.iloc[0])                                 # vor dem ersten Freitag
    assert s.tolist()[1:] == [2.5, 2.5, -5.0, -5.0, 7.5]
    assert em.series_for(d, None, W).isna().all() and em.series_for(d, "SPX", W).isna().all()


def test_attach_maps_by_ticker():
    panel = pd.DataFrame({"ticker": ["SAP.DE", "SAP.DE", "XYZ"],
                          "Date": pd.to_datetime(["2026-11-05", "2026-11-13", "2026-11-13"])})
    out = em.attach(panel, weekly=W, mapping={"SAP.DE": "DAX"})
    assert out["edge_market"].tolist()[:2] == [2.5, 7.5] and pd.isna(out["edge_market"].iloc[2])


def test_ticker_markets_priority(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / "yf_tickers.db")
    con.executescript("""
        CREATE TABLE stocks(id INTEGER, Ticker TEXT); CREATE TABLE indices(id INTEGER, name TEXT);
        CREATE TABLE stock_indices(stock_id INTEGER, index_id INTEGER);
        INSERT INTO stocks VALUES (1,'SAP.DE'),(2,'AAPL'),(3,'SDX.DE');
        INSERT INTO indices VALUES (1,'^STOXX50E'),(2,'^GDAXI'),(3,'^NDX'),(4,'^SPX'),(5,'^SDAXI');
        INSERT INTO stock_indices VALUES (1,1),(1,2),(2,3),(2,4),(3,5);""")
    con.close()
    monkeypatch.setenv("TradingDB", str(tmp_path))
    assert em.ticker_markets() == {"SAP.DE": "DAX", "AAPL": "SPX"}     # SDAX ohne Edge-Markt
