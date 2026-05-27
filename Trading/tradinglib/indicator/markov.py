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
    persistence diagonal) are available in buy/sell expressions:
        (markov_regime == 1)                   → currently Bull
        (markov_bull_persist > 0.75)           → Bull likely to stay Bull
        (markov_stat_bull > 0.50)              → long-run majority is Bull
        (overallValueTrend >= 69) & (markov_regime == 1)
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
    }

    # Palette — muted tints matching Pine Script storyboard
    # hsl(150,38,64)=Bull · hsl(354,48,64)=Bear · hsl(220,14,68)=Sideways
    _PALETTE = {
        1: {'ribbon': 'rgba(132,187,161,0.28)', 'solid': 'rgba(132,187,161,0.70)', 'name': 'Bull'},
        2: {'ribbon': 'rgba(197,127,134,0.28)', 'solid': 'rgba(197,127,134,0.70)', 'name': 'Bear'},
        0: {'ribbon': 'rgba(164,171,183,0.28)', 'solid': 'rgba(164,171,183,0.70)', 'name': 'Sideways'},
    }

    def __init__(self, df, symbol='', lookback=20, bull_pct=5.0, bear_pct=5.0,
                 banner_pos='top_left', matrix_pos='middle_left'):
        self.lookback = int(lookback)
        self.bull_threshold = float(bull_pct) / 100.0
        self.bear_threshold = float(bear_pct) / 100.0
        self.banner_pos = banner_pos if banner_pos in self._POS_MAP else 'top_left'
        self.matrix_pos = matrix_pos if matrix_pos in self._POS_MAP else 'middle_left'
        # Safe fallback until data() runs
        self._P = np.full((3, 3), 1.0 / 3.0)
        self._stat = np.full(3, 1.0 / 3.0)
        super().__init__(df=df, symbol=symbol)
        self.data()

    # ── Core computation ────────────────────────────────────────────────────

    def data(self):
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
                        df.reset_index(inplace=True)
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

            # Group consecutive same-regime bars into one rect shape each
            block_start = dates[0]
            block_regime = regimes[0]

            for i in range(1, len(dates)):
                if regimes[i] != block_regime:
                    self.fig.add_shape(
                        type='rect',
                        xref='x', yref='paper',
                        x0=block_start, x1=dates[i],
                        y0=0, y1=1,
                        fillcolor=self._PALETTE[block_regime]['ribbon'],
                        line_width=0,
                        layer='below',
                    )
                    block_start = dates[i]
                    block_regime = regimes[i]

            # Final block (always the most recent / right-most)
            self.fig.add_shape(
                type='rect',
                xref='x', yref='paper',
                x0=block_start, x1=dates[-1],
                y0=0, y1=1,
                fillcolor=self._PALETTE[block_regime]['ribbon'],
                line_width=0,
                layer='below',
            )

            # Invisible scatter — provides regime name on bar hover
            self.fig.add_trace(
                go.Scatter(
                    x=df['Date'],
                    y=df['Close'],
                    mode='markers',
                    marker=dict(size=1, opacity=0.0),
                    text=df['markov_regime_name'],
                    hovertemplate='Regime: <b>%{text}</b><extra>Markov</extra>',
                    showlegend=False,
                    name='Markov Regime',
                )
            )

            # ── Regime banner ────────────────────────────────────────────────
            current_regime = int(df['markov_regime'].iloc[-1])
            arrow = '▲' if current_regime == 1 else '▼' if current_regime == 2 else '─'
            bx, by, bxa, bya = self._POS_MAP[self.banner_pos]
            self.fig.add_annotation(
                x=bx, y=by,
                xref='paper', yref='paper',
                xanchor=bxa, yanchor=bya,
                text=f'<b>{arrow} {self._PALETTE[current_regime]["name"].upper()}</b>',
                showarrow=False,
                bgcolor=self._PALETTE[current_regime]['solid'],
                bordercolor='rgba(255,255,255,0.35)',
                borderwidth=1,
                borderpad=4,
                font=dict(color='white', size=13),
                align='left',
            )

            # ── Transition matrix ─────────────────────────────────────────────
            # Display order: Bull / Bear / Sideways (internal indices: 1, 2, 0)
            _disp = [1, 2, 0]
            _names = {1: 'BULL', 2: 'BEAR', 0: 'SIDE'}

            col_hdr = '  '.join(f'{_names[c]:>5}' for c in _disp)
            lines = [
                '<b>Markov 3×3</b>',
                f'→      {col_hdr}',
            ]
            for r in _disp:
                cells = []
                for c in _disp:
                    pct_str = f'{self._P[r, c] * 100:.0f}%'
                    if r == c:
                        cells.append(f'[{pct_str:>4s}]')  # diagonal — persistence
                    else:
                        cells.append(f'  {pct_str:>4s} ')  # off-diagonal
                lines.append(f'{_names[r]}  {"  ".join(cells)}')
            lines.append('─' * 26)
            stat_parts = '  '.join(
                f'{_names[i]}:{self._stat[i] * 100:.0f}%' for i in _disp
            )
            lines.append(f'∞  {stat_parts}')

            mx, my, mxa, mya = self._POS_MAP[self.matrix_pos]
            self.fig.add_annotation(
                x=mx, y=my,
                xref='paper', yref='paper',
                xanchor=mxa, yanchor=mya,
                text='<br>'.join(lines),
                showarrow=False,
                bgcolor='rgba(12,18,12,0.88)',
                bordercolor='rgba(63,222,126,0.45)',
                borderwidth=1,
                borderpad=6,
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

    def get_current_regime(self) -> dict:
        """Current regime and statistics as a plain dict.

        Usage:
            info = fd.markov.get_current_regime()
            # info['name']          → 'Bull' | 'Bear' | 'Sideways'
            # info['bull_persist']  → P(Bull→Bull), e.g. 0.82
            # info['stat_bull']     → long-run Bull fraction, e.g. 0.54
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
            }
        except Exception:
            return {}
