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


class Sqz(_indicator._Indicator):

    is_oszilator = False
    name = 'Squeeze / Volatility Phase (with Fibonacci)'

    params = {
        'show_trend':  {'type': 'bool',   'default': False, 'label': 'Show trend/expansion overlay'},
        # Höheres Timeframe als Regime-Filter. 'auto' = passend zum Bar-Abstand
        # (Daily->Woche, Intraday->Tag/4h) statt fix '4H' (auf Daily sinnlos).
        'htf_rule':    {'type': 'select', 'default': 'auto', 'label': 'Higher timeframe',
                        'options': ['auto', '1W', '1M', '1D', '4h', '1h']},
        # Vola-Schwellen (früher hartcodiert 0.02 / 0.7 / 1.2):
        'bb_squeeze':  {'type': 'float', 'default': 0.02, 'label': 'BB-width squeeze thr'},
        'atr_low':     {'type': 'float', 'default': 0.7,  'label': 'Contraction ATR factor'},
        'atr_expand':  {'type': 'float', 'default': 1.2,  'label': 'Expansion ATR factor'},
        # Fibonacci (früher hartcodiert n=1, order=6, direction='low_to_high'):
        'fib_order':   {'type': 'int',    'default': 6,  'min': 2, 'max': 50, 'label': 'Fib swing window'},
        'fib_n':       {'type': 'int',    'default': 1,  'min': 0, 'max': 20, 'label': 'Fib swing index'},
        'fib_dir':     {'type': 'select', 'default': 'auto', 'label': 'Fib direction',
                        'options': ['auto', 'low_to_high', 'high_to_low']},
    }

    def __init__(self, df, symbol="", show_trend=False, htf_rule='auto',
                 bb_squeeze=0.02, atr_low=0.7, atr_expand=1.2,
                 fib_order=6, fib_n=1, fib_dir='auto'):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.show_trend = show_trend
        self.htf_rule = htf_rule
        self.bb_squeeze = float(bb_squeeze)
        self.atr_low = float(atr_low)
        self.atr_expand = float(atr_expand)
        self.fib_order = int(fib_order)
        self.fib_n = int(fib_n)
        self.fib_dir = fib_dir

    def calculate_phases(self, data):
        """Classify each candle into a volatility phase (Contraction/Expansion/Trend)."""
        high_low = data['High'] - data['Low']
        high_close = (data['High'] - data['Close'].shift()).abs()
        low_close = (data['Low'] - data['Close'].shift()).abs()

        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        data['sqz_atr'] = true_range.rolling(14).mean()

        ma = data['Close'].rolling(20).mean()
        std = data['Close'].rolling(20).std()
        data['sqz_bb_width'] = (2 * std) / ma

        atr_mean = data['sqz_atr'].mean()
        phases = []
        signals = []
        for i in range(len(data)):
            if data['sqz_bb_width'].iloc[i] < self.bb_squeeze and data['sqz_atr'].iloc[i] < atr_mean * self.atr_low:
                phases.append("Contraction")
                signals.append("Buy Straddle/Strangle")
            elif data['sqz_atr'].iloc[i] > data['sqz_atr'].shift(1).iloc[i] * self.atr_expand:
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

        data['sqz_phase'] = phases
        data['sqz_signal'] = signals
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
        df['sqz_delta'] = delta
        df['sqz_cum_delta'] = pd.Series(delta).cumsum()
        return df

    def calculate_cumdelta_price_scaled(self, rule="5T"):
        """
        Aggregate CumDelta into OHLC form and scale it to the price candles.
        """
        if 'sqz_cum_delta' not in self.df.columns:
            self.df = self.calculate_cumulative_delta(self.df.reset_index())

        df = self.df.reset_index().copy()
        df = df.set_index("Date")

        # CumDelta OHLC
        cd_ohlc = df['sqz_cum_delta'].resample(rule).ohlc().dropna()
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
        
    def _resolve_htf(self):
        """Higher-timeframe-Resample-Regel bestimmen. 'auto' wählt anhand des
        typischen Bar-Abstands: Daily+->Woche, ~Stunden->Tag, Minuten->4h — statt
        eines fixen '4H', das auf Daily-Daten sinnlos ist (feiner als die Bars)."""
        rule = getattr(self, 'htf_rule', 'auto') or 'auto'
        if rule != 'auto':
            return rule
        try:
            d = pd.to_datetime(self.df['Date'])
            med = d.diff().dropna().median()
            secs = med.total_seconds()
        except Exception:
            return '1W'
        if secs >= 6 * 3600:       # >= ~1/4 Tag (Daily/Weekly-Bars)
            return '1W'
        if secs >= 3600:           # Stunden-Bars
            return '1D'
        return '4h'                # Minuten-Bars

    def _add_phase_markers(self, phase, ma20, tag='LTF', size=12, open_marker=False):
        """Markiert nur den BEGINN einer Contraction/Expansion-Phase (nicht jeden
        Bar) — durchgehend als BUBBLE (Kreis), keine Dreiecke:
          * Farbe  = Richtung: grün = bullish (Kauf, unter dem Bar),
                                rot  = bearish (Verkauf, über dem Bar).
          * Füllung/Größe = Phase: Squeeze = kleiner + halbtransparent (Anbahnung),
                                    Expansion = größer + voll (bestätigter Ausbruch).
          * HTF (open_marker) = hohle Bubble.
        """
        onset = phase != phase.shift(1)
        sym = 'circle-open' if open_marker else 'circle'
        bull, bear = '#2E7D32', '#C62828'

        def _bubble(mask, color, mk_size, opacity, above, label):
            if not bool(mask.any()):
                return
            y = (self.df['High'][mask] * 1.012) if above else (self.df['Low'][mask] * 0.988)
            self.fig.add_trace(go.Scatter(
                x=self.df.index[mask], y=y, mode='markers',
                marker=dict(color=color, size=mk_size, symbol=sym, opacity=opacity,
                            line=dict(color='white', width=1.5)),
                name=f'{label} ({tag})', showlegend=False,
                hovertext=[f'{label} · {tag}'] * int(mask.sum()), hoverinfo='text'))

        # Squeeze (Anbahnung) — kleiner, halbtransparent
        c = onset & (phase == 'Contraction')
        _bubble(c & (self.df['Close'] > ma20), bull, size, 0.55, False, 'Squeeze ▲ Kauf-Bias')
        _bubble(c & (self.df['Close'] <= ma20), bear, size, 0.55, True, 'Squeeze ▼ Verkauf-Bias')

        # Expansion (Ausbruch) — größer, voll
        e = onset & (phase == 'Expansion')
        _bubble(e & (self.df['Close'] > ma20), bull, size + 4, 1.0, False, 'Expansion ▲ Kauf')
        _bubble(e & (self.df['Close'] <= ma20), bear, size + 4, 1.0, True, 'Expansion ▼ Verkauf')

    def add_fig(self, htf_rule=None):
        """Add the indicator traces to the given Plotly figure."""
        self.fig = go.Figure()
        try:
            self.df = self.df.reset_index()
        except Exception:
            pass

        if not pd.api.types.is_datetime64_any_dtype(self.df['Date']):
            self.df['Date'] = pd.to_datetime(self.df['Date'])

        self.df = self.calculate_phases(self.df)

        htf_rule = htf_rule or self._resolve_htf()
        df_high = self.aggregate_to_htf(self.df, rule=htf_rule)
        df_high = self.calculate_phases(df_high)

        self.df = self.df.set_index('Date')
        df_high = df_high.set_index(df_high.index)
        self.df['sqz_htf_phase'] = df_high['sqz_phase'].reindex(self.df.index, method='ffill')


        # Phasen-Marker NUR am Phasenbeginn (Onset) statt auf jedem Bar — die
        # bisherige Punkt-pro-Bar-Logik hat die Kurslinie zugekleistert (v. a. der
        # HTF-„Trend"). Aussagekräftig sind Contraction (Squeeze) und Expansion
        # (Ausbruch inkl. Richtung); „Trend" wird gar nicht mehr markiert.
        ma20 = self.df['Close'].rolling(20).mean()
        self._add_phase_markers(self.df['sqz_phase'], ma20, tag='LTF', size=12, open_marker=False)
        if self.show_trend:
            # HTF-Regime als Bestätigung — größere, hohle Marker (nur bei Bedarf).
            self._add_phase_markers(self.df['sqz_htf_phase'], ma20, tag='HTF', size=17, open_marker=True)

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
        # Fibonacci aus Parametern (Swing-Fenster/Index/Richtung); direction='auto'
        # erkennt die Leg-Richtung selbst statt fix "low_to_high" (im Abtrend falsch).
        self.add_fibonacci(n=self.fib_n, order=self.fib_order, annotate=True,
                           direction=self.fib_dir)
        
        
        return self.fig

