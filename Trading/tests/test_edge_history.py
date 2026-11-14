"""Wochen-Verlauf der Edge Scores: Freitage, Inkrement, Speichern/Laden."""
import datetime as dt

import pandas as pd

from tradinglib import edge_history as eh


def test_last_friday_excludes_running_friday():
    assert eh.last_friday(dt.date(2026, 11, 13)) == dt.date(2026, 11, 6)    # Freitag selbst noch offen
    assert eh.last_friday(dt.date(2026, 11, 14)) == dt.date(2026, 11, 13)
    assert eh.last_friday(dt.date(2026, 11, 18)) == dt.date(2026, 11, 13)


def test_weeks_needed():
    upto = dt.date(2026, 11, 13)
    assert eh.weeks_needed(None, upto, 364) == 364
    assert eh.weeks_needed(upto, upto, 364) == 0
    assert eh.weeks_needed(dt.date(2026, 10, 30), upto, 364) == 2


def test_save_load_roundtrip_and_upsert(tmp_path):
    p = str(tmp_path / "edge.db")
    rows = pd.DataFrame({"date": pd.to_datetime(["2026-11-06", "2026-11-13"]), "key": "DAX",
                         "grp": "indices_world", "cot": [None, None], "macro": [None, None],
                         "trend": [2.0, 3.0], "total": [5.0, 7.5], "bias": ["bull", "bull"], "n": [1, 1]})
    con = eh._connect(p)
    assert eh.save(con, rows) == 2
    assert eh.save(con, rows.assign(total=[5.0, 2.5])) == 2                # upsert, keine Dubletten
    assert eh.last_dates(con) == {"DAX": dt.date(2026, 11, 13)}
    con.close()
    df = eh.load(path=p)
    assert len(df) == 2 and df["total"].tolist() == [5.0, 2.5] and pd.isna(df["cot"]).all()
    assert eh.load(keys=["GOLD"], path=p).empty
    assert eh.load(path=str(tmp_path / "missing.db")).empty


def test_update_is_incremental(tmp_path, monkeypatch):
    calls = []

    def fake_rows(key, group, upto, weeks, obs):
        calls.append((key, weeks))
        d = [pd.Timestamp(upto - dt.timedelta(weeks=i)) for i in range(weeks)][::-1]
        return pd.DataFrame({"date": d, "key": key, "grp": group, "cot": None, "macro": None,
                             "trend": 1.0, "total": 2.5, "bias": "bull", "n": 1})

    monkeypatch.setattr(eh, "market_rows", fake_rows)
    monkeypatch.setattr(eh.md, "load_obs", lambda: pd.DataFrame())
    monkeypatch.setattr(eh.cd, "ensure_fresh", lambda: None)
    monkeypatch.setattr(eh.md, "ensure_fresh", lambda: None)
    p = str(tmp_path / "edge.db")
    eh.update(today=dt.date(2026, 11, 14), keys=["DAX"], weeks=4, path=p)
    eh.update(today=dt.date(2026, 11, 21), keys=["DAX"], path=p)
    assert calls == [("DAX", 4), ("DAX", 1)]
    assert len(eh.load(path=p)) == 5
