import pandas as pd
import plotly.graph_objs as go
from plotly.subplots import make_subplots
import yfinance as yf
from tradinglib import market_data as md
import sys

class PortfolioAnalysis:
    def __init__(self, transactions_file, index_symbol, base_currency='EUR'):
        """
        Initialize the PortfolioAnalysis class with paths to the transaction data file and the index symbol.
        
        :param transactions_file: Path to the Excel file containing transaction data.
        :param index_symbol: Symbol of the index to compare against (e.g., '^IXIC' for NASDAQ).
        :param base_currency: The base currency for conversion (default is EUR).
        """
        self.transactions_file = transactions_file
        self.index_symbol = index_symbol
        self.base_currency = base_currency
        self.portfolio = {}
        self.index_performance = None
        
        self._load_data()
        self._process_data()

    def _load_data(self):
        """Load transactions from Excel, download OHLCV and exchange rates, and convert to base currency."""
        self.transactions_df = pd.read_excel(self.transactions_file)
        
        # Split the transactions into buy and sell events
        buy_transactions = self.transactions_df[['buyDate', 'symbol', 'buyVolume', 'buyPrice']].rename(columns={'buyDate': 'Date', 'buyVolume': 'Volume', 'buyPrice': 'Price'})
        sell_transactions = self.transactions_df[['sellDate', 'symbol', 'sellVolume', 'sellPrice']].rename(columns={'sellDate': 'Date', 'sellVolume': 'Volume', 'sellPrice': 'Price'})

        # Remove rows where sellVolume is 0
        sell_transactions = sell_transactions[~((sell_transactions['Volume'] == 0) & (sell_transactions['Date'].notnull()))]
        sell_transactions['Volume'] = -sell_transactions['Volume']  # Negate sell volumes for easier processing
        
        # Convert 'Date' columns to datetime and remove time component
        buy_transactions['Date'] = pd.to_datetime(buy_transactions['Date']).dt.date
        sell_transactions['Date'] = pd.to_datetime(sell_transactions['Date']).dt.date

        # Combine and sort by date
        self.transactions_df = pd.concat([buy_transactions, sell_transactions], ignore_index=True).dropna().sort_values(by='Date')
        
        # Convert 'Date' back to datetime for consistency
        self.transactions_df['Date'] = pd.to_datetime(self.transactions_df['Date'])
        
        # Ensure there are no NaT values in the Date column
        self.transactions_df = self.transactions_df.dropna(subset=['Date'])
        
        # Extract unique assets from the transactions
        assets = self.transactions_df['symbol'].unique()
        
        # Ensure start and end dates are not NaT
        start_date = self.transactions_df['Date'].min()
        end_date = self.transactions_df['Date'].max()
        if pd.isna(start_date) or pd.isna(end_date):
            raise ValueError("Date range contains NaT values. Please check the transaction data for missing dates.")
        
        # Download the asset data from yfinance and get the currency
        self.asset_data = {}
        self.asset_currency = {}
# Note: ticker.info is still used for currency; only history/download is routed through market_data
        for asset in assets:
            # Use centralized metadata wrapper for currency
            info = md.ticker_info(asset)
            self.asset_currency[asset] = info.get('currency')
            # Use centralized market_data.download for time series
            self.asset_data[asset] = md.download(tickers=asset, start=start_date, end=end_date, auto_adjust=False)
            # ensure consistent index and columns as before
            self.asset_data[asset] = self.asset_data[asset].reset_index().set_index('Date')

        # Download the index data via central wrapper
        self.index_df = md.download(self.index_symbol, start=start_date, end=end_date, auto_adjust=False)
        self.index_df = self.index_df.reset_index().set_index('Date')

        # Download exchange rates for the base currency
        self.exchange_rates = {}
        currencies = set(self.asset_currency.values())
        for currency in currencies:
            if currency != self.base_currency:
                rate_symbol = f"{currency}{self.base_currency}=X"
                self.exchange_rates[currency] = md.download(rate_symbol, start=start_date, end=end_date, auto_adjust=False)['Close'].reindex(pd.date_range(start=start_date, end=end_date, freq='B')).ffill()
            else:
                self.exchange_rates[currency] = pd.Series(1, index=pd.date_range(start=start_date, end=end_date, freq='B'))

        # Convert all asset and index data to the base currency
        for asset in assets:
            currency = self.asset_currency[asset]
            self.asset_data[asset][['Close', 'High', 'Low', 'Open']] = self.asset_data[asset][['Close', 'High', 'Low', 'Open']].multiply(self.exchange_rates[currency], axis=0)

        self.index_df[['Close', 'High', 'Low', 'Open']] = self.index_df[['Close', 'High', 'Low', 'Open']].multiply(self.exchange_rates[currency], axis=0)
        
    def _process_data(self):
        """Build the portfolio_performance DataFrame by processing all transaction events."""
        self.transactions_df['Date'] = pd.to_datetime(self.transactions_df['Date'])
        
        self.transactions_df = self.transactions_df.sort_values(by='Date')
        
        # Create a date range for the entire period
        all_dates = pd.date_range(start=self.transactions_df['Date'].min(), end=self.transactions_df['Date'].max(), freq='B')
        
        assets = self.transactions_df['symbol'].unique()
        self.portfolio_performance = pd.DataFrame(index=all_dates)
        
        for asset in assets:
            asset_data = self.transactions_df[self.transactions_df['symbol'] == asset]
            self._calculate_asset_performance(asset, asset_data, all_dates)
        
        self._calculate_index_performance(all_dates)

    def _calculate_asset_performance(self, asset, asset_data, all_dates):
        """Track daily gain/loss for one asset over all_dates and store in portfolio_performance."""
        holdings = 0
        total_invested = 0
        daily_values = pd.Series(0, index=all_dates)
        
        for date in daily_values.index:
            # Update holdings based on transactions
            transactions_today = asset_data[asset_data['Date'] == date]
            if not transactions_today.empty:
                for _, row in transactions_today.iterrows():
                    if row['Volume'] > 0:  # Buy
                        holdings += row['Volume']
                        total_invested += row['Volume'] * row['Price']
                    else:  # Sell
                        holdings += row['Volume']  # row['Volume'] is negative for sell
                        total_invested += row['Volume'] * row['Price']  # this subtracts from total_invested
            
            # Calculate the current value based on holdings
            if holdings > 0:
                current_value = holdings * self.asset_data[asset]['Close'].loc[date]
                daily_values.loc[date] = current_value - total_invested
            else:
                daily_values.loc[date] = 0

        self.portfolio_performance[asset] = daily_values

    def _calculate_index_performance(self, all_dates):
        """Compute the normalised index performance series reindexed to all_dates."""
        initial_value = self.index_df['Close'].iloc[0]
        self.index_performance = (self.index_df['Close'] / initial_value).reindex(all_dates).ffill()
        
    def plot_performance(self):
        """Plot individual asset performance and portfolio vs. index in a two-panel Plotly chart."""
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.2, 
                            subplot_titles=("Individual Asset Performance", "Portfolio vs. Index Performance"))

        # Plot individual asset performance
        for asset in self.portfolio_performance.columns:
            fig.add_trace(go.Scatter(x=self.portfolio_performance.index, y=self.portfolio_performance[asset],
                                     mode='lines', name=asset), row=1, col=1)

        # Calculate cumulative portfolio performance
        self.portfolio_performance['Total'] = self.portfolio_performance.sum(axis=1)
        initial_total_value = self.portfolio_performance['Total'].iloc[0]
        portfolio_cumulative_performance = self.portfolio_performance['Total'] / initial_total_value

        # Plot portfolio vs index performance
        fig.add_trace(go.Scatter(x=self.portfolio_performance.index, y=portfolio_cumulative_performance,
                                 mode='lines', name='Portfolio'), row=2, col=1)
        fig.add_trace(go.Scatter(x=self.index_performance.index, y=self.index_performance,
                                 mode='lines', name='Index'), row=2, col=1)

        # Update layout
        fig.update_layout(title_text="Portfolio Performance Analysis", height=800)
        fig.show()

if __name__ == "__main__":
#    if len(sys.argv) != 3:


    transactions_file = 'Invested_market.xlsx'
    index_symbol = '^GDAXI'
    analysis = PortfolioAnalysis(transactions_file, index_symbol)
    analysis.plot_performance()

