import streamlit as st
from tradinglib import ticker_tools as tt
import math

class Headlines(tt.TickerTools):
    
    def __init__(self, df, ticker, data, system_currency = 'EUR', screen_region_row1 = st, screen_region_row2 = st, interval = '1d', index_name = ""):

        self.df = df
        self.index_name = index_name
        if index_name == "":
            self.index_name = "Index not found"
        self.ticker = ticker
        self.data = data
        #self.index_name = data['index_name']
        self.interval = interval
        self.system_currency = system_currency
        self.screen_region_row1 = screen_region_row1
        self.screen_region_row2 = screen_region_row2
    
    def render(self):
    
        st.markdown(
                """
                <style>
                    [data-testid="stMetricValue"] {
                    font-size: 19px;
                }
               </style>
                """,
                unsafe_allow_html=True,
                )


        headline_row1 = self.screen_region_row1.empty()
        (p_date, p_close, p_currency, p_open, p_low, p_high, p_target, p_rating, p_asset, p_vol  ) = headline_row1.columns(10, gap='small')
        headline_row2 = self.screen_region_row2.empty()
        ( eps, ptb, div, roa, tpe, beta, l52w, h52w ,sor ,shar  ) = headline_row2.columns(10, gap='small')
        
        digits = 2
        if self.df['Close'].iloc[-1] < 1:
            digits = 4

        currency = self.get_ticker_value(self.ticker, 'currency')
        close_price = round(float(self.df['Close'].iloc[-1]),digits)
        d_txt = ''

        x_rate = 1
        if not currency == self.system_currency:
            x_rate = self.get_exchange_rate(currency,self.system_currency)
        
        d_txt = f"{round(tt.calculate_investment(self.df['log_vola'].iloc[-1])/x_rate,digits) } {self.system_currency}" 
        p_close.metric(
            label=f"Close: ",
            value=close_price,
            help=d_txt
        )

        d_txt = f'{self.system_currency}={round(x_rate,3)} - price: {round(close_price/x_rate,digits)}'
        if currency:
            lbl = "Currency"
            p_currency.metric(
                label=lbl,
                value=currency,
                help=d_txt
            )

#        trade = tl.get_trend(self.df, tl.trend_end-1)

        try:
            ret_val = round(self.data['buySell'].iloc[0],2) 
            p_asset.metric(
                label="Buy / sell",
                value=ret_val,
                help=f"Value: {self.data['overallValueTrend'].iloc[0]}, Trend: {self.data['trendDirection'].iloc[0]}"
            )
        except Exception:
            pass
        
        p_date.metric(
            label="Price date",
            value=f"{self.df['Date'].iloc[-1]}",
            help=f"Index: {self.index_name}"
        )
        p_open.metric(
            label="Open",
            value=round(float(self.df['Open'].iloc[0]),digits),
        )
        p_low.metric(
            label="Low",
            value=round(float(self.df['Low'].min()),digits),
        )
        p_high.metric(
            label="High",
            value=round(float(self.df['High'].max()),digits),
        )

        ret_val = self.get_ticker_value(self.ticker, 'volume')
        if ret_val:
            lbl = "Volume"
            try:
                p_vol.metric(
                    label=lbl,
                    value=f"{round(float(ret_val)/1000,1)} k"
                )
            except (TypeError, ValueError):
                p_vol.metric(label=lbl, value=str(ret_val))

        price_low = 0
        ret_val = self.get_ticker_value(self.ticker, 'fiftyTwoWeekLow') 
        if ret_val:
            lbl = "52 week low"
            price_low = ret_val
            l52w.metric(
                label=lbl,
                value=ret_val
                )
    
        price_high = 0
        ret_val = self.get_ticker_value(self.ticker, 'fiftyTwoWeekHigh') 
        if ret_val:
            lbl = "52 week high"
            price_high = ret_val
            h52w.metric(
                label=lbl,
                value=ret_val
                )
    
        ret_val = 0
        try:
            ret_val = round(self.data['sortino'].iloc[0],2)
        except Exception:
            pass
        if ret_val:
            lbl = "Sortino ratio"
            sor.metric(
                label=lbl,
                value=ret_val
                )

        ret_val = 0
        try:
            ret_val = round(self.data['sharpe'].iloc[0],2) 
        except Exception:
            pass
        if ret_val:
            lbl = "Sharpe ratio"
            shar.metric(
                label=lbl,
                value=ret_val
                )

        ret_val = self.get_ticker_value(self.ticker, 'targetMeanPrice')
        price_target = 0
        if ret_val:
                pct = 0
                if not (ret_val-self.df['Close'].iloc[-1]) == 0:
                    pct = round((ret_val-self.df['Close'].iloc[-1])/ret_val*100,2)
                    price_target = float(ret_val)
                    lbl = "Mean price target"
                    p_target.metric(
                        label=lbl,
                        value=ret_val,
                        delta=f"{pct} %",
                        help=f"Target high price: {self.get_ticker_value(self.ticker, 'targetHighPrice')}"
                    )
       
        ret_val = self.get_ticker_value(self.ticker, 'beta') 
        if ret_val:
           lbl = "Beta"
           beta.metric(
                label=lbl,
               value=ret_val
           )
    
        ret_val = 0
        try:
            ret_val = round(self.data['roa'].iloc[0] * 100,1) 
            lbl = f"RoA %"
            roa.metric(
                label=lbl,
                value=ret_val        
            )
        except Exception:
            pass

        ret_val = self.get_ticker_value(self.ticker, 'forwardEps') 
        if ret_val:
            lbl = "forward EPS"
            eps.metric(
                label=lbl,
                value=ret_val
            )
    
        ret_val = self.get_ticker_value(self.ticker, 'recommendationMean') 
        if ret_val:
            lbl = f"Analyst rating: {self.get_ticker_value(self.ticker, 'recommendationKey')}"
            p_rating.metric(
                label=lbl,
                value=ret_val
                )

        ret_val = self.get_ticker_value(self.ticker, 'dividendRate') 
        if ret_val:
            lbl = "Dividend rate"
            div.metric(
                label=lbl,
                value=ret_val
                )

        ret_val = self.get_ticker_value(self.ticker, 'priceToBook') 
        if ret_val:
            lbl = "Price to book"
            ptb.metric(
                label=lbl,
                value=ret_val
                )

        ret_val = self.get_ticker_value(self.ticker, 'forwardPE') 
        if ret_val:
            lbl = "Forward PE"
            tpe.metric(
                label=lbl,
                value=ret_val
                )
        else:        
            ret_val = self.get_ticker_value(self.ticker, 'trailingPE') 
            if ret_val:
                lbl = "Trailing PE"
                tpe.metric(
                    label=lbl,
                    value=ret_val
                    )

