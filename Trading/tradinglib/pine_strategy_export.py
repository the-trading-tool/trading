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

# ── Pine-Helper (Funktionen/Konstanten, einmalig oben im Skript) ─────────────
_HELPERS: dict[str, str] = {
    'atc_dev': 'atc_dev = input.float(2.0, "ATC Std-Abweichung", group="ATC")',
    'atc_reg': (
        '// ATC: manuelle OLS-Regression über die letzten `length` Bars.\n'
        '// Rueckgabe [Ankerwert, aktueller Wert, Residual-Stdev].\n'
        'atc_reg(length) =>\n'
        '    float n   = float(length)\n'
        '    float sx  = 0.0\n'
        '    float sy  = 0.0\n'
        '    float sxx = 0.0\n'
        '    float sxy = 0.0\n'
        '    for i = 0 to length - 1\n'
        '        float xi = float(i)\n'
        '        float yi = close[length - 1 - i]\n'
        '        sx  += xi\n'
        '        sy  += yi\n'
        '        sxx += xi * xi\n'
        '        sxy += xi * yi\n'
        '    float denom = n * sxx - sx * sx\n'
        '    float b     = denom != 0.0 ? (n * sxy - sx * sy) / denom : 0.0\n'
        '    float a     = (sy - b * sx) / n\n'
        '    float ssr   = 0.0\n'
        '    for i = 0 to length - 1\n'
        '        float err = close[length - 1 - i] - (a + b * float(i))\n'
        '        ssr += err * err\n'
        '    [a, a + b * (n - 1.0), math.sqrt(ssr / n)]'
    ),
}

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

    'ewo':            {'code': 'ewo = ta.sma(close, 5) - ta.sma(close, 21)', 'deps': [], 'helpers': []},
    'ewo_ema':        {'code': 'ewo_ema = ta.ema(ewo, 9)', 'deps': ['ewo'], 'helpers': []},
    'sup_support':    {'code': 'sup_support = ta.lowest(low, 21)', 'deps': [], 'helpers': []},
    'sup_resistance': {'code': 'sup_resistance = ta.highest(high, 21)', 'deps': [], 'helpers': []},
    'ema21':          {'code': 'ema21 = ta.ema(close, 21)', 'deps': [], 'helpers': []},
    'rsi':            {'code': 'rsi = ta.rsi(close, 8)', 'deps': [], 'helpers': []},

    'dTrend':  {'code': 'dTrend = close != close[1] ? (close - close[1]) / close * 100 : 0.0',
                'deps': [], 'helpers': []},
    '_wk_close': {'code': '_wk_close = request.security(syminfo.tickerid, "W", close, '
                          'lookahead=barmerge.lookahead_off)', 'deps': [], 'helpers': []},
    'wkTrend': {'code': 'wkTrend = _wk_close != _wk_close[1] ? '
                        '(_wk_close - _wk_close[1]) / _wk_close * 100 : 0.0',
                'deps': ['_wk_close'], 'helpers': []},

    # ── ATC: adaptiver Regressionskanal (Serie pro Bar; spiegelt _t_atc) ──────
    # High-Anker = Bars zurück zum höchsten High; Low-Anker = zum tiefsten Low.
    'atc_top_high': {'code':
        'int atc_h_off = 0\n'
        'for i = 1 to math.min(499, bar_index)\n'
        '    if high[i] > high[atc_h_off]\n'
        '        atc_h_off := i\n'
        'atc_hlen = math.max(2, atc_h_off + 1)\n'
        '[atc_h_anc, atc_h_cur, atc_hstd] = atc_reg(atc_hlen)\n'
        'atc_top_high = atc_h_cur + atc_dev * atc_hstd',
        'deps': [], 'helpers': ['atc_dev', 'atc_reg']},
    'atc_bot_low': {'code':
        'int atc_l_off = 0\n'
        'for i = 1 to math.min(499, bar_index)\n'
        '    if low[i] < low[atc_l_off]\n'
        '        atc_l_off := i\n'
        'atc_llen = math.max(2, atc_l_off + 1)\n'
        '[atc_l_anc, atc_l_cur, atc_lstd] = atc_reg(atc_llen)\n'
        'atc_bot_low = atc_l_cur - atc_dev * atc_lstd',
        'deps': [], 'helpers': ['atc_dev', 'atc_reg']},
    # Aliase (atc.py: atc_low = atc_bot_low, atc_high = atc_top_high)
    'atc_high': {'code': 'atc_high = atc_top_high', 'deps': ['atc_top_high'], 'helpers': []},
    'atc_low':  {'code': 'atc_low = atc_bot_low',  'deps': ['atc_bot_low'],  'helpers': []},
}

# Query-Spalten, die (noch) nicht rein technisch abbildbar sind.
_UNSUPPORTED = {
    'overallValueTrend': 'zusammengesetzter Score mit Fundamentaldaten (in TradingView nicht abbildbar)',
    'overallTrend':      'zusammengesetzter Score mit Fundamentaldaten (in TradingView nicht abbildbar)',
}

# Query verwendet Groß-OHLC (High/Low/Open); Pine nutzt Kleinschreibung.
_CASE_MAP = {'High': 'high', 'Low': 'low', 'Open': 'open', 'Close': 'close'}


class StrategyExportError(ValueError):
    """Raised when a buy/sell formula references non-exportable columns."""


def _referenced_columns(expr: str) -> set[str]:
    """Alle Bezeichner (Spaltennamen) eines Ausdrucks, case-normalisiert."""
    return {_CASE_MAP.get(t, t) for t in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', expr or '')}


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
        if spec['code'] is not None:
            def_blocks.append(spec['code'])

    for c in sorted(cols):
        if c in _COL_DEFS:
            add(c)
    helper_blocks = [_HELPERS[k] for k in helper_keys]
    return helper_blocks, def_blocks


def export_strategy(name: str, buy: str, sell: str) -> str:
    """Vollständiges Pine-`strategy()`-Skript für eine Buy/Sell-Formel.

    Raises StrategyExportError, wenn eine Bedingung nicht-exportierbare Spalten
    referenziert (z. B. overallValueTrend).
    """
    cols = _referenced_columns(buy) | _referenced_columns(sell)
    unsupported = {c: _UNSUPPORTED[c] for c in cols if c in _UNSUPPORTED}
    if unsupported:
        reasons = '; '.join(f'{c}: {r}' for c, r in sorted(unsupported.items()))
        raise StrategyExportError(f'{name}: nicht exportierbar — {reasons}')
    unknown = [c for c in cols if c not in _COL_DEFS]
    if unknown:
        raise StrategyExportError(f'{name}: unbekannte Spalten {sorted(unknown)}')

    helper_blocks, def_blocks = _resolve(cols)
    helpers = ('\n\n'.join(helper_blocks) + '\n\n') if helper_blocks else ''
    body = '\n\n'.join(def_blocks)

    return f"""//@version=5
strategy("{name}", overlay=true, default_qty_type=strategy.percent_of_equity,
     default_qty_value=100, calc_on_every_tick=false)

// ── Helfer ───────────────────────────────────────────────────────────────────
{helpers}// ── Indikatoren (aus den App-Formeln abgeleitet) ─────────────────────────────
{body}

// ── Bedingungen ──────────────────────────────────────────────────────────────
longCond = {_translate_expr(buy)}
exitCond = {_translate_expr(sell)}

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
            result[strat] = {'pine': export_strategy(strat, first.get('buy', ''), first.get('sell', ''))}
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
            _emit('buy_query', export_strategy('buy_query', buy, sell), None)
        except StrategyExportError as e:
            _emit('buy_query', None, str(e))

    print(f"\n{ok} exportiert, {skipped} abgelehnt (nach {out}/).")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
