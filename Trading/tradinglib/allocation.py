"""Propose an `invest` budget per (strategy x index) from slot count and market volatility.

The rule is deliberately small and stated in full, because the numbers it
produces move real money and you should be able to re-derive every one of them
by hand:

    weight = num_assets / volatility(index)

Both halves matter. **Slots** because capital has to scale with the number of
positions an index may hold at once -- giving a 6-slot index the same budget as
a 3-slot one halves its position size for no stated reason. **Inverse
volatility** because a slot in a jumpy market carries more risk per euro than
one in a calm market; dividing by volatility equalises the risk contribution
rather than the capital. That is the same idea the simulator already applies
*within* an index (asset_simulator.buy_asset weights by `avg_vola / asset_vola`);
this module applies it one level up, *between* markets.

What it deliberately does NOT do: judge whether a strategy is any good, look at
past returns, or tilt towards what worked. Only slot count and volatility go in.
Expected return is the part nobody can measure reliably, so it stays out.

Two guards come from measurements in this codebase:

* **Minimum slot size.** A Support/Resistance slot of 200 EUR on ^SSMI could not
  buy a single share of half that index (median share price 200 EUR, the dearest
  3,442 EUR). `min_slot` sets a floor, and `min_shares` can raise it further from
  the actual share prices.
* **Per-ticker MAX(Date).** Share prices are read at each ticker's own latest
  row, never at the table's global maximum -- the newest day in
  `asset_simulation` is usually only partly filled, and anchoring on it silently
  drops most of the index.

All database access is read-only (`mode=ro`). Nothing here writes, and nothing
goes through `fetch_data.load_price_data`, which would fall back to Yahoo and
persist what it fetched.
"""
from __future__ import annotations

import logging
import math
import os
import sqlite3

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TRADING_DAYS = 252
DEFAULT_YEARS = 1.0
MIN_VOLA_DAYS = 60          # below this a volatility estimate is not worth having


# ---------------------------------------------------------------- data access

def _db_dir(db_path: str = 'database') -> str:
    """Resolve the database directory the same way the app does."""
    env = os.environ.get('TradingDB')
    if env:
        return env
    if os.path.isabs(db_path):
        return db_path
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), db_path)


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect('file:' + path + '?mode=ro', uri=True)


def index_volatility(index: str, db_path: str = 'database',
                     years: float = DEFAULT_YEARS) -> tuple[float | None, int]:
    """Annualised standard deviation of the index's daily log returns.

    Returns ``(vola, days_used)``; ``(None, 0)`` when there is no local series or
    too little of it. The window is the last ``years`` of calendar history that
    the local file actually holds -- ^SSMI only starts in 2023, and truncating
    every market to the shortest series would throw away information for no gain.
    """
    path = os.path.join(_db_dir(db_path), f'yf_{index}.db')
    if not os.path.exists(path):
        logger.debug('allocation: no local series for %s', index)
        return None, 0
    try:
        conn = _ro(path)
        try:
            # Alias, weil day_data die Spalte gross schreibt (Close) und
            # asset_simulation klein (close). SQLite vergleicht Spaltennamen
            # case-insensitiv, der zurueckgegebene Name folgt aber dem SELECT.
            df = pd.read_sql_query(
                'SELECT Date, Close AS close FROM day_data ORDER BY Date', conn)
        finally:
            conn.close()
    except (sqlite3.Error, pd.errors.DatabaseError) as e:
        logger.debug('allocation: %s unreadable (%s)', index, e)
        return None, 0

    if df.empty:
        return None, 0
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df = df.dropna(subset=['Date', 'close'])
    df = df[df['close'] > 0]
    if len(df) < 2:
        return None, 0

    cutoff = df['Date'].max() - pd.Timedelta(days=int(round(365.25 * years)))
    win = df[df['Date'] >= cutoff]
    if len(win) < MIN_VOLA_DAYS:
        win = df.tail(MIN_VOLA_DAYS)          # kurze Reihe: nimm, was da ist
    if len(win) < MIN_VOLA_DAYS:
        return None, len(win)

    rets = np.log(win['close'].to_numpy()[1:] / win['close'].to_numpy()[:-1])
    rets = rets[np.isfinite(rets)]
    if len(rets) < MIN_VOLA_DAYS - 1:
        return None, len(rets) + 1
    vola = float(np.std(rets, ddof=1) * math.sqrt(TRADING_DAYS))
    return (vola if vola > 0 else None), len(win)


def median_share_price(index: str, db_path: str = 'database',
                       system_currency: str = 'EUR') -> float | None:
    """Median share price of the index members, in *system_currency*.

    Each ticker contributes its own most recent row. Anchoring on the table-wide
    MAX(Date) instead would look plausible and be wrong: that day is typically
    filled for a handful of tickers only.
    """
    d = _db_dir(db_path)
    try:
        tk = _ro(os.path.join(d, 'yf_tickers.db'))
        try:
            members = pd.read_sql_query(
                'SELECT s.Ticker AS ticker FROM stocks s '
                'JOIN stock_indices si ON s.id = si.stock_id '
                'JOIN indices i ON i.id = si.index_id WHERE i.name = ?',
                tk, params=(index,))['ticker'].tolist()
        finally:
            tk.close()
        if not members:
            return None

        sim = _ro(os.path.join(d, 'asset_simulation_.db'))
        try:
            q = ','.join('?' * len(members))
            px = pd.read_sql_query(
                f'SELECT a.ticker, a.close FROM asset_simulation a '
                f'JOIN (SELECT ticker, MAX(Date) md FROM asset_simulation '
                f'      WHERE ticker IN ({q}) GROUP BY ticker) l '
                f'  ON a.ticker = l.ticker AND a.Date = l.md',
                sim, params=members)
        finally:
            sim.close()
        if px.empty:
            return None

        info = _ro(os.path.join(d, 'asset_info.db'))
        try:
            cur = pd.read_sql_query('SELECT ticker, currency FROM asset_info', info)
        finally:
            info.close()
    except (sqlite3.Error, pd.errors.DatabaseError) as e:
        logger.debug('allocation: share prices for %s unavailable (%s)', index, e)
        return None

    px = px.merge(cur, on='ticker', how='left')
    px['close'] = pd.to_numeric(px['close'], errors='coerce')
    px = px.dropna(subset=['close'])
    px = px[px['close'] > 0]
    if px.empty:
        return None

    vals = [_to_system_currency(r.close, r.currency, system_currency)
            for r in px.itertuples()]
    vals = [v for v in vals if v is not None and v > 0]
    return float(np.median(vals)) if vals else None


_RATE_CACHE: dict = {}


def _to_system_currency(value: float, currency, system_currency: str = 'EUR'):
    """Convert a listing-currency price into the system currency.

    DataUtils.get_exchange_rate returns units of *currency* per system-currency
    unit, so this DIVIDES. Multiplying is the natural-looking mistake and lands
    Japanese shares at six-figure euro prices. It also handles the GBp pence
    quotes on their own, because asset_info stores 'GBp' as its own code.
    """
    cur = (currency or system_currency)
    if cur == system_currency:
        return value
    if cur not in _RATE_CACHE:
        try:
            from tradinglib.utils import DataUtils
            _RATE_CACHE[cur] = float(DataUtils.get_exchange_rate(cur, system_currency))
        except Exception:
            _RATE_CACHE[cur] = None
    rate = _RATE_CACHE[cur]
    if not rate:
        return None
    return value / rate


# ------------------------------------------------------------- the arithmetic

def raw_weights(pairs: list, volas: dict) -> dict:
    """weight = num_assets / volatility, normalised to 1.

    *pairs* is a list of ``(strategy, index, num_assets)``. Pairs whose index has
    no usable volatility fall back to the median of the others -- dropping them
    would silently remove a market from the portfolio, which is a bigger error
    than an approximate weight.
    """
    known = [v for v in volas.values() if v]
    fallback = float(np.median(known)) if known else 1.0
    w = {}
    for strat, idx, slots in pairs:
        v = volas.get(idx) or fallback
        w[(strat, idx)] = float(slots) / v
    total = sum(w.values())
    if total <= 0:
        raise ValueError('Alle Gewichte sind 0 — pruefe num_assets und Volatilitaet.')
    return {k: v / total for k, v in w.items()}


def apply_floor(weights: dict, budget: float, floors: dict) -> dict:
    """Distribute *budget* by *weights*, but never below the per-pair *floors*.

    Water-filling: pairs that fall short are pinned to their floor, the rest
    share what is left, repeat until nothing new falls short. Without this a
    small weight produces a slot that cannot buy a single share.
    """
    need = sum(floors.get(k, 0.0) for k in weights)
    if need > budget + 1e-9:
        raise ValueError(
            f'Die Mindestgroessen verlangen {need:,.0f} EUR, das Budget ist '
            f'{budget:,.0f} EUR. Weniger Slots, kleineres min_slot oder mehr Budget.')

    pinned: dict = {}
    free = dict(weights)
    while True:
        rest = budget - sum(pinned.values())
        wsum = sum(free.values())
        if wsum <= 0:
            break
        short = {k: floors.get(k, 0.0) for k in free
                 if rest * free[k] / wsum < floors.get(k, 0.0) - 1e-9}
        if not short:
            for k, w in free.items():
                pinned[k] = rest * w / wsum
            break
        for k, f in short.items():
            pinned[k] = f
            free.pop(k)
    return pinned


def round_to_step(values: dict, budget: float, step: int = 100,
                  floors: dict | None = None) -> dict:
    """Round to whole *step* units so the total still hits *budget* exactly.

    Largest remainder: floor everything, hand the leftover steps to the largest
    fractions. A plain round() would miss the budget by a few hundred euro, and
    that difference is real money sitting unused.
    """
    floors = floors or {}
    if step <= 0:
        return dict(values)
    units_total = int(round(budget / step))
    base = {k: int(math.floor(v / step)) for k, v in values.items()}
    rest = units_total - sum(base.values())
    frac = sorted(values, key=lambda k: (values[k] / step - base[k]), reverse=True)
    i = 0
    while rest > 0 and frac:
        base[frac[i % len(frac)]] += 1
        rest -= 1
        i += 1
    # Zu viel verteilt (kann bei groben Schritten passieren): dort abziehen, wo
    # noch Luft ueber der Mindestgroesse ist.
    while rest < 0:
        for k in sorted(base, key=lambda k: values[k], reverse=True):
            if (base[k] - 1) * step >= floors.get(k, 0.0) and base[k] > 0:
                base[k] -= 1
                rest += 1
                break
        else:
            break
    return {k: v * step for k, v in base.items()}


# ------------------------------------------------------------------ front end

def propose_allocation(transactions: dict, budget: float = 100_000.0, *,
                       min_slot: float = 0.0, min_shares: float = 0.0,
                       step: int = 100, years: float = DEFAULT_YEARS,
                       db_path: str = 'database', system_currency: str = 'EUR',
                       volas: dict | None = None) -> pd.DataFrame:
    """Return one row per (strategy x index) with the proposed `invest`.

    Columns: strategy, index, num_assets, vola, vola_days, weight, invest,
    slot (invest/num_assets), median_price, shares_per_slot, floor.

    *min_shares* > 0 raises each floor to ``min_shares x median share price`` of
    that index, so a slot can actually buy something. It costs one query per
    index; leave it at 0 to skip that entirely.
    """
    pairs = [(s, i, int(cfg.get('num_assets', 0) or 0))
             for s, idxs in transactions.items()
             for i, cfg in idxs.items()]
    if not pairs:
        raise ValueError('Keine (Strategie x Index)-Paare in den Transaktionen.')

    indices = sorted({i for _, i, _ in pairs})
    vola_days: dict = {}
    if volas is None:
        volas = {}
        for ix in indices:
            v, n = index_volatility(ix, db_path=db_path, years=years)
            volas[ix] = v
            vola_days[ix] = n
            if v is None:
                logger.warning('allocation: keine Volatilitaet fuer %s (%d Tage) '
                               '— Median der uebrigen wird verwendet', ix, n)

    prices: dict = {}
    if min_shares > 0:
        for ix in indices:
            prices[ix] = median_share_price(ix, db_path=db_path,
                                            system_currency=system_currency)

    weights = raw_weights(pairs, volas)
    floors = {}
    for s, i, slots in pairs:
        per_slot = float(min_slot)
        if min_shares > 0 and prices.get(i):
            per_slot = max(per_slot, min_shares * prices[i])
        floors[(s, i)] = per_slot * slots

    exact = apply_floor(weights, float(budget), floors)
    final = round_to_step(exact, float(budget), step=step, floors=floors)

    known = [v for v in volas.values() if v]
    fallback = float(np.median(known)) if known else None
    rows = []
    for s, i, slots in pairs:
        inv = final[(s, i)]
        slot = inv / slots if slots else float('nan')
        px = prices.get(i)
        rows.append({
            'strategy': s, 'index': i, 'num_assets': slots,
            'vola': volas.get(i) if volas.get(i) else fallback,
            'vola_est': 'gemessen' if volas.get(i) else 'Median (Ersatz)',
            'vola_days': vola_days.get(i, 0),
            'weight': weights[(s, i)],
            'invest': inv, 'slot': slot,
            'median_price': px,
            'shares_per_slot': (slot / px) if px else None,
            'floor': floors[(s, i)],
        })
    df = pd.DataFrame(rows).sort_values(['strategy', 'invest'],
                                        ascending=[True, False])
    return df.reset_index(drop=True)


def apply_allocation(transactions: dict, frame: pd.DataFrame) -> dict:
    """Copy *transactions* with `invest` replaced by the proposal. Nothing else."""
    import copy
    out = copy.deepcopy(transactions)
    # to_dict statt itertuples: 'index' ist als Attributname bei itertuples
    # belegt (der DataFrame-Index) und wuerde still den falschen Wert liefern.
    for r in frame.to_dict('records'):
        s, i = r['strategy'], r['index']
        if s in out and i in out[s]:
            out[s][i]['invest'] = int(r['invest'])
    return out


def format_transactions(transactions: dict, indent: str = '  ') -> str:
    """Pretty-print the dict so it can be pasted back into the configuration."""
    lines = ['{']
    for s, idxs in transactions.items():
        lines.append(f"{indent}{s!r}: {{")
        for i, cfg in idxs.items():
            lines.append(f"{indent*2}{i!r}: {{")
            for k, v in cfg.items():
                lines.append(f"{indent*3}{k!r}: {v!r},")
            lines.append(f"{indent*2}}},")
        lines.append(f"{indent}}},")
    lines.append('}')
    return '\n'.join(lines)


def load_transactions(username: str = 'kurt', db_path: str = 'database') -> dict:
    """Read the stored multi_transactions of *username* from config.db (read-only)."""
    import ast
    conn = _ro(os.path.join(_db_dir(db_path), 'config.db'))
    try:
        row = conn.execute('SELECT value FROM config WHERE key = ?',
                           (f'{username}:multi_transactions',)).fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError(f'Kein Eintrag {username}:multi_transactions in config.db')
    cfg = ast.literal_eval(row[0])
    while isinstance(cfg, str):          # doppelt serialisiert vorgefunden
        cfg = ast.literal_eval(cfg)
    return cfg


def _parse_flags(argv: list) -> dict:
    """Parse this module's own ``/key:value`` flags.

    Deliberately NOT cli.parse_args: that parser matches keys in an explicit
    if-chain, so a flag it does not know is dropped without a word and the CLI
    silently runs on defaults. Six allocation-only switches do not belong in a
    parser shared by every script either.
    """
    out = {}
    for a in argv[1:]:
        if not a.startswith('/'):
            continue
        raw = a[1:]
        key, _, val = raw.partition(':')
        out[key.lower()] = val if val != '' else True
    return out


def _main(argv=None) -> int:
    """CLI: python -m tradinglib.allocation [/budget:100000] [/min_slot:500] ...

    Schalter: /budget /min_slot /min_shares /step /years /user /dict

    **Unter PowerShell aufrufen.** In Git Bash schreibt MSYS ein Flag OHNE
    Doppelpunkt in einen Windows-Pfad um, `/dict` kommt dann nie an und die
    Ausgabe fehlt kommentarlos. Flags mit Wert (`/budget:100000`) sind davon
    nicht betroffen.
    """
    import sys

    argv = argv if argv is not None else sys.argv
    args = _parse_flags(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

    known = {'budget', 'min_slot', 'min_shares', 'step', 'years', 'user', 'dict'}
    for k in set(args) - known:
        logger.warning('unbekannter Schalter /%s — ignoriert. Bekannt: %s',
                       k, ', '.join(sorted(known)))

    def num(key, default):
        v = args.get(key, None)
        try:
            return float(v) if v not in (None, '', True) else float(default)
        except (TypeError, ValueError):
            logger.warning('/%s=%r ist keine Zahl — nehme %s', key, v, default)
            return float(default)

    budget = num('budget', 100_000)
    min_slot = num('min_slot', 0)
    min_shares = num('min_shares', 0)
    step = int(num('step', 100))
    years = num('years', DEFAULT_YEARS)
    user = args.get('user') if isinstance(args.get('user'), str) else 'kurt'

    trans = load_transactions(user)
    df = propose_allocation(trans, budget, min_slot=min_slot,
                            min_shares=min_shares, step=step, years=years)

    show = df.copy()
    show['vola'] = (show['vola'] * 100).round(1)
    for c in ('invest', 'slot', 'floor', 'median_price'):
        show[c] = show[c].astype(float).round(0)
    show['weight'] = (show['weight'] * 100).round(2)
    show['shares_per_slot'] = show['shares_per_slot'].astype(float).round(1)
    print(show.to_string(index=False))
    print(f'\nSumme: {df["invest"].sum():,.0f} EUR von {budget:,.0f} EUR'
          f'   Slots: {int(df["num_assets"].sum())}')

    if args.get('dict', False):
        print('\n' + format_transactions(apply_allocation(trans, df)))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
