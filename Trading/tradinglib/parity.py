import yfinance as yf
from tradinglib import market_data as md
import pandas as pd
import numpy as np
import warnings
import streamlit as st 
import datetime as dt

warnings.filterwarnings("ignore", category=FutureWarning)
pd.options.mode.copy_on_write = True 

class Parity:
    
    # mix of 48 well performing assets +40% (2023) + 30% 1st qtr 24 
    tickers = ['ABEC.DE','CAT1.DE','PNR','SIE.DE','RHM.DE','RNO.PA','IXD1.DE','APH','SU.PA','DTG.DE','DELL','NTAP','AOF.DE','AIR.DE','ALV.DE','HUBB','SAP.DE', 'AP2.DE','PRG.DE','NRO.PA','ACT.DE','JPM',
              'HNR1.DE','DECK','MSF.DE','MCK','3V64.DE','SMCI','NVD.DE','GNE.PA','STLA','NU','MUV2.DE','AXP','DB1.DE','INN1.DE','HEI.DE','EL.PA','EGLN.L','BSD2.DE','ETH-EUR','DBX7.DE',
              'O','SBX.DE','KGX.DE']

    data = None
    weights = None
    opt_freq = 60
    idx = '^GDAXI'
    t_date = dt.date(dt.datetime.now().year, 1, 1)
    t_date_e = dt.date(dt.datetime.now().year, dt.datetime.now().month, dt.datetime.now().day)
    start = t_date.strftime('%Y-%m-%d')            
    end = t_date_e.strftime('%Y-%m-%d')            

    #start = "2023-02-01"
    #end = "2025-11-01"
    initial_val = 100000
    interval = '1d'
    rolling_vol_rp = 0
    rolling_vol_sp = 0
    rolling_sharpe_rp = 0
    rolling_sharpe_sp = 0
    
    def fetch_data(self, tickers):
        tickers = list(dict.fromkeys(tickers))
        # Download data via centralized wrapper. The wrapper may return either
        # - a yf.download-style DataFrame where the first column level is e.g. 'Adj Close'
        # - or a locally-assembled MultiIndex with levels (['Open','High','Low','Close'], [ticker])
        # To be robust, try to select 'Adj Close' first, else fall back to 'Close'.
        df_all = md.download(tickers + [self.idx], start=self.start, end=self.end, actions=False, progress=False, auto_adjust=False, prepost=True)

        # Helper to pick adjusted-close or close depending on returned structure
        try:
            cols = df_all.columns
            # MultiIndex case: try to take level 0 == 'Adj Close'
            if hasattr(cols, 'levels') and len(cols.levels) > 1:
                if 'Adj Close' in cols.get_level_values(0):
                    df = df_all.xs('Adj Close', axis=1, level=0)
                elif 'Close' in cols.get_level_values(0):
                    df = df_all.xs('Close', axis=1, level=0)
                else:
                    # As a last resort, try to collapse by taking the last available numeric column per ticker
                    df = df_all.copy()
            else:
                # Single-level columns
                if 'Adj Close' in df_all.columns:
                    df = df_all['Adj Close']
                elif 'Close' in df_all.columns:
                    df = df_all['Close']
                else:
                    df = df_all.copy()
        except Exception:
            df = df_all.copy()

        return df

    def calculate_weights_pct(self, data):
        returns = data.fillna(0).pct_change().dropna()
        vol = returns.rolling(window=self.opt_freq, min_periods=2).std().mean()[:-1]  # Exclude index
        inv_vol = 1 / vol
        weights = inv_vol / np.sum(inv_vol)*100

        return weights
    

    # Perf Eval
    def calculate_weights(self, data):
#        vol = data.rolling(window=self.opt_freq, min_periods=2).std().dropna().iloc[-1][:-1]  # Exclude index
        vol = data.rolling(window=self.opt_freq, min_periods=2).std().fillna(0).iloc[-1][:-1]  # Exclude index
        inv_vol = 1 / vol
        weights = inv_vol / np.sum(inv_vol)
        return weights

    def simulate_portfolio(self, returns, n_days=opt_freq):
        port_val = [1]
        idx_val = [1]
        weights = np.ones(len(self.tickers)) / len(self.tickers)  # Start with equal weights

        for i in range(len(returns)):
            if i < self.opt_freq:  # If less than rolling window, use equal weights
                daily_port_return = np.dot(returns.iloc[i][:-1], weights)
            else:
                if i % n_days == 0:  # Rebalancing
                    weights = self.calculate_weights(returns.iloc[i-self.opt_freq:i])
                daily_port_return = np.dot(returns.iloc[i][:-1], weights)

            port_val.append(port_val[-1] * (1 + daily_port_return))
            idx_val.append(idx_val[-1] * (1 + returns.iloc[i][self.idx]))
        
        return port_val, idx_val

    def __init__(self, assets = ['TSLA','NVDA','META','MSFT','GOOGL','AMZN','AAPL'], benchmark = '^GDAXI'):

        # Main
        self.tickers = assets
        self.idx = benchmark
        self.data = self.fetch_data(self.tickers)
        self.weights = self.calculate_weights_pct(self.data)
        #print(self.weights.to_string())
        l = len(self.data)-1
        if self.opt_freq > l:
#            st.warning(f"Rolling window ({self.opt_freq}) is larger than the number of data points ({l}). Adjusting rolling window to {l}.")
            self.opt_freq = l   

#        returns = self.fetch_data(self.tickers).pct_change().dropna()
        returns = self.data.pct_change()#.dropna()
        port_val, idx_val = self.simulate_portfolio(returns, n_days=self.opt_freq)

        # Calculate rolling metrics 
        self.rolling_vol_rp = pd.Series(port_val[:-1]).pct_change().rolling(window=self.opt_freq, min_periods=2).std()
        self.rolling_vol_sp = returns[self.idx].rolling(window=self.opt_freq, min_periods=2).std()
        self.rolling_sharpe_rp = pd.Series(port_val[:-1]).pct_change().rolling(window=self.opt_freq, min_periods=2).mean() / self.rolling_vol_rp
        self.rolling_sharpe_sp = returns[self.idx].rolling(window=self.opt_freq, min_periods=2).mean() / self.rolling_vol_sp

        # Calculate weights over time
        self.weights_df = pd.DataFrame(index=returns.index, columns=self.tickers)
        for i, date in enumerate(returns.index):
            if i < self.opt_freq:
                self.weights_df.loc[date] = np.ones(len(self.tickers)) / len(self.tickers)
            elif i % self.opt_freq == 0:
                self.weights_df.loc[date] = self.calculate_weights(returns.iloc[i-self.opt_freq:i])
            else:
                self.weights_df.loc[date] = self.weights_df.iloc[i-1]
