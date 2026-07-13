import os
import math
from datetime import datetime

import pandas as pd
import streamlit as st

from tradinglib import ticker_tools as tt
from tradinglib.tools import open_db
from tradinglib.i18n import t


class Headlines(tt.TickerTools):

    def __init__(self, df, ticker, data, system_currency='EUR', screen_region_row1=st,
                 screen_region_row2=st, interval='1d', index_name="", compact=False):
        """Set up the key-data view.

        df      : OHLC price DataFrame of the selected chart (Close/Open/High/Low/Date).
        ticker  : asset_info metadata DataFrame (one row) — read via get_ticker_value().
        data    : legacy search DataFrame (kept for backwards compatibility; the signal
                  figures are now loaded directly from the simulation DBs, see
                  _load_signal_row, so this no longer needs to carry the sim columns).
        compact : True → the narrow two-row strip used by the compact / Market-Map
                  overlay. False → the full-width, section-based "Key Data" tab.
        """
        self.df = df
        self.index_name = index_name
        if index_name == "":
            self.index_name = "Index not found"
        self.ticker = ticker
        self.data = data
        self.interval = interval
        self.system_currency = system_currency
        self.screen_region_row1 = screen_region_row1
        self.screen_region_row2 = screen_region_row2
        self.compact = compact
        self._sig = {}
        self._sig_src = None

    # ------------------------------------------------------------------ #
    # Data loading
    # ------------------------------------------------------------------ #
    def _symbol(self):
        """Best-effort extraction of the ticker string from the metadata DataFrame."""
        for col in ('ticker', 'symbol', 'underlyingSymbol'):
            try:
                v = self.ticker[col].iloc[0]
                if isinstance(v, str) and v:
                    return v
            except Exception:
                pass
        # Fall back to a plain string ticker if that is what was passed in.
        return self.ticker if isinstance(self.ticker, str) else ''

    def _load_signal_row(self, symbol):
        """Latest simulation row for *symbol* as a plain dict.

        Primary source is the current-year asset_simulation_.db. Indices, metals,
        FX and crypto are not scored there, so fall back to asset_simulation_all.db
        (which does carry them). Returns {} when nothing is available.
        """
        if not symbol:
            return {}
        for fname in ('asset_simulation_.db', 'asset_simulation_all.db'):
            try:
                path = self.get_path(path='database', file_name=fname)
                if not os.path.exists(path):
                    continue
                conn = open_db(path, readonly=True)
                try:
                    row = pd.read_sql_query(
                        "SELECT * FROM asset_simulation WHERE ticker = ? "
                        "ORDER BY Date DESC LIMIT 1",
                        conn, params=(symbol,))
                finally:
                    conn.close()
                if row is not None and not row.empty:
                    self._sig_src = fname
                    return row.iloc[0].to_dict()
            except Exception:
                continue
        return {}

    def _load_score_history(self, symbol, n=30):
        """Last *n* overallValueTrend / overallTrend values (newest first) from the
        same DB that provided the signal row — used for the week/month direction."""
        if not symbol or not self._sig_src:
            return {}
        try:
            path = self.get_path(path='database', file_name=self._sig_src)
            conn = open_db(path, readonly=True)
            try:
                df = pd.read_sql_query(
                    "SELECT overallValueTrend, overallTrend FROM asset_simulation "
                    "WHERE ticker = ? ORDER BY Date DESC LIMIT ?",
                    conn, params=(symbol, n))
            finally:
                conn.close()
            if df is None or df.empty:
                return {}
            return {
                'ovt': pd.to_numeric(df['overallValueTrend'], errors='coerce').tolist(),
                'overall': pd.to_numeric(df['overallTrend'], errors='coerce').tolist(),
            }
        except Exception:
            return {}

    @staticmethod
    def _dir_delta(hist):
        """'W ↑ · M ↓' direction string from a newest-first value list, or None.

        Compares the latest value against ~5 rows (week) and ~21 rows (month) back.
        """
        if not hist:
            return None
        cur = hist[0]
        if cur is None or cur != cur:
            return None

        def arrow(n):
            if len(hist) > n:
                p = hist[n]
                if p is not None and p == p:
                    d = cur - p
                    return '↑' if d > 0 else '↓' if d < 0 else '→'
            return None

        w, m = arrow(5), arrow(21)
        parts = []
        if w:
            parts.append(f"W {w}")
        if m:
            parts.append(f"M {m}")
        return ' · '.join(parts) or None

    def _load_daily_closes(self, symbol):
        """Daily Close series from yf_<symbol>.db (for period returns). None on miss."""
        if not symbol:
            return None
        try:
            path = self.get_path(path='database', file_name=f'yf_{symbol}.db')
            if not os.path.exists(path):
                return None
            conn = open_db(path, readonly=True)
            try:
                df = pd.read_sql_query("SELECT Date, Close FROM day_data ORDER BY Date", conn)
            finally:
                conn.close()
            if df is None or df.empty:
                return None
            return df
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Value helpers
    # ------------------------------------------------------------------ #
    def _info(self, key, digits=2):
        """Scalar from the asset_info metadata df; None when missing/NaN/zero."""
        try:
            val = self.get_ticker_value(self.ticker, key, digits=digits)
        except Exception:
            return None
        if val is None or val == 0 or val == '':
            return None
        if isinstance(val, float) and val != val:
            return None
        return val

    def _sig_val(self, key, digits=2):
        """Scalar from the loaded simulation row; None when absent/NaN."""
        try:
            v = self._sig.get(key)
        except Exception:
            return None
        if v is None:
            return None
        try:
            f = float(v)
            if f != f:
                return None
            return round(f, digits)
        except (TypeError, ValueError):
            return v

    def _date(self, key):
        """asset_info epoch field → 'YYYY-MM-DD', or None."""
        try:
            v = self.get_ticker_value(self.ticker, key)
            if v and float(v) > 1e8:
                return datetime.utcfromtimestamp(int(float(v))).strftime('%Y-%m-%d')
        except Exception:
            pass
        return None

    @staticmethod
    def _big(v):
        """Human-readable magnitude: 44912956 → '44.9 Mio'."""
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        a = abs(v)
        if a >= 1e9:
            return f"{v / 1e9:.1f} Mrd"
        if a >= 1e6:
            return f"{v / 1e6:.1f} Mio"
        if a >= 1e3:
            return f"{v / 1e3:.1f} k"
        return f"{v:.0f}"

    @staticmethod
    def _pct(v, plus=False, digits=1):
        if v is None:
            return None
        return f"{v:+.{digits}f} %" if plus else f"{v:.{digits}f} %"

    @staticmethod
    def _item(label, value, delta=None, help=None, delta_color='normal'):
        """Build a metric tuple, or None when there is no value to show."""
        if value is None or value == '' or value == 0:
            return None
        return (label, value, delta, help, delta_color)

    def _grid(self, reg, title, items, per_row=6):
        """Render a titled row of st.metric cards, skipping the section if empty."""
        items = [it for it in items if it]
        if not items:
            return
        reg.markdown(
            f"<div style='font-size:15px;font-weight:500;color:var(--text-secondary);"
            f"margin:12px 0 2px'>{title}</div>",
            unsafe_allow_html=True)
        for i in range(0, len(items), per_row):
            chunk = items[i:i + per_row]
            cols = reg.columns(per_row, gap='small')
            for col, (label, value, delta, help, delta_color) in zip(cols, chunk):
                col.metric(label=label, value=value, delta=delta, help=help,
                           delta_color=delta_color)

    # ------------------------------------------------------------------ #
    # Price basics (shared by both layouts, exposed for quick-trade buttons)
    # ------------------------------------------------------------------ #
    def _compute_price_basics(self):
        self.digits = 4 if float(self.df['Close'].iloc[-1]) < 1 else 2
        self.currency = self.get_ticker_value(self.ticker, 'currency')
        self.close_price = round(float(self.df['Close'].iloc[-1]), self.digits)
        self.x_rate = 1
        if self.currency and self.currency != self.system_currency:
            self.x_rate = self.get_exchange_rate(self.currency, self.system_currency)

        self.suggested_investment = None
        self.invest_txt = ''
        if 'log_vola' in self.df.columns and not self.df['log_vola'].isna().all():
            self.suggested_investment = round(
                tt.calculate_investment(self.df['log_vola'].iloc[-1]) / self.x_rate, self.digits)
            self.invest_txt = f"{self.suggested_investment} {self.system_currency}"

        self.delta_pct = None
        if 'daily_returns' in self.df.columns and not math.isnan(self.df['daily_returns'].iloc[-1]):
            self.delta_pct = round(float(self.df['daily_returns'].iloc[-1]), 2)
        elif len(self.df) > 1:
            prev_close = float(self.df['Close'].iloc[-2])
            if prev_close:
                self.delta_pct = round((self.close_price - prev_close) / prev_close * 100, 2)

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def render(self):
        """Render the key-data view (compact strip or full section layout)."""
        if self.df is None or self.df.empty:
            return
        st.markdown(
            "<style>[data-testid=\"stMetricValue\"]{font-size:19px;}</style>",
            unsafe_allow_html=True)

        symbol = self._symbol()
        self._sig = self._load_signal_row(symbol)
        self._compute_price_basics()

        if self.compact:
            self._render_compact()
        else:
            self._render_full(symbol)

    # ------------------------------------------------------------------ #
    # Compact layout — narrow two-row strip (Market-Map / overlay)
    # ------------------------------------------------------------------ #
    def _render_compact(self):
        digits = self.digits
        headline_row1 = self.screen_region_row1.empty()
        (p_date, p_close, p_currency, p_open, p_low, p_high,
         p_target, p_rating, p_asset, p_vol) = headline_row1.columns(10, gap='small')
        headline_row2 = self.screen_region_row2.empty()
        (eps, ptb, div, roa, tpe, beta, l52w, h52w, sor) = headline_row2.columns(9, gap='small')

        p_close.metric(label="Close: ", value=self.close_price,
                       delta=f"{self.delta_pct} %" if self.delta_pct is not None else None,
                       help=self.invest_txt)

        if self.currency:
            p_currency.metric(
                label="Currency", value=self.currency,
                help=f'{self.system_currency}={round(self.x_rate, 3)} - '
                     f'price: {round(self.close_price / self.x_rate, digits)}')

        ovt = self._sig_val('overallValueTrend')
        if ovt is not None:
            p_asset.metric(label="Value", value=ovt,
                           help=f"Range: -100 to 100, Index: {self.index_name}")

        p_date.metric(label="Price date", value=f"{self.df['Date'].iloc[-1]}",
                      help=f"Index: {self.index_name}")
        p_open.metric(label="Open", value=round(float(self.df['Open'].iloc[0]), digits))
        p_low.metric(label="Low", value=round(float(self.df['Low'].min()), digits))
        p_high.metric(label="High", value=round(float(self.df['High'].max()), digits))

        vol = self._info('volume')
        if vol:
            try:
                p_vol.metric(label="Volume", value=f"{round(float(vol) / 1000, 1)} k")
            except (TypeError, ValueError):
                p_vol.metric(label="Volume", value=str(vol))

        low52 = self._info('fiftyTwoWeekLow')
        if low52:
            l52w.metric(label="52 week low", value=low52)
        high52 = self._info('fiftyTwoWeekHigh')
        if high52:
            h52w.metric(label="52 week high", value=high52)

        sortino = self._sig_val('sortino')
        if sortino:
            sor.metric(label="Sortino ratio", value=sortino)

        target = self._info('targetMeanPrice')
        if target and (target - self.close_price) != 0:
            pct = round((target - self.close_price) / target * 100, 2)
            p_target.metric(label="Mean price target", value=target, delta=f"{pct} %",
                            help=f"Target high price: {self._info('targetHighPrice')}")

        b = self._info('beta')
        if b:
            beta.metric(label="Beta", value=b)

        r = self._sig_val('roa', 4)
        if r is not None:
            roa.metric(label="RoA %", value=round(r * 100, 1))

        fe = self._info('forwardEps')
        if fe:
            eps.metric(label="forward EPS", value=fe)

        rec = self._info('recommendationMean')
        if rec:
            p_rating.metric(label=f"Analyst rating: {self.get_ticker_value(self.ticker, 'recommendationKey')}",
                            value=rec)

        dr = self._info('dividendRate')
        if dr:
            div.metric(label="Dividend rate", value=dr)

        p2b = self._info('priceToBook')
        if p2b:
            ptb.metric(label="Price to book", value=p2b)

        fpe = self._info('forwardPE')
        if fpe:
            tpe.metric(label="Forward PE", value=fpe)
        else:
            tpe_v = self._info('trailingPE')
            if tpe_v:
                tpe.metric(label="Trailing PE", value=tpe_v)

    # ------------------------------------------------------------------ #
    # Full layout — section-based, data-driven (Key Data tab)
    # ------------------------------------------------------------------ #
    def _render_full(self, symbol):
        reg = self.screen_region_row1
        quote_type = str(self.get_ticker_value(self.ticker, 'quoteType') or '').upper()
        c = self.close_price

        # Key-Data metrics one size larger than the compact strip.
        reg.markdown(
            "<style>[data-testid=\"stMetricValue\"]{font-size:22px;}"
            "[data-testid=\"stMetricLabel\"]{font-size:15px;}</style>",
            unsafe_allow_html=True)

        # --- Header line -------------------------------------------------
        name = self.get_ticker_value(self.ticker, 'longName') or \
            self.get_ticker_value(self.ticker, 'shortName') or symbol
        meta = ' · '.join([x for x in (symbol, self.currency or '',
                                       quote_type.title() if quote_type else '') if x])
        delta_html = ''
        if self.delta_pct is not None:
            col = 'var(--text-success)' if self.delta_pct >= 0 else 'var(--text-danger)'
            delta_html = f"<span style='font-size:16px;color:{col};margin-left:8px'>{self.delta_pct:+.2f} %</span>"
        reg.markdown(
            f"<div style='display:flex;align-items:baseline;justify-content:space-between;"
            f"gap:12px;margin-bottom:2px'>"
            f"<span><span style='font-size:22px;font-weight:500'>{name}</span> "
            f"<span style='font-size:14px;color:var(--text-muted)'>{meta}</span></span>"
            f"<span style='font-size:22px;font-weight:500'>{self.close_price} {self.currency or ''}"
            f"{delta_html}</span></div>",
            unsafe_allow_html=True)

        # --- Signal (proprietary scores) --------------------------------
        ovt = self._sig_val('overallValueTrend')
        trend_dwm = None
        parts = [self._sig_val('dTrend'), self._sig_val('wkTrend'), self._sig_val('moTrend')]
        if any(p is not None for p in parts):
            trend_dwm = ' / '.join('–' if p is None else f"{p:+.1f}" for p in parts)
        regime = self._sig_val('markov_regime')
        # markov_regime codes (indicator/markov.py): 1=Bull, 2=Bear, 0=Sideways
        regime_key = {1: 'keydata.regime_bull', 2: 'keydata.regime_bear',
                      0: 'keydata.regime_sideways'}.get(
            None if regime is None else int(round(regime)))
        regime_name = t(regime_key) if regime_key else None
        # Week/month direction of OVT and overall trend, derived from sim history.
        hist = self._load_score_history(symbol)
        ovt_dir = self._dir_delta(hist.get('ovt'))
        overall_dir = self._dir_delta(hist.get('overall'))
        signal_items = [
            self._item(t('keydata.ovt'), ovt, delta=ovt_dir, delta_color='off',
                       help=t('keydata.ovt_help')),
            self._item(t('keydata.overall_trend'), self._sig_val('overallTrend'),
                       delta=overall_dir, delta_color='off'),
            self._item(t('keydata.trend_dwm'), trend_dwm),
            self._item(t('keydata.take_profit'), self._sig_val('take_profit', self.digits)),
            self._item(t('keydata.stop_loss'), self._sig_val('stop_loss', self.digits)),
            self._item(t('keydata.support'), self._sig_val('sup_support', self.digits)),
            self._item(t('keydata.resistance'), self._sig_val('sup_resistance', self.digits)),
            self._item(t('keydata.regime'), regime_name),
            self._item(t('keydata.sharpe'), self._sig_val('sharpe')),
            self._item(t('keydata.sortino'), self._sig_val('sortino')),
            self._item(t('keydata.volatility'), self._pct(self._sig_val('vola'))),
            self._item(t('keydata.atr'), self._sig_val('atr', self.digits)),
        ]
        self._grid(reg, t('keydata.signal'), signal_items, per_row=6)

        # --- Price & performance (all asset types) ----------------------
        self._grid(reg, t('keydata.price'), self._price_items(symbol, c), per_row=6)

        # --- Fundamentals — equities only -------------------------------
        if quote_type == 'EQUITY':
            self._grid(reg, t('keydata.valuation'), self._valuation_items(c), per_row=6)
            self._grid(reg, t('keydata.profitability'), self._profitability_items(), per_row=6)

        # --- Risk (beta / short / governance — mostly equities) ---------
        risk_items = [
            self._item(t('keydata.beta'), self._info('beta')),
            self._item(t('keydata.short_float'), self._pct(self._info('shortPercentOfFloat'))),
            self._item(t('keydata.overall_risk'), self._info('overallRisk')),
        ]
        self._grid(reg, t('keydata.risk'), risk_items, per_row=6)

        # --- Dividend (only when it pays) -------------------------------
        self._grid(reg, t('keydata.dividend'), self._dividend_items(), per_row=6)

        # --- Type-specific ----------------------------------------------
        if quote_type == 'ETF':
            self._grid(reg, t('keydata.etf'), self._etf_items(), per_row=6)
        elif quote_type == 'CRYPTOCURRENCY':
            self._grid(reg, t('keydata.crypto'), self._crypto_items(), per_row=6)

    # ------------------------------------------------------------------ #
    # Section builders
    # ------------------------------------------------------------------ #
    def _price_items(self, symbol, c):
        items = []
        # Period returns from local daily history (independent of chart interval).
        dd = self._load_daily_closes(symbol)
        if dd is not None and len(dd) > 1:
            closes = pd.to_numeric(dd['Close'], errors='coerce').dropna()
            if len(closes) > 1:
                last = float(closes.iloc[-1])

                def ret(n):
                    if len(closes) > n:
                        base = float(closes.iloc[-1 - n])
                        if base:
                            return round((last - base) / base * 100, 1)
                    return None

                r1w, r1m, r3m, r1y = ret(5), ret(21), ret(63), ret(252)
                # YTD
                ytd = None
                try:
                    dates = pd.to_datetime(dd['Date'], errors='coerce')
                    yr = datetime.now().year
                    mask = dates.dt.year == yr
                    if mask.any():
                        base = float(pd.to_numeric(dd.loc[mask, 'Close'], errors='coerce').dropna().iloc[0])
                        if base:
                            ytd = round((last - base) / base * 100, 1)
                except Exception:
                    ytd = None

                def _rr(a, b):
                    if a is None and b is None:
                        return None
                    return f"{'–' if a is None else f'{a:+.1f}'} / {'–' if b is None else f'{b:+.1f}'} %"

                items.append(self._item(t('keydata.return_1w_1m'), _rr(r1w, r1m)))
                items.append(self._item(t('keydata.return_3m_1y'), _rr(r3m, r1y)))
                items.append(self._item(t('keydata.ytd'), self._pct(ytd, plus=True)))

        low, high = self._info('fiftyTwoWeekLow'), self._info('fiftyTwoWeekHigh')
        if low and high and high > low:
            pos = round((c - low) / (high - low) * 100)
            items.append(self._item(t('keydata.range_52w', pos=pos), f"{low} – {high}"))

        ma50, ma200 = self._info('fiftyDayAverage'), self._info('twoHundredDayAverage')
        if ma50 or ma200:
            d50 = round((c - ma50) / ma50 * 100, 1) if ma50 else None
            d200 = round((c - ma200) / ma200 * 100, 1) if ma200 else None
            items.append(self._item(
                t('keydata.vs_ma'),
                f"{'–' if d50 is None else f'{d50:+.1f}'} / {'–' if d200 is None else f'{d200:+.1f}'} %"))

        ath = self._info('allTimeHigh')
        if ath:
            dist = round((c - ath) / ath * 100, 1)
            items.append(self._item(t('keydata.ath'), f"{ath}", delta=f"{dist:+.1f} %"))

        relvol = self._sig_val('relvol_ratio')
        if relvol is not None:
            items.append(self._item(t('keydata.rel_volume'), f"{relvol:.2f}×"))
        else:
            vol = self._info('volume')
            if vol:
                items.append(self._item(t('keydata.volume'), self._big(vol)))
        return items

    def _valuation_items(self, c):
        target = self._info('targetMeanPrice')
        target_item = None
        if target:
            up = round((target - c) / c * 100, 1) if c else None
            target_item = self._item(t('keydata.price_target'), f"{target}",
                                     delta=None if up is None else f"{up:+.1f} %",
                                     help=t('keydata.target_high', v=self._info('targetHighPrice')))
        rating = self._info('recommendationMean')
        rating_item = None
        if rating:
            key = self.get_ticker_value(self.ticker, 'recommendationKey')
            rating_item = self._item(t('keydata.rating'), f"{key}",
                                     help=t('keydata.rating_help', mean=rating,
                                            n=self._info('numberOfAnalystOpinions')))
        fpe = self._info('forwardPE')
        tpe = self._info('trailingPE')
        return [
            self._item(t('keydata.pe'),
                       None if (fpe is None and tpe is None) else
                       f"{'–' if fpe is None else fpe} / {'–' if tpe is None else tpe}"),
            self._item(t('keydata.peg'), self._info('trailingPegRatio')),
            self._item(t('keydata.price_book'), self._info('priceToBook')),
            self._item(t('keydata.price_sales'), self._info('priceToSalesTrailing12Months')),
            self._item(t('keydata.ev_ebitda'), self._info('enterpriseToEbitda')),
            target_item,
            rating_item,
        ]

    def _profitability_items(self):
        def pct(key):
            v = self._info(key, digits=4)
            return None if v is None else round(v * 100, 1)

        roa = self._sig_val('roa', 4)
        roa_pct = None if roa is None else round(roa * 100, 1)
        if roa_pct is None:
            roa_pct = pct('returnOnAssets')
        roe = pct('returnOnEquity')
        return [
            self._item(t('keydata.margin_net'), self._pct(pct('profitMargins'))),
            self._item(t('keydata.margin_gross'), self._pct(pct('grossMargins'))),
            self._item(t('keydata.margin_ebitda'), self._pct(pct('ebitdaMargins'))),
            self._item(t('keydata.roe_roa'),
                       None if (roe is None and roa_pct is None) else
                       f"{'–' if roe is None else f'{roe:.1f}'} / "
                       f"{'–' if roa_pct is None else f'{roa_pct:.1f}'} %"),
            self._item(t('keydata.revenue_growth'), self._pct(pct('revenueGrowth'), plus=True)),
            self._item(t('keydata.debt_equity'), self._info('debtToEquity')),
            self._item(t('keydata.current_ratio'), self._info('currentRatio')),
            self._item(t('keydata.free_cashflow'), self._big(self._info('freeCashflow'))),
            self._item(t('keydata.market_cap'), self._big(self._info('marketCap'))),
            self._item(t('keydata.enterprise_value'), self._big(self._info('enterpriseValue'))),
        ]

    def _dividend_items(self):
        rate = self._info('dividendRate')
        yield_ = self._info('dividendYield', digits=4)
        if not rate and not yield_:
            return []
        yld = None if yield_ is None else round(yield_ * 100, 2) if yield_ < 1 else round(yield_, 2)
        payout = self._info('payoutRatio', digits=4)
        return [
            self._item(t('keydata.div_yield'), self._pct(yld, digits=2)),
            self._item(t('keydata.div_rate'), rate),
            self._item(t('keydata.payout'), self._pct(None if payout is None else round(payout * 100, 1))),
            self._item(t('keydata.ex_dividend'), self._date('exDividendDate') or self._date('dividendDate')),
            self._item(t('keydata.div_5y_avg'), self._pct(self._info('fiveYearAvgDividendYield'), digits=2)),
        ]

    def _etf_items(self):
        ytd = self._info('ytdReturn')
        # Fund returns arrive as fractions (0.126) or already-percent (12.6) — normalise.
        if ytd is not None and abs(ytd) < 1:
            ytd = round(ytd * 100, 1)
        exp = self._info('netExpenseRatio')
        if exp is not None and exp < 1:
            exp = round(exp * 100, 2)
        return [
            self._item(t('keydata.fund_volume'), self._big(self._info('netAssets') or self._info('totalAssets'))),
            self._item(t('keydata.nav'), self._info('navPrice')),
            self._item(t('keydata.provider'), self.get_ticker_value(self.ticker, 'fundFamily') or None),
            self._item(t('keydata.ytd'), self._pct(ytd, plus=True)),
            self._item(t('keydata.ter'), self._pct(exp, digits=2)),
        ]

    def _crypto_items(self):
        circ = self._info('circulatingSupply')
        mx = self._info('maxSupply')
        supply = None
        if circ or mx:
            supply = f"{self._big(circ) or '–'} / {self._big(mx) or '–'}"
        return [
            self._item(t('keydata.market_cap'), self._big(self._info('marketCap'))),
            self._item(t('keydata.supply'), supply),
            self._item(t('keydata.volume_24h'), self._big(self._info('volume24Hr'))),
            self._item(t('keydata.all_time_low'), self._info('allTimeLow')),
        ]
