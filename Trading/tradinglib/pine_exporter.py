"""
Pine Script v5 exporter for TradingView.

Reads the active overlay / oscillator selection from MultiCheckboxSelector,
fetches stored parameter overrides from SystemConfig, and produces two
self-contained Pine Script v5 files:
    overlay_indicators.pine   — overlay=true  (renders on the price chart)
    oscillator_indicators.pine — overlay=false (renders in a separate pane)
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable


# Indicators that cannot be meaningfully translated to Pine Script:
#   pre    — loads an external ML model (predictlib); Pine Script has no model-loading
#   bar    — standard OHLC bar chart; use TradingView's native "Bars" chart type
#   candle — standard candlestick chart; use TradingView's native "Candles" chart type
_UNSUPPORTED: set[str] = {'pre', 'bar', 'candle'}

# ── License block prepended to every generated Pine Script ────────────────────
# Pine Script convention: "// ©" marks the copyright line (shown in TradingView).
_PINE_LICENSE = (
    "// © Arbor, your growth tool. — https://github.com/the-trading-tool/trading\n"
    "// For private, non-commercial use only. No financial advice.\n"
    "// Use at your own risk. Data accuracy depends on third-party providers.\n"
    "// Technical indicators implement published algorithms; see per-indicator\n"
    "// '// Attribution:' comments for original authors and sources.\n"
)

# Per-indicator attribution shown as the second comment line of each template.
# Markov is excluded here because it already carries its own // Author line.
_INDICATOR_ATTRIBUTION: dict[str, str] = {
    # ── Oscillators ──────────────────────────────────────────────────────────
    'adx':    'J. Welles Wilder Jr. — "New Concepts in Technical Trading Systems" (1978)',
    'cci':    'Donald Lambert (1980) — "Commodities Channel Index: Tool for Trading Cyclic Trends"',
    'cumd':   'Cumulative Volume Delta — standard order-flow / footprint-chart concept',
    'dema':   'Patrick Mulloy (1994) — "Smoothing Data with Faster Moving Averages"; TEMA: same',
    'ewo':    'Derived from Bill Williams "Awesome Oscillator"; popularised in Pine community',
    'hor':    'Custom indicator — standard deviation channel concept',
    'macd':   'Gerald Appel (1970s) — standard technical indicator',
    'relvol': 'Standard relative-volume concept (currentVol / avgVol[n])',
    'rsi':    'J. Welles Wilder Jr. — "New Concepts in Technical Trading Systems" (1978)',
    'stoch':  'George C. Lane (1950s) — Stochastic Oscillator',
    'vol':    'Volume Delta — standard order-flow concept',
    'zcr':    'Standard zero-crossing-rate concept (signal processing)',
    'hbo':    'Hull Butterfly Oscillator — custom Hull MA derivative',
    'scr':    'Seasonal Score Oscillator — custom forward-return seasonal average',
    # ── Overlays ─────────────────────────────────────────────────────────────
    'atc':    'Average True Channel — custom implementation',
    'atl':    'Auto Trend Lines — custom implementation',
    'bol':    'John Bollinger (1980s) — Bollinger Bands',
    'bos':    'ICT / Smart Money Concepts community — Break of Structure',
    'bsz':    'Buy/Sell Zone — custom implementation',
    'don':    'Richard Donchian (1960s) — Donchian Channel',
    'fib':    'Fibonacci retracement — standard technical analysis convention',
    'fvg':    'ICT / Smart Money Concepts community — Fair Value Gap',
    'gan':    'W.D. Gann (early 20th century) — Gann Levels / Gann Fan',
    'pvt':    'Classic Pivot Points — floor trader method (CME, 1980s); Fibonacci / Woodie variants standard TA',
    'heikin': 'Traditional Japanese candlestick technique — Heikin Ashi',
    'ici':    'Inner Circle Trader (ICT) concept — custom implementation',
    'lqz':    'ICT / Smart Money Concepts community — Liquidity Zone',
    'mam':    'MA Multi — custom implementation',
    'mmm':    'Market Mood Meter — custom implementation',
    'nsdt':   'NSDT (TradingView community) — Hama Candles concept',
    'oft':    'Order Flow Tools — custom implementation',
    'qtrend': 'Quantitative Trend — custom implementation',
    'gframa': 'QuantEdgeB — G-FRAMA (Gaussian-smoothed Fractal Adaptive Moving Average)',
    'renko':  'Renko chart — standard brick-based price-movement technique',
    'sup':    'Standard support / resistance detection concept',
    'vwap':   'Volume Weighted Average Price — standard market-microstructure concept',
    'wml':    'Weighted Momentum Levels — custom implementation',
    # markov is intentionally absent — its template already carries // Author
}


# ── Style helpers ──────────────────────────────────────────────────────────────

def _hex_to_pine_color(hex_str: str, fallback: str) -> str:
    """Convert a #RRGGBB Python hex color string to a Pine color.rgb() constant.
    Returns *fallback* (a Pine color expression) when hex_str is empty or invalid.
    """
    if hex_str and isinstance(hex_str, str) and hex_str.startswith('#') and len(hex_str) >= 7:
        try:
            r = int(hex_str[1:3], 16)
            g = int(hex_str[3:5], 16)
            b = int(hex_str[5:7], 16)
            return f'color.rgb({r}, {g}, {b})'
        except ValueError:
            pass
    return fallback


def _style_inputs(p: dict, prefix: str, default_color: str, group: str) -> str:
    """Return Pine Script v5 input declarations for color, width and line style.

    Generated variables:
      {prefix}_col    -- color  (default from saved params or *default_color*)
      {prefix}_width  -- line width 1..5
      {prefix}_style  -- "solid" / "dashed" / "dotted"
      {prefix}_lstyle -- resolved Pine line.style_* (for use in line.new())

    Parameters
    ----------
    p             : saved param dict; may contain 'line_color', 'line_width', 'line_style'
    prefix        : Pine variable prefix (e.g. 'rsi', 'macd', 'n_rsi' for normalized)
    default_color : fallback Pine color when line_color is not set (e.g. 'color.blue')
    group         : indicator name shown as TradingView Settings group header
    """
    col = _hex_to_pine_color(p.get('line_color', ''), default_color)
    wid = max(1, min(5, int(p.get('line_width', 2))))
    sty = p.get('line_style', 'solid')
    if sty not in ('solid', 'dashed', 'dotted'):
        sty = 'solid'
    grp = f"{group} -- Style"
    return (
        f'{prefix}_col    = input.color({col}, "Color",      group="{grp}")\n'
        f'{prefix}_width  = input.int({wid},   "Width",      minval=1, maxval=5, group="{grp}")\n'
        f'{prefix}_style  = input.string("{sty}", "Line style",'
        f' options=["solid","dashed","dotted"], group="{grp}")\n'
        f'{prefix}_lstyle = {prefix}_style == "dashed" ? line.style_dashed :'
        f' {prefix}_style == "dotted" ? line.style_dotted : line.style_solid\n'
    )


# ── Visibility-toggle helpers ─────────────────────────────────────────────────
# Applied by generate_overlay / generate_oscillator to add one input.bool per
# indicator so TradingView users can show / hide each series individually.

_INDICATOR_LABELS: dict[str, str] = {
    # Oscillators
    'adx':    'ADX',
    'cci':    'CCI',
    'cumd':   'CVD',
    'dema':   'DEMA',
    'ewo':    'EWO',
    'hor':    'Horcrux',
    'macd':   'MACD',
    'relvol': 'Relative Volume',
    'rsi':    'RSI',
    'stoch':  'Stochastic',
    'vol':    'Volume Delta',
    'zcr':    'Z-Score',
    'hbo':    'Hull Butterfly Oscillator',
    'scr':    'Seasonal Score',
    # Overlays
    'atc':    'Auto Trend Channels',
    'atl':    'Auto Trend Lines',
    'bol':    'Bollinger Bands',
    'bos':    'Break of Structure',
    'bsz':    'Buy/Sell Zones',
    'don':    'Donchian Channel',
    'fib':    'Fibonacci',
    'fvg':    'Fair Value Gap',
    'gan':    'Gann Levels',
    'heikin': 'Heikin Ashi',
    'ici':    'Ichimoku',
    'lqz':    'Liquidity Zones',
    'mam':    'MA Multi',
    'markov': 'Markov Regime',
    'mmm':    'Market Mood Meter',
    'nsdt':   'NSDT HAMA',
    'oft':    'Order Flow',
    'pvt':    'Pivot Points',
    'qtrend': 'Quantitative Trend',
    'gframa': 'G-FRAMA',
    'renko':  'Renko Candles',
    'sup':    'Support/Resistance',
    'vwap':   'VWAP',
    'wml':    'Week/Month Levels',
}

# Pine Script drawing primitives: when found inside an `if` block body they
# signal that the block must be gated by the visibility toggle.
_DRAWING_PRIMITIVES: frozenset[str] = frozenset([
    'line.new(', 'label.new(', 'line.delete(', 'label.delete(',
    'plotcandle(', 'plotshape(', 'plot(', 'fill(', 'bgcolor(',
    'table.cell(', 'table.set_',
])


def _find_first_arg_end(line: str, start: int) -> int:
    """Return the index of the first ',' at depth 0 after *start*, or of the
    matching closing ')' / ']' when no comma exists (single-arg call)."""
    depth = 0
    for i in range(start, len(line)):
        c = line[i]
        if c in '([':
            depth += 1
        elif c in ')]':
            if depth == 0:
                return i
            depth -= 1
        elif c == ',' and depth == 0:
            return i
    return len(line)


def _append_display_param(
    lines: list[str], start: int, toggle_var: str
) -> tuple[list[str], int]:
    """Collect a (possibly multi-line) drawing function call and append a
    ``display`` parameter so the output is hidden when the toggle is off.

    Tracks parenthesis depth to find the matching closing ``)`` across
    continuation lines, then inserts
    ``, display = toggle_var ? display.all : display.none``
    before it.

    Returns *(modified_lines, next_line_index)*.

    This is Pine Script v6's native way to conditionally hide plots — it avoids
    the ``na``-masking approach that can cause runtime errors (RE10140).
    """
    collected: list[str] = []
    depth = 0
    i = start

    while i < len(lines):
        ln = lines[i]
        collected.append(ln)
        for c in ln:
            if c in '([':
                depth += 1
            elif c in ')]':
                depth -= 1
        i += 1
        if depth <= 0 and collected:
            break

    if collected:
        last = collected[-1]
        rp = last.rfind(')')
        if rp >= 0:
            disp = f', display = {toggle_var} ? display.all : display.none'
            collected[-1] = last[:rp] + disp + last[rp:]

    return collected, i


def _hline_to_plot(line: str, toggle_var: str) -> str:
    """Convert ``hline(level, color=col, linestyle=…)`` to a ``plot()`` with
    a ``display`` parameter.

    ``hline()`` cannot be used in local scope in Pine Script v6.  The
    replacement ``plot(level, …, display = show_x ? display.all : display.none)``
    keeps the line at global scope and hides it via the ``display`` parameter.
    ``linestyle`` is dropped (``plot()`` has no dashed/dotted style).
    """
    m = re.search(r'\bhline\(', line)
    if not m:
        return line

    start = m.end()
    level_end = _find_first_arg_end(line, start)
    level = line[start:level_end].strip()

    rest = line[level_end:]
    col_m = re.search(r'\bcolor\s*=\s*(color\.\w+(?:\([^)]*\))?)', rest)
    color_expr = col_m.group(1) if col_m else 'color.gray'

    disp = f'{toggle_var} ? display.all : display.none'
    return f'plot({level}, "", {color_expr}, 1, plot.style_linebr, display = {disp})'


def _collect_if_block(lines: list[str], start: int) -> tuple[list[str], int]:
    """Collect an ``if / else-if / else`` block including its indented body.

    Returns *(block_lines, next_index)*.
    """
    block = [lines[start]]
    i = start + 1
    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()
        if not stripped:
            block.append(ln)
            i += 1
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent > 0 or stripped.startswith('else'):
            block.append(ln)
            i += 1
        else:
            break
    return block, i


def _block_has_drawing(block: list[str]) -> bool:
    """Return True when any line in *block* contains a Pine drawing primitive."""
    return any(
        any(prim in ln for prim in _DRAWING_PRIMITIVES)
        for ln in block
    )


def _add_visibility_toggle(name: str, body: str) -> str:
    """Prepend an ``input.bool`` visibility toggle to a Pine Script indicator block
    and gate all drawing calls with it.

    Transformations (applied only to top-level, zero-indent statements):

    * ``plot(expr, …)``           → ``plot(show_x ? (expr) : na, …)``
    * ``plotshape(expr, …)``      → ``plotshape(show_x ? (expr) : na, …)``
    * ``plot/plotshape/plotcandle/fill/bgcolor``
                                  → ``display = show_x ? display.all : display.none``
                                    appended (Pine Script v6 native visibility control,
                                    avoids ``na``-masking runtime errors like RE10140)
    * ``hline(level, …)``         → ``plot(level, …, display = show_x ? …)``
                                    (hline cannot be in local scope in Pine Script v6)
    * ``if barstate.islast``      → ``if show_x and barstate.islast``
    * ``if barstate.isconfirmed`` → ``if show_x and barstate.isconfirmed``
    * Any other top-level ``if`` whose body contains drawing calls
                                  → ``if show_x and (original_condition)``

    Everything else (variable assignments, ``var`` declarations, ``input.*``,
    function definitions) is left unchanged so that Pine Script's global-scope
    constraints are honoured and code order is preserved.
    """
    label = _INDICATOR_LABELS.get(name, name.upper())
    tv = f'show_{name}'
    decl = f'{tv} = input.bool(true, "Show {label}", group="Visibility")\n'

    result: list[str] = []
    lines = body.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        is_toplevel = (not stripped) or (len(line) - len(line.lstrip())) == 0

        if is_toplevel and stripped:
            # ── barstate condition blocks ──────────────────────────────────────
            if re.match(r'^if barstate\.(islast|isconfirmed)', stripped):
                result.append(
                    re.sub(r'\bif barstate\.', f'if {tv} and barstate.', line, count=1)
                )
                i += 1
                continue

            # ── hline → convert to plot() with display parameter ──────────────
            if re.match(r'^hline\(', stripped):
                result.append(_hline_to_plot(line, tv))
                i += 1
                continue

            # ── plot / plotshape / plotcandle / fill / bgcolor ─────────────────
            # Collect the full (possibly multi-line) call and append display param.
            if re.match(
                r'^(?:\w+\s*=\s*)?(plot|plotshape|plotcandle|fill|bgcolor)\(',
                stripped,
            ):
                call_lines, i = _append_display_param(lines, i, tv)
                result.extend(call_lines)
                continue

            # ── other top-level if blocks ──────────────────────────────────────
            if re.match(r'^if\s+', stripped):
                block, i = _collect_if_block(lines, i)
                if _block_has_drawing(block):
                    old_cond = block[0][3:].strip()  # everything after 'if '
                    block[0] = f'if {tv} and ({old_cond})'
                result.extend(block)
                continue

        result.append(line)
        i += 1

    return decl + '\n'.join(result)


# ── Oscillator templates ───────────────────────────────────────────────────────

def _t_macd(p: dict) -> str:
    """Return the Pine Script v5 MACD oscillator template with configurable params."""
    fast = int(p.get('window_fast', 12))
    slow = int(p.get('window_slow', 26))
    sign = int(p.get('window_sign', 9))
    col_macd   = _hex_to_pine_color(p.get('color_macd',   ''), 'color.black')
    col_signal = _hex_to_pine_color(p.get('color_signal', ''), 'color.blue')
    wid = max(1, min(5, int(p.get('line_width', 2))))
    sty = p.get('line_style', 'solid')
    if sty not in ('solid', 'dashed', 'dotted'):
        sty = 'solid'
    grp = "MACD -- Style"
    return f"""\
// ── MACD ──────────────────────────────────────────────────────────────────────
macd_col    = input.color({col_macd},   "MACD color",   group="{grp}")
macd_sig_col= input.color({col_signal}, "Signal color", group="{grp}")
macd_width  = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
macd_style  = input.string("{sty}", "Line style", options=["solid","dashed","dotted"], group="{grp}")
macd_lstyle = macd_style == "dashed" ? line.style_dashed : macd_style == "dotted" ? line.style_dotted : line.style_solid
macd_fast   = {fast}
macd_slow   = {slow}
macd_signal = {sign}
[macd_line, macd_sig, macd_hist] = ta.macd(close, macd_fast, macd_slow, macd_signal)
plot(macd_line, "MACD",      macd_col,     macd_width)
plot(macd_sig,  "Signal",    macd_sig_col, 1)
plot(macd_hist, "Histogram",
     color  = macd_hist >= 0 ? color.new(color.green, 30) : color.new(color.red, 30),
     style  = plot.style_columns)
"""


def _t_rsi(p: dict) -> str:
    """Return the Pine Script v5 RSI oscillator template with configurable params."""
    lb  = int(p.get('lookback', 8))
    win = int(p.get('window',   14))
    style = _style_inputs(p, 'rsi', 'color.blue', 'RSI')
    return f"""\
// ── RSI ───────────────────────────────────────────────────────────────────────
{style}rsi_lb  = {lb}
rsi_win = {win}
rsi_val = ta.rsi(close, rsi_lb)
rsi_ema = ta.sma(rsi_val, rsi_win)
plot(rsi_val, "RSI",     rsi_col,    rsi_width)
plot(rsi_ema, "RSI EMA", color.gray, 1)
hline(75, color = color.new(color.green, 0), linestyle = hline.style_dotted)
hline(50, color = color.new(color.gray,  0), linestyle = hline.style_dashed)
hline(25, color = color.new(color.red,   0), linestyle = hline.style_dotted)
"""


def _t_cci(p: dict) -> str:
    """Return the Pine Script v5 CCI oscillator template with configurable params."""
    win = int(p.get('window', 14))
    style = _style_inputs(p, 'cci', 'color.navy', 'CCI')
    return f"""\
// ── CCI ───────────────────────────────────────────────────────────────────────
{style}cci_win = {win}
cci_val = ta.cci(hlc3, cci_win)
plot(cci_val, "CCI", cci_col, cci_width)
hline( 100, color = color.new(color.green, 0), linestyle = hline.style_dashed)
hline(-100, color = color.new(color.red,   0), linestyle = hline.style_dashed)
"""


def _t_adx(p: dict) -> str:
    """Return the Pine Script v5 ADX oscillator template with configurable params."""
    win   = int(p.get('window',      14))
    fast  = int(p.get('window_fast', 12))
    slow  = int(p.get('window_slow', 26))
    level = int(p.get('down_level',  25))
    col_adx      = _hex_to_pine_color(p.get('color_adx',      ''), 'color.blue')
    col_plus_di  = _hex_to_pine_color(p.get('color_plus_di',  ''), 'color.green')
    col_minus_di = _hex_to_pine_color(p.get('color_minus_di', ''), 'color.red')
    wid = max(1, min(5, int(p.get('line_width', 2))))
    sty = p.get('line_style', 'solid')
    if sty not in ('solid', 'dashed', 'dotted'):
        sty = 'solid'
    grp = "ADX -- Style"
    return f"""\
// ── ADX ───────────────────────────────────────────────────────────────────────
adx_col      = input.color({col_adx},      "ADX color", group="{grp}")
adx_plus_col = input.color({col_plus_di},  "+DI color", group="{grp}")
adx_minus_col= input.color({col_minus_di}, "-DI color", group="{grp}")
adx_width    = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
adx_style    = input.string("{sty}", "Line style", options=["solid","dashed","dotted"], group="{grp}")
adx_lstyle   = adx_style == "dashed" ? line.style_dashed : adx_style == "dotted" ? line.style_dotted : line.style_solid
[adx_plus, adx_minus, adx_val] = ta.dmi({win}, {win})
[adx_macd, adx_sig, _]         = ta.macd(close, {fast}, {slow}, {win})
plot(adx_val,   "ADX",   adx_col,       adx_width)
plot(adx_plus,  "+DI",   adx_plus_col,  1)
plot(adx_minus, "-DI",   adx_minus_col, 1)
plot(adx_macd,  "MACD",  color.black,   1)
hline({level}, color = color.new(color.green, 0), linestyle = hline.style_dashed)
hline(75,       color = color.new(color.gray,  0), linestyle = hline.style_dashed)
"""


def _t_stoch(p: dict) -> str:
    """Return the Pine Script v5 Stochastic oscillator template with configurable params."""
    win = int(p.get('window',        14))
    sm  = int(p.get('smooth_window',  3))
    style = _style_inputs(p, 'stoch', 'color.black', 'Stochastic')
    return f"""\
// ── Stochastic ────────────────────────────────────────────────────────────────
{style}stoch_k = ta.stoch(close, high, low, {win})
stoch_d = ta.sma(stoch_k, {sm})
plot(stoch_k, "Stoch %K", stoch_col,  stoch_width)
plot(stoch_d, "Stoch %D", color.blue, 1)
hline(80, color = color.new(color.red,   0), linestyle = hline.style_dotted)
hline(20, color = color.new(color.green, 0), linestyle = hline.style_dotted)
"""


def _t_zcr(p: dict) -> str:
    """Return the Pine Script v5 Zero-Crossing Rate template with configurable params."""
    win = int(p.get('window', 20))
    style = _style_inputs(p, 'zcr', 'color.blue', 'Z-Score')
    return f"""\
// ── Z-Score ───────────────────────────────────────────────────────────────────
{style}zcr_mean = ta.sma(close,   {win})
zcr_std  = ta.stdev(close, {win})
zcr_val  = zcr_std != 0 ? (close - zcr_mean) / zcr_std : 0.0
plot(zcr_val, "Z-Score", zcr_col, zcr_width)
hline( 2, color = color.new(color.gray,  60), linestyle = hline.style_dashed)
hline( 1, color = color.new(color.red,    0), linestyle = hline.style_dashed)
hline(-1, color = color.new(color.green,  0), linestyle = hline.style_dashed)
hline(-2, color = color.new(color.gray,  60), linestyle = hline.style_dashed)
"""


def _t_hbo(p: dict) -> str:
    """Return the Pine Script v6 Hull Butterfly Oscillator template with configurable params."""
    length = int(p.get('length', 14))
    mult   = float(p.get('mult', 2.0))
    col_bull = _hex_to_pine_color(p.get('color_bull', ''), 'color.rgb(12, 181, 26)')
    col_bear = _hex_to_pine_color(p.get('color_bear', ''), 'color.rgb(255, 17, 0)')
    grp  = "HBO"
    grps = "HBO -- Style"
    return f"""\
// ── Hull Butterfly Oscillator ─────────────────────────────────────────────────
hbo_len      = input.int({length}, "Length", minval=4, maxval=200, group="{grp}")
hbo_mult     = input.float({mult:.1f}, "Levels multiplier", minval=0.1, maxval=10.0, step=0.1, group="{grp}")
hbo_col_bull = input.color({col_bull}, "Bull color", group="{grps}")
hbo_col_bear = input.color({col_bear}, "Bear color", group="{grps}")
f_wma_inv(src, length) =>
    den = float(length) * (length + 1) / 2.0
    s   = 0.0
    for i = 0 to length - 1
        s += src[i] * float(i + 1)
    s / den
hbo_half = int(hbo_len / 2)
hbo_hull = int(math.round(math.sqrt(float(hbo_len))))
hbo_lc   = 2.0 * f_wma_inv(close, hbo_half) - f_wma_inv(close, hbo_len)
max_bars_back(hbo_lc, 25)
hbo_hso  = f_wma_inv(hbo_lc, hbo_hull) - ta.hma(close, hbo_len)
var float hbo_abs_sum = 0.0
var int   hbo_cnt     = 0
hbo_abs_sum += na(hbo_hso) ? 0.0 : math.abs(hbo_hso)
hbo_cnt     += 1
hbo_cmean    = hbo_abs_sum / float(hbo_cnt) * hbo_mult
var int hbo_os = 0
_hbo_cross   = (not na(hbo_hso) and not na(hbo_hso[1]) and not na(hbo_cmean) and not na(hbo_cmean[1])) and ((hbo_hso[1] < hbo_cmean[1] and hbo_hso >= hbo_cmean) or (hbo_hso[1] > hbo_cmean[1] and hbo_hso <= hbo_cmean) or (hbo_hso[1] < -hbo_cmean[1] and hbo_hso >= -hbo_cmean) or (hbo_hso[1] > -hbo_cmean[1] and hbo_hso <= -hbo_cmean))
hbo_os := na(hbo_hso) ? 0 : _hbo_cross ? 0 : hbo_hso < hbo_hso[1] and hbo_hso > hbo_cmean ? -1 : hbo_hso > hbo_hso[1] and hbo_hso < -hbo_cmean ? 1 : hbo_os
hbo_bull = hbo_os == 1  and hbo_os[1] != 1
hbo_bear = hbo_os == -1 and hbo_os[1] != -1
plot(hbo_hso, "HBO bars", hbo_hso >= 0 ? color.new(hbo_col_bull, 35) : color.new(hbo_col_bear, 35), 1, plot.style_columns)
plot(hbo_hso, "HBO line", color.new(color.gray, 30), 1)
plot(hbo_cmean,      "+cmean",   color.new(color.gray, 50), 1)
plot(hbo_cmean / 2,  "+cmean/2", color.new(color.gray, 65), 1)
plot(-hbo_cmean / 2, "-cmean/2", color.new(color.gray, 65), 1)
plot(-hbo_cmean,     "-cmean",   color.new(color.gray, 50), 1)
plotshape(hbo_bull ? hbo_hso : na, "Bull", shape.circle, location.absolute, hbo_col_bull, size=size.small)
plotshape(hbo_bear ? hbo_hso : na, "Bear", shape.circle, location.absolute, hbo_col_bear, size=size.small)
plot(0, "Zero", color.new(color.gray, 50), 1)
"""


def _t_scr(p: dict) -> str:
    """Return the Pine Script v6 Seasonal Score Oscillator template with configurable params."""
    scale     = float(p.get('scale', 10.0))
    min_years = int(p.get('min_years', 3))
    threshold = float(p.get('threshold', 30.0))
    col_1mo   = _hex_to_pine_color(p.get('color_1mo',  ''), 'color.black')
    col_3mo   = _hex_to_pine_color(p.get('color_3mo',  ''), 'color.orange')
    col_eoy   = _hex_to_pine_color(p.get('color_eoy',  ''), 'color.gray')
    grp  = "Seasonal Score"
    grps = "Seasonal Score -- Style"
    return f"""\
// ── Seasonal Score Oscillator ─────────────────────────────────────────────────
// Requires 1800+ bars of history for the full 7-year lookback.
max_bars_back(close, 2000)
scr_scale     = input.float({scale:.1f}, "Normalisation scale (%)", minval=1.0, maxval=50.0, step=0.5, group="{grp}")
scr_min_years = input.int({min_years}, "Min. prior years", minval=2, maxval=10, group="{grp}")
scr_threshold = input.float({threshold:.1f}, "Buy/Sell threshold (+/-)", minval=5.0, maxval=90.0, step=5.0, group="{grp}")
scr_col_1mo   = input.color({col_1mo},  "1-month line",  group="{grps}")
scr_col_3mo   = input.color({col_3mo},  "3-month line",  group="{grps}")
scr_col_eoy   = input.color({col_eoy},  "Year-end line", group="{grps}")
f_scr_score(horizon_bars) =>
    s = 0.0
    n = 0
    for k = 1 to 7
        past = close[252 * k]
        fwd  = close[252 * k - horizon_bars]
        if not na(past) and not na(fwd) and past > 0
            s += (fwd / past - 1.0) * 100.0
            n += 1
    n >= scr_min_years ? math.max(-100.0, math.min(100.0, s / n / scr_scale * 100.0)) : na
scr_1mo = f_scr_score(21)
scr_3mo = f_scr_score(63)
scr_eoy = f_scr_score(252)
var bool scr_state = false
scr_buy_raw  = not na(scr_1mo) and scr_1mo >  scr_threshold
scr_sell_raw = not na(scr_1mo) and scr_1mo < -scr_threshold
scr_buy_sig  = scr_buy_raw  and not scr_state
scr_sell_sig = scr_sell_raw and scr_state
scr_state := scr_buy_sig ? true : scr_sell_sig ? false : scr_state
plot(scr_1mo, "SCR 1-month",  scr_col_1mo, 2)
plot(scr_3mo, "SCR 3-month",  scr_col_3mo, 2)
plot(scr_eoy, "SCR year-end", scr_col_eoy, 1, plot.style_line)
plotshape(scr_buy_sig  ? scr_1mo : na, "Buy",  shape.triangleup,   location.absolute, color.teal,   size=size.small)
plotshape(scr_sell_sig ? scr_1mo : na, "Sell", shape.triangledown, location.absolute, color.maroon, size=size.small)
plot( scr_threshold, "Buy threshold",  color.new(color.green, 50), 1)
plot(-scr_threshold, "Sell threshold", color.new(color.red,   50), 1)
plot(0, "Zero", color.new(color.gray, 50), 1)
"""


def _t_ewo(p: dict) -> str:
    """Return the Pine Script v5 Elliott Wave Oscillator template with configurable params."""
    style = _style_inputs(p, 'ewo', 'color.black', 'EWO')
    return f"""\
// ── Elliott Wave Oscillator ───────────────────────────────────────────────────
{style}ewo_val  = ta.sma(close, 5) - ta.sma(close, 21)
ewo_ema  = ta.ema(ewo_val, 9)
ewo_diff = ewo_ema - ewo_ema[1]
plot(ewo_diff, "EWO diff",
     style = plot.style_columns,
     color = ewo_diff >= 0 ? color.new(color.green, 30) : color.new(color.red, 30))
plot(ewo_val, "EWO",     ewo_col,      ewo_width)
plot(ewo_ema, "EWO EMA", color.orange, 1)
ewo_ang_thr  = 0.01
ewo_rising   = (ewo_val - ewo_val[1]) >  ewo_ang_thr
ewo_falling  = (ewo_val - ewo_val[1]) < -ewo_ang_thr
// Signal only on the first bar of each new direction (rising edge of state change)
ewo_buy  = ewo_rising  and not ewo_rising[1]
ewo_sell = ewo_falling and not ewo_falling[1]
plotshape(ewo_buy  ? ewo_val : na, "EWO Buy",  shape.triangleup,   location.absolute, color.teal, size = size.small)
plotshape(ewo_sell ? ewo_val : na, "EWO Sell", shape.triangledown, location.absolute, color.red,  size = size.small)
"""


def _t_dema(p: dict) -> str:
    """Return the Pine Script v5 DEMA oscillator template with configurable params."""
    fast  = int(p.get('fast_length', 8))
    slow  = int(p.get('slow_length', 21))
    win   = int(p.get('window',      14))
    cross = bool(p.get('show_cross', True))
    col_slow = _hex_to_pine_color(p.get('color_slow', ''), 'color.green')
    col_fast = _hex_to_pine_color(p.get('color_fast', ''), 'color.red')
    wid = max(1, min(5, int(p.get('line_width', 2))))
    sty = p.get('line_style', 'solid')
    if sty not in ('solid', 'dashed', 'dotted'):
        sty = 'solid'
    grp = "DEMA -- Style"
    code = f"""\
// ── DeMarker RSI (DEMA) ───────────────────────────────────────────────────────
dema_slow_col = input.color({col_slow}, "Slow EMA color", group="{grp}")
dema_fast_col = input.color({col_fast}, "Fast EMA color", group="{grp}")
dema_width    = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
dema_style    = input.string("{sty}", "Line style", options=["solid","dashed","dotted"], group="{grp}")
dema_lstyle   = dema_style == "dashed" ? line.style_dashed : dema_style == "dotted" ? line.style_dotted : line.style_solid
dema_src  = ta.rsi(close, {win})
dema_f1   = ta.ema(dema_src, {fast})
dema_fast = 2 * dema_f1 - ta.ema(dema_f1, {fast})
dema_s1   = ta.ema(dema_src, {slow})
dema_slow = 2 * dema_s1 - ta.ema(dema_s1, {slow})
dema_m1   = ta.ema(dema_src, {win})
dema_mid  = 2 * dema_m1 - ta.ema(dema_m1, {win})
plot(dema_slow, "DEMA Slow", dema_slow_col, dema_width)
plot(dema_fast, "DEMA Fast", dema_fast_col, dema_width)
plot(dema_mid,  "DEMA Mid",  color.black,   1)
hline(70, color = color.new(color.gray, 0), linestyle = hline.style_dotted)
hline(50, color = color.new(color.gray, 0), linestyle = hline.style_dotted)
hline(30, color = color.new(color.gray, 0), linestyle = hline.style_dotted)
"""
    if cross:
        code += """\
dema_buy_sig  = ta.crossover(dema_fast,  dema_slow)
dema_sell_sig = ta.crossunder(dema_fast, dema_slow)
plotshape(dema_buy_sig  ? dema_fast : na, "DEMA Buy",  shape.triangleup,   location.absolute, color.green, size = size.small)
plotshape(dema_sell_sig ? dema_fast : na, "DEMA Sell", shape.triangledown, location.absolute, color.red,   size = size.small)
"""
    return code


def _t_vol(p: dict) -> str:
    """Return the Pine Script v5 Volume Delta template with configurable params."""
    cumd = bool(p.get('cumd', False))
    col_bull = _hex_to_pine_color(p.get('color_bull', ''), 'color.new(color.green, 20)')
    col_bear = _hex_to_pine_color(p.get('color_bear', ''), 'color.new(color.red,   20)')
    wid  = max(1, min(5, int(p.get('line_width', 1))))
    grp  = "Volume -- Style"
    code = f"""\
// ── Volume ────────────────────────────────────────────────────────────────────
vol_col_bull = input.color({col_bull}, "Bull color", group="{grp}")
vol_col_bear = input.color({col_bear}, "Bear color", group="{grp}")
vol_width    = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
vol_bar_col  = close >= open ? vol_col_bull : vol_col_bear
plot(volume, "Volume", vol_bar_col, vol_width, style = plot.style_columns)
"""
    if cumd:
        code += """\
var float vol_cum = 0.0
vol_delta = close > close[1] ? volume : close < close[1] ? -volume : 0
vol_cum  += vol_delta
vol_sma   = ta.sma(vol_cum, 21)
plot(vol_cum, "CumDelta",     color.blue, 1)
plot(vol_sma, "CumDelta SMA", color.gray, 1)
"""
    return code


def _t_hor(p: dict) -> str:
    """Return the Pine Script v5 Horcrux (std-dev channel) template with configurable params."""
    bb_len = int(p.get('bb_length',  20))
    lb     = int(p.get('lookback',   50))
    adj    = float(p.get('adjustment', 0.0))
    style = _style_inputs(p, 'hor', 'color.black', 'Horcrux')
    return f"""\
// ── Horcrux Oscillator ───────────────────────────────────────────────────────
{style}hor_bb_len = {bb_len}
hor_lb     = {lb}
hor_adj    = {adj}
hor_mid_bb = ta.sma(close, hor_bb_len)
[_, hor_u0, hor_l0] = ta.bb(close, hor_bb_len, 0.5)
[_, hor_u1, hor_l1] = ta.bb(close, hor_bb_len, 1.0)
[_, hor_u2, hor_l2] = ta.bb(close, hor_bb_len, 1.5)
[_, hor_u3, hor_l3] = ta.bb(close, hor_bb_len, 2.0)
[_, hor_u4, hor_l4] = ta.bb(close, hor_bb_len, 2.5)
[_, hor_u5, hor_l5] = ta.bb(close, hor_bb_len, 3.0)
[_, hor_u6, hor_l6] = ta.bb(close, hor_bb_len, 3.5)
hor_state = high < hor_l6 ? 15 : high < hor_l5 ? 14 : high < hor_l4 ? 13 : high < hor_l3 ? 12 : high < hor_l2 ? 11 : high < hor_l1 ? 10 : high < hor_l0 ? 9 : high < hor_mid_bb ? 8 : high < hor_u0 ? 7 : high < hor_u1 ? 6 : high < hor_u2 ? 5 : high < hor_u3 ? 4 : high < hor_u4 ? 3 : high < hor_u5 ? 2 : high < hor_u6 ? 1 : 0
hor_smax = ta.highest(hor_state, hor_lb)
hor_smin = ta.lowest(hor_state,  hor_lb)
hor_val  = hor_smax - hor_state
hor_thr  = (hor_smax + hor_smin) / 2 * (1 + hor_adj / 100)
hor_bar_col = hor_val < hor_thr ? color.new(color.orange, 20) : color.new(color.lime, 20)
plot(hor_val, "Horcrux",   hor_col,    hor_width)
plot(hor_thr, "Threshold", color.blue, 2)
plot(hor_val, "Horcrux bars", style = plot.style_columns, color = hor_bar_col)
"""


def _t_relvol(p: dict) -> str:
    """Return the Pine Script v5 Relative Volume template with configurable params."""
    length = int(p.get('relvol_length', 21))
    ratio  = float(p.get('relvol_ratio', 1.0))
    mode   = str(p.get('relvol_mode', 'Regular'))
    if mode.lower() == 'cumulative':
        rv_cur  = "ta.cum(volume)"
        rv_past = f"ta.sum(volume[1], {length})"
    else:
        rv_cur  = "volume"
        rv_past = f"ta.sma(volume[1], {length})"
    col_bull = _hex_to_pine_color(p.get('color_bull', ''), 'color.new(color.green, 30)')
    col_bear = _hex_to_pine_color(p.get('color_bear', ''), 'color.new(color.red,   30)')
    wid  = max(1, min(5, int(p.get('line_width', 1))))
    grp  = "RelVol -- Style"
    return f"""\
// ── Relative Volume ───────────────────────────────────────────────────────────
relvol_col_bull = input.color({col_bull}, "Bull color", group="{grp}")
relvol_col_bear = input.color({col_bear}, "Bear color", group="{grp}")
relvol_width    = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
rv_thr     = {ratio}
rv_current = {rv_cur}
rv_past    = {rv_past}
rv_ratio   = rv_past != 0 ? rv_current / rv_past : 1.0
rv_bar     = rv_ratio - rv_thr
rv_col     = close >= open ? relvol_col_bull : relvol_col_bear
plot(rv_bar, "RelVol", style = plot.style_columns, color = rv_col, linewidth = relvol_width)
hline(0, color = color.new(color.green, 0), linestyle = hline.style_dotted)
"""


def _t_cumd(p: dict) -> str:
    """Return the Pine Script v5 Cumulative Volume Delta template with configurable params."""
    reset_mode = str(p.get('reset_mode', 'monthly')).lower()
    center     = bool(p.get('center_cumdelta', True))
    _tf_map    = {'auto': 'D', 'daily': 'D', 'monthly': 'M', 'none': ''}
    pine_tf    = _tf_map.get(reset_mode, 'M')
    tf_note    = f'(Pine timeframe.change("{pine_tf}"))' if pine_tf else '(no reset)'
    if pine_tf:
        accum_line = (
            f'cumd_new  = timeframe.change("{pine_tf}")\n'
            f'cumd_val := cumd_new ? cumd_delta : cumd_val + cumd_delta'
        )
    else:
        accum_line = 'cumd_val += cumd_delta'
    center_block = (
        '// center_cumdelta: subtract rolling 500-bar midpoint (approximation of global min/max)\n'
        'cumd_c    = (ta.highest(cumd_val, 500) + ta.lowest(cumd_val, 500)) / 2.0\n'
        'cumd_disp = cumd_val - cumd_c'
    ) if center else 'cumd_disp = cumd_val'
    col_bull = _hex_to_pine_color(p.get('color_bull', ''), 'color.new(color.teal,    20)')
    col_bear = _hex_to_pine_color(p.get('color_bear', ''), 'color.rgb(220, 20, 60, 20)')
    grp = "CVD -- Style"
    return f"""\
// ── Cumulative Volume Delta  (reset_mode='{reset_mode}' {tf_note}) ─────────────
// Delta approximation: Volume x (Close - Open) / (High - Low)
cumd_col_bull = input.color({col_bull}, "Bull color", group="{grp}")
cumd_col_bear = input.color({col_bear}, "Bear color", group="{grp}")
cumd_hl    = high == low ? 1e-10 : high - low
cumd_delta = volume * (close - open) / cumd_hl

var float cumd_val = 0.0
{accum_line}
{center_block}

// CVD candle: prior CumDelta = open, current CumDelta = close
cumd_o    = cumd_disp - cumd_delta
cumd_h    = math.max(cumd_disp, cumd_o)
cumd_l    = math.min(cumd_disp, cumd_o)
cumd_bull = cumd_disp >= cumd_o
plotcandle(cumd_o, cumd_h, cumd_l, cumd_disp, "CVD",
           cumd_bull ? cumd_col_bull : cumd_col_bear,
           cumd_bull ? cumd_col_bull : cumd_col_bear,
           bordercolor = color.new(color.gray, 60))
hline(0, color = color.new(color.black, 0), linestyle = hline.style_dotted)
"""


# ── Overlay templates ──────────────────────────────────────────────────────────

def _t_bol(p: dict) -> str:
    """Return the Pine Script v5 Bollinger Bands overlay template with configurable params."""
    sw = int(p.get('slow_window', 21))
    fw = int(p.get('fast_window',  9))
    sd = float(p.get('slow_dev', 2.0))
    fd = float(p.get('fast_dev', 0.2))
    col_s = _hex_to_pine_color(p.get('color_slow_band', ''), 'color.orange')
    col_f = _hex_to_pine_color(p.get('color_fast_band', ''), 'color.blue')
    wid   = max(1, min(5, int(p.get('line_width', 1))))
    sty   = p.get('line_style', 'solid')
    if sty not in ('solid', 'dashed', 'dotted'):
        sty = 'solid'
    grp   = "Bollinger Bands -- Style"
    return f"""\
// ── Bollinger Bands ───────────────────────────────────────────────────────────
bol_col_s  = input.color({col_s}, "Slow band color", group="{grp}")
bol_col_f  = input.color({col_f}, "Fast band color", group="{grp}")
bol_width  = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
bol_style  = input.string("{sty}", "Line style", options=["solid","dashed","dotted"], group="{grp}")
bol_lstyle = bol_style == "dashed" ? line.style_dashed : bol_style == "dotted" ? line.style_dotted : line.style_solid
[bol_s_mid, bol_s_up, bol_s_lo] = ta.bb(close, {sw}, {sd})
plot(bol_s_mid, "BB Slow Mid", color.new(bol_col_s, 50), bol_width)
bol_s_up_p = plot(bol_s_up, "BB Slow Up",  color.new(bol_col_s, 50), bol_width)
bol_s_lo_p = plot(bol_s_lo, "BB Slow Low", color.new(bol_col_s, 50), bol_width)
fill(bol_s_up_p, bol_s_lo_p, color.new(bol_col_s, 90))
[bol_f_mid, bol_f_up, bol_f_lo] = ta.bb(close, {fw}, {fd})
plot(bol_f_mid, "BB Fast Mid", color.new(bol_col_f, 50), bol_width)
bol_f_up_p = plot(bol_f_up, "BB Fast Up",  color.new(bol_col_f, 50), bol_width)
bol_f_lo_p = plot(bol_f_lo, "BB Fast Low", color.new(bol_col_f, 50), bol_width)
fill(bol_f_up_p, bol_f_lo_p, color.new(bol_col_f, 90))
"""


def _t_vwap(p: dict) -> str:
    """Return the Pine Script v5 VWAP overlay template with configurable params."""
    tf   = {'D': 'D', 'W': 'W', 'M': 'M'}.get(str(p.get('timeframe', 'M')), 'M')
    prev = bool(p.get('show_prevwap', True))
    col_vwap    = _hex_to_pine_color(p.get('color_vwap',    ''), 'color.blue')
    col_prevwap = _hex_to_pine_color(p.get('color_prevwap', ''), 'color.new(color.orange, 20)')
    wid = max(1, min(5, int(p.get('line_width', 2))))
    sty = p.get('line_style', 'solid')
    if sty not in ('solid', 'dashed', 'dotted'):
        sty = 'solid'
    grp = "VWAP -- Style"
    code = f"""\
// ── VWAP (anchored {tf}) ───────────────────────────────────────────────────────
vwap_col      = input.color({col_vwap},    "VWAP color",      group="{grp}")
vwap_prev_col = input.color({col_prevwap}, "Prev VWAP color", group="{grp}")
vwap_width    = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
vwap_style    = input.string("{sty}", "Line style", options=["solid","dashed","dotted"], group="{grp}")
var float vwap_tpv  = 0.0
var float vwap_cumv = 0.0
vwap_new = timeframe.change("{tf}")
if vwap_new
    vwap_tpv  := 0.0
    vwap_cumv := 0.0
vwap_tpv  += hlc3 * volume
vwap_cumv += volume
vwap_val   = vwap_cumv != 0 ? vwap_tpv / vwap_cumv : na
plot(vwap_val, "VWAP", vwap_col, vwap_width)
"""
    if prev:
        code += """\
var float vwap_prev = na
if vwap_new
    vwap_prev := vwap_val[1]
plot(vwap_prev, "Prev VWAP", vwap_prev_col, 1)
"""
    return code


def _t_don(p: dict) -> str:
    """Return the Pine Script v5 Donchian Channel overlay template with configurable params."""
    period = int(p.get('period', 10))
    style = _style_inputs(p, 'don', 'color.navy', 'Donchian')
    return f"""\
// ── Donchian Channel ──────────────────────────────────────────────────────────
{style}don_up  = ta.highest(high, {period})
don_lo  = ta.lowest(low,   {period})
don_mid = (don_up + don_lo) / 2
don_up_p = plot(don_up,  "Don Upper", don_col, don_width)
don_lo_p = plot(don_lo,  "Don Lower", don_col, don_width)
plot(don_mid, "Don Mid", color.new(don_col, 30), 1, plot.style_linebr)
fill(don_up_p, don_lo_p, color.new(don_col, 92))
"""


def _t_bos(p: dict) -> str:
    """Return the Pine Script v5 Break of Structure overlay template with configurable params."""
    pips = float(p.get('ignore_pips', 1.0))
    col_bull = _hex_to_pine_color(p.get('color_bull', ''), 'color.green')
    col_bear = _hex_to_pine_color(p.get('color_bear', ''), 'color.red')
    wid  = max(1, min(5, int(p.get('line_width', 1))))
    sty  = p.get('line_style', 'dotted')
    if sty not in ('solid', 'dashed', 'dotted'):
        sty = 'dotted'
    grp  = "BOS -- Style"
    return f"""\
// ── Break of Structure ────────────────────────────────────────────────────────
bos_col_bull = input.color({col_bull}, "Bull color", group="{grp}")
bos_col_bear = input.color({col_bear}, "Bear color", group="{grp}")
bos_width    = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
bos_style    = input.string("{sty}", "Line style", options=["solid","dashed","dotted"], group="{grp}")
bos_lstyle   = bos_style == "dashed" ? line.style_dashed : bos_style == "dotted" ? line.style_dotted : line.style_solid
bos_pip_mult = {pips}
bos_atr      = ta.atr(14)
bos_pip      = bos_atr * 0.5 * bos_pip_mult
var float bos_ph = na
var float bos_pl = na
var int   bos_hb = na
var int   bos_lb = na
bos_brk_up = not na(bos_ph) and high > bos_ph + bos_pip
bos_brk_dn = not na(bos_pl) and low  < bos_pl - bos_pip
if bos_brk_up
    line.new(bos_hb, bos_ph, bar_index, bos_ph, color=bos_col_bull, width=bos_width, style=bos_lstyle)
    bos_ph := high
    bos_hb := bar_index
else if na(bos_ph) or high > bos_ph
    bos_ph := high
    bos_hb := bar_index
if bos_brk_dn
    line.new(bos_lb, bos_pl, bar_index, bos_pl, color=bos_col_bear, width=bos_width, style=bos_lstyle)
    bos_pl := low
    bos_lb := bar_index
else if na(bos_pl) or low < bos_pl
    bos_pl := low
    bos_lb := bar_index
"""


def _t_fvg(p: dict) -> str:
    """Return the Pine Script v5 Fair Value Gap overlay template with configurable params."""
    return """\
// ── Fair Value Gap ────────────────────────────────────────────────────────────
fvg_bull = high[2] < low
fvg_bear = low[2]  > high
plotshape(fvg_bull, "Bullish FVG", shape.cross, location.belowbar,
          color.blue, size = size.normal)
plotshape(fvg_bear, "Bearish FVG", shape.cross, location.abovebar,
          color.blue, size = size.normal)
"""


def _t_fib(p: dict) -> str:
    """Return the Pine Script v5 Fibonacci retracement overlay template with configurable params."""
    period = int(p.get('period', 100))
    return f"""\
// ── Fibonacci Levels ──────────────────────────────────────────────────────────
fib_high = ta.highest(close, {period})
fib_low  = ta.lowest(close,  {period})
fib_diff = fib_high - fib_low
plot(fib_high,                   "Fib 0%",     color.purple,               1, plot.style_linebr)
plot(fib_high - fib_diff * 0.236,"Fib 23.6%",  color.blue,                 1, plot.style_linebr)
plot(fib_high - fib_diff * 0.382,"Fib 38.2%",  color.green,                1, plot.style_linebr)
plot(fib_high - fib_diff * 0.500,"Fib 50%",    color.red,                  1, plot.style_linebr)
plot(fib_high - fib_diff * 0.618,"Fib 61.8%",  color.orange,               1, plot.style_linebr)
plot(fib_low,                    "Fib 100%",   color.black,                1, plot.style_linebr)
plot(fib_high + fib_diff * 0.236,"Fib +23.6%", color.new(color.lime,   50), 1, plot.style_linebr)
plot(fib_low  - fib_diff * 0.236,"Fib -23.6%", color.new(color.maroon, 50), 1, plot.style_linebr)
var label _fbl0 = na
var label _fbl1 = na
var label _fbl2 = na
var label _fbl3 = na
var label _fbl4 = na
var label _fbl5 = na
var label _fbl6 = na
var label _fbl7 = na
if barstate.islast
    label.delete(_fbl0)
    label.delete(_fbl1)
    label.delete(_fbl2)
    label.delete(_fbl3)
    label.delete(_fbl4)
    label.delete(_fbl5)
    label.delete(_fbl6)
    label.delete(_fbl7)
    _fbl0 := label.new(bar_index + 1, fib_high + fib_diff * 0.236, "+23.6% " + str.tostring(math.round(fib_high + fib_diff * 0.236, 1)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.lime,   20), textcolor=color.black, style=label.style_label_left, size=size.small)
    _fbl1 := label.new(bar_index + 1, fib_high,                    "0% "     + str.tostring(math.round(fib_high, 1)),                    xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.purple, 20), textcolor=color.white, style=label.style_label_left, size=size.small)
    _fbl2 := label.new(bar_index + 1, fib_high - fib_diff * 0.236, "23.6% "  + str.tostring(math.round(fib_high - fib_diff * 0.236, 1)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.blue,   20), textcolor=color.white, style=label.style_label_left, size=size.small)
    _fbl3 := label.new(bar_index + 1, fib_high - fib_diff * 0.382, "38.2% "  + str.tostring(math.round(fib_high - fib_diff * 0.382, 1)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.green,  20), textcolor=color.white, style=label.style_label_left, size=size.small)
    _fbl4 := label.new(bar_index + 1, fib_high - fib_diff * 0.500, "50% "    + str.tostring(math.round(fib_high - fib_diff * 0.500, 1)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.red,    20), textcolor=color.white, style=label.style_label_left, size=size.small)
    _fbl5 := label.new(bar_index + 1, fib_high - fib_diff * 0.618, "61.8% "  + str.tostring(math.round(fib_high - fib_diff * 0.618, 1)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.orange, 20), textcolor=color.black, style=label.style_label_left, size=size.small)
    _fbl6 := label.new(bar_index + 1, fib_low,                     "100% "   + str.tostring(math.round(fib_low, 1)),                     xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.black,  20), textcolor=color.white, style=label.style_label_left, size=size.small)
    _fbl7 := label.new(bar_index + 1, fib_low  - fib_diff * 0.236, "-23.6% " + str.tostring(math.round(fib_low  - fib_diff * 0.236, 1)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.maroon, 20), textcolor=color.white, style=label.style_label_left, size=size.small)
"""


def _t_sup(p: dict) -> str:
    """Return the Pine Script v5 Support/Resistance overlay template with configurable params."""
    win = int(p.get('window', 21))
    col_s = _hex_to_pine_color(p.get('color_support',    ''), 'color.green')
    col_r = _hex_to_pine_color(p.get('color_resistance', ''), 'color.red')
    wid   = max(1, min(5, int(p.get('line_width', 1))))
    sty   = p.get('line_style', 'dashed')
    if sty not in ('solid', 'dashed', 'dotted'):
        sty = 'dashed'
    grp   = "Support/Resistance -- Style"
    return f"""\
// ── Support / Resistance ──────────────────────────────────────────────────────
// Single horizontal line at the last bar's rolling-window min/max.
sup_col_s  = input.color({col_s}, "Support color",    group="{grp}")
sup_col_r  = input.color({col_r}, "Resistance color", group="{grp}")
sup_width  = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
sup_style  = input.string("{sty}", "Line style", options=["solid","dashed","dotted"], group="{grp}")
sup_lstyle = sup_style == "dashed" ? line.style_dashed : sup_style == "dotted" ? line.style_dotted : line.style_solid
sup_val = ta.lowest(low,   {win})
res_val = ta.highest(high, {win})
var line _sup_line = na
var line _res_line = na
if barstate.islast
    line.delete(_sup_line)
    line.delete(_res_line)
    _sup_line := line.new(bar_index, sup_val, bar_index + 1, sup_val, color=sup_col_s, width=sup_width, style=sup_lstyle, extend=extend.both)
    _res_line := line.new(bar_index, res_val, bar_index + 1, res_val, color=sup_col_r, width=sup_width, style=sup_lstyle, extend=extend.both)
    label.new(bar_index, sup_val, "Support: "    + str.tostring(math.round(sup_val, 2)), color=color.new(sup_col_s, 70), textcolor=sup_col_s, style=label.style_label_left, size=size.small)
    label.new(bar_index, res_val, "Resistance: " + str.tostring(math.round(res_val, 2)), color=color.new(sup_col_r, 70), textcolor=sup_col_r, style=label.style_label_left, size=size.small)
"""


def _t_heikin(p: dict) -> str:
    """Return the Pine Script v5 Heikin-Ashi overlay template with configurable params."""
    smooth = bool(p.get('smooth', False))
    slb    = int(p.get('smooth_length_before', 10))
    sla    = int(p.get('smooth_length_after',  10))
    _pine_ma = {'SMA': 'ta.sma', 'EMA': 'ta.ema', 'WMA': 'ta.wma',
                'RMA': 'ta.rma', 'HMA': 'ta.hma'}
    mat_b  = _pine_ma.get(str(p.get('smooth_ma_type_before', 'EMA')), 'ta.ema')
    mat_a  = _pine_ma.get(str(p.get('smooth_ma_type_after',  'EMA')), 'ta.ema')
    mat_e  = _pine_ma.get(str(p.get('ema_type', 'EMA')), 'ta.ema')
    ehl    = int(p.get('ema_high_length', 21))
    ell    = int(p.get('ema_low_length',  21))
    show_e      = bool(p.get('show_emas',     True))
    fill_band   = bool(p.get('fill_ema_band', False))

    col_bull     = _hex_to_pine_color(p.get('color_bull',     ''), 'color.new(color.lime,   20)')
    col_bear     = _hex_to_pine_color(p.get('color_bear',     ''), 'color.new(color.red,    20)')
    col_ema_high = _hex_to_pine_color(p.get('color_ema_high', ''), 'color.orange')
    col_ema_low  = _hex_to_pine_color(p.get('color_ema_low',  ''), 'color.aqua')
    grp = "Heikin Ashi -- Style"

    if smooth:
        src_o = f"{mat_b}(open,  {slb})"
        src_h = f"{mat_b}(high,  {slb})"
        src_l = f"{mat_b}(low,   {slb})"
        src_c = f"{mat_b}(close, {slb})"
    else:
        src_o, src_h, src_l, src_c = "open", "high", "low", "close"

    code = f"""\
// ── Heikin Ashi ───────────────────────────────────────────────────────────────
ha_bull_col  = input.color({col_bull},     "Bull candle color", group="{grp}")
ha_bear_col  = input.color({col_bear},     "Bear candle color", group="{grp}")
ha_ema_h_col = input.color({col_ema_high}, "EMA High color",    group="{grp}")
ha_ema_l_col = input.color({col_ema_low},  "EMA Low color",     group="{grp}")
ha_fill_band = input.bool({str(fill_band).lower()}, "Fill EMA band", group="{grp}")
ha_o_src = {src_o}
ha_h_src = {src_h}
ha_l_src = {src_l}
ha_c_src = {src_c}
ha_close = (ha_o_src + ha_h_src + ha_l_src + ha_c_src) / 4
var float ha_open = na
ha_open  := na(ha_open[1]) ? (ha_o_src + ha_c_src) / 2 : (ha_open[1] + ha_close[1]) / 2
ha_high  = math.max(ha_h_src, math.max(ha_open, ha_close))
ha_low   = math.min(ha_l_src, math.min(ha_open, ha_close))
"""
    if smooth:
        code += f"""\
ha_close := {mat_a}(ha_close, {sla})
ha_open  := {mat_a}(ha_open,  {sla})
ha_high  := {mat_a}(ha_high,  {sla})
ha_low   := {mat_a}(ha_low,   {sla})
"""
    code += """\
ha_col = ha_close >= ha_open ? ha_bull_col : ha_bear_col
plotcandle(ha_open, ha_high, ha_low, ha_close, "Heikin Ashi",
           ha_col, ha_col, bordercolor = color.new(color.gray, 70))
"""
    if show_e:
        code += f"""\
ha_p_high = plot({mat_e}(ha_high, {ehl}), "EMA HA High", ha_ema_h_col, 1)
ha_p_low  = plot({mat_e}(ha_low,  {ell}), "EMA HA Low",  ha_ema_l_col, 1)
fill(ha_p_high, ha_p_low, color = ha_fill_band ? color.new(color.gray, 85) : color.new(color.gray, 100))
"""
    return code


def _t_ici(p: dict) -> str:
    """Return the Pine Script v5 ICT overlay template."""
    win = int(p.get('window', 14))
    col_tenkan     = _hex_to_pine_color(p.get('color_tenkan',     ''), 'color.aqua')
    col_kijun      = _hex_to_pine_color(p.get('color_kijun',      ''), 'color.orange')
    col_chikou     = _hex_to_pine_color(p.get('color_chikou',     ''), 'color.new(color.gray, 30)')
    col_ema        = _hex_to_pine_color(p.get('color_ema',        ''), 'color.yellow')
    col_cloud_bull = _hex_to_pine_color(p.get('color_cloud_bull', ''), 'color.new(color.green, 80)')
    col_cloud_bear = _hex_to_pine_color(p.get('color_cloud_bear', ''), 'color.new(color.red,   80)')
    wid = max(1, min(5, int(p.get('line_width', 1))))
    grp = "Ichimoku -- Style"
    return f"""\
// ── Ichimoku ──────────────────────────────────────────────────────────────────
ici_tenkan_col = input.color({col_tenkan},     "Tenkan-sen color",   group="{grp}")
ici_kijun_col  = input.color({col_kijun},      "Kijun-sen color",    group="{grp}")
ici_chikou_col = input.color({col_chikou},     "Chikou span color",  group="{grp}")
ici_ema_col    = input.color({col_ema},        "EMA color",          group="{grp}")
ici_bull_col   = input.color({col_cloud_bull}, "Bull cloud color",   group="{grp}")
ici_bear_col   = input.color({col_cloud_bear}, "Bear cloud color",   group="{grp}")
ici_width      = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
ici_tenkan = (ta.highest(high,  9) + ta.lowest(low,  9)) / 2
ici_kijun  = (ta.highest(high, 26) + ta.lowest(low, 26)) / 2
ici_span_a = (ici_tenkan + ici_kijun) / 2
ici_span_b = (ta.highest(high, 52) + ta.lowest(low, 52)) / 2
ici_chikou = close[26]
ici_ema    = ta.ema(close, {win})
ici_sa_p = plot(ici_span_a, "Span A", color.new(ici_bull_col, 0), ici_width, offset=-26)
ici_sb_p = plot(ici_span_b, "Span B", color.new(ici_bear_col, 0), ici_width, offset=-26)
fill(ici_sa_p, ici_sb_p, ici_span_a >= ici_span_b ? ici_bull_col : ici_bear_col)
plot(ici_tenkan, "Tenkan",    ici_tenkan_col, ici_width)
plot(ici_kijun,  "Kijun",     ici_kijun_col,  ici_width)
plot(ici_chikou, "Chikou",    ici_chikou_col, 1, offset=-26)
plot(ici_ema,    "EMA {win}", ici_ema_col,    1, plot.style_linebr)
// Signale: Preis über Wolke = Long, unter Wolke = Short
ici_cloud_top    = math.max(ici_span_a[26], ici_span_b[26])
ici_cloud_bottom = math.min(ici_span_a[26], ici_span_b[26])
ici_long  = close > ici_cloud_top
ici_short = close < ici_cloud_bottom
// Nur den ersten Marker einer Serie anzeigen
plotshape(ici_long  and not ici_long[1],  "ICI Buy",  shape.triangleup,   location.belowbar, color.new(color.lime,  0), size=size.small)
plotshape(ici_short and not ici_short[1], "ICI Sell", shape.triangledown, location.abovebar, color.new(color.red,   0), size=size.small)
"""


def _t_lqz(p: dict) -> str:
    """Return the Pine Script v5 Liquidity Zone overlay template with configurable params."""
    win = int(p.get('window',    14))
    thr = float(p.get('threshold', 1.5))
    return f"""\
// ── Liquidity Zones (high-volume bar approximation) ───────────────────────────
lqz_avg  = ta.sma(volume,   {win})
lqz_std  = ta.stdev(volume, {win})
lqz_zone = volume > lqz_avg + {thr} * lqz_std
plotshape(lqz_zone and barstate.isconfirmed, "High Vol",
          shape.square, location.abovebar, color.new(color.gray, 50), size = size.tiny)
"""


def _t_atc(p: dict) -> str:
    """Return the Pine Script v5 Average True Channel overlay template with configurable params."""
    dev = float(p.get('dev_multi', 2.0))
    return f"""\
// ── Auto Trend Channels (line.new on last bar — mirrors Python ATC) ───────────
// Three symmetric regression channels: highest-high / lowest-low / full-range anchor.
// OLS fit + residual stdev computed with for-loops (avoids ta.linreg series-int limit).
atc_dev = {dev}

// Manual OLS on close[0..length-1]: returns [anchor_val, current_val, residual_stdev]
atc_reg(length) =>
    float n   = float(length)
    float sx  = 0.0
    float sy  = 0.0
    float sxx = 0.0
    float sxy = 0.0
    for i = 0 to length - 1
        float xi = float(i)
        float yi = close[length - 1 - i]
        sx  += xi
        sy  += yi
        sxx += xi * xi
        sxy += xi * yi
    float denom = n * sxx - sx * sx
    float b     = denom != 0.0 ? (n * sxy - sx * sy) / denom : 0.0
    float a     = (sy - b * sx) / n
    float ssr   = 0.0
    for i = 0 to length - 1
        float err = close[length - 1 - i] - (a + b * float(i))
        ssr += err * err
    [a, a + b * (n - 1.0), math.sqrt(ssr / n)]

if barstate.islast
    // ── High anchor (darkred) ─────────────────────────────────────────────────
    int atc_h_off = 0
    for i = 1 to math.min(499, bar_index)
        if high[i] > high[atc_h_off]
            atc_h_off := i
    atc_hlen = math.max(2, atc_h_off + 1)
    atc_hx0  = bar_index - atc_hlen + 1
    [atc_h_anc, atc_h_cur, atc_hstd] = atc_reg(atc_hlen)
    line.new(atc_hx0, atc_h_anc,                       bar_index, atc_h_cur,
             color=color.new(color.red, 40), width=1, style=line.style_dotted)
    line.new(atc_hx0, atc_h_anc + atc_dev * atc_hstd, bar_index, atc_h_cur + atc_dev * atc_hstd,
             color=color.new(color.red, 0), width=2)
    line.new(atc_hx0, atc_h_anc - atc_dev * atc_hstd, bar_index, atc_h_cur - atc_dev * atc_hstd,
             color=color.new(color.red, 0), width=2)

    // ── Low anchor (darkgreen) ────────────────────────────────────────────────
    int atc_l_off = 0
    for i = 1 to math.min(499, bar_index)
        if low[i] < low[atc_l_off]
            atc_l_off := i
    atc_llen = math.max(2, atc_l_off + 1)
    atc_lx0  = bar_index - atc_llen + 1
    [atc_l_anc, atc_l_cur, atc_lstd] = atc_reg(atc_llen)
    line.new(atc_lx0, atc_l_anc,                       bar_index, atc_l_cur,
             color=color.new(color.green, 40), width=1, style=line.style_dotted)
    line.new(atc_lx0, atc_l_anc + atc_dev * atc_lstd, bar_index, atc_l_cur + atc_dev * atc_lstd,
             color=color.new(color.green, 0), width=2)
    line.new(atc_lx0, atc_l_anc - atc_dev * atc_lstd, bar_index, atc_l_cur - atc_dev * atc_lstd,
             color=color.new(color.green, 0), width=2)

    // ── Zero anchor (darkblue — full visible range, flattest-slope approx.) ──
    atc_zlen = math.min(500, bar_index + 1)
    atc_zx0  = bar_index - atc_zlen + 1
    [atc_z_anc, atc_z_cur, atc_zstd] = atc_reg(atc_zlen)
    line.new(atc_zx0, atc_z_anc,                       bar_index, atc_z_cur,
             color=color.new(color.blue, 40), width=1, style=line.style_dotted)
    line.new(atc_zx0, atc_z_anc + atc_dev * atc_zstd, bar_index, atc_z_cur + atc_dev * atc_zstd,
             color=color.new(color.blue, 0), width=2)
    line.new(atc_zx0, atc_z_anc - atc_dev * atc_zstd, bar_index, atc_z_cur - atc_dev * atc_zstd,
             color=color.new(color.blue, 0), width=2)
"""


def _t_atl(p: dict) -> str:
    """Return the Pine Script v5 Auto Trend Lines overlay template with configurable params."""
    dev = float(p.get('dev_multi', 2.0))
    return f"""\
// ── Auto Trend Lines (line.new on last bar — mirrors Python ATL) ─────────────
// High anchor (darkred): mid dotted + top solid.
// Low  anchor (darkgreen): mid dotted + bot solid.
// Zero anchor (darkblue): top solid only — full visible range (flattest-slope approx.).
// OLS fit + residual stdev computed with for-loops (avoids ta.linreg series-int limit).
atl_dev = {dev}

// Manual OLS on close[0..length-1]: returns [anchor_val, current_val, residual_stdev]
atl_reg(length) =>
    float n   = float(length)
    float sx  = 0.0
    float sy  = 0.0
    float sxx = 0.0
    float sxy = 0.0
    for i = 0 to length - 1
        float xi = float(i)
        float yi = close[length - 1 - i]
        sx  += xi
        sy  += yi
        sxx += xi * xi
        sxy += xi * yi
    float denom = n * sxx - sx * sx
    float b     = denom != 0.0 ? (n * sxy - sx * sy) / denom : 0.0
    float a     = (sy - b * sx) / n
    float ssr   = 0.0
    for i = 0 to length - 1
        float err = close[length - 1 - i] - (a + b * float(i))
        ssr += err * err
    [a, a + b * (n - 1.0), math.sqrt(ssr / n)]

if barstate.islast
    // ── High anchor (darkred): mid dotted + top solid ─────────────────────
    int atl_h_off = 0
    for i = 1 to math.min(499, bar_index)
        if high[i] > high[atl_h_off]
            atl_h_off := i
    atl_hlen = math.max(2, atl_h_off + 1)
    atl_hx0  = bar_index - atl_hlen + 1
    [atl_h_anc, atl_h_cur, atl_hstd] = atl_reg(atl_hlen)
    // mid — dotted, semi-transparent red
    line.new(atl_hx0, atl_h_anc,                       bar_index, atl_h_cur,
             color=color.new(color.red, 40), width=1, style=line.style_dotted)
    // top — solid, opaque red
    line.new(atl_hx0, atl_h_anc + atl_dev * atl_hstd, bar_index, atl_h_cur + atl_dev * atl_hstd,
             color=color.new(color.red, 0), width=2)

    // ── Low anchor (darkgreen): mid dotted + bot solid ────────────────────
    int atl_l_off = 0
    for i = 1 to math.min(499, bar_index)
        if low[i] < low[atl_l_off]
            atl_l_off := i
    atl_llen = math.max(2, atl_l_off + 1)
    atl_lx0  = bar_index - atl_llen + 1
    [atl_l_anc, atl_l_cur, atl_lstd] = atl_reg(atl_llen)
    // mid — dotted, semi-transparent green
    line.new(atl_lx0, atl_l_anc,                       bar_index, atl_l_cur,
             color=color.new(color.green, 40), width=1, style=line.style_dotted)
    // bot — solid, opaque green
    line.new(atl_lx0, atl_l_anc - atl_dev * atl_lstd, bar_index, atl_l_cur - atl_dev * atl_lstd,
             color=color.new(color.green, 0), width=2)

    // ── Zero anchor (darkblue — full range, flattest-slope approx.) ──────
    atl_zlen = math.min(500, bar_index + 1)
    atl_zx0  = bar_index - atl_zlen + 1
    [atl_z_anc, atl_z_cur, atl_zstd] = atl_reg(atl_zlen)
    // top — solid, opaque blue
    line.new(atl_zx0, atl_z_anc + atl_dev * atl_zstd, bar_index, atl_z_cur + atl_dev * atl_zstd,
             color=color.new(color.blue, 0), width=2)
"""


def _t_bsz(p: dict) -> str:
    """Return the Pine Script v5 Buy/Sell Zone overlay template with configurable params."""
    lb = int(p.get('lookback',  5))
    rr = float(p.get('rr_ratio', 1.5))
    return f"""\
// ── Buy / Sell Zones (EWO + EMA crossover) ───────────────────────────────────
bsz_ema9  = ta.ema(close, 9)
bsz_ema20 = ta.ema(close, 20)
bsz_ewo   = ta.sma(close, 5) - ta.sma(close, 21)
bsz_eema  = ta.ema(bsz_ewo, 9)
bsz_buy   = bsz_ema9 > ta.highest(bsz_ema20, {lb})[1] and bsz_ewo > bsz_eema
bsz_sell  = bsz_ema9 < ta.lowest(bsz_ema20,  {lb})[1] and bsz_ewo < bsz_eema
plotshape(bsz_buy,  "BSZ Buy",  shape.triangleup,   location.belowbar,
          color.green, size = size.small)
plotshape(bsz_sell, "BSZ Sell", shape.triangledown, location.abovebar,
          color.red,   size = size.small)
"""


def _t_qtrend(p: dict) -> str:
    """Return the Pine Script v5 Quantitative Trend overlay template with configurable params."""
    tp  = int(p.get('trend_period',   200))
    ap  = int(p.get('atr_period',      14))
    am  = float(p.get('atr_mult',     1.0))
    kcl = int(p.get('kc_length',       20))
    kci = float(p.get('kc_mult_inner', 1.8))
    kco = float(p.get('kc_mult_outer', 3.3))
    bbl = int(p.get('bb_length',       20))
    bbm = float(p.get('bb_mult',       2.0))
    return f"""\
// ── QTrend + Keltner Squeeze ──────────────────────────────────────────────────
qt_high  = ta.highest(close, {tp})
qt_low   = ta.lowest(close,  {tp})
qt_mid   = (qt_high + qt_low) / 2
qt_atr   = ta.atr({ap})
qt_up    = close > qt_mid + {am} * qt_atr
qt_dn    = close < qt_mid - {am} * qt_atr
var int qt_trend = 0
qt_trend := qt_up ? 1 : qt_dn ? -1 : qt_trend
plot(qt_mid, "QTrend", qt_trend == 1 ? color.green : color.red, 2)

qt_kc_ema = ta.ema(close, {kcl})
qt_kc_atr = ta.atr({kcl})
qt_kc_ui  = qt_kc_ema + {kci} * qt_kc_atr
qt_kc_li  = qt_kc_ema - {kci} * qt_kc_atr
qt_kc_uo  = qt_kc_ema + {kco} * qt_kc_atr
qt_kc_lo  = qt_kc_ema - {kco} * qt_kc_atr
plot(qt_kc_ui, "KC Inner Up", color.new(color.red,  0), 1)
plot(qt_kc_li, "KC Inner Lo", color.new(color.blue, 0), 1)
plot(qt_kc_uo, "KC Outer Up", color.new(color.red,  40), 1, plot.style_linebr)
plot(qt_kc_lo, "KC Outer Lo", color.new(color.blue, 40), 1, plot.style_linebr)

qt_bb_std = ta.stdev(close, {bbl})
qt_bb_sma = ta.sma(close, {bbl})
qt_bb_up  = qt_bb_sma + {bbm} * qt_bb_std
qt_bb_lo  = qt_bb_sma - {bbm} * qt_bb_std
qt_squeeze = (qt_bb_up - qt_bb_lo) < (qt_kc_ui - qt_kc_li)
bgcolor(qt_squeeze ? color.new(color.gray, 85) : na, title = "Squeeze")

qt_brk_up = qt_squeeze[1] and close > qt_kc_ui and close[1] <= qt_kc_ui[1]
qt_brk_dn = qt_squeeze[1] and close < qt_kc_li and close[1] >= qt_kc_li[1]
plotshape(qt_brk_up, "Breakout Up",   shape.triangleup,   location.belowbar,
          color.green, size = size.small)
plotshape(qt_brk_dn, "Breakout Down", shape.triangledown, location.abovebar,
          color.red,   size = size.small)
"""


def _t_markov(p: dict) -> str:
    """Return the Pine Script v5 Markov Regime overlay template with configurable params."""
    lookback   = int(p.get('lookback',   20))
    bull_pct   = float(p.get('bull_pct',  5.0))
    bear_pct   = float(p.get('bear_pct',  5.0))
    banner_pos   = str(p.get('banner_pos', 'top_left'))
    matrix_pos   = str(p.get('matrix_pos', 'middle_left'))
    forecast_pos = str(p.get('forecast_pos', 'top_right'))
    return f"""\
// ── Markov Regime — Bull / Bear / Sideways ────────────────────────────────────
// Full Pine Script port.  Regime = log(close/close[lookback]):
//   > bull_pct% → Bull (1)  |  < -bear_pct% → Bear (2)  |  else Sideways (0)
// Builds a 3x3 transition matrix from visible history, iterates to the
// stationary distribution, projects the current regime 3 bars forward via
// v*P / v*P^2 / v*P^3, and renders ribbon + banner + matrix/stationary/forecast
// tables. The forecast table also shows the long-run/stationary mix as an
// extra row "∞" so the near-term projection can be compared to the baseline.
// Author of original Pine Script: Lewis Jackson · Framework: Roan (@RohOnChain)

// ── Inputs ────────────────────────────────────────────────────────────────────
grp_logic    = "Regime logic"
grp_display  = "Display"
grp_position = "Table positions"

lookback_window    = input.int({lookback},    "Lookback window (bars)",         minval=5,   maxval=250,  group=grp_logic)
bull_threshold_pct = input.float({bull_pct}, "Bull threshold (%)",             minval=0.1, maxval=50.0, group=grp_logic, step=0.1)
bear_threshold_pct = input.float({bear_pct}, "Bear threshold (%)",             minval=0.1, maxval=50.0, group=grp_logic, step=0.1)
stationary_power   = input.int(50,            "Stationary power (iterations)",  minval=10,  maxval=200,  group=grp_logic)

show_regime_ribbon     = input.bool(true,  "Show regime ribbon",         group=grp_display)
show_regime_banner     = input.bool(true,  "Show current-regime banner", group=grp_display)
show_matrix_table      = input.bool(true,  "Show transition matrix",     group=grp_display)
show_stationary_table  = input.bool(true,  "Show stationary distribution", group=grp_display)
show_forecast_table    = input.bool(true,  "Show 3-bar forecast table",  group=grp_display)
show_transition_labels = input.bool(true,  "Label state transitions on chart", group=grp_display)
table_text_size        = input.string("large", "Table text size", options=["small","normal","large","huge"], group=grp_display)
min_regime_hold        = input.int(4, "Min bars a regime must hold to be labelled", minval=1, maxval=50, group=grp_display)

banner_position_input    = input.string("{banner_pos}", "Banner position",    options=["top_left","top_center","top_right","middle_left","middle_center","middle_right","bottom_left","bottom_center","bottom_right"], group=grp_position)
matrix_position_input    = input.string("{matrix_pos}", "Matrix position",    options=["top_left","top_center","top_right","middle_left","middle_center","middle_right","bottom_left","bottom_center","bottom_right"], group=grp_position)
stationary_position_input = input.string("bottom_right","Stationary position",options=["top_left","top_center","top_right","middle_left","middle_center","middle_right","bottom_left","bottom_center","bottom_right"], group=grp_position)
forecast_position_input  = input.string("{forecast_pos}", "Forecast position",  options=["top_left","top_center","top_right","middle_left","middle_center","middle_right","bottom_left","bottom_center","bottom_right"], group=grp_position)

// ── Position / size helpers ───────────────────────────────────────────────────
position_from_string(s) =>
    switch s
        "top_left"      => position.top_left
        "top_center"    => position.top_center
        "top_right"     => position.top_right
        "middle_left"   => position.middle_left
        "middle_center" => position.middle_center
        "middle_right"  => position.middle_right
        "bottom_left"   => position.bottom_left
        "bottom_center" => position.bottom_center
        "bottom_right"  => position.bottom_right
        =>                 position.top_right

size_from_string(s) =>
    switch s
        "small"  => size.small
        "normal" => size.normal
        "large"  => size.large
        "huge"   => size.huge
        =>          size.large

notch_down(s) =>
    switch s
        "huge"   => "large"
        "large"  => "normal"
        "normal" => "small"
        =>          "small"

banner_pos_c    = position_from_string(banner_position_input)
matrix_pos_c    = position_from_string(matrix_position_input)
stationary_pos_c = position_from_string(stationary_position_input)
forecast_pos_c  = position_from_string(forecast_position_input)
val_size = size_from_string(table_text_size)
hdr_size = size_from_string(notch_down(table_text_size))

// ── Palette ───────────────────────────────────────────────────────────────────
// Bull  #84BBA1 hsl(150,38,64) · Bear #C57F86 hsl(354,48,64) · Side #A4ABB7 hsl(220,14,68)
c_bull_solid = color.rgb(132, 187, 161, 30)
c_bear_solid = color.rgb(197, 127, 134, 30)
c_side_solid = color.rgb(164, 171, 183, 30)
c_card_bg    = color.new(#0B0F0D, 8)
c_card_frame = color.new(#3FDE7E, 78)
c_accent     = #3FDE7E
c_diag_bg    = color.new(#3FDE7E, 80)
c_diag_txt   = #6BF0A6
c_off_txt    = color.new(color.white, 55)
c_hdr_txt    = color.new(color.white, 35)
c_foot_txt   = color.new(color.white, 60)
c_bg         = color.new(color.black, 30)

regime_solid(r) => r == 1 ? c_bull_solid : r == 2 ? c_bear_solid : c_side_solid
regime_name(r)  => r == 1 ? "Bull"       : r == 2 ? "Bear"       : "Sideways"
regime_abbr(r)  => r == 1 ? "BULL"       : r == 2 ? "BEAR"       : "SIDE"

// Display order (Bull=0, Bear=1, Side=2) -> internal regime code (0=Side, 1=Bull, 2=Bear)
disp_to_internal(d) => d == 0 ? 1 : d == 1 ? 2 : 0

// ── Per-bar regime ────────────────────────────────────────────────────────────
log_ret = math.log(close / close[lookback_window])
regime  = na(log_ret) ? int(na) :
          log_ret >  bull_threshold_pct / 100.0 ? 1 :
          log_ret < -bear_threshold_pct / 100.0 ? 2 : 0

// ── Regime ribbon ─────────────────────────────────────────────────────────────
mrk_ribbon = regime == 1 ? color.rgb(132,187,161,90) : regime == 2 ? color.rgb(197,127,134,90) : color.rgb(164,171,183,90)
bgcolor(show_regime_ribbon ? mrk_ribbon : na, title="Regime ribbon")

// ── Transition counting ───────────────────────────────────────────────────────
var mrk_counts = array.new_int(9, 0)
mrk_prev = regime[1]
if barstate.isconfirmed and not na(mrk_prev) and not na(regime)
    mrk_idx = mrk_prev * 3 + regime
    array.set(mrk_counts, mrk_idx, array.get(mrk_counts, mrk_idx) + 1)

// ── Transition labels (debounced) ─────────────────────────────────────────────
var int mrk_last_lbl = na
mrk_held = not na(regime)
for k = 0 to min_regime_hold - 1
    mrk_held := mrk_held and not na(regime[k]) and regime[k] == regime

if barstate.isconfirmed and not na(regime) and mrk_held and regime != mrk_last_lbl
    if show_transition_labels and not na(mrk_last_lbl)
        flip_off = min_regime_hold - 1
        label.new(bar_index - flip_off, high[flip_off],
                  regime_abbr(mrk_last_lbl) + " -> " + regime_abbr(regime),
                  yloc=yloc.abovebar, style=label.style_label_down,
                  color=regime_solid(regime), textcolor=color.white, size=size.normal)
    mrk_last_lbl := regime

// ── Table objects (created once) ──────────────────────────────────────────────
// Tables start as `na` and are created/deleted on demand below — this is
// what actually makes each one switchable. A `var table` that is always
// created up front keeps drawing its background/frame even with zero
// cells populated, so the toggle would visually do nothing.
var table mrk_tbl_banner     = na
var table mrk_tbl_matrix     = na
var table mrk_tbl_stationary = na
var table mrk_tbl_forecast   = na

// ── Unrolled 3x3 matrix multiplication ───────────────────────────────────────
matmul_3x3(A, B) =>
    a00 = array.get(A, 0)
    a01 = array.get(A, 1)
    a02 = array.get(A, 2)
    a10 = array.get(A, 3)
    a11 = array.get(A, 4)
    a12 = array.get(A, 5)
    a20 = array.get(A, 6)
    a21 = array.get(A, 7)
    a22 = array.get(A, 8)
    b00 = array.get(B, 0)
    b01 = array.get(B, 1)
    b02 = array.get(B, 2)
    b10 = array.get(B, 3)
    b11 = array.get(B, 4)
    b12 = array.get(B, 5)
    b20 = array.get(B, 6)
    b21 = array.get(B, 7)
    b22 = array.get(B, 8)
    C = array.new_float(9, 0.0)
    array.set(C,0, a00*b00+a01*b10+a02*b20)
    array.set(C,1, a00*b01+a01*b11+a02*b21)
    array.set(C,2, a00*b02+a01*b12+a02*b22)
    array.set(C,3, a10*b00+a11*b10+a12*b20)
    array.set(C,4, a10*b01+a11*b11+a12*b21)
    array.set(C,5, a10*b02+a11*b12+a12*b22)
    array.set(C,6, a20*b00+a21*b10+a22*b20)
    array.set(C,7, a20*b01+a21*b11+a22*b21)
    array.set(C,8, a20*b02+a21*b12+a22*b22)
    C

// Row-vector x 3x3-matrix - used to project the current regime forward
// one bar at a time: v(t+1) = v(t) . P  (v is a one-hot regime vector)
vecmul_3(v, M) =>
    v0  = array.get(v, 0)
    v1  = array.get(v, 1)
    v2  = array.get(v, 2)
    m00 = array.get(M, 0)
    m01 = array.get(M, 1)
    m02 = array.get(M, 2)
    m10 = array.get(M, 3)
    m11 = array.get(M, 4)
    m12 = array.get(M, 5)
    m20 = array.get(M, 6)
    m21 = array.get(M, 7)
    m22 = array.get(M, 8)
    out = array.new_float(3, 0.0)
    array.set(out, 0, v0*m00 + v1*m10 + v2*m20)
    array.set(out, 1, v0*m01 + v1*m11 + v2*m21)
    array.set(out, 2, v0*m02 + v1*m12 + v2*m22)
    out

fmt_pct(p_val) => str.tostring(math.round(p_val * 100)) + "%"

// ── Last-bar: build P, iterate to stationary, populate tables ─────────────────
if barstate.islast

    // Row-normalise counts to get transition matrix P
    mrk_P = array.new_float(9, 0.0)
    for r = 0 to 2
        row_sum = array.get(mrk_counts, r*3) + array.get(mrk_counts, r*3+1) + array.get(mrk_counts, r*3+2)
        for c = 0 to 2
            cell_v = row_sum > 0 ? array.get(mrk_counts, r*3+c) / row_sum : 1.0/3.0
            array.set(mrk_P, r*3+c, cell_v)

    // Matrix-power iteration: M := M * P  (stationary_power - 1 times)
    mrk_M = array.copy(mrk_P)
    for _i = 1 to stationary_power - 1
        mrk_M := matmul_3x3(mrk_M, mrk_P)

    // Row 0 of converged M = stationary distribution (internal order: 0=Side, 1=Bull, 2=Bear)
    mrk_stat = array.new_float(3)
    array.set(mrk_stat, 0, array.get(mrk_M, 0))   // prob of Sideways
    array.set(mrk_stat, 1, array.get(mrk_M, 1))   // prob of Bull
    array.set(mrk_stat, 2, array.get(mrk_M, 2))   // prob of Bear

    // ── Forecast: project the current regime forward 3 bars ──────────────────
    // One-hot vector for "today" (internal order: 0=Side, 1=Bull, 2=Bear),
    // then iterate v · P to get the distribution at t+1, t+2, t+3.
    mrk_vec0 = array.new_float(3, 0.0)
    if not na(regime)
        array.set(mrk_vec0, regime, 1.0)
    mrk_fc1 = vecmul_3(mrk_vec0, mrk_P)
    mrk_fc2 = vecmul_3(mrk_fc1,  mrk_P)
    mrk_fc3 = vecmul_3(mrk_fc2,  mrk_P)

    // Banner — create on demand, delete the instant the toggle goes off
    if show_regime_banner
        if na(mrk_tbl_banner)
            mrk_tbl_banner := table.new(banner_pos_c, 1, 1, bgcolor=c_bg, border_width=0)
        table.cell(mrk_tbl_banner, 0, 0, "Currently: " + regime_name(regime),
                   text_color=color.white, bgcolor=regime_solid(regime),
                   text_size=size.large, text_halign=text.align_center)
    else if not na(mrk_tbl_banner)
        table.delete(mrk_tbl_banner)
        mrk_tbl_banner := na

    // Transition matrix table (4 cols x 6 rows) — create on demand, delete on toggle-off
    if show_matrix_table
        if na(mrk_tbl_matrix)
            mrk_tbl_matrix := table.new(matrix_pos_c, 4, 6, bgcolor=c_card_bg, border_width=2, border_color=c_card_bg, frame_color=c_card_frame, frame_width=1)
        // Row 0 — card header
        table.cell(mrk_tbl_matrix, 0, 0, "MARKOV REGIME", text_color=c_accent,    bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_left)
        table.cell(mrk_tbl_matrix, 1, 0, "",              bgcolor=c_card_bg)
        table.cell(mrk_tbl_matrix, 2, 0, "",              bgcolor=c_card_bg)
        table.cell(mrk_tbl_matrix, 3, 0, "3x3",           text_color=c_foot_txt,  bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_right)
        // Row 1 — column headers (tomorrow's state)
        table.cell(mrk_tbl_matrix, 0, 1, "TODAY",  text_color=c_hdr_txt,   bgcolor=c_card_bg, text_size=hdr_size)
        table.cell(mrk_tbl_matrix, 1, 1, "Bull",   text_color=c_bull_solid, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_center)
        table.cell(mrk_tbl_matrix, 2, 1, "Bear",   text_color=c_bear_solid, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_center)
        table.cell(mrk_tbl_matrix, 3, 1, "Side",   text_color=c_side_solid, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_center)
        // Rows 2-4: transition probabilities in display order (Bull / Bear / Side)
        for dr = 0 to 2
            ri        = disp_to_internal(dr)
            row_name  = dr == 0 ? "Bull" : dr == 1 ? "Bear" : "Side"
            row_col   = dr == 0 ? c_bull_solid : dr == 1 ? c_bear_solid : c_side_solid
            table.cell(mrk_tbl_matrix, 0, dr+2, row_name, text_color=row_col, bgcolor=c_card_bg, text_size=hdr_size)
            for dc = 0 to 2
                ci      = disp_to_internal(dc)
                p_v     = array.get(mrk_P, ri*3+ci)
                is_diag = ri == ci
                table.cell(mrk_tbl_matrix, dc+1, dr+2, fmt_pct(p_v),
                           text_color = is_diag ? c_diag_txt : c_off_txt,
                           bgcolor    = is_diag ? c_diag_bg  : c_card_bg,
                           text_size=val_size, text_halign=text.align_center)
        // Row 5 — footer
        table.cell(mrk_tbl_matrix, 0, 5, "rows sum to 100%", text_color=c_foot_txt, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_left)
        table.cell(mrk_tbl_matrix, 1, 5, "", bgcolor=c_card_bg)
        table.cell(mrk_tbl_matrix, 2, 5, "", bgcolor=c_card_bg)
        table.cell(mrk_tbl_matrix, 3, 5, "", bgcolor=c_card_bg)
    else if not na(mrk_tbl_matrix)
        table.delete(mrk_tbl_matrix)
        mrk_tbl_matrix := na

    // Stationary distribution table (3 cols x 4 rows) — create on demand, delete on toggle-off
    if show_stationary_table
        if na(mrk_tbl_stationary)
            mrk_tbl_stationary := table.new(stationary_pos_c, 3, 4, bgcolor=c_card_bg, border_width=2, border_color=c_card_bg, frame_color=c_card_frame, frame_width=1)
        table.cell(mrk_tbl_stationary, 0, 0, "LONG-RUN MIX", text_color=c_accent, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_left)
        table.cell(mrk_tbl_stationary, 1, 0, "", bgcolor=c_card_bg)
        table.cell(mrk_tbl_stationary, 2, 0, "", bgcolor=c_card_bg)
        table.cell(mrk_tbl_stationary, 0, 1, "Bull", text_color=c_bull_solid, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_center)
        table.cell(mrk_tbl_stationary, 1, 1, "Bear", text_color=c_bear_solid, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_center)
        table.cell(mrk_tbl_stationary, 2, 1, "Side", text_color=c_side_solid, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_center)
        // Values: disp_to_internal maps display index to internal regime code
        for dc = 0 to 2
            ci_s    = disp_to_internal(dc)
            stat_v  = array.get(mrk_stat, ci_s)
            is_bull = dc == 0
            table.cell(mrk_tbl_stationary, dc, 2, fmt_pct(stat_v),
                       text_color = is_bull ? c_diag_txt : c_off_txt,
                       bgcolor    = is_bull ? c_diag_bg  : c_card_bg,
                       text_size=val_size, text_halign=text.align_center)
        table.cell(mrk_tbl_stationary, 0, 3, "stat",          text_color=c_foot_txt, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_center)
        table.cell(mrk_tbl_stationary, 1, 3, "sums to 100%",  text_color=c_foot_txt, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_left)
        table.cell(mrk_tbl_stationary, 2, 3, "",               bgcolor=c_card_bg)
    else if not na(mrk_tbl_stationary)
        table.delete(mrk_tbl_stationary)
        mrk_tbl_stationary := na

    // Forecast table (4 cols x 5 rows) — row "∞" shows the long-run/stationary
    // mix as a baseline reference, rows t+1..t+3 show the regime distribution
    // projected from today's regime via v·P, v·P², v·P³. Best-guess regime
    // per row is highlighted like the matrix diagonal.
    // Created on demand, deleted the instant the toggle goes off.
    if show_forecast_table
        if na(mrk_tbl_forecast)
            mrk_tbl_forecast := table.new(forecast_pos_c, 4, 5, bgcolor=c_card_bg, border_width=2, border_color=c_card_bg, frame_color=c_card_frame, frame_width=1)
        table.cell(mrk_tbl_forecast, 0, 0, "FORECAST +3", text_color=c_accent, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_left)
        table.cell(mrk_tbl_forecast, 1, 0, "Bull", text_color=c_bull_solid, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_center)
        table.cell(mrk_tbl_forecast, 2, 0, "Bear", text_color=c_bear_solid, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_center)
        table.cell(mrk_tbl_forecast, 3, 0, "Side", text_color=c_side_solid, bgcolor=c_card_bg, text_size=hdr_size, text_halign=text.align_center)

        // ∞ — long-run/stationary mix as baseline reference
        pt_bull = array.get(mrk_stat, 1)
        pt_bear = array.get(mrk_stat, 2)
        pt_side = array.get(mrk_stat, 0)
        bestT   = math.max(pt_bull, math.max(pt_bear, pt_side))
        table.cell(mrk_tbl_forecast, 0, 1, "∞", text_color=c_hdr_txt, bgcolor=c_card_bg, text_size=hdr_size)
        table.cell(mrk_tbl_forecast, 1, 1, fmt_pct(pt_bull), text_color = pt_bull == bestT ? c_diag_txt : c_off_txt, bgcolor = pt_bull == bestT ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)
        table.cell(mrk_tbl_forecast, 2, 1, fmt_pct(pt_bear), text_color = pt_bear == bestT ? c_diag_txt : c_off_txt, bgcolor = pt_bear == bestT ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)
        table.cell(mrk_tbl_forecast, 3, 1, fmt_pct(pt_side), text_color = pt_side == bestT ? c_diag_txt : c_off_txt, bgcolor = pt_side == bestT ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)

        // t+1
        p1_bull = array.get(mrk_fc1, 1)
        p1_bear = array.get(mrk_fc1, 2)
        p1_side = array.get(mrk_fc1, 0)
        best1   = math.max(p1_bull, math.max(p1_bear, p1_side))
        table.cell(mrk_tbl_forecast, 0, 2, "t+1", text_color=c_hdr_txt, bgcolor=c_card_bg, text_size=hdr_size)
        table.cell(mrk_tbl_forecast, 1, 2, fmt_pct(p1_bull), text_color = p1_bull == best1 ? c_diag_txt : c_off_txt, bgcolor = p1_bull == best1 ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)
        table.cell(mrk_tbl_forecast, 2, 2, fmt_pct(p1_bear), text_color = p1_bear == best1 ? c_diag_txt : c_off_txt, bgcolor = p1_bear == best1 ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)
        table.cell(mrk_tbl_forecast, 3, 2, fmt_pct(p1_side), text_color = p1_side == best1 ? c_diag_txt : c_off_txt, bgcolor = p1_side == best1 ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)

        // t+2
        p2_bull = array.get(mrk_fc2, 1)
        p2_bear = array.get(mrk_fc2, 2)
        p2_side = array.get(mrk_fc2, 0)
        best2   = math.max(p2_bull, math.max(p2_bear, p2_side))
        table.cell(mrk_tbl_forecast, 0, 3, "t+2", text_color=c_hdr_txt, bgcolor=c_card_bg, text_size=hdr_size)
        table.cell(mrk_tbl_forecast, 1, 3, fmt_pct(p2_bull), text_color = p2_bull == best2 ? c_diag_txt : c_off_txt, bgcolor = p2_bull == best2 ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)
        table.cell(mrk_tbl_forecast, 2, 3, fmt_pct(p2_bear), text_color = p2_bear == best2 ? c_diag_txt : c_off_txt, bgcolor = p2_bear == best2 ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)
        table.cell(mrk_tbl_forecast, 3, 3, fmt_pct(p2_side), text_color = p2_side == best2 ? c_diag_txt : c_off_txt, bgcolor = p2_side == best2 ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)

        // t+3
        p3_bull = array.get(mrk_fc3, 1)
        p3_bear = array.get(mrk_fc3, 2)
        p3_side = array.get(mrk_fc3, 0)
        best3   = math.max(p3_bull, math.max(p3_bear, p3_side))
        table.cell(mrk_tbl_forecast, 0, 4, "t+3", text_color=c_hdr_txt, bgcolor=c_card_bg, text_size=hdr_size)
        table.cell(mrk_tbl_forecast, 1, 4, fmt_pct(p3_bull), text_color = p3_bull == best3 ? c_diag_txt : c_off_txt, bgcolor = p3_bull == best3 ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)
        table.cell(mrk_tbl_forecast, 2, 4, fmt_pct(p3_bear), text_color = p3_bear == best3 ? c_diag_txt : c_off_txt, bgcolor = p3_bear == best3 ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)
        table.cell(mrk_tbl_forecast, 3, 4, fmt_pct(p3_side), text_color = p3_side == best3 ? c_diag_txt : c_off_txt, bgcolor = p3_side == best3 ? c_diag_bg : c_card_bg, text_size=val_size, text_halign=text.align_center)
    else if not na(mrk_tbl_forecast)
        table.delete(mrk_tbl_forecast)
        mrk_tbl_forecast := na
"""


def _t_gan(p: dict) -> str:
    """Return the Pine Script v5 Gann Levels overlay template with configurable params."""
    n      = max(1, min(8, int(p.get('levels', 5))))
    step   = {'1/8': 0.125, '1/4': 0.25, '1/2': 0.5}.get(str(p.get('step_frac', '1/8')), 0.125)
    anchor = {'close': 'close', 'hl2': 'hl2', 'hlc3': 'hlc3'}.get(
        str(p.get('anchor', 'close')), 'close')

    # Transparency increases with distance from price (0 = opaque for R1/S1)
    transp = [0, 20, 40, 60, 70, 75, 80, 85]

    level_vars = []
    for i in range(n):
        level_vars.append(
            f'float gan_R{i+1} = math.pow((gan_ka + {i}.0) * gan_stp, 2.0) * gan_f'
        )
        level_vars.append(
            f'float gan_S{i+1} = math.pow((gan_kb - {i}.0) * gan_stp, 2.0) * gan_f'
        )

    plot_calls = []
    for i in range(1, n + 1):
        w  = 2 if i == 1 else 1
        tr = transp[min(i - 1, len(transp) - 1)]
        plot_calls.append(
            f'plot(gan_R{i}, "Gann R{i}", color.new(color.red,   {tr}), {w}, plot.style_linebr)'
        )
        plot_calls.append(
            f'plot(gan_S{i}, "Gann S{i}", color.new(color.green, {tr}), {w}, plot.style_linebr)'
        )

    # Label declarations and barstate.islast block
    lbl_decls = '\n'.join(
        f'var label _ganR{i}_lbl = na\nvar label _ganS{i}_lbl = na'
        for i in range(1, n + 1)
    )
    lbl_deletes = '\n    '.join(
        f'label.delete(_ganR{i}_lbl)\n    label.delete(_ganS{i}_lbl)'
        for i in range(1, n + 1)
    )
    lbl_creates = '\n    '.join(
        (f'_ganR{i}_lbl := label.new(bar_index + 1, gan_R{i}, "GR{i}: " + str.tostring(math.round(gan_R{i}, 2)),'
         f' xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.red,   {transp[min(i-1,len(transp)-1)]}), textcolor=color.white, style=label.style_label_left, size=size.small)\n    '
         f'_ganS{i}_lbl := label.new(bar_index + 1, gan_S{i}, "GS{i}: " + str.tostring(math.round(gan_S{i}, 2)),'
         f' xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.green, {transp[min(i-1,len(transp)-1)]}), textcolor=color.white, style=label.style_label_left, size=size.small)')
        for i in range(1, n + 1)
    )

    levels_block = '\n'.join(level_vars)
    plots_block  = '\n'.join(plot_calls)

    return f"""\
// ── Gann Square of 9 ──────────────────────────────────────────────────────────
// Auto-scaling: factor = 10^(floor(log10(price)) - 1) keeps price/factor in
// [10, 1000) so octant steps give consistent ~2-3% spacing at any price.
float gan_anc = {anchor}
float gan_f   = math.pow(10.0, math.floor(math.log(math.max(gan_anc, 1e-10)) / math.log(10.0)) - 2.0)
float gan_num = gan_anc / gan_f
float gan_stp = {step}
float gan_sqn = math.sqrt(gan_num)
// kb: largest index with (kb*stp)^2*f strictly below price; ka: smallest strictly above
float gan_kb  = math.floor(gan_sqn / gan_stp)
gan_kb := math.pow(gan_kb * gan_stp, 2.0) * gan_f >= gan_anc ? gan_kb - 1.0 : gan_kb
float gan_ka  = gan_kb + 1.0
gan_ka := math.pow(gan_ka * gan_stp, 2.0) * gan_f <= gan_anc ? gan_ka + 1.0 : gan_ka
{levels_block}
{plots_block}
{lbl_decls}
if barstate.islast
    {lbl_deletes}
    {lbl_creates}
"""


def _t_pvt(p: dict) -> str:
    """Return the Pine Script v5 Pivot Points overlay template with configurable params."""
    method = str(p.get('method', 'classic')).lower()

    if method == 'fibonacci':
        pivot_block = (
            "float pvt_p  = (pvt_h + pvt_l + pvt_c) / 3\n"
            "float pvt_r1 = pvt_p + 0.382 * pvt_r\n"
            "float pvt_r2 = pvt_p + 0.618 * pvt_r\n"
            "float pvt_r3 = pvt_p + 1.000 * pvt_r\n"
            "float pvt_s1 = pvt_p - 0.382 * pvt_r\n"
            "float pvt_s2 = pvt_p - 0.618 * pvt_r\n"
            "float pvt_s3 = pvt_p - 1.000 * pvt_r"
        )
        method_label = "Fibonacci"
    elif method == 'woodie':
        pivot_block = (
            "float pvt_p  = (pvt_h + pvt_l + 2 * pvt_c) / 4\n"
            "float pvt_r1 = 2 * pvt_p - pvt_l\n"
            "float pvt_r2 = pvt_p + pvt_r\n"
            "float pvt_r3 = pvt_h + 2 * (pvt_p - pvt_l)\n"
            "float pvt_s1 = 2 * pvt_p - pvt_h\n"
            "float pvt_s2 = pvt_p - pvt_r\n"
            "float pvt_s3 = pvt_l - 2 * (pvt_h - pvt_p)"
        )
        method_label = "Woodie"
    else:  # classic (floor trader)
        pivot_block = (
            "float pvt_p  = (pvt_h + pvt_l + pvt_c) / 3\n"
            "float pvt_r1 = 2 * pvt_p - pvt_l\n"
            "float pvt_r2 = pvt_p + pvt_r\n"
            "float pvt_r3 = pvt_h + 2 * (pvt_p - pvt_l)\n"
            "float pvt_s1 = 2 * pvt_p - pvt_h\n"
            "float pvt_s2 = pvt_p - pvt_r\n"
            "float pvt_s3 = pvt_l - 2 * (pvt_h - pvt_p)"
        )
        method_label = "Classic"

    return f"""\
// ── Pivot Points ({method_label}) ─────────────────────────────────────────────────
// Levels from the previous bar's High / Low / Close — drawn as static hlines on
// the last bar only (extend.both) so no historical trend is shown.
float pvt_h = high[1]
float pvt_l = low[1]
float pvt_c = close[1]
float pvt_r = pvt_h - pvt_l
{pivot_block}
var line  _pvt_r3_line = na
var line  _pvt_r2_line = na
var line  _pvt_r1_line = na
var line  _pvt_p_line  = na
var line  _pvt_s1_line = na
var line  _pvt_s2_line = na
var line  _pvt_s3_line = na
var label _pvt_r3_lbl  = na
var label _pvt_r2_lbl  = na
var label _pvt_r1_lbl  = na
var label _pvt_p_lbl   = na
var label _pvt_s1_lbl  = na
var label _pvt_s2_lbl  = na
var label _pvt_s3_lbl  = na
if barstate.islast
    line.delete(_pvt_r3_line)
    line.delete(_pvt_r2_line)
    line.delete(_pvt_r1_line)
    line.delete(_pvt_p_line)
    line.delete(_pvt_s1_line)
    line.delete(_pvt_s2_line)
    line.delete(_pvt_s3_line)
    label.delete(_pvt_r3_lbl)
    label.delete(_pvt_r2_lbl)
    label.delete(_pvt_r1_lbl)
    label.delete(_pvt_p_lbl)
    label.delete(_pvt_s1_lbl)
    label.delete(_pvt_s2_lbl)
    label.delete(_pvt_s3_lbl)
    _pvt_r3_line := line.new(bar_index, pvt_r3, bar_index + 1, pvt_r3, color=color.new(color.red,   40), width=1, style=line.style_dashed, extend=extend.both)
    _pvt_r2_line := line.new(bar_index, pvt_r2, bar_index + 1, pvt_r2, color=color.new(color.red,   20), width=1, style=line.style_dashed, extend=extend.both)
    _pvt_r1_line := line.new(bar_index, pvt_r1, bar_index + 1, pvt_r1, color=color.new(color.red,    0), width=2, style=line.style_dashed, extend=extend.both)
    _pvt_p_line  := line.new(bar_index, pvt_p,  bar_index + 1, pvt_p,  color=color.new(color.orange, 0), width=2, style=line.style_dotted, extend=extend.both)
    _pvt_s1_line := line.new(bar_index, pvt_s1, bar_index + 1, pvt_s1, color=color.new(color.green,  0), width=2, style=line.style_dashed, extend=extend.both)
    _pvt_s2_line := line.new(bar_index, pvt_s2, bar_index + 1, pvt_s2, color=color.new(color.green, 20), width=1, style=line.style_dashed, extend=extend.both)
    _pvt_s3_line := line.new(bar_index, pvt_s3, bar_index + 1, pvt_s3, color=color.new(color.green, 40), width=1, style=line.style_dashed, extend=extend.both)
    _pvt_r3_lbl  := label.new(bar_index + 1, pvt_r3, "R3: " + str.tostring(math.round(pvt_r3, 2)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.red,   40), textcolor=color.white, style=label.style_label_left, size=size.small)
    _pvt_r2_lbl  := label.new(bar_index + 1, pvt_r2, "R2: " + str.tostring(math.round(pvt_r2, 2)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.red,   20), textcolor=color.white, style=label.style_label_left, size=size.small)
    _pvt_r1_lbl  := label.new(bar_index + 1, pvt_r1, "R1: " + str.tostring(math.round(pvt_r1, 2)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.red,    0), textcolor=color.white, style=label.style_label_left, size=size.small)
    _pvt_p_lbl   := label.new(bar_index + 1, pvt_p,  "P:  " + str.tostring(math.round(pvt_p,  2)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.orange, 0), textcolor=color.white, style=label.style_label_left, size=size.small)
    _pvt_s1_lbl  := label.new(bar_index + 1, pvt_s1, "S1: " + str.tostring(math.round(pvt_s1, 2)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.green,  0), textcolor=color.white, style=label.style_label_left, size=size.small)
    _pvt_s2_lbl  := label.new(bar_index + 1, pvt_s2, "S2: " + str.tostring(math.round(pvt_s2, 2)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.green, 20), textcolor=color.white, style=label.style_label_left, size=size.small)
    _pvt_s3_lbl  := label.new(bar_index + 1, pvt_s3, "S3: " + str.tostring(math.round(pvt_s3, 2)), xloc=xloc.bar_index, yloc=yloc.price, color=color.new(color.green, 40), textcolor=color.white, style=label.style_label_left, size=size.small)
"""


def _t_nsdt(p: dict) -> str:
    """Return the Pine Script v5 NSDT Hama Candles overlay template with configurable params."""
    open_len  = int(p.get('open_len',  21))
    close_len = int(p.get('close_len', 14))
    ma_len    = int(p.get('ma_len',   100))
    steps     = int(p.get('steps',      5))
    return f"""\
// ── NSDT HAMA Candles ─────────────────────────────────────────────────────────
// Back-ported from Pine Script definitions (NSDT HAMA Candles by NSDT).
nsdt_ol = {open_len}    // EMA length for HAMA open
nsdt_cl = {close_len}    // EMA length for HAMA close
nsdt_ml = {ma_len}   // Trend MA length
nsdt_st = {steps}     // Gradient colour steps

// HAMA candle components
nsdt_src_o  = (open[1] + close[1]) / 2.0
nsdt_src_c  = (open + high + low + close) / 4.0
nsdt_hama_o = ta.ema(nsdt_src_o,            nsdt_ol)
nsdt_hama_c = ta.ema(nsdt_src_c,            nsdt_cl)
nsdt_hama_h = ta.ema(math.max(high, close), 20)
nsdt_hama_l = ta.ema(math.min(low,  close), 20)

// Trend MA and 3-bar smoothed centre line
nsdt_ma   = ta.ema(hl2, nsdt_ml)
nsdt_mctr = ta.ema(nsdt_ma, 3)
nsdt_chg  = nsdt_ma - nsdt_ma[1]
nsdt_xup  = ta.crossover(nsdt_ma,  nsdt_mctr)
nsdt_xdn  = ta.crossunder(nsdt_ma, nsdt_mctr)

// Gradient state machine: mirrors Python "var qty" persistent counter
var int nsdt_qty = 0
if nsdt_ma > nsdt_mctr
    if nsdt_xup
        nsdt_qty := 1
    else if nsdt_chg > 0
        nsdt_qty := math.min(nsdt_st, nsdt_qty + 1)
    else if nsdt_chg < 0
        nsdt_qty := math.max(1, nsdt_qty - 1)
else if nsdt_ma < nsdt_mctr
    if nsdt_xdn
        nsdt_qty := 1
    else if nsdt_chg < 0
        nsdt_qty := math.min(nsdt_st, nsdt_qty + 1)
    else if nsdt_chg > 0
        nsdt_qty := math.max(1, nsdt_qty - 1)

// Colour gradient: bull = yellow → green, bear = yellow → red
nsdt_ratio = float(nsdt_qty) / float(nsdt_st)
nsdt_bull  = nsdt_ma >= nsdt_mctr
nsdt_col   = nsdt_bull
             ? color.from_gradient(nsdt_ratio, 0.0, 1.0, color.yellow, color.lime)
             : color.from_gradient(nsdt_ratio, 0.0, 1.0, color.yellow, color.red)

plotcandle(nsdt_hama_o, nsdt_hama_h, nsdt_hama_l, nsdt_hama_c, "HAMA",
           nsdt_col, nsdt_col, bordercolor = color.new(color.gray, 70))
plot(nsdt_ma, "NSDT MA", nsdt_col, 2)
"""


def _t_oft(p: dict) -> str:
    """Return the Pine Script v5 Order Flow Tools overlay template with configurable params."""
    period     = int(p.get('period',        21))
    ob_periods = int(p.get('ob_periods',     3))
    ob_thr     = float(p.get('ob_threshold', 0.0))
    use_wicks  = bool(p.get('use_wicks',  False))
    wick_comment = 'true' if use_wicks else 'false'
    return f"""\
// ── Order Flow Tracker ────────────────────────────────────────────────────────
oft_period     = {period}
oft_ob_periods = {ob_periods}
oft_ob_thr     = {ob_thr}

// 1. Order Flow Imbalance (buy / sell volume split by price direction)
oft_chg     = close - close[1]
oft_buy_vol = oft_chg > 0 ? volume : 0.0
oft_sel_vol = oft_chg < 0 ? volume : 0.0
oft_ofi     = math.sum(oft_buy_vol - oft_sel_vol, oft_period)
oft_sofi    = ta.ema(oft_ofi, 5)   // smoothed OFI (available for alerts)

// 2. Rolling Support / Resistance bands
oft_sup = ta.lowest(close,  oft_period)
oft_res = ta.highest(close, oft_period)
plot(oft_sup, "OFT Support",    color.new(color.teal,   40), 1, plot.style_linebr)
plot(oft_res, "OFT Resistance", color.new(color.orange, 40), 1, plot.style_linebr)

// 3. Contrarian signals: price near S/R AND OFI pushing the other way
oft_pct  = 0.01
oft_buy  = close >= oft_sup * (1.0 - oft_pct) and close <= oft_sup * (1.0 + oft_pct) and oft_ofi < 0
oft_sell = close >= oft_res * (1.0 - oft_pct) and close <= oft_res * (1.0 + oft_pct) and oft_ofi > 0
plotshape(oft_buy,  "OFT Buy",  shape.triangleup,   location.belowbar, color.teal, size = size.small)
plotshape(oft_sell, "OFT Sell", shape.triangledown, location.abovebar, color.red,  size = size.small)

// 4. Order Block detection (use_wicks = {wick_comment})
// Bullish OB: red candle oft_ob_periods bars ago + oft_ob_periods consecutive green candles
// Bearish OB: green candle oft_ob_periods bars ago + oft_ob_periods consecutive red candles
oft_red_setup = close[oft_ob_periods] < open[oft_ob_periods]
oft_grn_setup = close[oft_ob_periods] > open[oft_ob_periods]
oft_absmove   = math.abs((close - close[oft_ob_periods]) / close[oft_ob_periods]) * 100.0
oft_move_ok   = oft_ob_thr <= 0.0 or oft_absmove >= oft_ob_thr

bool oft_up_seq = true
bool oft_dn_seq = true
for j = 0 to oft_ob_periods - 1
    oft_up_seq := oft_up_seq and close[j] > open[j]
    oft_dn_seq := oft_dn_seq and close[j] < open[j]

oft_bull_ob = oft_move_ok and oft_red_setup and oft_up_seq
oft_bear_ob = oft_move_ok and oft_grn_setup and oft_dn_seq
plotshape(oft_bull_ob, "OFT Bullish OB", shape.xcross, location.belowbar, color.green, size = size.small)
plotshape(oft_bear_ob, "OFT Bearish OB", shape.xcross, location.abovebar, color.red,   size = size.small)
"""


def _t_mmm(p: dict) -> str:
    """Return the Pine Script v5 Market Mood Meter overlay template with configurable params."""
    show_trend  = bool(p.get('show_trend', False))
    trend_block = """\
// Trend overlay (show_trend = true)
mmm_ema50 = ta.ema(close, 50)
plot(mmm_ema50, "MMM Trend EMA50", close > mmm_ema50 ? color.green : color.red, 2)
""" if show_trend else ''
    return f"""\
// ── Market Maker Master Pattern (with Fibonacci) ──────────────────────────────
// Phase detection thresholds match the Python implementation:
//   Contraction: BB-width < 0.02 AND ATR < 70% of its 50-bar SMA
//   Expansion:   ATR jumped > 20% vs previous bar   |   else: Trend
// Fibonacci: simplified from local-extrema to ta.highest / ta.lowest.
// HTF: request.security maps to Python aggregate_to_htf("D").
mmm_bb_len  = 20
mmm_atr_len = 14
mmm_fib_len = 50

// Phase detection
mmm_atr     = ta.atr(mmm_atr_len)
mmm_atr_avg = ta.sma(mmm_atr, 50)
mmm_bb_mid  = ta.sma(close, mmm_bb_len)
mmm_bb_std  = ta.stdev(close, mmm_bb_len)
mmm_bb_w    = mmm_bb_mid != 0.0 ? (2.0 * mmm_bb_std) / mmm_bb_mid : na
mmm_contr   = mmm_bb_w < 0.02 and mmm_atr < mmm_atr_avg * 0.7
mmm_expan   = mmm_atr > mmm_atr[1] * 1.2
bgcolor(mmm_contr ? color.new(color.blue,   88) : na, title = "MMM Contraction")
bgcolor(mmm_expan ? color.new(color.orange, 88) : na, title = "MMM Expansion")

// Higher-timeframe daily close (mirrors Python resample("D"))
mmm_htf_c = request.security(syminfo.tickerid, "D", close)
plot(mmm_htf_c, "MMM HTF Close", color.new(color.yellow, 20), 2)

// Fibonacci from swing high / low over last mmm_fib_len bars
mmm_sh = ta.highest(high, mmm_fib_len)
mmm_sl = ta.lowest(low,   mmm_fib_len)
mmm_fd = mmm_sh - mmm_sl
plot(mmm_sh,                  "MMM Fib 1.000", color.new(color.red,    0), 1, plot.style_linebr)
plot(mmm_sh - mmm_fd * 0.236, "MMM Fib 0.764", color.new(color.orange, 0), 1, plot.style_linebr)
plot(mmm_sh - mmm_fd * 0.382, "MMM Fib 0.618", color.new(color.yellow, 0), 1, plot.style_linebr)
plot(mmm_sh - mmm_fd * 0.500, "MMM Fib 0.500", color.new(color.gray,   0), 1, plot.style_linebr)
plot(mmm_sh - mmm_fd * 0.618, "MMM Fib 0.382", color.new(color.lime,   0), 1, plot.style_linebr)
plot(mmm_sl,                  "MMM Fib 0.000", color.new(color.green,  0), 1, plot.style_linebr)

// CumDelta momentum signal (simplified: volume signed by price direction)
var float mmm_cumd = 0.0
mmm_cumd     += close > close[1] ? volume : close < close[1] ? -volume : 0.0
mmm_cumd_sma  = ta.sma(mmm_cumd, 21)
plotshape(ta.crossover(mmm_cumd,  mmm_cumd_sma), "MMM CD Bull", shape.triangleup,   location.belowbar, color.teal, size = size.tiny)
plotshape(ta.crossunder(mmm_cumd, mmm_cumd_sma), "MMM CD Bear", shape.triangledown, location.abovebar, color.red,  size = size.tiny)
{trend_block}"""


def _t_mam(p: dict) -> str:
    """Return the Pine Script v5 MA Multi overlay template with configurable params."""
    _N            = 5
    _defaults     = [('EMA', 9), ('EMA', 21), ('SMA', 50), ('SMA', 100), ('SMA', 200)]
    _default_en   = [True, True, True, True, True]
    _default_cols = ['color.orange', 'color.rgb(0, 191, 255)', 'color.black', 'color.gray', 'color.red']
    _pine_ma      = {'SMA': 'ta.sma', 'EMA': 'ta.ema', 'WMA': 'ta.wma'}

    # float width → Pine plot() integer linewidth (plot() only accepts int 1-4)
    _width_to_pine_int = {'0.5': 1, '1': 1, '1.5': 2, '2': 2}

    cfgs = [
        (
            bool(p.get(f'ma{i}_enabled', _default_en[i - 1])),
            str(p.get(f'ma{i}_type',    _defaults[i - 1][0])).upper(),
            int(p.get(f'ma{i}_period',  _defaults[i - 1][1])),
            _hex_to_pine_color(p.get(f'ma{i}_color', ''), _default_cols[i - 1]),
            str(p.get(f'ma{i}_width',   '1.5')),
            str(p.get(f'ma{i}_style',   'solid')),
        )
        for i in range(1, _N + 1)
    ]

    grp = "MA Multi -- Style"
    lines = ["// ── MA Multi ──────────────────────────────────────────────────────────────────\n"]

    for idx, (enabled, ma_type, period, col, width, style) in enumerate(cfgs, 1):
        pine_en  = 'true' if enabled else 'false'
        pine_wid = _width_to_pine_int.get(width, 2)
        sty      = style if style in ('solid', 'dashed', 'dotted') else 'solid'
        lbl      = f'{ma_type} {period}'
        lines.append(f'mam{idx}_show  = input.bool({pine_en},   "Show {lbl}",  group="{grp}")\n')
        lines.append(f'mam{idx}_col   = input.color({col},      "{lbl} color", group="{grp}")\n')
        lines.append(f'mam{idx}_width = input.int({pine_wid},   "{lbl} width", minval=1, maxval=4, group="{grp}")\n')
        # Pine Script plot() does not support dashed/dotted; the style input is kept for
        # completeness (e.g. custom line.new() usage) but has no effect on plot() lines.
        lines.append(f'mam{idx}_sty   = input.string("{sty}", "{lbl} style", options=["solid","dashed","dotted"], group="{grp}")\n')
    lines.append('\n')

    if any(ma_type == 'DEMA' for _, ma_type, *_ in cfgs):
        lines.append("""\
dema(src, len) =>
    _e1 = ta.ema(src, len)
    2 * _e1 - ta.ema(_e1, len)
""")

    for idx, (_, ma_type, period, _, _, _) in enumerate(cfgs, 1):
        var   = f'mam{idx}'
        label = f'{ma_type} {period}'
        if ma_type == 'DEMA':
            lines.append(f'{var} = mam{idx}_show ? dema(close, {period}) : na\n')
        else:
            fn = _pine_ma.get(ma_type, 'ta.ema')
            lines.append(f'{var} = mam{idx}_show ? {fn}(close, {period}) : na\n')
        lines.append(f'plot({var}, "{label}", mam{idx}_col, mam{idx}_width)\n')

    # Right-side labels at the last bar (one per MA, deleted+recreated on update)
    lines.append('\n')
    for idx, (_, ma_type, period, _, _, _) in enumerate(cfgs, 1):
        var   = f'mam{idx}'
        lbl   = f'{ma_type} {period}'
        lines.append(f'var label _mam{idx}_lbl = na\n')
        lines.append(
            f'if barstate.islast and mam{idx}_show and not na({var})\n'
            f'    label.delete(_mam{idx}_lbl)\n'
            f'    _mam{idx}_lbl := label.new(bar_index + 1, {var}, "{lbl}",\n'
            f'         xloc=xloc.bar_index, yloc=yloc.price,\n'
            f'         color=color.new(mam{idx}_col, 20), textcolor=color.white,\n'
            f'         style=label.style_label_left, size=size.small)\n'
        )

    return ''.join(lines)


# ── Normalized oscillator templates (for combined overlay=true chart) ──────────
#
# Each function takes (p: dict, slot: int) and returns Pine Script that:
#   • calculates the oscillator value
#   • maps every plotted y-value through _osc_px(val, v_lo, v_hi, slot)
#   • _osc_px / _floor / _osc_slot are defined by generate_combined()'s header
#   • slot 0 = bottom-most oscillator, slot N-1 = top-most
#
# Fixed y-ranges are used for bounded oscillators (RSI/Stoch/ADX/DEMA: 0–100,
# ZCR: ±4); dynamic oscillators use ta.lowest/ta.highest over a rolling window.

def _n_macd(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized MACD block for strategy scripts."""
    fast = int(p.get('window_fast', 12))
    slow = int(p.get('window_slow', 26))
    sign = int(p.get('window_sign', 9))
    col_macd   = _hex_to_pine_color(p.get('color_macd',   ''), 'color.black')
    col_signal = _hex_to_pine_color(p.get('color_signal', ''), 'color.blue')
    wid = max(1, min(5, int(p.get('line_width', 2))))
    sty = p.get('line_style', 'solid')
    if sty not in ('solid', 'dashed', 'dotted'):
        sty = 'solid'
    grp = "MACD (combined) -- Style"
    return f"""\
// ── MACD  (slot {slot}) ───────────────────────────────────────────────────────
n_macd_col     = input.color({col_macd},   "MACD color",   group="{grp}")
n_macd_sig_col = input.color({col_signal}, "Signal color", group="{grp}")
n_macd_width   = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
n_macd_style   = input.string("{sty}", "Line style", options=["solid","dashed","dotted"], group="{grp}")
[_macd_l, _macd_s, _macd_h] = ta.macd(close, {fast}, {slow}, {sign})
_macd_lo = ta.lowest( math.min(_macd_l, math.min(_macd_s, _macd_h)), 300)
_macd_hi = ta.highest(math.max(_macd_l, math.max(_macd_s, _macd_h)), 300)
_macd_z  = plot(_osc_px(0.0,     _macd_lo, _macd_hi, {slot}), "MACD zero", color.new(color.gray, 100))
_macd_hb = plot(_osc_px(_macd_h, _macd_lo, _macd_hi, {slot}), "MACD Hist",
                _macd_h >= 0 ? color.new(color.green, 30) : color.new(color.red, 30), 1)
fill(_macd_z, _macd_hb, _macd_h >= 0 ? color.new(color.green, 50) : color.new(color.red, 50))
plot(_osc_px(_macd_l, _macd_lo, _macd_hi, {slot}), "MACD",   n_macd_col,     n_macd_width)
plot(_osc_px(_macd_s, _macd_lo, _macd_hi, {slot}), "Signal", n_macd_sig_col, 1)
"""


def _n_rsi(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized RSI block for strategy scripts."""
    lb  = int(p.get('lookback', 8))
    win = int(p.get('window',  14))
    style = _style_inputs(p, 'n_rsi', 'color.blue', 'RSI (combined)')
    return f"""\
// ── RSI  (slot {slot}) ────────────────────────────────────────────────────────
{style}_rsi_val = ta.rsi(close, {lb})
_rsi_ema = ta.sma(_rsi_val, {win})
plot(_osc_px(_rsi_val, 0.0, 100.0, {slot}), "RSI",     n_rsi_col,  n_rsi_width)
plot(_osc_px(_rsi_ema, 0.0, 100.0, {slot}), "RSI EMA", color.gray, 1)
plot(_osc_px(75.0, 0.0, 100.0, {slot}), "RSI 75", color.new(color.green, 60), 1, plot.style_linebr)
plot(_osc_px(50.0, 0.0, 100.0, {slot}), "RSI 50", color.new(color.gray,  60), 1, plot.style_linebr)
plot(_osc_px(25.0, 0.0, 100.0, {slot}), "RSI 25", color.new(color.red,   60), 1, plot.style_linebr)
"""


def _n_cci(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized CCI block for strategy scripts."""
    win = int(p.get('window', 14))
    style = _style_inputs(p, 'n_cci', 'color.navy', 'CCI (combined)')
    return f"""\
// ── CCI  (slot {slot}) ────────────────────────────────────────────────────────
{style}_cci_val = ta.cci(hlc3, {win})
_cci_lo  = ta.lowest(_cci_val,  300)
_cci_hi  = ta.highest(_cci_val, 300)
plot(_osc_px(_cci_val,   _cci_lo, _cci_hi, {slot}), "CCI", n_cci_col, n_cci_width)
plot(_osc_px( 100.0, _cci_lo, _cci_hi, {slot}), "CCI +100", color.new(color.green, 60), 1, plot.style_linebr)
plot(_osc_px(-100.0, _cci_lo, _cci_hi, {slot}), "CCI -100", color.new(color.red,   60), 1, plot.style_linebr)
"""


def _n_adx(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized ADX block for strategy scripts."""
    win   = int(p.get('window',      14))
    level = int(p.get('down_level',  25))
    col_adx      = _hex_to_pine_color(p.get('color_adx',      ''), 'color.blue')
    col_plus_di  = _hex_to_pine_color(p.get('color_plus_di',  ''), 'color.green')
    col_minus_di = _hex_to_pine_color(p.get('color_minus_di', ''), 'color.red')
    wid = max(1, min(5, int(p.get('line_width', 2))))
    sty = p.get('line_style', 'solid')
    if sty not in ('solid', 'dashed', 'dotted'):
        sty = 'solid'
    grp = "ADX (combined) -- Style"
    return f"""\
// ── ADX  (slot {slot}) ────────────────────────────────────────────────────────
n_adx_col      = input.color({col_adx},      "ADX color", group="{grp}")
n_adx_plus_col = input.color({col_plus_di},  "+DI color", group="{grp}")
n_adx_minus_col= input.color({col_minus_di}, "-DI color", group="{grp}")
n_adx_width    = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
n_adx_style    = input.string("{sty}", "Line style", options=["solid","dashed","dotted"], group="{grp}")
[_adx_plus, _adx_minus, _adx_val] = ta.dmi({win}, {win})
plot(_osc_px(_adx_val,   0.0, 100.0, {slot}), "ADX",   n_adx_col,       n_adx_width)
plot(_osc_px(_adx_plus,  0.0, 100.0, {slot}), "+DI",   n_adx_plus_col,  1)
plot(_osc_px(_adx_minus, 0.0, 100.0, {slot}), "-DI",   n_adx_minus_col, 1)
plot(_osc_px({level}.0,  0.0, 100.0, {slot}), "ADX thr", color.new(color.green, 60), 1, plot.style_linebr)
plot(_osc_px(75.0,       0.0, 100.0, {slot}), "ADX 75",  color.new(color.gray,  60), 1, plot.style_linebr)
"""


def _n_stoch(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized Stochastic block for strategy scripts."""
    win = int(p.get('window',       14))
    sm  = int(p.get('smooth_window', 3))
    style = _style_inputs(p, 'n_stoch', 'color.black', 'Stochastic (combined)')
    return f"""\
// ── Stochastic  (slot {slot}) ─────────────────────────────────────────────────
{style}_stk = ta.stoch(close, high, low, {win})
_std = ta.sma(_stk, {sm})
plot(_osc_px(_stk, 0.0, 100.0, {slot}), "Stoch %K", n_stoch_col, n_stoch_width)
plot(_osc_px(_std, 0.0, 100.0, {slot}), "Stoch %D", color.blue,  1)
plot(_osc_px(80.0, 0.0, 100.0, {slot}), "Stoch 80", color.new(color.red,   60), 1, plot.style_linebr)
plot(_osc_px(20.0, 0.0, 100.0, {slot}), "Stoch 20", color.new(color.green, 60), 1, plot.style_linebr)
"""


def _n_zcr(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized ZCR block for strategy scripts."""
    win = int(p.get('window', 20))
    style = _style_inputs(p, 'n_zcr', 'color.blue', 'Z-Score (combined)')
    return f"""\
// ── Z-Score  (slot {slot}) ────────────────────────────────────────────────────
{style}_zcr_m = ta.sma(close,   {win})
_zcr_s = ta.stdev(close, {win})
_zcr_v = _zcr_s != 0 ? (close - _zcr_m) / _zcr_s : 0.0
plot(_osc_px(_zcr_v, -4.0, 4.0, {slot}), "Z-Score", n_zcr_col, n_zcr_width)
plot(_osc_px( 2.0, -4.0, 4.0, {slot}), "Z +2", color.new(color.gray,  60), 1, plot.style_linebr)
plot(_osc_px( 1.0, -4.0, 4.0, {slot}), "Z +1", color.new(color.red,   60), 1, plot.style_linebr)
plot(_osc_px(-1.0, -4.0, 4.0, {slot}), "Z -1", color.new(color.green, 60), 1, plot.style_linebr)
plot(_osc_px(-2.0, -4.0, 4.0, {slot}), "Z -2", color.new(color.gray,  60), 1, plot.style_linebr)
"""


def _n_ewo(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized EWO block for strategy scripts."""
    style = _style_inputs(p, 'n_ewo', 'color.black', 'EWO (combined)')
    return f"""\
// ── EWO  (slot {slot}) ────────────────────────────────────────────────────────
{style}_ewo_v = ta.sma(close, 5) - ta.sma(close, 21)
_ewo_e = ta.ema(_ewo_v, 9)
_ewo_d = _ewo_e - _ewo_e[1]
_ewo_lo = ta.lowest( math.min(_ewo_d, math.min(_ewo_v, _ewo_e)), 300)
_ewo_hi = ta.highest(math.max(_ewo_d, math.max(_ewo_v, _ewo_e)), 300)
_ewo_z  = plot(_osc_px(0.0,    _ewo_lo, _ewo_hi, {slot}), "EWO zero", color.new(color.gray, 100))
_ewo_db = plot(_osc_px(_ewo_d, _ewo_lo, _ewo_hi, {slot}), "EWO diff",
               _ewo_d >= 0 ? color.new(color.green, 30) : color.new(color.red, 30), 1)
fill(_ewo_z, _ewo_db, _ewo_d >= 0 ? color.new(color.green, 50) : color.new(color.red, 50))
plot(_osc_px(_ewo_v, _ewo_lo, _ewo_hi, {slot}), "EWO",     n_ewo_col,    n_ewo_width)
plot(_osc_px(_ewo_e, _ewo_lo, _ewo_hi, {slot}), "EWO EMA", color.orange, 1)
_ewo_rising  = (_ewo_v - _ewo_v[1]) >  0.01
_ewo_falling = (_ewo_v - _ewo_v[1]) < -0.01
// Signal only on the first bar of each new direction (rising edge of state change)
_ewo_buy  = _ewo_rising  and not _ewo_rising[1]
_ewo_sell = _ewo_falling and not _ewo_falling[1]
plotshape(_ewo_buy  ? _osc_px(_ewo_v, _ewo_lo, _ewo_hi, {slot}) : na,
          "EWO Buy",  shape.triangleup,   location.absolute, color.teal, size=size.small)
plotshape(_ewo_sell ? _osc_px(_ewo_v, _ewo_lo, _ewo_hi, {slot}) : na,
          "EWO Sell", shape.triangledown, location.absolute, color.red,  size=size.small)
"""


def _n_dema(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized DEMA block for strategy scripts."""
    fast  = int(p.get('fast_length', 8))
    slow  = int(p.get('slow_length', 21))
    win   = int(p.get('window',      14))
    cross = bool(p.get('show_cross', True))
    col_slow = _hex_to_pine_color(p.get('color_slow', ''), 'color.green')
    col_fast = _hex_to_pine_color(p.get('color_fast', ''), 'color.red')
    wid = max(1, min(5, int(p.get('line_width', 2))))
    sty = p.get('line_style', 'solid')
    if sty not in ('solid', 'dashed', 'dotted'):
        sty = 'solid'
    grp = "DEMA (combined) -- Style"
    code = f"""\
// ── DEMA  (slot {slot}) ───────────────────────────────────────────────────────
n_dema_slow_col = input.color({col_slow}, "Slow EMA color", group="{grp}")
n_dema_fast_col = input.color({col_fast}, "Fast EMA color", group="{grp}")
n_dema_width    = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
n_dema_style    = input.string("{sty}", "Line style", options=["solid","dashed","dotted"], group="{grp}")
_dema_src  = ta.rsi(close, {win})
_dema_f1   = ta.ema(_dema_src, {fast})
_dema_fast = 2 * _dema_f1 - ta.ema(_dema_f1, {fast})
_dema_s1   = ta.ema(_dema_src, {slow})
_dema_slow = 2 * _dema_s1 - ta.ema(_dema_s1, {slow})
_dema_m1   = ta.ema(_dema_src, {win})
_dema_mid  = 2 * _dema_m1 - ta.ema(_dema_m1, {win})
plot(_osc_px(_dema_slow, 0.0, 100.0, {slot}), "DEMA Slow", n_dema_slow_col, n_dema_width)
plot(_osc_px(_dema_fast, 0.0, 100.0, {slot}), "DEMA Fast", n_dema_fast_col, n_dema_width)
plot(_osc_px(_dema_mid,  0.0, 100.0, {slot}), "DEMA Mid",  color.black,     1)
plot(_osc_px(70.0, 0.0, 100.0, {slot}), "DEMA 70", color.new(color.gray, 60), 1, plot.style_linebr)
plot(_osc_px(50.0, 0.0, 100.0, {slot}), "DEMA 50", color.new(color.gray, 60), 1, plot.style_linebr)
plot(_osc_px(30.0, 0.0, 100.0, {slot}), "DEMA 30", color.new(color.gray, 60), 1, plot.style_linebr)
"""
    if cross:
        code += f"""\
_dema_buy  = ta.crossover(_dema_fast,  _dema_slow)
_dema_sell = ta.crossunder(_dema_fast, _dema_slow)
plotshape(_dema_buy  ? _osc_px(_dema_fast, 0.0, 100.0, {slot}) : na,
          "DEMA Buy",  shape.triangleup,   location.absolute, color.green, size=size.small)
plotshape(_dema_sell ? _osc_px(_dema_fast, 0.0, 100.0, {slot}) : na,
          "DEMA Sell", shape.triangledown, location.absolute, color.red,   size=size.small)
"""
    return code


def _n_vol(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized Volume Delta block for strategy scripts."""
    col_bull = _hex_to_pine_color(p.get('color_bull', ''), 'color.new(color.green, 20)')
    col_bear = _hex_to_pine_color(p.get('color_bear', ''), 'color.new(color.red,   20)')
    wid  = max(1, min(5, int(p.get('line_width', 1))))
    grp  = "Volume (combined) -- Style"
    return f"""\
// ── Volume  (slot {slot}) ─────────────────────────────────────────────────────
n_vol_col_bull = input.color({col_bull}, "Bull color", group="{grp}")
n_vol_col_bear = input.color({col_bear}, "Bear color", group="{grp}")
n_vol_width    = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
_vol_hi  = ta.highest(volume, 300)
_vol_bar = close >= open ? n_vol_col_bull : n_vol_col_bear
_vol_z   = plot(_osc_px(0.0,    0.0, _vol_hi, {slot}), "Vol zero",
                color.new(color.gray, 100))
_vol_b   = plot(_osc_px(volume, 0.0, _vol_hi, {slot}), "Volume", _vol_bar, n_vol_width)
fill(_vol_z, _vol_b, close >= open ? color.new(n_vol_col_bull, 50) : color.new(n_vol_col_bear, 50))
"""


def _n_hor(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized Horcrux block for strategy scripts."""
    bb_len = int(p.get('bb_length', 20))
    lb     = int(p.get('lookback',  50))
    adj    = float(p.get('adjustment', 0.0))
    style  = _style_inputs(p, 'n_hor', 'color.black', 'Horcrux (combined)')
    return f"""\
// ── Horcrux  (slot {slot}) ────────────────────────────────────────────────────
{style}_hor_mid = ta.sma(close, {bb_len})
[_, _hu0, _hl0] = ta.bb(close, {bb_len}, 0.5)
[_, _hu1, _hl1] = ta.bb(close, {bb_len}, 1.0)
[_, _hu2, _hl2] = ta.bb(close, {bb_len}, 1.5)
[_, _hu3, _hl3] = ta.bb(close, {bb_len}, 2.0)
[_, _hu4, _hl4] = ta.bb(close, {bb_len}, 2.5)
[_, _hu5, _hl5] = ta.bb(close, {bb_len}, 3.0)
[_, _hu6, _hl6] = ta.bb(close, {bb_len}, 3.5)
_hor_st = high<_hl6?15:high<_hl5?14:high<_hl4?13:high<_hl3?12:high<_hl2?11:high<_hl1?10:high<_hl0?9:high<_hor_mid?8:high<_hu0?7:high<_hu1?6:high<_hu2?5:high<_hu3?4:high<_hu4?3:high<_hu5?2:high<_hu6?1:0
_hor_sm = ta.highest(_hor_st, {lb})
_hor_si = ta.lowest(_hor_st,  {lb})
_hor_v  = _hor_sm - _hor_st
_hor_t  = (_hor_sm + _hor_si) / 2 * (1 + {adj} / 100)
plot(_osc_px(_hor_v, 0.0, 15.0, {slot}), "Horcrux",
     _hor_v < _hor_t ? color.new(color.orange, 20) : color.new(color.lime, 20), n_hor_width)
plot(_osc_px(_hor_t, 0.0, 15.0, {slot}), "Hor Thr", n_hor_col, n_hor_width)
"""


def _n_relvol(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized Relative Volume block for strategy scripts."""
    length = int(p.get('relvol_length', 21))
    ratio  = float(p.get('relvol_ratio', 1.0))
    mode   = str(p.get('relvol_mode', 'Regular'))
    if mode.lower() == 'cumulative':
        rv_cur  = "ta.cum(volume)"
        rv_past = f"ta.sum(volume[1], {length})"
    else:
        rv_cur  = "volume"
        rv_past = f"ta.sma(volume[1], {length})"
    col_bull = _hex_to_pine_color(p.get('color_bull', ''), 'color.new(color.green, 30)')
    col_bear = _hex_to_pine_color(p.get('color_bear', ''), 'color.new(color.red,   30)')
    wid  = max(1, min(5, int(p.get('line_width', 1))))
    grp  = "RelVol (combined) -- Style"
    return f"""\
// ── Relative Volume  (slot {slot}) ───────────────────────────────────────────
n_rv_col_bull = input.color({col_bull}, "Bull color", group="{grp}")
n_rv_col_bear = input.color({col_bear}, "Bear color", group="{grp}")
n_rv_width    = input.int({wid}, "Width", minval=1, maxval=5, group="{grp}")
_rv_cur  = {rv_cur}
_rv_past = {rv_past}
_rv_rat  = _rv_past != 0 ? _rv_cur / _rv_past : 1.0
_rv_bar  = _rv_rat - {ratio}
_rv_lo   = ta.lowest(_rv_bar,  300)
_rv_hi   = ta.highest(_rv_bar, 300)
_rv_col  = close >= open ? n_rv_col_bull : n_rv_col_bear
_rv_z    = plot(_osc_px(0.0,     _rv_lo, _rv_hi, {slot}), "RV zero", color.new(color.gray, 100))
_rv_b    = plot(_osc_px(_rv_bar, _rv_lo, _rv_hi, {slot}), "RelVol",  _rv_col, n_rv_width)
fill(_rv_z, _rv_b, close >= open ? color.new(n_rv_col_bull, 50) : color.new(n_rv_col_bear, 50))
"""


def _n_cumd(p: dict, slot: int) -> str:
    """Return the Pine Script v5 normalized Cumulative Delta block for strategy scripts."""
    reset_mode = str(p.get('reset_mode', 'monthly')).lower()
    _tf_map    = {'auto': 'D', 'daily': 'D', 'monthly': 'M', 'none': ''}
    pine_tf    = _tf_map.get(reset_mode, 'M')
    if pine_tf:
        accum_line = (
            f'_cumd_new := timeframe.change("{pine_tf}")\n'
            f'_cumd_v   := _cumd_new ? _cumd_d : _cumd_v + _cumd_d'
        )
    else:
        accum_line = '_cumd_v += _cumd_d'
    col_bull = _hex_to_pine_color(p.get('color_bull', ''), 'color.new(color.teal,    20)')
    col_bear = _hex_to_pine_color(p.get('color_bear', ''), 'color.rgb(220, 20, 60, 20)')
    grp  = "CVD (combined) -- Style"
    return f"""\
// ── CumDelta  (slot {slot}) ───────────────────────────────────────────────────
n_cumd_col_bull = input.color({col_bull}, "Bull color", group="{grp}")
n_cumd_col_bear = input.color({col_bear}, "Bear color", group="{grp}")
_cumd_hl  = high == low ? 1e-10 : high - low
_cumd_d   = volume * (close - open) / _cumd_hl
var float _cumd_v   = 0.0
var bool  _cumd_new = false
{accum_line}
_cumd_lo = ta.lowest(_cumd_v,  300)
_cumd_hi = ta.highest(_cumd_v, 300)
// CVD as open/close pairs (simplified: prior bar = open, current = close)
_cumd_o  = _cumd_v - _cumd_d
_cumd_px_o = _osc_px(_cumd_o, _cumd_lo, _cumd_hi, {slot})
_cumd_px_c = _osc_px(_cumd_v, _cumd_lo, _cumd_hi, {slot})
_cumd_bul  = _cumd_v >= _cumd_o
plotcandle(_cumd_px_o, math.max(_cumd_px_o, _cumd_px_c),
           math.min(_cumd_px_o, _cumd_px_c), _cumd_px_c, "CVD",
           _cumd_bul ? n_cumd_col_bull : n_cumd_col_bear,
           _cumd_bul ? n_cumd_col_bull : n_cumd_col_bear,
           bordercolor = color.new(color.gray, 60))
plot(_osc_px(0.0, _cumd_lo, _cumd_hi, {slot}), "CVD zero",
     color.new(color.black, 0), 1, plot.style_linebr)
"""


def _t_wml(p: dict) -> str:
    """Return the Pine Script v5 Weighted Momentum Levels overlay template with configurable params."""
    show_week  = bool(p.get('show_week',  True))
    show_month = bool(p.get('show_month', True))
    col_week  = _hex_to_pine_color(p.get('color_week',  ''), 'color.orange')
    col_month = _hex_to_pine_color(p.get('color_month', ''), 'color.red')
    grp = "Week/Month Levels -- Style"

    # Style inputs are emitted once at the top (outside barstate.islast block)
    input_lines = [
        '// ── Week/Month Levels ────────────────────────────────────────────────────────',
        f'wml_wk_col = input.color({col_week},  "Week levels color",  group="{grp}")',
        f'wml_mo_col = input.color({col_month}, "Month levels color", group="{grp}")',
    ]

    sec_lines, var_lines, del_lines, draw_lines = [], [], [], []

    if show_week:
        sec_lines += [
            'wml_wk_hi = request.security(syminfo.tickerid, "W", high[1])',
            'wml_wk_lo = request.security(syminfo.tickerid, "W", low[1])',
        ]
        var_lines += [
            'var line _wml_wk_hi_line = na',
            'var line _wml_wk_lo_line = na',
        ]
        del_lines += [
            '    line.delete(_wml_wk_hi_line)',
            '    line.delete(_wml_wk_lo_line)',
        ]
        draw_lines += [
            '    _wml_wk_hi_line := line.new(bar_index, wml_wk_hi, bar_index + 1, wml_wk_hi, '
            'color=wml_wk_col, width=1, style=line.style_dashed, extend=extend.both)',
            '    label.new(bar_index, wml_wk_hi, "Week High: " + str.tostring(math.round(wml_wk_hi, 2)), '
            'color=color.new(wml_wk_col, 70), textcolor=wml_wk_col, '
            'style=label.style_label_left, size=size.small)',
            '    _wml_wk_lo_line := line.new(bar_index, wml_wk_lo, bar_index + 1, wml_wk_lo, '
            'color=wml_wk_col, width=1, style=line.style_dashed, extend=extend.both)',
            '    label.new(bar_index, wml_wk_lo, "Week Low: " + str.tostring(math.round(wml_wk_lo, 2)), '
            'color=color.new(wml_wk_col, 70), textcolor=wml_wk_col, '
            'style=label.style_label_left, size=size.small)',
        ]

    if show_month:
        sec_lines += [
            'wml_mo_hi = request.security(syminfo.tickerid, "M", high[1])',
            'wml_mo_lo = request.security(syminfo.tickerid, "M", low[1])',
        ]
        var_lines += [
            'var line _wml_mo_hi_line = na',
            'var line _wml_mo_lo_line = na',
        ]
        del_lines += [
            '    line.delete(_wml_mo_hi_line)',
            '    line.delete(_wml_mo_lo_line)',
        ]
        draw_lines += [
            '    _wml_mo_hi_line := line.new(bar_index, wml_mo_hi, bar_index + 1, wml_mo_hi, '
            'color=wml_mo_col, width=1, style=line.style_dashed, extend=extend.both)',
            '    label.new(bar_index, wml_mo_hi, "Month High: " + str.tostring(math.round(wml_mo_hi, 2)), '
            'color=color.new(wml_mo_col, 70), textcolor=wml_mo_col, '
            'style=label.style_label_left, size=size.small)',
            '    _wml_mo_lo_line := line.new(bar_index, wml_mo_lo, bar_index + 1, wml_mo_lo, '
            'color=wml_mo_col, width=1, style=line.style_dashed, extend=extend.both)',
            '    label.new(bar_index, wml_mo_lo, "Month Low: " + str.tostring(math.round(wml_mo_lo, 2)), '
            'color=color.new(wml_mo_col, 70), textcolor=wml_mo_col, '
            'style=label.style_label_left, size=size.small)',
        ]

    body = '\n'.join(input_lines + sec_lines + var_lines)
    if del_lines or draw_lines:
        body += '\nif barstate.islast\n'
        body += '\n'.join(del_lines + draw_lines) + '\n'

    return body


def _t_renko(p: dict) -> str:
    """Return the Pine Script v6 Renko Candles overlay template with configurable params."""
    mode      = str(p.get('mode', 'ATR/2'))
    atr_per   = int(p.get('atr_period', 14))
    brick_fix = float(p.get('brick_size', 10.0))
    pct       = float(p.get('percentage', 0.1))

    col_up = _hex_to_pine_color(p.get('color_up',   ''), 'color.rgb(91, 156, 246)')
    col_dn = _hex_to_pine_color(p.get('color_down', ''), 'color.rgb(12, 50, 153)')
    grp  = "Renko"
    grps = "Renko -- Style"

    mode_lit = mode if mode in ('ATR/2', 'ATR', 'percentage', 'fixed') else 'ATR/2'

    return f"""\
// ── Renko Candles ─────────────────────────────────────────────────────────────
rk_mode      = input.string("{mode_lit}", "Brick size mode", options=["ATR/2", "ATR", "percentage", "fixed"], group="{grp}")
rk_atr_per   = input.int({atr_per}, "ATR Period", minval=2, maxval=100, group="{grp}")
rk_brick_fix = input.float({brick_fix:.4f}, "Fixed brick size", minval=0.0001, group="{grp}")
rk_pct       = input.float({pct:.4f}, "Percentage brick size (%)", minval=0.001, step=0.01, group="{grp}")
rk_col_up    = input.color({col_up}, "Bull brick color", group="{grps}")
rk_col_dn    = input.color({col_dn}, "Bear brick color", group="{grps}")
float rk_atr = ta.atr(rk_atr_per)
float rk_box = na(rk_atr) ? rk_brick_fix : rk_mode == "ATR" ? rk_atr : rk_mode == "ATR/2" ? rk_atr / 2 : rk_mode == "percentage" ? close * rk_pct / 100 : rk_brick_fix
rk_box := math.max(rk_box, 0.0001)
var float rk_o   = na
var float rk_c   = na
var int   rk_dir = 0
if na(rk_o)
    rk_o := math.floor(close / rk_box) * rk_box
    rk_c := rk_o
if not na(rk_c)
    float rk_diff = close - rk_c
    int   rk_n    = int(math.abs(rk_diff) / rk_box)
    if rk_n > 0
        int rk_d = rk_diff > 0 ? 1 : -1
        if rk_dir != 0 and rk_d != rk_dir and rk_n >= 2
            rk_d := -rk_dir
            rk_n := rk_n - 1
        rk_o   := rk_c
        rk_c   := rk_c + float(rk_d * rk_n) * rk_box
        rk_dir := rk_d
rk_col = rk_dir >= 0 ? rk_col_up : rk_col_dn
rk_h   = math.max(rk_o, rk_c)
rk_l   = math.min(rk_o, rk_c)
plotcandle(rk_o, rk_h, rk_l, rk_c, "Renko",
           rk_col, rk_col, bordercolor = color.new(color.gray, 70))
"""


def _t_gframa(p: dict) -> str:
    """Return the Pine Script v6 G-FRAMA overlay template with configurable params."""
    gauss_len  = int(p.get('gauss_length', 4))
    sigma      = float(p.get('gauss_sigma', 2.0))
    frama_len  = int(p.get('frama_length', 20))
    fast       = int(p.get('frama_upper', 8))
    slow       = int(p.get('frama_lower', 40))
    atr_len    = int(p.get('atr_length', 14))
    atr_mult   = float(p.get('atr_mult', 1.9))
    show_bands = 'true' if bool(p.get('show_bands', True)) else 'false'
    fill_bands = 'true' if bool(p.get('fill_bands', True)) else 'false'
    col_long   = _hex_to_pine_color(p.get('color_long',    ''), 'color.rgb(23, 191, 238)')
    col_short  = _hex_to_pine_color(p.get('color_short',   ''), 'color.rgb(180, 30, 30)')
    col_neu    = _hex_to_pine_color(p.get('color_neutral', ''), 'color.rgb(128, 128, 128)')
    grp  = "G-FRAMA"
    grps = "G-FRAMA -- Style"
    return f"""\
// ── G-FRAMA ───────────────────────────────────────────────────────────────────
gf_gauss_len  = input.int({gauss_len},   "Gaussian length",       minval=1,   maxval=50,   group="{grp}")
gf_sigma      = input.float({sigma:.1f}, "Gaussian sigma",        minval=0.1, maxval=10.0, step=0.1, group="{grp}")
gf_frama_len  = input.int({frama_len},   "FRAMA length",          minval=4,   maxval=200,  group="{grp}")
gf_fast       = input.int({fast},        "FRAMA fast limit",      minval=1,   maxval=50,   group="{grp}")
gf_slow       = input.int({slow},        "FRAMA slow limit",      minval=2,   maxval=200,  group="{grp}")
gf_atr_len    = input.int({atr_len},     "ATR length",            minval=1,   maxval=100,  group="{grp}")
gf_atr_mult   = input.float({atr_mult:.1f}, "ATR mult (upper band)", minval=0.1, maxval=10.0, step=0.1, group="{grp}")
gf_show_bands = input.bool({show_bands}, "Show ATR bands",        group="{grp}")
gf_fill_bands = input.bool({fill_bands}, "Fill ATR band",         group="{grp}")
gf_col_long   = input.color({col_long},  "Long color",    group="{grps}")
gf_col_short  = input.color({col_short}, "Short color",   group="{grps}")
gf_col_neu    = input.color({col_neu},   "Neutral color", group="{grps}")
gf_gauss(src, length, sigma) =>
    sum_n = 0.0
    sum_d = 0.0
    for i = 0 to length - 1
        w = math.exp(-0.5 * math.pow((i - (length - 1) / 2.0) / sigma, 2.0))
        sum_n += w * src[i]
        sum_d += w
    sum_n / sum_d
gf_frama(src, length, fast, slow) =>
    half    = length / 2
    hl_full = (ta.highest(high, length)        - ta.lowest(low, length))        / length
    hl1     = (ta.highest(high, half)           - ta.lowest(low, half))           / half
    hl_old  = (ta.highest(high[half], half)     - ta.lowest(low[half], half))     / half
    var float _d = 1.5
    if hl1 > 0 and hl_old > 0 and hl_full > 0
        _d := (math.log(hl1 + hl_old) - math.log(hl_full)) / math.log(2.0)
    _w     = math.log(2.0 / (slow + 1))
    _alpha = math.max(0.01, math.min(1.0, math.exp(_w * (_d - 1.0))))
    _oldN  = (2.0 - _alpha) / _alpha
    _newN  = float(slow - fast) * (_oldN - 1.0) / float(slow - 1) + float(fast)
    _na    = math.max(2.0 / (slow + 1), math.min(1.0, 2.0 / (_newN + 1.0)))
    var float _f = na
    _f := na(_f[1]) ? src : (1.0 - _na) * _f[1] + _na * src
    _f
gf_src   = gf_gauss(close, gf_gauss_len, gf_sigma)
gf_line  = gf_frama(gf_src, gf_frama_len, gf_fast, gf_slow)
gf_atr   = ta.atr(gf_atr_len)
gf_upper = gf_line + gf_atr * gf_atr_mult
gf_lower = gf_line - gf_atr
var int gf_qb = 0
gf_qb := close > gf_upper ? 1 : close < gf_lower ? -1 : gf_qb
gf_col  = gf_qb == 1 ? gf_col_long : gf_qb == -1 ? gf_col_short : gf_col_neu
gf_band_col = gf_show_bands ? color.new(color.gray, 70) : color.new(color.gray, 100)
gf_fill_col = gf_show_bands and gf_fill_bands ? color.new(gf_col, 88) : color.new(color.gray, 100)
gf_p_up = plot(gf_upper, "G-FRAMA Upper", gf_band_col, 1)
gf_p_dn = plot(gf_lower, "G-FRAMA Lower", gf_band_col, 1)
fill(gf_p_up, gf_p_dn, color = gf_fill_col)
plot(gf_line, "G-FRAMA", gf_col, 2)
"""


def _n_hbo(p: dict, slot: int) -> str:
    """Return the Pine Script v6 normalized Hull Butterfly Oscillator block for strategy scripts."""
    length = int(p.get('length', 14))
    mult   = float(p.get('mult', 2.0))
    style  = _style_inputs(p, 'n_hbo', 'color.rgb(12, 181, 26)', 'HBO (combined)')
    return f"""\
// ── Hull Butterfly Oscillator  (slot {slot}) ──────────────────────────────────
{style}f_wma_inv_hbo(src, length) =>
    den = float(length) * (length + 1) / 2.0
    s   = 0.0
    for i = 0 to length - 1
        s += src[i] * float(i + 1)
    s / den
_hbo_lc  = 2.0 * f_wma_inv_hbo(close, {int(length // 2)}) - f_wma_inv_hbo(close, {length})
max_bars_back(_hbo_lc, 25)
_hbo_hso = f_wma_inv_hbo(_hbo_lc, {int(round(length ** 0.5))}) - ta.hma(close, {length})
_hbo_lo  = ta.lowest(_hbo_hso,  300)
_hbo_hi  = ta.highest(_hbo_hso, 300)
plot(_osc_px(_hbo_hso, _hbo_lo, _hbo_hi, {slot}), "HBO", n_hbo_col, n_hbo_width)
"""


def _n_scr(p: dict, slot: int) -> str:
    """Return the Pine Script v6 normalized Seasonal Score block for strategy scripts."""
    scale     = float(p.get('scale', 10.0))
    min_years = int(p.get('min_years', 3))
    style     = _style_inputs(p, 'n_scr', 'color.black', 'Seasonal Score (combined)')
    return f"""\
// ── Seasonal Score  (slot {slot}) ─────────────────────────────────────────────
max_bars_back(close, 2000)
{style}f_scr_score_n(horizon_bars) =>
    s = 0.0
    n = 0
    for k = 1 to 7
        past = close[252 * k]
        fwd  = close[252 * k - horizon_bars]
        if not na(past) and not na(fwd) and past > 0
            s += (fwd / past - 1.0) * 100.0
            n += 1
    n >= {min_years} ? math.max(-100.0, math.min(100.0, s / n / {scale:.1f} * 100.0)) : na
_scr_1mo_n = f_scr_score_n(21)
plot(_osc_px(_scr_1mo_n, -100.0, 100.0, {slot}), "SCR 1mo", n_scr_col, n_scr_width)
"""


_OSC_NORM_TEMPLATES: dict[str, Callable] = {
    'adx':    _n_adx,
    'cci':    _n_cci,
    'cumd':   _n_cumd,
    'dema':   _n_dema,
    'ewo':    _n_ewo,
    'hbo':    _n_hbo,
    'hor':    _n_hor,
    'macd':   _n_macd,
    'relvol': _n_relvol,
    'rsi':    _n_rsi,
    'scr':    _n_scr,
    'stoch':  _n_stoch,
    'vol':    _n_vol,
    'zcr':    _n_zcr,
}


# ── Template registries ────────────────────────────────────────────────────────

_OSC_TEMPLATES: dict[str, Callable[[dict], str]] = {
    'adx':    _t_adx,
    'cci':    _t_cci,
    'cumd':   _t_cumd,
    'dema':   _t_dema,
    'ewo':    _t_ewo,
    'hbo':    _t_hbo,
    'hor':    _t_hor,
    'macd':   _t_macd,
    'relvol': _t_relvol,
    'rsi':    _t_rsi,
    'scr':    _t_scr,
    'stoch':  _t_stoch,
    'vol':    _t_vol,
    'zcr':    _t_zcr,
}

_OVL_TEMPLATES: dict[str, Callable[[dict], str]] = {
    'atc':    _t_atc,
    'atl':    _t_atl,
    'bol':    _t_bol,
    'bos':    _t_bos,
    'bsz':    _t_bsz,
    'don':    _t_don,
    'fib':    _t_fib,
    'fvg':    _t_fvg,
    'gan':    _t_gan,
    'gframa': _t_gframa,
    'heikin': _t_heikin,
    'ici':    _t_ici,
    'lqz':    _t_lqz,
    'mam':    _t_mam,
    'markov': _t_markov,
    'mmm':    _t_mmm,
    'nsdt':   _t_nsdt,
    'oft':    _t_oft,
    'pvt':    _t_pvt,
    'qtrend': _t_qtrend,
    'renko':  _t_renko,
    'sup':    _t_sup,
    'vwap':   _t_vwap,
    'wml':    _t_wml,
}


# ── Strategy export helpers ────────────────────────────────────────────────────
#
# Maps Python DataFrame column names (as used in buy_query / sell_query)
# to the Pine Script variable names produced by the computation blocks below.
_STRAT_COL_MAP: dict[str, str] = {
    # Raw OHLCV
    'Close':          'close',
    'Open':           'open',
    'High':           'high',
    'Low':            'low',
    'Volume':         'volume',
    # Heikin Ashi (heikin indicator)
    'ha_close':       'ha_close',
    'ha_open':        'ha_open',
    'ha_high':        'ha_high',
    'ha_low':         'ha_low',
    'ha_ema_high':    'ha_ema_hi',
    'ha_ema_low':     'ha_ema_lo',
    # MACD (macd indicator)
    'macd':           'str_macd',
    'macd_signal':    'str_macd_sig',
    'macd_diff':      'str_macd_hist',
    # RSI (rsi indicator)
    'rsi':            'str_rsi',
    'rsi_ema':        'str_rsi_ema',
    # Markov (markov indicator)  — 1=Bull 2=Bear 0=Sideways
    'markov_regime':  'str_regime',
    # EWO (ewo indicator)
    'ewo':            'str_ewo',
    'ewo_ema':        'str_ewo_ema',
    'ewo_angle':      'str_ewo_ang',
    # Stochastic (stoch indicator)
    'stoch':          'str_stoch',
    'stoch_signal':   'str_stoch_d',
    # Z-Score (zcr indicator)
    'Z':              'str_zcr',
    'zcr':            'str_zcr',
}

# Which indicator computation block is required for each column name.
_STRAT_COL_INDICATOR: dict[str, str] = {
    'ha_close':       'heikin',
    'ha_open':        'heikin',
    'ha_high':        'heikin',
    'ha_low':         'heikin',
    'ha_ema_high':    'heikin',
    'ha_ema_low':     'heikin',
    'macd':           'macd',
    'macd_signal':    'macd',
    'macd_diff':      'macd',
    'rsi':            'rsi',
    'rsi_ema':        'rsi',
    'markov_regime':  'markov',
    'ewo':            'ewo',
    'ewo_ema':        'ewo',
    'ewo_angle':      'ewo',
    'stoch':          'stoch',
    'stoch_signal':   'stoch',
    'Z':              'zcr',
    'zcr':            'zcr',
}

# ── Per-indicator computation snippets (no plot / plotshape calls) ─────────────

def _strat_heikin(p: dict) -> str:
    """Return the Pine Script v5 Heikin-Ashi sell-condition expression."""
    _pine_ma = {'SMA': 'ta.sma', 'EMA': 'ta.ema', 'WMA': 'ta.wma',
                'RMA': 'ta.rma', 'HMA': 'ta.hma'}
    smooth   = bool(p.get('smooth', False))
    slb      = int(p.get('smooth_length_before', 10))
    sla      = int(p.get('smooth_length_after',  10))
    mat_b    = _pine_ma.get(str(p.get('smooth_ma_type_before', 'EMA')), 'ta.ema')
    mat_a    = _pine_ma.get(str(p.get('smooth_ma_type_after',  'EMA')), 'ta.ema')
    ehl      = int(p.get('ema_high_length', 21))
    ell      = int(p.get('ema_low_length',  21))
    ma_fn    = _pine_ma.get(str(p.get('ema_type', 'EMA')), 'ta.ema')
    if smooth:
        src_o = f"{mat_b}(open,  {slb})"
        src_h = f"{mat_b}(high,  {slb})"
        src_l = f"{mat_b}(low,   {slb})"
        src_c = f"{mat_b}(close, {slb})"
    else:
        src_o, src_h, src_l, src_c = 'open', 'high', 'low', 'close'
    code = f"""\
// ── Heikin Ashi ───────────────────────────────────────────────────────────────
ha_close  = ({src_o} + {src_h} + {src_l} + {src_c}) / 4
var float ha_open = na
ha_open  := na(ha_open[1]) ? ({src_o} + {src_c}) / 2 : (ha_open[1] + ha_close[1]) / 2
ha_high   = math.max({src_h}, math.max(ha_open, ha_close))
ha_low    = math.min({src_l}, math.min(ha_open, ha_close))
"""
    if smooth:
        code += f"""\
ha_close := {mat_a}(ha_close, {sla})
ha_open  := {mat_a}(ha_open,  {sla})
ha_high  := {mat_a}(ha_high,  {sla})
ha_low   := {mat_a}(ha_low,   {sla})
"""
    code += f"""\
ha_ema_hi = {ma_fn}(ha_high, {ehl})
ha_ema_lo = {ma_fn}(ha_low,  {ell})
"""
    return code


def _strat_macd(p: dict) -> str:
    """Return the Pine Script v5 MACD sell-condition expression."""
    fast = int(p.get('window_fast', 12))
    slow = int(p.get('window_slow', 26))
    sign = int(p.get('window_sign', 9))
    return f"""\
// ── MACD ──────────────────────────────────────────────────────────────────────
[str_macd, str_macd_sig, str_macd_hist] = ta.macd(close, {fast}, {slow}, {sign})
"""


def _strat_rsi(p: dict) -> str:
    """Return the Pine Script v5 RSI sell-condition expression."""
    lb  = int(p.get('lookback', 8))
    win = int(p.get('window',  14))
    return f"""\
// ── RSI ───────────────────────────────────────────────────────────────────────
str_rsi     = ta.rsi(close, {lb})
str_rsi_ema = ta.sma(str_rsi, {win})
"""


def _strat_markov(p: dict) -> str:
    """Return the Pine Script v5 Markov regime sell-condition expression."""
    lb       = int(p.get('lookback',  20))
    bull_pct = float(p.get('bull_pct', 5.0))
    bear_pct = float(p.get('bear_pct', 5.0))
    return f"""\
// ── Markov Regime  (1=Bull  2=Bear  0=Sideways) ───────────────────────────────
str_log_ret = math.log(close / close[{lb}])
str_regime  = na(str_log_ret) ? int(na) :
              str_log_ret >  {bull_pct} / 100.0 ? 1 :
              str_log_ret < -{bear_pct} / 100.0 ? 2 : 0
"""


def _strat_ewo(p: dict) -> str:
    """Return the Pine Script v5 EWO sell-condition expression."""
    return """\
// ── EWO ───────────────────────────────────────────────────────────────────────
str_ewo     = ta.sma(close, 5) - ta.sma(close, 21)
str_ewo_ema = ta.ema(str_ewo, 9)
str_ewo_ang = str_ewo - str_ewo[1]
"""


def _strat_stoch(p: dict) -> str:
    """Return the Pine Script v5 Stochastic sell-condition expression."""
    win = int(p.get('window',        14))
    sm  = int(p.get('smooth_window',  3))
    return f"""\
// ── Stochastic ────────────────────────────────────────────────────────────────
str_stoch   = ta.stoch(close, high, low, {win})
str_stoch_d = ta.sma(str_stoch, {sm})
"""


def _strat_zcr(p: dict) -> str:
    """Return the Pine Script v5 ZCR sell-condition expression."""
    win = int(p.get('window', 20))
    return f"""\
// ── Z-Score ───────────────────────────────────────────────────────────────────
_zcr_mean = ta.sma(close,   {win})
_zcr_std  = ta.stdev(close, {win})
str_zcr   = _zcr_std != 0 ? (close - _zcr_mean) / _zcr_std : 0.0
"""


_STRAT_CALCS: dict[str, Callable[[dict], str]] = {
    'heikin': _strat_heikin,
    'macd':   _strat_macd,
    'rsi':    _strat_rsi,
    'markov': _strat_markov,
    'ewo':    _strat_ewo,
    'stoch':  _strat_stoch,
    'zcr':    _strat_zcr,
}

# Fixed emit order so dependent variables are always declared before use.
_STRAT_ORDER = ['heikin', 'ewo', 'macd', 'rsi', 'stoch', 'zcr', 'markov']


def _translate_query(query: str) -> str:
    """Convert a Python/pandas buy-sell expression to Pine Script syntax.

    Steps:
      1. Replace known DataFrame column names with Pine variable names
         (longest name first to prevent partial matches, e.g. 'macd_signal'
         before 'macd').
      2. Replace Python logical operators: & → and, | → or, ~ → not
    """
    result = query.strip()
    # Column name substitution (longest match first)
    for col, pine_var in sorted(_STRAT_COL_MAP.items(), key=lambda x: -len(x[0])):
        result = re.sub(r'\b' + re.escape(col) + r'\b', pine_var, result)
    # Logical operator conversion
    result = re.sub(r'\s*&\s*',  ' and ', result)
    result = re.sub(r'\s*\|\s*', ' or ',  result)
    result = re.sub(r'~\s*',     'not ',  result)
    # Tidy up any double spaces introduced by replacements
    result = re.sub(r'  +', ' ', result).strip()
    return result


# ── Main class ─────────────────────────────────────────────────────────────────

class PineExporter:
    """Generates Pine Script v5 source from a set of selected indicators."""

    def __init__(
        self,
        overlays:    list[str],
        oscillators: list[str],
        sys_conf=None,
    ):
        """Initialize the exporter with indicator selections and optional sys_conf parameter overrides."""
        self.overlays    = overlays
        self.oscillators = oscillators
        self.sys_conf    = sys_conf

    # ── helpers ────────────────────────────────────────────────────────────────

    def _params(self, name: str) -> dict:
        """Return stored parameter overrides for indicator_name from sys_conf."""
        if self.sys_conf is None:
            return {}
        try:
            return self.sys_conf.get_plugin_params(name) or {}
        except Exception:
            return {}

    def _render_block(self, name: str, templates: dict, with_toggle: bool = False) -> str:
        """Emit one indicator Pine Script block into the appropriate overlay or oscillator buffer.

        When *with_toggle* is True an ``input.bool`` visibility toggle is prepended
        and all drawing calls are gated by it so the user can show/hide the indicator
        independently in TradingView's Settings panel.
        """
        if name in _UNSUPPORTED:
            return f"// ── {name.upper()} — not translatable to Pine Script ──────────────────\n\n"
        fn = templates.get(name)
        if fn is None:
            return f"// ── {name.upper()} — Pine template not yet implemented ─────────────────\n\n"
        try:
            body = fn(self._params(name))
            # Inject attribution as the second line of the block, unless the
            # template already carries its own authorship comment.
            attr = _INDICATOR_ATTRIBUTION.get(name)
            if attr and "// Author" not in body and "// Attribution" not in body:
                first_newline = body.find("\n")
                if first_newline != -1:
                    body = (
                        body[:first_newline + 1]
                        + f"// Attribution: {attr}\n"
                        + body[first_newline + 1:]
                    )
            body = body + "\n"
            if with_toggle:
                body = _add_visibility_toggle(name, body)
            return body
        except Exception as exc:
            return f"// ── {name.upper()} — render error: {exc} ────────────────────────────────\n\n"

    @staticmethod
    def _header(title: str, overlay: bool) -> str:
        """Return the Pine Script v5 script header with license, version, and title."""
        ol    = "true" if overlay else "false"
        extra = ", max_lines_count=500, max_labels_count=500" if overlay else ""
        ts    = datetime.now().strftime("%Y-%m-%d %H:%M")
        return (
            f"//@version=6\n"
            f"{_PINE_LICENSE}"
            f"// Generated by Trading App — {ts}\n"
            f'indicator("{title}", overlay={ol}, max_bars_back=500{extra})\n\n'
        )

    # ── public API ─────────────────────────────────────────────────────────────

    def generate_overlay(self) -> str:
        """Generate a self-contained Pine Script v5 overlay file for the selected overlays.

        Each overlay gets an individual ``input.bool`` toggle in TradingView so users
        can show or hide it without removing the whole indicator.
        """
        if not self.overlays:
            return self._header("Overlays", True) + "// No overlays selected.\n"
        body = "".join(
            self._render_block(n, _OVL_TEMPLATES, with_toggle=True)
            for n in self.overlays
        )
        return self._header("Overlays", True) + body

    def generate_oscillator_single(self, name: str) -> str:
        """Generate a standalone Pine Script for ONE oscillator (its own pane).

        A ``input.bool`` visibility toggle is included so the user can hide the
        indicator without removing the script from the chart.
        """
        body = self._render_block(name, _OSC_TEMPLATES, with_toggle=True)
        return self._header(name.upper(), False) + body

    def generate_oscillator_zip(self, names: list[str]) -> bytes:
        """Generate a ZIP archive with one .pine file per oscillator."""
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in names:
                zf.writestr(f"{name}_oscillator.pine",
                            self.generate_oscillator_single(name).encode("utf-8"))
        return buf.getvalue()

    def generate_oscillator(self) -> str:
        """All oscillators combined in one file, each with its own visibility toggle."""
        if not self.oscillators:
            return self._header("Oscillators", False) + "// No oscillators selected.\n"
        body = "".join(
            self._render_block(n, _OSC_TEMPLATES, with_toggle=True)
            for n in self.oscillators
        )
        return self._header("Oscillators", False) + body

    def generate_combined(
        self,
        overlay_pct: float = 65.0,
        layout_lookback: int = 300,
    ) -> str:
        """Generate a single overlay=true Pine Script that renders:

        • Overlays normally on the price chart (top ``overlay_pct``% of chart height)
        • Oscillators normalized into the bottom ``(100 - overlay_pct)``% of chart height
          — each oscillator receives an equal share of that section

        The chart layout is dynamic: it re-calculates the visible y-range every bar
        using ta.highest/ta.lowest over ``layout_lookback`` bars, so zooming in/out
        adjusts the oscillator bands automatically.
        """
        ts        = datetime.now().strftime("%Y-%m-%d %H:%M")
        n_osc     = max(1, len(self.oscillators))
        osc_pct   = 100.0 - overlay_pct
        ovl_names = ", ".join(self.overlays)    or "–"
        osc_names = ", ".join(self.oscillators) or "–"

        header = (
            f"//@version=6\n"
            f"{_PINE_LICENSE}"
            f"// Generated by Trading App — {ts}\n"
            f"// Combined chart -- overlays on price + oscillators in bottom "
            f"{osc_pct:.0f}% of chart\n"
            f"// Overlays  ({overlay_pct:.0f}%): {ovl_names}\n"
            f"// Oscillators ({osc_pct:.0f}%, {n_osc} equal slots): {osc_names}\n"
            f'indicator("Combined", overlay=true, '
            f"max_bars_back=500, max_lines_count=500, max_labels_count=500)\n\n"
        )

        layout = (
            f"// ── Layout ───────────────────────────────────────────────────────────────────\n"
            f"_layout_lb = input.int({layout_lookback}, \"Range lookback (bars)\","
            f" minval=50, maxval=2000, group=\"Layout\")\n"
            f"_ovl_pct   = input.float({overlay_pct:.0f}, \"Overlay area (%)\","
            f" minval=20, maxval=90, step=5, group=\"Layout\") / 100.0\n"
            f"\n"
            f"// Visible price range estimate (based on H/L over the lookback window)\n"
            f"_ph    = ta.highest(high, _layout_lb)\n"
            f"_pl    = ta.lowest(low,   _layout_lb)\n"
            f"_pr    = _ph - _pl\n"
            f"\n"
            f"// Total oscillator section height expressed in price units\n"
            f"// Derivation: price_range = overlay_pct * total_height\n"
            f"//             osc_total   = total_height - price_range\n"
            f"//                        = price_range * (1 - ovl_pct) / ovl_pct\n"
            f"_osc_total = _pr * (1.0 - _ovl_pct) / _ovl_pct\n"
            f"_osc_slot  = _osc_total / {n_osc}.0   // height of one oscillator slot\n"
            f"_floor     = _pl - _osc_total          // lowest y-coordinate on chart\n"
            f"\n"
            f"// Separator line between price area and oscillator area\n"
            f"plot(_pl, \"──── Oscillators ────\", color.new(color.gray, 20), 1, plot.style_linebr)\n"
        )

        # Inter-slot separator lines (between oscillator bands, not at the very bottom)
        for i in range(1, n_osc):
            layout += (
                f"plot(_floor + {i}.0 * _osc_slot, "
                f"\"slot sep {i}\", color.new(color.gray, 70), 1, plot.style_linebr)\n"
            )

        # Slot labels: rendered once on the last bar at the mid-point of each slot
        slot_labels = "\n// Slot labels (indicator name at the vertical centre of each slot)\n"
        slot_labels += "if barstate.islast\n"
        for i, name in enumerate(self.oscillators):
            slot_labels += (
                f"    label.new(bar_index, _floor + ({i}.0 + 0.5) * _osc_slot,\n"
                f"              \"{name.upper()}\","
                f" color=color.new(color.gray, 60), textcolor=color.white,\n"
                f"              style=label.style_label_left, size=size.small)\n"
            )

        # _osc_px helper: normalize a value into a specific slot's y-range
        helper = (
            f"\n"
            f"// _osc_px: maps val in [v_lo, v_hi] to the y-coordinate of slot i\n"
            f"//   slot 0 = bottom, slot {n_osc - 1} = top oscillator slot\n"
            f"_osc_px(val, v_lo, v_hi, slot) =>\n"
            f"    _t = v_hi != v_lo "
            f"? math.max(0.0, math.min(1.0, (val - v_lo) / (v_hi - v_lo))) : 0.5\n"
            f"    _floor + float(slot) * _osc_slot + _t * _osc_slot\n\n"
        )

        # Overlay block (same Pine code as standalone overlay, unchanged)
        ovl_body = (
            "\n// ── Overlays "
            "─────────────────────────────────────────────────────────────────\n"
        )
        for name in self.overlays:
            ovl_body += self._render_block(name, _OVL_TEMPLATES)

        # Oscillator block (normalized into slots)
        osc_body = (
            f"\n// ── Oscillators "
            f"(normalized into bottom {osc_pct:.0f}%, {n_osc} equal slots)"
            f" ───────────────\n"
        )
        for slot, name in enumerate(self.oscillators):
            if name in _UNSUPPORTED:
                osc_body += (
                    f"// ── {name.upper()} — not translatable to Pine Script "
                    f"──────────────────\n\n"
                )
                continue
            fn = _OSC_NORM_TEMPLATES.get(name)
            if fn is None:
                osc_body += (
                    f"// ── {name.upper()} — normalized template not yet "
                    f"implemented ─────────────────\n\n"
                )
                continue
            try:
                osc_body += fn(self._params(name), slot) + "\n"
            except Exception as exc:
                osc_body += (
                    f"// ── {name.upper()} — render error: {exc} "
                    f"────────────────────────────────\n\n"
                )

        return header + layout + slot_labels + helper + ovl_body + osc_body

    def generate_strategy(self, buy_query: str, sell_query: str) -> str:
        """Generate a standalone Pine Script v5 *strategy* for the configured
        buy/sell rules.

        The method auto-detects which indicator variables are referenced in the
        query expressions, emits minimal computation code for those indicators
        (no plot calls — strategy scripts should be clean), translates the
        Python-style expressions to Pine-compatible boolean syntax, and wraps
        everything in a ``strategy()`` script with entry / close calls and
        visual signal markers.

        Parameters
        ----------
        buy_query:
            Python/pandas expression string (e.g.
            ``"(ha_close > ha_open) & (Close > ha_ema_high) & (rsi > 50)"``).
        sell_query:
            Python/pandas expression string (e.g.
            ``"(ha_close < ha_open) & (Close < ha_ema_low)"``).
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")

        # ── 1. Detect required indicator computation blocks ────────────────────
        combined = f"{buy_query} {sell_query}"
        needed: set[str] = set()
        for col, ind in _STRAT_COL_INDICATOR.items():
            if re.search(r'\b' + re.escape(col) + r'\b', combined):
                needed.add(ind)

        # ── 2. Emit computation blocks in dependency order ─────────────────────
        calc_lines: list[str] = []
        for ind in _STRAT_ORDER:
            if ind not in needed:
                continue
            fn = _STRAT_CALCS.get(ind)
            if fn is None:
                calc_lines.append(f"// ── {ind.upper()} — no strategy template available\n")
                continue
            try:
                calc_lines.append(fn(self._params(ind)))
            except Exception as exc:
                calc_lines.append(
                    f"// ── {ind.upper()} — render error: {exc}\n"
                )

        # ── 3. Translate buy/sell expressions ─────────────────────────────────
        pine_buy  = _translate_query(buy_query)  if buy_query  else "false"
        pine_sell = _translate_query(sell_query) if sell_query else "false"

        # ── 4. Assemble the script ─────────────────────────────────────────────
        header = (
            f"//@version=6\n"
            f"{_PINE_LICENSE}"
            f"// Generated by Trading App — {ts}\n"
            f"// Buy  : {buy_query}\n"
            f"// Sell : {sell_query}\n"
            f'strategy("Trading App Strategy", overlay=true,\n'
            f"         max_bars_back     = 500,\n"
            f"         default_qty_type  = strategy.percent_of_equity,\n"
            f"         default_qty_value = 100,\n"
            f"         commission_type   = strategy.commission.percent,\n"
            f"         commission_value  = 0.1)\n\n"
        )

        calcs = (
            "// ── Indicator computations ───────────────────────────────────────────────────\n"
            + "\n".join(calc_lines)
        )

        signals = f"""\

// ── Buy / Sell conditions ──────────────────────────────────────────────────────
strat_buy  = {pine_buy}
strat_sell = {pine_sell}

// ── Strategy entries / exits ──────────────────────────────────────────────────
if strat_buy
    strategy.entry("Long", strategy.long)
if strat_sell
    strategy.close("Long")

// ── Signal markers on chart ───────────────────────────────────────────────────
plotshape(strat_buy,  "Buy Signal",  shape.triangleup,   location.belowbar,
          color.new(color.teal, 0), size = size.small)
plotshape(strat_sell, "Sell Signal", shape.triangledown, location.abovebar,
          color.new(color.red,  0), size = size.small)
"""
        return header + calcs + signals


# ── Streamlit UI helper ────────────────────────────────────────────────────────

def render_export_buttons(
    overlays:    list[str],
    oscillators: list[str],
    sys_conf=None,
    region=None,
) -> None:
    """Renders Pine Script download buttons.

    Row 1 — two separate files (classic TradingView approach):
      • overlay_indicators.pine   → overlay=true, renders on the price chart
      • oscillator_indicators.pine → overlay=false, each in its own pane

    Row 2 — combined file (single overlay=true script):
      • combined_chart.pine  → overlays on price + oscillators normalized
        into the bottom N% of the chart; each oscillator gets an equal share
        of that section; the split ratio is configurable via a slider.
    """
    import streamlit as st
    r = region if region is not None else st

    exp = PineExporter(overlays, oscillators, sys_conf)

    # Collect indicators with no template (excluding globally unsupported)
    missing_ovl = [n for n in overlays    if n not in _OVL_TEMPLATES and n not in _UNSUPPORTED]
    missing_osc = [n for n in oscillators if n not in _OSC_TEMPLATES and n not in _UNSUPPORTED]
    unsup_sel   = [n for n in overlays + oscillators if n in _UNSUPPORTED]

    if unsup_sel:
        _unsup_reasons = {
            'pre':    'loads an external ML model; Pine Script has no model-loading capability',
            'bar':    'standard OHLC bar chart — use TradingView\'s native "Bars" chart type',
            'candle': 'standard candlestick chart — use TradingView\'s native "Candles" chart type',
        }
        _lines = [f"Cannot translate to Pine Script: **{', '.join(sorted(unsup_sel))}**"]
        for _n in sorted(unsup_sel):
            _lines.append(f"• **{_n}** — {_unsup_reasons.get(_n, 'not supported')}")
        r.warning("  \n".join(_lines))
    if missing_ovl or missing_osc:
        r.info(f"Pine template not yet available for: "
               f"**{', '.join(sorted(missing_ovl + missing_osc))}** "
               "(placeholder comment added)")

    # ── Row 1: separate files ─────────────────────────────────────────────────
    c1, c2 = r.columns(2)
    c1.download_button(
        label=f"⬇ overlay_indicators.pine  ({len(overlays)} selected)",
        data=exp.generate_overlay().encode("utf-8"),
        file_name="overlay_indicators.pine",
        mime="text/plain",
        use_container_width=True,
    )
    c2.download_button(
        label=f"⬇ oscillator_indicators.pine  ({len(oscillators)} selected)",
        data=exp.generate_oscillator().encode("utf-8"),
        file_name="oscillator_indicators.pine",
        mime="text/plain",
        use_container_width=True,
    )

    # ── Row 2: combined chart (only shown when both overlays AND oscillators) ──
    if overlays and oscillators:
        r.divider()
        r.caption(
            "**Combined chart** — overlays + oscillators in one `overlay=true` script.  \n"
            "TradingView natively cannot pin pane-height ratios from Pine Script; this "
            "workaround normalises oscillator values into the price axis so that the "
            f"split is fixed regardless of zoom level."
        )
        ovl_pct = r.slider(
            "Overlay area (%)",
            min_value=40, max_value=85, value=65, step=5,
            help=(
                f"Top {'{ovl_pct}'}% -> price overlays | "
                f"bottom {'{osc_pct}'}% -> {len(oscillators)} oscillator slot(s), "
                "each slot gets an equal share"
            ),
            key="pine_combined_ovl_pct",
        )
        osc_pct = 100 - ovl_pct
        n = len(oscillators)
        slot_w = osc_pct / n
        r.caption(
            f"Overlays: **{ovl_pct}%** of chart height  |  "
            f"Oscillators: **{osc_pct}%** split into **{n}** slot(s) "
            f"x **{slot_w:.1f}%** each"
        )
        r.download_button(
            label=(
                f"⬇ combined_chart.pine  "
                f"({len(overlays)} overlay + {len(oscillators)} oscillator)"
            ),
            data=exp.generate_combined(overlay_pct=float(ovl_pct)).encode("utf-8"),
            file_name="combined_chart.pine",
            mime="text/plain",
            use_container_width=True,
        )

    # ── Row 3: Strategy export ────────────────────────────────────────────────
    # Always shown when a sys_conf is available; the queries are read from the
    # stored configuration so they reflect whatever the user last saved.
    if sys_conf is not None:
        buy_query  = sys_conf.get_value("buy_query",  "") or ""
        sell_query = sys_conf.get_value("sell_query", "") or ""
        if buy_query or sell_query:
            r.divider()
            r.markdown("**Strategy export** — übersetzt deine Buy/Sell-Query in ein "
                       "TradingView `strategy()`-Skript (Pine Script v5).")
            r.code(
                f"Buy:  {buy_query}\nSell: {sell_query}",
                language="python",
            )
            try:
                strategy_pine = exp.generate_strategy(buy_query, sell_query)
                r.download_button(
                    label="⬇ strategy.pine",
                    data=strategy_pine.encode("utf-8"),
                    file_name="strategy.pine",
                    mime="text/plain",
                    use_container_width=True,
                )
            except Exception as _strat_err:
                r.error(f"Strategy-Export fehlgeschlagen: {_strat_err}")
