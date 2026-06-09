import ta
import sys
import logging
import plotly.graph_objects as go
import pandas as pd
from tradinglib.indicator import ewo, indicator

logger = logging.getLogger(__name__)

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

class Bsz(_indicator._Indicator):

    is_oszilator = False
    name = 'Buy Sell Zones'

    params = {
        'lookback': {'type': 'int',   'default': 5,   'min': 1, 'max': 50,  'label': 'Lookback bars'},
        'rr_ratio': {'type': 'float', 'default': 1.5,             'label': 'Risk/Reward ratio'},
        'zone_len': {'type': 'int',   'default': 30,  'min': 1, 'max': 200, 'label': 'Zone width (bars)'},
    }

    def __init__(self, df=pd.DataFrame(), symbol = None, lookback=5, rr_ratio=1.5, zone_len=30):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        self.ewo = ewo.Ewo(df=df)
        df = self.ewo.df
        try:
            df = df.reset_index()
            df['timestamp'] = df['Date']
            df = df.set_index('timestamp')
            # ✅ Ensure index is datetime
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
        except Exception:
            pass
        
        super().__init__(df=df, symbol=symbol)
        self.lookback = lookback
        self.rr_ratio = rr_ratio
        self.zone_len = zone_len
        self.zones = []
        self.data()
        
        
    def data(self): 
        """Compute the indicator values and attach them as columns to self.df."""

        zones = []
        df = self.df.copy()
        df = indicator.ema(df,50)
        df = indicator.ema(df,20)
        df = indicator.ema(df,9)
        df.columns = [c.lower() for c in df.columns]  # Normalize to lowercase
#        df.index = pd.to_datetime(df.index)           # Parse index to datetime
        
        for i in range(self.lookback, len(df) - self.lookback):

            # Bullish demand zone + trend
            if (
                df['ema9'].iloc[i] > max(df['ema20'].iloc[i-self.lookback:i]) and
                df['ewo'].iloc[i] > df['ewo_ema'].iloc[i] and
                df['close'].iloc[i+1] > df['open'].iloc[i+1] #and  # bullish candle
            ):
            ## Demand zone (buy area)
            #if df['low'].iloc[i] < min(df['low'].iloc[i - self.lookback:i]) and df['close'].iloc[i + 1] > df['high'].iloc[i]:
                entry = df['close'].iloc[i + 1]
                stop = df['low'].iloc[i] * 0.9995  # small buffer below low
                target = entry + self.rr_ratio * (entry - stop)

                zones.append({
                    'type': 'buy',
                    'index': i,
                    'low': df['low'].iloc[i],
                    'high': df['high'].iloc[i],
                    'entry': entry,
                    'stop': stop,
                    'target': target,
                    'timestamp': df.index[i]
                })

#            # Bearish demand zone - trend
            elif (
                df['ema9'].iloc[i] < min(df['ema20'].iloc[i-self.lookback:i]) and
                df['ewo'].iloc[i] < df['ewo_ema'].iloc[i] and
                df['close'].iloc[i+1] < df['open'].iloc[i+1] #and  # bullish candle
            ):
            # Supply zone (sell area)
                entry = df['close'].iloc[i + 1]
                stop = df['high'].iloc[i] * 1.001  # small buffer above high
                target = entry - self.rr_ratio * (stop - entry)

                zones.append({
                    'type': 'sell',
                    'index': i,
                    'low': df['low'].iloc[i],
                    'high': df['high'].iloc[i],
                    'entry': entry,
                    'stop': stop,
                    'target': target,
                    'timestamp': df.index[i]
                })

        self.zones = zones
        self.df['zones'] = None
        for z in self.zones:
            try:
                closest_index = self.df.index[self.df.index.get_indexer([z['timestamp']], method='nearest')[0]]
                self.df.at[closest_index, 'zones'] = z
            except Exception as e:
                logger.error("Error tagging zone at %s: %s", z['timestamp'], e)
        self.df
        
    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""

        self.fig = go.Figure()
        zone_shapes = []

        candle_width = self.df.index.to_series().diff().median()
        zone_width = candle_width * self.zone_len
        
        
        for zone in self.zones:
            x0 = pd.to_datetime(zone['timestamp'])
            x1 = x0 + zone_width
        
            entry = zone['entry']
            stop = zone['stop']
            target = zone['target']

            is_long = target > entry

            profit_color = 'rgba(0,255,0,0.2)'  # green
            risk_color = 'rgba(255,0,0,0.2)'    # red

            # Profit zone (Entry -> Target)
            zone_shapes.append(dict(
                type="rect",
                xref="x",
                yref="y",
                x0=x0,
                x1=x1,
                y0=min(entry, target),
                y1=max(entry, target),
                fillcolor=profit_color,
                opacity=0.3,
                line_width=0,
                layer="below"
            ))

            # Risk zone (Entry -> Stop)
            zone_shapes.append(dict(
                type="rect",
                xref="x",
                yref="y",
                x0=x0,
                x1=x1,
                y0=min(entry, stop),
                y1=max(entry, stop),
                fillcolor=risk_color,
                opacity=0.3,
                line_width=0,
                layer="below"
            ))

            # Entry marker
            self.fig.add_trace(go.Scatter(
                x=[x0], y=[zone['entry']],
                mode='markers+text',
                marker=dict(color='green' if is_long else 'red', size=10, symbol="circle"),
                text=["Entry"],
                textposition="top center",
                name=f"{zone['type'].capitalize()} Entry",
                showlegend=False
            ))

            # Stop-loss marker
            self.fig.add_trace(go.Scatter(
                x=[x0], y=[zone['stop']],
                mode='markers+text',
                marker=dict(color='orange', size=10, symbol="x"),
                text=["SL"],
                    textposition="bottom center",
                name="Stop Loss",
                showlegend=False
            ))

            # Take-profit marker
            self.fig.add_trace(go.Scatter(
                x=[x0], y=[zone['target']],
                mode='markers+text',
                marker=dict(color='blue', size=10, symbol="star"),
                text=["TP"],
                textposition="top center",
                name="Take Profit",
                showlegend=False
            ))

        self.fig.update_layout(shapes=zone_shapes)

