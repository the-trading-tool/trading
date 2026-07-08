"""
correlation_index.py — Rolling cross-asset correlation index.

Computes rolling Pearson correlations of daily log-returns for a fixed set of
macro asset pairs and stores the time series in ``correlation_index.db`` so the
Market → Korrelationsindex page can render them without recomputing.

Data source is purely local: the ``day_data`` table of each ``yf_<symbol>.db``
(path resolved via ``FetchData.get_path`` → respects the ``TradingDB`` env var).

The daily recompute is meant to run from the scheduler:

    python -m tradinglib.correlation_index            # recompute + store
    python -m tradinglib.correlation_index /quiet      # no stdout summary

Add it in Admin → Scheduler as a `daily` job, e.g. at 22:30 after the price
fetch has run.
"""
import logging
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

from tradinglib.fetch_data import FetchData
from tradinglib.tools import open_db

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

DB_NAME = 'correlation_index.db'

# Rolling correlation windows (trading days). corr_30 ≈ 6 weeks, corr_90 ≈ 4.5 months.
WINDOWS = (30, 90)

# Logical id → (local yf_<symbol>.db stem, human label)
# NOTE: the local price DB stem differs from the display ticker for some symbols:
#   ^SPX  → richest local history is yf_^SPX.db (not the ^GSPC alias)
#   USD   → the US Dollar Index is DX-Y.NYB (yf_DX-Y.NYB.db, Close ~90-115).
#           Do NOT use yf_DX.db — that stem is the *stock* "DX" (Dynex Capital,
#           a mortgage REIT, Close ~13) and would correlate positively with SPX.
#   BTC   → only BTC-EUR available locally; used as a proxy for BTC-USD
#           (daily-return correlation is nearly identical — the difference is only
#            the small EUR/USD return component).
# The [1] label is a code-level/CLI fallback only; the Streamlit page renders the
# user-facing symbol name via i18n (locale key corr.sym.<KEY>).
SYMBOLS: dict[str, tuple[str, str]] = {
    'SPX':    ('^SPX',      'S&P 500 (^SPX)'),
    'BTC':    ('BTC-EUR',   'Bitcoin (BTC-EUR, proxy for BTC-USD)'),
    'GOLD':   ('GC=F',      'Gold (GC=F)'),
    'USD':    ('DX-Y.NYB',  'US Dollar Index (DX-Y.NYB)'),
    'COPPER': ('HG=F',      'Copper (HG=F)'),
    'ZN':     ('ZN=F',      'US Treasuries 10Y (ZN=F)'),
}

# Asset pairs (order = as requested). The `label` is a code-level/CLI + DB-meta
# fallback only; the Streamlit page renders label, interpretation narratives and
# background note via i18n (locale keys corr.pair.<id>.{label,narr_pos,narr_neg,context}),
# picking narr_pos vs. narr_neg by the sign of the current correlation.
PAIRS: list[dict] = [
    {'id': 'spx_btc',    'a': 'SPX',    'b': 'BTC',  'label': 'S&P 500 <-> Bitcoin'},
    {'id': 'btc_gold',   'a': 'BTC',    'b': 'GOLD', 'label': 'Bitcoin <-> Gold'},
    {'id': 'spx_usd',    'a': 'SPX',    'b': 'USD',  'label': 'S&P 500 <-> US Dollar Index'},
    {'id': 'copper_spx', 'a': 'COPPER', 'b': 'SPX',  'label': 'Copper <-> S&P 500'},
    {'id': 'zn_spx',     'a': 'ZN',     'b': 'SPX',  'label': 'US Treasuries (10Y) <-> S&P 500'},
]

# ── Data loading ─────────────────────────────────────────────────────────────


def _load_close_series(local_symbol: str, fd: FetchData | None = None) -> pd.Series:
    """Return the full daily Close series of ``yf_<local_symbol>.db`` (day_data).

    Indexed by tz-naive date, sorted, deduplicated. Empty Series when the DB or
    table is missing. Path is resolved via FetchData.get_path (honours TradingDB).
    """
    fd = fd or FetchData()
    db_path = fd.get_path(path='database', file_name=f'yf_{local_symbol}.db')
    if not os.path.exists(db_path):
        logger.warning("correlation_index: price DB missing for %s (%s)", local_symbol, db_path)
        return pd.Series(dtype='float64')

    try:
        with open_db(db_path, readonly=True) as conn:
            df = pd.read_sql_query(
                "SELECT Date, Close FROM day_data ORDER BY Date", conn
            )
    except Exception as exc:
        logger.warning("correlation_index: read failed for %s: %s", local_symbol, exc)
        return pd.Series(dtype='float64')

    if df.empty:
        return pd.Series(dtype='float64')

    df['Date'] = pd.to_datetime(df['Date'], format='mixed', errors='coerce').dt.normalize()
    df = df.dropna(subset=['Date', 'Close'])
    s = pd.Series(df['Close'].to_numpy(dtype='float64'), index=df['Date'])
    s = s[~s.index.duplicated(keep='last')].sort_index()
    return s


# ── Correlation computation ──────────────────────────────────────────────────


def compute_pair(series_a: pd.Series, series_b: pd.Series,
                 windows: tuple[int, ...] = WINDOWS) -> pd.DataFrame:
    """Rolling Pearson correlation of daily log-returns for one pair.

    Returns a DataFrame indexed by Date with one ``corr_<w>`` column per window.
    Empty DataFrame when the aligned overlap is too short.
    """
    if series_a.empty or series_b.empty:
        return pd.DataFrame()

    aligned = pd.concat([series_a.rename('a'), series_b.rename('b')], axis=1).dropna()
    # Guard against non-positive prices before log()
    aligned = aligned[(aligned['a'] > 0) & (aligned['b'] > 0)]
    if len(aligned) < min(windows) + 2:
        return pd.DataFrame()

    ret = np.log(aligned).diff().dropna()

    out = pd.DataFrame(index=ret.index)
    for w in windows:
        out[f'corr_{w}'] = ret['a'].rolling(w).corr(ret['b'])

    out = out.dropna(how='all')
    return out


def full_sample_corr(series_a: pd.Series, series_b: pd.Series) -> float | None:
    """Correlation of daily log-returns over the entire common history."""
    aligned = pd.concat([series_a.rename('a'), series_b.rename('b')], axis=1).dropna()
    aligned = aligned[(aligned['a'] > 0) & (aligned['b'] > 0)]
    if len(aligned) < 3:
        return None
    ret = np.log(aligned).diff().dropna()
    val = ret['a'].corr(ret['b'])
    return None if pd.isna(val) else round(float(val), 4)


def compute_all(windows: tuple[int, ...] = WINDOWS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute rolling + full-sample correlations for every pair.

    Returns (series_df, meta_df):
      series_df: long format [pair_id, Date, corr_30, corr_90]
      meta_df:   one row per pair [pair_id, sym_a, sym_b, label, full_corr,
                 n_obs, first_date, last_date]
    """
    fd = FetchData()
    close_cache: dict[str, pd.Series] = {}

    def _close(logical_id: str) -> pd.Series:
        if logical_id not in close_cache:
            local_sym = SYMBOLS[logical_id][0]
            close_cache[logical_id] = _load_close_series(local_sym, fd)
        return close_cache[logical_id]

    series_frames: list[pd.DataFrame] = []
    meta_rows: list[dict] = []

    for pair in PAIRS:
        a = _close(pair['a'])
        b = _close(pair['b'])
        corr = compute_pair(a, b, windows)

        if corr.empty:
            logger.warning("correlation_index: no data for pair %s", pair['id'])
            meta_rows.append({
                'pair_id': pair['id'], 'sym_a': pair['a'], 'sym_b': pair['b'],
                'label': pair['label'], 'full_corr': None, 'n_obs': 0,
                'first_date': None, 'last_date': None,
            })
            continue

        frame = corr.reset_index().rename(columns={'index': 'Date'})
        if 'Date' not in frame.columns:
            frame = frame.rename(columns={frame.columns[0]: 'Date'})
        frame.insert(0, 'pair_id', pair['id'])
        frame['Date'] = pd.to_datetime(frame['Date']).dt.strftime('%Y-%m-%d')
        series_frames.append(frame)

        meta_rows.append({
            'pair_id': pair['id'], 'sym_a': pair['a'], 'sym_b': pair['b'],
            'label': pair['label'],
            'full_corr': full_sample_corr(a, b),
            'n_obs': int(len(corr)),
            'first_date': str(corr.index[0].date()),
            'last_date': str(corr.index[-1].date()),
        })

    series_df = (pd.concat(series_frames, ignore_index=True)
                 if series_frames else pd.DataFrame(columns=['pair_id', 'Date'] +
                                                     [f'corr_{w}' for w in windows]))
    meta_df = pd.DataFrame(meta_rows)
    return series_df, meta_df


# ── Persistence ──────────────────────────────────────────────────────────────


def save_to_db(series_df: pd.DataFrame, meta_df: pd.DataFrame,
               fd: FetchData | None = None) -> str:
    """Overwrite correlation_index.db with the fresh series + meta tables.

    Returns the DB path. A full daily recompute is cheap, so a plain replace
    (no upsert) keeps the schema trivial and avoids stale rows.
    """
    fd = fd or FetchData()
    db_path = fd.get_path(path='database', file_name=DB_NAME)
    with open_db(db_path) as conn:
        series_df.to_sql('correlation', conn, if_exists='replace', index=False)
        meta_df.to_sql('correlation_meta', conn, if_exists='replace', index=False)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_corr_pair_date ON correlation (pair_id, Date)"
        )
        conn.execute("DROP TABLE IF EXISTS correlation_run")
        conn.execute("CREATE TABLE correlation_run (updated_at TEXT)")
        conn.execute("INSERT INTO correlation_run (updated_at) VALUES (?)",
                     (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
        conn.commit()
    return db_path


def load_from_db(fd: FetchData | None = None) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    """Read stored correlation series + meta + last-run timestamp.

    Returns (series_df, meta_df, updated_at). Empty frames / None when the DB
    does not exist yet.
    """
    fd = fd or FetchData()
    db_path = fd.get_path(path='database', file_name=DB_NAME)
    if not os.path.exists(db_path):
        return pd.DataFrame(), pd.DataFrame(), None
    try:
        with open_db(db_path, readonly=True) as conn:
            series = pd.read_sql_query("SELECT * FROM correlation", conn)
            try:
                meta = pd.read_sql_query("SELECT * FROM correlation_meta", conn)
            except Exception:
                meta = pd.DataFrame()
            try:
                updated_at = conn.execute(
                    "SELECT updated_at FROM correlation_run LIMIT 1"
                ).fetchone()
                updated_at = updated_at[0] if updated_at else None
            except Exception:
                updated_at = None
    except Exception as exc:
        logger.warning("correlation_index: load_from_db failed: %s", exc)
        return pd.DataFrame(), pd.DataFrame(), None
    return series, meta, updated_at


def run(windows: tuple[int, ...] = WINDOWS, quiet: bool = False) -> None:
    """Recompute all pairs and persist them. Entry point for the scheduler."""
    series_df, meta_df = compute_all(windows)
    db_path = save_to_db(series_df, meta_df)
    if not quiet:
        print(f"[correlation_index] {datetime.now():%Y-%m-%d %H:%M:%S} -> {db_path}")
        for _, row in meta_df.iterrows():
            fc = row['full_corr']
            fc_str = f"{fc:+.2f}" if fc is not None else "n/a"
            print(f"  {row['pair_id']:<12} {row['label']:<34} "
                  f"n={row['n_obs']:>5}  full={fc_str}")


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    # Scheduler captures stdout on a cp1252 Windows console; the pair labels
    # contain non-latin1 glyphs (↔). Force UTF-8 so the summary can't crash the job.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    from tradinglib import logging_config as lgc
    lgc.enable_logging(to_console=True, level='INFO')
    _quiet = any(a.lower() in ('/quiet', '--quiet') for a in sys.argv[1:])
    run(quiet=_quiet)
