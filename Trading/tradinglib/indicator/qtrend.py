import numpy as np
import pandas as pd
import sys
import plotly.graph_objects as go
import streamlit as st 

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    print('No Import')

from tradinglib.indicator import _indicator


class Qtrend(_indicator._Indicator):

    is_oszilator = False
    name = "QTrend + Keltner Squeeze"

    params = {
        'trend_period':   {'type': 'int',   'default': 200, 'min': 10, 'max': 500, 'label': 'Trend MA period'},
        'atr_period':     {'type': 'int',   'default': 14,  'min': 2,  'max': 100, 'label': 'ATR period'},
        'atr_mult':       {'type': 'float', 'default': 1.0,              'label': 'ATR multiplier'},
        'kc_length':      {'type': 'int',   'default': 20,  'min': 2,  'max': 200, 'label': 'Keltner channel length'},
        'kc_mult_inner':  {'type': 'float', 'default': 1.8,              'label': 'Keltner inner multiplier'},
        'kc_mult_outer':  {'type': 'float', 'default': 3.3,              'label': 'Keltner outer multiplier'},
        'bb_length':      {'type': 'int',   'default': 20,  'min': 2,  'max': 200, 'label': 'Bollinger Band length'},
        'bb_mult':        {'type': 'float', 'default': 2.0,              'label': 'Bollinger Band multiplier'},
        'risk_reward':    {'type': 'float', 'default': 2.0,              'label': 'Risk/Reward ratio'},
    }

    def __init__(
        self,
        df,
        symbol="",
        trend_period=200,
        atr_period=14,
        atr_mult=1.0,
        kc_length=20,
        kc_mult_inner=1.8,
        kc_mult_outer=3.3,
        bb_length=20,
        bb_mult=2.0,
        risk_reward=2.0
    ):

        self.trend_period = trend_period
        self.atr_period = atr_period
        self.atr_mult = atr_mult

        self.kc_length = kc_length
        self.kc_mult_inner = kc_mult_inner
        self.kc_mult_outer = kc_mult_outer

        self.bb_length = bb_length
        self.bb_mult = bb_mult
        self.risk_reward = risk_reward

        super().__init__(df=df, symbol=symbol)

        self.data()
        # self.add_fig()

    # --------------------------------------------------
    # Helper Functions
    # --------------------------------------------------

    def ema(self, series, length):
        return series.ewm(span=length, adjust=False).mean()

    def atr(self, length):

        high = self.df['High']
        low = self.df['Low']
        close = self.df['Close']

        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        return tr.rolling(length).mean()

    # --------------------------------------------------
    # DATA CALCULATION
    # --------------------------------------------------

    def data(self):

        import logging
        logger = logging.getLogger(__name__)

        try:
            rows_in = len(self.df) if self.df is not None else 0
            logger.debug("qtrend.data: starting with rows=%s for symbol=%s", rows_in, getattr(self, 'symbol', ''))
            # Always print a minimal diagnostic to stdout so it appears in Streamlit/terminal
        except Exception:
            pass

        if self.has_index(self.df):
            self.df.reset_index(inplace=True)
#        self.df['timestamp'] = pd.to_datetime(self.df['Date']).astype(int) // 10**9
        close = self.df['Close']

        # --------------------------------------------------
        # Q TREND
        # --------------------------------------------------

        highest = close.rolling(self.trend_period).max()
        lowest = close.rolling(self.trend_period).min()

        d = highest - lowest
        m = (highest + lowest) / 2

        atr = self.atr(self.atr_period).shift(1)
        epsilon = self.atr_mult * atr

        change_up = close > (m + epsilon)
        change_down = close < (m - epsilon)

        trend_state = np.where(change_up, 1,
                        np.where(change_down, -1, 0))

        trend_state = pd.Series(trend_state).replace(0, np.nan).ffill()

        self.df['qtrend_line'] = m
        self.df['qtrend_state'] = trend_state

        self.df['qtrend_buy'] = (trend_state == 1) & (trend_state.shift() != 1)
        self.df['qtrend_sell'] = (trend_state == -1) & (trend_state.shift() != -1)

        # --------------------------------------------------
        # KELTNER CHANNEL
        # --------------------------------------------------

        ema_center = self.ema(close, self.kc_length)
        atr_val = self.atr(self.kc_length)

        upper_inner = ema_center + self.kc_mult_inner * atr_val
        upper_outer = ema_center + self.kc_mult_outer * atr_val

        lower_inner = ema_center - self.kc_mult_inner * atr_val
        lower_outer = ema_center - self.kc_mult_outer * atr_val

        self.df['kc_mid'] = ema_center
        self.df['kc_upper_inner'] = upper_inner
        self.df['kc_upper_outer'] = upper_outer
        self.df['kc_lower_inner'] = lower_inner
        self.df['kc_lower_outer'] = lower_outer

        # --------------------------------------------------
        # BOLLINGER BANDS
        # --------------------------------------------------

        bb_basis = close.rolling(self.bb_length).mean()
        bb_dev = close.rolling(self.bb_length).std() * self.bb_mult

        bb_upper = bb_basis + bb_dev
        bb_lower = bb_basis - bb_dev

        self.df['bb_upper'] = bb_upper
        self.df['bb_lower'] = bb_lower

        # --------------------------------------------------
        # SQUEEZE CONDITION
        # --------------------------------------------------

        squeeze = (bb_upper - bb_lower) < (upper_inner - lower_inner)

        self.df['in_squeeze'] = squeeze

        # --------------------------------------------------
        # BREAKOUT
        # --------------------------------------------------

        breakout_up = (
            squeeze.shift(1) &
            (close > upper_inner) &
            (close.shift(1) <= upper_inner)
        )

        breakout_down = (
            squeeze.shift(1) &
            (close < lower_inner) &
            (close.shift(1) >= lower_inner)
        )

        self.df['squeeze_breakout_up'] = breakout_up
        self.df['squeeze_breakout_down'] = breakout_down

        # --------------------------------------------------
        # REBALANCE
        # --------------------------------------------------

        rebalance_up = (
            breakout_up.shift(1) &
            (close <= upper_inner)
        )

        rebalance_down = (
            breakout_down.shift(1) &
            (close >= lower_inner)
        )

        self.df['rebalance_up'] = rebalance_up
        self.df['rebalance_down'] = rebalance_down


        # -----------------------------
        # ENTRY CONDITIONS
        # -----------------------------

        long_entry = (
            (self.df["qtrend_state"] == 1)
            & (self.df["squeeze_breakout_up"])
        )

        short_entry = (
            (self.df["qtrend_state"] == -1)
            & (self.df["squeeze_breakout_down"])
        )

        self.df["long_entry"] = long_entry
        self.df["short_entry"] = short_entry

        # -----------------------------
        # POSITION STATE
        # -----------------------------

        position = 0
        entry_price = 0
        stop_loss = 0
        take_profit = 0

        positions = []
        entries = []
        sl_list = []
        tp_list = []

        try:
            for i in range(len(self.df)):

                close = self.df["Close"].iloc[i]

                if position == 0:

                    if long_entry.iloc[i]:

                        position = 1
                        entry_price = close
                        stop_loss = self.df["kc_lower_inner"].iloc[i]

                        risk = entry_price - stop_loss
                        take_profit = entry_price + risk * self.risk_reward

                    elif short_entry.iloc[i]:

                        position = -1
                        entry_price = close
                        stop_loss = self.df["kc_upper_inner"].iloc[i]

                        risk = stop_loss - entry_price
                        take_profit = entry_price - risk * self.risk_reward

                elif position == 1:

                    if close <= stop_loss or close >= take_profit:
                        position = 0

                elif position == -1:

                    if close >= stop_loss or close <= take_profit:
                        position = 0

                positions.append(position)
                entries.append(entry_price)
                sl_list.append(stop_loss)
                tp_list.append(take_profit)

        except Exception as e:
            print(f"Error in position calculation: {e}")

#        self.df["qtrend_position"] = positions
        self.df["qtrend_entry_price"] = entries
        self.df["qtrend_stop_loss"] = sl_list
        self.df["qtrend_take_profit"] = tp_list
        self.df['qtrend_take_profit'] = self.df['qtrend_take_profit'].replace(0, None)
        self.df['qtrend_stop_loss'] = self.df['qtrend_stop_loss'].replace(0, None)
        self.df['qtrend_entry_price'] = self.df['qtrend_entry_price'].replace(0, None)

        self.df.drop(['kc_mid','bb_upper','bb_lower','qtrend_buy','qtrend_sell','qtrend_state','in_squeeze','rebalance_up','rebalance_down'], axis=1, inplace=True)
        try:
            self.df.set_index('Date', inplace=True)
        except Exception:
            pass
        try:
            rows_out = len(self.df) if self.df is not None else 0
            logger.debug("qtrend.data: finished with rows=%s for symbol=%s", rows_out, getattr(self, 'symbol', ''))
        except Exception:
            pass

    # --------------------------------------------------
    # FIGURE
    # --------------------------------------------------

    def add_fig(self):

        self.fig = go.Figure()

        try:
            self.df.reset_index(inplace=True)
        except Exception:
            pass

        self.fig.add_trace(
            go.Scatter(
                x=self.df['Date'],
                y=self.df['qtrend_line'],
                mode="lines",
                showlegend=False,
                line=dict(color="orange"),
                name="QTrend"
            )
        )

        self.fig.add_trace(
            go.Scatter(
                x=self.df['Date'],
                y=self.df['kc_upper_inner'],
                mode="lines",
                showlegend=False,
                line=dict(color="red"),
                name="KC Upper Inner"
            )
        )

        self.fig.add_trace(
            go.Scatter(
                x=self.df['Date'],
                y=self.df['kc_lower_inner'],
                mode="lines",
                showlegend=False,
                line=dict(color="blue"),
                name="KC Lower Inner"
            )
        )

        self.fig.add_trace(
            go.Scatter(
                x=self.df['Date'],
                y=self.df['kc_upper_outer'],
                mode="lines",
                showlegend=False,
                line=dict(color="red",dash="dot"),
                name="KC Upper Outer"
            )
        )

        self.fig.add_trace(
            go.Scatter(
                x=self.df['Date'],
                y=self.df['kc_lower_outer'],
                mode="lines",
                showlegend=False,
                line=dict(color="blue",dash="dot"),
                name="KC Lower Outer"
            )
        )

        self.fig.add_trace(
            go.Scatter(
                x=self.df[self.df['squeeze_breakout_up']]['Date'],
                y=self.df[self.df['squeeze_breakout_up']]['Close'],
                mode="markers",
                showlegend=False,
                marker=dict(color="green", size=10),
                name="Breakout Up"
            )
        )

        self.fig.add_trace(
            go.Scatter(
                x=self.df[self.df['squeeze_breakout_down']]['Date'],
                y=self.df[self.df['squeeze_breakout_down']]['Close'],
                mode="markers",
                showlegend=False,
                marker=dict(color="red", size=10),
                name="Breakout Down"
            )
        )

        # --------------------------------------------------
        # LONG ENTRIES
        # --------------------------------------------------

        if "long_entry" in self.df:

            long_df = self.df[self.df["long_entry"]]

            self.fig.add_trace(
                go.Scatter(
                    x=long_df["Date"],
                    y=long_df["Close"],
                    mode="markers",
                    showlegend=False,
                    marker=dict(
                        color="darkgreen",
                        size=12,
                        symbol="triangle-up"
                    ),
                    name="LONG"
                )
            )

        # --------------------------------------------------
        # SHORT ENTRIES
        # --------------------------------------------------

        if "short_entry" in self.df:

            short_df = self.df[self.df["short_entry"]]

            self.fig.add_trace(
                go.Scatter(
                    x=short_df["Date"],
                    y=short_df["Close"],
                    mode="markers",
                    showlegend=False,
                    marker=dict(
                        color="darkred",
                        size=12,
                        symbol="triangle-down"
                    ),
                    name="SHORT"
                )
            )

        # --------------------------------------------------
        # STOP LOSS
        # --------------------------------------------------

        if "qtrend_stop_loss" in self.df:

            self.fig.add_trace(
                go.Scatter(
                    x=self.df["Date"],
                    y=self.df["qtrend_stop_loss"],
                    mode="lines",
                    showlegend=False,
                    line=dict(color="red", dash="dot"),
                    name="Stop Loss"
                )
            )

        # --------------------------------------------------
        # TAKE PROFIT
        # --------------------------------------------------

        if "qtrend_take_profit" in self.df:

            self.fig.add_trace(
                go.Scatter(
                    x=self.df["Date"],
                    y=self.df["qtrend_take_profit"],
                    mode="lines",
                    showlegend=False,
                    line=dict(color="green", dash="dot"),
                    name="Take Profit"
                )
            )

        if "qtrend_entry_price" in self.df:

            self.fig.add_trace(
                go.Scatter(
                    x=self.df["Date"],
                    y=self.df["qtrend_entry_price"],
                    mode="lines",
                    showlegend=False,
                    line=dict(color="blue", dash="dot"),
                    name="Entry Price"
                )
            )

