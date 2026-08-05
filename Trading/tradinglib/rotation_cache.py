"""Persistent per-day cache for the Rotation / Correlation hub.

The hub's four dashboards (Global Rotation, Sector Rotation, Correlation,
Fear & Greed) are all decorated with ``@st.cache_data``, but that cache only
lives in the RAM of the current Streamlit process: after a restart, a cache
eviction, a TTL expiry or in a second worker every dashboard recomputes from
scratch — and because ``st.tabs`` renders *all* tab bodies eagerly, opening the
hub triggers every one of them at once.

All of these dashboards are built from daily bars, so ONE computation per key
and calendar day is enough. This module is the persistent layer that makes that
possible (SQLite, WAL, JSON payloads) — identical in spirit to the
``stress_cache`` table behind :func:`regime_data_engine.compute_market_stress`.

Because the cache lives on disk it can be filled *ahead of time* by a scheduled
job (``warm_rotation.py``), so the first visitor of the day pays nothing.

Payloads may contain DataFrames (sector summary, pair matrix), so the JSON
codec below round-trips those explicitly rather than relying on plain
``json.dumps``.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from io import StringIO

import numpy as np
import pandas as pd

from tradinglib import tools as ts

logger = logging.getLogger(__name__)

_CACHE_DB = 'rotation_cache.db'


# ── JSON codec (DataFrame / tuple aware) ──────────────────────────────────────

def _enc(o):
    """Recursively convert a payload into JSON-serialisable form."""
    if isinstance(o, pd.DataFrame):
        if o.empty:
            # to_json/read_json does not round-trip an empty frame's index dtype
            # (int → float), so rebuild empty frames from their schema instead.
            return {"__df_empty__": {"columns": [str(c) for c in o.columns],
                                     "dtypes": [str(d) for d in o.dtypes]}}
        return {"__df__": o.to_json(orient="split", date_format="iso")}
    if isinstance(o, pd.Series):
        return {"__series__": o.to_json(orient="split", date_format="iso")}
    if isinstance(o, tuple):
        return {"__tuple__": [_enc(v) for v in o]}
    if isinstance(o, dict):
        # Plain JSON coerces non-str keys to strings, which would silently break
        # the round-trip — keep those as an explicit item list instead.
        if all(isinstance(k, str) for k in o):
            return {k: _enc(v) for k, v in o.items()}
        return {"__items__": [[_enc(k), _enc(v)] for k, v in o.items()]}
    if isinstance(o, list):
        return [_enc(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (pd.Timestamp, dt.date, dt.datetime)):
        return {"__ts__": o.isoformat()}
    return o


def _dec(o):
    """Inverse of :func:`_enc`."""
    if isinstance(o, dict):
        if "__df__" in o and len(o) == 1:
            return pd.read_json(StringIO(o["__df__"]), orient="split")
        if "__df_empty__" in o and len(o) == 1:
            spec = o["__df_empty__"]
            return pd.DataFrame({c: pd.Series(dtype=d) for c, d
                                 in zip(spec["columns"], spec["dtypes"])})
        if "__series__" in o and len(o) == 1:
            return pd.read_json(StringIO(o["__series__"]), orient="split", typ="series")
        if "__tuple__" in o and len(o) == 1:
            return tuple(_dec(v) for v in o["__tuple__"])
        if "__items__" in o and len(o) == 1:
            return {_dec(k): _dec(v) for k, v in o["__items__"]}
        if "__ts__" in o and len(o) == 1:
            return pd.Timestamp(o["__ts__"])
        return {k: _dec(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_dec(v) for v in o]
    return o


# ── Storage ───────────────────────────────────────────────────────────────────

# ── Cache keys ────────────────────────────────────────────────────────────────
# Built here rather than at the call sites so the pages and warm_rotation.py
# cannot drift apart — a mismatched key would silently defeat the warming.

def sector_key(benchmark: str, period: str, etfs_json: str,
               weights_json: str, include_pe: bool) -> str:
    """Cache key for one Sector Rotation configuration."""
    return f"sector|{benchmark}|{period}|{include_pe}|{etfs_json}|{weights_json}"


def stock_key(sector: str, rank_col: str, top_n: int,
              show_rsc: bool, sector_etf: str) -> str:
    """Cache key for one Best-of-Sector table configuration."""
    return f"stocks|{sector}|{rank_col}|{top_n}|{show_rsc}|{sector_etf}"


def assessment_key(ticker: str, username: str) -> str:
    """Cache key for the dashboard's market assessment."""
    return f"assessment|{ticker}|{username}"


def global_key(scope: str) -> str:
    """Cache key for a Global Rotation scope ('equities', 'all', 'uni|<name>')."""
    return f"global|{scope}"


def fear_greed_key(index: str) -> str:
    """Cache key for the Fear & Greed index of one exchange index."""
    return f"fear_greed|{index}"


# ── Storage paths ─────────────────────────────────────────────────────────────

def _db_path() -> str:
    """Absolute path of the rotation cache database."""
    return ts.Tools().get_path(path='database', file_name=_CACHE_DB)


def _today() -> str:
    """Today as an ISO date string — the second half of every cache key."""
    return dt.date.today().isoformat()


def get(key: str):
    """Return today's cached payload for *key*, or ``None`` if not cached."""
    try:
        with ts.open_db(_db_path(), readonly=True) as conn:
            row = conn.execute(
                "SELECT payload FROM rotation_cache WHERE cache_key=? AND day=?",
                (key, _today()),
            ).fetchone()
        return _dec(json.loads(row[0])) if row and row[0] else None
    except Exception:
        # Missing table/file on first run is normal — treat as a cache miss.
        return None


def put(key: str, value) -> None:
    """Persist *value* for *key* + today (best effort — never raises)."""
    try:
        with ts.open_db(_db_path()) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rotation_cache (
                    cache_key TEXT, day TEXT, payload TEXT,
                    PRIMARY KEY (cache_key, day)
                )""")
            conn.execute("""
                INSERT INTO rotation_cache (cache_key, day, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key, day) DO UPDATE SET payload=excluded.payload
            """, (key, _today(), json.dumps(_enc(value))))
            conn.commit()
    except Exception as exc:
        logger.debug("rotation_cache put %s failed: %s", key, exc)


def get_or_compute(key: str, fn):
    """Return the cached payload for *key*, computing and storing it on a miss.

    Empty results (``None``, empty dict/DataFrame) are deliberately NOT cached
    so a transient data outage doesn't pin an empty dashboard for the rest of
    the day.
    """
    hit = get(key)
    if hit is not None:
        return hit
    value = fn()
    if _is_worth_caching(value):
        put(key, value)
    return value


def _is_worth_caching(value) -> bool:
    """False for empty/failed results, which should stay retryable."""
    if value is None:
        return False
    if isinstance(value, pd.DataFrame):
        return not value.empty
    if isinstance(value, tuple):
        # The first element is the primary payload in every tuple we cache
        # ((summary, rrg_w, rrg_d) and (df, debug_info)). Judging by `any()`
        # would happily cache an empty table whose debug string is non-empty.
        return bool(value) and _is_worth_caching(value[0])
    if isinstance(value, (dict, list)):
        return bool(value)
    return True


def drop(key: str) -> None:
    """Remove one key for today — lets a warm run with /force really recompute
    even when the producing function caches internally."""
    try:
        with ts.open_db(_db_path()) as conn:
            conn.execute("DELETE FROM rotation_cache WHERE cache_key=? AND day=?",
                         (key, _today()))
            conn.commit()
    except Exception as exc:
        logger.debug("rotation_cache drop %s failed: %s", key, exc)


def clear() -> None:
    """Drop all persisted entries (used by the UI's manual refresh button)."""
    try:
        with ts.open_db(_db_path()) as conn:
            conn.execute("DELETE FROM rotation_cache")
            conn.commit()
    except Exception as exc:
        logger.debug("rotation_cache clear failed: %s", exc)


def purge_old(keep_days: int = 3) -> int:
    """Delete entries older than *keep_days*; returns the number of rows removed."""
    cutoff = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    try:
        with ts.open_db(_db_path()) as conn:
            cur = conn.execute("DELETE FROM rotation_cache WHERE day < ?", (cutoff,))
            conn.commit()
            return cur.rowcount or 0
    except Exception:
        return 0
