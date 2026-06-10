import numpy as np
import sys
import pandas as pd
import plotly.graph_objects as go
import logging

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    pass

from tradinglib.indicator import _indicator

logger = logging.getLogger(__name__)


class Markov(_indicator._Indicator):
    """Markov Regime Detection — Bull / Bear / Sideways.

    Computes a rolling log-return regime label per bar, builds the 3x3
    transition matrix from visible history, and derives the stationary
    (long-run equilibrium) distribution via matrix power iteration.

    Regime codes stored in df['markov_regime']:
        1 = Bull, 2 = Bear, 0 = Sideways

    All scalar columns (transition probabilities, stationary distribution,
    persistence diagonal, 3-bar forecast) are available in buy/sell expressions:
        (markov_regime == 1)                   → currently Bull
        (markov_bull_persist > 0.75)           → Bull likely to stay Bull
        (markov_stat_bull > 0.50)              → long-run majority is Bull
        (markov_fc1_bull > 0.60)               → Bull most likely at t+1
        (markov_fc3_bear > markov_fc3_bull)    → Bear more likely than Bull at t+3
        (overallValueTrend >= 69) & (markov_regime == 1)

    Forecast columns `markov_fc{h}_{regime}` (h=1..3, regime=bull|bear|side)
    project the current regime forward via the transition matrix
    (v·P, v·P², v·P³) — the expected regime distribution for the next
    three bars. Both the on-chart annotation and `get_forecast_table()`
    additionally show a leading row "∞" with the long-run/stationary
    distribution as a baseline, so the near-term projection can be read
    against where the regime mix settles in equilibrium. Toggle the
    on-chart table via `show_forecast`/`forecast_pos`, or pull it directly
    with `get_forecast_table()`.
    """

    is_oszilator = False
    name = 'Markov Regime'

    _POSITIONS = [
        'top_left', 'top_center', 'top_right',
        'middle_left', 'middle_center', 'middle_right',
        'bottom_left', 'bottom_center', 'bottom_right',
    ]

    # Maps position name → (x, y, xanchor, yanchor) in Plotly paper coords
    _POS_MAP = {
        'top_left':      (0.01, 0.99, 'left',   'top'),
        'top_center':    (0.50, 0.99, 'center', 'top'),
        'top_right':     (0.99, 0.99, 'right',  'top'),
        'middle_left':   (0.01, 0.50, 'left',   'middle'),
        'middle_center': (0.50, 0.50, 'center', 'middle'),
        'middle_right':  (0.99, 0.50, 'right',  'middle'),
        'bottom_left':   (0.01, 0.02, 'left',   'bottom'),
        'bottom_center': (0.50, 0.02, 'center', 'bottom'),
        'bottom_right':  (0.99, 0.02, 'right',  'bottom'),
    }

    params = {
        'lookback': {
            'type': 'int', 'default': 20, 'min': 5, 'max': 250,
            'label': 'Lookback (Bars)',
        },
        'bull_pct': {
            'type': 'float', 'default': 5.0, 'min': 0.1, 'max': 50.0,
            'label': 'Bull-Schwelle (%)',
        },
        'bear_pct': {
            'type': 'float', 'default': 5.0, 'min': 0.1, 'max': 50.0,
            'label': 'Bear-Schwelle (%)',
        },
        'show_ribbon': {
            'type': 'bool', 'default': True,
            'label': 'Regime-Ribbon anzeigen',
        },
        'show_banner': {
            'type': 'bool', 'default': True,
            'label': 'Regime-Banner anzeigen',
        },
        'show_matrix': {
            'type': 'bool', 'default': True,
            'label': 'Übergangsmatrix anzeigen',
        },
        'show_stationary': {
            'type': 'bool', 'default': True,
            'label': 'Stationäre Verteilung (∞) anzeigen',
        },
        'banner_pos': {
            'type': 'select', 'default': 'top_left',
            'options': _POSITIONS,
            'label': 'Banner-Position',
        },
        'matrix_pos': {
            'type': 'select', 'default': 'middle_left',
            'options': _POSITIONS,
            'label': 'Matrix-Position',
        },
        'stationary_pos': {
            'type': 'select', 'default': 'bottom_right',
            'options': _POSITIONS,
            'label': 'Stationär-Position',
        },
        'show_forecast': {
            'type': 'bool', 'default': True,
            'label': 'Prognose (nächste 3 Bars) anzeigen',
        },
        'forecast_pos': {
            'type': 'select', 'default': 'top_right',
            'options': _POSITIONS,
            'label': 'Prognose-Position',
        },
    }

    # Palette — muted tints matching Pine Script storyboard
    # hsl(150,38,64)=Bull · hsl(354,48,64)=Bear · hsl(220,14,68)=Sideways
    _PALETTE = {
        1: {'ribbon': 'rgba(132,187,161,0.28)', 'solid': 'rgba(132,187,161,0.70)', 'name': 'Bull'},
        2: {'ribbon': 'rgba(197,127,134,0.28)', 'solid': 'rgba(197,127,134,0.70)', 'name': 'Bear'},
        0: {'ribbon': 'rgba(164,171,183,0.28)', 'solid': 'rgba(164,171,183,0.70)', 'name': 'Sideways'},
    }

    def __init__(self, df, symbol='', lookback=20, bull_pct=5.0, bear_pct=5.0,
                 show_ribbon=True, show_banner=True, show_matrix=True, show_stationary=True,
                 show_forecast=True,
                 banner_pos='top_left', matrix_pos='middle_left', stationary_pos='bottom_right',
                 forecast_pos='top_right'):
        self.lookback = int(lookback)
        self.bull_threshold = float(bull_pct) / 100.0
        self.bear_threshold = float(bear_pct) / 100.0
        self.show_ribbon = bool(show_ribbon)
        self.show_banner = bool(show_banner)
        self.show_matrix = bool(show_matrix)
        self.show_stationary = bool(show_stationary)
        self.show_forecast = bool(show_forecast)
        self.banner_pos     = banner_pos     if banner_pos     in self._POS_MAP else 'top_left'
        self.matrix_pos     = matrix_pos     if matrix_pos     in self._POS_MAP else 'middle_left'
        self.stationary_pos = stationary_pos if stationary_pos in self._POS_MAP else 'bottom_right'
        self.forecast_pos   = forecast_pos   if forecast_pos   in self._POS_MAP else 'top_right'
        # Safe fallback until data() runs
        self._P = np.full((3, 3), 1.0 / 3.0)
        self._stat = np.full(3, 1.0 / 3.0)
        # _forecast[h] = regime probability distribution h+1 bars ahead
        # (h=0 → t+1, h=1 → t+2, h=2 → t+3); columns follow internal order
        # [Sideways, Bull, Bear].
        self._forecast = np.full((3, 3), 1.0 / 3.0)
        super().__init__(df=df, symbol=symbol)
        self.data()

    # ── Core computation ────────────────────────────────────────────────────

    def data(self):
        """Compute the indicator values and attach them as columns to self.df."""
        df = self.df
        close = df['Close']

        # Rolling log-return over lookback bars (identical to Pine Script)
        log_ret = np.log(close / close.shift(self.lookback))

        regime_raw = np.where(
            log_ret > self.bull_threshold, 1.0,
            np.where(log_ret < -self.bear_threshold, 2.0, 0.0),
        )
        df['markov_regime'] = regime_raw
        # First lookback bars have no valid log-return → NaN
        df.loc[df.index[:self.lookback], 'markov_regime'] = np.nan

        df['markov_regime_name'] = df['markov_regime'].map(
            {0.0: 'Sideways', 1.0: 'Bull', 2.0: 'Bear'}
        )

        # 3x3 transition matrix from consecutive regime pairs (NaN rows skipped)
        r_vals = df['markov_regime'].dropna().astype(int).values
        counts = np.zeros((3, 3), dtype=float)
        for i in range(len(r_vals) - 1):
            counts[r_vals[i], r_vals[i + 1]] += 1

        row_sums = counts.sum(axis=1, keepdims=True)
        P = np.where(row_sums > 0, counts / row_sums, 1.0 / 3.0)
        self._P = P

        # Stationary distribution — matrix power (50 iters → converged for any
        # well-behaved 3x3 stochastic matrix; each row becomes identical)
        M = P.copy()
        for _ in range(49):
            M = M @ P
        self._stat = M[0]  # stat[0]=Sideways, stat[1]=Bull, stat[2]=Bear

        # ── Forecast: regime distribution for the next 3 bars ────────────────
        # Start from the current regime as a one-hot vector and project it
        # forward bar-by-bar via the transition matrix: v·P, v·P², v·P³.
        r_now = df['markov_regime'].dropna()
        cur_regime = int(r_now.iloc[-1]) if len(r_now) else 0
        vec = np.zeros(3)
        vec[cur_regime] = 1.0
        forecast = np.zeros((3, 3))
        for h in range(3):
            vec = vec @ P
            forecast[h] = vec
        self._forecast = forecast

        # ── Scalar columns for buy/sell expressions ──────────────────────────
        # Stationary long-run mix
        df['markov_stat_bull'] = self._stat[1]
        df['markov_stat_bear'] = self._stat[2]
        df['markov_stat_side'] = self._stat[0]

        # Persistence — P(regime stays itself); load-bearing for regime filters
        df['markov_bull_persist'] = P[1, 1]
        df['markov_bear_persist'] = P[2, 2]
        df['markov_side_persist'] = P[0, 0]

        # Full transition matrix as columns (optional, available in expressions)
        _lbl = {0: 'side', 1: 'bull', 2: 'bear'}
        for ri in range(3):
            for ci in range(3):
                df[f'markov_p_{_lbl[ri]}_{_lbl[ci]}'] = P[ri, ci]

        # Forecast columns — probability of each regime h bars ahead, e.g.
        #   markov_fc1_bull → P(Bull at t+1)   markov_fc3_bear → P(Bear at t+3)
        for h in range(3):
            for ri in range(3):
                df[f'markov_fc{h + 1}_{_lbl[ri]}'] = forecast[h, ri]

        self.df = df

    # ── Chart overlay ───────────────────────────────────────────────────────

    def add_fig(self):
        """Regime ribbon as colored background shapes + hover scatter.

        Shapes use yref='paper' so they span the full chart height (row 1)
        regardless of price scale — identical to Pine Script's bgcolor().
        The invisible scatter provides the regime name on hover.
        """
        self.fig = go.Figure()
        try:
            df = self.df.copy()

            # Normalise: ensure Date is a column, not just the index
            if 'Date' not in df.columns:
                if df.index.name == 'Date':
                    df['Date'] = df.index
                else:
                    try:
                        df = df.reset_index()
                    except Exception:
                        pass

            if 'Date' not in df.columns or 'markov_regime' not in df.columns:
                return

            df = df.dropna(subset=['markov_regime', 'Date'])
            if len(df) < 2:
                return

            df['markov_regime'] = df['markov_regime'].astype(int)
            dates = df['Date'].values
            regimes = df['markov_regime'].values

            # ── Regime ribbon ─────────────────────────────────────────────────
            if self.show_ribbon:
                block_start = dates[0]
                block_regime = regimes[0]
                for i in range(1, len(dates)):
                    if regimes[i] != block_regime:
                        self.fig.add_shape(
                            type='rect', xref='x', yref='paper',
                            x0=block_start, x1=dates[i], y0=0, y1=1,
                            fillcolor=self._PALETTE[block_regime]['ribbon'],
                            line_width=0, layer='below',
                        )
                        block_start, block_regime = dates[i], regimes[i]
                self.fig.add_shape(
                    type='rect', xref='x', yref='paper',
                    x0=block_start, x1=dates[-1], y0=0, y1=1,
                    fillcolor=self._PALETTE[block_regime]['ribbon'],
                    line_width=0, layer='below',
                )

            # Invisible scatter — provides regime name on bar hover (always on)
            self.fig.add_trace(
                go.Scatter(
                    x=df['Date'], y=df['Close'],
                    mode='markers', marker=dict(size=1, opacity=0.0),
                    text=df['markov_regime_name'],
                    hovertemplate='Regime: <b>%{text}</b><extra>Markov</extra>',
                    showlegend=False, name='Markov Regime',
                )
            )

            current_regime = int(df['markov_regime'].iloc[-1])

            # ── Regime banner ─────────────────────────────────────────────────
            if self.show_banner:
                arrow = '▲' if current_regime == 1 else '▼' if current_regime == 2 else '─'
                bx, by, bxa, bya = self._POS_MAP[self.banner_pos]
                self.fig.add_annotation(
                    x=bx, y=by, xref='paper', yref='paper',
                    xanchor=bxa, yanchor=bya,
                    text=f'<b>{arrow} {self._PALETTE[current_regime]["name"].upper()}</b>',
                    showarrow=False,
                    bgcolor=self._PALETTE[current_regime]['solid'],
                    bordercolor='rgba(255,255,255,0.35)',
                    borderwidth=1, borderpad=4,
                    font=dict(color='white', size=13),
                    align='left',
                )

            _disp  = [1, 2, 0]
            _names = {1: 'BULL', 2: 'BEAR', 0: 'SIDE'}

            # ── Transition matrix ─────────────────────────────────────────────
            if self.show_matrix:
                col_hdr = '  '.join(f'{_names[c]:>5}' for c in _disp)
                lines = ['<b>Markov 3×3</b>', f'→      {col_hdr}']
                for r in _disp:
                    cells = []
                    for c in _disp:
                        pct = f'{self._P[r, c] * 100:.0f}%'
                        cells.append(f'[{pct:>4s}]' if r == c else f'  {pct:>4s} ')
                    lines.append(f'{_names[r]}  {"  ".join(cells)}')
                # ∞-Zeile im Matrix-Block einbetten, wenn beide aktiv sind
                if self.show_stationary:
                    stat_parts = '  '.join(
                        f'{_names[i]}:{self._stat[i] * 100:>3.0f}%' for i in _disp
                    )
                    lines += ['─' * 26, f'∞  {stat_parts}']
                mx, my, mxa, mya = self._POS_MAP[self.matrix_pos]
                self.fig.add_annotation(
                    x=mx, y=my, xref='paper', yref='paper',
                    xanchor=mxa, yanchor=mya,
                    text='<br>'.join(lines),
                    showarrow=False,
                    bgcolor='rgba(12,18,12,0.88)',
                    bordercolor='rgba(63,222,126,0.45)',
                    borderwidth=1, borderpad=6,
                    font=dict(color='white', size=11, family='Courier New, monospace'),
                    align='left',
                )

            # ── Prognose: Regimeverteilung für die nächsten 3 Bars ────────────
            if self.show_forecast:
                # Right-justify the percentages (":>3.0f") so every "BULL:/BEAR:/SIDE:"
                # column starts at the same character position across all rows, and
                # pad the row labels to the same printed width ("∞" + 4 spaces ==
                # "t+1" + 2 spaces == 5 chars) so the data columns line up vertically.
                def _fmt_dist(dist):
                    return '  '.join(
                        f'{_names[i]}:{dist[i] * 100:>3.0f}%' for i in _disp
                    )
                fc_lines = ['<b>Forecast +3 Bars</b>']
                fc_lines.append(f'∞    {_fmt_dist(self._stat)}')
                for h in range(3):
                    fc_lines.append(f't+{h + 1}  {_fmt_dist(self._forecast[h])}')
                fx, fy, fxa, fya = self._POS_MAP[self.forecast_pos]
                self.fig.add_annotation(
                    x=fx, y=fy, xref='paper', yref='paper',
                    xanchor=fxa, yanchor=fya,
                    text='<br>'.join(fc_lines),
                    showarrow=False,
                    bgcolor='rgba(12,18,12,0.88)',
                    bordercolor='rgba(63,222,126,0.45)',
                    borderwidth=1, borderpad=6,
                    font=dict(color='white', size=11, family='Courier New, monospace'),
                    align='left',
                )

            # ── Stationäre Verteilung — eigenständig wenn Matrix aus ──────────
            if self.show_stationary and not self.show_matrix:
                stat_parts = '  '.join(
                    f'{_names[i]}:{self._stat[i] * 100:>3.0f}%' for i in _disp
                )
                sx, sy, sxa, sya = self._POS_MAP[self.stationary_pos]
                self.fig.add_annotation(
                    x=sx, y=sy, xref='paper', yref='paper',
                    xanchor=sxa, yanchor=sya,
                    text=f'<b>∞</b>  {stat_parts}',
                    showarrow=False,
                    bgcolor='rgba(12,18,12,0.88)',
                    bordercolor='rgba(63,222,126,0.45)',
                    borderwidth=1, borderpad=5,
                    font=dict(color='white', size=11, family='Courier New, monospace'),
                    align='left',
                )

        except Exception as e:
            logger.exception("Markov.add_fig error: %s", e)

    # ── Streamlit helper methods ─────────────────────────────────────────────

    def get_matrix_table(self) -> go.Figure:
        """Plotly Table figure with the 3x3 transition matrix.

        Usage in any Streamlit page:
            markov_inst = fd.markov  # after FetchData has loaded 'markov'
            st.plotly_chart(markov_inst.get_matrix_table(), use_container_width=True)
        """
        # Display order: Bull / Bear / Sideways → internal indices 1, 2, 0
        disp_labels = ['Bull', 'Bear', 'Sideways']
        disp_idx = [1, 2, 0]
        diag_fill = {
            1: 'rgba(132,187,161,0.55)',
            2: 'rgba(197,127,134,0.55)',
            0: 'rgba(164,171,183,0.55)',
        }
        dark = 'rgba(22,28,22,0.95)'
        header_colors = ['#aaaaaa', '#84BBA1', '#C57F86', '#A4ABB7']

        row_labels = disp_labels
        col_values = []
        col_fill = []
        for c_pos, c_idx in enumerate(disp_idx):
            col_vals = []
            col_col = []
            for r_pos, r_idx in enumerate(disp_idx):
                col_vals.append(f"{self._P[r_idx, c_idx] * 100:.0f}%")
                col_col.append(diag_fill[r_idx] if r_idx == c_idx else dark)
            col_values.append(col_vals)
            col_fill.append(col_col)

        stat_line = (
            f"∞-Mix:  "
            f"Bull {self._stat[1]*100:.0f}%  "
            f"Bear {self._stat[2]*100:.0f}%  "
            f"Side {self._stat[0]*100:.0f}%"
        )

        fig = go.Figure(go.Table(
            header=dict(
                values=['→'] + disp_labels,
                fill_color=dark,
                font=dict(color=header_colors, size=12),
                align='center',
                height=28,
            ),
            cells=dict(
                values=[[lb for lb in row_labels]] + col_values,
                fill_color=[[dark] * 3] + col_fill,
                font=dict(color='white', size=13),
                align='center',
                height=26,
            ),
        ))
        fig.update_layout(
            title=dict(
                text=f'Markov Transition Matrix   <span style="font-size:11px;color:#888">{stat_line}</span>',
                font=dict(color='#3FDE7E', size=13),
            ),
            height=185,
            margin=dict(l=4, r=4, t=36, b=4),
            paper_bgcolor=dark,
        )
        return fig

    def get_forecast_table(self) -> go.Figure:
        """Plotly Table figure with the 3-bar regime forecast.

        Starts from the current regime as a one-hot vector and projects it
        forward bar-by-bar via the transition matrix (v·P, v·P², v·P³) —
        i.e. the expected regime distribution for t+1 .. t+3. The most
        likely regime per horizon is highlighted.

        An extra leading row "∞" shows the long-run/stationary mix as a
        baseline reference, so the near-term forecast can be compared
        against where the regime probabilities settle in the long run.

        Usage in any Streamlit page:
            markov_inst = fd.markov  # after FetchData has loaded 'markov'
            st.plotly_chart(markov_inst.get_forecast_table(), use_container_width=True)
        """
        disp_labels = ['Bull', 'Bear', 'Sideways']
        disp_idx = [1, 2, 0]
        diag_fill = {
            1: 'rgba(132,187,161,0.55)',
            2: 'rgba(197,127,134,0.55)',
            0: 'rgba(164,171,183,0.55)',
        }
        dark = 'rgba(22,28,22,0.95)'
        header_colors = ['#aaaaaa', '#84BBA1', '#C57F86', '#A4ABB7']

        # Row 0 = "∞" (stationary/long-run baseline), rows 1-3 = t+1 .. t+3
        row_labels = ['∞'] + [f't+{h + 1}' for h in range(3)]
        dists = [self._stat] + [self._forecast[h] for h in range(3)]
        most_likely = [int(np.argmax(d)) for d in dists]

        col_values = []
        col_fill = []
        for c_idx in disp_idx:
            col_vals = []
            col_col = []
            for ri, dist in enumerate(dists):
                col_vals.append(f"{dist[c_idx] * 100:.0f}%")
                col_col.append(diag_fill[c_idx] if most_likely[ri] == c_idx else dark)
            col_values.append(col_vals)
            col_fill.append(col_col)

        fig = go.Figure(go.Table(
            header=dict(
                values=['Horizont'] + disp_labels,
                fill_color=dark,
                font=dict(color=header_colors, size=12),
                align='center',
                height=28,
            ),
            cells=dict(
                values=[row_labels] + col_values,
                fill_color=[[dark] * len(row_labels)] + col_fill,
                font=dict(color='white', size=13),
                align='center',
                height=26,
            ),
        ))
        fig.update_layout(
            title=dict(
                text='Markov Forecast — nächste 3 Bars (+ stationäre Basis "t")',
                font=dict(color='#3FDE7E', size=13),
            ),
            height=186,
            margin=dict(l=4, r=4, t=36, b=4),
            paper_bgcolor=dark,
        )
        return fig

    def get_current_regime(self) -> dict:
        """Current regime and statistics as a plain dict.

        Usage:
            info = fd.markov.get_current_regime()
            # info['name']          → 'Bull' | 'Bear' | 'Sideways'
            # info['bull_persist']  → P(Bull→Bull), e.g. 0.82
            # info['stat_bull']     → long-run Bull fraction, e.g. 0.54
            # info['forecast'][0]   → {'bull':.., 'bear':.., 'side':..} at t+1
            # info['forecast'][2]   → distribution at t+3
        """
        try:
            r = int(self.df['markov_regime'].dropna().iloc[-1])
            return {
                'regime': r,
                'name': self._PALETTE[r]['name'],
                'color': self._PALETTE[r]['solid'],
                'stat_bull': float(self._stat[1]),
                'stat_bear': float(self._stat[2]),
                'stat_side': float(self._stat[0]),
                'bull_persist': float(self._P[1, 1]),
                'bear_persist': float(self._P[2, 2]),
                'side_persist': float(self._P[0, 0]),
                # Forecast — regime probabilities for the next 3 bars.
                # forecast[h] = [P(Sideways), P(Bull), P(Bear)] at t+(h+1)
                'forecast': [
                    {
                        'bull': float(self._forecast[h, 1]),
                        'bear': float(self._forecast[h, 2]),
                        'side': float(self._forecast[h, 0]),
                    }
                    for h in range(3)
                ],
            }
        except Exception:
            return {}
