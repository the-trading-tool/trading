"""
broker_tradability.py — Broker-agnostic tradability check.

Answers, per ticker, the question: *"Is this instrument tradable at my broker?"* —
so that generated signals can be restricted to actually orderable instruments.

Brokers are plugged in via a plugin mechanism:
  - scalable : Scalable Capital (gettex + Xetra + LS Exchange) — per-ISIN query
               via the local proxy ``unofficial-scalable-capital-api``
               (``/securities/{isin}/tradability``, default port 3141).
  - alpaca   : Alpaca — ``/v2/assets`` list (US equities), symbol-based.
  - ibkr     : Interactive Brokers — permissive (SMART routing covers practically
               all listed US/EU instruments), optional exclusion list.
  - none     : no filter — everything tradable (default).

The active broker is stored in ``config.db`` under ``'<user>:broker'``.

Results are cached in ``asset_info.db`` (table ``broker_tradability_cache``) and
re-checked online only after ``REFRESH_DAYS`` — one query per ISIN, then offline.
ISINs come from ``yf_tickers.db`` (column ``stocks.ISIN``); missing ones are
fetched via yfinance/FMP on demand and written back.

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

REFRESH_DAYS = 7          # number of days after which a cached result is re-checked
_DB_PATH = "database"


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Tradability:
    ticker: str
    isin: Optional[str]
    broker: str
    tradable: Optional[bool]      # True / False / None (= unknown, e.g. broker offline)
    venues: Optional[str]         # e.g. "gettex,xetra" — if supplied by the broker
    source: str                   # 'cache' | 'scalable_proxy' | 'alpaca_assets' | ...

    def as_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# ISIN resolution (yf_tickers.db → optional yfinance/FMP fetch)
# ─────────────────────────────────────────────────────────────────────────────

class IsinResolver(Tools):
    """Return ISINs from yf_tickers.db; optionally fetch missing ones and write them back."""

    def __init__(self, db_path: str = _DB_PATH):
        self._db = self.get_path(path=db_path, file_name="yf_tickers.db")

    def get(self, ticker: str) -> Optional[str]:
        """Return ISIN from stocks.ISIN (None for index/FX or when missing)."""
        try:
            with open_db(self._db, readonly=True) as conn:
                row = conn.execute(
                    "SELECT ISIN FROM stocks WHERE Ticker = ?", (ticker,)
                ).fetchone()
            if row and row[0]:
                return str(row[0]).strip().upper()
        except Exception as e:
            logger.debug("ISIN lookup for %s failed: %s", ticker, e)
        return None

    def resolve(self, ticker: str, allow_network: bool = True) -> Optional[str]:
        """Get ISIN; optionally fetch via yfinance/FMP when missing and persist it."""
        isin = self.get(ticker)
        if isin or not allow_network:
            return isin
        if ticker.startswith("^") or ticker.endswith("=X"):
            return None
        try:
            from backfill_isin import fetch_isin
            isin = fetch_isin(ticker)
        except Exception as e:
            logger.debug("ISIN fetch for %s failed: %s", ticker, e)
            isin = None
        if isin:
            self._persist(ticker, isin)
        return isin

    def _persist(self, ticker: str, isin: str):
        """Write ISIN back to stocks (best effort)."""
        try:
            with open_db(self._db) as conn:
                conn.execute(
                    "UPDATE stocks SET ISIN = ? WHERE Ticker = ? AND (ISIN IS NULL OR ISIN = '')",
                    (isin, ticker),
                )
                conn.commit()
        except Exception as e:
            logger.debug("ISIN persist for %s failed: %s", ticker, e)


# ─────────────────────────────────────────────────────────────────────────────
# Broker checker (plugin base + implementations)
# ─────────────────────────────────────────────────────────────────────────────

class BrokerChecker:
    """Base class. Subclasses implement ``_check(ticker, isin)``."""

    broker_id = "base"
    needs_isin = False

    def _check(self, ticker: str, isin: Optional[str]) -> Tradability:
        raise NotImplementedError


class NoFilterChecker(BrokerChecker):
    """No filter — everything tradable (for users without broker restrictions)."""

    broker_id = "none"

    def _check(self, ticker, isin):
        return Tradability(ticker, isin, self.broker_id, True, None, "no_filter")


class ScalableChecker(BrokerChecker):
    """Scalable Capital via local proxy (unofficial-scalable-capital-api).

    Queries ``GET {base}/securities/{isin}/tradability`` (tradability across venues —
    more appropriate than ``/buyable``, which checks buyability within own portfolios).
    If the proxy is unreachable or the ISIN is missing, ``tradable=None`` (unknown)
    is reported — the filter does not lose signals due to an infrastructure outage.

    The proxy runs on port 3141 by default (``http://127.0.0.1:3141``); changed only
    when started with ``--port``.

    Configuration (config.db, per user):
      - ``scalable_proxy_url``     default ``http://localhost:3141``
      - ``scalable_gateway_token`` optional → header ``X-Gateway-Token``
    """

    broker_id = "scalable"
    needs_isin = True

    def __init__(self, base_url: str, token: Optional[str] = None):
        self._base = (base_url or "http://localhost:3141").rstrip("/")
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
                f"{self._base}/securities/{isin}/tradability", headers=headers, timeout=10
            )
            if resp.status_code == 404:
                # Proxy does not know the instrument → not tradable at Scalable
                return Tradability(ticker, isin, self.broker_id, False, None, "scalable_proxy")
            resp.raise_for_status()
            tradable, venues = _parse_scalable_payload(resp.json())
            return Tradability(ticker, isin, self.broker_id, tradable, venues, "scalable_proxy")
        except Exception as e:
            logger.debug("Scalable proxy for %s (%s) unreachable: %s", ticker, isin, e)
            return Tradability(ticker, isin, self.broker_id, None, None, "proxy_unreachable")


class AlpacaChecker(BrokerChecker):
    """Alpaca — tradability via the ``/v2/assets`` list (US equities).

    Loads the active, tradable symbols once per run and checks via symbol membership.
    Without an API key, ``tradable=None`` is reported.

    Keys: KSP entry ``alpaca`` (user/password) or env vars
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
            logger.debug("Alpaca assets fetch failed: %s", e)
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
    """Interactive Brokers — permissive heuristic.

    IBKR covers practically all instruments listed on major exchanges via SMART
    routing. Without a running TWS/Gateway there is no reliable online contract
    check, so: tradable = True when an ISIN exists or the symbol is US; individual
    tickers can be excluded via config.db ``ibkr_exclude`` (comma-separated).
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
# Helper functions for checkers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_scalable_payload(payload) -> tuple[Optional[bool], Optional[str]]:
    """Robustly parse the /tradability (or /buyable) response → (tradable, venues).

    The response structure of the unofficial proxy is not guaranteed. Checked in order:
      1. direct bool,
      2. a venue list (tradability across trading venues) — tradable = at least one
         venue allows buying; venue names are collected,
      3. flat bool fields (e.g. /buyable).
    On ambiguous structure → (None, None) so the filter degrades cleanly.
    """
    if isinstance(payload, bool):
        return payload, None

    # 2. Venue list — directly or under a known key
    venue_list = None
    if isinstance(payload, list):
        venue_list = payload
    elif isinstance(payload, dict):
        for vk in ("venues", "tradingVenues", "tradability", "exchanges"):
            if isinstance(payload.get(vk), list):
                venue_list = payload[vk]
                break

    if venue_list is not None:
        buyable, names = None, []
        for v in venue_list:
            if not isinstance(v, dict):
                continue
            flag = next((v[k] for k in ("buy", "buyable", "tradable", "isTradable")
                         if isinstance(v.get(k), bool)), None)
            if flag is True:
                buyable = True
                nm = (v.get("name") or v.get("venue") or v.get("exchange")
                      or v.get("venueId"))
                if nm:
                    names.append(str(nm))
            elif flag is False and buyable is None:
                buyable = False
        return buyable, (",".join(dict.fromkeys(names)) or None)

    # 3. Flat bool fields
    if isinstance(payload, dict):
        for key in ("buyable", "tradable", "isTradable", "isBuyable", "buy"):
            if isinstance(payload.get(key), bool):
                venues = payload.get("exchanges")
                if isinstance(venues, list):
                    venues = ",".join(str(v) for v in venues)
                return payload[key], venues if isinstance(venues, str) else None
    return None, None


def _alpaca_creds() -> tuple[str, str]:
    """Return Alpaca credentials from KSP (entry 'alpaca') or env."""
    try:
        from tradinglib.ksplib import Ksp
        creds = Ksp(storage_path=_DB_PATH, secrets_path=_DB_PATH).get_ksp("alpaca")
        if creds:
            key_id = creds.get("user") or creds.get("username") or ""
            secret = creds.get("password") or ""
            if key_id and secret:
                return key_id, secret
    except Exception as e:
        logger.debug("KSP lookup for Alpaca failed: %s", e)
    return (os.environ.get("APCA_API_KEY_ID", ""),
            os.environ.get("APCA_API_SECRET_KEY", ""))


# ─────────────────────────────────────────────────────────────────────────────
# Configuration / factory
# ─────────────────────────────────────────────────────────────────────────────

def _cfg(key: str, default=None, username: str = "admin"):
    """Read a single config value (without Streamlit dependency)."""
    try:
        from tradinglib import system_config as sysconf
        return sysconf.SystemConfig(username=username, region=None,
                                    bare_mode=True).get_value(key, default)
    except Exception as e:
        logger.debug("Config lookup '%s' failed: %s", key, e)
        return default


def get_checker(broker_id: Optional[str] = None, username: str = "admin") -> BrokerChecker:
    """Build a checker for the (configured) broker."""
    if broker_id is None:
        broker_id = (_cfg("broker", "none", username) or "none")
    broker_id = str(broker_id).strip().lower()

    if broker_id == "scalable":
        return ScalableChecker(
            base_url=_cfg("scalable_proxy_url", "http://localhost:3141", username),
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
    """Persistent cache in asset_info.db (broker_tradability_cache)."""

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
                        tradable   INTEGER,   -- 1 / 0 / NULL (unknown)
                        venues     TEXT,
                        source     TEXT,
                        checked_at TEXT,
                        PRIMARY KEY (broker, ticker)
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.debug("Cache setup skipped: %s", e)

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
            logger.debug("Cache get %s/%s failed: %s", broker, ticker, e)
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
            logger.debug("Cache put %s/%s failed: %s", r.broker, r.ticker, e)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def check_tradable(tickers, broker_id: Optional[str] = None, *,
                   username: str = "admin", allow_network: bool = True,
                   use_cache: bool = True) -> dict[str, Tradability]:
    """Determine tradability per ticker → {ticker: Tradability}.

    Order per ticker: cache (fresh) → resolve ISIN → query broker → cache result.
    Network errors yield ``tradable=None`` (unknown) but do not abort the run.
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
        # Only fetch ISIN online where the broker actually needs it
        # (Scalable). For none/ibkr/alpaca the local ISIN suffices → no network storm.
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
    """Split a ticker list into tradable / not-tradable / unknown.

    drop_unknown=False (default): unknown results (broker offline etc.) go into
    ``tradable`` — no signal loss on infrastructure problems.
    drop_unknown=True (strict): only clearly tradable instruments pass.

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
    # Unknown results pass through by default (no signal loss on broker outage);
    # in strict mode they are excluded.
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
    """Return members of an index (e.g. '^RUT') from yf_tickers.db."""
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
    # dedupe, preserve order
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
        print("No tickers specified (/tickers:, /index: or /file:).")
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

    print(f"  [+] tradable      : {len(buckets['tradable'])}")
    print(f"  [-] not tradable  : {len(buckets['not_tradable'])}")
    print(f"  [?] unknown       : {len(buckets['unknown'])}")
    if buckets["not_tradable"]:
        print("  not tradable:", ", ".join(sorted(buckets["not_tradable"])))
    if buckets["unknown"]:
        print("  unknown:", ", ".join(sorted(buckets["unknown"])))

    if opts["out"]:
        payload = {"broker": broker,
                   "generated_at": datetime.now().isoformat(),
                   "summary": {k: len(v) for k, v in buckets.items()},
                   "buckets": {k: sorted(v) for k, v in buckets.items()},
                   "details": [results[t].as_dict() for t in opts["tickers"]]}
        with open(opts["out"], "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, ensure_ascii=False)
        print(f"  -> Report written: {opts['out']}")


if __name__ == "__main__":
    main()
