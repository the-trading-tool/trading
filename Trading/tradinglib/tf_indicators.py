"""
Lazy, cache-aware multi-timeframe indicator utilities.

API highlights:
- get_indicator(df, indicator_name, timeframe='D', start=None, end=None, params={})
  computes the requested indicator on the requested timeframe and returns a Series or
  DataFrame. Results are cached per (ticker, timeframe, indicator, params, source).

Plugin contract (tradinglib.indicator.<name>): expose a function named `compute(df, **params)`
which accepts a DataFrame (with Date index or a 'Date' column) and returns a Series or
DataFrame aligned to the input index (resampled timeframe index when used).

This module also contains a few built-in, vectorized indicators (ma, atr, rsi, macd)
so you can use them without adding plugins.
"""
from __future__ import annotations

import hashlib
import logging
import pkgutil
import importlib
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Simple in-memory caches
_TF_CACHE: Dict[str, pd.DataFrame] = {}
_PLUGIN_REGISTRY: Dict[str, Any] = {}


def _fingerprint_params(params: Dict[str, Any]) -> str:
    """Return a short SHA-1 hex string that uniquely identifies a params dict."""
    s = repr(sorted(params.items()))
    return hashlib.sha1(s.encode()).hexdigest()


def _source_fingerprint(df: pd.DataFrame) -> str:
    """Return a cache key string derived from the DataFrame length and last date value."""
    if df is None or df.empty:
        return 'empty'
    last = df['Date'].max() if 'Date' in df else df.index.max()
    return f"{len(df)}_{pd.Timestamp(last).value}"


def _ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure df has a DatetimeIndex; converts a 'Date' column if needed."""
    df = df.copy()
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.set_index('Date')
    else:
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError('DataFrame must have a Date column or a DatetimeIndex')
    df = df.sort_index()
    return df


def resample_to_tf(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample an OHLCV-like dataframe to the requested timeframe.

    timeframe: 'D', 'W', 'M' or a pandas-compatible rule.
    """
    df = _ensure_datetime_index(df)
    rule = timeframe if timeframe in ('D', 'W', 'M') else timeframe
    agg = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }
    # Keep any other columns by taking last observed value in the period
    other_cols = [c for c in df.columns if c not in agg]
    res = df.resample(rule).agg(agg)
    if other_cols:
        res_other = df[other_cols].resample(rule).last()
        res = res.join(res_other)
    res = res.dropna(subset=['close'], how='all')
    res = res.reset_index()
    return res


def _compute_builtin_indicator(df_tf: pd.DataFrame, name: str, params: Dict[str, Any]):
    """Compute a built-in indicator (ma, ema, atr, rsi, macd) and return a DataFrame."""
    df = df_tf.copy()
    s = df['close'].astype('float64')
    name = name.lower()
    # normalize ema token alias
    if name == 'ema':
        # handle in dedicated branch below
        pass
    if name.startswith('ma') or name == 'ma':
        # params: window or windows
        windows = params.get('windows') or params.get('window') or params.get('w')
        if windows is None:
            windows = [20]
        if isinstance(windows, int):
            windows = [windows]
        out = pd.DataFrame(index=df.index)
        for w in windows:
            out[f'ma{w}'] = s.rolling(w, min_periods=1).mean().astype('float32')
        return out

    if name.startswith('ema') or name == 'ema':
        windows = params.get('windows') or params.get('window') or params.get('w')
        if windows is None:
            windows = [9]
        if isinstance(windows, int):
            windows = [windows]
        out = pd.DataFrame(index=df.index)
        for w in windows:
            out[f'ema{w}'] = s.ewm(span=int(w), adjust=False).mean().astype('float32')
        return out

    if name == 'atr':
        tr1 = df['high'] - df['low']
        tr2 = (df['high'] - df['close'].shift()).abs()
        tr3 = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        windows = params.get('windows') or params.get('window') or [14]
        if isinstance(windows, int):
            windows = [windows]
        out = pd.DataFrame(index=df.index)
        for w in windows:
            out[f'atr{w}'] = tr.rolling(w, min_periods=1).mean().astype('float32')
        return out

    if name == 'rsi':
        delta = s.diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        windows = params.get('windows') or params.get('window') or [14]
        if isinstance(windows, int):
            windows = [windows]
        out = pd.DataFrame(index=df.index)
        for w in windows:
            ma_up = up.ewm(alpha=1 / w, adjust=False).mean()
            ma_down = down.ewm(alpha=1 / w, adjust=False).mean()
            rs = ma_up / ma_down.replace(0, np.nan)
            out[f'rsi{w}'] = (100 - (100 / (1 + rs))).astype('float32')
        return out

    if name == 'macd':
        p1 = params.get('p1', 12)
        p2 = params.get('p2', 26)
        sig = params.get('sig', 9)
        ema1 = s.ewm(span=p1, adjust=False).mean()
        ema2 = s.ewm(span=p2, adjust=False).mean()
        macd = ema1 - ema2
        macd_sig = macd.ewm(span=sig, adjust=False).mean()
        out = pd.DataFrame(index=df.index)
        out['macd'] = macd.astype('float32')
        out['macd_sig'] = macd_sig.astype('float32')
        return out

    raise KeyError(f'Built-in indicator {name} not found')


def _compute_ema_of_series(series: pd.Series, window: int):
    """Compute a standard EMA using the given span and return a float32 Series."""
    return series.ewm(span=window, adjust=False).mean().astype('float32')


def parse_indicator_token(token: str, default_tf: str = 'D'):
    """Parse tokens like:
    - 'ema21' -> ('ema', {'window':21}, 'D', output_name='ema21')
    - 'ma20@W' -> ('ma', {'window':20}, 'W', 'ma20@W')
    - 'ewoEma21' -> ('ema', {'window':21, 'base_col':'ewo'}, 'D', 'ewoEma21')

    Returns: (indicator_name, params_dict, timeframe, output_token_name)
    """
    tf = default_tf
    t = token
    # timeframe via @tf or suffix _D/_W/_M
    if '@' in t:
        t, tf = t.split('@', 1)
        tf = tf.upper()
    elif '_' in t and t.rsplit('_', 1)[-1].upper() in ('D', 'W', 'M'):
        t, maybe = t.rsplit('_', 1)
        tf = maybe.upper()

    # camelCase nested e.g. ewoEma21
    m = None
    import re
    camel = re.match(r'(?P<prefix>[a-z]+)(?P<rest>[A-Z].*)', t)
    if camel:
        prefix = camel.group('prefix')
        rest = camel.group('rest')
        # rest may be Ema21 -> indicator letters + digits
        m2 = re.match(r'(?P<ind>[A-Za-z]+)(?P<num>\d+)?', rest)
        if m2:
            ind = m2.group('ind').lower()
            num = m2.group('num')
            params = {}
            if num:
                params['window'] = int(num)
            params['base_col'] = prefix
            return ind, params, tf, token

    # simple letter+digits e.g. ema21 or ma50
    m = re.match(r'(?P<ind>[A-Za-z]+)(?P<num>\d+)$', t)
    if m:
        ind = m.group('ind').lower()
        num = int(m.group('num'))
        return ind, {'window': num}, tf, token

    # fallback: whole token as indicator name
    return t.lower(), {}, tf, token


def _discover_plugins(package_name: str = 'tradinglib.indicator'):
    """Discover and import plugins under tradinglib.indicator.*

    Each plugin should expose a `compute(df, **params)` function.
    """
    try:
        package = importlib.import_module(package_name)
    except Exception:
        logger.debug('No plugin package %s found', package_name)
        return {}

    discovered = {}
    for finder, name, ispkg in pkgutil.iter_modules(package.__path__):
        full = f"{package_name}.{name}"
        try:
            mod = importlib.import_module(full)

            # Build a compute wrapper that supports several legacy styles
            def make_wrapper(module, mod_name):
                # 1) module.compute(df, **params)
                if hasattr(module, 'compute') and callable(getattr(module, 'compute')):
                    return lambda df, **p: module.compute(df, **p)

                # 2) class with capitalized name e.g. Ewo
                class_name = mod_name.capitalize()
                if hasattr(module, class_name):
                    cls = getattr(module, class_name)

                    def wrapper(df, **p):
                        # try different ctor signatures
                        inst = None
                        try:
                            inst = cls(df=df.copy(), symbol=p.get('symbol', ''))
                        except Exception:
                            try:
                                inst = cls(df.copy(), p.get('symbol', ''))
                            except Exception:
                                inst = cls(df.copy())
                        # call data() if exists
                        try:
                            if hasattr(inst, 'data') and callable(inst.data):
                                inst.data()
                        except Exception:
                            logger.exception('Error running data() for plugin %s', mod_name)
                        if hasattr(inst, 'df'):
                            return inst.df
                        # fallback: return empty
                        return pd.DataFrame()

                    return wrapper

                # 3) search for compute_* functions
                for attr in dir(module):
                    if attr.startswith('compute_') and callable(getattr(module, attr)):
                        return lambda df, **p: getattr(module, attr)(df, **p)

                return None

            wrapper = make_wrapper(mod, name)
            if wrapper is not None:
                discovered[name] = {'compute': wrapper, 'module': mod}
                logger.debug('Registered indicator plugin/adaptor: %s', name)
            else:
                logger.debug('Module %s skipped (no compute/Adaptor)', full)
        except Exception as e:
            logger.exception('Error importing plugin %s: %s', full, e)
    return discovered


def _ensure_plugins_loaded():
    """Lazily populate _PLUGIN_REGISTRY by discovering tradinglib.indicator plugins."""
    global _PLUGIN_REGISTRY
    if not _PLUGIN_REGISTRY:
        _PLUGIN_REGISTRY.update(_discover_plugins())


def get_indicator(df: pd.DataFrame,
                  indicator_name: str,
                  timeframe: str = 'D',
                  start: Optional[pd.Timestamp] = None,
                  end: Optional[pd.Timestamp] = None,
                  params: Optional[Dict[str, Any]] = None,
                  price_col: str = 'close') -> pd.DataFrame:
    """Compute an indicator over an arbitrary time range and timeframe.

    Returns a DataFrame (or Series) aligned to the resampled timeframe index.
    Caching key uses ticker (if present), timeframe, indicator, params and source fingerprint.
    """
    params = params or {}
    if df is None or df.empty:
        return pd.DataFrame()

    # optional slicing
    df_local = df.copy()
    if 'Date' in df_local.columns:
        df_local['Date'] = pd.to_datetime(df_local['Date'])
        if start is not None:
            df_local = df_local[df_local['Date'] >= pd.to_datetime(start)]
        if end is not None:
            df_local = df_local[df_local['Date'] <= pd.to_datetime(end)]

    ticker = None
    if 'ticker' in df_local.columns and not df_local['ticker'].empty:
        ticker = str(df_local['ticker'].iat[0])

    params_fp = _fingerprint_params(params)
    src_fp = _source_fingerprint(df_local)
    key = f"{ticker}|{timeframe}|{indicator_name}|{params_fp}|{src_fp}|{start}|{end}"
    if key in _TF_CACHE:
        return _TF_CACHE[key]

    # Resample if needed
    if timeframe == 'D':
        df_tf = df_local.copy()
        if 'Date' in df_tf.columns:
            df_tf = df_tf.reset_index(drop=True)
    else:
        df_tf = resample_to_tf(df_local, timeframe)

    # Ensure price_col exists
    if price_col not in df_tf.columns:
        raise KeyError(f"Price column '{price_col}' not in dataframe for indicator {indicator_name}")

    # Normalize columns to support legacy plugins that expect 'Close'/'Open' capitalized
    # Provide both lowercase and Title-case variants to the plugin wrapper
    try:
        df_tf_norm = df_tf.copy()
        for c in list(df_tf.columns):
            # if original is lower, add Title, and vice versa
            try:
                if c.lower() not in df_tf_norm.columns:
                    df_tf_norm[c.lower()] = df_tf[c]
                if c.capitalize() not in df_tf_norm.columns:
                    df_tf_norm[c.capitalize()] = df_tf[c]
            except Exception:
                pass
    except Exception:
        df_tf_norm = df_tf.copy()

    # Allow indicator_name to be a token like 'ema21' or 'ewoEma21'. Parse it.
    ind_name, parsed_params, parsed_tf, out_token = parse_indicator_token(indicator_name, default_tf=timeframe)
    # if token specified a different timeframe, honour it
    if parsed_tf and parsed_tf != timeframe:
        # redo resample with parsed tf
        df_tf = resample_to_tf(df_local, parsed_tf)
    # merge params
    merged_params = dict(params or {})
    merged_params.update(parsed_params or {})

    # Built-ins first
    try:
        # support ema/ma on custom base_col
        if ind_name in ('ema', 'ma') and 'base_col' in merged_params:
            base = merged_params.pop('base_col')
            if base not in df_tf.columns:
                # try to compute base indicator first (recursive call)
                try:
                    base_df = get_indicator(df_local, base, timeframe=parsed_tf, params={}, price_col=price_col)
                    if not base_df.empty:
                        # merge base column into df_tf on Date
                        if 'Date' in base_df.columns:
                            # ensure df_tf has Date column
                            if 'Date' not in df_tf.columns:
                                df_tf = df_tf.reset_index()
                            df_tf = pd.merge(df_tf, base_df, on='Date', how='left')
                        else:
                            # fallback: concat by position
                            df_tf = pd.concat([df_tf.reset_index(drop=True), base_df.reset_index(drop=True)], axis=1)
                except Exception:
                    logger.exception('Failed to compute base indicator %s for %s', base, indicator_name)
            if base not in df_tf.columns:
                raise KeyError(f"base_col '{base}' not found in data for indicator {indicator_name}")
            series = df_tf[base].astype('float64')
            win = merged_params.get('window') or merged_params.get('w') or 14
            if ind_name == 'ema':
                out_ser = _compute_ema_of_series(series, int(win))
            else:
                out_ser = series.rolling(int(win), min_periods=1).mean().astype('float32')
            out = out_ser.to_frame(name=out_token)
        else:
            out = _compute_builtin_indicator(df_tf, ind_name, merged_params)
    except KeyError:
        # plugin
        _ensure_plugins_loaded()
        if ind_name in _PLUGIN_REGISTRY:
            try:
                entry = _PLUGIN_REGISTRY[ind_name]
                func = entry['compute'] if isinstance(entry, dict) and 'compute' in entry else entry
                # pass normalized dataframe to plugins so they can use 'Close' or 'close'
                out = func(df_tf_norm.copy(), **merged_params)
            except Exception:
                logger.exception('Error computing plugin indicator %s', ind_name)
                out = pd.DataFrame()
        else:
            raise KeyError(f'Indicator {indicator_name} not found as builtin or plugin')

    # Normalize output: ensure DataFrame with Date column (resampled index)
    if isinstance(out, pd.Series):
        out = out.to_frame()
    if not out.empty:
        # attach Date column like resample_to_tf returns
        if 'Date' not in out.columns:
            out = out.reset_index()
            # try to find a datetime-like column and rename to 'Date'
            for col in out.columns:
                if np.issubdtype(out[col].dtype, np.datetime64):
                    out = out.rename(columns={col: 'Date'})
                    break
            else:
                # fallback: if df_tf has Date column and lengths match, copy it
                try:
                    if 'Date' in df_tf.columns and len(out) == len(df_tf):
                        out['Date'] = df_tf['Date'].values
                except Exception:
                    pass
    _TF_CACHE[key] = out
    return out


def clear_cache():
    """Clear the in-memory indicator result cache (useful in tests or after schema changes)."""
    _TF_CACHE.clear()

