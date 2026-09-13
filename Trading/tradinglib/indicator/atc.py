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
        # Each bar's channel is fitted over this many trailing bars — never
        # over bars that came later. 252 = one trading year of daily bars.
        'lookback':       {'type': 'int',   'default': 252,  'min': 20, 'max': 2520,
                           'label': 'Channel lookback per bar (bars)'},
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
                 show_zero_mid = False, show_low_mid = False, show_low_top = False,
                 lookback = 252):
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
        try:
            self.lookback = max(20, int(lookback))
        except (TypeError, ValueError):
            self.lookback = 252
        # Today's channel per anchor — what the chart draws as straight lines.
        # The df columns hold something else: one causal value per bar.
        self.channels = {}
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
    

    # ── Causal computation ────────────────────────────────────────────────────
    #
    # A regression channel is not a per-bar series. The old implementation fitted
    # ONE channel over the whole frame and wrote its line into the columns, so the
    # value on a past bar came from a fit that already knew the bars after it.
    # Measured on the stored columns: 98.8-99.6 % of the steps in atc_top_high
    # were exactly straight, and the values deviated from the channel a trader
    # actually had on that day by a median 3-40 %. A sell rule on the upper edge
    # looked dead in the chart (0 signals on ^GDAXI over a year) while firing 16
    # times in daily operation.
    #
    # Now every bar gets the last point of the channel fitted over its own
    # trailing `lookback` bars, with the same anchor rules as before. The value on
    # the most recent bar equals the old one whenever the frame spans the
    # lookback; only history changes, and it changes to what was knowable then.

    @staticmethod
    def _is_daily(index):
        """Daily bars: median spacing between 20 hours and four days."""
        if len(index) < 3:
            return False
        spacing = pd.Series(index).diff().dt.total_seconds().median()
        return bool(spacing) and 20 * 3600 <= spacing <= 4 * 24 * 3600

    def _target_index(self):
        idx = pd.to_datetime(self.df.index, errors='coerce')
        if idx.isna().all() and 'Date' in self.df.columns:
            idx = pd.to_datetime(self.df['Date'], errors='coerce')
        return pd.DatetimeIndex(idx)

    def _history(self, target):
        """Full local daily history for daily charts, otherwise the frame itself.

        A daily chart only holds the displayed period, so its first bars would
        get channels over a handful of bars — and a different number than the
        backtest, which loads years. Intraday and weekly frames are channelled on
        their own bars: that is what an intraday channel means.
        """
        if self.symbol and self._is_daily(target):
            try:
                from tradinglib import four_ps
                hist = four_ps.load_daily(self.symbol)
                if hist is not None and len(hist) >= len(target) // 2:
                    # Older bars cannot reach any bar of the chart through its
                    # trailing window — cut them, it changes no value.
                    first = target.min()
                    if pd.notna(first):
                        pos = int(hist.index.searchsorted(first))
                        hist = hist.iloc[max(0, pos - self.lookback - 5):]
                    return hist[['High', 'Low', 'Close']].astype(float)
            except Exception:
                logger.debug("atc: daily history for %s not loadable", self.symbol,
                             exc_info=True)
        frame = self.df[['High', 'Low', 'Close']].copy()
        frame.index = target
        return frame.astype(float)

    @staticmethod
    def _prefix(y):
        """Prefix sums for O(1) least squares over any window."""
        idx = np.arange(len(y), dtype=float)
        p1 = np.concatenate(([0.0], np.cumsum(y)))
        p2 = np.concatenate(([0.0], np.cumsum(y * y)))
        q = np.concatenate(([0.0], np.cumsum(idx * y)))
        return p1, p2, q

    @staticmethod
    def _sums(end, lengths, p1, p2, q):
        lengths = np.asarray(lengths, dtype=float)
        start = (end - lengths + 1).astype(int)
        sy = p1[end + 1] - p1[start]
        syy = p2[end + 1] - p2[start]
        sxy = (q[end + 1] - q[start]) - start * sy        # x = i - start
        sx = lengths * (lengths - 1) / 2.0
        sxx = (lengths - 1) * lengths * (2 * lengths - 1) / 6.0
        cov = sxy - sx * sy / lengths
        varx = sxx - sx * sx / lengths
        return lengths, sy, syy, sx, cov, varx

    def _slopes_ending_at(self, end, lengths, p1, p2, q):
        lengths, sy, syy, sx, cov, varx = self._sums(end, lengths, p1, p2, q)
        with np.errstate(divide='ignore', invalid='ignore'):
            return np.where(varx > 0, cov / varx, 0.0)

    def _channel_end(self, end, length, p1, p2, q):
        """(mid, stdev) of the least-squares line over `length` bars ending at
        `end`, evaluated at its last bar — what calc_regression_channel() gives."""
        lengths, sy, syy, sx, cov, varx = self._sums(end, [length], p1, p2, q)
        n = lengths[0]
        slope = cov[0] / varx[0] if varx[0] > 0 else 0.0
        intercept = (sy[0] - slope * sx[0]) / n
        mid = intercept + slope * (n - 1)
        ssr = (syy[0] - sy[0] * sy[0] / n) - slope * cov[0]
        return mid, float(np.sqrt(max(ssr, 0.0) / n))

    def _length(self, name, y_win, high_win, low_win, min_len):
        """Regression length for one trailing window — the rules of the old version."""
        wl = len(y_win)
        if name == "high":
            length = wl - int(np.argmax(high_win))
        elif name == "low":
            length = wl - int(np.argmin(low_win))
        else:  # "zero": the length whose slope is closest to zero
            lengths = np.arange(2, wl)
            if len(lengths) == 0:
                length = wl
            else:
                slopes = self._slopes_ending_at(wl - 1, lengths, *self._prefix(y_win))
                length = int(lengths[int(np.argmin(np.abs(slopes)))])
        return max(min_len, min(length, wl))

    def _causal_columns(self, hist, name):
        """Per-bar causal channel values for one anchor."""
        close_t = np.asarray(self.transform_price(hist['Close']), dtype=float)
        high = hist['High'].to_numpy(dtype=float)
        low = hist['Low'].to_numpy(dtype=float)
        n = len(close_t)
        mid = np.full(n, np.nan)
        top = np.full(n, np.nan)
        bot = np.full(n, np.nan)

        # Shift to zero mean before the prefix sums — the channel does not care,
        # and squared prices summed over years would cost precision.
        offset = float(np.nanmean(close_t)) if n else 0.0
        y = close_t - offset

        for end in range(n):
            start = max(0, end - self.lookback + 1)
            wl = end - start + 1
            min_len = max(10, wl // 5)
            if wl < min_len:
                continue
            y_win = y[start:end + 1]
            length = self._length(name, y_win, high[start:end + 1],
                                  low[start:end + 1], min_len)
            if self.use_exp_weight:
                # Weighted fits have no prefix-sum shortcut; slower, same rule.
                try:
                    m, t, b, *_ = self.calc_regression_channel(
                        pd.Series(close_t[start:end + 1]), length)
                    mid[end], top[end], bot[end] = m[-1], t[-1], b[-1]
                except Exception:
                    pass
                continue
            m, sd = self._channel_end(wl - 1, length, *self._prefix(y_win))
            mid[end] = m + offset
            top[end] = mid[end] + self.dev_multi * sd
            bot[end] = mid[end] - self.dev_multi * sd

        mid_p = self.inverse_transform_price(mid)
        top_p = self.inverse_transform_price(top)
        bot_p = self.inverse_transform_price(bot)
        width = top_p - bot_p
        close = hist['Close'].to_numpy(dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            width_pct = np.where(close != 0, width / close * 100.0, np.nan)
        return pd.DataFrame({
            f"atc_mid_{name}": mid_p, f"atc_top_{name}": top_p,
            f"atc_bot_{name}": bot_p, f"atc_width_{name}": width,
            f"atc_width_pct_{name}": width_pct,
        }, index=hist.index)

    def _todays_channel(self, hist, name, target):
        """The straight channel as of the last bar — what the chart draws."""
        window = hist.iloc[-self.lookback:]
        wl = len(window)
        min_len = max(10, wl // 5)
        if wl < min_len:
            return None
        close_t = self.transform_price(window['Close']).astype(float)
        centred = close_t.to_numpy(dtype=float) - float(close_t.mean())
        length = self._length(name, centred, window['High'].to_numpy(dtype=float),
                              window['Low'].to_numpy(dtype=float), min_len)
        mid, top, bot, *_ = self.calc_regression_channel(close_t, length)
        seg = pd.DataFrame({
            'mid': self.inverse_transform_price(mid),
            'top': self.inverse_transform_price(top),
            'bot': self.inverse_transform_price(bot),
        }, index=window.index[-length:])
        # Only what falls inside the chart — drawing beyond it would stretch the
        # shared date axis.
        if len(target) and target.notna().any():
            seg = seg[(seg.index >= target.min()) & (seg.index <= target.max())]
        if seg.empty:
            return None
        close_last = float(window['Close'].iloc[-1])
        width = float(seg['top'].iloc[-1] - seg['bot'].iloc[-1])
        seg.attrs['width'] = width
        seg.attrs['width_pct'] = width / close_last * 100.0 if close_last else np.nan
        return seg

    def data(self):
        """Compute causal channel columns and today's drawable channels."""

        if isinstance(self.df.columns, pd.MultiIndex):
            self.df.columns = self.df.columns.get_level_values(0)

        self.close = self.df["Close"]
        self.high = self.df["High"]
        self.low = self.df["Low"]
        self.close_t = self.transform_price(self.close)

        target = self._target_index()
        try:
            hist = self._history(target)
        except Exception as e:
            logger.error("atc: %s", e)
            return
        if hist.empty:
            return

        for name in self.anchors:
            try:
                columns = self._causal_columns(hist, name)
                if columns.index.equals(target):
                    projected = columns
                else:
                    projected = columns.reindex(target, method='ffill')
                for col in columns.columns:
                    self.df[col] = projected[col].to_numpy()
                seg = self._todays_channel(hist, name, target)
                if seg is not None:
                    self.channels[name] = seg
            except Exception as e:
                logger.error("atc %s: %s", name, e)

        if 'atc_bot_low' in self.df:
            self.df['atc_low'] = self.df['atc_bot_low']
        if 'atc_top_high' in self.df:
            self.df['atc_high'] = self.df['atc_top_high']

    def add_fig(self):
        """Draw today's channels as straight lines, plus the width label.

        The chart shows the channel as it stands on the last bar. The causal
        per-bar columns behind the buy/sell formulas are a different thing — a
        line through those would zig-zag, because each point belongs to a
        different fit.
        """

        self.fig = go.Figure()

        for name, color in zip(self.anchors, self.channel_colors):
            seg = self.channels.get(name)
            if seg is None or seg.empty:
                continue
            try:
                x = seg.index
                if self.draws(name, 'mid'):
                    self.fig.add_trace(go.Scatter(
                        x=x, y=seg['mid'],
                        line=dict(dash='dot', color=color, width=2),
                        opacity=0.7, showlegend=False, name=f'atc_mid_{name}'))
                if self.draws(name, 'top'):
                    self.fig.add_trace(go.Scatter(
                        x=x, y=seg['top'],
                        line=dict(color=color, width=2),
                        opacity=0.7, showlegend=False, name=f'atc_top_{name}'))
                if self.draws(name, 'bot'):
                    self.fig.add_trace(go.Scatter(
                        x=x, y=seg['bot'],
                        line=dict(color=color, width=2),
                        opacity=0.7, showlegend=False, name=f'atc_bot_{name}'))
                self._add_width_label(seg, name, color, slot=self.anchors.index(name))
            except Exception:
                logger.debug("atc: drawing %s failed", name, exc_info=True)

    # Abstand der Beschriftung vom rechten Rand, in Balken. Gestaffelt, damit
    # sich die drei Zahlen nicht ueberlagern, wenn die Kanaele aehnlich breit
    # sind (im Minutenchart lagen sonst drei Labels uebereinander). In Balken
    # statt in Prozent der Kanallaenge, weil die Kanaele unterschiedlich weit
    # zurueckreichen, das Zoomfenster aber immer am rechten Rand endet.
    LABEL_OFFSET_BARS = (4, 11, 18)

    def _add_width_label(self, seg, name, color, slot=0):
        """Kanalbreite als Zahl zwischen die beiden Parallelen schreiben.

        Bewusst als Text-Spur und nicht als Annotation: Annotationen aus
        Overlays uebernimmt tiny_chart mit ``xref='paper'``, ein Datums-x
        wuerde dabei verrutschen. Eine Spur lebt in Datenkoordinaten und wird
        wie jede andere uebertragen.
        """
        if not self.show_width:
            return
        # Der ausgewiesene Wert gilt fuer den letzten Balken: der absolute
        # Abstand der Parallelen ist ueber den Kanal konstant (2 x dev_multi x
        # stdev), der Prozentwert bezieht sich auf den Kurs dieses Balkens.
        width, pct = seg.attrs.get('width'), seg.attrs.get('width_pct')
        if width is None or pct is None or pd.isna(width) or pd.isna(pct):
            return
        # Gesetzt wird die Zahl kurz VOR dem rechten Rand. Direkt am Rand
        # draengen sich die Kurs- und EMA-Fahnen; in der Kanalmitte lag sie
        # dagegen links ausserhalb des Bildes, weil die Kanaele weiter
        # zurueckreichen als das Zoomfenster des Charts.
        offset = self.LABEL_OFFSET_BARS[slot % len(self.LABEL_OFFSET_BARS)]
        pos = seg.iloc[-min(offset + 1, len(seg))]
        # Mittig zwischen die Parallelen -- unabhaengig davon, welche der
        # beiden gerade gezeichnet wird.
        y = (pos['top'] + pos['bot']) / 2.0
        # Unter 1 % zwei Nachkommastellen: im Minutenchart liegen alle drei
        # Kanaele bei "0,3 %", gerundet auf eine Stelle sagt das nichts mehr.
        txt = f'{pct:.2f} %' if abs(pct) < 1 else f'{pct:.1f} %'
        self.fig.add_trace(go.Scatter(
            x=[pos.name], y=[y],
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

