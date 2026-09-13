import sys
import logging

import numpy as np
import pandas as pd
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

logger = logging.getLogger(__name__)


class Obd(_indicator._Indicator):
    """Order Block Detector.

    Order block = the last opposite-coloured candle before an impulse of
    ``ob_periods`` same-coloured candles. The zone is that candle's body
    (or full range with ``use_wicks``); it stays active until a close breaks
    through it (mitigation).

    Every block carries a weight 0..100: impulse size in ATR units, scaled by
    the relative volume of the impulse (``ob_weight_ref`` ATR at average
    volume = 100). Absolute, not a rank, so live and stored values agree.

    Optional flow-imbalance S/R signal (``sr_show``): close near the rolling
    support/resistance of the last ``sr_window`` bars, counter to the volume
    flow. Always computed as ``obd_buy``/``obd_sell``, drawn only on request.
    """

    is_oszilator = False
    name = 'Order Block Detector'

    params = {
        # Order blocks
        'ob_periods':        {'type': 'int',    'default': 3,     'min': 1,  'max': 20,  'label': 'Order block: impulse candles'},
        'ob_threshold':      {'type': 'float',  'default': 1.0,                          'label': 'Order block: min impulse move %'},
        'use_wicks':         {'type': 'bool',   'default': False,                        'label': 'Order block: zone incl. wicks (else body)'},
        'ob_extend':         {'type': 'select', 'default': 'mitigated', 'label': 'Order block: extend',
                              'options': ['mitigated', 'right', 'fixed']},
        'ob_length':         {'type': 'int',    'default': 10,    'min': 1,  'max': 500, 'label': 'Order block: length in bars (extend=fixed)'},
        'ob_show_mitigated': {'type': 'bool',   'default': True,                         'label': 'Order block: show mitigated (faded)'},
        'ob_min_weight':     {'type': 'int',    'default': 0,     'min': 0,  'max': 100, 'label': 'Order block: min weight (0-100)'},
        'ob_weight_ref':     {'type': 'float',  'default': 3.0,                          'label': 'Order block: impulse in ATR for weight 100'},
        'ob_zone_scale':     {'type': 'float',  'default': 1.0,                          'label': 'Order block: zone height factor (wider/narrower)'},
        'ob_weight_height':  {'type': 'bool',   'default': False,                        'label': 'Order block: zone height follows weight'},
        # Flow-imbalance S/R signal (optional)
        'sr_show':           {'type': 'bool',   'default': False,                        'label': 'S/R flow signal: show'},
        'sr_line_width':     {'type': 'int',    'default': 1,     'min': 1,  'max': 5,   'label': 'S/R flow signal: line width'},
        'period':            {'type': 'int',    'default': 21,    'min': 2,  'max': 200, 'label': 'S/R flow signal: flow imbalance period'},
        'sr_window':         {'type': 'int',    'default': 21,    'min': 5,  'max': 200, 'label': 'S/R flow signal: lookback'},
        'sr_zone':           {'type': 'float',  'default': 1.0,                          'label': 'S/R flow signal: proximity zone %'},
    }

    ATR_PERIOD = 14
    VOL_PERIOD = 20

    def __init__(self, df, symbol="", period=21, ob_periods=3, ob_threshold=1.0,
                 use_wicks=False, ob_extend="mitigated", ob_length=10,
                 ob_show_mitigated=True, ob_min_weight=0, ob_weight_ref=3.0,
                 ob_zone_scale=1.0, ob_weight_height=False,
                 sr_show=False, sr_line_width=1, sr_window=21, sr_zone=1.0,
                 sr_min_gap=None):
        """Initialize the indicator with the provided DataFrame and optional symbol/params."""
        super().__init__(df=df, symbol=symbol)
        self.period = int(period)
        self.ob_periods = max(1, int(ob_periods))
        self.ob_threshold = float(ob_threshold)
        self.use_wicks = bool(use_wicks)
        # Legacy values 'left'/'none' from the old select map to a fixed length.
        self.ob_extend = ob_extend if ob_extend in ('mitigated', 'right', 'fixed') else 'fixed'
        self.ob_length = max(1, int(ob_length))
        self.ob_show_mitigated = bool(ob_show_mitigated)
        self.ob_min_weight = float(ob_min_weight)
        self.ob_weight_ref = float(ob_weight_ref) if float(ob_weight_ref) > 0 else 3.0
        self.ob_zone_scale = float(ob_zone_scale) if float(ob_zone_scale) > 0 else 1.0
        self.ob_weight_height = bool(ob_weight_height)
        self.sr_show = bool(sr_show)
        self.sr_line_width = max(1, int(sr_line_width))
        self.sr_window = int(sr_window)
        self.sr_zone = float(sr_zone)
        # sr_min_gap is accepted for stored configs of the old level grid; unused.
        self.zones = []
        self.data()

    # ------------------------------------------------------------------ data

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""
        self.df = self.df.ffill()
        self._flow_signal()
        try:
            self._order_blocks()
        except Exception as e:
            logger.exception("obd: order block detection failed for %s: %s", self.symbol, e)

    def _flow_signal(self):
        """Counter-trend signal at the rolling support/resistance (causal)."""
        df = self.df
        close = pd.to_numeric(df['Close'], errors='coerce')
        volume = pd.to_numeric(df['Volume'], errors='coerce').fillna(0) if 'Volume' in df else pd.Series(0.0, index=df.index)

        df['obd_price_change'] = close.diff()
        df['obd_buy_vol'] = volume.where(df['obd_price_change'] > 0, 0)
        df['obd_sell_vol'] = volume.where(df['obd_price_change'] < 0, 0)
        df['obd_flow_imbalance'] = (df['obd_buy_vol'] - df['obd_sell_vol']).rolling(self.period).sum()
        df['obd_flow_smoothed'] = df['obd_flow_imbalance'].ewm(span=5).mean()

        # Levels as known on each bar: extremes of the trailing window only.
        # The old version built a level grid from the whole frame (look-ahead)
        # which put nearly every close within the proximity zone of some level.
        support = close.rolling(self.sr_window, min_periods=self.sr_window).min()
        resistance = close.rolling(self.sr_window, min_periods=self.sr_window).max()
        df['obd_sr_support'] = support
        df['obd_sr_resistance'] = resistance

        zone = self.sr_zone / 100.0
        near_sup = (close - support).abs() <= support * zone
        near_res = (close - resistance).abs() <= resistance * zone
        # NaN instead of False: non-signal bars stay gaps in the line.
        df['obd_buy'] = close.where(near_sup & (df['obd_flow_imbalance'] < 0))
        df['obd_sell'] = close.where(near_res & (df['obd_flow_imbalance'] > 0))

    def _order_blocks(self):
        """Detect order blocks, weight them and track mitigation causally."""
        df = self.df
        n = len(df)
        o = pd.to_numeric(df['Open'], errors='coerce').to_numpy(dtype=float)
        h = pd.to_numeric(df['High'], errors='coerce').to_numpy(dtype=float)
        l = pd.to_numeric(df['Low'], errors='coerce').to_numpy(dtype=float)
        c = pd.to_numeric(df['Close'], errors='coerce').to_numpy(dtype=float)
        v = (pd.to_numeric(df['Volume'], errors='coerce').to_numpy(dtype=float)
             if 'Volume' in df else np.zeros(n))

        prev_c = np.roll(c, 1)
        prev_c[0] = np.nan
        tr = np.nanmax(np.vstack([h - l, np.abs(h - prev_c), np.abs(l - prev_c)]), axis=0)
        # min_periods=1: a block near the chart start still gets a weight
        # instead of 0 (the ATR warms up within a few bars).
        atr = pd.Series(tr).ewm(alpha=1.0 / self.ATR_PERIOD, adjust=False,
                                min_periods=1).mean().to_numpy()
        # Average volume known before the block candle (no look-ahead).
        vol_avg = pd.Series(v).rolling(self.VOL_PERIOD, min_periods=5).mean().shift(1).to_numpy()

        green = c > o
        red = c < o
        p = self.ob_periods

        cols = {k: np.full(n, np.nan) for k in (
            'obd_ob_bull', 'obd_ob_bear', 'obd_ob_bull_weight', 'obd_ob_bear_weight',
            'obd_bull_top', 'obd_bull_bot', 'obd_bull_weight',
            'obd_bear_top', 'obd_bear_bot', 'obd_bear_weight')}

        zones = []
        active = []  # indices into zones, still unmitigated

        for i in range(n):
            # 1) mitigation of zones known before this bar
            still = []
            for zi in active:
                z = zones[zi]
                broken = c[i] < z['bot'] if z['kind'] == 'bull' else c[i] > z['top']
                if broken:
                    z['end'] = i
                else:
                    still.append(zi)
            active = still

            # 2) new block confirmed on this bar (candle i-p followed by p impulse candles)
            k = i - p
            if k >= 0 and np.isfinite(c[k]) and c[k] != 0:
                move_pct = abs((c[i] - c[k]) / c[k]) * 100.0
                kind = None
                if move_pct >= self.ob_threshold:
                    if red[k] and green[k + 1:i + 1].all():
                        kind = 'bull'
                    elif green[k] and red[k + 1:i + 1].all():
                        kind = 'bear'
                if kind:
                    if self.use_wicks:
                        top, bot = h[k], l[k]
                    else:
                        top, bot = max(o[k], c[k]), min(o[k], c[k])
                    weight = self._weight(abs(c[i] - c[k]), atr[i], v[k + 1:i + 1], vol_avg[k])
                    zones.append({'kind': kind, 'start': k, 'confirm': i, 'end': None,
                                  'top': float(top), 'bot': float(bot), 'weight': weight})
                    active.append(len(zones) - 1)
                    mid = (top + bot) / 2.0
                    cols[f'obd_ob_{kind}'][k] = mid
                    cols[f'obd_ob_{kind}_weight'][k] = weight

            # 3) nearest active zone per side as known on this bar
            for kind in ('bull', 'bear'):
                cand = [zones[zi] for zi in active
                        if zones[zi]['kind'] == kind and zones[zi]['weight'] >= self.ob_min_weight]
                if not cand:
                    continue
                # Active bull zones all sit at or below the close (else they
                # would be mitigated), so the highest one is the nearest; the
                # mirror holds for bear zones.
                if kind == 'bull':
                    z = max(cand, key=lambda z: z['top'])
                else:
                    z = min(cand, key=lambda z: z['bot'])
                cols[f'obd_{kind}_top'][i] = z['top']
                cols[f'obd_{kind}_bot'][i] = z['bot']
                cols[f'obd_{kind}_weight'][i] = z['weight']

        for name, arr in cols.items():
            df[name] = arr
        self.zones = zones

    def _weight(self, move, atr_now, impulse_vol, vol_avg):
        """Weight 0..100: impulse in ATR units, scaled by relative volume."""
        if not np.isfinite(atr_now) or atr_now <= 0:
            return 0.0
        strength = move / atr_now
        rel_vol = np.nan
        if np.isfinite(vol_avg) and vol_avg > 0 and len(impulse_vol):
            rel_vol = np.nanmean(impulse_vol) / vol_avg
        # Indices without volume (or zero) stay neutral.
        vol_factor = np.sqrt(np.clip(rel_vol, 0.5, 2.0)) if np.isfinite(rel_vol) else 1.0
        return float(round(min(strength * vol_factor / self.ob_weight_ref, 1.0) * 100.0, 1))

    # ------------------------------------------------------------------ plot

    def _dates(self):
        if 'Date' in self.df.columns:
            return list(self.df['Date'])
        return list(self.df.index)

    def add_fig(self):
        """Add the indicator traces to the given Plotly figure."""
        self.fig = go.Figure()
        dates = self._dates()
        n = len(dates)
        if n == 0:
            return

        if self.sr_show:
            for col, color, label in (('obd_buy', 'darkcyan', 'S/R flow buy'),
                                      ('obd_sell', 'darkred', 'S/R flow sell')):
                if col in self.df:
                    self.fig.add_trace(go.Scatter(
                        x=dates, y=self.df[col], mode='lines',
                        line=dict(color=color, width=self.sr_line_width),
                        showlegend=False, name=label, opacity=0.8,
                    ))

        mk = {'bull': ([], [], []), 'bear': ([], [], [])}
        for z in self.zones:
            if z['weight'] < self.ob_min_weight:
                continue
            mitigated = z['end'] is not None
            if mitigated and not self.ob_show_mitigated:
                continue

            if self.ob_extend == 'fixed':
                end = min(z['start'] + self.ob_length, n - 1)
            elif self.ob_extend == 'mitigated' and mitigated:
                end = z['end']
            else:
                end = n - 1

            w = z['weight'] / 100.0
            mid = (z['top'] + z['bot']) / 2.0
            half = (z['top'] - z['bot']) / 2.0 * self.ob_zone_scale
            if self.ob_weight_height:
                half *= 0.4 + 0.6 * w
            # Doji blocks have no body; keep them visible as a thin band.
            half = max(half, abs(mid) * 0.0005)

            rgb = '0,150,70' if z['kind'] == 'bull' else '210,40,40'
            fill_alpha = (0.06 + 0.30 * w) * (0.4 if mitigated else 1.0)
            line_alpha = (0.35 + 0.55 * w) * (0.5 if mitigated else 1.0)
            self.fig.add_shape(
                type='rect', xref='x', yref='y',
                x0=dates[z['start']], x1=dates[end],
                y0=mid - half, y1=mid + half,
                fillcolor=f'rgba({rgb},{fill_alpha:.3f})',
                line=dict(color=f'rgba({rgb},{line_alpha:.3f})',
                          width=0.5 + 2.0 * w,
                          dash='dot' if mitigated else 'solid'),
                layer='below',
            )

            xs, ys, texts = mk[z['kind']]
            xs.append(dates[z['start']])
            ys.append(mid)
            state = f"mitigated {dates[z['end']]}" if mitigated else 'active'
            texts.append(f"{'Bullish' if z['kind'] == 'bull' else 'Bearish'} OB · weight {z['weight']:.0f}"
                         f"<br>{z['bot']:.2f} – {z['top']:.2f}<br>{state}")

        for kind, color, symbol in (('bull', 'green', 'triangle-up'), ('bear', 'red', 'triangle-down')):
            xs, ys, texts = mk[kind]
            if not xs:
                continue
            self.fig.add_trace(go.Scatter(
                x=xs, y=ys, mode='markers',
                marker=dict(color=color, size=7, symbol=symbol, opacity=0.8),
                hovertext=texts, hoverinfo='text',
                showlegend=False, name=f'obd_ob_{kind}',
            ))
