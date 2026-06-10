import numpy as np
import pandas as pd
from ta.volatility import BollingerBands
import sys
import logging
import plotly.graph_objects as go

logger = logging.getLogger(__name__)

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator


class Hor(_indicator._Indicator):

    is_oszilator = True
    name = "Horcrux Oscillator"

    params = {
        'bb_length':  {'type': 'int',   'default': 20, 'min': 2,  'max': 200, 'label': 'Bollinger Band length'},
        'lookback':   {'type': 'int',   'default': 50, 'min': 5,  'max': 500, 'label': 'Lookback window'},
        'adjustment': {'type': 'float', 'default': 0.0,             'label': 'Threshold adjustment'},
    }

    def __init__(self, df, symbol="", bb_length=20, lookback=50, adjustment=0):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""

        super().__init__(df=df, symbol=symbol)

        self.show_buy_sell = False
        self.bb_length = bb_length
        self.lookback = lookback
        self.adjustment = adjustment

        self.data()

    # -------------------------------------------------------------
    # BUY / SELL SIGNALS
    # -------------------------------------------------------------

    def compute_signals(self):
        """Generate buy/sell signal columns from indicator values."""

        buy = (
            (self.df["hor_val"] > self.df["hor_threshold"]) &
            (self.df["hor_val"].shift(1) <= self.df["hor_threshold"].shift(1))
        )

        sell = (
            (self.df["hor_val"] < self.df["hor_threshold"]) &
            (self.df["hor_val"].shift(1) >= self.df["hor_threshold"].shift(1))
        )

        self.df["hor_buy"] = np.where(buy, self.df["hor_val"], np.nan)
        self.df["hor_sell"] = np.where(sell, self.df["hor_val"], np.nan)

    # -------------------------------------------------------------
    # Bollinger Bands
    # -------------------------------------------------------------

    def bollinger(self, series, length, std):
        """Compute Bollinger Band upper/lower values for the signal series."""

        bb = BollingerBands(
            close=series,
            window=length,
            window_dev=std
        )

        mid = bb.bollinger_mavg()
        upper = bb.bollinger_hband()
        lower = bb.bollinger_lband()

        return mid, upper, lower
    # -------------------------------------------------------------
    # Horcrux Berechnung
    # -------------------------------------------------------------

    def compute_horcrux(self):
        """Apply the core Horcrux (std-dev channel) algorithm."""

        close = self.df["Close"]
        source = self.df["High"]

        std_levels = [0.5, 1, 1.5, 2, 2.5, 3, 3.5]

        mids = []
        uppers = []
        lowers = []

        for std in std_levels:
            m, u, l = self.bollinger(close, self.bb_length, std)
            mids.append(m)
            uppers.append(u)
            lowers.append(l)

        average_middle = pd.concat(mids, axis=1).mean(axis=1)
        
        state = np.zeros(len(self.df))

        try:
            for i in range(len(self.df)):

                s = source.iloc[i]

                if s < lowers[6].iloc[i]:
                    state[i] = 15
                elif s < lowers[5].iloc[i]:
                    state[i] = 14
                elif s < lowers[4].iloc[i]:
                    state[i] = 13
                elif s < lowers[3].iloc[i]:
                    state[i] = 12
                elif s < lowers[2].iloc[i]:
                    state[i] = 11
                elif s < lowers[1].iloc[i]:
                    state[i] = 10
                elif s < lowers[0].iloc[i]:
                    state[i] = 9
                elif s < average_middle.iloc[i]:
                    state[i] = 8
                elif s < uppers[0].iloc[i]:
                    state[i] = 7
                elif s < uppers[1].iloc[i]:
                    state[i] = 6
                elif s < uppers[2].iloc[i]:
                    state[i] = 5
                elif s < uppers[3].iloc[i]:
                    state[i] = 4
                elif s < uppers[4].iloc[i]:
                    state[i] = 3
                elif s < uppers[5].iloc[i]:
                    state[i] = 2
                elif s < uppers[6].iloc[i]:
                    state[i] = 1
                else:
                    state[i] = 0
        except Exception as e:
            logger.error("Error computing state: %s", e)

        self.df["state"] = state

        self.df["stateMax"] = self.df["state"].rolling(self.lookback).max()
        self.df["stateMin"] = self.df["state"].rolling(self.lookback).min()

        self.df["hor_val"] = self.df["stateMax"] - self.df["state"]

        threshold = (self.df["stateMax"] + self.df["stateMin"]) / 2

        threshold_adjustment = (self.adjustment / 100) * threshold

        self.df["hor_threshold"] = threshold + threshold_adjustment

        self.df["horcrux_state"] = np.where(
            self.df["hor_val"] < self.df["hor_threshold"],
            "weak",
            "strong"
        )

    # -------------------------------------------------------------
    # DATA
    # -------------------------------------------------------------

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""

        self.compute_horcrux()
        self.compute_signals()
        self.df = self.df.drop(['state','stateMax','stateMin','hor_buy','hor_sell'], axis=1)
        
    # -------------------------------------------------------------
    # PLOT
    # -------------------------------------------------------------

    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""

        self.fig = go.Figure()

        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        colors = [
            "orange" if h < t else "lime"
            for h, t in zip(self.df["hor_val"], self.df["hor_threshold"])
        ]

        self.fig.add_trace(
            go.Scatter(
                x=self.df["Date"],
                y=self.df["hor_val"],
                name="Horcrux",
                showlegend=False,
                line=dict(color="black", width=2),
            )
        )

        self.fig.add_trace(
            go.Scatter(
                x=self.df["Date"],
                y=self.df["hor_threshold"],
                name="Threshold",
                showlegend=False,
                line=dict(color="blue", width=2),
            )
        )

        self.fig.add_trace(
            go.Bar(
                x=self.df["Date"],
                y=self.df["hor_val"],
                marker_color=colors,
                name="State",
                opacity=0.4,
                showlegend=False,
            )
        )
