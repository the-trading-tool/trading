import sys
import pandas as pd
import plotly.graph_objects as go
import numpy as np

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator


class Cumd(_indicator._Indicator):
    """
    Cumulative Delta auf Range-Bars Basis (anstatt Zeit-Candles).
    Mit Reset je nach Einstellung (daily/monthly/auto/None).
    """

    is_oszilator = True
    name = "Cumulative Delta RangeBars"

    params = {
        'range_size':       {'type': 'float',  'default': 0.01,      'label': 'Range bar size'},
        'center_cumdelta':  {'type': 'bool',   'default': True,      'label': 'Center cumulative delta'},
        'reset_mode':       {'type': 'select', 'default': 'monthly', 'label': 'Reset mode', 'options': ['auto', 'daily', 'monthly', 'none']},
        'impulse_factor':   {'type': 'int',    'default': 7,  'min': 1, 'max': 20, 'label': 'Impulse threshold factor'},
        'lookback':         {'type': 'int',    'default': 3,  'min': 1, 'max': 50, 'label': 'Lookback bars'},
        'color_bull':       {'type': 'color',  'default': '',        'label': 'Bull candle color'},
        'color_bear':       {'type': 'color',  'default': '',        'label': 'Bear candle color'},
    }

    def __init__(self, df, symbol="", range_size=0.01, center_cumdelta=True,
                 reset_mode="monthly", impulse_factor=7, lookback=3):
        """
        range_size: Preisbewegung pro Range-Bar in derselben Einheit wie Close (z.B. 0.01)
        reset_mode: "auto" | "daily" | "monthly" | None
        impulse_factor: Schwellenfaktor für Bullish/Bearish Impulse (z.B. 2, 3, 5)
        lookback: Anzahl der letzten Kerzen für Vergleich
        """
        super().__init__(df=df, symbol=symbol)
        self.range_size = range_size
        self.range_bars = None
        self.reset_mode = reset_mode
        self.impulse_factor = impulse_factor
        self.lookback = lookback

        self.df = self.calculate_cumulative_delta(df)
        if center_cumdelta:
            self.center_cumdelta()
        if self.range_bars is None:
            self.build_range_bars()

    def calculate_cumulative_delta(self, df):
        df = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df['Date']):
            df['Date'] = pd.to_datetime(df['Date'])
        df = df.sort_values("Date")

        delta = []
        for i in range(len(df)):
            if df['High'].iloc[i] == df['Low'].iloc[i]:
                d = 0
            else:
                d = df['Volume'].iloc[i] * (df['Close'].iloc[i] - df['Open'].iloc[i]) / (df['High'].iloc[i] - df['Low'].iloc[i])
            delta.append(d)
        df['cumd_delta'] = delta

        mode = self.reset_mode
        if mode == "auto":
            freq = pd.infer_freq(df['Date'])
            if freq is None:
                freq = (df['Date'].iloc[1] - df['Date'].iloc[0])
            if (isinstance(freq, str) and "D" in freq) or (not isinstance(freq, str) and freq >= pd.Timedelta("1D")):
                mode = "monthly"
            else:
                mode = "daily"

        if mode == "daily":
            grouper = df['Date'].dt.to_period("D")
            df['cumd_cum_delta'] = df.groupby(grouper)['cumd_delta'].cumsum()
        elif mode == "monthly":
            grouper = df['Date'].dt.to_period("M")
            df['cumd_cum_delta'] = df.groupby(grouper)['cumd_delta'].cumsum()
        else:
            df['cumd_cum_delta'] = np.cumsum(df['cumd_delta'])

        return df

    def center_cumdelta(self):
        if 'CumDelta' not in self.df.columns:
            return
        min_val = self.df['cumd_cum_delta'].min()
        max_val = self.df['cumd_cum_delta'].max()
        center = (max_val + min_val) / 2
        self.df['cumd_cum_delta'] = self.df['cumd_cum_delta'] - center

    def build_range_bars(self):
        bars = []
        if 'Close' not in self.df.columns:
            raise ValueError("DataFrame muss 'Close' Spalte enthalten")

        open_price = self.df['cumd_cum_delta'].iloc[0]
        high_price = open_price
        low_price = open_price
        close_price = open_price

        for idx, row in self.df.iterrows():
            price = row['cumd_cum_delta']
            high_price = max(high_price, price)
            low_price = min(low_price, price)

            if high_price - low_price >= self.range_size:
                close_price = price
                bars.append({
                    'Date': row['Date'],
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price
                })
                open_price = close_price
                high_price = close_price
                low_price = close_price

        self.range_bars = pd.DataFrame(bars)
        return self.range_bars

    def add_rangebar_candles(self):

        self.fig.add_trace(go.Candlestick(
            x=self.range_bars['Date'],
            open=self.range_bars['open'],
            high=self.range_bars['high'],
            low=self.range_bars['low'],
            close=self.range_bars['close'],
            increasing_line_color="limegreen",
            decreasing_line_color="crimson",
            showlegend=False,
            opacity=0.8,
            name=f"CumDelta RangeBars ({self.range_size})"
        ))

    def add_cvd_candles(self):
        """
        Zeichnet die Cumulative Volume Delta Candles ähnlich zum Pine Script.
        """
        if "cumd_delta" not in self.df.columns:
            self.df = self.calculate_cumulative_delta(self.df)

        # Hier: Pine Script berechnet open/max/min/last basierend auf Delta-Volumen
        # Wir simulieren das mit CumDelta als "Price"
        cvd_bars = []
        cum_vol = 0

        for idx, row in self.df.iterrows():
            cum_vol += row["Volume"] if not np.isnan(row["Volume"]) else 0

            open_vol = row["cumd_cum_delta"] - row["cumd_delta"]
            max_vol = max(row["cumd_cum_delta"], open_vol)
            min_vol = min(row["cumd_cum_delta"], open_vol)
            last_vol = row["cumd_cum_delta"]

            cvd_bars.append({
                "Date": row["Date"],
                "open": open_vol,
                "high": max_vol,
                "low": min_vol,
                "close": last_vol
            })

        cvd_df = pd.DataFrame(cvd_bars)

        # Farbe: bullish = grün, bearish = rot
        self.fig.add_trace(go.Candlestick(
            x=cvd_df["Date"],
            open=cvd_df["open"],
            high=cvd_df["high"],
            low=cvd_df["low"],
            close=cvd_df["close"],
            increasing_line_color="teal",
            decreasing_line_color="crimson",
            showlegend=False,
            name="CVD Candles"
        ))

        # horizontale Linie bei 0 (wie im Pine Script hline(0))
        self.fig.add_hline(y=0, line=dict(color="black", width=1, dash="dot"))

        return cvd_df

    def add_impulse_markers(self):
        if self.impulse_factor is None or self.range_bars is None:
            return

        markers_x = []
        markers_y = []
        colors = []
        symbols = []

        for i in range(self.lookback, len(self.range_bars)):
            curr = self.range_bars.iloc[i]
            body = abs(curr['close'] - curr['open'])
            bullish = curr['close'] > curr['open']

            # nur gleichfarbige Kerzen im Lookback berücksichtigen
            prev_bodies = []
            for j in range(i - self.lookback, i):
                prev = self.range_bars.iloc[j]
                if bullish and prev['close'] > prev['open']:
                    prev_bodies.append(abs(prev['close'] - prev['open']))
                elif not bullish and prev['close'] < prev['open']:
                    prev_bodies.append(abs(prev['close'] - prev['open']))

            if prev_bodies:
                avg_prev = np.mean(prev_bodies)
                if avg_prev > 0 and body > self.impulse_factor * avg_prev:
                    markers_x.append(curr['Date'])
                    if bullish:
                        markers_y.append(curr['high'])
                        colors.append("green")
                        symbols.append("triangle-up")
                    else:
                        markers_y.append(curr['low'])
                        colors.append("red")
                        symbols.append("triangle-down")

        self.fig.add_trace(go.Scatter(
            x=markers_x,
            y=markers_y,
            mode="markers",
            marker=dict(color=colors, size=12, symbol=symbols),
            name="Impulse Marker",
            showlegend=False
        ))

    
    def add_cvd_markers(self, cvd_df, impulse_factor=3, trend_change_only=True, 
                        bull_symbol="star", bear_symbol="x", bull_color="limegreen", bear_color="crimson"):
        """
        Fügt Marker auf den CVD-Candles hinzu.
        
        impulse_factor: wie viel größer das Delta sein muss als vorherige Candle
        trend_change_only: True = Marker nur bei Trendwechsel (bullish -> bearish oder umgekehrt)
        bull_symbol/bear_symbol: Marker-Symbole für bull/bear Impulse
        bull_color/bear_color: Marker-Farben für bull/bear Impulse
        """
        markers_x = []
        markers_y = []
        colors = []
        symbols = []

        cvd_df["body"] = cvd_df["close"] - cvd_df["open"]

        for i in range(1, len(cvd_df)):
            curr = cvd_df.iloc[i]
            prev = cvd_df.iloc[i - 1]

            body = curr["body"]
            bullish = body > 0
            prev_bullish = prev["body"] > 0

            # Bedingung für Trendwechsel
            if trend_change_only and (bullish == prev_bullish):
                continue

            # Impulsbedingung
            prev_body = abs(prev["body"])
            if prev_body > 0 and abs(body) > impulse_factor * prev_body:
                markers_x.append(curr["Date"])
                if bullish:
                    markers_y.append(curr["high"])
                    colors.append(bull_color)
                    symbols.append(bull_symbol)
                else:
                    markers_y.append(curr["low"])
                    colors.append(bear_color)
                    symbols.append(bear_symbol)

        self.fig.add_trace(go.Scatter(
            x=markers_x,
            y=markers_y,
            mode="markers",
            marker=dict(color=colors, size=14, symbol=symbols),
            name="CVD Impulse Marker",
            showlegend=False
        ))


    def add_fig(self):
        self.fig = go.Figure()
        self.add_cvd_candles()
        self.add_impulse_markers()
#        cvd_df = self.add_cvd_candles()
        # nur Trendwechsel, mit Custom Symbolen
#        self.add_cvd_markers(
#            cvd_df, 
#            impulse_factor=7, 
#            trend_change_only=True, 
#            bull_symbol="star", 
#            bear_symbol="x", 
#            bull_color="limegreen", 
#            bear_color="orange"
#        )               
        self.fig.add_hline(y=0, line=dict(color="black", width=1, dash="dot"))
        return self.fig
