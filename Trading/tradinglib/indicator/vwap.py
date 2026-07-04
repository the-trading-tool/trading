import sys
import pandas as pd
import numpy as np
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

class Vwap(_indicator._Indicator):

    is_oszilator = False
    name = 'Volume Weighted Average Price'

    params = {
        'timeframe':     {'type': 'select', 'default': 'M',   'label': 'VWAP timeframe', 'options': ['D', 'W', 'M']},
        'show_prevwap':  {'type': 'bool',   'default': True,  'label': 'Show previous VWAP'},
        'show_bcolors':  {'type': 'bool',   'default': False, 'label': 'Color bars by VWAP position'},
        'color_vwap':    {'type': 'color',  'default': '',    'label': 'VWAP line color'},
        'color_prevwap': {'type': 'color',  'default': '',    'label': 'Prev VWAP color'},
    }

    def __init__(self, df, symbol="", timeframe = "M", show_prevwap=True, show_bcolors=False,
                 color_vwap='', color_prevwap=''):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.timeframe = timeframe
        self.show_prevwap = show_prevwap
        self.show_bcolors = show_bcolors
        self.color_vwap    = color_vwap    or 'blue'
        self.color_prevwap = color_prevwap or 'orange'
        self.data()

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""
        self.df1 = self.df.copy()

        try:
            # typischer Preis = hl2
#            self.df1["hl2"] = (self.df["High"] + self.df["Low"]) / 2
            self.df1["hl2"] = (self.df["High"] + self.df["Low"] + self.df["Close"]) / 3

            # Gruppierung nach Timeframe (z.B. W = Weekly VWAP Reset)
            self.df1["Period"] = self.df["Date"].dt.to_period(self.timeframe)

            # Cumulative Berechnungen pro Periode
            self.df1["TPV"] = self.df1["hl2"] * self.df["Volume"]
            self.df1["V2"] = self.df1["hl2"] ** 2 * self.df["Volume"]

            self.df1["Cum_TPV"] = self.df1.groupby("Period")["TPV"].cumsum()
            self.df1["Cum_Volume"] = self.df1.groupby("Period")["Volume"].cumsum()
            self.df1["Cum_V2"] = self.df1.groupby("Period")["V2"].cumsum()

            # VWAP
            self.df1["VWAP"] = self.df1["Cum_TPV"] / self.df1["Cum_Volume"]

            # Standardabweichung (Deviation)
            self.df1["Variance"] = self.df1["Cum_V2"] / self.df1["Cum_Volume"] - self.df1["VWAP"] ** 2
            self.df1["Deviation"] = np.sqrt(self.df1["Variance"].clip(lower=0))

            # Previous VWAP (wie im Pine Script: "prevwap")
            self.df1["PrevVWAP"] = self.df1.groupby("Period")["VWAP"].shift(1)

            # Coloring: grün wenn Close > VWAP, sonst rot
            self.df1["Color"] = np.where(self.df["Close"] > self.df1["VWAP"], "green", "red")
            self.df['VWAP'] = self.df1['VWAP'] 

        except Exception:
            pass
        
    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        try:
            self.df1 = self.df1.reset_index()
        except Exception:
            pass

        try:
            # VWAP als Linie
            self.fig.add_trace(go.Scatter(
                x=self.df['Date'],
                y=self.df['VWAP'],
                mode='lines',
                name='VWAP',
		        showlegend = False,
                line=dict(color=self.color_vwap, width=2, dash='dot')
            ))
        except Exception:
            pass

        if self.show_prevwap:
            try:
                self.fig.add_trace(go.Scatter(
                    x=self.df1["Date"],
                    y=self.df1["PrevVWAP"],
                    mode="lines",
                    name="Prev VWAP",
		            showlegend = False,
                    line=dict(color=self.color_prevwap, width=1, dash="dash")
                ))
            except Exception:
                pass

        # Optional: Bar Coloring
        if self.show_bcolors:
            try:
                self.fig.add_trace(go.Bar(
                    x=self.df1["Date"],
                    y=self.df1["Close"],
                    marker_color=self.df1["Color"],
		            showlegend = False,
                    name="Bars"
                ))
            except Exception:
                pass