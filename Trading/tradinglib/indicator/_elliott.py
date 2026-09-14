"""Causal Elliott wave counting for the EWO indicator.

Two stages, both strictly causal:

1. ``pivots()`` — an ATR zigzag. A swing high/low only becomes a pivot on the
   bar where the close has moved ``mult × ATR`` away from it; the pivot keeps the
   date of the extreme but carries its confirmation bar.
2. ``count_waves()`` — walks the confirmed pivots in confirmation order and tries
   to fit an impulse 1-2-3-4-5 followed by an A-B-C correction.

Hard rules (a count is dropped as soon as one breaks):
  - wave 2 does not retrace beyond the start of wave 1
  - wave 3 goes beyond the end of wave 1, wave 5 beyond the end of wave 3
  - wave 4 does not enter the price territory of wave 1
  - wave 3 is not the shortest of 1, 3 and 5
EWO guideline (optional, ``require_ewo_peak``):
  - the EWO extreme of wave 3 is at least as large as those of waves 1 and 5
    (wave 5 therefore shows the classic divergence)

A count is one rule-conforming reading, not "the" count — Elliott counts are
ambiguous by nature. ``wave`` per bar only uses pivots confirmed up to that bar,
so the column is safe for buy/sell formulas and backtests; the chart labels sit
on the extreme bars and are hindsight, like every zigzag leg.

Encoding of ``wave``: +1..+5 = wave of an up impulse in progress, +6/+7/+8 =
correction A/B/C in progress after an up impulse; negative values mirror that
for down impulses; 0 = no pivot confirmed yet.
"""
import numpy as np
import pandas as pd

MAX_CANDIDATES = 12
ABC = ('A', 'B', 'C')


def atr(high, low, close, period=14) -> np.ndarray:
    """Wilder ATR as a numpy array (first bar uses high-low)."""
    high = pd.Series(np.asarray(high, dtype=float))
    low = pd.Series(np.asarray(low, dtype=float))
    close = pd.Series(np.asarray(close, dtype=float))
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()


def pivots(high, low, close, atr_arr, mult):
    """Causal ATR zigzag.

    Returns ``(confirmed, pending)``: ``confirmed`` is a list of dicts
    ``{i, c, price, kind}`` (``i`` extreme bar, ``c`` confirmation bar, ``kind``
    +1 high / -1 low) in confirmation order; ``pending`` is the running,
    unconfirmed extreme (same keys, ``c`` None) or None.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    out = []
    direction = 0              # +1 looking for a high, -1 looking for a low
    hi, hi_i = -np.inf, -1
    lo, lo_i = np.inf, -1
    ext, ext_i = np.nan, -1

    def _running_low(start, end):
        seg = low[start:end + 1]
        if seg.size == 0 or np.all(np.isnan(seg)):
            return low[end], end
        j = int(np.nanargmin(seg))
        return seg[j], start + j

    def _running_high(start, end):
        seg = high[start:end + 1]
        if seg.size == 0 or np.all(np.isnan(seg)):
            return high[end], end
        j = int(np.nanargmax(seg))
        return seg[j], start + j

    for i in range(n):
        h, l, c = high[i], low[i], close[i]
        if np.isnan(c) or np.isnan(h) or np.isnan(l):
            continue
        a = atr_arr[i]
        step = mult * a if (a == a and a > 0) else np.inf

        if direction == 0:
            if h > hi:
                hi, hi_i = h, i
            if l < lo:
                lo, lo_i = l, i
            if c <= hi - step:
                out.append({'i': hi_i, 'c': i, 'price': hi, 'kind': 1})
                direction = -1
                ext, ext_i = _running_low(min(hi_i + 1, i), i)
            elif c >= lo + step:
                out.append({'i': lo_i, 'c': i, 'price': lo, 'kind': -1})
                direction = 1
                ext, ext_i = _running_high(min(lo_i + 1, i), i)
        elif direction > 0:
            if h > ext:
                ext, ext_i = h, i
            if c <= ext - step:
                out.append({'i': ext_i, 'c': i, 'price': ext, 'kind': 1})
                direction = -1
                ext, ext_i = _running_low(min(ext_i + 1, i), i)
        else:
            if l < ext:
                ext, ext_i = l, i
            if c >= ext + step:
                out.append({'i': ext_i, 'c': i, 'price': ext, 'kind': -1})
                direction = 1
                ext, ext_i = _running_high(min(ext_i + 1, i), i)

    pending = None
    if direction != 0 and ext_i >= 0:
        pending = {'i': ext_i, 'c': None, 'price': ext, 'kind': direction}
    return out, pending


def _peak(ewo, a, b, d):
    """Largest direction-adjusted EWO value between bars a and b (inclusive)."""
    seg = d * ewo[min(a, b):max(a, b) + 1]
    if seg.size == 0 or np.all(np.isnan(seg)):
        return -np.inf
    return float(np.nanmax(seg))


def _extends(cand, k, pv, ewo, require_ewo_peak):
    """True when pivot k is a rule-conforming next wave of the candidate."""
    d = cand['dir']
    idx = cand['idx'] + [k]
    m = len(idx) - 1
    s = [d * pv[j]['price'] for j in idx]
    if m == 1:
        return s[1] > s[0]
    if m == 2:
        return s[2] > s[0]
    if m == 3:
        return s[3] > s[1]
    if m == 4:
        return s[4] > s[1]
    if m == 5:
        if s[5] <= s[3]:
            return False
        l1, l3, l5 = s[1] - s[0], s[3] - s[2], s[5] - s[4]
        if l3 < l1 and l3 < l5:
            return False
        if require_ewo_peak:
            bars = [pv[j]['i'] for j in idx]
            p1 = _peak(ewo, bars[0], bars[1], d)
            p3 = _peak(ewo, bars[2], bars[3], d)
            p5 = _peak(ewo, bars[4], bars[5], d)
            if not (p3 >= p1 and p3 >= p5):
                return False
        return True
    return False


def count_waves(pv, n, ewo, require_ewo_peak=True, pending=None):
    """Fit impulse + correction on confirmed pivots.

    Returns ``(wave, labels)``: ``wave`` is an int array of length ``n``;
    ``labels`` a list of dicts ``{i, text, kind, group, state}`` with
    ``group`` 'impulse'/'abc' and ``state`` 'done' (finished structure),
    'running' (confirmed pivot of an unfinished count at the right edge) or
    'pending' (the unconfirmed running extreme).
    """
    ewo = np.asarray(ewo, dtype=float)
    wave = np.zeros(n, dtype=int)
    labels = []
    cands = []           # search mode: [{'dir', 'idx'}]
    corr = None          # correction mode: {'dir', 'impulse', 'abc'}

    def _search(k):
        nonlocal cands, corr
        keep = []
        for cand in cands:
            if _extends(cand, k, pv, ewo, require_ewo_peak):
                new = {'dir': cand['dir'], 'idx': cand['idx'] + [k]}
                if len(new['idx']) == 6:
                    for w, j in enumerate(new['idx'][1:], start=1):
                        labels.append({'i': pv[j]['i'], 'text': str(w), 'kind': pv[j]['kind'],
                                       'group': 'impulse', 'state': 'done'})
                    corr = {'dir': new['dir'], 'impulse': new['idx'], 'abc': []}
                    cands = []
                    return
                keep.append(new)
        # A low starts an up impulse, a high a down impulse.
        keep.append({'dir': -pv[k]['kind'], 'idx': [k]})
        cands = keep[-MAX_CANDIDATES:]

    def _state():
        if corr is not None:
            return corr['dir'] * (6 + len(corr['abc']))
        if not cands:
            return 0
        best = max(cands, key=lambda c: len(c['idx']))   # first (oldest) on ties
        return best['dir'] * len(best['idx'])

    for k, p in enumerate(pv):
        if corr is not None:
            d = corr['dir']
            abc = corr['abc']
            if len(abc) == 1 and d * p['price'] >= d * pv[corr['impulse'][5]]['price']:
                # B beyond the end of wave 5: no correction — recount from A.
                a = abc[0]
                corr, cands = None, []
                _search(a)
                _search(k)
            else:
                abc.append(k)
                if len(abc) == 3:
                    for letter, j in zip(ABC, abc):
                        labels.append({'i': pv[j]['i'], 'text': letter, 'kind': pv[j]['kind'],
                                       'group': 'abc', 'state': 'done'})
                    corr, cands = None, []
                    _search(k)
        else:
            _search(k)
        end = pv[k + 1]['c'] if k + 1 < len(pv) else n
        wave[p['c']:end] = _state()

    # Unfinished structure at the right edge.
    if corr is not None:
        for letter, j in zip(ABC, corr['abc']):
            labels.append({'i': pv[j]['i'], 'text': letter, 'kind': pv[j]['kind'],
                           'group': 'abc', 'state': 'running'})
        if pending is not None and len(corr['abc']) < 3:
            labels.append({'i': pending['i'], 'text': ABC[len(corr['abc'])] + '?',
                           'kind': pending['kind'], 'group': 'abc', 'state': 'pending'})
    elif cands:
        best = max(cands, key=lambda c: len(c['idx']))
        # Only a count with waves 1 and 2 confirmed says anything.
        if len(best['idx']) >= 3:
            for w, j in enumerate(best['idx'][1:], start=1):
                labels.append({'i': pv[j]['i'], 'text': str(w), 'kind': pv[j]['kind'],
                               'group': 'impulse', 'state': 'running'})
            if pending is not None:
                labels.append({'i': pending['i'], 'text': f"{len(best['idx'])}?",
                               'kind': pending['kind'], 'group': 'impulse', 'state': 'pending'})
    return wave, labels


def elliott(df, ewo, mult=3.0, require_ewo_peak=True, atr_period=14):
    """Run pivots + count on a price frame. Returns ``(wave, labels)``."""
    close = pd.to_numeric(df['Close'], errors='coerce').to_numpy(dtype=float)
    high = pd.to_numeric(df['High'], errors='coerce').to_numpy(dtype=float) if 'High' in df else close
    low = pd.to_numeric(df['Low'], errors='coerce').to_numpy(dtype=float) if 'Low' in df else close
    atr_arr = atr(high, low, close, atr_period)
    pv, pending = pivots(high, low, close, atr_arr, mult)
    return count_waves(pv, len(close), np.asarray(ewo, dtype=float),
                       require_ewo_peak=require_ewo_peak, pending=pending)
