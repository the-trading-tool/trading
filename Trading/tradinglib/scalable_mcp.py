"""Scalable Capital MCP → CSV-shaped frame adapter.

The Scalable MCP server (https://mcp.scalable.capital/mcp) exposes the same
depot data as the CSV export, but as JSON and with a stable transaction id.
This module maps that JSON onto the exact column layout of the CSV export, so
``parse_scalable_csv()`` and the whole downstream import path stay untouched.

Transport is deliberately NOT part of this module. Talking to the MCP server
needs an OAuth 2.1 authorization-code flow with PKCE, which cannot run inside a
Streamlit request. Feed it a payload instead — captured by any MCP-capable
assistant, or later by a native client:

    payloads = load_mcp_payloads(path)
    df_csv   = mcp_to_scalable_frame(payloads)
    parsed   = parse_scalable_csv(df_csv)      # unchanged existing parser

Expected payload: the JSON returned by ``list_portfolio_transactions``, either
as one object, or as a list of pages. Details from ``get_transaction_details``
are optional but strongly recommended — see PRICE ACCURACY below.

PRICE ACCURACY
    The transaction LIST carries no execution price. Its ``amount`` is the gross
    booking amount INCLUDING transaction taxes, so amount/quantity silently
    overstates the entry price wherever a financial transaction tax applies
    (ES/FR/IT). Only ``get_transaction_details`` reports the true
    ``averagePrice`` and the tax split. Rows priced from the list alone are
    flagged in the ``price_estimated`` column.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from tradinglib.scalable_import import _format_de_number

logger = logging.getLogger(__name__)

# Column layout of the Scalable CSV export — the contract with parse_scalable_csv().
SCALABLE_CSV_COLUMNS = ['date', 'time', 'status', 'reference', 'description',
                        'assetType', 'type', 'isin', 'shares', 'price',
                        'amount', 'fee', 'tax', 'currency']

# MCP status → CSV status. Only 'Executed' is imported by the parser; every
# other value passes through unchanged so cancelled orders stay filtered out.
_STATUS_MAP = {
    'SETTLED':        'Executed',
    'FILLED':         'Executed',
    'PARTIAL_FILLED': 'Executed',
    'CONFIRMED':      'Executed',
}

# MCP cash transaction type → CSV type label understood by _SCALABLE_ACTION_MAP.
_CASH_TYPE_MAP = {
    'CASH_TRANSFER_IN':  'Cash transfer in',
    'CASH_TRANSFER_OUT': 'Cash transfer out',
    'DEPOSIT':           'Cash transfer in',
    'WITHDRAWAL':        'Cash transfer out',
    'FEE':               'Fee',
    'INTEREST':          'Interest',
    'DISTRIBUTION':      'Distribution',
}

_SIDE_MAP = {'BUY': 'Buy', 'SELL': 'Sell'}


# ─────────────────────────────────────────────────────────────────────────────
# Timestamps
# ─────────────────────────────────────────────────────────────────────────────

def _local_tz():
    """Return the timezone the CSV export uses (the account's local time).

    The CSV writes local wall-clock time while the MCP server returns UTC. Both
    end up in the same trades.db, so the MCP side has to be converted or new
    rows sit two hours off the existing ones.
    """
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo('Europe/Berlin')
    except Exception:
        return timezone(timedelta(hours=1))   # fallback: CET without DST


def _split_timestamp(iso_utc: str, local_override: str = '') -> tuple:
    """Return (date, time) in CSV format from an ISO-8601 UTC timestamp.

    ``local_override`` takes precedence: transaction details carry the FILLED
    event as local wall-clock time already, which is exactly what the CSV shows.
    """
    if local_override:
        try:
            dt = datetime.fromisoformat(str(local_override).replace('Z', ''))
            return dt.strftime('%d.%m.%Y'), dt.strftime('%H:%M:%S')
        except ValueError:
            pass
    if not iso_utc:
        return '', ''
    try:
        dt = datetime.fromisoformat(str(iso_utc).replace('Z', '+00:00'))
    except ValueError:
        return '', ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(_local_tz())
    return dt.strftime('%d.%m.%Y'), dt.strftime('%H:%M:%S')


# ─────────────────────────────────────────────────────────────────────────────
# Payload helpers
# ─────────────────────────────────────────────────────────────────────────────

def _num(val, default=0.0) -> float:
    """MCP sends decimals as strings; None and '' mean 'not applicable'."""
    if val is None or val == '':
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def load_mcp_payloads(path: str) -> list:
    """Read one JSON file holding a single payload or a list of payloads."""
    with open(path, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else [data]


def split_payloads(data) -> tuple:
    """Sort a mixed JSON dump into (transaction pages, detail payloads).

    Accepts what an assistant realistically hands over: a single response, a
    list of mixed responses, or ``{"pages": [...], "details": [...]}``. Sorting
    by shape keeps the caller from having to label anything.
    """
    if isinstance(data, dict) and ('pages' in data or 'details' in data):
        pages = data.get('pages') or []
        details = data.get('details') or []
        return (pages if isinstance(pages, list) else [pages],
                details if isinstance(details, list) else [details])

    entries = data if isinstance(data, list) else [data]
    pages, details = [], []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if 'transactions' in entry:
            pages.append(entry)
        elif 'transaction' in entry or 'securityTrade' in entry:
            details.append(entry)
    return pages, details


def index_details(details) -> dict:
    """Index get_transaction_details payloads by transaction id."""
    if details is None:
        return {}
    if isinstance(details, dict):
        details = [details]
    out = {}
    for entry in details:
        tx = entry.get('transaction', entry) if isinstance(entry, dict) else None
        if isinstance(tx, dict) and tx.get('id'):
            out[str(tx['id'])] = tx
    return out


def _filled_local_time(detail: dict) -> str:
    """Local wall-clock timestamp of the FILLED event, if the detail has one."""
    for event in reversed(detail.get('history') or []):
        if str(event.get('state', '')).upper() == 'FILLED' and event.get('timestamp'):
            return str(event['timestamp'])
    return ''


# ─────────────────────────────────────────────────────────────────────────────
# Row mapping
# ─────────────────────────────────────────────────────────────────────────────

def _security_row(item: dict, detail: dict) -> dict:
    """Map one security transaction (buy/sell) to the CSV layout."""
    sec = item.get('security') or {}
    trade = (detail or {}).get('securityTrade') or {}
    amounts = trade.get('tradeTransactionAmounts') or {}
    aggregated = trade.get('aggregatedTransactionTaxes') or {}

    shares = _num(sec.get('quantity'))
    gross = abs(_num(sec.get('amount')))          # booking amount incl. taxes

    tax = _num(aggregated.get('totalTax'), _num(amounts.get('taxAmount')))
    fee = _num(trade.get('fee')) + _num(trade.get('transactionalFee')) \
        + _num(amounts.get('transactionFee')) + _num(amounts.get('venueFee'))

    estimated = False
    price = _num(trade.get('averagePrice'))
    if not price:
        market = _num(amounts.get('marketValuation'))
        if market and shares:
            price = market / shares
        elif shares:
            # List-only fallback: gross includes tax, so this overstates the
            # entry price wherever a transaction tax applies.
            price = gross / shares
            estimated = True

    date_s, time_s = _split_timestamp(item.get('lastEventAt'),
                                      _filled_local_time(detail or {}))
    return {
        'date':        date_s,
        'time':        time_s,
        'status':      _STATUS_MAP.get(str(item.get('status', '')).upper(),
                                       str(item.get('status', '')).title()),
        'reference':   str(item.get('id', '')),
        'description': str(item.get('description', '')),
        'assetType':   'Security',
        'type':        _SIDE_MAP.get(str(sec.get('side', '')).upper(), ''),
        'isin':        str(sec.get('isin') or ''),
        'shares':      _format_de_number(shares, 6),
        'price':       _format_de_number(price, 4),
        'amount':      _format_de_number(gross, 2),
        'fee':         _format_de_number(fee, 2),
        'tax':         _format_de_number(tax, 2),
        'currency':    str(item.get('currency', 'EUR')),
        'price_estimated': estimated,
    }


def _cash_row(item: dict) -> dict:
    """Map one cash transaction (dividend, interest, fee, transfer)."""
    cash = item.get('cash') or {}
    raw_type = str(cash.get('transactionType', '')).upper()
    date_s, time_s = _split_timestamp(item.get('lastEventAt'))
    return {
        'date':        date_s,
        'time':        time_s,
        'status':      _STATUS_MAP.get(str(item.get('status', '')).upper(),
                                       str(item.get('status', '')).title()),
        'reference':   str(item.get('id', '')),
        'description': str(item.get('description', '')),
        'assetType':   'Cash',
        # Unknown types keep a readable label and surface in the parser's
        # "unrecognised type" bucket instead of being silently dropped.
        'type':        _CASH_TYPE_MAP.get(raw_type, raw_type.replace('_', ' ').capitalize()),
        'isin':        str(cash.get('relatedIsin') or ''),
        'shares':      '',
        'price':       '',
        'amount':      _format_de_number(_num(cash.get('amount')), 2),
        'fee':         '',
        'tax':         '',
        'currency':    str(item.get('currency', 'EUR')),
        'price_estimated': False,
    }


def mcp_to_scalable_frame(payloads, details=None, include_crypto: bool = False) -> pd.DataFrame:
    """Convert MCP transaction payloads into a Scalable-CSV-shaped DataFrame.

    :param payloads:       one list_portfolio_transactions payload, or a list of pages
    :param details:        optional get_transaction_details payloads — without them
                           price and tax are estimated from the gross amount
    :param include_crypto: map the parallel ``crypto`` block as well. Off by
                           default: crypto entries carry no ISIN, so they cannot
                           be resolved to a ticker and would only add noise.

    The returned frame carries one extra column beyond the CSV layout,
    ``price_estimated``, so callers can warn about rows priced without details.
    """
    if isinstance(payloads, dict):
        payloads = [payloads]
    detail_map = index_details(details)

    rows = []
    for page in payloads or []:
        if not isinstance(page, dict):
            continue
        items = list(page.get('transactions') or [])
        if include_crypto:
            crypto = (page.get('crypto') or {}).get('transactions') or []
            items += [dict(c, kind='crypto') for c in crypto]

        for item in items:
            if not isinstance(item, dict):
                continue
            kind = str(item.get('kind', '')).lower()
            try:
                if kind == 'cash' or item.get('cash'):
                    rows.append(_cash_row(item))
                elif kind == 'crypto':
                    # Crypto uses coinQuantity for the actual coin amount; the
                    # ETP-style `quantity` is a different unit entirely.
                    item = dict(item)
                    item['security'] = {
                        'isin': '', 'quantity': item.get('coinQuantity'),
                        'amount': item.get('amount'), 'side': item.get('side'),
                    }
                    rows.append(_security_row(item, {}))
                else:
                    rows.append(_security_row(item, detail_map.get(str(item.get('id', '')), {})))
            except Exception as e:
                logger.warning('Scalable MCP: row skipped (%s): %s', item.get('id'), e)

    if not rows:
        return pd.DataFrame(columns=SCALABLE_CSV_COLUMNS + ['price_estimated'])

    df = pd.DataFrame(rows)
    # Deduplicate: overlapping pages repeat transactions, and the id is stable.
    df = df.drop_duplicates(subset=['reference'], keep='first').reset_index(drop=True)
    return df[SCALABLE_CSV_COLUMNS + ['price_estimated']]


def transaction_ids_needing_details(payloads) -> list:
    """Ids of buy/sell rows whose price and tax require get_transaction_details.

    Use this to drive the detail fetch: cash rows never need one, so a sync only
    pays for the trades it actually imports.
    """
    if isinstance(payloads, dict):
        payloads = [payloads]
    ids = []
    for page in payloads or []:
        for item in (page.get('transactions') or []) if isinstance(page, dict) else []:
            if isinstance(item, dict) and item.get('security') and item.get('id'):
                ids.append(str(item['id']))
    return list(dict.fromkeys(ids))


def write_scalable_csv(df: pd.DataFrame, path: str) -> str:
    """Write the mapped frame as a Scalable-style CSV (semicolon separated).

    Lets a captured MCP payload be fed through the existing CSV uploader
    unchanged, which is the quickest way to use this without a native client.
    """
    df[SCALABLE_CSV_COLUMNS].to_csv(path, sep=';', index=False, encoding='utf-8-sig')
    return path
