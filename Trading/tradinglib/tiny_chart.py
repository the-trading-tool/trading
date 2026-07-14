import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
import pandas as pd
from tradinglib import tools as ts

logger = logging.getLogger(__name__)
from tradinglib import ticker_tools as tt
from tradinglib import graph_tools as gt
from tradinglib.indicator import indicator
from tradinglib.indicator import _indicator
from tradinglib import fetch_data as ft
from tradinglib import system_config as sysconf
from tradinglib.utils import DataUtils, get_display_name
import streamlit as st 
import datetime

class tiny_chart(gt.GraphTools):
    
    period = None
    interval = None
    longname = ''
    ftime_str = ts.Tools().ftime_str
    candle_chart = False
    symbol = None
    fig = None
    ticker = None
    df = pd.DataFrame()
    currency = ''
    scale = True
    ly_high = 0
    ly_low = 0
    range_breaks = False
    long_ma = 200
    short_ma = 20
    trend_length = 15
    trend_end = 0
    show_legend = False
    show_trend = False
    url = ''
    tc_height = 700
    tc_width = 500
    ath = 0
    purchase_price = 0
    x_rate = 1
    dt_breaks = None
    dt_all = None
    dt_obs = None
    calc_ly_hl = False

    def __init__(self, symbol, longname='', period = '1mo', interval = '60m', scale = True, candle_chart = False, range_breaks = False, ly_high = 0, ly_low = 0, url='', tc_width=500, tc_height=700, ath=False, purchase_price=0, x_rate = 1, show_trend=False, trend_length = 15, add_sub_plots=None, max_periods=254, show_legend=False, add_overlays = None,calc_ly_hl=False, username='', zoom = False, exchange = '', pips_select = False, add_current = False, region=st, no_plot_overlays=None, no_plot_oszilators=None):

        if symbol:
            self.username = username
            self.pips_select = pips_select
            self.zoom = zoom
            self.exchange = exchange
            self.x_rate = x_rate
            self.purchase_price = purchase_price
            self.ath = ath
            self.tc_width = tc_width
            self.tc_height = tc_height
            self.url = url
            self.show_trend = show_trend
            self.symbol = symbol
            self.ly_high = ly_high
            self.ly_low = ly_low
            self.scale = scale
            self.longname = longname #(" ".join(re.findall(r"[A-Za-z0-9üäöÜÄÖß]*", longname))).replace("  "," ")
            self.sys_conf = sysconf.SystemConfig(username=self.username)
            self.tz_info = self.sys_conf.get_value("tz_info",default="Europe/Berlin")
            # Whether filled band overlays break over non-trading periods
            # (weekends/holidays). Applied globally to all indicators for this
            # render via the _Indicator class attribute; see segmented_band().
            _indicator._Indicator.mark_nontrading_gaps = bool(
                self.sys_conf.get_value("mark_nontrading_times", True))
            self.buy_query = self.sys_conf.get_value("buy_query", default="(ha_close > ha_open) & (Close > ha_ema_high) & (macd > macd_signal) & (rsi > 50) & (markov_regime < 2)")
            self.sell_query = self.sys_conf.get_value("sell_query", default="(ha_close < ha_open) & (Close < ha_ema_low)")
            self.period = period
            self.interval = interval
            self.range_breaks = range_breaks
            self.trend_length = trend_length
            self.add_sub_plots = add_sub_plots
            self.max_periods = max_periods
            self.show_legend = show_legend
            self.add_overlays = add_overlays
            self.no_plot_overlays = set(no_plot_overlays or [])
            self.no_plot_oszilators = set(no_plot_oszilators or [])
            self.calc_ly_hl = calc_ly_hl             
            self.add_current = add_current
            self.region = region
            self.get_data()
            self.graph()
            
    def _add_hline_outside(self, y, text, line_color='grey', line_dash='dot', line_width=1, row=1):
        """Add hline with label arrow positioned outside the chart on the right."""
        self.fig.add_hline(
            y=y,
            line_width=line_width,
            line_dash=line_dash,
            line_color=line_color,
            row=row, col=1
        )
        yref = 'y' if row == 1 else f'y{row}'
        self.fig.add_annotation(
            x=1.0,
            y=y,
            xref='paper',
            yref=yref,
            text=f' {text} ',
            showarrow=False,
            xanchor='left',
            yanchor='middle',
            bgcolor=line_color,
            font=dict(color='white', size=13),
            bordercolor=line_color,
            borderpad=3,
            opacity=0.9,
        )

    def get_num_rows(self):
        return DataUtils.get_num_rows(self.df)

    def get_last_year_hl(self, symbol):

        now = datetime.datetime.now()
        last_year = now.year - 1

        h = 0
        l = 0
        enddate = datetime.datetime.strptime(f"{last_year}-12-31 00:00:00", "%Y-%m-%d 00:00:00").date().strftime("%Y-%m-%d 00:00:00")
        startdate = datetime.datetime.strptime(f"{last_year}-01-01 00:00:00", "%Y-%m-%d 00:00:00").date().strftime("%Y-%m-%d 00:00:00")

        self.df = self.df.set_index('Date')
        self.ly_low = self.df.loc[startdate:enddate]['Close'].min()
        self.ly_high = self.df.loc[startdate:enddate]['Close'].max()
        self.df = self.df.reset_index()


    def get_data(self):

        indicators = []
        if not self.add_overlays == [] and not self.add_overlays == None:
            indicators.extend(self.add_overlays)
        if not self.add_sub_plots == [] and not self.add_sub_plots == None:
            indicators.extend(self.add_sub_plots)
        self.fd = ft.FetchData(database_path='database', tz_info=self.tz_info, indicators=indicators, buy_query=self.buy_query, sell_query=self.sell_query, sys_conf=self.sys_conf)
        (self.df, self.ticker) = self.fd.fetch_data(self.symbol, period=self.period, interval=self.interval, add_current=self.add_current, max_periods=self.max_periods, pips_select=self.pips_select, region=self.region)

        # Normalize incoming DataFrame: ensure there's a usable 'Date' column
        try:
            df = self.df
            if df is None:
                df = pd.DataFrame()

            # If Date column missing, try common alternatives
            if 'Date' not in df.columns:
                if 'timestamp' in df.columns:
                    try:
                        df['Date'] = pd.to_datetime(df['timestamp'], errors='coerce')
                    except Exception:
                        df['Date'] = df['timestamp']
                elif 'index' in df.columns:
                    # index column may contain mixed types — try to coerce to datetime
                    parsed = pd.to_datetime(df['index'], errors='coerce')
                    if parsed.notna().sum() >= int(0.5 * len(parsed)):
                        df['Date'] = parsed
                    else:
                        # fallback: if index column contains mostly numeric ids, try using actual DataFrame index
                        try:
                            df['Date'] = pd.to_datetime(df.index, errors='coerce')
                        except Exception:
                            df['Date'] = df.index
                else:
                    # last resort: try the DataFrame index
                    try:
                        df['Date'] = pd.to_datetime(df.index, errors='coerce')
                    except Exception:
                        df['Date'] = df.index

            # If Date is datetime-like, format it to the display format used elsewhere
            try:
                df['Date'] = pd.to_datetime(df['Date'], errors='coerce').dt.strftime(self.ftime_str)
            except Exception:
                # keep whatever is there
                pass

            # If Open column missing but there are lowercase columns from other modules, try to coerce
            for col in ['Open', 'High', 'Low', 'Close']:
                if col not in df.columns and col.lower() in df.columns:
                    df[col] = df[col.lower()]

            # finally assign back
            self.df = df
        except Exception:
            # keep original if normalization fails
            pass

        # filter out empty/zero-open rows if Open exists
        try:
            if 'Open' in self.df.columns:
                self.df = self.df[self.df['Open'] != 0]
        except Exception:
            pass

        if self.longname == '':
            try:
                self.longname = get_display_name(self.ticker.iloc[0])
            except Exception:
                pass
        self.df = indicator.trend(self.df, trend_length=self.trend_length, trend_end=self.trend_end )
        if self.df['Close'].count() < 200 and self.df['Close'].count() > 2:
            self.long_ma = self.df['Close'].count() - 1 # Let'S calculate at least a long MA 

        self.df[f'sma{self.long_ma}'] = self.df['Close'].rolling(int(self.long_ma)).mean()
        
        
    def graph(self):

        if self.df is None or self.df.empty:
            self.fig = None
            return

        # Prevent pandas from trying to insert a 'Date' column twice: if the index
        # is named 'Date' and a 'Date' column already exists, clear the index name
        # so reset_index() won't attempt to create a duplicate column.
        try:
            if getattr(self.df.index, 'name', None) == 'Date' and 'Date' in self.df.columns:
                self.df.index.name = None
        except Exception:
            pass
        self.df = self.df.reset_index()
        self.df = self.df.sort_values(['Date'], ascending=True)
        try:
            pct_change = f"<br>Change in period: {round((self.df['Close'].iloc[-1]-self.df['Close'].iloc[0]),2)} ({round((self.df['Close'].iloc[-1]/self.df['Close'].iloc[0]-1)*100,1)}%)"
        except Exception:
            pct_change = ''
            pass
        color = '#555555'
        try:
            if self.long_ma > 0:
                if (self.df[f'sma{self.long_ma}'].iloc[-1] > self.df['Close'].iloc[-1]) or (self.ly_high > 0 and self.ly_high > self.df['Close'].iloc[-1]) or (self.df[f'sma{self.long_ma}'].iloc[-1] > self.df['sma50'].iloc[-1]):
                    # red
                    color = '#b30b0b'
                elif (self.df[f'sma50'].iloc[-1] < self.df[f'sma{self.long_ma}'].iloc[-1]) or (self.df['Close'].iloc[-1]-self.df['Close'].iloc[0] < 0) or (self.df[f'sma20'].iloc[-1] > self.df['Close'].iloc[-1]):
                    # blue
                    color = '#0b0bb3'
                else:
                    # green
                    color = '#0bb30b'
        except Exception:
            pass

        gain = ''
        gcolor = '#555555'
        if self.purchase_price > 0:
            cp = self.df['Close'].iloc[-1] / self.x_rate
            gain_v = round((cp-self.purchase_price)/cp*100,2)
            gain = f"<br>Gain: {gain_v}%  Buy: {round(self.purchase_price,2)} ./. Close: {round(cp,2)} EUR"
            if gain_v < 0:
                gcolor = '#b30b0b'
            else:
                gcolor = '#0bb30b'

        titles = [f"""<a href='{self.url}"{self.symbol}"&details=True' target='_blank' rel='noopener'><span style='color:#555555; font-size:14pt;'>{self.symbol}-{self.longname}</span><span style='color:{color};'>{pct_change}</span><span style='color:{gcolor};'>{gain}</span></a>""", '']

        visible_sub_plots = [n for n in (self.add_sub_plots or []) if n not in self.no_plot_oszilators]

        if visible_sub_plots:

            row_width = []
            num_subplots = len(visible_sub_plots)
            for i in range(num_subplots):
                row_width.append(0.4 / num_subplots)
            row_width.append(0.6)

            self.fig = make_subplots(
                shared_xaxes=True,
                rows = num_subplots+1,
                cols = 1,
                subplot_titles = titles,
                horizontal_spacing=0.05,  # Minimum horizontal spacing
                vertical_spacing=0.05,     # Minimum vertical spacing
                row_width=row_width
            )
        else:
            self.fig = make_subplots(
                rows = 1,
                cols = 1,
                horizontal_spacing=0.03,  # Minimum horizontal spacing
                vertical_spacing=0.03,     # Minimum vertical spacing
                subplot_titles = titles,
            )
            
        if self.scale:
            self.fig.update_layout(
                autosize = False,
                height=self.tc_height,
                width=self.tc_width-50,
                margin=dict(l=110, r=90),
            )
                
        if self.add_overlays:
            try:
                for n in self.add_overlays:
                    if n in self.no_plot_overlays:
                        continue
                    try:
                        logger = logging.getLogger(__name__)
                        logger.debug("tiny_chart: processing overlay %s for %s", n, self.symbol)
                    except Exception:
                        pass

                    try:
                        inst = getattr(self.fd, n, None)
                        if inst is None:
                            logging.getLogger(__name__).warning("tiny_chart: overlay '%s' not found on FetchData for %s", n, self.symbol)
                            continue

                        # call the overlay's add_fig() and inspect the resulting fig
                        try:
                            inst.add_fig()
                        except Exception as e:
                            logging.getLogger(__name__).exception("tiny_chart: overlay '%s' add_fig() raised: %s", n, e)
                            continue

                        fig = getattr(inst, 'fig', None)
                        if fig is None:
                            logging.getLogger(__name__).warning("tiny_chart: overlay '%s' for %s created no fig", n, self.symbol)
                            continue

                        try:
                            num_traces = len(getattr(fig, 'data', []))
                        except Exception:
                            num_traces = 0

                        logging.getLogger(__name__).debug("tiny_chart: overlay '%s' for %s has fig traces=%s shapes=%s annotations=%s", n, self.symbol, num_traces, len(getattr(fig.layout, 'shapes', [])), len(getattr(fig.layout, 'annotations', [])))

                        if num_traces == 0 and not getattr(fig, 'layout', None):
                            # nothing to add
                            continue

                        # add traces, shapes, annotations defensively
                        for trace in getattr(fig, 'data', []):
                            try:
                                self.fig.add_trace(trace, row=1, col=1)
                            except Exception:
                                pass
                        for shape in getattr(fig.layout, 'shapes', []):
                            try:
                                # Do NOT pass row/col here: Plotly would override
                                # yref='paper' with the subplot's y-axis, turning
                                # paper-relative coords (0–1) into price coords
                                # (invisible). Shapes carry their own xref/yref.
                                self.fig.add_shape(shape)
                            except Exception:
                                pass
                        for annotation in getattr(fig.layout, 'annotations', []):
                            try:
                                # Same reason as shapes: row=1,col=1 would
                                # override xref/yref='paper' with axis coords.
                                self.fig.add_annotation(annotation)
                            except Exception:
                                pass

                    except Exception as e:
                        logging.getLogger(__name__).exception("tiny_chart: error processing overlay %s for %s: %s", n, self.symbol, e)
                        continue
            except Exception:
                pass

        # Debug: list traces currently in figure (type, name, visible)
        try:
            logger = logging.getLogger(__name__)
            trace_info = []
            for i, tr in enumerate(getattr(self.fig, 'data', [])):
                try:
                    ttype = getattr(tr, 'type', None)
                except Exception:
                    ttype = None
                trace_info.append((i, ttype, getattr(tr, 'name', None), getattr(tr, 'visible', None)))
            logger.debug("tiny_chart traces for %s after overlays: %s", self.symbol, trace_info)
        except Exception:
            pass

        # If candles requested, ensure any candlestick traces are visible and
        # adjust y-axis range so candles are not off-screen.
        try:
            if self.candle_chart and 'Low' in self.df.columns and 'High' in self.df.columns:
                lows = pd.to_numeric(self.df['Low'], errors='coerce')
                highs = pd.to_numeric(self.df['High'], errors='coerce')
                if lows.notna().any() and highs.notna().any():
                    minv = float(lows.min())
                    maxv = float(highs.max())
                    padding = (maxv - minv) * 0.05 if maxv > minv else 1.0
                    try:
                        self.fig.update_yaxes(range=[minv - padding, maxv + padding], row=1, col=1)
                    except Exception:
                        pass

                # Make sure any candlestick traces are visible
                try:
                    for tr in list(getattr(self.fig, 'data', [])):
                        if getattr(tr, 'type', None) == 'candlestick':
                            try:
                                tr.visible = True
                            except Exception:
                                try:
                                    tr['visible'] = True
                                except Exception:
                                    pass
                except Exception:
                    pass
        except Exception:
            pass

        self.fig.add_trace(go.Scatter(x = self.df['Date'],
                        y = self.df['Close'],
                        line_color = 'darkgrey',
                        line = {'width':1},
                        name = 'Close',
                        showlegend = False,
                        opacity = 0.9
                        ),                 
              row = 1, col = 1)

        try:
            self._add_hline_outside(
                y=self.df['Close'].iloc[-1],
                text=f'{round(self.df["Close"].iloc[-1], 2)}',
                line_color='darkgreen',
                line_dash='dot',
            )
        except Exception:
            pass

        try:
            self.fig.add_trace(
                go.Scatter(x = self.df['Date'],
                         y = self.df['buy_close'],
                         mode = "markers",
                         line_color = 'darkcyan',
                         marker_symbol="triangle-up",
                         marker_color="darkcyan",
                         showlegend = False,
                         marker_line_width=1, marker_size=10,
                         name = 'Buy',
                         opacity = 1),
              row = 1, col = 1)
        except Exception as e:
            logger.error(f"Error {self.symbol} adding buy signals: {e}")
            pass

        try:
            self.fig.add_trace(
                go.Scatter(x = self.df['Date'],
                         y = self.df['sell_close'],
                         mode = "markers",
                         line_color = 'darkred',
                         marker_symbol="triangle-down",
                         marker_color="darkred",
                         showlegend = False,
                         marker_line_width=1, marker_size=10,
                         name = 'Sell',
                         opacity = 1),
              row = 1, col = 1)
        except Exception as e:
            logger.error(f"Error {self.symbol} adding sell signals: {e}")
            pass

        try:
            self.fig.add_trace(
                go.Scatter(x = self.df['Date'], y = self.df[f'ema21'], name = f'EMA 21',
                        line_color = 'blue',
                        line = { 'width':0.8},
                        #textfont=dict(color='#E58606'),
                        showlegend = self.show_legend,
                        ),
                row = 1,
                col = 1,
            )           
#                     #marker = {'color':'red', 'size':14},
        except Exception:
            pass
        try:
            self.fig.add_trace(
                go.Scatter(x = self.df['Date'], y = self.df[f'ema9'], name = f'EMA 9',
                        line_color = 'darkorange',
                        line = { 'width':0.8},
                        #textfont=dict(color='#E58606'),
                        showlegend = self.show_legend,
                        ),
                row = 1,
                col = 1,
            )           
#                     #marker = {'color':'red', 'size':14},
        except Exception:
            pass

        try:
            self.fig.add_trace(
                go.Scatter(x = self.df['Date'], y = self.df[f'ema50'], name = f'EMA 50',
                        line_color = 'black',
                        line = { 'width':0.8},
                        showlegend = self.show_legend,
                        ),
                row = 1,
                col = 1,
            )

        except Exception:
            pass

        try:
            self.fig.add_trace(
                go.Scatter(x = self.df['Date'], y = self.df[f'sma100'], name = f'MA 100',
                        line_color = 'gray',
                        line = { 'width':0.8},
                        showlegend = self.show_legend,
                        ),
                row = 1,
                col = 1,
            )
#                     #marker = {'color':'red', 'size':14},
        except Exception:
            pass

        try:
            self.fig.add_trace(
                go.Scatter(x = self.df['Date'], y = self.df[f'sma{self.long_ma}'], name = f'SMA{self.long_ma}',
                        line_color = 'red',
                        line = { 'width':0.8},
                        showlegend = self.show_legend,
                        ),
                row = 1,
                col = 1,
            )
#                     #marker = {'color':'red', 'size':14},
        except Exception:
            pass

        try:
            self._add_hline_outside(
                y=self.df['Close'].iloc[0],
                text=f'{round(self.df["Close"].iloc[0], 2)}',
                line_color='darkred',
                line_dash='dot',
            )
        except Exception:
            pass

        if self.calc_ly_hl:
            self.get_last_year_hl(self.symbol)

        try:
            if self.ath:
                # ATH lookup must NOT reuse self.fd: fetch_data() stores each
                # indicator as an attribute on the FetchData instance
                # (self.fd.ewo, self.fd.rsi, …). Reusing self.fd here — with a
                # different interval ('1mo') — would overwrite the sub-plot's
                # weekly/daily oscillator instances with monthly ones, so the
                # sub-plots (getattr(self.fd, n) below) would render monthly data
                # on the main axis (values before the first candle). Use a
                # throwaway, indicator-free instance instead (also cheaper).
                _ath_fd = ft.FetchData(database_path='database', tz_info=self.tz_info,
                                       indicators=[], sys_conf=self.sys_conf)
                (df,_) = _ath_fd.fetch_data(self.symbol, period='max', interval='1mo', add_current=False, max_periods=self.max_periods, region=st)
                ath = df['High'].max()
                self._add_hline_outside(
                    y=ath,
                    text=f'ATH: {round(ath, 2)}',
                    line_color='darkgreen',
                    line_dash='dot',
                )
        except Exception:
            pass

        try:
        
            if self.ly_high > 0:

                self._add_hline_outside(
                    y=self.ly_high,
                    text=f'LY High: {round(self.ly_high, 2)}',
                    line_color='green',
                    line_dash='dash',
                )

            if self.ly_low > 0:

                self._add_hline_outside(
                    y=self.ly_low,
                    text=f'LY Low: {round(self.ly_low, 2)}',
                    line_color='red',
                    line_dash='dash',
                )
            
        except Exception:
            pass
        
        if self.show_trend:
        
            end_trend = -1
            start_trend = -self.trend_length

            try:
                if self.df['highTrend'].iloc[start_trend] > 0:
                    self.fig.add_annotation(
                        x=self.df['Date'].iloc[start_trend], y=(self.df['highTrend'].iloc[start_trend]-(self.df['highTrend'].iloc[start_trend]-self.df['lowTrend'].iloc[start_trend])/2), # position
                        text=f"&#916; {round((1-(self.df['lowTrend'].iloc[start_trend]/self.df['highTrend'].iloc[start_trend]))*100,2)}%",
                        showarrow=False
                    )

                if self.df['highTrend'].iloc[end_trend] > 0:
                    self.fig.add_annotation(
                        x=self.df['Date'].iloc[end_trend], y=(self.df['highTrend'].iloc[end_trend]-(self.df['highTrend'].iloc[end_trend]-self.df['lowTrend'].iloc[end_trend])/2), # position
                        text=f"&#916; {round((1-(self.df['lowTrend'].iloc[end_trend]/self.df['highTrend'].iloc[end_trend]))*100,2)}%",
                        showarrow=False
                    )
            except Exception:
                pass
            
            self.fig.add_trace(
                go.Scatter(
                    x = self.df['Date'],
                    y = self.df['highTrend'],
                    fill="toself", mode='lines',
                    name = 'High price trend',
                    showlegend = self.show_legend,
                    line_color = 'green',
                ),
            row=1, col=1
            )
    
            self.fig.add_trace(
                go.Scatter(
                    x = self.df['Date'],
                    y = self.df['lowTrend'],
                    fill="toself", mode='lines',
                    name = 'Low price trend',
                    showlegend = self.show_legend,
                    line_color = 'red',
                ),
                row=1, col=1
            )

        self.fig['layout']['yaxis']['title'] = f'{self.currency}'

        if self.range_breaks and not "mo" in self.interval:
            if self.exchange == "":
                self.exchange = tt.TickerTools().get_ticker_value(self.ticker,"exchange")
            rangebreaks = self.get_range_breaks(self.df, self.exchange)
            self.fig.update_xaxes(
                        rangebreaks = rangebreaks,
            )

        self.fig.update_xaxes(
            rangeslider_visible = False
        )
            
#                )    

        if visible_sub_plots:

            row = 2
            for n in visible_sub_plots:
                try:
                    _sub = getattr(self.fd, n)
                    _sub.add_fig()
                    for trace in _sub.fig.data:
                        self.fig.add_trace(trace, row=row, col=1)
                    for shape in _sub.fig.layout.shapes:
                        self.fig.add_shape(shape, row=row, col=1)
                    for annotation in _sub.fig.layout.annotations:
                        self.fig.add_annotation(annotation, row=1, col=1)              

                    self.fig['layout'][f'yaxis{row}']['title'] = n
                    row += 1
                except Exception as e:
                    logger.warning("Error in %s modul %s, missing %s", self.symbol, n, e)
                    pass
#            (value_i, unit_i) = ts.Tools().split_interval(self.interval)
#        if unit_i == "min":

        if self.zoom:
            
                pips = self.trend_length*4 # pips                
                length = len(self.df['Date'])
                if pips > length:
                    pips = length
                end = self.df['Date'].iloc[-1]
                start = self.df['Date'].iloc[-pips]

                self.fig.update_xaxes(
                    type="date", 
                    range=[start, end]
                    )

                # DataFrame auf Zoom-Bereich filtern
                visible_df = self.df[(self.df['Date'] >= start) & (self.df['Date'] <= end)]

                # Min/Max der sichtbaren Y-Werte berechnen
                min = visible_df['Low'].min()
                max = visible_df['High'].max()
                padding = (max - min) * 0.05
                self.fig.update_yaxes(
                    range=[min-padding, max+padding],
                    row=1, col=1
                    )

        self.fig.update_xaxes(
            rangeslider_visible = False,
            zeroline=False,
            spikedash='solid',
            spikemode='across',
            spikesnap='cursor',
            type="date",
            showspikes=True,
            spikethickness=0.5,
            )

        self.fig.update_yaxes(
            showticklabels=True,
            showspikes=True,
            spikethickness=0.5,
            spikemode='across',
            spikesnap='cursor',
            spikedash='solid'
            )
 
#            dtick="M1",              # Every month (M1 = 1 month, M3 = every 3rd month etc.)
#            ticklabelmode="period"   # ensures "1." is used as the start date
#        )
#            # style of new shapes
#            )

