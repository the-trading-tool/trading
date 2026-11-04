import logging
import streamlit as st
from typing import Optional
from tradinglib.indicator import indicator
from tradinglib import multi_select as ms
from tradinglib.utils import get_display_name

logger = logging.getLogger(__name__)

# Basis des Start-Zooms in Kerzen; multipliziert mit dem konfigurierten
# Zoomfaktor (chart_zoom_factor, Default 4) ergibt sie das sichtbare Fenster.
# 21 wie im Asset Viewer, damit beide Ansichten denselben Ausschnitt zeigen.
ZOOM_BASE = 21

# Chart periods with the calendar days they cover, shortest first.
_PERIOD_DAYS = (('1mo', 31), ('3mo', 92), ('6mo', 183), ('1y', 366), ('2y', 731),
                ('5y', 1827), ('10y', 3653), ('max', None))
# How far back intraday bars reach (Yahoo limits; the local tables are no longer).
_INTRADAY_DAYS = {'1m': 7, '2m': 59, '5m': 59, '15m': 59, '30m': 59, '90m': 59,
                  '60m': 729, '1h': 729}


def period_since(start, period: str, interval: str, today=None) -> tuple:
    """(period, interval) whose chart reaches back to *start* (the buy date).

    The configured period stays when it already covers the date; otherwise the
    shortest longer one. An intraday interval that cannot reach that far back
    becomes daily bars. *start* unusable -> both unchanged.
    """
    import pandas as pd
    try:
        start = pd.Timestamp(start)
    except (TypeError, ValueError):
        return period, interval
    if pd.isna(start):
        return period, interval
    today = pd.Timestamp(today) if today is not None else pd.Timestamp.now()
    need = max((today.normalize() - start.normalize()).days, 0) + 1
    if interval in _INTRADAY_DAYS and need > _INTRADAY_DAYS[interval]:
        interval = '1d'
    days = dict(_PERIOD_DAYS)
    have = days.get(period, 0)
    if have is None or (have and have >= need):
        return period, interval
    for name, span in _PERIOD_DAYS:
        if span is None or span >= need:
            return name, interval
    return 'max', interval


class ChartsGridRenderer:
    """Reusable renderer for a grid of tiny charts.

    Parameters (most are positional via named args in render):
      columns: number of columns in grid.

    render(...) accepts named params:
      tickers: iterable of ticker symbols
      tc: tiny_chart module/class with tiny_chart(...) factory
      period, interval: passed to tiny_chart
      overlays, oszilators: passed to tiny_chart
      username, url: passed to tiny_chart
      chart_config: plotly config object
      name_df: optional pandas.DataFrame to lookup human-readable name; if provided must contain columns name_lookup_col and name_field
      mkt: optional object with attribute df (pandas.DataFrame) used as name source when provided
      name_lookup_col: column in df that contains ticker values (default 'ticker')
      name_field: column with display name (default 'longName')
    """

    def __init__(self, columns: int = 2):
        """Set the number of chart columns in the grid."""
        self.columns = columns

    def get_selectors(self, sys_config, list_data=None):
        """Show multi-selectors UI and return normalized selectors.

        sys_config: SystemConfig-like object that implements get_selectors(interval, period, overlays, oszilators)
        list_data: optional selector lists to pass to the MultiCheckboxSelector
        Returns: (interval, period, overlays, oszilators)
        """
        if list_data is None:
            # load indicator definitions if available
            try:
                _ = indicator.IndicatorLoader('./tradinglib/indicator')
            except Exception:
                pass
            list_data = ms.MultiCheckboxSelector.lists

        multi_selector = ms.MultiCheckboxSelector(list_data=list_data, sys_conf=sys_config)
        multi_selector.render()
        if sys_config.get_value("pine_export", False):
            multi_selector.render_pine_export()
        interval = multi_selector.get_selected_options('Interval')[:1]
        period = multi_selector.get_selected_options('Period')[:1]
        overlays = multi_selector.get_selected_options('Overlay')
        oszilators = multi_selector.get_selected_options('Oszilator')

        try:
            interval, period, overlays, oszilators = sys_config.get_selectors(interval, period, overlays, oszilators)
        except Exception:
            # fallback: return the raw selections
            pass

        return (interval, period, overlays, oszilators)

    def render(self, *, tickers, tc, period, interval, overlays, oszilators, username, url, chart_config,
               name_df=None, mkt=None, name_lookup_col: str = 'ticker', name_field: str = 'longName',
               region: Optional[object] = None, log_exceptions: bool = False, progress_callback=None,
               start_dates: Optional[dict] = None):
        """Render the grid.

        start_dates: optional {ticker: date} -- that ticker's chart then shows the
                prices from this date on (the buy date), with a period long enough
                to reach it. Tickers without an entry keep the normal view.

        region: optional streamlit-like region object (defaults to global st). The region must provide
                .empty() and .columns(n) methods.
        log_exceptions: if True, print exceptions with the symbol name to aid debugging (matches prior behavior).
        progress_callback: optional callable(done, total) invoked after each ticker has been processed.
        """
        if region is None:
            region = st

        # normalize tickers to a list (supports pd.Series)
        try:
            tickers = list(tickers)
        except Exception:
            tickers = [tickers]

        items = len(tickers)
        rows = round(items / self.columns) + 2

        ic = {}
        ic_c = {}
        for i in range(0, rows):
            ic[i] = region.empty()
            ic_c[i] = ic[i].columns(self.columns)
        j = -1

        # choose source for name lookup
        df_source = None
        if mkt is not None and hasattr(mkt, 'df'):
            df_source = getattr(mkt, 'df')
        elif name_df is not None:
            df_source = name_df

        for p in range(0, items):
            symbol = None
            try:
                symbol = tickers[p]

                name = ""
                try:
                    if df_source is not None:
                        sel = df_source.loc[df_source[name_lookup_col] == symbol]
                        if not sel.empty:
                            name = get_display_name(sel.iloc[0], name_col=name_field)
                except Exception:
                    name = ""

                # zoom_base statt trend_length: trend_length=0 haelt die
                # Trendberechnung (indicator.trend) unveraendert, waehrend der
                # Startausschnitt demselben konfigurierbaren Zoomfaktor folgt
                # wie der Asset Viewer (dort ist die Basis ebenfalls 21).
                since = (start_dates or {}).get(symbol)
                p_sym, i_sym = (period_since(since, period, interval) if since is not None
                                else (period, interval))
                t_chart = tc.tiny_chart(symbol, name, url=f"{url}", period=p_sym, interval=i_sym, range_breaks=True,
                                         candle_chart=True, add_sub_plots=oszilators, show_trend=False, trend_length=0,
                                         add_overlays=overlays, username=username, zoom=True,
                                         zoom_base=ZOOM_BASE, zoom_start=since)

                # now add the charts to the right rows / cols
                i = p % self.columns
                if i == 0:
                    j += 1
                ic_c[j][i].plotly_chart(
                    t_chart.fig,
                    use_container_width=True,
                    theme="streamlit",
                    config=chart_config,
                )
            except Exception as e:
                # Keep rendering despite individual chart errors; optionally log like original callers
                if log_exceptions:
                    try:
                        logger.warning("Error occurred while plotting chart for %s: %s", symbol, e)
                    except Exception:
                        logger.warning("Error occurred while plotting chart: %s", e)
                continue
            finally:
                if progress_callback is not None:
                    try:
                        progress_callback(p + 1, items)
                    except Exception:
                        pass
