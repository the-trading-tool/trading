"""Held Scalable positions measured against the Multi-Strategies formulas.

For every position in trades.db this answers: which configured strategies even
apply to it (via its index membership), and when did each of them last say buy
or sell?

Signals come from ``portfolio_analysis._live_signal_for_ticker`` — the same live
FetchData + ``indicator.buy_sell`` path the chart uses. That matters twice over:

  * Buy/sell formulas reference live-only columns (``ovtEma9`` and friends) that
    ``asset_simulation`` does not carry, so evaluating against the simulation
    tables would silently drop conditions.
  * ``buy_sell`` markers are position-aware — a sell is only emitted while the
    strategy holds. "Last sell" therefore means "last exit signal", not "the
    formula happened to be true".

A ticker can sit in several indices, and the same strategy usually carries the
same formula across all of them; evaluations are deduplicated per distinct
(ticker, buy, sell) triple so the work does not multiply with membership.
"""
import ast
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from tradinglib import tools

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────────────────────────────────────

def index_map(db_path: str = 'database') -> dict:
    """ticker → [index names] from yf_tickers.db.

    Only real exchange indices (``^`` prefix) are returned; the other groups in
    the table are categories such as ETP or CURRENCIES, which carry no strategy.
    """
    out = {}
    try:
        from tradinglib.tools import open_db
        db_file = tools.Tools().get_path(path=db_path, file_name='yf_tickers.db')
        with open_db(db_file, readonly=True) as conn:
            rows = conn.execute(
                'SELECT s.Ticker, i.name FROM stocks s '
                'JOIN stock_indices si ON si.stock_id = s.id '
                'JOIN indices i ON i.id = si.index_id '
                "WHERE i.name LIKE '^%'").fetchall()
    except Exception as e:
        logger.warning('Index membership lookup failed: %s', e)
        return out
    for ticker, index in rows:
        out.setdefault(str(ticker), []).append(str(index))
    for names in out.values():
        names.sort()
    return out


def strategy_index_queries(username: str = 'admin') -> list:
    """Every configured (strategy, index) pair with its buy/sell formula.

    Mirrors how the agent reads ``multi_transactions``: outer keys are strategy
    names, inner dict keys are index names, and index settings override the
    strategy-level ones.
    """
    from tradinglib import system_config as sysconf

    cfg = sysconf.SystemConfig(username=username)
    try:
        raw = cfg.get_value('multi_transactions', None)
    finally:
        try:
            cfg.close()
        except Exception:
            pass   # SystemConfig.close() raises — see Db_tools.close()

    return parse_strategy_config(raw)


def parse_strategy_config(raw) -> list:
    """Turn a ``multi_transactions`` value into (strategy, index, buy, sell) rows.

    Accepts the dict or its string form (config.db stores it as text). Index
    settings override strategy-level ones, matching how the agent merges them.
    A strategy that carries ``buy`` at the top level is not index-keyed and
    stands for itself.
    """
    if isinstance(raw, str) and raw.strip():
        try:
            raw = ast.literal_eval(raw)
        except Exception as e:
            logger.warning('multi_transactions unreadable: %s', e)
            return []
    if not isinstance(raw, dict):
        return []

    pairs = []
    for strategy, scfg in raw.items():
        if not isinstance(scfg, dict):
            continue
        strat_params = {k: v for k, v in scfg.items() if not isinstance(v, dict)}
        index_cfgs = {k: v for k, v in scfg.items() if isinstance(v, dict)}
        if index_cfgs and 'buy' not in scfg:
            for index, icfg in index_cfgs.items():
                merged = {**strat_params, **icfg}
                pairs.append({'strategy': strategy, 'index': index,
                              'buy': str(merged.get('buy', '') or ''),
                              'sell': str(merged.get('sell', '') or '')})
        else:
            pairs.append({'strategy': strategy, 'index': strategy,
                          'buy': str(scfg.get('buy', '') or ''),
                          'sell': str(scfg.get('sell', '') or '')})
    return [p for p in pairs if p['buy'] or p['sell']]


def held_positions(db_path: str = 'database') -> list:
    """Open positions from trades.db as [{ticker, shares, avg_price}]."""
    from tradinglib.own_trades_analysis import _get_open_positions_for_trails
    return _get_open_positions_for_trails(db_path)


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation
# ─────────────────────────────────────────────────────────────────────────────

_QUERY_KEYWORDS = {'and', 'or', 'not', 'True', 'False', 'abs', 'min', 'max', 'np'}


def available_columns(ticker: str, queries, db_path: str = 'database',
                      username: str = 'admin', interval: str = '1d',
                      period: str = '1y') -> set:
    """Columns one live fetch actually produces, plus their lowercase aliases.

    The expression evaluator resolves ``close`` to ``Close``, so comparing a
    formula against the raw column names alone reports false gaps.
    """
    from tradinglib.portfolio_analysis import _indicators_for_queries
    from tradinglib.fetch_data import FetchData
    from tradinglib import system_config as sysconf

    joined = ' '.join(queries)
    try:
        cfg = sysconf.SystemConfig(username=username)
        fd = FetchData(database_path=db_path,
                       indicators=list(_indicators_for_queries(joined, '')),
                       sys_conf=cfg)
        df, _ = fd.fetch_data(ticker, period=period, interval=interval,
                              add_current=False, region=None)
    except Exception as e:
        logger.warning('Column probe for %s failed: %s', ticker, e)
        return set()
    if df is None or getattr(df, 'empty', True):
        return set()
    cols = set(df.columns)
    return cols | {str(c).lower() for c in cols}


def missing_columns(query: str, available: set) -> list:
    """Identifiers a formula uses that the live path does not provide.

    Only meaningful with a non-empty ``available`` set — an empty probe means
    "unknown", not "everything is missing".
    """
    if not available or not query:
        return []
    ids = set(re.findall(r'[A-Za-z_]\w*', query)) - _QUERY_KEYWORDS
    return sorted(i for i in ids
                  if i not in available and i.lower() not in available)


def applicable_pairs(tickers, pairs, memberships) -> list:
    """(ticker, strategy, index, buy, sell) for every configured combination.

    A position whose index carries no strategy simply produces no row — that is
    information too, and the caller reports it rather than hiding it.
    """
    out = []
    for ticker in tickers:
        indices = set(memberships.get(ticker, []))
        for p in pairs:
            if p['index'] in indices:
                out.append({'ticker': ticker, **p})
    return out


def evaluate(tickers=None, username: str = 'admin', db_path: str = 'database',
             interval: str = '1d', period: str = '1y',
             positions=None, max_workers: int = 6) -> tuple:
    """Run every applicable strategy formula over every held position.

    Returns ``(rows, warnings)``. Each row carries ticker, strategy, index and
    the last buy/sell dates that strategy produced.
    """
    from tradinglib.portfolio_analysis import _indicators_for_queries, _live_signal_for_ticker

    if positions is None:
        positions = held_positions(db_path)
    shares = {str(p['ticker']).upper(): float(p.get('shares', 0) or 0) for p in positions}
    if tickers is None:
        tickers = sorted(shares)
    tickers = [str(t).upper() for t in tickers if t]

    warnings = []
    pairs = strategy_index_queries(username)
    if not pairs:
        return [], ['Keine Strategien in multi_transactions konfiguriert.']

    memberships = index_map(db_path)
    combos = applicable_pairs(tickers, pairs, memberships)

    # Probe the live path once: a formula that references simulation-only columns
    # (dTrend, wkTrend) cannot be evaluated here and must say so instead of
    # rendering as "no signal".
    unsupported = {}
    if combos:
        probe = available_columns(combos[0]['ticker'],
                                 [p['buy'] for p in pairs] + [p['sell'] for p in pairs],
                                 db_path, username, interval, period)
        for p in pairs:
            gaps = sorted(set(missing_columns(p['buy'], probe))
                          | set(missing_columns(p['sell'], probe)))
            if gaps:
                unsupported[(p['strategy'], p['index'])] = gaps
        broken = sorted({s for (s, _i) in unsupported})
        if broken:
            _cols = sorted({c for g in unsupported.values() for c in g})
            warnings.append(
                'Live nicht auswertbar: ' + ', '.join(broken)
                + ' — die Formeln nutzen ' + ', '.join(_cols)
                + ', was nur in asset_simulation existiert, nicht im Live-Pfad.')

    no_strategy = [t for t in tickers if not any(c['ticker'] == t for c in combos)]
    if no_strategy:
        warnings.append(
            'Ohne passende Strategie (Index nicht konfiguriert oder keine '
            'Indexzuordnung): ' + ', '.join(sorted(no_strategy)))

    # One evaluation per distinct formula pair — strategies share formulas across
    # indices, and a ticker in two indices of one strategy is still one run.
    work = {}
    for c in combos:
        if (c['strategy'], c['index']) in unsupported:
            continue          # would only produce a misleading empty result
        work.setdefault((c['ticker'], c['buy'], c['sell']), None)

    results = {}
    if work:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(work))) as ex:
            futs = {}
            for (ticker, buy, sell) in work:
                indicators = tuple(_indicators_for_queries(buy, sell))
                futs[ex.submit(_live_signal_for_ticker, ticker, buy, sell,
                               indicators, db_path, username, interval, period)] = \
                    (ticker, buy, sell)
            for fut in as_completed(futs):
                key = futs[fut]
                try:
                    results[key] = fut.result()
                except Exception as e:
                    logger.warning('Signal für %s fehlgeschlagen: %s', key[0], e)
                    results[key] = None

    rows = []
    for c in combos:
        gaps = unsupported.get((c['strategy'], c['index']), [])
        res = None if gaps else results.get((c['ticker'], c['buy'], c['sell']))
        rows.append({
            'problem':   ('Formel nutzt ' + ', '.join(gaps) + ' (live nicht verfügbar)')
                         if gaps else '',
            'ticker':    c['ticker'],
            'shares':    shares.get(c['ticker'], 0.0),
            'index':     c['index'],
            'strategy':  c['strategy'],
            'last_buy':  (res or {}).get('last_buy'),
            'last_sell': (res or {}).get('last_sell'),
            'last_type': (res or {}).get('last_type'),
            'last_date': (res or {}).get('last_date'),
            'action':    (res or {}).get('action'),
            'computed':  res is not None,
        })

    failed = sorted({r['ticker'] for r in rows if not r['computed'] and not r['problem']})
    if failed:
        warnings.append('Nicht berechenbar (fehlende Kursdaten oder Indikatoren): '
                        + ', '.join(failed))
    return rows, warnings


_ACTION_LABEL = {'add': 'Nachkaufen', 'reduce': 'Reduzieren', 'hold': 'Halten'}
_TYPE_LABEL = {'add': 'Kauf', 'reduce': 'Verkauf', 'conflict': 'Kauf+Verkauf'}


def to_frame(rows, names: dict = None) -> pd.DataFrame:
    """Readable table, newest signal first."""
    names = names or {}
    out = []
    for r in rows:
        out.append({
            'Titel':          names.get(r['ticker'], r['ticker']),
            'Ticker':         r['ticker'],
            'Stück':          r['shares'],
            'Index':          r['index'],
            'Strategie':      r['strategy'],
            'Letztes Signal': _TYPE_LABEL.get(r['last_type'] or '', '—'),
            'am':             r['last_date'] or '',
            'letzter Kauf':   r['last_buy'] or '',
            'letzter Verkauf': r['last_sell'] or '',
            'Einschätzung':   _ACTION_LABEL.get(r['action'] or '', '—'),
            # An empty signal must be distinguishable from one that could not be
            # computed — otherwise "no signal" reads as "strategy says nothing".
            'Hinweis':        r.get('problem', ''),
        })
    df = pd.DataFrame(out)
    if df.empty:
        return df
    return df.sort_values(['am', 'Titel'], ascending=[False, True]).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

_RESULT_KEY = 'scalable_signals_result'


def _long_names(tickers, db_path: str = 'database') -> dict:
    """ticker → longName, best effort."""
    try:
        from tradinglib.portfolio_analysis import _lookup_asset_info
        info = _lookup_asset_info(list(tickers), db_path=db_path) or {}
    except Exception as e:
        logger.debug('Long-name lookup failed: %s', e)
        return {}
    out = {}
    for ticker, row in info.items():
        if isinstance(row, dict):
            name = row.get('longName') or row.get('longname') or row.get('shortName')
            if name:
                out[str(ticker)] = str(name)
    return out


def render_holdings_signals(region, db_path: str = 'database', username: str = ''):
    """Bestände gegen die konfigurierten Strategie-Formeln rechnen."""
    import streamlit as st

    r = region
    r.markdown('### Strategie-Signale der Bestände')
    r.caption(
        'Rechnet jede gehaltene Position gegen die Buy/Sell-Formeln der Strategien, '
        'die für ihren Index konfiguriert sind — über denselben Live-Pfad wie die '
        'Charts, also positionsabhängig: ein Verkaufssignal erscheint nur, solange '
        'die Strategie die Position hält. Die Berechnung lädt Kurshistorie je Titel '
        'und dauert daher etwas.'
    )

    if r.button('Signale berechnen', type='primary', key='scalable_signals_run'):
        with st.spinner('Rechne Bestände gegen die Strategie-Formeln …'):
            try:
                rows, warns = evaluate(username=username or 'admin', db_path=db_path)
                names = _long_names({row['ticker'] for row in rows}, db_path)
                st.session_state[_RESULT_KEY] = (rows, warns, names)
            except Exception as e:
                logger.exception('Holdings signal run failed')
                r.error(f'Berechnung fehlgeschlagen: {e}')
                return

    cached = st.session_state.get(_RESULT_KEY)
    if not cached:
        r.info('Noch nicht berechnet.')
        return

    rows, warns, names = cached
    for w in warns:
        r.warning(w)

    df = to_frame(rows, names)
    if df.empty:
        r.info('Keine Position lässt sich einer konfigurierten Strategie zuordnen.')
        return

    live = df[df['Hinweis'] == ''].drop(columns=['Hinweis'])
    blocked = df[df['Hinweis'] != '']

    r.caption(f'{len(live)} von {len(df)} Kombinationen live auswertbar.')
    r.dataframe(live, hide_index=True, use_container_width=True)

    if not blocked.empty:
        with r.expander(f'Nicht auswertbar ({len(blocked)})'):
            r.caption(
                'Diese Strategien nutzen Spalten, die nur die Simulation kennt. '
                'Sie liefern hier bewusst kein Signal, statt ein leeres vorzutäuschen.'
            )
            r.dataframe(blocked[['Titel', 'Index', 'Strategie', 'Hinweis']],
                        hide_index=True, use_container_width=True)
