import logging
import streamlit as st
from typing import Optional
from tradinglib.indicator import indicator
from tradinglib import multi_select as ms
from tradinglib.utils import get_display_name

logger = logging.getLogger(__name__)


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
               region: Optional[object] = None, log_exceptions: bool = False, progress_callback=None):
        """Render the grid.

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

                t_chart = tc.tiny_chart(symbol, name, url=f"{url}", period=period, interval=interval, range_breaks=True,
                                         candle_chart=True, add_sub_plots=oszilators, show_trend=False, trend_length=0,
                                         add_overlays=overlays, username=username, zoom=True)

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
