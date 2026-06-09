import sys
import logging
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from sklearn.linear_model import LinearRegression
from typing import Tuple

logger = logging.getLogger(__name__)

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

class Atc(_indicator._Indicator):

    is_oszilator = False
    name = 'Auto Trend Channels'

    params = {
        'dev_multi':      {'type': 'float', 'default': 2.0,  'label': 'Channel deviation multiplier'},
        'use_gog_scale':  {'type': 'bool',  'default': False, 'label': 'Log scale'},
        'use_exp_weight': {'type': 'bool',  'default': False, 'label': 'Exponential weighting'},
    }

    def __init__(self, df, symbol = "", dev_multi = 2.0, use_gog_scale = False, use_exp_weight = False ,anchors = ["high", "low", "zero"], channel_colors=["darkred", "darkgreen", "darkblue"]):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        if df.empty:
            logger.debug("Empty dataframe")
        # Do NOT mutate the passed df — reset_index(inplace=True) would destroy
        # the caller's DatetimeIndex and cause NaN values downstream.
        super().__init__(df=df, symbol=symbol)
        self.use_gog_scale = use_gog_scale
        self.use_exp_weight = use_exp_weight
        self.dev_multi = dev_multi
        self.anchors = anchors
        self.channel_colors = channel_colors
        self.max_bars = len(self.df)   # use len(), not len(df['Date'])
        self.data()
                
    def transform_price(self, p: pd.Series) -> pd.Series:
        """Transform price data using log scale if enabled."""
        return np.log10(p) if self.use_gog_scale else p

    def inverse_transform_price(self, p: np.ndarray) -> np.ndarray:
        """Inverse transform the price data."""
        return np.power(10, p) if self.use_gog_scale else p

    def exponential_weights(self, n: int, decay: float = 0.90) -> np.ndarray:
        """Return exponential weights for a given length."""
        return decay ** np.arange(n)[::-1]

    def find_bar_highest(self, high_series: pd.Series, max_bars: int) -> int:
        """Find the bar length since the highest high."""
        window = high_series[-max_bars:]
        idxmax = window.idxmax()
        return len(high_series) - high_series.index.get_loc(idxmax)

    def find_bar_lowest(self, low_series: pd.Series, max_bars: int) -> int:
        """Find the bar length since the lowest low."""
        window = low_series[-max_bars:]
        idxmin = window.idxmin()
        return len(low_series) - low_series.index.get_loc(idxmin)

    def find_slope_zero(self, series: pd.Series, max_bars: int) -> int:
        """
        Find the length over which the slope is closest to zero.
        Returns the best length.
        """
        best_len = 2
        best_slope = float("inf")
        for L in range(2, min(max_bars, len(series))):
            y = series.iloc[-L:]
            x = np.arange(L).reshape(-1, 1)
            model = LinearRegression()
            model.fit(x, y.values)
            slope = model.coef_[0]
            if abs(slope) < best_slope:
                best_slope = abs(slope)
                best_len = L
        return best_len
    
    def calc_regression_channel(self, series: pd.Series, length: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float, float, float]:
        """
        Calculate regression channel lines and metrics.
        Returns:
        reg_line, top channel, bottom channel, slope, r², and Pearson r.
        """
        y = series.iloc[-length:]
        x = np.arange(length).reshape(-1, 1)
        model = LinearRegression()
        if self.use_exp_weight:
            weights = self.exponential_weights(length)
            weights /= weights.sum()
            model.fit(x, y.values, sample_weight=weights)
        else:
            model.fit(x, y.values)
        slope = model.coef_[0]
        intercept = model.intercept_

        reg_line = intercept + slope * x.squeeze()
        residuals = y.values - reg_line
        stdev = np.std(residuals)
        r_val = np.corrcoef(np.arange(length), y.values)[0, 1]
        r2 = r_val ** 2

        return reg_line, reg_line + self.dev_multi * stdev, reg_line - self.dev_multi * stdev, slope, r2, r_val        
    

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""

        if isinstance(self.df.columns, pd.MultiIndex):
            self.df.columns = self.df.columns.get_level_values(0)

        self.close = self.df["Close"]
        self.high = self.df["High"]
        self.low = self.df["Low"]

        self.close_t = self.transform_price(self.close)

        def add_array(values, name):
            """Append array-level plot traces (fill areas, bands) to the figure."""
            new_col = np.full(len(self.df), np.nan)
            new_col[-len(values):] = values
            self.df[name] = new_col

        for name, color in zip(self.anchors, self.channel_colors):
            if name == "high":
                length = self.find_bar_highest(self.high, self.max_bars)
            elif name == "low":
                length = self.find_bar_lowest(self.low, self.max_bars)
            elif name == "zero":
                length = self.find_slope_zero(self.close_t, self.max_bars)
            length = max(2, min(length, len(self.close)))
            pass
        
            try:
                mid, top, bot, slope, r2, r_val = self.calc_regression_channel(self.close_t, length)
                add_array(self.inverse_transform_price(mid), f"atc_mid_{name}")
                add_array(self.inverse_transform_price(top), f"atc_top_{name}")
                add_array(self.inverse_transform_price(bot), f"atc_bot_{name}")
            except Exception as e:
                logger.error("%s", e)
                pass

        if 'atc_bot_low' in self.df:
            self.df['atc_low'] = self.df['atc_bot_low']                        
        if 'atc_top_high' in self.df:
            self.df['atc_high'] = self.df['atc_top_high']                        
        
    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""

        self.fig = go.Figure()

        # Build a temporary flat copy for plotting — never mutate self.df in-place.
        try:
            plot_base = self.df.reset_index()   # returns NEW df, no inplace
        except Exception:
            plot_base = self.df.copy()

        date_col = next((c for c in ['Date', 'Datetime', 'timestamp'] if c in plot_base.columns), None)
        if date_col is None:
            return

        for name, color in zip(self.anchors, self.channel_colors):

            try:
                top_col = f'atc_top_{name}'
                mid_col = f'atc_mid_{name}'
                bot_col = f'atc_bot_{name}'

                ref_col = top_col if top_col in plot_base.columns else mid_col
                if ref_col not in plot_base.columns:
                    continue
                plot_df = plot_base[plot_base[ref_col].notna()]
                if plot_df.empty:
                    continue

                self.fig.add_trace(go.Scatter(
                    x=plot_df[date_col],
                    y=plot_df[mid_col],
                    line=dict(dash='dot', color=color, width=2),
                    opacity=0.7,
                    showlegend=False,
                    name=mid_col)
                )

                self.fig.add_trace(go.Scatter(
                    x=plot_df[date_col],
                    y=plot_df[top_col],
                    line=dict(color=color, width=2),
                    opacity=0.7,
                    showlegend=False,
                    name=top_col)
                )

                self.fig.add_trace(go.Scatter(
                    x=plot_df[date_col],
                    y=plot_df[bot_col],
                    line=dict(color=color, width=2),
                    opacity=0.7,
                    showlegend=False,
                    name=bot_col)
                )

            except Exception:
                pass


#            trend = "Up" if slope > 0.01 else "Down" if slope < -0.01 else "Flat"


        """

    channel_info.append({
        "anchor": name,
        "length": length,
        "slope": slope,
        "r2": r2,
        "r": r_val,
        "last_mid": last_mid,
        "last_top": top_plot[-1],
        "last_bot": bot_plot[-1],
        "trend": trend,
        "color": color,
        "x_pos": xvals[-1]
    })


# Confluence and current trend check
current_condition = ""
confluence_detected = False
if channel_info:
    mids = [c["last_mid"] for c in channel_info]
    avg_mid = np.mean(mids)
    stdev_mids = np.std(mids)
    threshold = 0.5 * stdev_mids  # Confluence threshold
    if np.max(mids) - np.min(mids) < threshold:
        confluence_detected = True
        x_confl = max(c["x_pos"] for c in channel_info)
        ax.annotate("Confluence", xy=(x_confl, avg_mid),
                    xytext=(-100, -30), textcoords="offset points",
                    arrowprops=dict(arrowstyle="->", color="yellow"),
                    color="yellow", fontsize=9, fontweight="bold")

    current_price = close.iloc[-1]
    tops = [c["last_top"] for c in channel_info]
    bots = [c["last_bot"] for c in channel_info]
    if current_price > max(tops):
        current_condition = "Overextended Uptrend"
    elif current_price < min(bots):
        current_condition = "Oversold Downtrend"
    else:
        current_condition = "Within Channels"
        """            

