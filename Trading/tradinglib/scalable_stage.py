"""Headless order staging: signals → order basket → push to the phone.

Runs without Streamlit so the scheduler can drive it. The chain is:

    signals / breached stops  →  OrderDraft  →  scalable_orders.db  →  Pushover

The push carries the values to enter and a deep link into Scalable's own trade
dialog. **Nothing here places an order.** The link opens the dialog; quantity,
order type and limit are typed there and confirmed by you. That is also why the
values travel in the message text: the deep link accepts instrument and side
only — extra query parameters such as ``&quantity=1`` are ignored by Scalable.

CLI:
    python -m tradinglib.scalable_stage /dry-run
    python -m tradinglib.scalable_stage /buys /stops
    python -m tradinglib.scalable_stage /test-push
    python -m tradinglib.scalable_stage /buys /no-push /user:admin
"""
import logging
import sys
from datetime import datetime

from tradinglib import tools
from tradinglib.scalable_orders import (OrderBasket, STATUS_OPEN, broker_handoff_url,
                                        default_buffer_pct, drafts_from_agent_signals,
                                        drafts_from_position_sells, isin_map_from_trades,
                                        _dec)

logger = logging.getLogger(__name__)

PUSH_TITLE = 'Order vorbereitet'
PUSH_ACTION = 'Bei Scalable stellen'


# ─────────────────────────────────────────────────────────────────────────────
# Sources
# ─────────────────────────────────────────────────────────────────────────────

def _fill_longnames(signals, db_path: str = 'database') -> None:
    """Add the plain company name where the signal only carries a ticker.

    The push is read on a phone: "Siemens Energy" is recognisable at a glance,
    "ENR.DE" is not. Missing names are left alone rather than guessed.
    """
    wanted = [str(s.get('ticker', '')).strip() for s in signals
              if not s.get('longname') and s.get('ticker')]
    if not wanted:
        return
    names = {}
    try:
        from tradinglib.tools import open_db
        db_file = tools.Tools().get_path(path=db_path, file_name='asset_info.db')
        marks = ','.join('?' * len(wanted))
        with open_db(db_file, readonly=True) as conn:
            for ticker, longname in conn.execute(
                    f'SELECT ticker, longName FROM asset_info WHERE ticker IN ({marks})',
                    wanted).fetchall():
                if longname:
                    names[str(ticker)] = str(longname)
    except Exception as e:
        logger.debug('Staging: long-name lookup failed: %s', e)
        return
    for s in signals:
        if not s.get('longname'):
            s['longname'] = names.get(str(s.get('ticker', '')), '')


def collect_buy_drafts(username: str = 'admin', db_path: str = 'database',
                       index_filter=None) -> tuple:
    """Buy drafts from the trading agent's own signal path.

    Deliberately reuses ``SignalEvaluator`` and ``_size_signals`` rather than
    re-deriving anything: the staged quantity has to be the one the Signals tab
    and the live agent would use, or the basket quietly diverges from them.
    """
    from tradinglib import system_config as sysconf
    from tradinglib.premium.trading_agent import TradingAgent, _size_signals
    from tradinglib.premium.trading_bridge import OrderLog, SignalEvaluator

    cfg = sysconf.SystemConfig(username=username)
    # The agent object is used only for its read-only config helpers; no broker
    # is created and nothing is ordered.
    agent = TradingAgent(sys_config=cfg, order_log=OrderLog(db_path=db_path),
                         db_path=db_path)
    strategies = agent._enabled_strategies()
    if not strategies:
        return [], [('—', 'keine aktiven Strategien konfiguriert')]

    system_ccy = cfg.get_value('system_currency', 'EUR') or 'EUR'
    evaluator = SignalEvaluator(username=username, db_path=db_path,
                                sim_db_override=agent._best_sim_db())

    pairs = []
    for name, scfg in strategies.items():
        strat_params = {k: v for k, v in scfg.items() if not isinstance(v, dict)}
        index_cfgs = {k: v for k, v in scfg.items() if isinstance(v, dict)}
        if index_cfgs and 'buy' not in scfg:
            for idx, icfg in index_cfgs.items():
                pairs.append((name, idx, {**strat_params, **icfg}))
        else:
            pairs.append((name, name, scfg))

    wanted = {i.lstrip('^').upper() for i in (index_filter or [])}
    buffer_pct = default_buffer_pct(username)
    drafts, skips = [], []

    for name, idx, scfg in pairs:
        if wanted and idx.lstrip('^').upper() not in wanted:
            continue
        try:
            signals, err = evaluator.get_signals(name, idx, scfg, sell_lookback_days=0)
        except Exception as e:
            logger.error('Staging: get_signals(%s/%s) failed: %s', name, idx, e)
            skips.append((f'{name}/{idx}', f'Signalberechnung fehlgeschlagen: {e}'))
            continue
        if err:
            skips.append((f'{name}/{idx}', str(err)))
            continue

        raw_buys = [s for s in (signals or []) if s.get('signal') == 'buy']
        sized = _size_signals(raw_buys, scfg, system_ccy)
        _fill_longnames(sized, db_path)
        for s in sized:
            s.setdefault('strategy', name)
        made, skipped = drafts_from_agent_signals(sized, buffer_pct=buffer_pct,
                                                  allow_network=True)
        drafts += made
        skips += skipped

    return drafts, skips


def collect_stop_drafts(db_path: str = 'database') -> tuple:
    """Sell drafts for positions whose trailing stop is breached.

    Reads the trails table the Risk Management tab maintains. It is only as
    fresh as the last trailing-stop update — this job does not recompute it, so
    a stale table yields stale exits.
    """
    import pandas as pd
    from tradinglib.tools import open_db

    try:
        db_file = tools.Tools().get_path(path=db_path, file_name='trades.db')
        with open_db(db_file, readonly=True) as conn:
            trails = pd.read_sql_query(
                'SELECT ticker, last_price, trail_stop, updated_at '
                'FROM own_trades_trails WHERE breached = 1', conn)
    except Exception as e:
        logger.info('Staging: no trailing-stop data (%s)', e)
        return [], []

    if trails.empty:
        return [], []

    from tradinglib.own_trades_analysis import _get_open_positions_for_trails
    held = {str(p['ticker']).upper(): p for p in _get_open_positions_for_trails(db_path)}
    isins = isin_map_from_trades(db_path)

    rows = []
    for _, row in trails.iterrows():
        ticker = str(row['ticker']).upper()
        rows.append({
            'ticker': ticker,
            'isin':   isins.get(ticker, ''),
            'shares': float(held.get(ticker, {}).get('shares', 0) or 0),
            'price':  float(row.get('last_price', 0) or 0),
            'reason': f"Trailing Stop {row.get('trail_stop')} gerissen",
        })
    return drafts_from_position_sells(rows, allow_network=True)


# ─────────────────────────────────────────────────────────────────────────────
# Push
# ─────────────────────────────────────────────────────────────────────────────

def push_text(draft) -> str:
    """What to type in the Scalable dialog — the deep link cannot carry it."""
    if draft.order_type == 'limit':
        price = f'Limit {_dec(draft.limit_price)} {draft.currency}'
    elif draft.order_type == 'stop':
        price = f'Stop {_dec(draft.stop_price)} {draft.currency}'
    else:
        price = 'Market'
    size = f'{int(draft.shares)} Stk.' if draft.shares else f'{_dec(draft.amount)} {draft.currency}'
    side = 'Kauf' if draft.side == 'buy' else 'Verkauf'
    lines = [f'{side}: {draft.name or draft.ticker or draft.isin}',
             f'{size} · {price}',
             f'ISIN {draft.isin}']
    if draft.note:
        lines.append(draft.note)
    return '\n'.join(lines)


def send_push(draft, notifier=None) -> bool:
    """One push per draft, linking into the broker's trade dialog.

    Deduplication keys on the draft id, so a draft is announced once no matter
    how often the job runs.
    """
    if notifier is None:
        from tradinglib.pushover_notifier import PushoverNotifier
        notifier = PushoverNotifier()
    url = broker_handoff_url(draft)
    return bool(notifier.send_notification(
        ticker=f'order:{draft.draft_id}',
        price=float(draft.limit_price or draft.stop_price or 0),
        date=datetime.now().strftime('%Y-%m-%d'),
        message=push_text(draft),
        title=PUSH_TITLE,
        url=url,
        url_title=PUSH_ACTION if url else '',
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Job
# ─────────────────────────────────────────────────────────────────────────────

def stage(username: str = 'admin', db_path: str = 'database', buys: bool = True,
          stops: bool = False, push: bool = True, dry_run: bool = False,
          index_filter=None) -> dict:
    """Collect, stage and announce. Returns a summary for the log."""
    basket = OrderBasket(db_path=db_path)
    already = basket.open_isins()

    drafts, skips = [], []
    if buys:
        d, s = collect_buy_drafts(username, db_path, index_filter)
        drafts += d
        skips += s
    if stops:
        d, s = collect_stop_drafts(db_path)
        drafts += d
        skips += s

    # An ISIN already waiting in the basket is not staged twice — the same
    # signal fires on consecutive days until it is acted on.
    fresh, duplicates = [], 0
    for d in drafts:
        if d.isin in already:
            duplicates += 1
            continue
        already.add(d.isin)
        fresh.append(d)

    summary = {'found': len(drafts), 'staged': 0, 'pushed': 0,
               'duplicates': duplicates, 'skipped': skips, 'dry_run': dry_run}

    if dry_run:
        summary['drafts'] = fresh
        return summary

    added, rejected = basket.add_many(fresh)
    summary['staged'] = added
    for draft, problems in rejected:
        skips.append((draft.ticker or draft.isin, '; '.join(problems)))

    if push and added:
        notifier = None
        try:
            from tradinglib.pushover_notifier import PushoverNotifier
            notifier = PushoverNotifier()
        except Exception as e:
            logger.warning('Staging: Pushover unavailable (%s) — orders are staged '
                           'but not announced', e)
        if notifier is not None:
            # Only what actually made it into the basket — a draft the validation
            # threw out must not be announced as if it were waiting.
            rejected_ids = {d.draft_id for d, _ in rejected}
            staged_ids = {d.draft_id for d in fresh} - rejected_ids
            for d in basket.list(STATUS_OPEN):
                if d.draft_id in staged_ids:
                    try:
                        if send_push(d, notifier):
                            summary['pushed'] += 1
                    except Exception as e:
                        logger.warning('Staging: push for %s failed: %s', d.ticker, e)
    return summary


def _format_summary(summary: dict) -> str:
    parts = [f"gefunden {summary['found']}",
             f"übernommen {summary['staged']}",
             f"gemeldet {summary['pushed']}"]
    if summary['duplicates']:
        parts.append(f"schon im Korb {summary['duplicates']}")
    text = 'Order-Staging: ' + ', '.join(parts)
    for ticker, why in summary['skipped']:
        text += f'\n  übersprungen {ticker}: {why}'
    for d in summary.get('drafts', []):
        text += '\n  [dry-run] ' + push_text(d).replace('\n', ' | ')
    return text


def _parse(argv) -> tuple:
    """Split ``/flag`` and ``/key:value`` arguments.

    Parsed here rather than via cli.parse_args: that parser expects sys.argv
    including the program name, returns a fixed key set for asset_perf2, and
    silently drops flags it does not know — three ways to lose an option.
    """
    flags, values = set(), {}
    for arg in argv:
        if not arg.startswith('/'):
            continue
        body = arg[1:]
        if ':' in body:
            key, _, val = body.partition(':')
            values[key.lower()] = val          # value keeps its original case
        else:
            flags.add(body.lower())
    return flags, values


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    flags, args = _parse(argv)

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

    username = args.get('user') or 'admin'
    db_path = args.get('db') or 'database'
    dry_run = 'dry-run' in flags or 'dry_run' in flags
    stops = 'stops' in flags
    # Buys are the default; /stops alone means "only stops".
    buys = 'buys' in flags or not stops
    push = 'no-push' not in flags and 'no_push' not in flags

    if 'test-push' in flags or 'test_push' in flags:
        drafts = OrderBasket(db_path=db_path).list(STATUS_OPEN)
        if not drafts:
            print('Order-Korb ist leer — nichts zu melden.')
            return 1
        sent = send_push(drafts[0])
        print(f'Test-Push für {drafts[0].ticker or drafts[0].isin}: '
              + ('gesendet' if sent else 'übersprungen (schon gemeldet)'))
        return 0

    index_filter = [i.strip() for i in (args.get('index') or '').split(',') if i.strip()]
    summary = stage(username=username, db_path=db_path, buys=buys, stops=stops,
                    push=push, dry_run=dry_run, index_filter=index_filter or None)
    print(_format_summary(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
