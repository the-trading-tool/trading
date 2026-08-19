"""Kandidaten-Trichter — Streamlit-Seite.

Eine Seite statt fuenf: Vorfilter, Sektorlage, Relativstaerke und Signal liefen
bisher ueber Dashboard, Rotation, All Assets und den Own-Trades-Tab verteilt.
Hier haengen sie in einer Kette, jeder Schritt zeigt, wieviel er wegnimmt, und
am Ende steht eine Liste, die kurz genug fuer die Einzelanalyse im Asset Viewer
ist.

Die Vorgaben sind eine Startaufstellung, kein Urteil — jeder Schritt laesst sich
abschalten oder verschieben. Gerechnet wird in :mod:`tradinglib.candidates`.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote

import pandas as pd
import streamlit as st

from tradinglib import candidates as cand
from tradinglib import system_config as sysconf
from tradinglib.i18n import t

logger = logging.getLogger(__name__)

# Universen, die im Auswahlfeld oben stehen; der Rest aus yf_tickers folgt.
_PREFERRED = ['^SPX', '^NDX', '^DJI', '^GDAXI', '^MDAXI', '^SDAXI', '^TECDAX',
              '^STOXX50E', '^FTSE', '^IBEX', '^SSMI', '^RUT', '^N225', '^HSI']

_STATE_KEY = '_cand_result'


def _rank_label(col: str) -> str:
    """Lesbarer Name einer Kennzahl; faellt auf den Spaltennamen zurueck."""
    key = f'cand.rank_{col}'
    txt = t(key)
    return col if txt == key else txt


def _viewer_url(ticker: str) -> str:
    """Asset-Viewer-Link mit aktivem Detail-Tab, relativ zur eigenen Herkunft."""
    return f"/?asset=true&symbol={quote(str(ticker))}&details=true"


class CandidatesPage:
    def __init__(self, username: str = 'admin', db_path: str = 'database'):
        self.username = username
        self.db_path = db_path
        self.sys_config = sysconf.SystemConfig(username=username)

    # ── Seite ────────────────────────────────────────────────────────────────
    def render(self):
        st.title(t('cand.title'))
        st.caption(t('cand.subtitle'))

        opt = cand.settings(self.username)
        opt = self._filter_form(opt)
        if opt is None:                      # noch nichts angefordert
            self._show_cached()
            self._method()
            return

        sig = json.dumps(opt, sort_keys=True, default=str)
        cached = st.session_state.get(_STATE_KEY)
        if not (cached and cached.get('sig') == sig):
            with st.spinner(t('cand.computing')):
                try:
                    df, steps = cand.find(self.username, self.db_path, **opt)
                except Exception as exc:
                    logger.exception('candidates page: Lauf fehlgeschlagen')
                    st.error(t('cand.failed', error=exc))
                    return
            st.session_state[_STATE_KEY] = {'sig': sig, 'df': df,
                                           'steps': steps, 'opt': opt}
        self._show_cached()
        self._method()

    # ── Filter ───────────────────────────────────────────────────────────────
    def _filter_form(self, opt: dict):
        """Formular zeichnen. Gibt die Einstellungen zurueck, wenn gerechnet
        werden soll, sonst None."""
        groups = cand.universe_options(self.db_path)
        ordered = [g for g in _PREFERRED if g in groups] + \
                  [g for g in groups if g not in _PREFERRED]
        universe = [u for u in (opt.get('universe') or []) if u in ordered]

        with st.expander(t('cand.filters'), expanded=True):
            with st.form('_cand_form'):
                c1, c2 = st.columns([0.55, 0.45])
                universe = c1.multiselect(t('cand.universe'), ordered,
                                          default=universe,
                                          help=t('cand.universe_help'))
                rank_col = c2.selectbox(
                    t('cand.rank_col'), cand.RANK_COLUMNS,
                    index=(list(cand.RANK_COLUMNS).index(opt['rank_col'])
                           if opt['rank_col'] in cand.RANK_COLUMNS else 0),
                    format_func=_rank_label, help=t('cand.rank_col_help'))
                prefilter = st.text_area(t('cand.prefilter'), opt['prefilter'],
                                         height=68, help=t('cand.prefilter_help'))

                c1, c2, c3 = st.columns(3)
                use_rotation = c1.checkbox(t('cand.use_rotation'),
                                           value=bool(opt['use_rotation']),
                                           help=t('cand.use_rotation_help'))
                min_sector_rsc = c2.number_input(t('cand.min_sector_rsc'),
                                                 value=float(opt['min_sector_rsc']),
                                                 step=0.5, format='%.1f',
                                                 help=t('cand.min_sector_rsc_help'))
                with c3:
                    use_rsc = st.checkbox(t('cand.use_rsc'),
                                          value=bool(opt['use_rsc']),
                                          help=t('cand.use_rsc_help'))
                    min_rsc = st.number_input(t('cand.min_rsc'),
                                              value=float(opt['min_rsc']),
                                              step=1.0, format='%.1f',
                                              disabled=not use_rsc,
                                              help=t('cand.min_rsc_help'))

                c1, c2, c3, c4 = st.columns(4)
                pool_n = c1.number_input(t('cand.pool_n'), 10, 400,
                                         int(opt['pool_n']), step=10,
                                         help=t('cand.pool_n_help'))
                max_per_sector = c2.number_input(t('cand.max_per_sector'), 0, 20,
                                                 int(opt['max_per_sector']),
                                                 help=t('cand.max_per_sector_help'))
                top_n = c3.number_input(t('cand.top_n'), 1, 100,
                                        int(opt['top_n']),
                                        help=t('cand.top_n_help'))
                require_isin = c4.checkbox(t('cand.require_isin'),
                                           value=bool(opt['require_isin']),
                                           help=t('cand.require_isin_help'))

                c1, c2 = st.columns(2)
                with_signal = c1.checkbox(t('cand.with_signal'),
                                          value=bool(opt['with_signal']),
                                          help=t('cand.with_signal_help'))
                only_add = c2.checkbox(t('cand.only_add'),
                                       value=bool(opt['only_add']),
                                       help=t('cand.only_add_help'))

                b1, b2, b3 = st.columns([0.4, 0.3, 0.3])
                run = b1.form_submit_button(t('cand.run'), type='primary',
                                            use_container_width=True)
                save = b2.form_submit_button(t('cand.save'),
                                             use_container_width=True)
                reset = b3.form_submit_button(t('cand.reset'),
                                              use_container_width=True)

        values = {
            'universe': universe, 'prefilter': prefilter,
            'use_rotation': use_rotation, 'min_sector_rsc': float(min_sector_rsc),
            'use_rsc': use_rsc, 'min_rsc': float(min_rsc),
            'require_isin': require_isin,
            'rank_col': rank_col, 'pool_n': int(pool_n),
            'max_per_sector': int(max_per_sector), 'top_n': int(top_n),
            'with_signal': with_signal, 'only_add': only_add,
        }
        if reset:
            cand.save_settings(self.username, dict(cand.DEFAULTS))
            st.session_state.pop(_STATE_KEY, None)
            st.rerun()
        if save:
            cand.save_settings(self.username, values)
            st.success(t('cand.saved'))
        return values if (run or save) else None

    # ── Ergebnis ─────────────────────────────────────────────────────────────
    def _show_cached(self):
        res = st.session_state.get(_STATE_KEY)
        if not res:
            st.info(t('cand.no_run_yet'))
            return
        self._funnel(res['steps'])
        self._table(res['df'], res.get('opt') or {})

    def _funnel(self, steps: list):
        if not steps:
            return
        st.subheader(t('cand.funnel'))
        def label(s):
            # Der Rechenkern liefert Schluessel plus deutsche Beschriftung; die
            # Beschriftung traegt, solange ein Schluessel noch nicht uebersetzt
            # ist (t() gibt sonst den Schluessel selbst zurueck).
            key = f"cand.step_{s.get('key', '')}"
            txt = t(key)
            return s['label'] if txt == key else txt

        tbl = pd.DataFrame({
            t('cand.col_step'): [label(s) for s in steps],
            t('cand.col_before'): [s['before'] for s in steps],
            t('cand.col_after'): [s['after'] for s in steps],
            t('cand.col_removed'): [s['before'] - s['after'] for s in steps],
            t('cand.col_note'): [s['note'] for s in steps],
        })
        st.dataframe(tbl, hide_index=True, use_container_width=True)

    def _table(self, df: pd.DataFrame, opt: dict):
        st.subheader(t('cand.result'))
        if df is None or df.empty:
            st.warning(t('cand.empty'))
            return

        def col(name, digits=None):
            if name not in df.columns:
                return [None] * len(df)
            s = pd.to_numeric(df[name], errors='coerce')
            return s.round(digits) if digits is not None else s

        strength_col = opt.get('rank_col') or 'overallValueTrend'
        if strength_col not in df.columns:
            strength_col = 'overallValueTrend'
        view = pd.DataFrame({
            t('cand.col_ticker'): df['ticker'].astype(str),
            t('cand.col_name'): [str(x)[:38] for x in df.get('longName', '')],
            t('cand.col_sector'): [str(x)[:22] for x in df.get('sector', '')],
            t('cand.col_sector_rsc'): col('sector_rsc', 2),
            t('cand.col_vs_sector'): col('RSC_vs_ETF', 1),
            # Spaltenkopf folgt der gewaehlten Vorsortierung -- sonst stuende
            # dort 'Value Trend', waehrend nach Sharpe sortiert wurde.
            _rank_label(strength_col): col(strength_col, 2),
            t('cand.col_price'): col('close', 2),
            t('cand.col_currency'): [str(x) for x in df.get('currency', '')],
            t('cand.col_signal'): [self._signal_text(a, ty, d) for a, ty, d in zip(
                df.get('signal', pd.Series([None] * len(df))),
                df.get('last_signal', pd.Series([None] * len(df))),
                df.get('last_signal_date', pd.Series([None] * len(df))))],
        })
        # Abgeschaltete Schritte hinterlassen leere Spalten (ohne Rotation gibt
        # es keine Sektorstaerke, ohne Relativstaerke keinen Vorsprung). Die
        # Ticker-Spalte bleibt immer stehen, damit nie eine leere Tabelle
        # entsteht.
        keep = [c for i, c in enumerate(view.columns)
                if i == 0 or not view[c].isna().all()]
        view = view[keep]
        event = st.dataframe(view, use_container_width=True, hide_index=True,
                             on_select='rerun', selection_mode='single-row',
                             key='_cand_table')
        rows = (event.selection.rows if event and getattr(event, 'selection', None) else [])
        selected = str(df['ticker'].iloc[rows[0]]) if rows else ''

        c1, c2, c3 = st.columns([0.3, 0.35, 0.35])
        tickers = [str(x) for x in df['ticker']]
        if c1.button(t('cand.save_monitored'), key='_cand_save_monitored',
                     use_container_width=True):
            self.sys_config.set_value('monitored_assets', ", ".join(tickers))
            st.success(t('cand.saved_monitored', n=len(tickers)))
        if selected:
            c2.link_button(t('cand.open_viewer'), _viewer_url(selected),
                           use_container_width=True)
        c3.caption(t('cand.select_hint'))
        with st.expander(t('cand.ticker_list'), expanded=False):
            st.code(", ".join(tickers), language='text')

    @staticmethod
    def _signal_text(action, last_type, when) -> str:
        """'Einstieg · 2026-08-10', mit ⚡ wenn das Signal auf der letzten Kerze
        steht.

        Die Positionsempfehlung wird sonst weggelassen: sie ist bei einer
        Einstiegsliste fast immer 'halten' und traegt dort nichts bei.
        """
        if not last_type:
            return ''
        ts = pd.to_datetime(when, errors='coerce')
        label = t(f'cand.sig_{last_type}')
        if pd.notna(ts):
            label = f"{label} · {ts:%Y-%m-%d}"
        return f"⚡ {label}" if action == 'add' else label

    # ── Erklaerung ───────────────────────────────────────────────────────────
    def _method(self):
        with st.expander(t('cand.method'), expanded=False):
            st.markdown(t('cand.method_md'))
