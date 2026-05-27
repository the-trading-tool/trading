import pandas as pd
import numpy as np
import plotly.graph_objects as go
import sys

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    print('No Import')

from tradinglib.indicator import (_indicator, indicator)


class Heikin(_indicator._Indicator):
    """
    Heikin-Ashi & Smoothed Heiken-Ashi Chart Generator
    """

    name = 'Heikin Ashi'
    is_oszilator = False

    params = {
        'smooth':               {'type': 'bool',   'default': False, 'label': 'Smoothed HA'},
        'smooth_length_before': {'type': 'int',    'default': 10,    'label': 'Smooth length (before)', 'min': 1, 'max': 200},
        'smooth_ma_type_before':{'type': 'select', 'default': 'EMA', 'label': 'MA type (before)',       'options': ['SMA','EMA','WMA','RMA','HMA']},
        'smooth_length_after':  {'type': 'int',    'default': 10,    'label': 'Smooth length (after)',  'min': 1, 'max': 200},
        'smooth_ma_type_after': {'type': 'select', 'default': 'EMA', 'label': 'MA type (after)',        'options': ['SMA','EMA','WMA','RMA','HMA']},
        'ema_high_length':      {'type': 'int',    'default': 21,    'label': 'EMA High length',        'min': 1, 'max': 200},
        'ema_low_length':       {'type': 'int',    'default': 21,    'label': 'EMA Low length',         'min': 1, 'max': 200},
        'ema_type':             {'type': 'select', 'default': 'EMA', 'label': 'EMA type',               'options': ['SMA','EMA','WMA','RMA','HMA']},
        'show_emas':            {'type': 'bool',   'default': True,  'label': 'Show EMAs'},
        'color_bull':           {'type': 'color',  'default': '',    'label': 'Bull candle color'},
        'color_bear':           {'type': 'color',  'default': '',    'label': 'Bear candle color'},
        'color_ema_high':       {'type': 'color',  'default': '',    'label': 'EMA High color'},
        'color_ema_low':        {'type': 'color',  'default': '',    'label': 'EMA Low color'},
    }

    def __init__(
        self,
        df,
        symbol="",
        smooth=False,
        smooth_length_before=10,
        smooth_ma_type_before='EMA',
        smooth_length_after=10,
        smooth_ma_type_after='EMA',
        # === NEW ===
        ema_high_length=21,
        ema_low_length=21,
        ema_type='EMA',
        show_emas=True
    ):
        super().__init__(df=df, symbol=symbol)

        self.df = df.copy()
        self.smooth = smooth
        self.smooth_length_before = smooth_length_before
        self.smooth_ma_type_before = smooth_ma_type_before
        self.smooth_length_after = smooth_length_after
        self.smooth_ma_type_after = smooth_ma_type_after

        # === EMA settings ===
        self.ema_high_length = ema_high_length
        self.ema_low_length = ema_low_length
        self.ema_type = ema_type
        self.show_emas = show_emas

        # Pass a copy so _calculate_heikin_ashi's reset_index() does not mutate
        # self.df's index — the Date-string index must stay intact for the merge
        # in fetch_data.py to align correctly against the main DataFrame.
        self.df_ha = self._calculate_heikin_ashi(self.df.copy())

        # Use .values (positional) to avoid index-mismatch NaNs: self.df has a
        # Date-string index while self.df_ha has a reset integer index.
        self.df['ha_ema_high'] = self.df_ha['EMA_HA_High'].values
        self.df['ha_ema_low']  = self.df_ha['EMA_HA_Low'].values
        # Export HA candle body columns so buy/sell queries can reference them.
        self.df['ha_close']    = self.df_ha['HA_Close'].values
        self.df['ha_open']     = self.df_ha['HA_Open'].values
        self.add_fig()

    # === Moving Average Utility ===
    def _ma(self, series: pd.Series, length: int, ma_type: str = 'EMA') -> pd.Series:
        if ma_type.upper() == 'SMA':
            return series.rolling(length).mean()
        elif ma_type.upper() == 'EMA':
            return series.ewm(span=length, adjust=False).mean()
        elif ma_type.upper() == 'WMA':
            weights = np.arange(1, length + 1)
            return series.rolling(length).apply(
                lambda x: np.dot(x, weights) / weights.sum(), raw=True
            )
        elif ma_type.upper() == 'RMA':
            return series.ewm(alpha=1 / length, adjust=False).mean()
        elif ma_type.upper() == 'HMA':
            wma_half = self._ma(series, length // 2, 'WMA')
            wma_full = self._ma(series, length, 'WMA')
            return self._ma(2 * wma_half - wma_full, int(np.sqrt(length)), 'WMA')
        else:
            return series  # fallback

    # === Core Calculation ===
    def _calculate_heikin_ashi(self, df):
        try:
            df.reset_index(inplace=True)
        except Exception:
            pass

        df = df.copy()

        # === Optional: Pre-smoothing of OHLC ===
        if self.smooth:
            for col in ['Open', 'High', 'Low', 'Close']:
                df[col] = self._ma(
                    df[col],
                    self.smooth_length_before,
                    self.smooth_ma_type_before
                )

        # === Heikin-Ashi ===
        ha_df = pd.DataFrame(index=df.index)

        ha_df['HA_Close'] = (
            df['Open'] + df['High'] + df['Low'] + df['Close']
        ) / 4

        ha_open = [(df['Open'].iloc[0] + df['Close'].iloc[0]) / 2]
        for i in range(1, len(df)):
            ha_open.append(
                (ha_open[i - 1] + ha_df['HA_Close'].iloc[i - 1]) / 2
            )

        ha_df['HA_Open'] = ha_open

        ha_df['HA_High'] = (
            df[['High', 'Low']]
            .join(ha_df[['HA_Open', 'HA_Close']])
            .max(axis=1)
        )

        ha_df['HA_Low'] = (
            df[['High', 'Low']]
            .join(ha_df[['HA_Open', 'HA_Close']])
            .min(axis=1)
        )

        # === Optional: Post-smoothing ===
        if self.smooth:
            for col in ['HA_Open', 'HA_High', 'HA_Low', 'HA_Close']:
                ha_df[col] = self._ma(
                    ha_df[col],
                    self.smooth_length_after,
                    self.smooth_ma_type_after
                )

        # === EMA on HA High / Low ===
        ha_df['EMA_HA_High'] = self._ma(
            ha_df['HA_High'],
            self.ema_high_length,
            self.ema_type
        )

        ha_df['EMA_HA_Low'] = self._ma(
            ha_df['HA_Low'],
            self.ema_low_length,
            self.ema_type
        )

        ha_df['Date'] = df['Date'] if 'Date' in df.columns else df.index
        return ha_df

    # === Plot ===
    def add_fig(self):
        self.fig = go.Figure()

        df = self.df_ha.copy()
        df.reset_index(drop=True, inplace=True)

        title = "Smoothed Heiken Ashi" if self.smooth else "Heiken Ashi"

        # === HA Candles ===
        self.fig.add_trace(
            go.Candlestick(
                x=df['Date'],
                open=df['HA_Open'],
                high=df['HA_High'],
                low=df['HA_Low'],
                close=df['HA_Close'],
                name=title,
                showlegend=False,
                opacity=0.7,
                decreasing_line_color='indianred',
                increasing_line_color='limegreen',
            )
        )

        # === EMA Lines ===
        if self.show_emas:
            self.fig.add_trace(
                go.Scatter(
                    x=df['Date'],
                    y=df['EMA_HA_High'],
                    showlegend=False,
                    mode='lines',
                    name=f'EMA HA High ({self.ema_high_length})',
                    line=dict(color='orange', width=1.5),
                )
            )

            self.fig.add_trace(
                go.Scatter(
                    x=df['Date'],
                    y=df['EMA_HA_Low'],
                    showlegend=False,
                    mode='lines',
                    name=f'EMA HA Low ({self.ema_low_length})',
                    line=dict(color='deepskyblue', width=1.5),
#                    fill='tonexty',              # Füllt den Bereich bis zur vorherigen Linie
#                    fillcolor='rgba(128, 128, 128, 0.2)'  # Farbe der Füllung (mit Transparenz)
                )
            )
