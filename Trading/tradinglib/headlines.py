import streamlit as st
from tradinglib import ticker_tools as tt
import math

class Headlines(tt.TickerTools):
    
    def __init__(self, df, ticker, data, system_currency='EUR', screen_region_row1=st, screen_region_row2=st, interval='1d', index_name=""):
        """Set up the headline metrics view with price data, ticker metadata, and layout regions."""
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
    
    def render(self):
        """Render the two-row metrics strip: price/volume/rating row and financial KPI row."""
        if self.df is None or self.df.empty:
            return
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
        ( eps, ptb, div, roa, tpe, beta, l52w, h52w ,sor  ) = headline_row2.columns(9, gap='small')
        
        digits = 2
        if self.df['Close'].iloc[-1] < 1:
            digits = 4

        currency = self.get_ticker_value(self.ticker, 'currency')
        close_price = round(float(self.df['Close'].iloc[-1]),digits)
        d_txt = ''

        x_rate = 1
        if not currency == self.system_currency:
            x_rate = self.get_exchange_rate(currency,self.system_currency)

        # Exposed for callers that need the headline figures outside this view
        # (e.g. main_page's quick-order buttons use close_price/suggested_investment).
        self.close_price = close_price
        self.currency = currency
        self.x_rate = x_rate
        self.suggested_investment = None

        if 'log_vola' in self.df.columns and not self.df['log_vola'].isna().all():
            self.suggested_investment = round(tt.calculate_investment(self.df['log_vola'].iloc[-1])/x_rate, digits)
            d_txt = f"{self.suggested_investment} {self.system_currency}"
        else:
            d_txt = ''

        delta_pct = None
        if 'daily_returns' in self.df.columns and not math.isnan(self.df['daily_returns'].iloc[-1]):
            delta_pct = round(float(self.df['daily_returns'].iloc[-1]), 2)
        elif len(self.df) > 1:
            prev_close = float(self.df['Close'].iloc[-2])
            if prev_close:
                delta_pct = round((close_price - prev_close) / prev_close * 100, 2)

        p_close.metric(
            label=f"Close: ",
            value=close_price,
            delta=f"{delta_pct} %" if delta_pct is not None else None,
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


        try:
            ret_val = round(self.data['overallValueTrend'].iloc[0], 2)
            p_asset.metric(
                label="Value",
                value=ret_val,
                help=f"Range: -100 to 100, Index: {self.index_name}"
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

