import sys
import logging
import plotly.graph_objects as go
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator


class Mmm(_indicator._Indicator):

    is_oszilator = False
    name = 'Market Maker Master Pattern (with Fibonacci)'

    params = {
        'show_trend': {'type': 'bool', 'default': False, 'label': 'Show trend overlay'},
    }

    def __init__(self, df, symbol="", show_trend=False):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.show_trend = show_trend

    def calculate_phases(self, data):
        """Classify each candle into a market phase (trend/consolidation)."""
        high_low = data['High'] - data['Low']
        high_close = (data['High'] - data['Close'].shift()).abs()
        low_close = (data['Low'] - data['Close'].shift()).abs()

        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        data['mmm_atr'] = true_range.rolling(14).mean()

        ma = data['Close'].rolling(20).mean()
        std = data['Close'].rolling(20).std()
        data['mmm_bb_width'] = (2 * std) / ma

        phases = []
        signals = []
        for i in range(len(data)):
            if data['mmm_bb_width'].iloc[i] < 0.02 and data['mmm_atr'].iloc[i] < data['mmm_atr'].mean() * 0.7:
                phases.append("Contraction")
                signals.append("Buy Straddle/Strangle")
            elif data['mmm_atr'].iloc[i] > data['mmm_atr'].shift(1).iloc[i] * 1.2:
                phases.append("Expansion")
                if data['Close'].iloc[i] > ma.iloc[i]:
                    signals.append("Buy Call Option")
                else:
                    signals.append("Buy Put Option")
            else:
                phases.append("Trend")
                if data['Close'].iloc[i] > ma.iloc[i]:
                    signals.append("Hold Calls")
                else:
                    signals.append("Hold Puts")

        data['mmm_phase'] = phases
        data['mmm_signal'] = signals
        return data

    def aggregate_to_htf(self, df, rule="1H"):
        """Resample OHLCV data to a higher timeframe."""
        df_htf = df.resample(rule, on="Date").agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last'
        }).dropna()
        return df_htf

    def heikin_ashi_colors(self, df):
        """Map Heikin-Ashi candle direction to color values."""
        ha_close = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
        ha_open = [(df['Open'].iloc[0] + df['Close'].iloc[0]) / 2]
        for i in range(1, len(df)):
            ha_open.append((ha_open[i-1] + ha_close.iloc[i-1]) / 2)

        colors = []
        for i in range(len(df)):
            if ha_close.iloc[i] >= ha_open[i]:
                colors.append('aquamarine')
            else:
                colors.append('darkgrey')
        return colors
    
    # ---------------------------
    # Swing / Fibonacci utilities
    # ---------------------------
    def detect_local_extrema(self, order=5):
        """Detect swing highs and lows in the price series."""
        highs = []
        lows = []
        n = len(self.df)
        if n < (2 * order + 1):
            return highs, lows

        high_vals = self.df['High'].values
        low_vals = self.df['Low'].values

        for i in range(order, n - order):
            window_h = high_vals[i - order: i + order + 1]
            if high_vals[i] == window_h.max():
                highs.append(i)
            window_l = low_vals[i - order: i + order + 1]
            if low_vals[i] == window_l.min():
                lows.append(i)

        return highs, lows

    def get_alternating_pairs(self, order=5):
        """Extract alternating high/low pairs from the extrema list."""
        highs, lows = self.detect_local_extrema(order=order)
        events = []
        for idx in highs:
            events.append({'idx': idx, 'date': self.df.index[idx], 'type': 'H', 'price': float(self.df['High'].iloc[idx])})
        for idx in lows:
            events.append({'idx': idx, 'date': self.df.index[idx], 'type': 'L', 'price': float(self.df['Low'].iloc[idx])})
        events = sorted(events, key=lambda e: e['idx'])

        pairs = []
        for i in range(1, len(events)):
            prev = events[i - 1]
            cur = events[i]
            if prev['type'] != cur['type']:
                pairs.append((prev, cur))
        return pairs

    def add_fibonacci(self,
                    n=0,
                    order=5,
                    levels=None,
                    colors=None,
                    annotate=True,
                    show_markers=True,
                    direction="auto",
                    extend_to_end=False,
                    annotation_side = "left",
                    to_current=False,
                    to_extreme=False):
        """
        Draw Fibonacci retracements.

        - n: swing point or swing-pair index (0 = most recent)
        - order: window size for swing detection
        - direction: "low_to_high", "high_to_low" or "auto"
        - extend_to_end: extend lines to the end of the chart
        - to_current: from the swing to the last price
        - to_extreme: from the swing to the last extremum (highest high or lowest low since swing)
        """

        if levels is None:
            levels = [-2.0, -1.0, -0.618, -0.5, -0.236, 0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0,
                      1.272, 1.414, 1.618, 2.0]

        default_colors = ["blue", "purple", "green", "red", "orange", "cyan", "magenta",
                        "brown", "olive", "teal", "navy",'darkblue','darkgreen','darkred','purple','darkgrey']

        if colors is None:
            colors = default_colors

        # --- Determine swing point ---
        highs, lows = self.detect_local_extrema(order=order)
        events = []
        for idx in highs:
            events.append({'idx': idx, 'date': self.df.index[idx],
                        'type': 'H', 'price': float(self.df['High'].iloc[idx])})
        for idx in lows:
            events.append({'idx': idx, 'date': self.df.index[idx],
                        'type': 'L', 'price': float(self.df['Low'].iloc[idx])})
        events = sorted(events, key=lambda e: e['idx'])

        try:
            swing = events[-1 - int(n)]
        except Exception:
            return

        start_date = self.df.index[swing['idx']]
        start_price = swing['price']

        # --- Determine target ---
        if to_current:
            end_date = self.df.index[-1]
            end_price = float(self.df['Close'].iloc[-1])
        elif to_extreme:
            if swing['type'] == 'L':
                # from the low to the highest high afterwards
                idx_range = range(swing['idx'], len(self.df))
                sub_df = self.df.iloc[idx_range]
                end_idx = sub_df['High'].idxmax()
                end_date = end_idx
                end_price = float(self.df.loc[end_idx, 'High'])
                if direction == "auto":
                    direction = "low_to_high"
            else:
                # from the high to the lowest low afterwards
                idx_range = range(swing['idx'], len(self.df))
                sub_df = self.df.iloc[idx_range]
                end_idx = sub_df['Low'].idxmin()
                end_date = end_idx
                end_price = float(self.df.loc[end_idx, 'Low'])
                if direction == "auto":
                    direction = "high_to_low"
        else:
            # fallback: normal swing pair
            pairs = self.get_alternating_pairs(order=order)
            if not pairs:
                return
            try:
                left, right = pairs[-1 - int(n)]
            except Exception:
                return
            start_date, end_date = self.df.index[left['idx']], self.df.index[right['idx']]
            start_price, end_price = left['price'], right['price']
            if direction == "auto":
                if left['type'] == 'L' and right['type'] == 'H':
                    direction = "low_to_high"
                else:
                    direction = "high_to_low"

        # --- Price calculation as before ---
        if direction == "low_to_high":
            low_price, high_price = min(start_price, end_price), max(start_price, end_price)
            price_func = lambda lvl: low_price + (high_price - low_price) * lvl
        else:
            high_price, low_price = max(start_price, end_price), min(start_price, end_price)
            price_func = lambda lvl: high_price - (high_price - low_price) * lvl

        if extend_to_end:
            end_date = self.df.index[-1]

        for i, lvl in enumerate(levels):
            price_level = float(price_func(lvl))
            color = colors[i % len(colors)]

            # Optionally shorten the line when the annotation should appear on the left
            line_start = start_date
            line_end = end_date
            if annotation_side == "left":
                time_span = self.df.index[-1] - self.df.index[0]
                shorten = pd.Timedelta(seconds=time_span.total_seconds() * 0.05)
                line_end = end_date - shorten
                if line_end <= line_start:
                    line_end = end_date

            self.fig.add_trace(
                go.Scatter(
                    x=[line_start, line_end],
                    y=[price_level, price_level],
                    mode='lines',
                    line=dict(color=color, width=1.5, dash='dash'),
                    name=f'Fib {lvl:.3f}',
                    showlegend=False
                )
            )

            if annotate:
                if annotation_side == "right":
                    ann_x = line_end
                    ann_anchor = "left"
                else:
                    ann_x = line_start
                    ann_anchor = "right"

                atext=f"{int(lvl*100)}% ({price_level:.3f})"
                if self.df['Close'].iloc[-1] > 100:
                    atext=f"{int(lvl*100)}% ({price_level:.0f})"
                elif self.df['Close'].iloc[-1] > 10:
                    atext=f"{int(lvl*100)}% ({price_level:.1f})"
                elif self.df['Close'].iloc[-1] > 1:
                    atext=f"{int(lvl*100)}% ({price_level:.2f})"

                self.fig.add_annotation(
                    x=ann_x, y=price_level,
                    text=atext,
                    showarrow=False,
                    xanchor=ann_anchor, yanchor="middle",
                    bgcolor='rgba(255,255,255,0.8)',
                    bordercolor=color,
                    font=dict(size=10)
                )

        if show_markers:
            self.fig.add_trace(
                go.Scatter(
                    x=[start_date, end_date],
                    y=[start_price, end_price],
                    mode='markers',
                    marker=dict(size=8, color='black'),
                    name='Swing/Extreme',
                    showlegend=False
                )
            )

    # -----------------------------
    # CumDelta calculation + candles
    # -----------------------------
    def calculate_cumulative_delta(self, df):
        """
        Simple CumDelta: compare close to the previous day.
        """
        delta = []
        for i in range(len(df)):
            if i == 0:
                delta.append(0)
            else:
                if df['Close'].iloc[i] > df['Close'].iloc[i-1]:
                    delta.append(df['Volume'].iloc[i])   # buy volume
                elif df['Close'].iloc[i] < df['Close'].iloc[i-1]:
                    delta.append(-df['Volume'].iloc[i])  # sell volume
                else:
                    delta.append(0)
        df['mmm_delta'] = delta
        df['mmm_cum_delta'] = pd.Series(delta).cumsum()
        return df

    def calculate_cumdelta_price_scaled(self, rule="5T"):
        """
        Aggregate CumDelta into OHLC form and scale it to the price candles.
        """
        if 'mmm_cum_delta' not in self.df.columns:
            self.df = self.calculate_cumulative_delta(self.df.reset_index())

        df = self.df.reset_index().copy()
        df = df.set_index("Date")

        # CumDelta OHLC
        cd_ohlc = df['mmm_cum_delta'].resample(rule).ohlc().dropna()
        # Price OHLC for the same period
        price_ohlc = df[['Open', 'High', 'Low', 'Close']].resample(rule).ohlc().dropna()

        scaled = []
        for idx in cd_ohlc.index.intersection(price_ohlc.index):
            cd = cd_ohlc.loc[idx]
            pr = price_ohlc.loc[idx]

            cd_min, cd_max = cd[['low', 'high']].min(), cd[['low', 'high']].max()
            pr_min, pr_max = pr['low']['Low'], pr['high']['High']  # price range

            if cd_max == cd_min:  # Edge case: Flat CumDelta
                scale = 0
            else:
                scale = (pr_max - pr_min) / (cd_max - cd_min)

            def scale_val(v):
                """Scale a value to the target display range."""
                return pr_min + (v - cd_min) * scale

            scaled.append({
                "Date": idx,
                "open": scale_val(cd['open']),
                "high": scale_val(cd['high']),
                "low": scale_val(cd['low']),
                "close": scale_val(cd['close']),
            })

        return pd.DataFrame(scaled)

    def add_cumdelta_candles_scaled(self, rule="5T"):
        """
        Add CumDelta candles overlaid and scaled to the price axis.
        """
        ohlc = self.calculate_cumdelta_price_scaled(rule=rule)
        if ohlc.empty:
            return

        self.fig.add_trace(go.Candlestick(
            x=ohlc['Date'],
            open=ohlc['open'],
            high=ohlc['high'],
            low=ohlc['low'],
            close=ohlc['close'],
            increasing_line_color="limegreen",
            decreasing_line_color="crimson",
            opacity=0.4,
            name=f"CumDelta Candles Scaled ({rule})",
            showlegend=True
        ))
        
    def add_fig(self, htf_rule="4H"):
        """Add the indicator traces to the given Plotly figure."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        if not pd.api.types.is_datetime64_any_dtype(self.df['Date']):
            self.df['Date'] = pd.to_datetime(self.df['Date'])

        self.df = self.calculate_phases(self.df)

        df_high = self.aggregate_to_htf(self.df, rule=htf_rule)
        df_high = self.calculate_phases(df_high)

        self.df = self.df.set_index('Date')
        df_high = df_high.set_index(df_high.index)
        self.df['mmm_htf_phase'] = df_high['mmm_phase'].reindex(self.df.index, method='ffill')


        # Candlestick-Farben korrekt zuweisen (Color per point nicht direkt möglich, daher mit custom line traces)
#        for i in range(len(self.df)):
#                )
#            )

        colors = {'Contraction': 'orange', 'Expansion': 'red', 'Trend': 'green'}
        for phase in self.df['mmm_phase'].unique():
            if phase == "Trend" and not self.show_trend:
                continue
            if phase == "Expansion" and not self.show_trend:
                continue
            mask = self.df['mmm_phase'] == phase
            self.fig.add_trace(
                go.Scatter(
                    x=self.df.index[mask],
                    y=self.df['Close'][mask],
                    mode="markers",
                    marker=dict(color=colors[phase], size=10),
                    name=f"{phase} (Low TF)",
                    showlegend=False,
                ))

        htf_colors = {
            'Contraction': 'darkorange',
            'Expansion': 'purple',
            'Trend': 'blue'
        }

        for phase in self.df['mmm_htf_phase'].unique():
            if phase == "Trend" and not self.show_trend:
                continue
            if phase == "Expansion" and not self.show_trend:
                continue
            mask = self.df['mmm_htf_phase'] == phase
            if mask.any():
                self.fig.add_trace(
                    go.Scatter(
                        x=self.df.index[mask],
                        y=self.df['Close'][mask],
                        mode="markers",
                        marker=dict(color=htf_colors[phase], size=10),
                        name=f"{phase} (High TF)",
                        showlegend=False,
                    ))

        """
        # --- Verbesserte Volume Delta Marker Logik mit Signifikanzfilter ---
        # --- Erkenntnis: Volume Delta Marker ist zuverlässiger
        gap = 9
        offset_pct = 0.05            # Offset in % der Candle-Höhe für Marker-Position
        min_delta_rel = 0.5           # Relative Schwelle: 0.5 = 50% des Durchschnittsvolumens

        # Sicherheitsprüfung: ausreichend Daten?
        if len(self.df) > gap:
            delta_colors = []
            delta_symbols = []
            delta_markers_x = []
            delta_markers_y = []

            mean_vol = self.df['Volume'].mean()

            for i in range(gap, len(self.df)):
                vol_now = self.df['Volume'].iloc[i]
                vol_prev = self.df['Volume'].iloc[i - gap]
                close_now = self.df['Close'].iloc[i]
                close_prev = self.df['Close'].iloc[i - gap]

                # Volumenänderung & Signifikanz
                delta = vol_now - vol_prev
                if abs(delta) < mean_vol * min_delta_rel:
                    continue  # zu unbedeutend → kein Signal

                # kleine Offset-Berechnung
                candle_range = self.df['High'].iloc[i] - self.df['Low'].iloc[i]
                up_y = self.df['High'].iloc[i] + offset_pct * candle_range
                down_y = self.df['Low'].iloc[i] - offset_pct * candle_range

                # Bedingungen
                if delta > 0 and close_now >= close_prev:
                    # bullisches Signal
                    delta_colors.append('green')
                    delta_symbols.append('triangle-up')
                    delta_markers_x.append(self.df.index[i])
                    delta_markers_y.append(up_y)

                elif delta < 0 and close_now <= close_prev:
                    # bärisches Signal
                    delta_colors.append('red')
                    delta_symbols.append('triangle-down')
                    delta_markers_x.append(self.df.index[i])
                    delta_markers_y.append(down_y)

            # Plot hinzufügen (nur wenn Marker existieren)
            if delta_markers_x:
                self.fig.add_trace(
                    go.Scatter(
                        x=delta_markers_x,
                        y=delta_markers_y,
                        mode='markers',
                        marker=dict(color=delta_colors, size=10, symbol=delta_symbols),
                        name=f'Volume Delta Extremes (gap={gap}, rel>{min_delta_rel})',
                        showlegend=False
                    )
                )
        else:
            logger.warning("Nicht genug Daten für Volume-Delta-Berechnung (len=%s, gap=%s)", len(self.df), gap)
        """
        # Fibonacci: letztes Paar, optional bis zum Chartende
#        self.add_fibonacci(n=0, order=5, annotate=True, annotation_side='left', direction="low_to_high", to_extreme=True)
        self.add_fibonacci(n=1, order=6, annotate=True, direction="low_to_high")
#        self.add_fibonacci(n=3, order=10, annotate=True, direction="low_to_high")
        
        
        return self.fig

