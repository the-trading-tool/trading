"""Measure selection scores, trend gates and risk profiles against the universe.

Read-only: every database connection is opened with mode=ro, so a measurement
run cannot change production data.

The default run reproduces the diagnosis the redesign started from — the
information coefficient of the stored composite scores, how much of it sits in
the analyst-target column, and what the common "is it trending" gates actually
deliver against simply holding the universe.

Usage:
    python score_eval.py                          # default battery, last 3 years
    python score_eval.py /years:2020-2025         # explicit range (or 2023,2024)
    python score_eval.py /horizon:63              # quarterly instead of monthly
    python score_eval.py /columns:sharpe,ewo      # measure these columns too
    python score_eval.py /gate:"adx > 25"         # add a gate (repeatable)
    python score_eval.py /gate:name=close>sma200  # ... with a label
    python score_eval.py /no-profiles             # skip the risk-profile section
    python score_eval.py /neutralise:roa          # residual IC against a column
    python score_eval.py /json:report.json        # also write the raw numbers

Exit code is 0 unless the panel could not be built.
"""
import json
import logging
import sys

from tradinglib import score_eval as se

logger = logging.getLogger("score_eval")

# Columns whose ranking power the default run reports.
DEFAULT_COLUMNS = ['overallValueTrend', 'overallTrend', 'pctTargetHighPrice',
                   'sharpe', 'logVola', 'relvol_ratio']

# The column the default run neutralises against: it is a snapshot of today's
# analyst price targets applied to the whole history, so any skill that
# disappears when it is removed was never point-in-time to begin with.
DEFAULT_NEUTRALISE = 'pctTargetHighPrice'

# "Is it trending now" in the forms the app already uses somewhere.
DEFAULT_GATES = {
    'structure sma50/200': '(close > sma200) & (sma50 > sma200)',
    'full ma stack':       '(close > sma20) & (sma20 > sma50) & (sma50 > sma200)',
    'trendDirection >= 2': 'trendDirection >= 2',
    'adx>25 & mom>rsiEma': '(adx > 25) & (momentum > rsi_ema)',
    'within 5% of ATH':    'close >= 0.95 * ath',
    'fps phase 3/4':       'fps_phase >= 3',
    'markov regime > 0':   'markov_regime > 0',
}

# Provisional risk profiles as absolute logVola cuts, read off the measured
# distribution of the universe. Absolute rather than cross-sectional on purpose:
# a percentile rank depends on whichever tickers happen to be in the panel that
# day and cannot be reproduced in the live chart path.
DEFAULT_PROFILES = {
    'conservative': 'logVola <= 0.110',
    'balanced':     'logVola <= 0.170',
    'dynamic':      'logVola <= 0.214',
    'offensive':    'logVola > 0',
}

# Everything the default gates and profiles reference, so the panel carries it.
GATE_COLUMNS = ['sma20', 'sma50', 'sma200', 'adx', 'momentum', 'rsi_ema',
                'trendDirection', 'fps_phase', 'markov_regime', 'ath', 'logVola']


def _arg_value(arg: str) -> str:
    """Value of a /flag:value or --flag=value argument."""
    if ':' in arg:
        return arg.split(':', 1)[1]
    if '=' in arg:
        return arg.split('=', 1)[1]
    return ''


def _parse_years(raw: str) -> list:
    """Accept '2023', '2023,2024' and '2020-2025'."""
    years = []
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            start, end = part.split('-', 1)
            years.extend(range(int(start), int(end) + 1))
        else:
            years.append(int(part))
    return years


def main(argv=None) -> int:
    argv = argv or sys.argv
    args = argv[1:]

    years, horizon, columns = None, 21, list(DEFAULT_COLUMNS)
    gates = dict(DEFAULT_GATES)
    profiles = dict(DEFAULT_PROFILES)
    neutralise = DEFAULT_NEUTRALISE
    bucket_count, json_path, tickers = 5, '', None
    min_cross_section = 200

    for arg in args:
        low = arg.lower()
        # Every flag needs its own branch here — a flag that is only documented
        # and never parsed is silently ignored, which has cost time before.
        if low.startswith(('/years', '--years')):
            years = _parse_years(_arg_value(arg))
        elif low.startswith(('/horizon', '--horizon')):
            horizon = int(_arg_value(arg))
        elif low.startswith(('/columns', '--columns')):
            columns = [c.strip() for c in _arg_value(arg).split(',') if c.strip()]
        elif low.startswith(('/add-columns', '--add-columns')):
            columns += [c.strip() for c in _arg_value(arg).split(',') if c.strip()]
        elif low.startswith(('/gate', '--gate')):
            value = _arg_value(arg)
            name, _, expression = value.partition('=')
            if not expression:
                name, expression = value, value
            gates[name.strip()] = expression.strip()
        elif low.startswith(('/tickers', '--tickers')):
            tickers = [t.strip() for t in _arg_value(arg).split(',') if t.strip()]
        elif low.startswith(('/neutralise', '--neutralise', '/neutralize', '--neutralize')):
            neutralise = _arg_value(arg)
        elif low.startswith(('/buckets', '--buckets')):
            bucket_count = int(_arg_value(arg))
        elif low.startswith(('/min-cross', '--min-cross')):
            min_cross_section = int(_arg_value(arg))
        elif low.startswith(('/json', '--json')):
            json_path = _arg_value(arg)
        elif low in ('/no-gates', '--no-gates'):
            gates = {}
        elif low in ('/no-profiles', '--no-profiles'):
            profiles = {}
        elif low in ('/help', '--help', '/?', '-h'):
            print(__doc__)
            return 0
        else:
            print(f"unknown argument: {arg}\n")
            print(__doc__)
            return 2

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if years is None:
        available = se.available_years()
        years = available[-3:] if available else []

    wanted = list(dict.fromkeys(columns + GATE_COLUMNS +
                                ([neutralise] if neutralise else [])))
    try:
        panel = se.load_panel(years=years, columns=wanted, horizon=horizon,
                              min_cross_section=min_cross_section, tickers=tickers)
    except Exception as exc:
        logger.error("panel could not be built: %s", exc)
        return 1

    report = se.evaluate(panel, columns=columns, gates=gates, profiles=profiles,
                         neutralise=neutralise, bucket_count=bucket_count)
    print(se.format_report(report))

    if json_path:
        serialisable = dict(report)
        serialisable['buckets'] = {k: v.reset_index().to_dict('records')
                                   for k, v in report['buckets'].items()}
        with open(json_path, 'w', encoding='utf-8') as handle:
            json.dump(serialisable, handle, indent=2, default=str)
        logger.info("raw numbers written to %s", json_path)
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.exit(main())
