"""Causal Elliott wave counting for the EWO indicator.

Two stages, both strictly causal:

1. ``pivots()`` — an ATR zigzag. A swing high/low only becomes a pivot on the
   bar where the close has moved ``mult × ATR`` away from it; the pivot keeps the
   date of the extreme but carries its confirmation bar. Consecutive pivots never
   share a bar: when the reversal happens on the extreme bar itself, the order of
   high and low inside that bar is unknown, so the opposite extreme is searched
   from the next bar on.
2. ``count_waves()`` — walks the confirmed pivots in confirmation order and tries
   to fit an impulse 1-2-3-4-5 followed by an A-B-C correction.

Impulse, hard rules (a count is dropped as soon as one breaks):
  - wave 2 does not retrace beyond the start of wave 1
  - wave 3 goes beyond the end of wave 1
  - wave 4 does not enter the price territory of wave 1
  - wave 3 is not the shortest of 1, 3 and 5
  - wave 5 goes beyond the end of wave 3 — unless ``allow_truncation``: the
    rulebook allows a truncated fifth, but without sub-wave analysis it cannot be
    told apart from any small bounce after wave 4, hence opt-in.
EWO guidelines after Tom Joseph (optional):
  - ``require_ewo_peak``: the EWO extreme of wave 3 is at least as large as those
    of waves 1 and 5 (wave 5 therefore shows the classic divergence)
  - ``require_w4_ewo_zero``: the EWO pulls back to or through the zero line
    between the end of wave 3 and the end of wave 5

Correction after the impulse (B measured as retracement of A):
  - zigzag (5-3-5): B retraces less than ``FLAT_B_MIN`` of A
  - flat (3-3-5): B retraces at least ``FLAT_B_MIN`` of A; an expanded flat may
    go beyond the start of A (= end of wave 5) up to ``FLAT_B_MAX``
  - both: C ends beyond the end of A
  A B beyond ``FLAT_B_MAX`` or a C short of A ends the correction reading; the
  pivots from A on are recounted as a possible new impulse. Running flats,
  truncated Cs, triangles, combinations and diagonals are not recognised, and
  sub-waves are not checked — one degree per ATR multiple.

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
FLAT_B_MIN = 0.9      # Frost/Prechter: in a flat, B retraces at least 90 % of A
FLAT_B_MAX = 1.382    # guideline ceiling for the B wave of an expanded flat


def atr(high, low, close, period=14) -> np.ndarray:
    """Wilder ATR as a numpy array (first bar uses high-low)."""
    high = pd.Series(np.asarray(high, dtype=float))
    low = pd.Series(np.asarray(low, dtype=float))
    close = pd.Series(np.asarray(close, dtype=float))
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()


def joseph_ewo(df) -> np.ndarray:
    """EWO after Tom Joseph: SMA5 - SMA35 of the median price (High+Low)/2."""
    close = pd.to_numeric(df['Close'], errors='coerce')
    if 'High' in df and 'Low' in df:
        mid = (pd.to_numeric(df['High'], errors='coerce') + pd.to_numeric(df['Low'], errors='coerce')) / 2
    else:
        mid = close
    return (mid.rolling(5).mean() - mid.rolling(35).mean()).to_numpy(dtype=float)


def pivots(high, low, close, atr_arr, mult):
    """Causal ATR zigzag.

    Returns ``(confirmed, pending)``: ``confirmed`` is a list of dicts
    ``{i, c, price, kind}`` (``i`` extreme bar, ``c`` confirmation bar, ``kind``
    +1 high / -1 low) in confirmation order, with strictly increasing ``i``;
    ``pending`` is the running, unconfirmed extreme (same keys, ``c`` None) or
    None.
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

    def _after(arr, pick, start, end, empty):
        """Extreme of arr over (start, end]; ``empty`` when that range is empty."""
        seg = arr[start + 1:end + 1]
        if seg.size == 0 or np.all(np.isnan(seg)):
            return empty, -1
        j = int(pick(seg))
        return seg[j], start + 1 + j

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
                ext, ext_i = _after(low, np.nanargmin, hi_i, i, np.inf)
            elif c >= lo + step:
                out.append({'i': lo_i, 'c': i, 'price': lo, 'kind': -1})
                direction = 1
                ext, ext_i = _after(high, np.nanargmax, lo_i, i, -np.inf)
        elif direction > 0:
            if ext_i < i and h > ext:
                ext, ext_i = h, i
            if ext_i >= 0 and c <= ext - step:
                out.append({'i': ext_i, 'c': i, 'price': ext, 'kind': 1})
                direction = -1
                ext, ext_i = _after(low, np.nanargmin, ext_i, i, np.inf)
        else:
            if ext_i < i and l < ext:
                ext, ext_i = l, i
            if ext_i >= 0 and c >= ext + step:
                out.append({'i': ext_i, 'c': i, 'price': ext, 'kind': -1})
                direction = 1
                ext, ext_i = _after(high, np.nanargmax, ext_i, i, -np.inf)

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


def _trough(ewo, a, b, d):
    """Smallest direction-adjusted EWO value between bars a and b (inclusive)."""
    seg = d * ewo[min(a, b):max(a, b) + 1]
    if seg.size == 0 or np.all(np.isnan(seg)):
        return np.inf
    return float(np.nanmin(seg))


def _extends(cand, k, pv, ewo, opts):
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
        if s[5] <= s[3] and not opts['allow_truncation']:
            return False
        l1, l3, l5 = s[1] - s[0], s[3] - s[2], s[5] - s[4]
        if l3 < l1 and l3 < l5:
            return False
        bars = [pv[j]['i'] for j in idx]
        if opts['require_ewo_peak']:
            p1 = _peak(ewo, bars[0], bars[1], d)
            p3 = _peak(ewo, bars[2], bars[3], d)
            p5 = _peak(ewo, bars[4], bars[5], d)
            if not (p3 >= p1 and p3 >= p5):
                return False
        if opts['require_w4_ewo_zero'] and _trough(ewo, bars[3], bars[5], d) > 0:
            return False
        return True
    return False


def count_waves(pv, n, ewo, require_ewo_peak=True, pending=None,
                allow_truncation=False, require_w4_ewo_zero=False):
    """Fit impulse + correction on confirmed pivots.

    Returns ``(wave, labels)``: ``wave`` is an int array of length ``n``;
    ``labels`` a list of dicts ``{i, text, kind, group, state, form}`` with
    ``group`` 'impulse'/'abc', ``form`` 'zigzag'/'flat' for correction labels
    (None while unknown), and ``state`` 'done' (finished structure), 'running'
    (confirmed pivot of an unfinished count at the right edge) or 'pending'
    (the unconfirmed running extreme).
    """
    ewo = np.asarray(ewo, dtype=float)
    opts = {'require_ewo_peak': require_ewo_peak, 'allow_truncation': allow_truncation,
            'require_w4_ewo_zero': require_w4_ewo_zero}
    wave = np.zeros(n, dtype=int)
    labels = []
    st = {'cands': [], 'corr': None}   # search candidates / correction in progress

    def _label(j, text, group, state, form=None):
        labels.append({'i': pv[j]['i'], 'text': text, 'kind': pv[j]['kind'],
                       'group': group, 'state': state, 'form': form})

    def _search(k):
        keep = []
        for cand in st['cands']:
            if _extends(cand, k, pv, ewo, opts):
                new = {'dir': cand['dir'], 'idx': cand['idx'] + [k]}
                if len(new['idx']) == 6:
                    for w, j in enumerate(new['idx'][1:], start=1):
                        _label(j, str(w), 'impulse', 'done')
                    st['corr'] = {'dir': new['dir'], 'impulse': new['idx'], 'abc': [], 'form': None}
                    st['cands'] = []
                    return
                keep.append(new)
        # A low starts an up impulse, a high a down impulse.
        keep.append({'dir': -pv[k]['kind'], 'idx': [k]})
        st['cands'] = keep[-MAX_CANDIDATES:]

    def _replay(seq):
        """Abandon the correction reading and recount the given pivots."""
        st['corr'], st['cands'] = None, []
        for j in seq:
            _feed(j)

    def _correct(k):
        corr = st['corr']
        d, abc = corr['dir'], corr['abc']
        s5 = d * pv[corr['impulse'][5]]['price']
        if not abc:
            abc.append(k)                                   # A
            return
        s_a = d * pv[abc[0]]['price']
        s_k = d * pv[k]['price']
        if len(abc) == 1:                                   # B
            ratio = (s_k - s_a) / (s5 - s_a) if s5 != s_a else np.inf
            if ratio > FLAT_B_MAX:
                _replay([abc[0], k])
                return
            corr['form'] = 'flat' if ratio >= FLAT_B_MIN else 'zigzag'
            abc.append(k)
            return
        if s_k < s_a:                                       # C beyond the end of A
            for letter, j in zip(ABC, abc + [k]):
                _label(j, letter, 'abc', 'done', corr['form'])
            st['corr'], st['cands'] = None, []
            _search(k)
        else:
            _replay([abc[0], abc[1], k])

    def _feed(k):
        if st['corr'] is not None:
            _correct(k)
        else:
            _search(k)

    def _state():
        corr = st['corr']
        if corr is not None:
            return corr['dir'] * (6 + len(corr['abc']))
        if not st['cands']:
            return 0
        best = max(st['cands'], key=lambda c: len(c['idx']))   # first (oldest) on ties
        return best['dir'] * len(best['idx'])

    for k, p in enumerate(pv):
        _feed(k)
        end = pv[k + 1]['c'] if k + 1 < len(pv) else n
        wave[p['c']:end] = _state()

    # Unfinished structure at the right edge.
    corr = st['corr']
    if corr is not None:
        for letter, j in zip(ABC, corr['abc']):
            _label(j, letter, 'abc', 'running', corr['form'])
        if pending is not None and len(corr['abc']) < 3:
            labels.append({'i': pending['i'], 'text': ABC[len(corr['abc'])] + '?',
                           'kind': pending['kind'], 'group': 'abc', 'state': 'pending',
                           'form': corr['form']})
    elif st['cands']:
        best = max(st['cands'], key=lambda c: len(c['idx']))
        # Only a count with waves 1 and 2 confirmed says anything.
        if len(best['idx']) >= 3:
            for w, j in enumerate(best['idx'][1:], start=1):
                _label(j, str(w), 'impulse', 'running')
            if pending is not None:
                labels.append({'i': pending['i'], 'text': f"{len(best['idx'])}?",
                               'kind': pending['kind'], 'group': 'impulse', 'state': 'pending',
                               'form': None})
    return wave, labels


def elliott(df, ewo, mult=3.0, require_ewo_peak=True, atr_period=14,
            allow_truncation=False, require_w4_ewo_zero=False):
    """Run pivots + count on a price frame. Returns ``(wave, labels)``."""
    close = pd.to_numeric(df['Close'], errors='coerce').to_numpy(dtype=float)
    high = pd.to_numeric(df['High'], errors='coerce').to_numpy(dtype=float) if 'High' in df else close
    low = pd.to_numeric(df['Low'], errors='coerce').to_numpy(dtype=float) if 'Low' in df else close
    atr_arr = atr(high, low, close, atr_period)
    pv, pending = pivots(high, low, close, atr_arr, mult)
    return count_waves(pv, len(close), np.asarray(ewo, dtype=float),
                       require_ewo_peak=require_ewo_peak, pending=pending,
                       allow_truncation=allow_truncation,
                       require_w4_ewo_zero=require_w4_ewo_zero)
