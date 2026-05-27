# Importing libraries

# Pandas & NumPy
import pandas as pd
import numpy as np

# Yfinance to retrieve financial data 
import yfinance as yf
from tradinglib import market_data as md

# Plotly for Data Visualization
import plotly.express as px
import plotly.graph_objs as go
import plotly.subplots as sp
from plotly.subplots import make_subplots
import plotly.figure_factory as ff
import plotly.io as pio

import warnings
warnings.filterwarnings('ignore')

import datetime as dt
import streamlit as st 

class portfolio: 

    tickers_and_values = {}
    ticker_names = {}
    port_returns = None
    benchmark_returns = None 
    tickers_weights = 0
    df = None
    benchmark = '^GDAXI'
    start_date = '2024-02-01'
    end_date = '2024-04-15'
    ftime_str = '%Y-%m-%d'
    fig_portfolio_distribution = None
    fig_portfolio_trend = None
    fig_portfolio_sharp_ratio = None
    fig_portfolio_trend_summary = None
    fig_portfolio_total_sharp_ratio = None
    
    debug = False
    
    def portfolio_returns(self):

        """
        This function downloads historical stock data, calculates the weighted returns to build a portfolio,
        and compares these returns to a benchmark.
        It also displays the portfolio allocation and the performance of the portfolio against the benchmark.

        Parameters:
        - tickers_and_values (dict): A dictionary where keys are ticker symbols (str) and values are the current
          amounts (float) invested in each ticker.
        - start_date (str): The start date for the historical data in the format 'YYYY-MM-DD'.
        - end_date (str): The end date for the historical data in the format 'YYYY-MM-DD'.
        - benchmark (str): The ticker symbol for the benchmark against which to compare the portfolio's performance.

        Returns:
        - Displays three plots:
        1. A pie chart showing the portfolio allocation by ticker.
        2. A plot to analyze historical returns and volatility of each security
             in the portfolio. (Not plotted if portfolio only has one security)
        2. A comparison between portfolio returns and volatility against the benchmark over the specified period.

        Notes:
        - The function assumes that 'yfinance', 'pandas', 'plotly.graph_objects', and 'plotly.express' are imported
        as 'yf', 'pd', 'go', and 'px' respectively.
        - For single security portfolios, the function calculates returns without weighting.
        - The function utilizes a helper function 'portfolio_vs_benchmark' for comparing portfolio returns with
        the benchmark, which needs to be defined separately.
        - Another helper function 'perform_portfolio_analysis' is called for portfolios with more than one security,
        which also needs to be defined separately.
        """

        # Obtaining tickers data with yfinance
        delta = f'{(dt.datetime.strptime(self.end_date, self.ftime_str) - dt.datetime.strptime(self.start_date,self.ftime_str)).days}d'
        self.df = md.download(tickers=list(self.tickers_and_values.keys()),
             start=self.start_date, end=self.end_date, interval='1d', 
             actions=False, 
             progress=False, 
             auto_adjust=False, 
             prepost=True
             )

        if self.debug:
            # getting the timestamp
            ts = dt.datetime.timestamp(dt.datetime.now())
            self.df.to_csv(f'./prtf_in{ts}.csv',sep=';',quotechar='"', decimal=',')

        # Checking if there is data available in the given date range
        if isinstance(self.df.columns, pd.MultiIndex):
            missing_data_tickers = []
            for ticker in self.tickers_and_values.keys():
                first_valid_index = self.df['Adj Close'][ticker].first_valid_index()
                if first_valid_index is None or first_valid_index.strftime('%Y-%m-%d') > self.start_date:
                    missing_data_tickers.append(ticker)
            
            RED_BOLD_TEXT = '\033[1;31m'
            RESET_TEXT = '\033[0m'

            if missing_data_tickers:
                print(f"{RED_BOLD_TEXT}\n No data available for the following tickers starting from {self.start_date}: {', '.join(missing_data_tickers)}{RESET_TEXT}")
                return
        else:
            # For a single ticker, simply check the first valid index
            first_valid_index = self.df['Adj Close'].first_valid_index()
            if first_valid_index is None or first_valid_index.strftime('%Y-%m-%d') > self.start_date:
                print(f"{RED_BOLD_TEXT}\n No data available for the ticker starting from {self.start_date}{RESET_TEXT}")
                return
    
        # Calculating portfolio value
        total_portfolio_value = sum(self.tickers_and_values.values())

        # Calculating the weights for each security in the portfolio
        self.tickers_weights = {ticker: value / total_portfolio_value for ticker, value in self.tickers_and_values.items()}

        # Checking if dataframe has MultiIndex columns
        if isinstance(self.df.columns, pd.MultiIndex):
            self.df = self.df['Adj Close'].fillna(self.df['Close']) # If 'Adjusted Close' is not available, use 'Close'
        
        # Checking if there are more than just one security in the portfolio
        if len(self.tickers_weights) > 1:
            weights = list(self.tickers_weights.values()) # Obtaining weights
            weighted_returns = self.df.pct_change().mul(weights, axis = 1) # Computed weighted returns
            self.port_returns = weighted_returns.sum(axis=1) # Sum weighted returns to build portfolio returns
        # If there is only one security in the portfolio...
        else:
            self.df = self.df['Adj Close'].fillna(self.df['Close'])  # Obtaining 'Adjusted Close'. If not available, use 'Close'
            port_returns = self.df.pct_change() # Computing returns without weights

        # Obtaining benchmark data with yfinance
        benchmark_df = md.download(self.benchmark, 
                   start=self.start_date, end=self.end_date, interval='1d', actions=False, progress=False, auto_adjust=False, prepost=True) 
        # Obtaining 'Adjusted Close'. If not available, use 'Close'.
        benchmark_df = benchmark_df['Adj Close'].fillna(benchmark_df['Close'])

        # Computing benchmark returns
        self.benchmark_returns = benchmark_df.pct_change()

        # Plotting a pie plot
        self.fig_portfolio_distribution = go.Figure(data=[go.Pie(
            labels=list(self.tickers_weights.keys()), # Obtaining tickers 
            values=list(self.tickers_weights.values()), # Obtaining weights
            hoverinfo='label+percent', 
            textinfo='label+percent',
            hole=.65,
            marker=dict(colors=px.colors.qualitative.G10)
        )])

        # Defining layout
        self.fig_portfolio_distribution.update_layout(title={'text': '<b>Portfolio Allocation</b>'}, height=550)


    def perform_portfolio_analysis(self):

        """
        This function takes historical stock data and the weights of the securities in the portfolio,
        It calculates individual security returns, cumulative returns, volatility, and Sharpe Ratios.
        It then visualizes this data, showing historical performance and a risk-reward plot.

        Parameters:
        - df (pd.DataFrame): DataFrame containing historical stock data with securities as columns.
        - tickers_weights (dict): A dictionary where keys are ticker symbols (str) and values are their 
            respective weights (float)in the portfolio.

        Returns:
        - fig1: A Plotly Figure with two subplots:
        1. Line plot showing the historical returns of each security in the portfolio.
        2. Plot showing the annualized volatility and last cumulative return of each security 
            colored by their respective Sharpe Ratio.

        Notes:
        - The function assumes that 'pandas', 'numpy', and 'plotly.graph_objects' are imported as 'pd', 'np', and 'go' respectively.
        - The function also utilizes 'plotly.subplots.make_subplots' for creating subplots.
        - The risk-free rate is assumed to be 1% per annum for Sharpe Ratio calculation.
        """

    # Starting DataFrame and Series 
        individual_cumsum = pd.DataFrame()
        individual_vol = pd.Series(dtype=float)
        individual_sharpe = pd.Series(dtype=float)


        # Iterating through tickers and weights in the tickers_weights dictionary
        for ticker, weight in self.tickers_weights.items():
            if ticker in self.df.columns: # Confirming that the tickers are available
                individual_returns = self.df[ticker].pct_change() # Computing individual daily returns for each ticker
                individual_cumsum[ticker] = ((1 + individual_returns).cumprod() - 1) * 100 # Computing cumulative returns over the period for each ticker 
                vol = (individual_returns.std() * np.sqrt(252)) * 100 # Computing annualized volatility
                individual_vol[ticker] = vol # Adding annualized volatility for each ticker
                individual_excess_returns = individual_returns - 0.01 / 252 # Computing the excess returns
                sharpe = (individual_excess_returns.mean() / individual_returns.std() * np.sqrt(252)).round(2) # Computing Sharpe Ratio
                individual_sharpe[ticker] = sharpe # Adding Sharpe Ratio for each ticker

                # Creating subplots for comparison across securities
                self.fig_portfolio_trend = make_subplots(rows = 1, cols = 1, horizontal_spacing=0.2,
                            column_titles=['Historical Performance Assets', 'Risk-Reward'],
                            #column_widths=[.50, .50],
                            shared_xaxes=False, shared_yaxes=False)

                # Creating subplots for comparison across securities
                self.fig_portfolio_sharp_ratio = make_subplots(rows = 1, cols = 1, horizontal_spacing=0.2,
                            column_titles=['Historical Performance Assets', 'Risk-Reward'],
                            #column_widths=[.50, .50],
                            shared_xaxes=False, shared_yaxes=False)
        
        # Adding the historical returns for each ticker on the first subplot    
        for ticker in individual_cumsum.columns:
            self.fig_portfolio_trend.add_trace(go.Scatter(x=individual_cumsum.index,
                                  y=individual_cumsum[ticker],
                                  mode = 'lines',
                                  name = f'{ticker} - {self.ticker_names[ticker]}',
                                  hovertemplate = '%{y:.2f}%',
#                                  showlegend=False
                                  ),
                            row=1, col=1)

        # Defining colors for markers on the second subplot
        sharpe_colors = [individual_sharpe[ticker] for ticker in individual_cumsum.columns]

        ticker_names = []
        for ticker in individual_cumsum.columns.tolist():
            ticker_names.append(f'{ticker} {self.ticker_names[ticker]}'[0:25].replace(' ','<br>'))
        
        # Adding markers for each ticker on the second subplot
        self.fig_portfolio_sharp_ratio.add_trace(go.Scatter(x=individual_vol.tolist(),
                              y=individual_cumsum.iloc[-1].tolist(),
                              mode='markers+text',
                              marker=dict(size=75, color = sharpe_colors, 
                                          colorscale = 'Bluered_r',
                                          colorbar=dict(title='Sharpe Ratio'),
                                          showscale=True),
                              name = 'Returns',
                              text = ticker_names, #individual_cumsum.columns.tolist(),
                              textfont=dict(color='white'),
#                              showlegend=False,
                              hovertemplate = '%{y:.2f}%<br>Annualized Volatility: %{x:.2f}%<br>Sharpe Ratio: %{marker.color:.2f}<br>%{text}',
                              textposition='middle center'),
                        row=1, col=1)
            
        # Updating layout
        self.fig_portfolio_trend.update_layout(title={'text': f'<b>Portfolio Analysis</b>'},
                       template = 'plotly_white',
                       height = 700, width = 1000,
                       #hovermode = 'x unified'
                       )
        
        self.fig_portfolio_sharp_ratio.update_layout(title={'text': f'<b>Portfolio Analysis</b>'},
                       template = 'plotly_white',
                       height = 700, width = 1000,
                       #hovermode = 'x unified'
                       )
        
        self.fig_portfolio_trend.update_yaxes(title_text='Returns (%)', col=1)
        self.fig_portfolio_sharp_ratio.update_yaxes(title_text='Returns (%)', col = 1)
        self.fig_portfolio_trend.update_xaxes(title_text = 'Date', col = 1)
        self.fig_portfolio_sharp_ratio.update_xaxes(title_text = 'Annualized Volatility (%)', col =1)
            

    def portfolio_vs_benchmark(self):

        """
        This function calculates and displays the cumulative returns, annualized volatility, and Sharpe Ratios
        for both the portfolio and the benchmark. It provides a side-by-side comparison to assess the portfolio's
        performance relative to the benchmark.

        Parameters:
        - port_returns (pd.Series): A Pandas Series containing the daily returns of the portfolio.
        - benchmark_returns (pd.Series): A Pandas Series containing the daily returns of the benchmark.

        Returns:
        - fig2: A Plotly Figure object with two subplots:
        1. Line plot showing the cumulative returns of both the portfolio and the benchmark over time.
        2. Scatter plot indicating the annualized volatility and the last cumulative return of both the portfolio
             and the benchmark, colored by their respective Sharpe Ratios.

        Notes:
        - The function assumes that 'numpy' and 'plotly.graph_objects' are imported as 'np' and 'go' respectively.
        - The function also utilizes 'plotly.subplots.make_subplots' for creating subplots.
        - The risk-free rate is assumed to be 1% per annum for Sharpe Ratio calculation.
        """

        # Computing the cumulative returns for the portfolio and the benchmark

        try:
            portfolio_cumsum = (((1 + self.port_returns).cumprod() - 1) * 100).round(2)
            benchmark_cumsum = (((1 + self.benchmark_returns).cumprod() - 1) * 100).round(2)
        except Exception:
            return
            pass

        # Computing the annualized volatility for the portfolio and the benchmark
        port_vol = ((self.port_returns.std() * np.sqrt(252)) * 100).round(2)
        benchmark_vol = ((self.benchmark_returns.std() * np.sqrt(252)) * 100).round(2)

        # Computing Sharpe Ratio for the portfolio and the benchmark
        excess_port_returns = self.port_returns - 0.01 / 252
        port_sharpe = (excess_port_returns.mean() / self.port_returns.std() * np.sqrt(252)).round(2)
        exces_benchmark_returns = self.benchmark_returns - 0.01 / 252
        benchmark_sharpe = (exces_benchmark_returns.mean() / self.benchmark_returns.std() * np.sqrt(252)).round(2)
        if isinstance(benchmark_sharpe, pd.Series):
            benchmark_sharpe = benchmark_sharpe.iloc[0]
        if isinstance(benchmark_vol, pd.Series):
            benchmark_vol = benchmark_vol.iloc[0]

        pf_color = '#0000ff'
        bm_color = '#ff0000'
        if port_sharpe < benchmark_sharpe:
            pf_color = bm_color
            bm_color = '#0000ff'

        # Creating a subplot to compare portfolio performance with the benchmark
        self.fig_portfolio_trend_summary = make_subplots(rows = 1, cols = 2, horizontal_spacing=0.2,
                        column_titles=['Cumulative Returns', 'Portfolio Risk-Reward'],
                        column_widths=[.50, .50],
                        shared_xaxes=False, shared_yaxes=False)

        # Adding the cumulative returns for the portfolio
        self.fig_portfolio_trend_summary.add_trace(go.Scatter(x=portfolio_cumsum.index, 
                             y = portfolio_cumsum,
                             mode = 'lines', name = 'Portfolio', showlegend=False,
                             line=dict(color=pf_color),
                             hovertemplate = '%{y:.2f}%'),
                             row=1,col=1)
        benchmark_cumsum.index = pd.to_datetime(benchmark_cumsum.index)
        portfolio_cumsum.index = pd.to_datetime(portfolio_cumsum.index)
        benchmark_cumsum.fillna(0,inplace=True)
        benchmark_cumsum.rename(columns={ benchmark_cumsum.columns[0]: 0 }, inplace = True)    
#        st.write(benchmark_cumsum)
        # Adding the cumulative returns for the benchmark
        self.fig_portfolio_trend_summary.add_trace(go.Scatter(x=benchmark_cumsum.index, 
                             y = benchmark_cumsum[0],
                             mode = 'lines', name = self.benchmark, showlegend=False,
                             line=dict(color=bm_color),
                             hovertemplate = '%{y:.2f}%'),
                             row=1,col=1)
        # Creating risk-reward plot for the benchmark and the portfolio
        self.fig_portfolio_trend_summary.add_trace(go.Scatter(x = [port_vol, benchmark_vol], y = [portfolio_cumsum.iloc[-1], benchmark_cumsum[0].iloc[-1]],
                             mode = 'markers+text', 
                             marker=dict(size = 75, 
                                         color = [port_sharpe, benchmark_sharpe],
                                         colorscale='Bluered_r',
                                         colorbar=dict(title='Sharpe Ratio'),
                                         showscale=True),
                             name = 'Returns', 
                             text=['Portfolio', self.benchmark], textposition='middle center',
                             textfont=dict(color='white'),
                             hovertemplate = '%{y:.2f}%<br>Annualized Volatility: %{x:.2f}%<br>Sharpe Ratio: %{marker.color:.2f}',
                             showlegend=False),
                             row = 1, col = 2)
    
    
        # Configuring layout
        self.fig_portfolio_trend_summary.update_layout(title={'text': f'<b>Portfolio vs Benchmark</b>'},
                      template = 'plotly_white',
                      height = 700, width = 1000,
                      hovermode = 'x unified')
    
        self.fig_portfolio_trend_summary.update_yaxes(title_text='Cumulative Returns (%)', col=1)
        self.fig_portfolio_trend_summary.update_yaxes(title_text='Cumulative Returns (%)', col = 2)
        self.fig_portfolio_trend_summary.update_xaxes(title_text = 'Date', col = 1)
        self.fig_portfolio_trend_summary.update_xaxes(title_text = 'Annualized Volatility (%)', col =2)


    # init module
    def __init__(self, tickers, ticker_names = {}, start_date='2024-02-01', end_date='2024-12-31', benchmark='^GDAXI'):

        self.tickers_and_values = tickers
        self.ticker_names = ticker_names
        self.start_date = start_date
        self.end_date = end_date
        self.benchmark = benchmark
        
        # calc portfolio returns
        self.portfolio_returns()

        # Running function to compare portfolio and benchmark
        self.portfolio_vs_benchmark()    
        
        # If we have more than one security in the portfolio, 
        # we run function to evaluate each security individually
        if len(self.tickers_weights) > 1:
            self.perform_portfolio_analysis()
        

