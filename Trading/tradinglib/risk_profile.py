"""Risk profiles: what an investor is willing to sit through, as a filter.

The measurement in ``score_eval`` produced one clear result: forward *return* is
barely predictable (rank IC 0.065 for the stored composite score), forward
*risk* is highly predictable (0.70 for volatility, -0.33 for drawdown). So the
profile belongs on the risk axis, where a promise can actually be kept, and the
trend signal does the ranking *inside* the universe the profile leaves over —
in that order, because it measurably matters: over 2023-2025 the edge of the
6-month momentum quintile doubled once the selection was restricted to the calm
end of the universe.

Two design decisions worth knowing before changing anything here:

* **Absolute cuts, not percentile ranks.** A cross-sectional rank depends on
  whichever tickers happen to be in the panel that day, so the live chart and
  the stored simulation would disagree — the exact two-path problem ``ovt.py``
  has to paper over with its stored/live merge. Every threshold in this module
  is an absolute number, reproducible from a single ticker's own data.
* **The stated band is measured, not asserted.** Each profile carries the
  volatility and drawdown it actually delivered over the calibration window,
  taken at a quantile that is named. "Conservative" does not mean "never more
  than 25 % volatility"; it means "half the time below 21 %, and 85 % of the
  time below 31 %" — a claim that survives contact with the data.

The risk measure is ATR as a share of price: it predicted forward volatility
slightly better than ``logVola`` (Spearman 0.71 vs 0.68), and it is the one
number an investor can picture — "this thing moves about 2 % on an average day".
``logVola`` is the fallback for frames that carry no ATR, cut at the same
coverage so the two routes select the same share of the universe.

Usage::

    from tradinglib import risk_profile as rp
    profile = rp.resolve(username)                 # preset + the user's changes
    mask    = rp.apply(combined_df, profile)       # boolean selection
    expr    = rp.filter_expression(profile)        # for buy/sell formulas
    frame   = rp.score_frame(combined_df)          # riskScore / riskBucket / trendScore

CLI::

    python -m tradinglib.risk_profile              # show the profiles
    python -m tradinglib.risk_profile /calibrate   # re-measure from the sim DBs
    python -m tradinglib.risk_profile /calibrate /save
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Global (not per-user) config slot holding a re-measured calibration.
CALIBRATION_KEY = 'risk_profile_calibration'
CONFIG_KEY = 'risk_profile'
APP_USER = '_app'

# Provenance of the numbers below. Everything in PROFILES except the policy
# fields was measured on this panel; re-running the CLI with /calibrate
# reproduces it and writes an updated calibration into config.db.
CALIBRATION_VERSION = '2026-09-12'
CALIBRATION_SOURCE = ('asset_simulation 2020-2026, 21-bar horizon, '
                      '5.02m rows, 1727 dates, 3653 tickers')

# Quantiles the stated bands refer to. Named here because a band without its
# quantile is not a claim, it is a wish.
VOL_BAND_QUANTILE = 0.85
DRAWDOWN_QUANTILE = 0.10

# Score breakpoints: ATR share of price -> 0..100, 100 = calmest. The inner five
# are the measured quantiles of the universe (p5/p25/p50/p75/p95), frozen into a
# fixed curve so the score is the same number live and in storage; the outer two
# are anchors that let the ends of the scale actually be reached.
ATR_BREAKPOINTS = [0.005, 0.0151, 0.0216, 0.0291, 0.0417, 0.0759, 0.20]
LOG_VOLA_BREAKPOINTS = [0.02, 0.0755, 0.1102, 0.1494, 0.2129, 0.3797, 0.90]
BREAKPOINT_SCORES = [100.0, 95.0, 75.0, 50.0, 25.0, 5.0, 0.0]

# Trend strength: distance to the asset's own record high, mapped onto 0..100.
# The inner points are the measured quantiles (p5/p25/p50/p75/p95) of the
# universe, the ends are anchors — 0 = a full round trip from the high, 100 = at
# the high. The record high is the running maximum of the *full* local daily
# close history, the same definition ``fps_dist_high`` uses.
#
# Why this one measure and not a richer blend: over 2020-2026 nothing about
# trend ranking predicted *return* reliably (12M momentum IC -0.001, distance to
# the high +0.003 at t 0.8), but distance to the high is the only candidate
# whose hit rate *and* drawdown improve monotonically across all five quintiles
# (hit 50.7 -> 52.1 %, drawdown -9.7 -> -6.0 %). Blends with momentum or the SMA
# slope scored better in single years and lost that monotonicity.
TREND_BREAKPOINTS = [-1.0, -0.9076, -0.6362, -0.3876, -0.1811, -0.0361, 0.0]
TREND_SCORES = [0.0, 5.0, 25.0, 50.0, 75.0, 95.0, 100.0]

PROFILES = {
    'conservative': {
        'order': 1,
        'bucket': 1,
        'label_key': 'risk_profile.conservative',      # locale entry follows with the UI
        # --- measured: the cut and what it delivered ---
        'max_atr_pct': 0.022,        # ATR <= 2.2 % of price
        'max_log_vola': 0.1123,      # same coverage on the fallback measure
        'coverage': 0.2149,
        'typical_vol': 0.2133,        # median annualised forward volatility
        'vol_band': 0.315,           # p85 — the stated ceiling
        'drawdown_floor': -0.1022,    # p10 of the worst close within the horizon
        # --- policy: derived from the measurement, but a choice ---
        'stop_loss_pct': 11.0,       # at the measured p10: stops out the worst tenth
        'target_position_vol': 0.21,
    },
    'balanced': {
        'order': 2,
        'bucket': 2,
        'label_key': 'risk_profile.balanced',
        'max_atr_pct': 0.030,
        'max_log_vola': 0.1543,
        'coverage': 0.4625,
        'typical_vol': 0.2494,
        'vol_band': 0.3714,
        'drawdown_floor': -0.117,
        'stop_loss_pct': 12.0,
        'target_position_vol': 0.25,
    },
    'dynamic': {
        'order': 3,
        'bucket': 3,
        'label_key': 'risk_profile.dynamic',
        'max_atr_pct': 0.042,
        'max_log_vola': 0.2147,
        'coverage': 0.7059,
        'typical_vol': 0.2847,
        'vol_band': 0.441,
        'drawdown_floor': -0.1332,
        'stop_loss_pct': 13.0,
        'target_position_vol': 0.28,
    },
    'offensive': {
        'order': 4,
        'bucket': 4,
        'label_key': 'risk_profile.offensive',
        'max_atr_pct': None,         # no cut — the whole universe
        'max_log_vola': None,
        'coverage': 1.0,
        'typical_vol': 0.3393,
        'vol_band': 0.6233,
        'drawdown_floor': -0.1693,
        'stop_loss_pct': 17.0,
        'target_position_vol': 0.34,
    },
}

DEFAULT_PROFILE = 'balanced'

# Fields a user may override on top of a preset. Deliberately short: the
# measured fields describe what the cut delivered and stop being true the moment
# somebody edits them by hand, so only the cut itself and the policy knobs are
# editable. Changing a cut invalidates the stated band — re-run /calibrate.
EDITABLE_FIELDS = ('max_atr_pct', 'max_log_vola', 'stop_loss_pct',
                   'target_position_vol')

DEFAULTS = {
    'profile': DEFAULT_PROFILE,
    'custom': {},          # {field: value} overrides on top of the preset
}


# ---------------------------------------------------------------------------
# Config binding
# ---------------------------------------------------------------------------

def _config(username: str):
    from tradinglib import system_config as sysconf
    return sysconf.SystemConfig(username=username)


def settings(username: str) -> dict:
    """The user's stored choice, filled up with the defaults."""
    out = {'profile': DEFAULTS['profile'], 'custom': {}}
    try:
        stored = _config(username).get_value(CONFIG_KEY, None)
    except Exception:
        logger.debug("risk_profile: settings not readable", exc_info=True)
        return out
    if isinstance(stored, dict):
        name = stored.get('profile')
        if name in PROFILES:
            out['profile'] = name
        custom = stored.get('custom')
        if isinstance(custom, dict):
            out['custom'] = {k: v for k, v in custom.items() if k in EDITABLE_FIELDS}
    return out


def save_settings(username: str, values: dict) -> None:
    """Persist the chosen preset and any overrides for this user."""
    name = values.get('profile')
    payload = {
        'profile': name if name in PROFILES else DEFAULT_PROFILE,
        'custom': {k: v for k, v in (values.get('custom') or {}).items()
                   if k in EDITABLE_FIELDS},
    }
    try:
        _config(username).set_value(CONFIG_KEY, payload)
    except Exception:
        logger.warning("risk_profile: settings not writable", exc_info=True)


def resolve(username: str = '', name: str = '', calibration: dict = None) -> dict:
    """The effective profile: preset, the user's overrides, then the calibration.

    Pass *name* to get a preset directly (ignoring the stored choice), or
    *username* to get what that user actually has configured. Pass
    *calibration* to preview a freshly measured one instead of the stored one.
    """
    if name:
        chosen, custom = (name if name in PROFILES else DEFAULT_PROFILE), {}
    else:
        stored = settings(username) if username else DEFAULTS
        chosen, custom = stored['profile'], stored['custom']

    profile = dict(PROFILES[chosen])
    profile['name'] = chosen
    calibration = calibration or load_calibration()
    if chosen in calibration.get('profiles', {}):
        profile.update(calibration['profiles'][chosen])
        profile['calibration_version'] = calibration.get('version')
    profile.update(custom)
    profile['customised'] = bool(custom)
    return profile


def available() -> list:
    """Profile names in presentation order."""
    return [n for n, _ in sorted(PROFILES.items(), key=lambda kv: kv[1]['order'])]


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def _builtin_calibration() -> dict:
    return {
        'version': CALIBRATION_VERSION,
        'source': CALIBRATION_SOURCE,
        'vol_quantile': VOL_BAND_QUANTILE,
        'drawdown_quantile': DRAWDOWN_QUANTILE,
        'profiles': {},        # empty = the values in PROFILES are current
    }


def load_calibration() -> dict:
    """The stored calibration, or the built-in one shipped with the module."""
    try:
        stored = _config(APP_USER).get_value(CALIBRATION_KEY, None)
        if isinstance(stored, dict) and stored.get('profiles') is not None:
            return stored
    except Exception:
        logger.debug("risk_profile: calibration not readable", exc_info=True)
    return _builtin_calibration()


def save_calibration(calibration: dict) -> None:
    """Store a re-measured calibration app-wide (not per user)."""
    try:
        _config(APP_USER).set_value(CALIBRATION_KEY, calibration)
    except Exception:
        logger.warning("risk_profile: calibration not writable", exc_info=True)


def calibrate(panel=None, years=None, horizon: int = 21) -> dict:
    """Re-measure every profile's band from the simulation data (read-only).

    Returns a calibration dict in the shape ``save_calibration`` expects. What
    changes are the *delivered* numbers — coverage, typical volatility, the band
    and the drawdown floor — never the cut: the cut is the profile's definition,
    the band is what that definition currently buys.
    """
    from tradinglib import score_eval as se

    if panel is None:
        panel = se.load_panel(years=years or [], columns=['logVola', 'atr'],
                              horizon=horizon)
    frame = panel.df.copy()
    frame['atr_pct'] = frame['atr'] / frame['close'] if 'atr' in frame.columns else np.nan

    out = _builtin_calibration()
    out['source'] = (f"asset_simulation {min(panel.years)}-{max(panel.years)}, "
                     f"{horizon}-bar horizon, {len(frame):,} rows, "
                     f"{frame['Date'].nunique()} dates, {frame['ticker'].nunique()} tickers")
    out['horizon'] = horizon
    for name, preset in PROFILES.items():
        mask = _mask_for(frame, preset)
        selected = frame[mask]
        if len(selected) < 1000:
            logger.warning("risk_profile: too few rows to calibrate %s", name)
            continue
        vol = selected['fwd_vol'].dropna()
        mdd = selected['fwd_mdd'].dropna()
        out['profiles'][name] = {
            'coverage': round(len(selected) / len(frame), 4),
            'typical_vol': round(float(vol.quantile(0.5)), 4),
            'vol_band': round(float(vol.quantile(VOL_BAND_QUANTILE)), 4),
            'drawdown_floor': round(float(mdd.quantile(DRAWDOWN_QUANTILE)), 4),
        }
    return out


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def _has(frame, *columns) -> bool:
    return all(column in frame.columns for column in columns)


def _mask_for(frame: pd.DataFrame, profile: dict) -> pd.Series:
    """Boolean selection for a profile, ATR first and logVola as the fallback."""
    keep = pd.Series(True, index=frame.index)
    atr_cut = profile.get('max_atr_pct')
    vola_cut = profile.get('max_log_vola')

    def num(column):
        # Legacy rows can carry numbers as TEXT (bulk_upsert affinity), and a
        # string comparison here would silently select the wrong half.
        return pd.to_numeric(frame[column], errors='coerce')

    if atr_cut is not None and _has(frame, 'atr_pct'):
        keep &= num('atr_pct') <= atr_cut
    elif atr_cut is not None and _has(frame, 'atr', 'close'):
        close = num('close')
        keep &= (num('atr') / close.where(close > 0)) <= atr_cut
    elif vola_cut is not None and _has(frame, 'logVola'):
        keep &= num('logVola') <= vola_cut
    elif atr_cut is not None or vola_cut is not None:
        logger.warning("risk_profile: frame carries neither atr/close nor logVola — "
                       "profile %s selects everything", profile.get('name', '?'))
    return keep.fillna(False)


def apply(frame: pd.DataFrame, profile) -> pd.Series:
    """Boolean mask of the rows that fit *profile* (a dict or a profile name)."""
    if isinstance(profile, str):
        profile = resolve(name=profile)
    return _mask_for(frame, profile)


def filter_expression(profile, prefer: str = 'atr') -> str:
    """The profile as an expression usable in buy/sell formulas.

    The Strategy Finder and Multi Strategies both evaluate their formulas with
    ``ExpressionEvaluator`` over the frame read from ``asset_simulation``, so an
    expression built here drops straight into either of them.
    """
    if isinstance(profile, str):
        profile = resolve(name=profile)
    atr_cut = profile.get('max_atr_pct')
    vola_cut = profile.get('max_log_vola')
    if prefer == 'atr' and atr_cut is not None:
        return f"(atr / close <= {atr_cut:g})"
    if vola_cut is not None:
        return f"(logVola <= {vola_cut:g})"
    return '(close > 0)'      # offensive: no risk cut at all


def bands(profile) -> dict:
    """The profile's stated promise, in the shape ``score_eval.conformity`` wants."""
    if isinstance(profile, str):
        profile = resolve(name=profile)
    return {
        'fwd_vol': (0.0, float(profile['vol_band'])),
        'fwd_mdd': (float(profile['drawdown_floor']), float('inf')),
    }


# ---------------------------------------------------------------------------
# Snapshot of the stored universe
# ---------------------------------------------------------------------------

# Window the snapshot looks back over. Long enough for a bank holiday weekend —
# and taking each ticker's own last row inside it is what makes the snapshot
# safe: the newest date in a simulation DB is regularly half-written, so
# anchoring on MAX(Date) alone would silently reduce the universe to whichever
# handful of tickers happened to be processed first.
SNAPSHOT_LOOKBACK_DAYS = 10

SNAPSHOT_COLUMNS = ('close', 'currency', 'riskScore', 'riskBucket', 'trendScore',
                    'atr', 'logVola', 'sharpe', 'fps_phase')


def universe_snapshot(db_path: str = 'database',
                      db_name: str = 'asset_simulation_.db',
                      with_names: bool = True) -> pd.DataFrame:
    """Latest stored row per ticker, with the profile columns (read-only).

    Returns an empty frame when the simulation DB is missing or the profile
    columns have not been backfilled yet.
    """
    import os
    from tradinglib.tools import Tools, open_db

    path = Tools().get_path(path=db_path, file_name=db_name)
    if not path or not os.path.exists(path):
        logger.warning("risk_profile: %s not found", db_name)
        return pd.DataFrame()

    conn = open_db(path, readonly=True)
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(asset_simulation)")}
        use = [c for c in SNAPSHOT_COLUMNS if c in have]
        newest = conn.execute("SELECT MAX(Date) FROM asset_simulation").fetchone()[0]
        if not newest:
            return pd.DataFrame()
        cutoff = (pd.to_datetime(newest) -
                  pd.Timedelta(days=SNAPSHOT_LOOKBACK_DAYS)).strftime('%Y-%m-%d 00:00:00')
        frame = pd.read_sql_query(
            f"SELECT ticker, Date, {', '.join(use)} FROM asset_simulation "
            f"WHERE Date >= ?", conn, params=(cutoff,))
    except Exception:
        logger.warning("risk_profile: snapshot query failed", exc_info=True)
        return pd.DataFrame()
    finally:
        conn.close()

    if frame.empty:
        return frame
    for column in use:
        if column != 'currency':
            frame[column] = pd.to_numeric(frame[column], errors='coerce')
    frame = frame.sort_values('Date').groupby('ticker', as_index=False).last()

    if with_names:
        frame = frame.merge(_names(db_path, frame['ticker'].tolist()),
                            on='ticker', how='left')
    return frame


def _names(db_path: str, tickers: list) -> pd.DataFrame:
    """Display name and sector per ticker, empty frame when unavailable."""
    import os
    from tradinglib.tools import Tools, open_db

    path = Tools().get_path(path=db_path, file_name='asset_info.db')
    if not path or not os.path.exists(path) or not tickers:
        return pd.DataFrame(columns=['ticker', 'longName', 'sector'])
    conn = open_db(path, readonly=True)
    try:
        return pd.read_sql_query(
            "SELECT ticker, longName, sector FROM asset_info", conn)
    except Exception:
        logger.debug("risk_profile: asset_info unavailable", exc_info=True)
        return pd.DataFrame(columns=['ticker', 'longName', 'sector'])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def risk_score(values, measure: str = 'atr') -> np.ndarray:
    """Map a risk measure onto 0..100, where 100 is the calmest.

    Piecewise linear through frozen breakpoints, so the number does not move
    when the universe of the day changes — the same value comes out in the live
    chart and in a stored column.
    """
    points = ATR_BREAKPOINTS if measure == 'atr' else LOG_VOLA_BREAKPOINTS
    raw = np.asarray(pd.to_numeric(pd.Series(values), errors='coerce'), dtype=float)
    scored = np.interp(raw, points, BREAKPOINT_SCORES,
                       left=BREAKPOINT_SCORES[0], right=BREAKPOINT_SCORES[-1])
    return np.where(np.isnan(raw), np.nan, scored)


def trend_score(distance_to_high) -> np.ndarray:
    """Map the distance to the record high (<= 0) onto 0..100, 100 = at the high.

    Calibrated in absolute terms like the risk score, for the same reason: a
    percentile rank would change with the universe of the day and could not be
    reproduced in the live chart. Note what that means for a fixed threshold —
    ``trendScore >= 75`` selects fewer names in a weak market and more in a
    strong one, where a quintile cut would always return a fifth of the list.

    What the number is good for, and what it is not: it does **not** predict
    return. Over 2020-2026 the quintile with the *largest* distance to the high
    had by far the highest mean forward return (1.93 % vs 0.85 % per 21 days) —
    at the worst drawdown and the worst hit rate, and flattered by the fact that
    the universe only contains the beaten-down names that survived to today.
    What it does predict is consistency: hit rate rises and drawdown shrinks
    monotonically across all five quintiles. Treat it as a quality filter, not
    as an alpha source.
    """
    raw = np.asarray(pd.to_numeric(pd.Series(distance_to_high), errors='coerce'),
                     dtype=float)
    scored = np.interp(raw, TREND_BREAKPOINTS, TREND_SCORES,
                       left=TREND_SCORES[0], right=TREND_SCORES[-1])
    return np.where(np.isnan(raw), np.nan, scored)


def risk_bucket(frame: pd.DataFrame) -> pd.Series:
    """1..4 — the calmest profile whose cut a row still passes."""
    bucket = pd.Series(np.nan, index=frame.index)
    for name in reversed(available()):        # widest first, so the calmest wins
        preset = PROFILES[name]
        mask = _mask_for(frame, preset)
        bucket = bucket.where(~mask, preset['bucket'])
    return bucket


def score_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach ``riskScore`` and ``riskBucket`` to a frame (returns a copy).

    Works off ``atr``/``close`` where present and falls back to ``logVola``;
    both routes are calibrated to the same universe quantiles, so a frame that
    has only one of them still lands on a comparable scale.
    """
    out = frame.copy()
    if _has(out, 'atr', 'close'):
        out['atr_pct'] = pd.to_numeric(out['atr'], errors='coerce') / \
            pd.to_numeric(out['close'], errors='coerce')
        out['riskScore'] = risk_score(out['atr_pct'], measure='atr')
        if 'logVola' in out.columns:
            # fill the gaps ATR leaves (1.8 % of rows in the measured panel)
            fallback = risk_score(out['logVola'], measure='logVola')
            out['riskScore'] = out['riskScore'].where(out['riskScore'].notna(), fallback)
    elif 'logVola' in out.columns:
        out['riskScore'] = risk_score(out['logVola'], measure='logVola')
    else:
        logger.warning("risk_profile: frame carries neither atr/close nor logVola")
        out['riskScore'] = np.nan
    out['riskBucket'] = risk_bucket(out)

    if 'dist_ath' in out.columns:
        out['trendScore'] = trend_score(out['dist_ath'])
    elif _has(out, 'close', 'ath'):
        ath = pd.to_numeric(out['ath'], errors='coerce')
        close = pd.to_numeric(out['close'], errors='coerce')
        out['trendScore'] = trend_score(close / ath.where(ath > 0) - 1.0)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_profiles(calibration: dict = None) -> str:
    """The profile table as text, with the provenance of its numbers."""
    calibration = calibration or load_calibration()
    lines = [f"risk profiles — calibration {calibration.get('version')} "
             f"({calibration.get('source')})",
             f"bands are quantiles of what the cut delivered: volatility at p"
             f"{int(calibration.get('vol_quantile', VOL_BAND_QUANTILE) * 100)}, "
             f"drawdown at p{int(calibration.get('drawdown_quantile', DRAWDOWN_QUANTILE) * 100)}",
             '',
             f"{'profile':14s} {'ATR%':>6s} {'logVola':>8s} {'cover%':>7s} "
             f"{'typ.vol':>8s} {'vol band':>9s} {'max dd%':>8s} {'stop%':>6s}"]
    for name in available():
        p = resolve(name=name, calibration=calibration)
        atr = 'none' if p['max_atr_pct'] is None else f"{p['max_atr_pct'] * 100:.1f}"
        vola = 'none' if p['max_log_vola'] is None else f"{p['max_log_vola']:.4f}"
        lines.append(f"{name:14s} {atr:>6s} {vola:>8s} {p['coverage'] * 100:7.1f} "
                     f"{p['typical_vol']:8.3f} {p['vol_band']:9.3f} "
                     f"{p['drawdown_floor'] * 100:8.1f} {p['stop_loss_pct']:6.1f}")
        lines.append(f"{'':14s} {filter_expression(p)}")
    return "\n".join(lines)


def main(argv=None) -> int:
    import sys
    argv = argv or sys.argv
    args = [a.lower() for a in argv[1:]]
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if any(a in ('/help', '--help', '-h', '/?') for a in args):
        print(__doc__)
        return 0

    if any(a in ('/calibrate', '--calibrate') for a in args):
        years = []
        for arg in argv[1:]:
            if arg.lower().startswith(('/years', '--years')):
                raw = arg.split(':', 1)[-1] if ':' in arg else arg.split('=', 1)[-1]
                years = [int(y) for y in raw.replace('-', ',').split(',') if y.strip()]
        calibration = calibrate(years=years or None)
        print(format_profiles(calibration))
        print()
        for name, values in calibration['profiles'].items():
            print(f"  {name:14s} {values}")
        if any(a in ('/save', '--save') for a in args):
            save_calibration(calibration)
            print("\ncalibration stored app-wide in config.db")
        else:
            print("\n(not stored — add /save to keep it)")
        return 0

    print(format_profiles())
    return 0


if __name__ == '__main__':
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.exit(main())
