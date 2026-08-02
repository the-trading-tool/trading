import ta
import sys
import plotly.graph_objects as go
import numpy as np
import streamlit as st 

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

class Obd(_indicator._Indicator):

    is_oszilator = False
    name = 'Order Block Detector'

    params = {
        'period':       {'type': 'int',    'default': 21,     'min': 2,  'max': 200, 'label': 'Flow imbalance period'},
        'ob_periods':   {'type': 'int',    'default': 3,      'min': 1,  'max': 20,  'label': 'Order block periods'},
        'ob_threshold': {'type': 'float',  'default': 1.0,               'label': 'Order block min move %'},
        'use_wicks':    {'type': 'bool',   'default': False,              'label': 'Include wicks'},
        'ob_extend':    {'type': 'select', 'default': 'right', 'label': 'Extend direction', 'options': ['right', 'left', 'none']},
        # Support/Resistance (jetzt parametrierbar + skalenrelativ statt hartcodiert):
        'sr_window':    {'type': 'int',    'default': 21,     'min': 5,  'max': 200, 'label': 'S/R lookback'},
        'sr_min_gap':   {'type': 'float',  'default': 0.5,               'label': 'S/R min gap %'},
        'sr_zone':      {'type': 'float',  'default': 1.0,               'label': 'S/R proximity zone %'},
    }

    def __init__(self, df, symbol="", period=21, ob_periods=3, ob_threshold=1.0,
                 use_wicks=False, ob_extend="right", sr_window=21, sr_min_gap=0.5,
                 sr_zone=1.0):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.period = period
        self.ob_periods = ob_periods
        self.ob_threshold = ob_threshold
        self.ob_extend = ob_extend
        self.use_wicks = use_wicks
        # S/R-Steuerung: Fenster, Mindestabstand zwischen Levels (% des Preises),
        # Näherungszone fürs Signal (% um das Level). Prozentual → funktioniert auf
        # jedem Preisniveau (5-€-Aktie wie BTC), früher absolut 0.5 / hart 1 %.
        self.sr_window = int(sr_window)
        self.sr_min_gap = float(sr_min_gap)
        self.sr_zone = float(sr_zone)
        self.data()

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""

        self.df = self.df.ffill()

        # 1. Order flow volume
        self.df['obd_price_change'] = self.df['Close'].diff()
        self.df['obd_buy_vol'] = self.df['Volume'].where(self.df['obd_price_change'] > 0, 0)
        self.df['obd_sell_vol'] = self.df['Volume'].where(self.df['obd_price_change'] < 0, 0)

        # 2. Rolling Order Flow Imbalance
        self.df['obd_flow_imbalance'] = (self.df['obd_buy_vol'] - self.df['obd_sell_vol']).rolling(self.period).sum()

        # Optional: smoothing
        self.df['obd_flow_smoothed'] = self.df['obd_flow_imbalance'].ewm(span=5).mean()

        # 3. Support/Resistance — Fenster + Mindestabstand jetzt aus Parametern,
        #    Abstand RELATIV (% des Levels) statt absolut → skalenunabhängig.
        def find_levels(window, min_gap_pct):
            """Detect significant price levels from the OHLCV data."""
            support = self.df['Close'].rolling(window).min().dropna().drop_duplicates()
            resistance = self.df['Close'].rolling(window).max().dropna().drop_duplicates()

            def filter_levels(levels):
                """Filter overlapping or redundant price levels by relative proximity."""
                filtered = []
                for level in sorted(levels):
                    # neues Level nur behalten, wenn es >= min_gap_pct % vom letzten entfernt ist
                    if not filtered or abs(level - filtered[-1]) >= abs(filtered[-1]) * (min_gap_pct / 100.0):
                        filtered.append(level)
                return filtered

            return filter_levels(support.tolist()), filter_levels(resistance.tolist())

        support_levels, resistance_levels = find_levels(self.sr_window, self.sr_min_gap)

        # 4. Signal logic (vectorised) — Näherungszone aus Parameter (% um das Level)
        #    NaN statt False: Nicht-Signal-Bars bleiben Lücken (kein Strich auf 0)
        #    und die Spalte ist float (kein bool→float-Dtype-Warning beim Setzen).
        price_range_pct = self.sr_zone / 100.0
        self.df['obd_buy'] = np.nan
        self.df['obd_sell'] = np.nan

        for s in support_levels:
            mask = (
                (self.df['Close'] >= s * (1 - price_range_pct)) &
                (self.df['Close'] <= s * (1 + price_range_pct)) &
                (self.df['obd_flow_imbalance'] < 0)  # counter-trend logic
            )
            self.df.loc[mask, 'obd_buy'] = self.df['Close']

        for r in resistance_levels:
            mask = (
                (self.df['Close'] >= r * (1 - price_range_pct)) &
                (self.df['Close'] <= r * (1 + price_range_pct)) &
                (self.df['obd_flow_imbalance'] > 0)  # counter-trend logic
            )
            self.df.loc[mask, 'obd_sell'] = self.df['Close']

        try:
            # === 5. Order Block Detection ===
            self.df['obd_ob_bull'] = np.nan
            self.df['obd_ob_bear'] = np.nan

            for i in range(self.ob_periods, len(self.df) - self.ob_periods):
                ob_candle = i - self.ob_periods
                close_ob = self.df['Close'].iloc[ob_candle]
                open_ob = self.df['Open'].iloc[ob_candle]
                close_seq_end = self.df['Close'].iloc[i]

                absmove = abs((close_seq_end - close_ob) / close_ob) * 100
                if absmove < self.ob_threshold:
                    continue

                # === Bullish OB ===
                if close_ob < open_ob:  # red candle
                    up_seq = all(
                        self.df['Close'].iloc[ob_candle + j] > self.df['Open'].iloc[ob_candle + j]
                        for j in range(1, self.ob_periods + 1)
                    )
                    if up_seq:
                        high = self.df['High'].iloc[ob_candle] if self.use_wicks else self.df['Open'].iloc[ob_candle]
                        low = self.df['Low'].iloc[ob_candle]
                        self.df.at[self.df.index[ob_candle], 'obd_ob_bull'] = (high + low) / 2

                # === Bearish OB ===
                if close_ob > open_ob:  # green candle
                    down_seq = all(
                        self.df['Close'].iloc[ob_candle + j] < self.df['Open'].iloc[ob_candle + j]
                        for j in range(1, self.ob_periods + 1)
                    )
                    if down_seq:
                        high = self.df['High'].iloc[ob_candle]
                        low = self.df['Low'].iloc[ob_candle] if self.use_wicks else self.df['Open'].iloc[ob_candle]
                        self.df.at[self.df.index[ob_candle], 'obd_ob_bear'] = (high + low) / 2
        except Exception as e:
            st.write(e)
            pass

    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        try:
            self.fig.add_trace(
                go.Scatter(
                    x=self.df['Date'],
                    y=self.df['obd_buy'],
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
                    y=self.df['obd_sell'],
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
                    y=self.df['obd_ob_bull'],
                    mode="markers",
                    marker=dict(color='green', size=10, symbol="x"),
                    showlegend=False,
                    name='obd_ob_bull'
                )
            )

            # === Plot Bearish OBs ===
            self.fig.add_trace(
                go.Scatter(
                    x=self.df['Date'],
                    y=self.df['obd_ob_bear'],
                    mode="markers",
                    marker=dict(color='red', size=10, symbol="x"),
                    showlegend=False,
                    name='obd_ob_bear'
                )
            )
    
            # Helper for x-length (e.g. 10 bars corresponds to ~20 pips visually)
            bar_limit = self.ob_periods * 2  # configurable: how many candles wide should the line extend?

            # === Last Bullish OB ===
            bullish_obs = self.df['obd_ob_bull'].dropna()
            if not bullish_obs.empty:
                for i in range(0,len(bullish_obs.index)):
                    last_idx = bullish_obs.index[i]
                    last_val = bullish_obs.iloc[i]

                    # Limit time axis to bar_limit
                    end_idx = min(last_idx + bar_limit, len(self.df) - 1)
                    x_vals = [self.df['Date'].iloc[last_idx], self.df['Date'].iloc[end_idx]]

                    # Optional: vertical price range ±20 pips (visually helpful)
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

            # === Last Bearish OB ===
            bearish_obs = self.df['obd_ob_bear'].dropna()
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

