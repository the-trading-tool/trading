"""Order basket for Scalable Capital — prepare orders, release them at the broker.

Scalable deliberately keeps a human in the loop: ``preview_*_order`` returns a
signed submission blob that ``submit_*_order`` only accepts after an explicit,
per-order confirmation. Nothing here tries to work around that.

**This module never contacts the broker.** It decides what to order and records
what happened; placing the order is a separate act in Scalable's own interface.
``broker_handoff_url()`` opens the right instrument's trade dialog there, but
quantity, order type and limit are still entered by hand — the deep link carries
instrument and side only. The status buttons are bookkeeping, not instructions
to the bank.

Drafts live in their own ``scalable_orders.db`` and come from three sources:
trading-agent signals, sell signals on positions actually held, and manual
entries. Export produces the exact argument objects the MCP preview tools expect,
plus a plain checklist for typing them into the Scalable app by hand.

Validation mirrors the broker's constraints, so a draft that passes here is one
the broker will accept:
  * ISIN required and well-formed — Scalable addresses instruments by ISIN only
  * share quantities are whole units. Fractions are possible for BUYS only, and
    only as an AMOUNT order ("buy for 15,400 EUR" instead of "buy 0.22 BTC") —
    that is the broker's own way of expressing them. Sells are share-based with
    no amount alternative, so a fractional rest stays in the portfolio.
  * order types market, limit and stop; no trailing stop, no OTO/bracket legs
  * venues gettex, Xetra and EIX
"""
import logging
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime

import pandas as pd

from tradinglib import tools

logger = logging.getLogger(__name__)

DB_NAME = 'scalable_orders.db'
TABLE = 'order_drafts'

ISIN_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{9}\d$')
ORDER_TYPES = ('market', 'limit', 'stop')
VENUES = ('gettex', 'xetra', 'eix')
SIDES = ('buy', 'sell')

STATUS_OPEN = 'open'
STATUS_RELEASED = 'released'
# Rejected at the broker. A rejected draft stays on record as NOT executed and is
# never retried — nothing in this module reschedules anything. It leaves the open
# basket, so no builder, export or sync picks it up again. Getting it back into
# play is an explicit act: reopen() puts it in the basket once more.
STATUS_REJECTED = 'rejected'


# ─────────────────────────────────────────────────────────────────────────────
# Model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OrderDraft:
    """One prepared, not yet released order."""
    side: str
    isin: str
    ticker: str = ''
    name: str = ''
    shares: float = 0.0            # whole shares; 0 when sizing by amount (buy only)
    amount: float = 0.0            # cash amount for a buy, alternative to shares
    order_type: str = 'market'
    limit_price: float = 0.0
    stop_price: float = 0.0
    venue: str = ''                # empty = portfolio default
    currency: str = 'EUR'
    source: str = 'manual'         # agent | position | manual
    note: str = ''
    status: str = STATUS_OPEN
    attempts: int = 0              # how often this draft was put into the basket
    status_at: str = ''            # when the status last changed
    draft_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    def as_dict(self) -> dict:
        return asdict(self)


def _dec(value) -> str:
    """Format a number for the broker: plain decimal, no exponent, no float noise."""
    text = f'{float(value):.6f}'.rstrip('0').rstrip('.')
    return text or '0'


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate(draft: OrderDraft) -> list:
    """Return a list of problems; empty means the broker should accept it."""
    problems = []

    if draft.side not in SIDES:
        problems.append(f'Unbekannte Orderrichtung: {draft.side!r}')
    if not ISIN_RE.match(str(draft.isin or '').upper()):
        problems.append(f'Keine gültige ISIN: {draft.isin!r}')
    if draft.order_type not in ORDER_TYPES:
        problems.append(f'Ordertyp muss market, limit oder stop sein (nicht {draft.order_type!r})')
    if draft.venue and draft.venue not in VENUES:
        problems.append(f'Handelsplatz muss gettex, xetra oder eix sein (nicht {draft.venue!r})')

    if draft.order_type == 'limit' and draft.limit_price <= 0:
        problems.append('Limit-Order ohne Limitpreis')
    if draft.order_type == 'stop' and draft.stop_price <= 0:
        problems.append('Stop-Order ohne Stoppreis')

    # Quantity: buys may be sized by amount, sells are share-based only.
    if draft.side == 'sell':
        if draft.amount and not draft.shares:
            problems.append('Verkäufe können nur über Stückzahl beauftragt werden, nicht über einen Betrag')
        if draft.shares <= 0:
            problems.append('Verkauf ohne Stückzahl')
    else:
        if draft.shares <= 0 and draft.amount <= 0:
            problems.append('Kauf ohne Stückzahl und ohne Betrag')

    if draft.shares and float(draft.shares) != int(draft.shares):
        # Bruchteile sind erlaubt — aber nur als Betrags-Order, denn die
        # Stückzahl-Variante des Brokers nimmt ausschliesslich ganze Einheiten.
        # Beim Verkauf gibt es diesen Ausweg nicht: dort kennt Scalable nur
        # Stückzahlen, ein Bruchteil-Rest bleibt liegen.
        if draft.side == 'buy':
            problems.append(
                f'Bruchteil {draft.shares} nur als Betrag beauftragbar — '
                'amount setzen statt shares')
        else:
            problems.append(f'Nur ganze Stücke verkäuflich (nicht {draft.shares})')

    return problems


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

def to_preview_payload(draft: OrderDraft) -> dict:
    """Build the exact arguments for preview_buy_order / preview_sell_order."""
    if draft.side == 'buy' and draft.amount and not draft.shares:
        quantity = {'mode': 'amount', 'amount': _dec(draft.amount)}
    else:
        # int() ohne Pruefung machte aus 0,22 Stueck stillschweigend eine
        # 0-Stueck-Order. Lieber laut scheitern: validate() faengt den Fall
        # vorher ab, hier bleibt die Rueckversicherung.
        if float(draft.shares) != int(draft.shares):
            raise ValueError(
                f'Bruchteil {draft.shares} kann nicht als Stueckzahl beauftragt '
                'werden — fuer Kaeufe amount setzen, Verkaeufe gehen nur ganz')
        quantity = {'mode': 'shares', 'shares': int(draft.shares)}

    if draft.order_type == 'limit':
        order = {'type': 'limit', 'limitPrice': _dec(draft.limit_price)}
    elif draft.order_type == 'stop':
        order = {'type': 'stop', 'stopPrice': _dec(draft.stop_price)}
    else:
        order = {'type': 'market'}

    args = {'isin': str(draft.isin).upper(), 'quantity': quantity, 'order': order}
    if draft.venue:
        args['venue'] = draft.venue
    return args


def broker_handoff_url(draft) -> str:
    """Deep link into Scalable's own trade dialog for this instrument.

    Same shape the MCP preview returns as ``brokerHandoff.url`` — that is the
    documented way to hand an order over when the client does not submit it
    itself, which is exactly this basket's position.

    The link carries instrument and side only: quantity, order type and limit
    still have to be entered in the Scalable dialog. It saves the search, not
    the typing — the checklist next to it is what you type from.
    """
    isin = str(getattr(draft, 'isin', '') or '').upper()
    if not ISIN_RE.match(isin):
        return ''
    side = 'SELL' if getattr(draft, 'side', 'buy') == 'sell' else 'BUY'
    return ('https://de.scalable.capital/broker/security'
            f'?isin={isin}&modal=trade&security={isin}&type={side}')


def export_payloads(drafts) -> list:
    """Preview calls for a whole basket, in the order the basket lists them.

    Each entry names the tool and its arguments. Submitting stays a separate,
    per-order confirmation at the broker — this only prepares the previews.
    """
    out = []
    for d in drafts:
        out.append({
            'tool': 'preview_buy_order' if d.side == 'buy' else 'preview_sell_order',
            'arguments': to_preview_payload(d),
            'draft_id': d.draft_id,
            'label': f'{d.side.upper()} {d.name or d.ticker or d.isin}',
        })
    return out


def to_checklist_frame(drafts) -> pd.DataFrame:
    """Readable table of the basket — also what you retype in the Scalable app."""
    rows = []
    for d in drafts:
        if d.order_type == 'limit':
            price = f'Limit {_dec(d.limit_price)}'
        elif d.order_type == 'stop':
            price = f'Stop {_dec(d.stop_price)}'
        else:
            price = 'Market'
        size = f'{int(d.shares)} Stk.' if d.shares else f'{_dec(d.amount)} {d.currency}'
        rows.append({
            'Richtung':     'Kauf' if d.side == 'buy' else 'Verkauf',
            'Titel':        d.name or d.ticker or d.isin,
            'ISIN':         d.isin,
            'Menge':        size,
            'Ordertyp':     price,
            'Handelsplatz': d.venue or 'Standard',
            'Quelle':       d.source,
            'Hinweis':      d.note,
            'Angelegt':     d.created_at,
            'draft_id':     d.draft_id,
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Store
# ─────────────────────────────────────────────────────────────────────────────

class OrderBasket:
    """Persistent basket of prepared orders."""

    def __init__(self, db_path: str = 'database'):
        self.db_path = db_path

    def _db(self):
        return tools.Db_tools(db_path=self.db_path, database_name=DB_NAME)

    def _ensure(self, dbt, row: dict):
        dbt.ensure_table_and_columns(keys=list(row.keys()), row_dict=row,
                                     database_name=TABLE, primary_key=False)

    def add(self, draft: OrderDraft) -> tuple:
        """Store a draft. Returns (ok, problems) — invalid drafts are not stored."""
        problems = validate(draft)
        if problems:
            return False, problems
        draft.isin = str(draft.isin).upper()
        row = draft.as_dict()
        dbt = self._db()
        try:
            self._ensure(dbt, row)
            dbt.insert_data(keys=list(row.keys()), row_dict=row,
                            database_name=TABLE, replace=False)
            dbt.conn.commit()
        finally:
            dbt.close()
        return True, []

    def add_many(self, drafts) -> tuple:
        """Add several drafts. Returns (added, rejected) where rejected is
        a list of (draft, problems)."""
        added, rejected = 0, []
        for d in drafts:
            ok, problems = self.add(d)
            if ok:
                added += 1
            else:
                rejected.append((d, problems))
        return added, rejected

    def list(self, status: str = STATUS_OPEN) -> list:
        """Drafts with the given status, oldest first. status='' returns all."""
        dbt = self._db()
        try:
            try:
                cols = [r[1] for r in dbt.cursor.execute(f'PRAGMA table_info({TABLE})').fetchall()]
            except Exception:
                return []
            if not cols:
                return []
            sql = f'SELECT {", ".join(cols)} FROM {TABLE}'
            params = ()
            if status:
                sql += ' WHERE status = ?'
                params = (status,)
            sql += ' ORDER BY created_at'
            rows = dbt.cursor.execute(sql, params).fetchall()
        except Exception as e:
            logger.warning('Order basket: read failed: %s', e)
            return []
        finally:
            dbt.close()

        known = set(OrderDraft.__dataclass_fields__.keys())
        out = []
        for row in rows:
            data = {k: v for k, v in zip(cols, row) if k in known and v is not None}
            for num in ('shares', 'amount', 'limit_price', 'stop_price'):
                if num in data:
                    try:
                        data[num] = float(data[num])
                    except (TypeError, ValueError):
                        data[num] = 0.0
            # SQLite hands back a REAL for the counter — keep it an int.
            if 'attempts' in data:
                try:
                    data['attempts'] = int(float(data['attempts']))
                except (TypeError, ValueError):
                    data['attempts'] = 0
            try:
                out.append(OrderDraft(**data))
            except TypeError as e:
                logger.warning('Order basket: skipping malformed row: %s', e)
        return out

    def _update_status(self, draft_ids, status: str, bump_attempts: bool = False) -> int:
        ids = [str(i) for i in (draft_ids or [])]
        if not ids:
            return 0
        dbt = self._db()
        try:
            # Rows written before these columns existed are updated on their own
            # terms — the status change must not fail over a missing column.
            cols = {row[1] for row in
                    dbt.cursor.execute(f'PRAGMA table_info({TABLE})').fetchall()}
            sets, params = ['status = ?'], [status]
            if 'status_at' in cols:
                sets.append('status_at = ?')
                params.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            if bump_attempts and 'attempts' in cols:
                sets.append('attempts = COALESCE(attempts, 0) + 1')
            marks = ','.join('?' * len(ids))
            dbt.cursor.execute(
                f'UPDATE {TABLE} SET {", ".join(sets)} WHERE draft_id IN ({marks})',
                params + ids)
            changed = dbt.cursor.rowcount
            dbt.conn.commit()
            return changed
        except Exception as e:
            logger.warning('Order basket: status update failed: %s', e)
            return 0
        finally:
            dbt.close()

    def mark_released(self, draft_ids) -> int:
        """Mark drafts as released at the broker — they leave the open basket.

        Released is a bookkeeping state set by you, not a confirmation that the
        broker accepted anything. The executed order comes back through the
        transaction import like any other trade.
        """
        return self._update_status(draft_ids, STATUS_RELEASED)

    def mark_rejected(self, draft_ids) -> int:
        """Record drafts as rejected at the broker — not executed, not retried.

        The draft stays on record with its full parameters so it remains visible
        in the log. Nothing reschedules it: only reopen() puts it back in play.
        """
        return self._update_status(draft_ids, STATUS_REJECTED)

    def reopen(self, draft_ids) -> int:
        """Put drafts back into the open basket and count the new attempt.

        Deliberately manual — a rejected order is a decision, not a failure to
        retry. Prices are kept as they were; re-staging an old limit is a choice
        the basket shows rather than silently corrects.
        """
        return self._update_status(draft_ids, STATUS_OPEN, bump_attempts=True)

    def remove(self, draft_ids) -> int:
        ids = [str(i) for i in (draft_ids or [])]
        if not ids:
            return 0
        dbt = self._db()
        try:
            marks = ','.join('?' * len(ids))
            dbt.cursor.execute(f'DELETE FROM {TABLE} WHERE draft_id IN ({marks})', ids)
            removed = dbt.cursor.rowcount
            dbt.conn.commit()
            return removed
        except Exception as e:
            logger.warning('Order basket: delete failed: %s', e)
            return 0
        finally:
            dbt.close()

    def open_isins(self) -> set:
        """ISINs already sitting in the open basket — guards against double entries."""
        return {d.isin for d in self.list(STATUS_OPEN)}


# ─────────────────────────────────────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────────────────────────────────────

def _isin_for(ticker: str, allow_network: bool = False) -> str:
    """Resolve ticker → ISIN via the existing resolver (yf_tickers.db)."""
    try:
        from tradinglib.broker_tradability import IsinResolver
        return IsinResolver().resolve(ticker, allow_network=allow_network) or ''
    except Exception as e:
        logger.debug('ISIN lookup for %s failed: %s', ticker, e)
        return ''


def _limit_from_buffer(price: float, side: str, buffer_pct: float) -> float:
    """Limit price derived from the basket's buffer (``BUFFER_KEY``).

    Buys bid BELOW the last price, sells ask ABOVE it: when an order is prepared
    for later manual release, the point is not to pay worse than the signal.
    Note this is the opposite direction to ``agent_limit_buffer_pct``, which
    allows a fill above the signal so the live agent still gets executed.
    """
    if not price or buffer_pct <= 0:
        return 0.0
    factor = (1 - buffer_pct / 100) if side == 'buy' else (1 + buffer_pct / 100)
    return round(float(price) * factor, 4)


def drafts_from_agent_signals(signals, buffer_pct: float = 0.0, venue: str = '',
                              allow_network: bool = False) -> tuple:
    """Turn sized trading-agent signals into buy drafts.

    Expects what ``_size_signals`` produces: ticker, qty and optionally price,
    longname and strategy. Signals without a resolvable ISIN are returned as
    skips rather than silently dropped — Scalable cannot address them.
    """
    drafts, skipped = [], []
    for sig in signals or []:
        ticker = str(sig.get('ticker', '')).strip()
        qty = float(sig.get('qty', 0) or 0)
        isin = str(sig.get('isin', '') or '').strip().upper() or _isin_for(ticker, allow_network)
        if not isin:
            skipped.append((ticker, 'keine ISIN gefunden'))
            continue
        if qty <= 0:
            skipped.append((ticker, 'keine Stückzahl'))
            continue
        price = float(sig.get('price', 0) or 0)
        # Bruchteile: Scalable nimmt sie nur als BETRAGS-Order entgegen, nicht
        # als Bruchteil einer Stückzahl. Ein Kaufsignal über 0,22 BTC wird also
        # zu "kaufe für 15.400 EUR". Vorher fiel so ein Signal komplett weg
        # (qty < 1 -> übersprungen), und int(qty) hätte 0,22 zu 0 gemacht.
        _shares, _amount = float(int(qty)), 0.0
        if qty != int(qty) or qty < 1:
            if price <= 0:
                skipped.append((ticker, 'Bruchteil ohne Kurs — Betrag nicht berechenbar'))
                continue
            _shares, _amount = 0.0, round(qty * price, 2)
            if _amount <= 0:
                skipped.append((ticker, 'Bruchteil ergibt keinen Betrag'))
                continue
        limit = _limit_from_buffer(price, 'buy', buffer_pct)
        drafts.append(OrderDraft(
            side='buy', isin=isin, ticker=ticker,
            name=str(sig.get('longname', '') or sig.get('name', '') or ''),
            shares=_shares, amount=_amount,
            order_type='limit' if limit else 'market',
            limit_price=limit, venue=venue,
            currency=str(sig.get('currency', 'EUR') or 'EUR'),
            source='agent',
            note=str(sig.get('strategy', '') or ''),
        ))
    return drafts, skipped


def isin_map_from_trades(db_path: str = 'database') -> dict:
    """Map ticker → ISIN from trades.db.

    Preferred over the yf_tickers.db resolver for instruments actually held:
    rows imported from Scalable always carry an ISIN, while the ticker database
    is only partially filled.
    """
    out = {}
    try:
        dbt = tools.Db_tools(db_path=db_path, database_name='trades.db')
    except Exception as e:
        logger.debug('ISIN map: cannot open trades.db: %s', e)
        return out
    try:
        cols = {row[1].lower() for row in
                dbt.cursor.execute('PRAGMA table_info(trades)').fetchall()}
        if not {'ticker', 'isin'} <= cols:
            return out
        for ticker, isin in dbt.cursor.execute(
                'SELECT ticker, isin FROM trades '
                'WHERE isin IS NOT NULL AND isin != "" AND ticker IS NOT NULL').fetchall():
            key = str(ticker).upper().strip()
            if key and key not in out:
                out[key] = str(isin).upper().strip()
    except Exception as e:
        logger.debug('ISIN map from trades.db failed: %s', e)
    finally:
        try:
            dbt.close()
        except Exception:
            pass
    return out


BUFFER_KEY = 'scalable_limit_buffer_pct'


def default_buffer_pct(username: str = 'admin') -> float:
    """Limit buffer for prepared orders, in percent. 0 means market order.

    Deliberately NOT ``agent_limit_buffer_pct``: that one means "accept a fill up
    to N% ABOVE the signal" so the live agent still gets filled on a rising
    price. Here the buffer runs the other way — bid below, ask above (see
    ``_limit_from_buffer``). One key with two opposite meanings is a trap, so the
    basket has its own.
    """
    try:
        from tradinglib import system_config as sysconf
        cfg = sysconf.SystemConfig(username=username)
        try:
            return float(cfg.get_value(BUFFER_KEY, 0) or 0)
        finally:
            try:
                cfg.close()
            except Exception:
                pass
    except Exception as e:
        logger.debug('Limit buffer lookup failed: %s', e)
        return 0.0


def set_default_buffer_pct(value: float, username: str = 'admin') -> None:
    """Persist the basket's limit buffer."""
    from tradinglib import system_config as sysconf
    cfg = sysconf.SystemConfig(username=username)
    try:
        cfg.set_value(BUFFER_KEY, float(value))
    finally:
        try:
            cfg.close()
        except Exception:
            pass


def drafts_from_position_sells(positions, buffer_pct: float = 0.0, venue: str = '',
                               allow_network: bool = False) -> tuple:
    """Turn exit signals on held positions into sell drafts.

    Expects rows with ticker/isin and a share count — typically the open
    positions from the Scalable import, filtered to those flagged for exit.
    Quantities are floored to whole shares; a fractional rest cannot be sold
    through the API and is reported as a skip.
    """
    drafts, skipped = [], []
    for pos in positions or []:
        ticker = str(pos.get('ticker', '')).strip()
        isin = str(pos.get('isin', '') or '').strip().upper() or _isin_for(ticker, allow_network)
        if not isin:
            skipped.append((ticker, 'keine ISIN gefunden'))
            continue
        shares = float(pos.get('shares', 0) or 0)
        if shares < 1:
            skipped.append((ticker, f'Bestand {shares} — keine ganzen Stücke verkäuflich'))
            continue
        if shares != int(shares):
            skipped.append((ticker, f'Bruchteil {round(shares - int(shares), 6)} bleibt liegen'))
        price = float(pos.get('price', 0) or 0)
        limit = _limit_from_buffer(price, 'sell', buffer_pct)
        drafts.append(OrderDraft(
            side='sell', isin=isin, ticker=ticker,
            name=str(pos.get('longname', '') or pos.get('name', '') or ''),
            shares=float(int(shares)),
            order_type='limit' if limit else 'market',
            limit_price=limit, venue=venue,
            currency=str(pos.get('currency', 'EUR') or 'EUR'),
            source='position',
            note=str(pos.get('reason', '') or ''),
        ))
    return drafts, skipped


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

def render_add_to_basket(region, ticker: str, isin: str = '', name: str = '',
                         price: float = 0.0, db_path: str = 'database',
                         key_prefix: str = 'basket'):
    """Compact 'add this instrument to the order basket' form.

    Meant to sit next to a chart: the instrument is known, only side, size and
    order type still need a decision.
    """
    import streamlit as st

    r = region
    isin = (isin or _isin_for(ticker, allow_network=True) or '').upper()
    if not isin:
        r.info(f'Für {ticker} ist keine ISIN hinterlegt — ohne ISIN kann Scalable '
               'den Titel nicht ansprechen.')
        return

    c1, c2, c3 = r.columns(3)
    side = c1.selectbox('Richtung', ['Kauf', 'Verkauf'], key=f'{key_prefix}_side')
    order_type = c2.selectbox('Ordertyp', ['Market', 'Limit', 'Stop'], key=f'{key_prefix}_type')
    venue = c3.selectbox('Handelsplatz', ['Standard'] + list(VENUES), key=f'{key_prefix}_venue')

    c4, c5 = r.columns(2)
    shares = c4.number_input('Stück', min_value=0, step=1, value=0, key=f'{key_prefix}_shares')
    limit = 0.0
    if order_type in ('Limit', 'Stop'):
        limit = c5.number_input(f'{order_type}preis', min_value=0.0, step=0.01,
                                value=float(price or 0.0), format='%.4f',
                                key=f'{key_prefix}_price')

    if r.button('In den Order-Korb', key=f'{key_prefix}_add'):
        draft = OrderDraft(
            side='buy' if side == 'Kauf' else 'sell',
            isin=isin, ticker=ticker, name=name, shares=float(shares),
            order_type=order_type.lower(),
            limit_price=limit if order_type == 'Limit' else 0.0,
            stop_price=limit if order_type == 'Stop' else 0.0,
            venue='' if venue == 'Standard' else venue,
            source='manual',
        )
        ok, problems = OrderBasket(db_path=db_path).add(draft)
        if ok:
            r.success(f'{side} {name or ticker} in den Order-Korb gelegt.')
        else:
            r.error('Nicht übernommen: ' + '; '.join(problems))


_MSG_KEY = 'scalable_basket_msg'


def _basket_action(db_path: str, action: str, ids: list):
    """Button callback: change the state BEFORE the page is drawn again.

    Streamlit already reruns the script after a click. Doing the write inside the
    render and then calling st.rerun() aborts that render half-drawn, which left
    a stale table sitting under an "empty basket" notice. A callback runs before
    the rerun, so the page is built once, from the finished state.
    """
    import streamlit as st

    if not ids:
        st.session_state[_MSG_KEY] = ('warning', 'Keine Zeile ausgewählt.')
        return
    basket = OrderBasket(db_path=db_path)
    if action == 'released':
        n = basket.mark_released(ids)
        msg = (f'{n} Order(s) als bei Scalable gestellt vermerkt. Die Ausführung '
               'kommt beim nächsten Transaktions-Import zurück.')
    elif action == 'rejected':
        n = basket.mark_rejected(ids)
        msg = (f'{n} Order(s) als abgelehnt vermerkt — sie bleiben als nicht '
               'ausgeführt im Protokoll und werden nicht erneut gestellt.')
    elif action == 'reopen':
        n = basket.reopen(ids)
        msg = f'{n} Order(s) liegen wieder im Korb.'
    else:
        n = basket.remove(ids)
        msg = f'{n} Eintrag/Einträge entfernt.'
    st.session_state[_MSG_KEY] = ('success' if n else 'warning', msg)


def _flush_message(r):
    """Show the result of the last action once, then forget it."""
    import streamlit as st

    kind, text = st.session_state.pop(_MSG_KEY, (None, None))
    if kind == 'success':
        r.success(text)
    elif kind == 'warning':
        r.warning(text)


def render_order_basket(region, db_path: str = 'database', username: str = ''):
    """Review the prepared orders, export them, mark them released."""
    import json as _json

    import streamlit as st

    r = region
    basket = OrderBasket(db_path=db_path)
    drafts = basket.list(STATUS_OPEN)

    r.markdown('### Order-Korb')
    _flush_message(r)
    r.markdown(
        '**Diese App stellt keine Orders** — sie hat keine Verbindung zu Scalable. '
        'Der Ablauf je Order:\n'
        '1. **„Order stellen ↗"** unten öffnet den Handelsdialog bei Scalable\n'
        '2. dort Stückzahl, Ordertyp und Limit eintragen und bestätigen oder abbrechen\n'
        '3. hier vermerken, was passiert ist — die Knöpfe darunter ändern **nur** '
        'diesen Vermerk'
    )

    _render_buffer_setting(r, username)

    if not drafts:
        r.info('Der Order-Korb ist leer.')
        _render_basket_log(r, basket)
        return

    # Step 1 first, and as real buttons: the hand-off used to be the last column
    # of a wide table, where it was easy to miss entirely.
    for d in drafts:
        h_left, h_right = r.columns([3, 1])
        if d.order_type == 'limit':
            _price = f'Limit {_dec(d.limit_price)}'
        elif d.order_type == 'stop':
            _price = f'Stop {_dec(d.stop_price)}'
        else:
            _price = 'Market'
        _size = f'{int(d.shares)} Stk.' if d.shares else f'{_dec(d.amount)} {d.currency}'
        h_left.markdown(
            f'**{"Kauf" if d.side == "buy" else "Verkauf"} {d.name or d.ticker or d.isin}** — '
            f'{_size}, {_price}'
            + (f', {d.venue}' if d.venue else '')
        )
        h_left.caption(f'ISIN {d.isin} · bei Scalable einzutragen: {_size}, {_price}')
        _url = broker_handoff_url(d)
        if _url:
            h_right.link_button('Order stellen ↗', _url, use_container_width=True)
        else:
            h_right.caption('Keine gültige ISIN')

    r.divider()

    frame = to_checklist_frame(drafts)
    view = frame.drop(columns=['draft_id'])
    event = r.dataframe(
        view, hide_index=True, use_container_width=True,
        on_select='rerun', selection_mode='multi-row',
        key='scalable_basket_table',
    )
    r.caption('Zeile(n) auswählen, um sie unten zu vermerken oder zu entfernen.')
    try:
        picked = list(event.selection.rows)
    except Exception:
        picked = []
    selected_ids = [frame.iloc[i]['draft_id'] for i in picked] if picked else []

    # These only move the draft between local states. Nothing here reaches the
    # broker — the label has to say so, or the button reads like a release.
    c1, c2, c3, c4 = r.columns(4)
    c1.button(f'Bei Scalable gestellt ({len(selected_ids)})',
              disabled=not selected_ids, key='scalable_basket_release',
              on_click=_basket_action, args=(db_path, 'released', selected_ids))
    c2.button(f'Bei Scalable abgelehnt ({len(selected_ids)})',
              disabled=not selected_ids, key='scalable_basket_reject',
              on_click=_basket_action, args=(db_path, 'rejected', selected_ids))
    c3.button(f'Entfernen ({len(selected_ids)})',
              disabled=not selected_ids, key='scalable_basket_remove',
              on_click=_basket_action, args=(db_path, 'remove', selected_ids))

    chosen = [d for d in drafts if d.draft_id in selected_ids] or drafts
    payload = _json.dumps(export_payloads(chosen), indent=2, ensure_ascii=False)
    c4.download_button('Payloads herunterladen', data=payload,
                       file_name='scalable_order_previews.json', mime='application/json',
                       key='scalable_basket_download')

    with r.expander(f'Aufrufe für die Order-Vorschau ({len(chosen)})'):
        r.caption(
            'Argumente für preview_buy_order / preview_sell_order. Jede Vorschau '
            'liefert eine eigene Bestätigung, die einzeln freigegeben wird.'
        )
        r.code(payload, language='json')

    with r.expander('Zum Abtippen in der Scalable-App'):
        r.dataframe(view, hide_index=True, use_container_width=True)

    _render_basket_log(r, basket)


def _render_buffer_setting(r, username: str = ''):
    """Limit buffer for newly prepared orders.

    Sits here rather than in the agent's settings tab: it is a different key with
    the opposite direction, and putting it next to the agent's field would invite
    exactly the confusion the separate key avoids.
    """
    with r.expander('⚙ Limitpuffer für neue Orders'):
        current = default_buffer_pct(username or 'admin')
        chosen = r.number_input(
            'Abstand zum Kurs in % (0 = Market-Order)',
            min_value=0.0, max_value=10.0, value=float(current), step=0.1,
            format='%.2f', key='scalable_buffer_input',
            help='Käufe bieten um diesen Abstand UNTER dem Signalkurs, Verkäufe '
                 'stellen darüber. Gilt nur für Orders, die ab jetzt vorbereitet '
                 'werden — bereits im Korb liegende behalten ihren Preis. '
                 'Nicht zu verwechseln mit „Limit-entry buffer" beim Agenten: '
                 'der erlaubt einen Fill ÜBER dem Signal, also die Gegenrichtung.',
        )
        if abs(float(chosen) - float(current)) > 1e-9:
            try:
                set_default_buffer_pct(float(chosen), username or 'admin')
                r.success(f'Gespeichert: {_dec(chosen)} %')
            except Exception as e:
                r.error(f'Konnte nicht gespeichert werden: {e}')
        if not chosen:
            r.caption('Bei 0 werden Orders als Market vorbereitet — ohne Preisgrenze.')


def _render_basket_log(r, basket):
    """Protokoll der abgelehnten und freigegebenen Orders.

    Rejected drafts are kept verbatim and never re-staged on their own — the
    button below is the only way back into the basket.
    """
    import streamlit as st

    done = basket.list(STATUS_REJECTED) + basket.list(STATUS_RELEASED)
    if not done:
        return

    rejected = [d for d in done if d.status == STATUS_REJECTED]
    with r.expander(f'Protokoll ({len(done)}) — davon {len(rejected)} abgelehnt'):
        r.caption(
            'Abgelehnte Orders bleiben als nicht ausgeführt stehen. Es erfolgt kein '
            'automatischer neuer Versuch — erneutes Einstellen ist immer eine bewusste '
            'Entscheidung. Preise werden dabei unverändert übernommen.'
        )
        log = to_checklist_frame(done)
        log.insert(0, 'Status', ['abgelehnt' if d.status == STATUS_REJECTED
                                 else 'gestellt' for d in done])
        log['Versuche'] = [int(getattr(d, 'attempts', 0) or 0) + 1 for d in done]
        log['Geändert'] = [getattr(d, 'status_at', '') or '' for d in done]
        log_view = log.drop(columns=['draft_id'])
        event = r.dataframe(log_view, hide_index=True, use_container_width=True,
                            on_select='rerun', selection_mode='multi-row',
                            key='scalable_basket_log_table')
        try:
            picked = list(event.selection.rows)
        except Exception:
            picked = []
        ids = [log.iloc[i]['draft_id'] for i in picked] if picked else []

        lc1, lc2 = r.columns(2)
        lc1.button(f'Erneut einstellen ({len(ids)})', disabled=not ids,
                   type='primary', key='scalable_basket_reopen',
                   on_click=_basket_action, args=(basket.db_path, 'reopen', ids))
        lc2.button(f'Endgültig löschen ({len(ids)})', disabled=not ids,
                   key='scalable_basket_log_remove',
                   on_click=_basket_action, args=(basket.db_path, 'remove', ids))
