import sys
import pandas as pd
import plotly.graph_objects as go
import numpy as np

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator


class Relvol(_indicator._Indicator):
    """
    Cumulative Delta auf Range-Bars Basis (anstatt Zeit-Candles).
    Optional: Relative Volume Ratio Anzeige.
    """

    is_oszilator = True
    name = "Relative Volume Bars"

    params = {
        'relvol_length':              {'type': 'int',    'default': 21,        'min': 2, 'max': 200, 'label': 'Volume MA length'},
        'relvol_mode':                {'type': 'select', 'default': 'Regular', 'label': 'Volume mode', 'options': ['Regular', 'Cumulative']},
        'relvol_ratio':               {'type': 'float',  'default': 1.0,                 'label': 'Color threshold ratio'},
        'relvol_adjust_unconfirmed':  {'type': 'bool',   'default': True,                'label': 'Extrapolate open bars'},
        'color_bull':                 {'type': 'color',  'default': '',                  'label': 'Bull bar color'},
        'color_bear':                 {'type': 'color',  'default': '',                  'label': 'Bear bar color'},
    }

    def __init__(self, df, symbol="",
                 relvol_length=21,
                 relvol_mode = "Regular",
                 relvol_ratio = 1,
                 relvol_adjust_unconfirmed=True):
        """
        range_size: Preisbewegung pro Range-Bar
        relvol_mode: "cumulative" oder "regular"
        relvol_ratio: Ratios > relvol_ratio are colored green else red
        relvol_adjust_unconfirmed: Extrapolation offener Bars aktivieren
        """
        super().__init__(df=df, symbol=symbol)
        self.range_bars = None
        self.relvol_ratio = relvol_ratio
        self.relvol_mode = relvol_mode
        self.relvol_length = relvol_length
        self.relvol_adjust_unconfirmed = relvol_adjust_unconfirmed
        self.df = self.calculate_relative_volume(df)


    # ----------------------------------------------------------------------
    # RELATIVE VOLUME
    # ----------------------------------------------------------------------

    def calculate_relative_volume(self, df):
        """
        Simuliert Pine Script Logik von ta.relativeVolume():
        currentVolume / avg(pastVolume), mit optionaler Cumulative-Mode.
        """
        df = df.copy()
        length = self.relvol_length

        # Extrapolation unbestätigter Kerzen (optional)
        if self.relvol_adjust_unconfirmed and len(df) > 1:
            avg_prev = df['Volume'].iloc[:-1].rolling(length).mean()
            extrap_factor = df['Volume'].iloc[-2] / avg_prev.iloc[-2] if avg_prev.iloc[-2] != 0 else 1
            df['relvol_adj_vol'] = df['Volume']
            df['relvol_adj_vol'].iloc[-1] = df['Volume'].iloc[-1] * extrap_factor
        else:
            df['relvol_adj_vol'] = df['Volume']

        if self.relvol_mode.lower() == "cumulative":
            df['relvol_current'] = df['relvol_adj_vol'].cumsum()
            df['relvol_past'] = df['relvol_adj_vol'].rolling(length).sum().shift(1)
        else:  # Regular mode
            df['relvol_current'] = df['relvol_adj_vol']
            df['relvol_past'] = df['relvol_adj_vol'].rolling(length).mean().shift(1)

        df['relvol_ratio'] = df['relvol_current'] / df['relvol_past']
        df['relvol_ratio'].replace([np.inf, -np.inf], np.nan, inplace=True)
        df['relvol_ratio'].fillna(1, inplace=True)
        return df

    # ----------------------------------------------------------------------
    # FIGURE BUILDER
    # ----------------------------------------------------------------------

    def add_fig(self):
        self.fig = go.Figure()

        """
        Fügt Histogramm für Relative Volume Ratio hinzu (wie Pine plot.style_columns).
        """
        if "relvol_ratio" not in self.df.columns:
            return
        df = self.df.copy()
        
        # Erstellen der Farbliste basierend auf der Bedingung
        color = ['red' if self.df['Close'][i] < self.df['Open'][i] else 'green' for i in range(len(self.df))]
#        color = ['black' if self.df['RelVol_Ratio'][i] >= self.relvol_ratio or self.df['RelVol_Ratio'][i] == 0 else 'grey' for i in range(len(self.df))]
        df['bar_size'] = self.df['relvol_ratio'] - self.relvol_ratio 
        df['bar_size'] = np.where((self.df['Volume']==0),0,df['bar_size'])
        df['base'] = self.relvol_ratio

        if not "Date" in df.columns:
            df.reset_index(inplace=True)

        self.fig.add_trace(go.Bar(
            x=df["Date"],
            y=df["bar_size"],
            base = df['base'],
            marker_color=color,
            name="Relative Volume Ratio",
            showlegend=False,
            opacity=0.6,
        ))
        self.fig.add_hline(y=self.relvol_ratio, line=dict(color="darkgreen", width=2, dash="dot"))

        self.fig.add_hline(y=0, line=dict(color="black", width=1, dash="dot"))
        return self.fig
