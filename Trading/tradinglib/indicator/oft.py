import ta
import sys
import plotly.graph_objects as go
import numpy as np
import streamlit as st 

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    print('No Import')

from tradinglib.indicator import _indicator

class Oft(_indicator._Indicator):

    is_oszilator = False
    name = 'Order Flow Tracker'

    params = {
        'period':       {'type': 'int',    'default': 21,     'min': 2,  'max': 200, 'label': 'OFT period'},
        'ob_periods':   {'type': 'int',    'default': 3,      'min': 1,  'max': 20,  'label': 'Order block periods'},
        'ob_threshold': {'type': 'float',  'default': 0.0,               'label': 'Order block threshold'},
        'use_wicks':    {'type': 'bool',   'default': False,              'label': 'Include wicks'},
        'ob_extend':    {'type': 'select', 'default': 'right', 'label': 'Extend direction', 'options': ['right', 'left', 'none']},
    }

    def __init__(self, df, symbol="", period=21, ob_periods=3, ob_threshold=0.0, use_wicks=False, ob_extend="right"):
        super().__init__(df=df, symbol=symbol)
        self.period = period
        self.ob_periods = ob_periods
        self.ob_threshold = ob_threshold
        self.ob_extend = ob_extend
        self.use_wicks = use_wicks
        self.data()
#        self.add_fig()

    def data(self):

        self.df.ffill(inplace=True)

        # 1. Order Flow Volumen
        self.df['oft_price_change'] = self.df['Close'].diff()
        self.df['oft_buy_vol'] = self.df['Volume'].where(self.df['oft_price_change'] > 0, 0)
        self.df['oft_sell_vol'] = self.df['Volume'].where(self.df['oft_price_change'] < 0, 0)

        # 2. Rolling Order Flow Imbalance
        self.df['oft_flow_imbalance'] = (self.df['oft_buy_vol'] - self.df['oft_sell_vol']).rolling(self.period).sum()

        # Optional: Glättung
        self.df['oft_flow_smoothed'] = self.df['oft_flow_imbalance'].ewm(span=5).mean()

        # 3. Support/Resistance
        def find_levels(window=21, min_distance=0.5):
            support = self.df['Close'].rolling(window).min().dropna().drop_duplicates()
            resistance = self.df['Close'].rolling(window).max().dropna().drop_duplicates()

            def filter_levels(levels, min_distance):
                filtered = []
                for level in sorted(levels):
                    if not filtered or abs(level - filtered[-1]) >= min_distance:
                        filtered.append(level)
                return filtered

            support = filter_levels(support.tolist(), min_distance)
            resistance = filter_levels(resistance.tolist(), min_distance)
            return support, resistance

        support_levels, resistance_levels = find_levels(window=21, min_distance=0.5)

        # 4. Signal-Logik (vectorisiert)
        price_range_pct = 0.01
        self.df['oft_buy'] = False
        self.df['oft_sell'] = False

        for s in support_levels:
            mask = (
                (self.df['Close'] >= s * (1 - price_range_pct)) &
                (self.df['Close'] <= s * (1 + price_range_pct)) &
                (self.df['oft_flow_imbalance'] < 0)  # Kontra-Trend Logik
            )
            self.df.loc[mask, 'oft_buy'] = self.df['Close']

        for r in resistance_levels:
            mask = (
                (self.df['Close'] >= r * (1 - price_range_pct)) &
                (self.df['Close'] <= r * (1 + price_range_pct)) &
                (self.df['oft_flow_imbalance'] > 0)  # Kontra-Trend Logik
            )
            self.df.loc[mask, 'oft_sell'] = self.df['Close']

        try:
            # === 5. Order Block Detection ===
            self.df['oft_ob_bull'] = np.nan
            self.df['oft_ob_bear'] = np.nan

            for i in range(self.ob_periods, len(self.df) - self.ob_periods):
                ob_candle = i - self.ob_periods
                close_ob = self.df['Close'].iloc[ob_candle]
                open_ob = self.df['Open'].iloc[ob_candle]
                close_seq_end = self.df['Close'].iloc[i]

                absmove = abs((close_seq_end - close_ob) / close_ob) * 100
                if absmove < self.ob_threshold:
                    continue

                # === Bullish OB ===
                if close_ob < open_ob:  # rote Kerze
                    up_seq = all(
                        self.df['Close'].iloc[ob_candle + j] > self.df['Open'].iloc[ob_candle + j]
                        for j in range(1, self.ob_periods + 1)
                    )
                    if up_seq:
                        high = self.df['High'].iloc[ob_candle] if self.use_wicks else self.df['Open'].iloc[ob_candle]
                        low = self.df['Low'].iloc[ob_candle]
                        self.df.at[self.df.index[ob_candle], 'oft_ob_bull'] = (high + low) / 2

                # === Bearish OB ===
                if close_ob > open_ob:  # grüne Kerze
                    down_seq = all(
                        self.df['Close'].iloc[ob_candle + j] < self.df['Open'].iloc[ob_candle + j]
                        for j in range(1, self.ob_periods + 1)
                    )
                    if down_seq:
                        high = self.df['High'].iloc[ob_candle]
                        low = self.df['Low'].iloc[ob_candle] if self.use_wicks else self.df['Open'].iloc[ob_candle]
                        self.df.at[self.df.index[ob_candle], 'oft_ob_bear'] = (high + low) / 2
        except Exception as e:
            st.write(e)
            pass

    def add_fig(self):
        self.fig = go.Figure()
        try:
            self.df.reset_index(inplace=True)
        except Exception:
            pass

        try:
            self.fig.add_trace(
                go.Scatter(
                    x=self.df['Date'],
                    y=self.df['oft_buy'],
                    line=dict(color='darkcyan', width=5),
                    showlegend=False,
                    name='Buy',
                    opacity=1
                )
            )
        except Exception:
            pass

        try:
            self.fig.add_trace(
                go.Scatter(
                    x=self.df['Date'],
                    y=self.df['oft_sell'],
                    line=dict(color='darkred', width=5),
                    showlegend=False,
                    name='Sell',
                    opacity=1
                )
            )
        except Exception:
            pass


        try:
            # === Plot Bullish OBs ===
            self.fig.add_trace(
                go.Scatter(
                    x=self.df['Date'],
                    y=self.df['oft_ob_bull'],
                    mode="markers",
                    marker=dict(color='green', size=10, symbol="x"),
#                    line=dict(color='green', width=10),
                    showlegend=False,
                    name='oft_ob_bull'
                )
            )

            # === Plot Bearish OBs ===
            self.fig.add_trace(
                go.Scatter(
                    x=self.df['Date'],
                    y=self.df['oft_ob_bear'],
                    mode="markers",
                    marker=dict(color='red', size=10, symbol="x"),
#                    line=dict(color='green', width=10),
                    showlegend=False,
                    name='oft_ob_bear'
                )
            )
    
            # Helper für X-Länge (z.B. 10 Balken entspricht etwa 20 Pips visuell)
            bar_limit = self.ob_periods * 2  # anpassbar: wie viele Kerzen breit soll die Linie gehen?

            # === Letzter Bullish OB ===
            bullish_obs = self.df['oft_ob_bull'].dropna()
            if not bullish_obs.empty:
                for i in range(0,len(bullish_obs.index)):
                    last_idx = bullish_obs.index[i]
                    last_val = bullish_obs.iloc[i]

                    # Zeitachse begrenzen auf bar_limit
                    end_idx = min(last_idx + bar_limit, len(self.df) - 1)
                    x_vals = [self.df['Date'].iloc[last_idx], self.df['Date'].iloc[end_idx]]

                    # Optional: vertikaler Preisbereich ±20 Pips (visuell hilfreich)
                    y_vals = [last_val, last_val]

                    self.fig.add_trace(
                        go.Scatter(
                            x=x_vals,
                            y=y_vals,
                            mode="lines",
                            line=dict(color="green", width=2, dash="dash"),
                            showlegend=False,
                            name="Bullish OB"
                        )
                    )

            # === Letzter Bearish OB ===
            bearish_obs = self.df['oft_ob_bear'].dropna()
            if not bearish_obs.empty:
                for i in range(0,len(bearish_obs.index)):
                    last_idx = bearish_obs.index[i]
                    last_val = bearish_obs.iloc[i]

                    end_idx = min(last_idx + bar_limit, len(self.df) - 1)
                    x_vals = [self.df['Date'].iloc[last_idx], self.df['Date'].iloc[end_idx]]
                    y_vals = [last_val, last_val]

                    self.fig.add_trace(
                        go.Scatter(
                            x=x_vals,
                            y=y_vals,
                            mode="lines",
                            line=dict(color="red", width=2, dash="dash"),
                            showlegend=False,
                            name="Bearish OB"
                        )
                    )
            
        except Exception as e:
            st.write(e)
            pass
        
    