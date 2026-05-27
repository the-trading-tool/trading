import pandas as pd
import numpy as np
import plotly.graph_objects as go
import sys

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import (_indicator, indicator)


class Renko(_indicator._Indicator):

    name = "Renko Candles"
    is_oszilator = False

    params = {
        'mode':       {'type': 'select', 'default': 'ATR/2',  'label': 'Brick size mode',   'options': ['ATR/2', 'ATR', 'percentage', 'fixed']},
        'atr_period': {'type': 'int',    'default': 14,        'min': 2, 'max': 100, 'label': 'ATR period'},
        'brick_size': {'type': 'float',  'default': 10.0,                'label': 'Fixed brick size'},
        'percentage': {'type': 'float',  'default': 0.1,                 'label': 'Percentage brick size'},
        'num_bricks': {'type': 'int',    'default': 252,       'min': 10, 'max': 1000, 'label': 'Max bricks to display'},
    }

    def __init__(self, df, symbol="", mode='ATR/2', atr_period=14, brick_size=10.0,
                 percentage=0.1, num_bricks=252,
                 color_up="#5b9cf6", color_down="#0c3299",
                 unconfirmed_color="gray"):
        super().__init__(df=df, symbol=symbol)
        self.mode = mode
        self.atr_period = atr_period
        self.brick_size = brick_size
        self.percentage = percentage
        self.num_bricks = num_bricks
        self.color_up = color_up
        self.color_down = color_down
        self.unconfirmed_color = unconfirmed_color

        self.df_renko = self._calculate_renko(df)
#        self.add_fig()

    # -----------------------------------------------
    def _atr(self, df, period=14):
        high_low = df["High"] - df["Low"]
        high_close = np.abs(df["High"] - df["Close"].shift())
        low_close = np.abs(df["Low"] - df["Close"].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.rolling(period).mean()

    # -----------------------------------------------
    def _get_brick_size(self, df):
        if self.mode == "ATR":
            return self._atr(df, self.atr_period).iloc[-1]
        elif self.mode == "ATR/2":
            return self._atr(df, self.atr_period).iloc[-1] / 2
        elif self.mode == "ATR/4":
            return self._atr(df, self.atr_period).iloc[-1] / 4
        elif self.mode == "Percentage":
            return df["Close"].iloc[-1] * (self.percentage / 100)
        else:
            return self.brick_size

    # -----------------------------------------------
    def _calculate_renko(self, df):
        df = df.copy()
        df["Date"] = df.index if df.index.name else df["Date"] if "Date" in df.columns else df.index

        box_size = self._get_brick_size(df)
        closes = df["Close"].values
        dates = df["Date"].values

        bricks, trends, brick_dates = [], [], []

        last_brick_close = np.floor(closes[0] / box_size) * box_size
        trend = 0

        for price, date in zip(closes, dates):
            diff = price - last_brick_close
            num_bricks = int(abs(diff) / box_size)
            if num_bricks > 0:
                direction = 1 if diff > 0 else -1
                if trend != 0 and direction != trend and num_bricks >= 2:
                    direction = -trend
                    num_bricks -= 1

                for _ in range(num_bricks):
                    last_brick_close += direction * box_size
                    bricks.append(last_brick_close)
                    trends.append(direction)
                    brick_dates.append(date)
                trend = direction

        renko_df = pd.DataFrame({
            "Renko_Date": brick_dates,
            "Renko_Brick_Close": bricks,
            "Renko_Trend": trends
        })
        renko_df["Renko_Brick_Open"] = renko_df["Renko_Brick_Close"].shift(1)
        renko_df["Renko_Brick_Open"].fillna(renko_df["Renko_Brick_Close"], inplace=True)

        renko_df["Renko_High"] = renko_df[["Renko_Brick_Open", "Renko_Brick_Close"]].max(axis=1)
        renko_df["Renko_Low"] = renko_df[["Renko_Brick_Open", "Renko_Brick_Close"]].min(axis=1)
        renko_df["Renko_Color"] = np.where(renko_df["Renko_Trend"] == 1, self.color_up, self.color_down)

        # Date als Index behalten
        renko_df.set_index("Renko_Date", inplace=True)
        return renko_df #.tail(self.num_bricks)

    # -----------------------------------------------
    def add_fig(self):
        self.fig = go.Figure()
        df = self.df_renko.copy()

        self.fig.add_trace(go.Candlestick(
            x=df.index,
            open=df["Renko_Brick_Open"],
            high=df["Renko_High"],
            low=df["Renko_Low"],
            close=df["Renko_Brick_Close"],
            increasing_line_color=self.color_up,
            decreasing_line_color=self.color_down,
            name="Renko Overlay",
            showlegend=False,
            opacity=0.7
        ))

#        self.fig.update_layout(
#            title=f"Renko Overlay ({self.mode})",
#            xaxis_title="Date",
#            yaxis_title="Price",
#            template="plotly_dark",
#            xaxis_rangeslider_visible=False
#        )

#    def show(self):
#        self.fig.show()
