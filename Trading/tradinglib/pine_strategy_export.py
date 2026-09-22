"""
pine_strategy_export.py — Buy/Sell-Formeln aus `multi_transactions` in ein
handelbares TradingView Pine-Script-`strategy()` übersetzen.

Anders als `pine_exporter.py` (das Visualisierungs-`indicator()` für Overlay-/
Oszillator-Kombis erzeugt) übersetzt dieses Modul die technischen Buy/Sell-
Bedingungen der Strategien direkt in `strategy.entry` / `strategy.close`.

Grenzen (bewusst): Bedingungen, die auf den zusammengesetzten Score
`overallValueTrend` / `overallTrend` zugreifen, werden **abgelehnt** — dieser
Score mischt Fundamentaldaten (ROA, KGV, Analysten-Kursziele …) aus `asset_info`
ein, die TradingView nicht (oder nur verzögert/lückenhaft) liefert.
"""
import re


# ── Parameter: dieselben wie im Backtest ─────────────────────────────────────
# Die Multi-Strategien handeln auf den gespeicherten Spalten; asset_perf2 rechnet
# sie mit den Standardparametern der Indikator-Klassen (kein params_map). Die
# Chart-Einstellungen aus dem ⚙-Dialog gelten dort NICHT — deshalb werden hier
# die Klassen-Defaults gelesen, nicht die plugin_params.
_FALLBACK = {
    'ewo':    {'short_window': 5, 'long_window': 21, 'ema_span': 9,
               'wave_atr_mult': 3.0, 'require_ewo_peak': True,
               'require_w4_ewo_zero': False, 'allow_truncation': False,
               'wave_ewo_basis': 'ewo'},
    'rsi':    {'lookback': 8, 'window': 14},
    'macd':   {'window_fast': 12, 'window_slow': 26, 'window_sign': 9},
    'relvol': {'relvol_length': 21},
    'atc':    {'dev_multi': 2.0, 'use_gog_scale': False, 'lookback': 252},
    'obd':    {'ob_periods': 3, 'ob_threshold': 1.0, 'use_wicks': False,
               'ob_min_weight': 0, 'ob_weight_ref': 3.0, 'period': 21,
               'sr_window': 21, 'sr_zone': 1.0},
}


def _default(indicator: str, key: str):
    """Default param of an indicator class (= what the backtest used)."""
    try:
        import importlib
        mod = importlib.import_module(f'tradinglib.indicator.{indicator}')
        cls = getattr(mod, indicator.capitalize())
        return cls.params[key]['default']
    except Exception:
        return _FALLBACK[indicator][key]


def _defaults(indicator: str) -> dict:
    return {k: _default(indicator, k) for k in _FALLBACK[indicator]}


def _pine_bool(v) -> str:
    return 'true' if v else 'false'


# ── Pine-Helper (Funktionen/Konstanten, einmalig oben im Skript) ─────────────
_HELPERS: dict[str, str] = {
    'ha_base': (
        '// Heikin-Ashi-Basis (spiegelt heikin.py)\n'
        'ha_close = (open + high + low + close) / 4\n'
        'var float ha_open = na\n'
        'ha_open := na(ha_open[1]) ? (open + close) / 2 : (ha_open[1] + ha_close[1]) / 2\n'
        'ha_high = math.max(high, math.max(ha_open, ha_close))\n'
        'ha_low  = math.min(low,  math.min(ha_open, ha_close))'
    ),
}


def _helper_atc() -> str:
    """Causal ATC as atc.py: per bar the channel over the trailing `lookback` bars.

    High anchor = bars since the highest high, low anchor = since the lowest low,
    zero anchor = the length whose slope is closest to zero; channel value is the
    last point of that least-squares line ± dev × residual stdev. Prices live in
    arrays indexed by bar_index, so no history buffer is needed.
    """
    p = _defaults('atc')
    return f"""\
// ATC kausal (spiegelt atc.py): je Balken Kanal ueber die letzten atc_lb Balken
atc_dev = input.float({float(p['dev_multi'])}, "ATC Std-Abweichung", group="ATC")
atc_lb  = input.int({int(p['lookback'])}, "ATC Rueckblick je Balken", minval=20, group="ATC")
atc_log = input.bool({_pine_bool(p['use_gog_scale'])}, "ATC Log-Skala", group="ATC")
var array<float> atc_y = array.new<float>()
var array<float> atc_h = array.new<float>()
var array<float> atc_l = array.new<float>()
array.push(atc_y, atc_log ? math.log10(close) : close)
array.push(atc_h, high)
array.push(atc_l, low)
atc_wl  = math.min(atc_lb, bar_index + 1)
atc_min = math.max(10, int(atc_wl / 5))

atc_fit(int e, int len) =>
    // least squares over the bars e-len+1 .. e: [value at e, residual stdev]
    float sk = 0.0
    float skk = 0.0
    float sy = 0.0
    float syy = 0.0
    float sky = 0.0
    for k = 0 to len - 1
        float y = array.get(atc_y, e - k)
        sk += k
        skk += k * k
        sy += y
        syy += y * y
        sky += k * y
    float n = len
    float cov = sky - sk * sy / n
    float varx = skk - sk * sk / n
    float b = varx > 0 ? cov / varx : 0.0
    float ssr = (syy - sy * sy / n) - b * cov
    [(sy - b * sk) / n, math.sqrt(math.max(ssr, 0.0) / n)]

atc_len(int anchor, int e, int wl, int min_len) =>
    // 1 = high anchor, -1 = low anchor, 0 = flattest slope
    int length = wl
    int s = e - wl + 1
    if anchor != 0
        float best = na
        int bi = s
        for j = s to e
            float v = anchor == 1 ? array.get(atc_h, j) : array.get(atc_l, j)
            if not na(v) and (na(best) or (anchor == 1 ? v > best : v < best))
                best := v
                bi := j
        length := e - bi + 1
    else if wl > 2
        float sk = 0.0
        float skk = 0.0
        float sy = 0.0
        float sky = 0.0
        float best = na
        for k = 0 to wl - 2
            float y = array.get(atc_y, e - k)
            sk += k
            skk += k * k
            sy += y
            sky += k * y
            if k >= 1
                float n = k + 1
                float varx = skk - sk * sk / n
                float sl = varx > 0 ? math.abs((sky - sk * sy / n) / varx) : 0.0
                if na(best) or sl < best
                    best := sl
                    length := k + 1
    math.max(min_len, math.min(length, wl))

atc_calc(int anchor) =>
    float m = na
    float sd = na
    if atc_wl >= atc_min
        [a, s] = atc_fit(bar_index, atc_len(anchor, bar_index, atc_wl, atc_min))
        m := a
        sd := s
    [m, sd]

atc_px(float v) =>
    atc_log ? math.pow(10, v) : v"""


def _helper_trend() -> str:
    """trendScore as prof.py/risk_profile.trend_score: distance to the record high,
    piecewise linear over the calibrated knots."""
    try:
        from tradinglib import risk_profile as rp
        xs, ys = list(rp.TREND_BREAKPOINTS), list(rp.TREND_SCORES)
    except Exception:
        xs = [-1.0, -0.9076, -0.6362, -0.3876, -0.1811, -0.0361, 0.0]
        ys = [0.0, 5.0, 25.0, 50.0, 75.0, 95.0, 100.0]
    fx = ', '.join(str(float(x)) for x in xs)
    fy = ', '.join(str(float(y)) for y in ys)
    return f"""\
// trendScore (spiegelt prof.py): Abstand zum Rekordhoch -> 0..100 ueber die
// kalibrierten Stuetzstellen (risk_profile.TREND_BREAKPOINTS). Achtung: das
// Rekordhoch sieht nur die in TradingView geladene Historie.
var trend_x = array.from({fx})
var trend_y = array.from({fy})
trend_interp(float d) =>
    float r = na
    int n = array.size(trend_x)
    if not na(d)
        if d <= array.get(trend_x, 0)
            r := array.get(trend_y, 0)
        else if d >= array.get(trend_x, n - 1)
            r := array.get(trend_y, n - 1)
        else
            for i = 1 to n - 1
                if d <= array.get(trend_x, i)
                    float x0 = array.get(trend_x, i - 1)
                    float y0 = array.get(trend_y, i - 1)
                    r := y0 + (array.get(trend_y, i) - y0) * (d - x0) / (array.get(trend_x, i) - x0)
                    break
    r"""


def _helper_elliott() -> str:
    """Causal Elliott count (ewo_wave), shared with pine_exporter._elliott_core."""
    from tradinglib.pine_exporter import _elliott_core
    p = _defaults('ewo')
    basis = 'ewo_joseph' if p['wave_ewo_basis'] == 'joseph_5_35' else 'ewo'
    return (
        f"xew_mult     = {float(p['wave_atr_mult'])}\n"
        f"xew_req_peak = {_pine_bool(p['require_ewo_peak'])}\n"
        f"xew_req_w4   = {_pine_bool(p['require_w4_ewo_zero'])}\n"
        f"xew_trunc    = {_pine_bool(p['allow_truncation'])}\n"
        "ewo_joseph   = ta.sma(hl2, 5) - ta.sma(hl2, 35)\n"
        + _elliott_core('xew', basis, basis)
        + "xew_qclear(xew_st)"
    )


def _helper_obd() -> str:
    """Order Block Detector columns, shared with pine_exporter._obd_core."""
    from tradinglib.pine_exporter import _obd_core
    p = _defaults('obd')
    ref = float(p['ob_weight_ref']) or 3.0
    return (
        f"xobd_p      = {int(p['ob_periods'])}\n"
        f"xobd_thr    = {float(p['ob_threshold'])}\n"
        f"xobd_wicks  = {_pine_bool(p['use_wicks'])}\n"
        f"xobd_min_w  = {float(p['ob_min_weight'])}\n"
        f"xobd_ref    = {ref}\n"
        f"xobd_period = {int(p['period'])}\n"
        f"xobd_win    = {int(p['sr_window'])}\n"
        f"xobd_zone   = {float(p['sr_zone'])}\n"
        + _obd_core('xobd').rstrip('\n')
    )


# Helpers built from the indicator classes, rendered on demand.
_HELPER_BUILDERS = {
    'atc_base':  _helper_atc,
    'trend_ath': _helper_trend,
}


def _ewo_code(col: str) -> str:
    p = _defaults('ewo')
    return {
        'ewo':     f"ewo = ta.sma(close, {int(p['short_window'])}) - ta.sma(close, {int(p['long_window'])})",
        'ewo_ema': f"ewo_ema = ta.ema(ewo, {int(p['ema_span'])})",
    }[col]


def _rsi_code(col: str) -> str:
    p = _defaults('rsi')
    return {
        'rsi':     f"rsi = ta.rsi(close, {int(p['lookback'])})",
        'rsi_ema': f"rsi_ema = ta.sma(rsi, {int(p['window'])})",
    }[col]


def _macd_code() -> str:
    p = _defaults('macd')
    return (f"[macd, macd_signal, macd_diff] = ta.macd(close, {int(p['window_fast'])}, "
            f"{int(p['window_slow'])}, {int(p['window_sign'])})")


def _relvol_code() -> str:
    n = int(_defaults('relvol')['relvol_length'])
    return (f"relvol_past = ta.sma(volume, {n})[1]\n"
            "relvol_ratio = na(volume) or na(relvol_past) or relvol_past == 0 ? 1.0 : volume / relvol_past")


def _atc_anchor_code(anchor: str) -> str:
    """All causal ATC columns of one anchor (atc.py column names)."""
    a = {'high': 1, 'low': -1, 'zero': 0}[anchor]
    return (
        f"[atc_m_{anchor}, atc_s_{anchor}] = atc_calc({a})\n"
        f"atc_mid_{anchor} = atc_px(atc_m_{anchor})\n"
        f"atc_top_{anchor} = atc_px(atc_m_{anchor} + atc_dev * atc_s_{anchor})\n"
        f"atc_bot_{anchor} = atc_px(atc_m_{anchor} - atc_dev * atc_s_{anchor})\n"
        f"atc_width_{anchor} = atc_top_{anchor} - atc_bot_{anchor}\n"
        f"atc_width_pct_{anchor} = close != 0 ? atc_width_{anchor} / close * 100 : na"
    )

# ── Spalte → Pine-Berechnung ─────────────────────────────────────────────────
# Jede unterstützte Query-Spalte wird als gleichnamige Pine-Variable definiert,
# sodass die Ausdruck-Übersetzung 1:1 bleibt.
#   code    : Pine-Codeblock (mehrzeilig erlaubt); None = native Serie (close/…)
#   deps    : andere Spalten, die vorher definiert sein müssen
#   helpers : _HELPERS-Keys, die vorab emittiert werden müssen
_COL_DEFS: dict[str, dict] = {
    'close': {'code': None, 'deps': [], 'helpers': []},
    'open':  {'code': None, 'deps': [], 'helpers': []},
    'high':  {'code': None, 'deps': [], 'helpers': []},
    'low':   {'code': None, 'deps': [], 'helpers': []},

    'ewo':            {'code': lambda: _ewo_code('ewo'), 'deps': [], 'helpers': []},
    'ewo_ema':        {'code': lambda: _ewo_code('ewo_ema'), 'deps': ['ewo'], 'helpers': []},
    'ewo_angle':      {'code': 'ewo_angle = math.todegrees(math.atan(ewo - ewo[1]))',
                       'deps': ['ewo'], 'helpers': []},
    'ewo_diff':       {'code': 'ewo_diff = ewo_ema - ewo_ema[1]', 'deps': ['ewo_ema'], 'helpers': []},
    'ewo_trend':      {'code': 'ewo_trend = ewo - ewo[1] > 0 ? 1 : -1', 'deps': ['ewo'], 'helpers': []},
    '_elliott':       {'code': _helper_elliott, 'deps': ['ewo'], 'helpers': []},
    'ewo_wave':       {'code': 'ewo_wave = xew_wave', 'deps': ['_elliott'], 'helpers': []},
    'sup_support':    {'code': 'sup_support = ta.lowest(low, 21)', 'deps': [], 'helpers': []},
    'sup_resistance': {'code': 'sup_resistance = ta.highest(high, 21)', 'deps': [], 'helpers': []},
    'ema9':           {'code': 'ema9 = ta.ema(close, 9)', 'deps': [], 'helpers': []},
    'ema21':          {'code': 'ema21 = ta.ema(close, 21)', 'deps': [], 'helpers': []},
    'rsi':            {'code': lambda: _rsi_code('rsi'), 'deps': [], 'helpers': []},
    'rsi_ema':        {'code': lambda: _rsi_code('rsi_ema'), 'deps': ['rsi'], 'helpers': []},
    'momentum':       {'code': 'momentum = ta.stoch(close, high, low, 14)', 'deps': [], 'helpers': []},

    # ── MACD / relatives Volumen (Defaults wie der Backtest) ──────────────────
    '_macd':          {'code': _macd_code, 'deps': [], 'helpers': []},
    'macd':           {'code': None, 'deps': ['_macd'], 'helpers': []},
    'macd_signal':    {'code': None, 'deps': ['_macd'], 'helpers': []},
    'macd_diff':      {'code': None, 'deps': ['_macd'], 'helpers': []},
    'macd_trend':     {'code': 'macd_trend = (macd_diff - macd_diff[1] > 0 ? 0.5 : -0.5) + (macd > macd_signal ? 0.5 : -0.5)',
                       'deps': ['_macd'], 'helpers': []},
    'relvol_ratio':   {'code': _relvol_code, 'deps': [], 'helpers': []},
    'relvol_direction': {'code': 'relvol_direction = close > open ? 1 : close < open ? -1 : 0',
                         'deps': [], 'helpers': []},

    # ── trendScore (prof.py) ──────────────────────────────────────────────────
    'trendScore':     {'code': 'trend_ath = ta.max(close)\n'
                               'trendScore = trend_interp(trend_ath > 0 ? close / trend_ath - 1.0 : na)',
                       'deps': [], 'helpers': ['trend_ath']},

    # ── Order Block Detector (obd.py, naechste aktive Zone je Seite) ─────────
    '_obd':           {'code': _helper_obd, 'deps': [], 'helpers': []},
    **{col: {'code': f'{col} = x{col}', 'deps': ['_obd'], 'helpers': []}
       for col in ('obd_bull_top', 'obd_bull_bot', 'obd_bull_weight',
                   'obd_bear_top', 'obd_bear_bot', 'obd_bear_weight',
                   'obd_buy', 'obd_sell')},

    # ── Heikin Ashi (Basis via ha_base-Helper; EMA-Länge 21 wie heikin.py) ────
    'ha_close':    {'code': None, 'deps': [], 'helpers': ['ha_base']},
    'ha_open':     {'code': None, 'deps': [], 'helpers': ['ha_base']},
    'ha_ema_high': {'code': 'ha_ema_high = ta.ema(ha_high, 21)', 'deps': [], 'helpers': ['ha_base']},
    'ha_ema_low':  {'code': 'ha_ema_low = ta.ema(ha_low, 21)',  'deps': [], 'helpers': ['ha_base']},

    'dTrend':  {'code': 'dTrend = close != close[1] ? (close - close[1]) / close * 100 : 0.0',
                'deps': [], 'helpers': []},
    '_wk_close': {'code': '_wk_close = request.security(syminfo.tickerid, "W", close, '
                          'lookahead=barmerge.lookahead_off)', 'deps': [], 'helpers': []},
    'wkTrend': {'code': 'wkTrend = _wk_close != _wk_close[1] ? '
                        '(_wk_close - _wk_close[1]) / _wk_close * 100 : 0.0',
                'deps': ['_wk_close'], 'helpers': []},

    # ── ATC kausal (atc.py): je Anker ein Block mit allen sechs Spalten ───────
    **{f'_atc_{a}': {'code': (lambda a=a: _atc_anchor_code(a)), 'deps': [], 'helpers': ['atc_base']}
       for a in ('high', 'low', 'zero')},
    **{f'atc_{line}_{a}': {'code': None, 'deps': [f'_atc_{a}'], 'helpers': []}
       for a in ('high', 'low', 'zero')
       for line in ('mid', 'top', 'bot', 'width', 'width_pct')},
    # Aliase (atc.py: atc_low = atc_bot_low, atc_high = atc_top_high)
    'atc_high': {'code': 'atc_high = atc_top_high', 'deps': ['_atc_high'], 'helpers': []},
    'atc_low':  {'code': 'atc_low = atc_bot_low',  'deps': ['_atc_low'],  'helpers': []},
}

# Query-Spalten, die nicht rein technisch abbildbar sind.
_SCORE_REASON = ('Score enthaelt Fundamentaldaten aus asset_info (ROA, Margen, Kursziele …), '
                 'die TradingView nicht hat — auch overallTrend: Value-Punkte gegen technische '
                 'Gewichte (asset_perf2.Sum). Nur den technischen Teil zu exportieren waere eine '
                 'andere Strategie unter demselben Namen')
_UNSUPPORTED = {
    'overallValueTrend': _SCORE_REASON,
    'overallTrend':      _SCORE_REASON,
}

# Query verwendet Groß-OHLC (High/Low/Open); Pine nutzt Kleinschreibung.
_CASE_MAP = {'High': 'high', 'Low': 'low', 'Open': 'open', 'Close': 'close'}


class StrategyExportError(ValueError):
    """Raised when a buy/sell formula references non-exportable columns."""


def _referenced_columns(expr: str) -> set[str]:
    """Alle Bezeichner (Spaltennamen) eines Ausdrucks, case-normalisiert.

    Ein literales ``\\n`` (JSON-Form der Zeilentrennung) ist kein Bezeichner ``n``.
    """
    text = str(expr or '').replace('\\n', '\n')
    return {_CASE_MAP.get(t, t) for t in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', text)}


def _translate_expr(expr: str) -> str:
    """Python-Buy/Sell-Ausdruck → Pine-Boolescher-Ausdruck."""
    e = expr or ''
    for src, dst in _CASE_MAP.items():
        e = re.sub(rf'\b{src}\b', dst, e)
    e = re.sub(r'\[\s*-(\d+)\s*\]', r'[\1]', e)          # col[-1] → col[1]
    e = e.replace('&', ' and ').replace('|', ' or ').replace('~', ' not ')
    return re.sub(r'\s+', ' ', e).strip()


def _resolve(cols: set[str]) -> tuple[list[str], list[str]]:
    """Return (helper_blocks, def_blocks) in korrekter Reihenfolge, dedupliziert."""
    def_blocks: list[str] = []
    helper_keys: list[str] = []
    done: set[str] = set()

    def add(col: str):
        if col in done:
            return
        spec = _COL_DEFS[col]
        for d in spec['deps']:
            add(d)
        for h in spec['helpers']:
            if h not in helper_keys:
                helper_keys.append(h)
        done.add(col)
        code = spec['code']
        if callable(code):
            code = code()
        if code is not None:
            def_blocks.append(code)

    for c in sorted(cols):
        if c in _COL_DEFS:
            add(c)
    helper_blocks = [_HELPER_BUILDERS[k]() if k in _HELPER_BUILDERS else _HELPERS[k]
                     for k in helper_keys]
    return helper_blocks, def_blocks


def _split_conditions(expr: str) -> list[str]:
    """Split like the app (tools.split_conditions): one condition per line."""
    try:
        from tradinglib.tools import split_conditions
        return split_conditions(expr)
    except Exception:
        text = str(expr or '').replace('\\n', '\n')
        return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _paren(expr: str) -> str:
    """Wrap in parentheses unless the whole expression already is one group."""
    e = expr.strip()
    if e.startswith('(') and e.endswith(')'):
        depth = 0
        for i, ch in enumerate(e):
            depth += ch == '('
            depth -= ch == ')'
            if depth == 0 and i < len(e) - 1:
                break
        else:
            return e
    return f'({e})'


def _condition(expr: str, window: int = 1) -> str:
    """Formula → Pine bool, with the app's multi-line semantics.

    Several lines are ANDed (a newline must not become a bare space: ``(a) (b)``
    is no valid Pine). With ``signal_window`` > 1 each line only has to have been
    true within the last *window* bars, as tools._window_mask does.
    """
    lines = _split_conditions(expr)
    if not lines:
        return 'false'
    if len(lines) == 1:
        return _translate_expr(lines[0])
    if int(window or 1) > 1:
        return ' and '.join(f'(math.sum({_paren(_translate_expr(ln))} ? 1 : 0, {int(window)}) > 0)'
                            for ln in lines)
    return ' and '.join(_paren(_translate_expr(ln)) for ln in lines)


def export_strategy(name: str, buy: str, sell: str, signal_window: int = 1) -> str:
    """Vollständiges Pine-`strategy()`-Skript für eine Buy/Sell-Formel.

    Raises StrategyExportError, wenn eine Bedingung nicht-exportierbare Spalten
    referenziert (z. B. overallValueTrend).
    """
    cols = _referenced_columns(buy) | _referenced_columns(sell)
    unsupported = sorted(c for c in cols if c in _UNSUPPORTED)
    if unsupported:
        reasons = sorted({_UNSUPPORTED[c] for c in unsupported})
        raise StrategyExportError(
            f'{name}: nicht exportierbar ({", ".join(unsupported)}) — {"; ".join(reasons)}')
    unknown = [c for c in cols if c not in _COL_DEFS or c.startswith('_')]
    if unknown:
        raise StrategyExportError(f'{name}: unbekannte Spalten {sorted(unknown)}')

    helper_blocks, def_blocks = _resolve(cols)
    helpers = ('\n\n'.join(helper_blocks) + '\n\n') if helper_blocks else ''
    body = '\n\n'.join(def_blocks)
    window = int(signal_window or 1)
    window_note = (f"// Signalfenster {window}: bei mehrzeiligen Formeln muss jede Zeile\n"
                   f"// innerhalb der letzten {window} Balken erfuellt gewesen sein.\n"
                   if window > 1 and max(len(_split_conditions(buy)),
                                         len(_split_conditions(sell))) > 1 else '')

    return f"""//@version=5
strategy("{name}", overlay=true, default_qty_type=strategy.percent_of_equity,
     default_qty_value=100, calc_on_every_tick=false)

// ── Helfer ───────────────────────────────────────────────────────────────────
{helpers}// ── Indikatoren (aus den App-Formeln abgeleitet, Parameter wie im Backtest) ──
{body}

// ── Bedingungen ──────────────────────────────────────────────────────────────
{window_note}longCond = {_condition(buy, window)}
exitCond = {_condition(sell, window)}

if longCond and strategy.position_size == 0
    strategy.entry("Long", strategy.long)
if exitCond and strategy.position_size > 0
    strategy.close("Long")
"""


def export_from_config(transactions: dict) -> dict:
    """Für jede Strategie EIN Skript (erste Index-Bedingung als Repräsentant).

    Returns {strategy_name: {'pine': str} | {'error': str}}.
    """
    result: dict[str, dict] = {}
    for strat, idxs in transactions.items():
        first = next(iter(idxs.values()), {})
        try:
            result[strat] = {'pine': export_strategy(strat, first.get('buy', ''), first.get('sell', ''),
                                                     signal_window=first.get('signal_window') or 1)}
        except StrategyExportError as e:
            result[strat] = {'error': str(e)}
    return result


def _config_value(username: str, key: str):
    """Raw config.db value for '<username>:<key>' (respects TradingDB env), or None."""
    import sqlite3
    from tradinglib import tools
    path = tools.Tools().get_path(path='database', file_name='config.db')
    con = sqlite3.connect(path)
    try:
        row = con.execute("SELECT value FROM config WHERE key = ?",
                          (f'{username}:{key}',)).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def _load_transactions(username: str) -> dict:
    """Read a user's `multi_transactions` from config.db."""
    import ast
    raw = _config_value(username, 'multi_transactions')
    if not raw:
        return {}
    d = ast.literal_eval(raw)
    if isinstance(d, str):      # config-DB speichert den dict teils doppelt-kodiert
        d = ast.literal_eval(d)
    return d


def _load_query(username: str, key: str) -> str:
    """Read a single buy/sell query string; strips surrounding JSON quotes."""
    import ast
    raw = _config_value(username, key)
    if not raw:
        return ''
    try:                        # Werte sind i. d. R. als JSON-String ("...") abgelegt
        val = ast.literal_eval(raw)
        return val if isinstance(val, str) else str(raw)
    except Exception:
        return str(raw)


def _query_window(username: str) -> int:
    """Signal window of the standalone buy/sell query (tools.signal_window)."""
    try:
        from tradinglib import tools
        return int(tools.signal_window(username) or 1)
    except Exception:
        return 1


def main(argv=None) -> int:
    """CLI: TradingView-Strategie-Skripte aus der Config eines Users schreiben.

    python -m tradinglib.pine_strategy_export [/user:kurt] [/out:pine_export]
    """
    import sys
    import os
    import re
    argv = list(sys.argv[1:] if argv is None else argv)
    user, out = 'kurt', 'pine_export'
    for a in argv:
        if a.startswith('/user:'):
            user = a.split(':', 1)[1]
        elif a.startswith('/out:'):
            out = a.split(':', 1)[1]

    os.makedirs(out, exist_ok=True)
    slug = lambda s: re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_')
    ok = skipped = 0

    def _emit(name: str, pine: 'str | None', err: 'str | None'):
        nonlocal ok, skipped
        if pine is not None:
            fn = os.path.join(out, slug(name) + '.pine')
            with open(fn, 'w', encoding='utf-8') as fh:
                fh.write(pine)
            print(f"  OK        {name:26} -> {fn}")
            ok += 1
        else:
            print(f"  ABGELEHNT {name:26}    {err}")
            skipped += 1

    # 1) Strategien aus multi_transactions
    for strat, res in export_from_config(_load_transactions(user)).items():
        _emit(strat, res.get('pine'), res.get('error'))

    # 2) Eigenstaendige Einzel-Query in config.db (<user>:buy_query / :sell_query)
    buy, sell = _load_query(user, 'buy_query'), _load_query(user, 'sell_query')
    if buy or sell:
        try:
            _emit('buy_query', export_strategy('buy_query', buy, sell,
                                               signal_window=_query_window(user)), None)
        except StrategyExportError as e:
            _emit('buy_query', None, str(e))

    print(f"\n{ok} exportiert, {skipped} abgelehnt (nach {out}/).")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
