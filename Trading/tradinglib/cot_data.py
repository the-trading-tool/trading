"""COT Edge Scorecard — Daten und Scoring aus dem CFTC Commitments of Traders Report.

Quelle: CFTC Public Reporting API (Socrata), Datensatz "Legacy – Futures Only"
(6dca-aqww). Kostenlos, ohne API-Key. Stichtag ist jeweils Dienstag, die CFTC
veröffentlicht am Freitag darauf (ca. 15:30 ET). Gespeichert wird lokal in
``database/cot.db``; die Seite (:mod:`tradinglib.cot_page`) liest nur von dort.

Betrachtet werden die Non-Commercials (große Spekulanten) — dieselbe Gruppe, die
auch kommerzielle Tools wie der EdgeFinder als "Long/Short Contracts" zeigen.

Scoring (eigene, bewusst einfache und nachvollziehbare Methodik, −3 … +3):
  * Positionierung: Long-Anteil der Non-Commercials (long / (long + short)),
    gemessen an der **eigenen Historie** des Marktes: Perzentil-Rang des aktuellen
    Long-Anteils in den letzten 156 Wochen (3 Jahre, mind. 52)
        ≥ 80 → +2   ≥ 60 → +1   ≤ 20 → −2   ≤ 40 → −1   sonst 0
    Bis 2026-11-12 waren es absolute Schwellen (≥ 60 % long → +2). Das bewertete
    strukturell long gehaltene Märkte fast immer bullish (Silber: Median 68,9 %
    long, 76 % aller Wochen ≥ 60 %) — auch während die Spekulanten abbauten.
  * Dynamik: Veränderung des Long-Anteils zur Vorwoche
        > +1 Pp → +1   < −1 Pp → −1   sonst 0
Zusätzlich (nicht im Score): COT-Index = Lage der Netto-Position in ihrer
52-Wochen-Spanne (0–100, Larry Williams). ≥ 90 bzw. ≤ 10 gilt als "überfüllt" —
ein Warnhinweis, dass der Trend einseitig positioniert ist.

Forex-Paare: Score(Basis) − Score(Quote), USD über den ICE-Dollar-Index.

Der Score ist ein Bias-Filter, kein Einstiegssignal.

CLI (z. B. für den Scheduler, freitags abends):
    python -m tradinglib.cot_data            # inkrementell nachladen
    python -m tradinglib.cot_data /full      # komplette Historie neu laden
"""
from __future__ import annotations

import datetime as dt
import itertools
import logging
import os
import sqlite3
import sys
from contextlib import closing

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_DB_DIR = os.environ.get("TradingDB") or "database"
API_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
HISTORY_START = dt.date(2018, 1, 1)
RELEASE_LAG_DAYS = 3          # Dienstag-Stichtag → Freitag-Veröffentlichung
INDEX_WEEKS = 52              # Fenster für den COT-Index
RANK_WEEKS = 156              # Fenster für den Perzentil-Rang der Positionierung
RANK_MIN_WEEKS = 52

# key, CFTC-Code, Anzeigename (Fallback; die Seite übersetzt über cot.mkt_<key>),
# Gruppe (Locale cot.group_<gruppe>), Währung (nur FX-Hauptwährungen)
MARKETS: list[tuple[str, str, str, str, str | None]] = [
    ("USD", "098662", "US-Dollar-Index", "fx", "USD"),
    ("EUR", "099741", "Euro", "fx", "EUR"),
    ("GBP", "096742", "Britisches Pfund", "fx", "GBP"),
    ("JPY", "097741", "Japanischer Yen", "fx", "JPY"),
    ("CHF", "092741", "Schweizer Franken", "fx", "CHF"),
    ("CAD", "090741", "Kanadischer Dollar", "fx", "CAD"),
    ("AUD", "232741", "Australischer Dollar", "fx", "AUD"),
    ("NZD", "112741", "Neuseeland-Dollar", "fx", "NZD"),
    ("MXN", "095741", "Mexikanischer Peso", "fx", None),
    ("GOLD", "088691", "Gold", "commodities", None),
    ("SILVER", "084691", "Silber", "commodities", None),
    ("COPPER", "085692", "Kupfer", "commodities", None),
    ("WTI", "067651", "Rohöl WTI", "commodities", None),
    ("NATGAS", "023651", "Erdgas", "commodities", None),
    ("SPX", "13874A", "S&P 500 (E-mini)", "indices", None),
    ("NDX", "209742", "Nasdaq 100 (E-mini)", "indices", None),
    ("DJI", "124603", "Dow Jones (Mini)", "indices", None),
    ("RUT", "239742", "Russell 2000 (E-mini)", "indices", None),
    ("UST10Y", "043602", "US-Treasury 10J", "rates", None),
    ("BTC", "133741", "Bitcoin (CME)", "crypto", None),
]
_BY_CODE = {m[1]: m for m in MARKETS}
_BY_KEY = {m[0]: m for m in MARKETS}
FX_CCYS = [m[4] for m in MARKETS if m[4]]

# API-Feld → DB-Spalte
_FIELDS = {
    "open_interest_all": "oi",
    "noncomm_positions_long_all": "nc_long",
    "noncomm_positions_short_all": "nc_short",
    "noncomm_postions_spread_all": "nc_spread",   # sic — Tippfehler im CFTC-Schema
    "comm_positions_long_all": "comm_long",
    "comm_positions_short_all": "comm_short",
    "nonrept_positions_long_all": "nonrep_long",
    "nonrept_positions_short_all": "nonrep_short",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cot_weekly (
    code         TEXT NOT NULL,
    report_date  TEXT NOT NULL,
    market_name  TEXT,
    oi           INTEGER,
    nc_long      INTEGER,
    nc_short     INTEGER,
    nc_spread    INTEGER,
    comm_long    INTEGER,
    comm_short   INTEGER,
    nonrep_long  INTEGER,
    nonrep_short INTEGER,
    PRIMARY KEY (code, report_date)
);
CREATE TABLE IF NOT EXISTS cot_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def db_path() -> str:
    return os.path.join(_DB_DIR, "cot.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(_DB_DIR, exist_ok=True)
    con = sqlite3.connect(db_path())
    con.executescript(_SCHEMA)
    return con


# ── Abruf ─────────────────────────────────────────────────────────────────────

def _download(since: dt.date, codes: list[str], timeout: int = 60) -> list[dict]:
    code_list = ",".join(f"'{c}'" for c in codes)   # Codes stammen aus MARKETS, nicht vom Benutzer
    params = {
        "$select": ",".join(["cftc_contract_market_code", "report_date_as_yyyy_mm_dd",
                             "market_and_exchange_names", *_FIELDS]),
        "$where": (f"cftc_contract_market_code in ({code_list}) AND "
                   f"report_date_as_yyyy_mm_dd >= '{since.isoformat()}T00:00:00.000'"),
        "$order": "report_date_as_yyyy_mm_dd",
        "$limit": 50000,
    }
    resp = requests.get(API_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _to_int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def fetch(full: bool = False) -> int:
    """Lädt neue Wochen von der CFTC und speichert sie. Gibt die Zahl der Zeilen zurück.

    Inkrementell ab der letzten gespeicherten Woche (minus 2 Wochen, damit
    nachträgliche CFTC-Korrekturen überschrieben werden); ``full`` lädt alles ab
    ``HISTORY_START`` neu.
    """
    with closing(_connect()) as con:
        since = HISTORY_START
        if not full:
            row = con.execute("SELECT MAX(report_date) FROM cot_weekly").fetchone()
            if row and row[0]:
                since = max(HISTORY_START, dt.date.fromisoformat(row[0]) - dt.timedelta(days=14))
        _set_meta(con, "last_attempt", dt.datetime.now().isoformat(timespec="seconds"))
        con.commit()

        records = _download(since, [m[1] for m in MARKETS])
        rows = []
        for r in records:
            code = r.get("cftc_contract_market_code")
            date = (r.get("report_date_as_yyyy_mm_dd") or "")[:10]
            if code not in _BY_CODE or not date:
                continue
            rows.append((code, date, r.get("market_and_exchange_names"),
                         *(_to_int(r.get(f)) for f in _FIELDS)))
        con.executemany(
            f"INSERT OR REPLACE INTO cot_weekly (code, report_date, market_name, "
            f"{', '.join(_FIELDS.values())}) VALUES ({', '.join('?' * (3 + len(_FIELDS)))})",
            rows)
        _set_meta(con, "last_success", dt.datetime.now().isoformat(timespec="seconds"))
        con.commit()
    logger.info("COT: %d Zeilen ab %s gespeichert", len(rows), since)
    return len(rows)


def _set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT OR REPLACE INTO cot_meta (key, value) VALUES (?, ?)", (key, value))


def get_meta(key: str) -> str | None:
    if not os.path.exists(db_path()):
        return None
    with closing(_connect()) as con:
        row = con.execute("SELECT value FROM cot_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def latest_report_date() -> dt.date | None:
    if not os.path.exists(db_path()):
        return None
    with closing(_connect()) as con:
        row = con.execute("SELECT MAX(report_date) FROM cot_weekly").fetchone()
    return dt.date.fromisoformat(row[0]) if row and row[0] else None


def is_stale(today: dt.date | None = None, retry_hours: float = 6) -> bool:
    """True, wenn ein neuer Report veröffentlicht sein müsste und der letzte
    Abrufversuch länger als ``retry_hours`` zurückliegt (verhindert, dass jeder
    Seitenaufruf die API anfragt, solange die CFTC noch nicht geliefert hat)."""
    today = today or dt.date.today()
    latest = latest_report_date()
    if latest is None:
        return True
    next_release = latest + dt.timedelta(days=7 + RELEASE_LAG_DAYS)
    if today < next_release:
        return False
    last = get_meta("last_attempt")
    if not last:
        return True
    try:
        age = dt.datetime.now() - dt.datetime.fromisoformat(last)
    except ValueError:
        return True
    return age > dt.timedelta(hours=retry_hours)


def ensure_fresh() -> tuple[bool, str | None]:
    """Lädt nach, falls nötig. Gibt (aktualisiert, Fehlermeldung) zurück; ein
    Netzwerkfehler ist nicht fatal — die Seite zeigt dann den letzten Stand."""
    if not is_stale():
        return False, None
    try:
        fetch()
        return True, None
    except Exception as e:  # Netzwerk/HTTP/JSON
        logger.warning("COT-Abruf fehlgeschlagen: %s", e)
        return False, str(e)


# ── Kennzahlen & Scoring ──────────────────────────────────────────────────────

def positioning_score(rank: float) -> int:
    """Punkte aus dem Perzentil-Rang (0–100) des Long-Anteils in der eigenen Historie."""
    if rank is None or pd.isna(rank):
        return 0
    if rank >= 80:
        return 2
    if rank >= 60:
        return 1
    if rank <= 20:
        return -2
    if rank <= 40:
        return -1
    return 0


def rolling_rank(s: pd.Series, window: int = RANK_WEEKS, min_periods: int = RANK_MIN_WEEKS) -> pd.Series:
    """Anteil der Werte im nachlaufenden Fenster (inkl. aktuellem), die ≤ dem aktuellen
    Wert sind, in Prozent. Kausal: nutzt nur Vergangenheit und Gegenwart."""
    return s.rolling(window, min_periods=min_periods).apply(
        lambda w: (w <= w[-1]).mean() * 100, raw=True)


def momentum_score(delta_long_pct: float) -> int:
    if pd.isna(delta_long_pct):
        return 0
    if delta_long_pct > 1:
        return 1
    if delta_long_pct < -1:
        return -1
    return 0


def bias_label(score: float) -> str:
    """Englisches Kürzel; die Seite übersetzt über ``cot.bias_<label>``."""
    if score >= 3:
        return "strong_bull"
    if score >= 1:
        return "bull"
    if score <= -3:
        return "strong_bear"
    if score <= -1:
        return "bear"
    return "neutral"


def pair_bias_label(diff: float, strong: float = 4, bias: float = 2) -> str:
    """Bias eines Paares aus der Score-Differenz (COT: Spanne −6 … +6)."""
    if diff >= strong:
        return "strong_bull"
    if diff >= bias:
        return "bull"
    if diff <= -strong:
        return "strong_bear"
    if diff <= -bias:
        return "bear"
    return "neutral"


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Ergänzt die Rohzeilen EINES Marktes (aufsteigend nach Datum) um alle
    abgeleiteten Spalten. Reine Funktion — testbar ohne DB/Netz."""
    df = df.sort_values("report_date").reset_index(drop=True).copy()
    tot = (df["nc_long"] + df["nc_short"]).replace(0, float("nan"))
    df["long_pct"] = (df["nc_long"] / tot * 100).astype(float)
    df["short_pct"] = 100 - df["long_pct"]
    df["net"] = df["nc_long"] - df["nc_short"]
    df["d_long"] = df["nc_long"].diff()
    df["d_short"] = df["nc_short"].diff()
    df["d_net"] = df["net"].diff()
    df["d_long_pct"] = df["long_pct"].diff()
    df["comm_net"] = df["comm_long"] - df["comm_short"]
    roll = df["net"].rolling(INDEX_WEEKS, min_periods=min(INDEX_WEEKS, 26))
    lo, hi = roll.min(), roll.max()
    rng = (hi - lo).replace(0, float("nan"))
    df["cot_index"] = ((df["net"] - lo) / rng * 100).astype(float)
    df["long_rank"] = rolling_rank(df["long_pct"])
    df["score_pos"] = df["long_rank"].apply(positioning_score)
    df["score_mom"] = df["d_long_pct"].apply(momentum_score)
    df["score"] = df["score_pos"] + df["score_mom"]
    df["release_date"] = pd.to_datetime(df["report_date"]) + pd.Timedelta(days=RELEASE_LAG_DAYS)
    return df


def load_history(key: str) -> pd.DataFrame:
    """Gesamte Historie eines Marktes inkl. Kennzahlen (leer, wenn keine Daten)."""
    m = _BY_KEY[key]
    if not os.path.exists(db_path()):
        return pd.DataFrame()
    with closing(_connect()) as con:
        df = pd.read_sql_query(
            "SELECT * FROM cot_weekly WHERE code = ? ORDER BY report_date", con, params=(m[1],))
    if df.empty:
        return df
    return enrich(df)


def scorecard() -> pd.DataFrame:
    """Letzte Woche je Markt mit Score, sortiert nach Gruppe und Score."""
    rows = []
    for key, code, label, group, _ccy in MARKETS:
        h = load_history(key)
        if h.empty:
            continue
        last = h.iloc[-1]
        rows.append({
            "key": key, "market": label, "group": group,
            "report_date": last["report_date"], "release_date": last["release_date"].date(),
            "nc_long": int(last["nc_long"]), "nc_short": int(last["nc_short"]),
            "net": int(last["net"]),
            "d_net": None if pd.isna(last["d_net"]) else int(last["d_net"]),
            "long_pct": float(last["long_pct"]),
            "long_rank": None if pd.isna(last["long_rank"]) else float(last["long_rank"]),
            "d_long_pct": None if pd.isna(last["d_long_pct"]) else float(last["d_long_pct"]),
            "cot_index": None if pd.isna(last["cot_index"]) else float(last["cot_index"]),
            "score_pos": int(last["score_pos"]), "score_mom": int(last["score_mom"]),
            "score": int(last["score"]), "bias": bias_label(last["score"]),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    order = {g: i for i, g in enumerate(dict.fromkeys(m[3] for m in MARKETS))}
    df["_g"] = df["group"].map(order)
    return df.sort_values(["_g", "score"], ascending=[True, False]).drop(columns="_g").reset_index(drop=True)


def pair_bias(card: pd.DataFrame, strong: float = 4, bias: float = 2) -> pd.DataFrame:
    """Alle Paare der FX-Hauptwährungen in Marktkonvention (EURUSD, nicht USDEUR).

    ``card`` braucht die Spalten ``key`` (Währung) und ``score``; ``strong``/``bias``
    sind die Schwellen der Score-Differenz (auch von der Makro-Scorecard genutzt)."""
    if card.empty:
        return pd.DataFrame()
    s = dict(zip(card["key"], card["score"]))
    # Marktübliche Reihenfolge der Basiswährung
    prio = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]
    rows = []
    for a, b in itertools.combinations([c for c in prio if c in s], 2):
        base, quote = (a, b) if prio.index(a) < prio.index(b) else (b, a)
        diff = s[base] - s[quote]
        rows.append({"pair": f"{base}{quote}", "base_score": s[base],
                     "quote_score": s[quote], "score": diff, "bias": pair_bias_label(diff, strong, bias)})
    df = pd.DataFrame(rows)
    df["_abs"] = df["score"].abs()
    return df.sort_values(["_abs", "pair"], ascending=[False, True]).drop(columns="_abs").reset_index(drop=True)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    full = any(a.lower() in ("/full", "--full") for a in argv)
    n = fetch(full=full)
    print(f"COT: {n} Zeilen gespeichert, letzter Stichtag {latest_report_date()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
