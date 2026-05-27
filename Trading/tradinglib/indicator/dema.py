import sys
import pandas as pd
import numpy as np
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib.indicators")
except ImportError:
    print('No Import')

from tradinglib.indicator import _indicator
from tradinglib.indicator import indicator

class Dema(_indicator._Indicator):

    is_oszilator = True
    name = 'DeMarker RSI Indicator'

    params = {
        'fast_length': {'type': 'int',   'default': 8,   'min': 2, 'max': 100, 'label': 'Fast EMA length'},
        'slow_length': {'type': 'int',   'default': 21,  'min': 2, 'max': 200, 'label': 'Slow EMA length'},
        'window':      {'type': 'int',   'default': 14,  'min': 2, 'max': 100, 'label': 'RSI window'},
        'show_cross':  {'type': 'bool',  'default': True,           'label': 'Show crossover signals'},
        'color_slow':  {'type': 'color', 'default': '',             'label': 'Slow EMA color'},
        'color_fast':  {'type': 'color', 'default': '',             'label': 'Fast EMA color'},
    }

    def __init__(self, df, symbol="", fast_length=8, slow_length=21, show_cross=True, window=14):
        self.fast_length = fast_length
        self.slow_length = slow_length
        self.show_cross = show_cross
        self.window = window
        super().__init__(df=df, symbol=symbol)
        self.data()
        # self.add_fig()  # Optional

    def data(self):
        df = self.df.copy()

        # ---------- HaClose optional ----------
        # Wenn du die HA-Close (wie im Pine) willst, kannst du stattdessen:
        # df['ha_close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
        # price = df['ha_close']
        price = df['Close']

        # ---------- RSI Berechnung (mit Index-Aligment) ----------
        delta = price.diff()
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)

        # Wichtig: Series mit gleichem Index erzeugen, damit späteres Assign funktioniert
        gain_s = pd.Series(gain, index=df.index)
        loss_s = pd.Series(loss, index=df.index)

        # EWM (wie in deinem ursprünglichen RSI)
        avg_gain = gain_s.ewm(com=self.window - 1, adjust=False).mean()
        avg_loss = loss_s.ewm(com=self.window - 1, adjust=False).mean()

        # Schutz gegen Division durch 0 / inf
        avg_loss = avg_loss.replace(0, np.nan)  # avoid inf
        rs = avg_gain / avg_loss
        rs = rs.replace([np.inf, -np.inf], np.nan)

        rsi = 100 - (100 / (1 + rs))

        # Falls du initial NaNs sinnvoll füllen willst (optional):
        # z.B. rsi = rsi.fillna(method='bfill').fillna(50)
        # Ich lasse es erstmal unverändert, aber wir setzen es mit korrektem Index ins df:
        df['dema_rsi'] = rsi

        # ---------- Doppelte EMA Glättungen ----------
        df['dema_ema_fast_1'] = df['dema_rsi'].ewm(span=self.fast_length, adjust=False).mean()
        df['dema_ema_fast']   = 2 * df['dema_ema_fast_1'] - df['dema_ema_fast_1'].ewm(span=self.fast_length, adjust=False).mean()

        df['dema_ema_slow_1'] = df['dema_rsi'].ewm(span=self.slow_length, adjust=False).mean()
        df['dema_ema_slow']   = 2 * df['dema_ema_slow_1'] - df['dema_ema_slow_1'].ewm(span=self.slow_length, adjust=False).mean()

        # ---------- Zusätzliche geglättete RSI Linie (v3) ----------
        df['v2'] = df['dema_rsi'].ewm(span=self.window, adjust=False).mean()
        df['dema_mid'] = 2 * df['v2'] - df['v2'].ewm(span=self.window, adjust=False).mean()

        # ---------- Trendfarbe ----------
        df['trend_color'] = np.where(df['dema_ema_fast'] > df['dema_ema_slow'], 'green', 'red')

        # ---------- Crossovers ----------
        df['dema_buy']  = np.where(((df['dema_ema_fast'] > df['dema_ema_slow']) & (df['dema_ema_fast'].shift(1) <= df['dema_ema_slow'].shift(1))),df['dema_ema_fast'],np.nan)
        df['dema_sell'] = np.where(((df['dema_ema_fast'] < df['dema_ema_slow']) & (df['dema_ema_fast'].shift(1) >= df['dema_ema_slow'].shift(1))),df['dema_ema_fast'],np.nan)

        self.df = df
        self.df.drop(['dema_ema_fast_1','dema_ema_slow_1','v2'], axis=1, inplace=True)

#        self.df['dema_buy_signal'] = df['dema_buy_signal']
#        self.df['dema_sell_signal'] = df['dema_sell_signal']
        
    def add_fig(self):
        df = self.df.copy()
        self.fig = go.Figure()

        if not 'Date' in df.columns:
            df.reset_index(inplace=True)

        # ---------- Plot Slow & Fast ----------
        self.fig.add_trace(go.Scatter(
            x=df['Date'], y=df['dema_ema_slow'],
            showlegend=False,
            name='EMA Slow', line=dict(color='green', width=2)
        ))

        self.fig.add_trace(go.Scatter(
            x=df['Date'], y=df['dema_ema_fast'],
            showlegend=False,
            name='EMA Fast', line=dict(color='red', width=2)
        ))

        # ---------- Schwarze Mittellinie (v3) ----------
        self.fig.add_trace(go.Scatter(
            x=df['Date'], y=df['dema_mid'],
            showlegend=False,
            name='dema_mid line', line=dict(color='black', width=0.5)
        ))

        # ---------- Dynamische Hintergrundfarbe basierend auf Trend ----------
        trend_changes = df['trend_color'].ne(df['trend_color'].shift()).cumsum()

        for _, seg in df.groupby(trend_changes):
            color = 'rgba(0,255,0,0.15)' if seg['trend_color'].iloc[0] == 'green' else 'rgba(255,0,0,0.15)'
            
            self.fig.add_trace(go.Scatter(
                x=pd.concat([seg['Date'], seg['Date'][::-1]]),
                y=pd.concat([seg['dema_ema_fast'], seg['dema_ema_slow'][::-1]]),
                fill='toself',
                fillcolor=color,
                line=dict(color='rgba(0,0,0,0)'),
                hoverinfo='skip',
                showlegend=False,
                name=f"Trend {seg['trend_color'].iloc[0]}"
            ))

        # ---------- Cross Signals ----------
        if self.show_cross:

            self.fig.add_trace(go.Scatter(
                x=df['Date'], y=df['dema_buy'],
                mode='markers+text',
                text=['B'] * len(self.df),
                textposition='bottom center',
                marker=dict(color='green', size=10),
                showlegend=False,
                name='Buy'
            ))

            self.fig.add_trace(go.Scatter(
                x=df['Date'], y=df['dema_sell'],
                mode='markers+text',
                text=['S'] * len(df),
                textposition='top center',
                marker=dict(color='red', size=10),
                showlegend=False,
                name='Sell'
            ))

        for p in [70,50,30]:
            self.fig.add_hline(
                        y=p,
                        line_width=2,
                        name = '',
                        line_dash="dot",
                        line_color='grey',
                        )

        for p in [100,0]:
            self.fig.add_hline(
                        y=p,
                        line_width=0.5,
                        name = '',
                        line_color='grey',
                        )


#        self.fig.update_layout(
#            title="DEMARSI Indicator",
#            yaxis_title="RSI Value",
#            template="plotly_dark"
#        )
