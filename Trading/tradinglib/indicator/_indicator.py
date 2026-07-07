import numpy as np
import ta
import pandas as pd
import sys

try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    talib = None
    TALIB_AVAILABLE = False

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

class _Indicator():

    fig = None
    df = None
    is_oszilator = False
    name = '-'
    params = {}  # override in subclass to declare configurable parameters

    # Style params present on every indicator (merged into params dialog + Pine export)
    style_params: dict = {
        'line_color': {
            'type':    'color',
            'default': '',
            'label':   'Line color (empty = indicator default)',
        },
        'line_width': {
            'type':    'int',
            'default': 2,
            'min':     1,
            'max':     5,
            'label':   'Line width',
        },
        'line_style': {
            'type':    'select',
            'default': 'solid',
            'options': ['solid', 'dashed', 'dotted'],
            'label':   'Line style',
        },
    }
    
    def __init__(self, symbol=None, df=pd.DataFrame()):
                """Initialize the indicator with the provided DataFrame and optional symbol/params."""
                self.symbol = symbol
                df = df.copy()
                self.df = df                

    # Whether filled bands break over non-trading periods (weekends/holidays/
    # overnight). True = split the fill at time gaps: always rangebreak-safe and
    # the gaps double as honest markers of non-trading time. False = one
    # continuous polygon: smoother, but the fill can be imprecise at transitions
    # for assets that have bars on otherwise-closed days (e.g. Sunday futures).
    # Set once per render by tiny_chart from config key 'mark_nontrading_times'.
    mark_nontrading_gaps = True

    def segmented_band(self, dates, upper, lower, gap_factor=1.5, mark_gaps=None):
        """Build rangebreak-safe ``fill='toself'`` band polygons.

        Returns ``(xs, ys)`` with ``None``-separated closed sub-polygons (upper
        edge forwards, lower edge backwards), ready for a single
        ``go.Scatter(x=xs, y=ys, fill='toself')``.

        Plotly distorts a filled area that crosses an x-axis ``rangebreak``
        (weekend/holiday/overnight gap) into a ballooning wedge — this affects
        both ``tonexty`` and a single ``toself`` polygon. Splitting the fill at
        the time gaps in the data means no polygon ever spans a collapsed break.
        The rule is asset-agnostic: the split threshold keys off the data's own
        median bar spacing, so 24/7 assets (crypto) stay one continuous band while
        stocks/FX break over non-trading periods. NaN rows are dropped.

        ``mark_gaps`` (default: the class attribute ``mark_nontrading_gaps``, set
        from config) toggles the split. When False the band is one continuous
        polygon — see the caveat on ``mark_nontrading_gaps``.
        """
        frame = pd.DataFrame({
            'd': pd.to_datetime(pd.Series(list(dates)).values, errors='coerce'),
            'u': pd.to_numeric(pd.Series(list(upper)), errors='coerce').values,
            'l': pd.to_numeric(pd.Series(list(lower)), errors='coerce').values,
        }).dropna().reset_index(drop=True)
        if frame.empty:
            return [], []

        if mark_gaps is None:
            mark_gaps = self.mark_nontrading_gaps

        if mark_gaps:
            secs = frame['d'].diff().dt.total_seconds()
            med = secs.median()
            seg = (secs > gap_factor * med).cumsum() if med and med > 0 \
                else pd.Series(0, index=frame.index)
        else:
            # Continuous fill: a vertex sitting on a day the x-axis rangebreak
            # collapses (a weekend bar on a Sun-open future, say) balloons the
            # polygon. Drop weekend bars so no vertex lands on a collapsed day —
            # unless the asset genuinely trades 24/7 (crypto: heavy Sat+Sun
            # coverage), where those bars are real and the axis has no break.
            dow = frame['d'].dt.dayofweek
            is_24_7 = (dow == 5).any() and (dow == 6).any() and (dow >= 5).mean() > 0.15
            if not is_24_7:
                frame = frame[dow < 5].reset_index(drop=True)
                if frame.empty:
                    return [], []
            seg = pd.Series(0, index=frame.index)

        xs, ys = [], []
        for _, g in frame.groupby(seg):
            d = g['d'].tolist()
            xs += d + d[::-1] + [None]
            ys += g['u'].tolist() + g['l'].tolist()[::-1] + [None]
        return xs, ys

    def has_index(self, df):

        return True if df.index.names[0] else False
    
    def data(self): 
        """ 
        This class should master the data we pass it to the __init__ procedure
        as a df and allow to reuse it to draw a figure with plotly
        Mind to import pandas and plotly into your own class
        """
        raise NotImplementedError("You need to implement this class before using it.")
        pass

    def add_fig(self):
        """
        This class should contain figures with all trace specific information to allow to combine it later
        into a new fig object by using the pre-calculated df
        """

        raise NotImplementedError("You need to implement this class before using it.")
        pass

    def _add_hline_outside(self, y, text, line_color='grey', line_dash='dot', line_width=1, font_color='white'):
        """Add hline with label arrow positioned in the left margin.
        Annotations use xref='paper' so tiny_chart.py transfers them without coordinate distortion."""
        self.fig.add_hline(
            y=y,
            line_width=line_width,
            line_dash=line_dash,
            line_color=line_color,
        )
        self.fig.add_annotation(
            x=0.0,
            y=y,
            xref='paper',
            yref='y',
            text=f' {text} ',
            showarrow=False,
            xanchor='left',
            yanchor='middle',
            bgcolor=line_color,
            font=dict(color=font_color, size=13),
            bordercolor=line_color,
            borderpad=3,
            opacity=0.9,
        )


