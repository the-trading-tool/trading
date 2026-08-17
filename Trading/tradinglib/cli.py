import sys
from typing import Dict, Any


def print_help():
    """Print usage information for the CLI scripts to stdout."""
    print("Please run using parameters:\n"
          "  true - true = all index members but 5 days back only,\n"
          "  init - init = all index members full year,\n"
          "  /all - all = all tickers but does not send Pushover notifications,\n"
          "  /year:[YYYY] - get data for a specific year (max 6 years back),\n"
          "  /index:^SPX[,^DJI,...] - scan only the given index/indices (comma-separated, case-insensitive),\n"
          "  /group:NAME1,NAME2 - only tickers of the given groups from the indices table\n"
          "                       (e.g. /group:CRYPTO or /group:METALS,COMMODITIES),\n"
          "  /add_current - adds the last close price,\n"
          "  /inverse - select tickers not related to an index,\n"
          "  /silent - suppress notifications,\n"
          "  /worker:N - number of workers to use,\n"
          "  /rescore - re-compute overallTrend/overallValueTrend from stored DB columns (fast, no yfinance),\n"
          "  /backfill:ind1,ind2 - fill missing indicator columns from local OHLCV (fast, no yfinance),\n"
          "                        known indicators: heikin,markov,macd,rsi,ewo,adx,dema,hor,sup,relvol,atc,fps\n"
          "  /force            - combined with /backfill: re-compute all tickers even if already filled\n"
          "                      (use when column was added with DEFAULT 0 and all values are 0),\n"
          "  /log:LEVEL - enable console logging at LEVEL (DEBUG, INFO, ...),\n"
          "  /logfile:PATH - also write logs to PATH")


def parse_args(argv=None) -> Dict[str, Any]:
    """Parse the script's simple /pref:xxx CLI style into a dict.

    Returns a dict with keys matching the variables used by asset_perf2.
    """
    if argv is None:
        argv = sys.argv

    result = {
        'simulate': True,
        'init': False,
        'all': False,
        'year': '',
        'inverse': False,
        'index_name': '',
        'index_only': False,
        'select': False,
        'index_member': False,
        'group': None,      # list[str] when set (group names from the indices table)
    'selection': None,
        'silent': False,
        'add_current': False,
        'memory': False,
        'worker': 0,
        # enable console logging by default for scripts
        'log_to_console': True,
        'log_level': 'INFO',
        'log_file': None,
        # fast modes (no yfinance)
        'rescore': False,
        'backfill': None,   # list[str] when set
        'force': False,     # skip already-filled check in backfill
        'repair': False,    # get_asset_info: nur Ticker ohne Namen nachziehen
        'apply': False,     # sync_index_members: schreiben statt Trockenlauf
        'nocheck': False,   # sync_index_members: neue Symbole nicht validieren
        'tickers': None,    # repair_intraday_tz: nur diese Ticker (kommagetrennt)
    }

    if len(argv) <= 1:
        return result

    if argv[1].lower() == 'true':
        result['simulate'] = True
    if argv[1].lower() == 'init':
        result['init'] = True

    for i in range(1, len(argv)):
        if len(argv[i]) == 0:
            continue
        if argv[i][:1] == '/':
            # Lower-case only the key (prefix before ':'), leave the value as-is
            # — otherwise case-sensitive values would be lost
            # (e.g. /index:^SPX or /select:'... LIKE "%.MC"'; SQLite '='/ LIKE
            # are case-sensitive). Values that should be case-insensitive are
            # explicitly normalised below (group → upper, backfill → lower).
            raw = argv[i][1:]
            if ":" in raw:
                pref, suf = raw.split(':', 1)
            else:
                pref, suf = raw, None
            pref = pref.lower()
            if pref == 'silent':
                result['silent'] = True
            if pref == 'select':
                result['select'] = True
                if suf:
                    result['selection'] = suf
            if pref == 'index_member':
                result['index_member'] = True
            if pref == 'add_current':
                result['add_current'] = True
            if pref == 'all':
                result['all'] = True
            if pref == 'init':
                result['init'] = True
            if pref == 'inverse':
                result['inverse'] = True
            if pref == 'memory':
                result['memory'] = True
            if pref == 'year' and suf:
                try:
                    result['year'] = int(suf)
                except Exception:
                    result['year'] = ''
            if pref == 'worker' and suf:
                try:
                    result['worker'] = int(suf)
                except Exception:
                    result['worker'] = 1
            if pref == 'index':
                if suf:
                    result['index_name'] = suf
                else:
                    result['index_only'] = True
            if pref == 'group' and suf:
                # Group names in the DB are uppercase (CRYPTO, METALS, ^GDAXI …);
                # the query also uses UPPER(i.name) → input case is irrelevant.
                result['group'] = [s.strip().upper() for s in suf.split(',') if s.strip()]
            if pref == 'log':
                result['log_to_console'] = True
                if suf:
                    result['log_level'] = suf.upper()
            if pref == 'logfile' and suf:
                result['log_file'] = suf
            if pref == 'rescore':
                result['rescore'] = True
            if pref == 'backfill' and suf:
                # Indicator keys in INDICATOR_BACKFILL_MAP are lowercase →
                # normalise input so that /backfill:Heikin still matches.
                result['backfill'] = [s.strip().lower() for s in suf.split(',') if s.strip()]
            if pref == 'force':
                result['force'] = True
            if pref == 'repair':
                result['repair'] = True
            if pref == 'apply':
                result['apply'] = True
            if pref == 'nocheck':
                result['nocheck'] = True
            if pref == 'tickers' and suf:
                result['tickers'] = suf

    return result


def parse_interval_periods(argv=None):
    """Parse positional interval:period pairs from argv while preserving
    the original script behavior.

    Returns: (intervals, periods, pos, arg)
      - intervals: list of interval strings (left side of colon)
      - periods: list of period strings (right side of colon)
      - pos: '' if no pairs found, otherwise the integer index of the last ':' found
      - arg: last pref-style argument (without leading '/') seen, or '' when none
    """
    if argv is None:
        argv = sys.argv

    intervals = []
    periods = []
    pos = ''
    arg = ''

    last_suf = None
    for i in range(1, len(argv)):
        a = argv[i]
        if not a:
            continue
        # treat args that start with '/' as pref-style options and do not
        # parse them as interval:period colon pairs (cleanup of legacy ambiguity)
        if a.startswith('/'):
            try:
                arg = a[1:]
            except Exception:
                pass
            continue

        # only non-pref args may be colon pairs like '1d:1mo'
        if ':' in a:
            try:
                pos = a.index(':')
                pref, suf = a.split(':', 1)
                intervals.append(pref)
                periods.append(suf)
                last_suf = suf
            except Exception:
                # ignore malformed pairs
                pass

    return intervals, periods, pos, arg, last_suf
