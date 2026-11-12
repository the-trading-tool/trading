"""Makro-Scorecard — Daten und Scoring je Währung (Edge Scores, Stufe 2).

Alle Quellen sind kostenlos und ohne API-Key:

| Indikator          | Quelle                                        |
|--------------------|-----------------------------------------------|
| Leitzins           | BIS ``WS_CBPOL`` (Monatsende)                 |
| Inflation (J/J)    | BIS ``WS_LONG_CPI`` (771 = Veränderung J/J %) |
| Arbeitslosenquote  | FRED (OECD-Reihen, ``fredgraph.csv``); Euroraum: Eurostat ``une_rt_m`` |
| BIP-Wachstum (Q/Q) | OECD ``DF_QNA_EXPENDITURE_GROWTH_OECD``; Euroraum: Eurostat ``namq_10_gdp`` |
| Frühindikator CLI  | OECD ``DF_CLI`` (EUR über G4E = vier große EU-Länder; CHF/NZD gibt es nicht) |

Gespeichert in ``database/macro.db`` (``macro_obs``: Währung, Indikator, Periode,
Wert). Jede Quelle wird einzeln abgerufen — fällt eine aus, bleiben die anderen
aktuell, der Fehler steht in ``macro_meta``.

Scoring je Währung (eigene, einfache Methodik; Sicht "was stützt die Währung"):

| Komponente | Regel                                                              | Punkte |
|------------|--------------------------------------------------------------------|--------|
| Zinsen     | Leitzins über/unter dem Median der 8 Währungen                     | ±1     |
|            | Leitzins ggü. vor 6 Monaten gestiegen/gesenkt                       | ±1     |
| Inflation  | J/J-Rate ggü. vor 3 Monaten (Quartalsreihen: 1 Quartal) > ±0,2 Pp  | ±1     |
| Arbeit     | Arbeitslosenquote ggü. vor 3 Monaten (Quartal: 1) gefallen/gestiegen > 0,1 Pp | ±1 |
| Wachstum   | BIP Q/Q ≥ 0,5 % → +1, < 0 % → −1                                    | ±1     |
| Frühindik. | CLI > 100 und steigend → +1, < 100 und fallend → −1                 | ±1     |

Summe −6 … +6. Steigende Inflation zählt positiv, weil sie Zinserhöhungen
wahrscheinlicher macht (Zinsdifferenz-Logik), nicht weil sie "gut" ist.

CLI: ``python -m tradinglib.macro_data``
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import os
import sqlite3
import sys
from contextlib import closing

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_DB_DIR = os.environ.get("TradingDB") or "database"
START = "2015-01"
REFRESH_HOURS = 24          # Monatsdaten — einmal täglich reicht
RETRY_HOURS = 6
_TIMEOUT = 90
_CSV = {"Accept": "application/vnd.sdmx.data+csv; charset=utf-8"}

CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
INDICATORS = ["rate", "cpi", "unemp", "gdp", "cli"]

_BIS = {"US": "USD", "XM": "EUR", "GB": "GBP", "JP": "JPY", "CH": "CHF",
        "CA": "CAD", "AU": "AUD", "NZ": "NZD"}
_OECD = {"USA": "USD", "GBR": "GBP", "JPN": "JPY", "CHE": "CHF", "CAN": "CAD",
         "AUS": "AUD", "NZL": "NZD", "G4E": "EUR"}
_FRED_UNEMP = {"USD": "UNRATE", "GBP": "LRHUTTTTGBM156S", "JPY": "LRHUTTTTJPM156S",
               "CHF": "LRHUTTTTCHQ156S", "CAD": "LRHUTTTTCAM156S", "AUD": "LRHUTTTTAUM156S",
               "NZD": "LRHUTTTTNZQ156S"}
# Euroraum-Code wechselt mit Beitritten (Bulgarien 2026 → EA21); erster Treffer gewinnt
_EA_CODES = ["EA21", "EA20"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS macro_obs (
    ccy TEXT NOT NULL, indicator TEXT NOT NULL, period TEXT NOT NULL,
    freq TEXT, value REAL, source TEXT,
    PRIMARY KEY (ccy, indicator, period)
);
CREATE TABLE IF NOT EXISTS macro_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def db_path() -> str:
    return os.path.join(_DB_DIR, "macro.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(_DB_DIR, exist_ok=True)
    con = sqlite3.connect(db_path())
    con.executescript(_SCHEMA)
    return con


# ── Perioden ──────────────────────────────────────────────────────────────────

def norm_period(p: str) -> tuple[str, str]:
    """'2026-10' → ('2026-10-01','M'), '2026-Q2' → ('2026-04-01','Q'),
    '2026-10-01' (FRED) → ('2026-10-01','M')."""
    p = str(p).strip()
    if "-Q" in p:
        y, q = p.split("-Q")
        return f"{int(y):04d}-{(int(q) - 1) * 3 + 1:02d}-01", "Q"
    if len(p) == 7:
        return f"{p}-01", "M"
    return p[:10], "M"


# ── Abruf je Quelle (jede liefert DataFrame ccy/indicator/period/freq/value/source) ──

def _rows(ccy, ind, periods, values, source, freq=None) -> list[dict]:
    out = []
    for p, v in zip(periods, values):
        if v is None or pd.isna(v):
            continue
        d, f = norm_period(p)
        out.append({"ccy": ccy, "indicator": ind, "period": d, "freq": freq or f,
                    "value": float(v), "source": source})
    return out


def _bis(flow: str, suffix: str, ind: str) -> list[dict]:
    keys = "+".join(_BIS)
    url = (f"https://stats.bis.org/api/v2/data/dataflow/BIS/{flow}/1.0/M.{keys}{suffix}"
           f"?startPeriod={START}&format=csv")
    r = requests.get(url, timeout=_TIMEOUT)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), usecols=["REF_AREA", "TIME_PERIOD", "OBS_VALUE"])
    rows = []
    for area, g in df.groupby("REF_AREA"):
        if area in _BIS:
            rows += _rows(_BIS[area], ind, g["TIME_PERIOD"], pd.to_numeric(g["OBS_VALUE"], errors="coerce"), "BIS")
    return rows


def fetch_rate() -> list[dict]:
    return _bis("WS_CBPOL", "", "rate")


def fetch_cpi() -> list[dict]:
    return _bis("WS_LONG_CPI", ".771", "cpi")


def _oecd(url: str, ind: str, areas: dict) -> list[dict]:
    r = requests.get(url, timeout=_TIMEOUT, headers=_CSV)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), usecols=["REF_AREA", "TIME_PERIOD", "OBS_VALUE"])
    rows = []
    for area, g in df.groupby("REF_AREA"):
        if area in areas:
            rows += _rows(areas[area], ind, g["TIME_PERIOD"], pd.to_numeric(g["OBS_VALUE"], errors="coerce"), "OECD")
    return rows


def _eurostat(dataset: str, params: dict, ind: str) -> list[dict]:
    for geo in _EA_CODES:
        url = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"
        r = requests.get(url, params={**params, "geo": geo}, timeout=_TIMEOUT)
        r.raise_for_status()
        j = r.json()
        times = j.get("dimension", {}).get("time", {}).get("category", {}).get("index", {})
        vals = j.get("value", {})
        if not vals:
            continue
        pos = {v: k for k, v in times.items()}
        periods = [pos[int(i)] for i in vals]
        return _rows("EUR", ind, periods, list(vals.values()), f"Eurostat {geo}")
    return []


def fetch_gdp() -> list[dict]:
    areas = {k: v for k, v in _OECD.items() if k != "G4E"}
    url = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_OECD,1.1/"
           f"Q..{'+'.join(areas)}.S1..B1GQ......G1.?startPeriod={START[:4]}-Q1")
    rows = _oecd(url, "gdp", areas)
    rows += _eurostat("namq_10_gdp", {"unit": "CLV_PCH_PRE", "s_adj": "SCA", "na_item": "B1GQ",
                                      "sinceTimePeriod": f"{START[:4]}-Q1"}, "gdp")
    return rows


def fetch_cli() -> list[dict]:
    url = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI,4.1/"
           f"{'+'.join(_OECD)}.M.LI...AA...H?startPeriod={START}")
    return _oecd(url, "cli", _OECD)


def fetch_unemp() -> list[dict]:
    rows = []
    for ccy, sid in _FRED_UNEMP.items():
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": sid},
                         timeout=_TIMEOUT)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["date", "value"]
        df = df[df["date"] >= f"{START}-01"]
        freq = "Q" if sid.endswith("Q156S") else "M"
        rows += _rows(ccy, "unemp", df["date"], pd.to_numeric(df["value"], errors="coerce"),
                      f"FRED {sid}", freq)
    rows += _eurostat("une_rt_m", {"s_adj": "SA", "age": "TOTAL", "sex": "T", "unit": "PC_ACT",
                                   "sinceTimePeriod": START}, "unemp")
    return rows


FETCHERS = {"rate": fetch_rate, "cpi": fetch_cpi, "unemp": fetch_unemp,
            "gdp": fetch_gdp, "cli": fetch_cli}


def fetch(indicators: list[str] | None = None) -> dict[str, str]:
    """Lädt alle (oder die genannten) Indikatoren. Rückgabe {indikator: 'ok n' | Fehler}."""
    status = {}
    with closing(_connect()) as con:
        _set_meta(con, "last_attempt", dt.datetime.now().isoformat(timespec="seconds"))
        con.commit()
        for ind in indicators or INDICATORS:
            try:
                rows = FETCHERS[ind]()
                if not rows:
                    raise ValueError("keine Daten")
                con.execute("DELETE FROM macro_obs WHERE indicator = ?", (ind,))
                con.executemany(
                    "INSERT OR REPLACE INTO macro_obs VALUES (:ccy, :indicator, :period, :freq, :value, :source)",
                    rows)
                status[ind] = f"ok {len(rows)}"
                _set_meta(con, f"err_{ind}", "")
            except Exception as e:   # Netz/HTTP/Format — andere Quellen trotzdem laden
                logger.warning("Makro-Abruf %s fehlgeschlagen: %s", ind, e)
                status[ind] = f"Fehler: {e}"
                _set_meta(con, f"err_{ind}", str(e)[:300])
            con.commit()
        if any(v.startswith("ok") for v in status.values()):
            _set_meta(con, "last_success", dt.datetime.now().isoformat(timespec="seconds"))
        con.commit()
    return status


def _set_meta(con, key, value) -> None:
    con.execute("INSERT OR REPLACE INTO macro_meta (key, value) VALUES (?, ?)", (key, value))


def get_meta(key: str) -> str | None:
    if not os.path.exists(db_path()):
        return None
    with closing(_connect()) as con:
        row = con.execute("SELECT value FROM macro_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _age(key: str) -> dt.timedelta | None:
    v = get_meta(key)
    try:
        return dt.datetime.now() - dt.datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


def is_stale() -> bool:
    ok = _age("last_success")
    if ok is not None and ok < dt.timedelta(hours=REFRESH_HOURS):
        return False
    tried = _age("last_attempt")
    return tried is None or tried > dt.timedelta(hours=RETRY_HOURS)


def ensure_fresh() -> tuple[bool, dict | None]:
    if not is_stale():
        return False, None
    try:
        status = fetch()
    except Exception as e:
        logger.warning("Makro-Abruf fehlgeschlagen: %s", e)
        return False, {"all": str(e)}
    errors = {k: v for k, v in status.items() if not v.startswith("ok")}
    return True, errors or None


def load_obs() -> pd.DataFrame:
    if not os.path.exists(db_path()):
        return pd.DataFrame(columns=["ccy", "indicator", "period", "freq", "value", "source"])
    with closing(_connect()) as con:
        df = pd.read_sql_query("SELECT * FROM macro_obs ORDER BY ccy, indicator, period", con)
    df["period"] = pd.to_datetime(df["period"])
    return df


# ── Scoring (reine Funktionen, testbar ohne Netz) ─────────────────────────────

def _lag_value(s: pd.DataFrame, months: int) -> float | None:
    """Wert ``months`` Monate vor dem letzten (Quartalsreihen: eine Periode zurück)."""
    if len(s) < 2:
        return None
    if s["freq"].iloc[-1] == "Q":
        return float(s["value"].iloc[-2])
    target = s["period"].iloc[-1] - pd.DateOffset(months=months)
    prev = s[s["period"] <= target]
    return float(prev["value"].iloc[-1]) if len(prev) else None


def _sign(x: float | None, thr: float) -> int:
    if x is None or pd.isna(x):
        return 0
    return 1 if x > thr else -1 if x < -thr else 0


def latest(obs: pd.DataFrame) -> pd.DataFrame:
    """Je Währung × Indikator: letzter Wert, Periode, Vergleichswert, Veränderung."""
    lags = {"rate": 6, "cpi": 3, "unemp": 3, "gdp": 3, "cli": 1}
    rows = []
    for (ccy, ind), s in obs.sort_values("period").groupby(["ccy", "indicator"]):
        s = s.reset_index(drop=True)
        prev = _lag_value(s, lags[ind])
        last = float(s["value"].iloc[-1])
        rows.append({"ccy": ccy, "indicator": ind, "period": s["period"].iloc[-1],
                     "freq": s["freq"].iloc[-1], "value": last, "prev": prev,
                     "change": None if prev is None else last - prev,
                     "source": s["source"].iloc[-1]})
    return pd.DataFrame(rows)


def score_table(lat: pd.DataFrame) -> pd.DataFrame:
    """Punkte je Komponente und Summe je Währung (Zeilen = Währungen)."""
    if lat.empty:
        return pd.DataFrame()
    v = lat.pivot(index="ccy", columns="indicator", values="value")
    ch = lat.pivot(index="ccy", columns="indicator", values="change")
    out = pd.DataFrame(index=[c for c in CURRENCIES if c in v.index])
    get = lambda df, ind, c: (df.at[c, ind] if ind in df.columns and c in df.index else None)

    med = v["rate"].median() if "rate" in v else None
    pts = {}
    for c in out.index:
        rate, d_rate = get(v, "rate", c), get(ch, "rate", c)
        p_rate = (0 if rate is None or pd.isna(rate) or med is None
                  else 1 if rate > med else -1 if rate < med else 0) + _sign(d_rate, 0.01)
        p_cpi = _sign(get(ch, "cpi", c), 0.2)
        p_unemp = -_sign(get(ch, "unemp", c), 0.1)
        gdp = get(v, "gdp", c)
        p_gdp = 0 if gdp is None or pd.isna(gdp) else 1 if gdp >= 0.5 else -1 if gdp < 0 else 0
        cli, d_cli = get(v, "cli", c), get(ch, "cli", c)
        p_cli = 0
        if cli is not None and not pd.isna(cli) and d_cli is not None and not pd.isna(d_cli):
            p_cli = 1 if cli > 100 and d_cli > 0 else -1 if cli < 100 and d_cli < 0 else 0
        pts[c] = {"p_rate": p_rate, "p_cpi": p_cpi, "p_unemp": p_unemp, "p_gdp": p_gdp, "p_cli": p_cli}
    out = pd.DataFrame.from_dict(pts, orient="index")
    out["score"] = out.sum(axis=1)
    out["bias"] = out["score"].apply(bias_label)
    return out


def bias_label(score: float) -> str:
    if score >= 4:
        return "strong_bull"
    if score >= 2:
        return "bull"
    if score <= -4:
        return "strong_bear"
    if score <= -2:
        return "bear"
    return "neutral"


def scorecard() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(Punkte je Währung, letzte Werte je Währung × Indikator)."""
    lat = latest(load_obs())
    return score_table(lat), lat


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for k, v in fetch().items():
        print(f"{k:6s} {v}")
    sc, _ = scorecard()
    print(sc.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
