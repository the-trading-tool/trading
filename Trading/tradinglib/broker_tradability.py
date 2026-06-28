"""
broker_tradability.py — Broker-agnostische Handelbarkeitsprüfung.

Beantwortet pro Ticker die Frage: *„Ist dieses Papier bei meinem Broker
handelbar?"* — damit lassen sich generierte Signale auf die tatsächlich
orderbaren Werte einschränken.

Broker werden über einen Plugin-Mechanismus angebunden:
  - scalable : Scalable Capital (gettex + Xetra + LS Exchange) — per-ISIN-Abfrage
               über den lokalen Proxy ``unofficial-scalable-capital-api``
               (``/securities/{isin}/buyable``).
  - alpaca   : Alpaca — ``/v2/assets``-Liste (US-Aktien), Symbol-basiert.
  - ibkr     : Interactive Brokers — permissiv (SMART-Routing deckt faktisch
               alle gelisteten US/EU-Titel), optionale Ausschlussliste.
  - none     : kein Filter — alles handelbar (Default).

Der aktive Broker steht in ``config.db`` unter ``'<user>:broker'``.

Ergebnisse werden in ``asset_info.db`` (Tabelle ``broker_tradability_cache``)
gecached und erst nach ``REFRESH_DAYS`` erneut online geprüft — also eine
Abfrage pro ISIN, danach offline. ISINs kommen aus ``yf_tickers.db`` (Spalte
``stocks.ISIN``); fehlende werden bei Bedarf per yfinance/FMP nachgeladen und
zurückgeschrieben.

CLI:
    python -m tradinglib.broker_tradability /index:^RUT
    python -m tradinglib.broker_tradability /tickers:PLUG,OKLO,AAPL /broker:scalable
    python -m tradinglib.broker_tradability /file:rut_import_89.txt /out:rut_scalable.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional

from tradinglib.tools import Tools, open_db

logger = logging.getLogger(__name__)

REFRESH_DAYS = 7          # nach wie vielen Tagen ein gecachtes Ergebnis neu geprüft wird
_DB_PATH = "database"


# ─────────────────────────────────────────────────────────────────────────────
# Datenmodell
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Tradability:
    ticker: str
    isin: Optional[str]
    broker: str
    tradable: Optional[bool]      # True / False / None (= unbekannt, z.B. Broker offline)
    venues: Optional[str]         # z.B. "gettex,xetra" — sofern der Broker es liefert
    source: str                   # 'cache' | 'scalable_proxy' | 'alpaca_assets' | ...

    def as_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# ISIN-Auflösung (yf_tickers.db → optional yfinance/FMP-Nachladen)
# ─────────────────────────────────────────────────────────────────────────────

class IsinResolver(Tools):
    """Liefert ISINs aus yf_tickers.db; lädt fehlende optional nach und schreibt sie zurück."""

    def __init__(self, db_path: str = _DB_PATH):
        self._db = self.get_path(path=db_path, file_name="yf_tickers.db")

    def get(self, ticker: str) -> Optional[str]:
        """ISIN aus stocks.ISIN (None bei Index/FX oder fehlend)."""
        try:
            with open_db(self._db, readonly=True) as conn:
                row = conn.execute(
                    "SELECT ISIN FROM stocks WHERE Ticker = ?", (ticker,)
                ).fetchone()
            if row and row[0]:
                return str(row[0]).strip().upper()
        except Exception as e:
            logger.debug("ISIN-Lookup für %s fehlgeschlagen: %s", ticker, e)
        return None

    def resolve(self, ticker: str, allow_network: bool = True) -> Optional[str]:
        """ISIN holen; bei Fehlen optional per yfinance/FMP nachladen und persistieren."""
        isin = self.get(ticker)
        if isin or not allow_network:
            return isin
        if ticker.startswith("^") or ticker.endswith("=X"):
            return None
        try:
            from backfill_isin import fetch_isin
            isin = fetch_isin(ticker)
        except Exception as e:
            logger.debug("ISIN-Nachladen für %s fehlgeschlagen: %s", ticker, e)
            isin = None
        if isin:
            self._persist(ticker, isin)
        return isin

    def _persist(self, ticker: str, isin: str):
        """ISIN in stocks zurückschreiben (Best effort)."""
        try:
            with open_db(self._db) as conn:
                conn.execute(
                    "UPDATE stocks SET ISIN = ? WHERE Ticker = ? AND (ISIN IS NULL OR ISIN = '')",
                    (isin, ticker),
                )
                conn.commit()
        except Exception as e:
            logger.debug("ISIN-Persist für %s fehlgeschlagen: %s", ticker, e)


# ─────────────────────────────────────────────────────────────────────────────
# Broker-Checker (Plugin-Basis + Implementierungen)
# ─────────────────────────────────────────────────────────────────────────────

class BrokerChecker:
    """Basisklasse. Unterklassen implementieren ``_check(ticker, isin)``."""

    broker_id = "base"
    needs_isin = False

    def _check(self, ticker: str, isin: Optional[str]) -> Tradability:
        raise NotImplementedError


class NoFilterChecker(BrokerChecker):
    """Kein Filter — alles handelbar (für Nutzer ohne Broker-Einschränkung)."""

    broker_id = "none"

    def _check(self, ticker, isin):
        return Tradability(ticker, isin, self.broker_id, True, None, "no_filter")


class ScalableChecker(BrokerChecker):
    """Scalable Capital via lokalem Proxy (unofficial-scalable-capital-api).

    Abgefragt wird ``GET {base}/securities/{isin}/buyable``. Ist der Proxy nicht
    erreichbar oder fehlt die ISIN, wird ``tradable=None`` (unbekannt) gemeldet —
    der Filter verliert dann keine Signale durch eine Infrastruktur-Störung.

    Konfiguration (config.db, je Nutzer):
      - ``scalable_proxy_url``    Default ``http://localhost:8080``
      - ``scalable_gateway_token`` optional → Header ``X-Gateway-Token``
    """

    broker_id = "scalable"
    needs_isin = True

    def __init__(self, base_url: str, token: Optional[str] = None):
        self._base = (base_url or "http://localhost:8080").rstrip("/")
        self._token = token or ""

    def _check(self, ticker, isin):
        if not isin:
            return Tradability(ticker, None, self.broker_id, None, None, "no_isin")
        try:
            import requests
        except Exception:
            return Tradability(ticker, isin, self.broker_id, None, None, "no_requests")

        headers = {"X-Gateway-Token": self._token} if self._token else {}
        try:
            resp = requests.get(
                f"{self._base}/securities/{isin}/buyable", headers=headers, timeout=10
            )
            if resp.status_code == 404:
                # Proxy kennt das Papier nicht → bei Scalable nicht handelbar
                return Tradability(ticker, isin, self.broker_id, False, None, "scalable_proxy")
            resp.raise_for_status()
            tradable, venues = _parse_scalable_payload(resp.json())
            return Tradability(ticker, isin, self.broker_id, tradable, venues, "scalable_proxy")
        except Exception as e:
            logger.debug("Scalable-Proxy für %s (%s) nicht erreichbar: %s", ticker, isin, e)
            return Tradability(ticker, isin, self.broker_id, None, None, "proxy_unreachable")


class AlpacaChecker(BrokerChecker):
    """Alpaca — Handelbarkeit über die ``/v2/assets``-Liste (US-Aktien).

    Lädt die aktiven, handelbaren Symbole einmal pro Lauf und prüft per
    Symbol-Mitgliedschaft. Ohne API-Key wird ``tradable=None`` gemeldet.

    Keys: KSP-Eintrag ``alpaca`` (user/password) oder Env
    ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY``.
    """

    broker_id = "alpaca"

    def __init__(self):
        self._symbols: Optional[set] = None

    def _load_symbols(self) -> Optional[set]:
        if self._symbols is not None:
            return self._symbols
        key_id, secret = _alpaca_creds()
        if not key_id or not secret:
            self._symbols = None
            return None
        try:
            import requests
            resp = requests.get(
                "https://paper-api.alpaca.markets/v2/assets",
                params={"status": "active", "asset_class": "us_equity"},
                headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret},
                timeout=20,
            )
            resp.raise_for_status()
            self._symbols = {
                a["symbol"].upper()
                for a in resp.json()
                if a.get("tradable")
            }
        except Exception as e:
            logger.debug("Alpaca-Assets-Abruf fehlgeschlagen: %s", e)
            self._symbols = None
        return self._symbols

    def _check(self, ticker, isin):
        symbols = self._load_symbols()
        if symbols is None:
            return Tradability(ticker, isin, self.broker_id, None, None, "alpaca_unavailable")
        sym = ticker.split(".")[0].upper()
        return Tradability(
            ticker, isin, self.broker_id, sym in symbols, "alpaca", "alpaca_assets"
        )


class IbkrChecker(BrokerChecker):
    """Interactive Brokers — permissive Heuristik.

    IBKR deckt über SMART-Routing faktisch alle an Hauptbörsen gelisteten
    US/EU-Titel ab. Ohne laufendes TWS/Gateway gibt es keine zuverlässige
    Online-Kontraktprüfung, daher: handelbar = True, sofern eine ISIN existiert
    oder es ein US-Symbol ist; per config.db ``ibkr_exclude`` (kommagetrennt)
    lassen sich einzelne Ticker ausschließen.
    """

    broker_id = "ibkr"

    def __init__(self, exclude: Optional[set] = None):
        self._exclude = exclude or set()

    def _check(self, ticker, isin):
        if ticker.upper() in self._exclude:
            return Tradability(ticker, isin, self.broker_id, False, None, "ibkr_exclude")
        is_us = "." not in ticker and "^" not in ticker
        tradable = bool(isin) or is_us
        return Tradability(ticker, isin, self.broker_id, tradable, "ibkr", "ibkr_heuristic")


# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen für Checker
# ─────────────────────────────────────────────────────────────────────────────

def _parse_scalable_payload(payload) -> tuple[Optional[bool], Optional[str]]:
    """Robustes Parsen der Proxy-Antwort → (tradable, venues).

    Die Antwortstruktur des inoffiziellen Proxys ist nicht garantiert; daher
    werden gängige Bool-Felder geprüft. Bei unklarer Struktur → (None, None).
    """
    if isinstance(payload, bool):
        return payload, None
    if isinstance(payload, dict):
        for key in ("buyable", "tradable", "isTradable", "isBuyable"):
            if isinstance(payload.get(key), bool):
                venues = payload.get("venues") or payload.get("exchanges")
                if isinstance(venues, list):
                    venues = ",".join(str(v) for v in venues)
                return payload[key], venues if isinstance(venues, str) else None
    return None, None


def _alpaca_creds() -> tuple[str, str]:
    """Alpaca-Zugangsdaten aus KSP (Eintrag 'alpaca') oder Env."""
    try:
        from tradinglib.ksplib import Ksp
        creds = Ksp(storage_path=_DB_PATH, secrets_path=_DB_PATH).get_ksp("alpaca")
        if creds:
            key_id = creds.get("user") or creds.get("username") or ""
            secret = creds.get("password") or ""
            if key_id and secret:
                return key_id, secret
    except Exception as e:
        logger.debug("KSP-Lookup für Alpaca fehlgeschlagen: %s", e)
    return (os.environ.get("APCA_API_KEY_ID", ""),
            os.environ.get("APCA_API_SECRET_KEY", ""))


# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration / Factory
# ─────────────────────────────────────────────────────────────────────────────

def _cfg(key: str, default=None, username: str = "admin"):
    """Einzelnen Config-Wert lesen (ohne Streamlit-Abhängigkeit)."""
    try:
        from tradinglib import system_config as sysconf
        return sysconf.SystemConfig(username=username, region=None,
                                    bare_mode=True).get_value(key, default)
    except Exception as e:
        logger.debug("Config-Lookup '%s' fehlgeschlagen: %s", key, e)
        return default


def get_checker(broker_id: Optional[str] = None, username: str = "admin") -> BrokerChecker:
    """Checker für den (konfigurierten) Broker bauen."""
    if broker_id is None:
        broker_id = (_cfg("broker", "none", username) or "none")
    broker_id = str(broker_id).strip().lower()

    if broker_id == "scalable":
        return ScalableChecker(
            base_url=_cfg("scalable_proxy_url", "http://localhost:8080", username),
            token=_cfg("scalable_gateway_token", "", username),
        )
    if broker_id == "alpaca":
        return AlpacaChecker()
    if broker_id == "ibkr":
        raw = _cfg("ibkr_exclude", "", username) or ""
        exclude = {t.strip().upper() for t in raw.split(",") if t.strip()}
        return IbkrChecker(exclude=exclude)
    return NoFilterChecker()


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────

class _Cache(Tools):
    """Persistenter Cache in asset_info.db (broker_tradability_cache)."""

    def __init__(self, db_path: str = _DB_PATH):
        self._db = self.get_path(path=db_path, file_name="asset_info.db")
        self._ensure()

    def _ensure(self):
        try:
            with open_db(self._db) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS broker_tradability_cache (
                        broker     TEXT,
                        ticker     TEXT,
                        isin       TEXT,
                        tradable   INTEGER,   -- 1 / 0 / NULL
                        venues     TEXT,
                        source     TEXT,
                        checked_at TEXT,
                        PRIMARY KEY (broker, ticker)
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.debug("Cache-Setup übersprungen: %s", e)

    def get(self, broker: str, ticker: str) -> Optional[Tradability]:
        try:
            with open_db(self._db, readonly=True) as conn:
                row = conn.execute(
                    "SELECT isin, tradable, venues, checked_at "
                    "FROM broker_tradability_cache WHERE broker=? AND ticker=?",
                    (broker, ticker),
                ).fetchone()
            if not row:
                return None
            checked = datetime.fromisoformat(row[3]) if row[3] else datetime.min
            if datetime.now() - checked > timedelta(days=REFRESH_DAYS):
                return None
            tradable = None if row[1] is None else bool(row[1])
            return Tradability(ticker, row[0], broker, tradable, row[2], "cache")
        except Exception as e:
            logger.debug("Cache-Get %s/%s fehlgeschlagen: %s", broker, ticker, e)
            return None

    def put(self, r: Tradability):
        try:
            with open_db(self._db) as conn:
                conn.execute("""
                    INSERT INTO broker_tradability_cache
                        (broker, ticker, isin, tradable, venues, source, checked_at)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(broker, ticker) DO UPDATE SET
                        isin=excluded.isin, tradable=excluded.tradable,
                        venues=excluded.venues, source=excluded.source,
                        checked_at=excluded.checked_at
                """, (r.broker, r.ticker, r.isin,
                      None if r.tradable is None else int(r.tradable),
                      r.venues, r.source, datetime.now().isoformat()))
                conn.commit()
        except Exception as e:
            logger.debug("Cache-Put %s/%s fehlgeschlagen: %s", r.broker, r.ticker, e)


# ─────────────────────────────────────────────────────────────────────────────
# Öffentliche API
# ─────────────────────────────────────────────────────────────────────────────

def check_tradable(tickers, broker_id: Optional[str] = None, *,
                   username: str = "admin", allow_network: bool = True,
                   use_cache: bool = True) -> dict[str, Tradability]:
    """Handelbarkeit je Ticker bestimmen → {ticker: Tradability}.

    Reihenfolge je Ticker: Cache (frisch) → ISIN auflösen → Broker abfragen →
    Ergebnis cachen. Netzwerkfehler ergeben ``tradable=None`` (unbekannt),
    brechen den Lauf aber nicht ab.
    """
    if isinstance(tickers, str):
        tickers = [tickers]
    checker = get_checker(broker_id, username)
    resolver = IsinResolver()
    cache = _Cache() if use_cache else None

    out: dict[str, Tradability] = {}
    for ticker in tickers:
        ticker = ticker.strip().upper()
        if not ticker:
            continue
        if cache:
            hit = cache.get(checker.broker_id, ticker)
            if hit is not None:
                out[ticker] = hit
                continue
        # ISIN nur dort online nachladen, wo der Broker sie wirklich braucht
        # (Scalable). Für none/ibkr/alpaca genügt die lokale ISIN -> kein Netz-Sturm.
        isin = resolver.resolve(ticker,
                                allow_network=allow_network and checker.needs_isin)
        result = checker._check(ticker, isin)
        if cache:
            cache.put(result)
        out[ticker] = result
    return out


def filter_tradable(tickers, broker_id: Optional[str] = None, *,
                    username: str = "admin", allow_network: bool = True,
                    drop_unknown: bool = False) -> dict[str, list[str]]:
    """Tickerliste in handelbar / nicht-handelbar / unbekannt aufteilen.

    drop_unknown=False (Default): unbekannte (Broker offline o.ä.) kommen zu
    ``tradable`` — kein Signalverlust bei Infrastrukturproblemen.
    drop_unknown=True (strikt): nur eindeutig handelbare passieren.

    Returns: {'tradable': [...], 'not_tradable': [...], 'unknown': [...]}
    """
    results = check_tradable(tickers, broker_id, username=username,
                             allow_network=allow_network)
    tradable, not_tradable, unknown = [], [], []
    for t, r in results.items():
        if r.tradable is True:
            tradable.append(t)
        elif r.tradable is False:
            not_tradable.append(t)
        else:
            unknown.append(t)
    # Unbekannte standardmäßig durchlassen (kein Signalverlust bei Broker-Ausfall);
    # im strikten Modus bleiben sie draußen.
    if not drop_unknown:
        tradable = tradable + unknown
    return {
        "tradable": sorted(set(tradable)),
        "not_tradable": sorted(set(not_tradable)),
        "unknown": sorted(set(unknown)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _load_index_members(index: str, db_path: str = _DB_PATH) -> list[str]:
    """Mitglieder eines Index (z.B. '^RUT') aus yf_tickers.db."""
    db = Tools().get_path(path=db_path, file_name="yf_tickers.db")
    with open_db(db, readonly=True) as conn:
        rows = conn.execute("""
            SELECT s.Ticker FROM stocks s
            JOIN stock_indices si ON si.stock_id = s.id
            JOIN indices i ON i.id = si.index_id
            WHERE i.name = ?
            ORDER BY s.Ticker
        """, (index,)).fetchall()
    return [r[0] for r in rows]


def _parse_cli(argv):
    opts = {"tickers": [], "broker": None, "out": None, "strict": False,
            "no_net": False, "no_cache": False, "log": "INFO"}
    for arg in argv[1:]:
        if not arg.startswith("/"):
            continue
        key, _, val = arg[1:].partition(":")
        key = key.lower()
        if key == "tickers" and val:
            opts["tickers"] += [t.strip().upper() for t in val.split(",") if t.strip()]
        elif key == "index" and val:
            opts["tickers"] += _load_index_members(val)
        elif key == "file" and val:
            with open(val, "r", encoding="utf-8") as fh:
                text = fh.read().replace(",", " ").replace("\n", " ")
            opts["tickers"] += [t.strip().upper() for t in text.split() if t.strip()]
        elif key == "broker" and val:
            opts["broker"] = val.strip().lower()
        elif key == "out" and val:
            opts["out"] = val
        elif key == "strict":
            opts["strict"] = True
        elif key in ("no_net", "nonet"):
            opts["no_net"] = True
        elif key in ("no_cache", "nocache"):
            opts["no_cache"] = True
        elif key == "log" and val:
            opts["log"] = val.upper()
    # dedupe, Reihenfolge erhalten
    seen, uniq = set(), []
    for t in opts["tickers"]:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    opts["tickers"] = uniq
    return opts


def main(argv=None):
    argv = argv or sys.argv
    opts = _parse_cli(argv)
    logging.basicConfig(level=getattr(logging, opts["log"], logging.INFO),
                        format="%(levelname)s %(name)s: %(message)s")

    if not opts["tickers"]:
        print(__doc__)
        print("Keine Ticker angegeben (/tickers:, /index: oder /file:).")
        return

    broker = opts["broker"] or (_cfg("broker", "none") or "none")
    print(f"Broker: {broker}  |  {len(opts['tickers'])} Ticker  |  "
          f"strict={opts['strict']}")

    results = check_tradable(
        opts["tickers"], broker_id=opts["broker"],
        allow_network=not opts["no_net"], use_cache=not opts["no_cache"],
    )
    buckets = {"tradable": [], "not_tradable": [], "unknown": []}
    for t, r in results.items():
        bucket = ("tradable" if r.tradable is True
                  else "not_tradable" if r.tradable is False else "unknown")
        buckets[bucket].append(t)

    print(f"  [+] handelbar     : {len(buckets['tradable'])}")
    print(f"  [-] nicht handelbar: {len(buckets['not_tradable'])}")
    print(f"  [?] unbekannt      : {len(buckets['unknown'])}")
    if buckets["not_tradable"]:
        print("  nicht handelbar:", ", ".join(sorted(buckets["not_tradable"])))
    if buckets["unknown"]:
        print("  unbekannt:", ", ".join(sorted(buckets["unknown"])))

    if opts["out"]:
        payload = {"broker": broker,
                   "generated_at": datetime.now().isoformat(),
                   "summary": {k: len(v) for k, v in buckets.items()},
                   "buckets": {k: sorted(v) for k, v in buckets.items()},
                   "details": [results[t].as_dict() for t in opts["tickers"]]}
        with open(opts["out"], "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, ensure_ascii=False)
        print(f"  -> Report geschrieben: {opts['out']}")


if __name__ == "__main__":
    main()
