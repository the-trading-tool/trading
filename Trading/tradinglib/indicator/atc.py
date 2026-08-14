import sys
import logging
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from sklearn.linear_model import LinearRegression
from typing import Tuple

logger = logging.getLogger(__name__)

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

class Atc(_indicator._Indicator):

    is_oszilator = False
    name = 'Auto Trend Channels'

    params = {
        'dev_multi':      {'type': 'float', 'default': 2.0,  'label': 'Channel deviation multiplier'},
        'use_gog_scale':  {'type': 'bool',  'default': False, 'label': 'Log scale'},
        'use_exp_weight': {'type': 'bool',  'default': False, 'label': 'Exponential weighting'},
        'show_width':     {'type': 'bool',  'default': True,  'label': 'Show channel width'},
        # Optional lines. Each channel always draws the line its anchor sits on
        # (see ANCHOR_LINE) — nine lines at once made the chart unreadable and
        # pushed channel edges far outside the price range.
        'show_high_mid':  {'type': 'bool',  'default': False, 'label': 'High channel: middle line'},
        'show_high_bot':  {'type': 'bool',  'default': False, 'label': 'High channel: lower line'},
        'show_zero_mid':  {'type': 'bool',  'default': False, 'label': 'Zero channel: middle line'},
        'show_low_mid':   {'type': 'bool',  'default': False, 'label': 'Low channel: middle line'},
        'show_low_top':   {'type': 'bool',  'default': False, 'label': 'Low channel: upper line'},
    }

    # Line each anchor is defined by — always drawn, never switchable. The high
    # channel is anchored at the highest high (its upper edge), the low channel
    # at the lowest low (its lower edge); the flat channel is only meaningful as
    # a corridor, so it keeps both edges.
    ANCHOR_LINE = {
        'high': ('top',),
        'zero': ('top', 'bot'),
        'low':  ('bot',),
    }

    def __init__(self, df, symbol = "", dev_multi = 2.0, use_gog_scale = False, use_exp_weight = False ,anchors = ["high", "low", "zero"], channel_colors=["darkred", "darkgreen", "darkblue"],
                 show_width = True, show_high_mid = False, show_high_bot = False,
                 show_zero_mid = False, show_low_mid = False, show_low_top = False):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        if df.empty:
            logger.debug("Empty dataframe")
        # Do NOT mutate the passed df — reset_index(inplace=True) would destroy
        # the caller's DatetimeIndex and cause NaN values downstream.
        super().__init__(df=df, symbol=symbol)
        self.use_gog_scale = use_gog_scale
        self.use_exp_weight = use_exp_weight
        self.dev_multi = dev_multi
        self.anchors = anchors
        self.channel_colors = channel_colors
        self.show_width = show_width
        self.optional_lines = {
            ('high', 'mid'): show_high_mid,
            ('high', 'bot'): show_high_bot,
            ('zero', 'mid'): show_zero_mid,
            ('low',  'mid'): show_low_mid,
            ('low',  'top'): show_low_top,
        }
        self.max_bars = len(self.df)   # use len(), not len(df['Date'])
        self.data()

    def draws(self, anchor: str, line: str) -> bool:
        """Is this line of that channel drawn? Anchor line always, rest per config."""
        if line in self.ANCHOR_LINE.get(anchor, ()):
            return True
        return bool(self.optional_lines.get((anchor, line), False))
                
    def transform_price(self, p: pd.Series) -> pd.Series:
        """Transform price data using log scale if enabled."""
        return np.log10(p) if self.use_gog_scale else p

    def inverse_transform_price(self, p: np.ndarray) -> np.ndarray:
        """Inverse transform the price data."""
        return np.power(10, p) if self.use_gog_scale else p

    def exponential_weights(self, n: int, decay: float = 0.90) -> np.ndarray:
        """Return exponential weights for a given length."""
        return decay ** np.arange(n)[::-1]

    def find_bar_highest(self, high_series: pd.Series, max_bars: int) -> int:
        """Find the bar length since the highest high."""
        window = high_series[-max_bars:]
        idxmax = window.idxmax()
        return len(high_series) - high_series.index.get_loc(idxmax)

    def find_bar_lowest(self, low_series: pd.Series, max_bars: int) -> int:
        """Find the bar length since the lowest low."""
        window = low_series[-max_bars:]
        idxmin = window.idxmin()
        return len(low_series) - low_series.index.get_loc(idxmin)

    def find_slope_zero(self, series: pd.Series, max_bars: int) -> int:
        """
        Find the length over which the slope is closest to zero.
        Returns the best length.
        """
        best_len = 2
        best_slope = float("inf")
        for L in range(2, min(max_bars, len(series))):
            y = series.iloc[-L:]
            x = np.arange(L).reshape(-1, 1)
            model = LinearRegression()
            model.fit(x, y.values)
            slope = model.coef_[0]
            if abs(slope) < best_slope:
                best_slope = abs(slope)
                best_len = L
        return best_len
    
    def calc_regression_channel(self, series: pd.Series, length: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float, float, float]:
        """
        Calculate regression channel lines and metrics.
        Returns:
        reg_line, top channel, bottom channel, slope, r², and Pearson r.
        """
        y = series.iloc[-length:]
        x = np.arange(length).reshape(-1, 1)
        model = LinearRegression()
        if self.use_exp_weight:
            weights = self.exponential_weights(length)
            weights /= weights.sum()
            model.fit(x, y.values, sample_weight=weights)
        else:
            model.fit(x, y.values)
        slope = model.coef_[0]
        intercept = model.intercept_

        reg_line = intercept + slope * x.squeeze()
        residuals = y.values - reg_line
        stdev = np.std(residuals)
        r_val = np.corrcoef(np.arange(length), y.values)[0, 1]
        r2 = r_val ** 2

        return reg_line, reg_line + self.dev_multi * stdev, reg_line - self.dev_multi * stdev, slope, r2, r_val        
    

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""

        if isinstance(self.df.columns, pd.MultiIndex):
            self.df.columns = self.df.columns.get_level_values(0)

        self.close = self.df["Close"]
        self.high = self.df["High"]
        self.low = self.df["Low"]

        self.close_t = self.transform_price(self.close)

        def add_array(values, name):
            """Append array-level plot traces (fill areas, bands) to the figure."""
            new_col = np.full(len(self.df), np.nan)
            new_col[-len(values):] = values
            self.df[name] = new_col

        # Mindestlaenge des Regressionsfensters: mindestens 20 % der sichtbaren
        # Balken, nie unter 10.
        min_length = max(10, len(self.close) // 5)

        for name, color in zip(self.anchors, self.channel_colors):
            if name == "high":
                length = self.find_bar_highest(self.high, self.max_bars)
            elif name == "low":
                length = self.find_bar_lowest(self.low, self.max_bars)
            elif name == "zero":
                length = self.find_slope_zero(self.close_t, self.max_bars)
            # Mindestfenster: sonst legt eine 2-Balken-Regression einen Kanal
            # ohne Aussage an, dessen Raender weit vom Kurs wegkippen.
            length = max(min_length, min(length, len(self.close)))

            try:
                mid, top, bot, slope, r2, r_val = self.calc_regression_channel(self.close_t, length)
                mid_p = self.inverse_transform_price(mid)
                top_p = self.inverse_transform_price(top)
                bot_p = self.inverse_transform_price(bot)
                add_array(mid_p, f"atc_mid_{name}")
                add_array(top_p, f"atc_top_{name}")
                add_array(bot_p, f"atc_bot_{name}")

                # Kanalbreite = Abstand der beiden Parallelen. Absolut in
                # Kurseinheiten und relativ zum Kurs -- erst der Prozentwert
                # ist zwischen Werten vergleichbar (ein DAX-Kanal von 60
                # Punkten ist eng, bei einer 5-Euro-Aktie waere er gewaltig).
                # Beides als Spalte, damit es auch in Buy/Sell-Formeln zur
                # Verfuegung steht.
                #
                # Bezug ist bewusst der KURS und nicht die eigene Mittellinie
                # des Kanals. Die Mittellinien der drei Kanaele liegen weit
                # auseinander -- beim KOSPI 6.063 (high) gegen 8.030 (low) --,
                # und mit je eigenem Nenner bekam der sichtbar schmalere Kanal
                # die groessere Zahl (2.171 Punkte = 35,8 %, gegen 2.846 Punkte
                # = 35,4 %). Ein gemeinsamer Nenner macht die drei Zahlen
                # untereinander vergleichbar und deckt sich mit dem, was man im
                # Chart sieht.
                width = top_p - bot_p
                ref = np.asarray(self.close.values[-len(width):], dtype=float)
                with np.errstate(divide='ignore', invalid='ignore'):
                    width_pct = np.where(ref != 0, width / ref * 100.0, np.nan)
                add_array(width, f"atc_width_{name}")
                add_array(width_pct, f"atc_width_pct_{name}")
            except Exception as e:
                logger.error("%s", e)
                pass

        if 'atc_bot_low' in self.df:
            self.df['atc_low'] = self.df['atc_bot_low']                        
        if 'atc_top_high' in self.df:
            self.df['atc_high'] = self.df['atc_top_high']                        
        
    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""

        self.fig = go.Figure()

        # Build a temporary flat copy for plotting — never mutate self.df in-place.
        try:
            plot_base = self.df.reset_index()   # returns NEW df, no inplace
        except Exception:
            plot_base = self.df.copy()

        date_col = next((c for c in ['Date', 'Datetime', 'timestamp'] if c in plot_base.columns), None)
        if date_col is None:
            return

        for name, color in zip(self.anchors, self.channel_colors):

            try:
                top_col = f'atc_top_{name}'
                mid_col = f'atc_mid_{name}'
                bot_col = f'atc_bot_{name}'

                ref_col = top_col if top_col in plot_base.columns else mid_col
                if ref_col not in plot_base.columns:
                    continue
                plot_df = plot_base[plot_base[ref_col].notna()]
                if plot_df.empty:
                    continue

                if self.draws(name, 'mid') and mid_col in plot_df.columns:
                    self.fig.add_trace(go.Scatter(
                        x=plot_df[date_col],
                        y=plot_df[mid_col],
                        line=dict(dash='dot', color=color, width=2),
                        opacity=0.7,
                        showlegend=False,
                        name=mid_col)
                    )

                if self.draws(name, 'top') and top_col in plot_df.columns:
                    self.fig.add_trace(go.Scatter(
                        x=plot_df[date_col],
                        y=plot_df[top_col],
                        line=dict(color=color, width=2),
                        opacity=0.7,
                        showlegend=False,
                        name=top_col)
                    )

                if self.draws(name, 'bot') and bot_col in plot_df.columns:
                    self.fig.add_trace(go.Scatter(
                        x=plot_df[date_col],
                        y=plot_df[bot_col],
                        line=dict(color=color, width=2),
                        opacity=0.7,
                        showlegend=False,
                        name=bot_col)
                    )

                self._add_width_label(plot_df, date_col, name, color,
                                      slot=self.anchors.index(name))

            except Exception:
                pass

    # Abstand der Beschriftung vom rechten Rand, in Balken. Gestaffelt, damit
    # sich die drei Zahlen nicht ueberlagern, wenn die Kanaele aehnlich breit
    # sind (im Minutenchart lagen sonst drei Labels uebereinander). In Balken
    # statt in Prozent der Kanallaenge, weil die Kanaele unterschiedlich weit
    # zurueckreichen, das Zoomfenster aber immer am rechten Rand endet.
    LABEL_OFFSET_BARS = (4, 11, 18)

    def _add_width_label(self, plot_df, date_col, name, color, slot=0):
        """Kanalbreite als Zahl zwischen die beiden Parallelen schreiben.

        Bewusst als Text-Spur und nicht als Annotation: Annotationen aus
        Overlays uebernimmt tiny_chart mit ``xref='paper'``, ein Datums-x
        wuerde dabei verrutschen. Eine Spur lebt in Datenkoordinaten und wird
        wie jede andere uebertragen.
        """
        if not self.show_width:
            return
        wcol, pcol = f'atc_width_{name}', f'atc_width_pct_{name}'
        top_col, bot_col = f'atc_top_{name}', f'atc_bot_{name}'
        if not {wcol, pcol, top_col, bot_col} <= set(plot_df.columns):
            return
        # Der ausgewiesene Wert gilt fuer den letzten Balken: der absolute
        # Abstand der Parallelen ist ueber den Kanal konstant (2 x dev_multi x
        # stdev), der Prozentwert aber nicht -- er bezieht sich auf die
        # Mittellinie, und die wandert. Also immer den aktuellen Rand nehmen.
        last = plot_df.iloc[-1]
        width, pct = last[wcol], last[pcol]
        if pd.isna(width) or pd.isna(pct):
            return
        # Gesetzt wird die Zahl kurz VOR dem rechten Rand. Direkt am Rand
        # draengen sich die Kurs- und EMA-Fahnen; in der Kanalmitte lag sie
        # dagegen links ausserhalb des Bildes, weil die Kanaele weiter
        # zurueckreichen als das Zoomfenster des Charts.
        offset = self.LABEL_OFFSET_BARS[slot % len(self.LABEL_OFFSET_BARS)]
        pos = plot_df.iloc[-min(offset + 1, len(plot_df))]
        # Mittig zwischen die Parallelen -- unabhaengig davon, welche der
        # beiden gerade gezeichnet wird.
        y = (pos[top_col] + pos[bot_col]) / 2.0
        # Unter 1 % zwei Nachkommastellen: im Minutenchart liegen alle drei
        # Kanaele bei "0,3 %", gerundet auf eine Stelle sagt das nichts mehr.
        txt = f'{pct:.2f} %' if abs(pct) < 1 else f'{pct:.1f} %'
        self.fig.add_trace(go.Scatter(
            x=[pos[date_col]], y=[y],
            mode='text',
            text=[txt],
            textposition='middle center',
            # Fett und groesser, dazu ein Kontrastschatten: die Zahl steht
            # ueber Kerzen und gefuellten Baendern, ohne Absetzung war sie dort
            # kaum zu lesen. 'auto' waehlt die Schattenfarbe passend zum
            # Hintergrund und traegt damit auch den Dunkelmodus mit.
            textfont=dict(color=color, size=15, weight='bold', shadow='auto'),
            hoverinfo='text',
            hovertext=f'{name}: {width:,.2f} ({pct:.2f} %)',
            showlegend=False,
            name=f'atc_width_{name}_label')
        )


#            trend = "Up" if slope > 0.01 else "Down" if slope < -0.01 else "Flat"


        """

    channel_info.append({
        "anchor": name,
        "length": length,
        "slope": slope,
        "r2": r2,
        "r": r_val,
        "last_mid": last_mid,
        "last_top": top_plot[-1],
        "last_bot": bot_plot[-1],
        "trend": trend,
        "color": color,
        "x_pos": xvals[-1]
    })


# Confluence and current trend check
current_condition = ""
confluence_detected = False
if channel_info:
    mids = [c["last_mid"] for c in channel_info]
    avg_mid = np.mean(mids)
    stdev_mids = np.std(mids)
    threshold = 0.5 * stdev_mids  # Confluence threshold
    if np.max(mids) - np.min(mids) < threshold:
        confluence_detected = True
        x_confl = max(c["x_pos"] for c in channel_info)
        ax.annotate("Confluence", xy=(x_confl, avg_mid),
                    xytext=(-100, -30), textcoords="offset points",
                    arrowprops=dict(arrowstyle="->", color="yellow"),
                    color="yellow", fontsize=9, fontweight="bold")

    current_price = close.iloc[-1]
    tops = [c["last_top"] for c in channel_info]
    bots = [c["last_bot"] for c in channel_info]
    if current_price > max(tops):
        current_condition = "Overextended Uptrend"
    elif current_price < min(bots):
        current_condition = "Oversold Downtrend"
    else:
        current_condition = "Within Channels"
        """            

