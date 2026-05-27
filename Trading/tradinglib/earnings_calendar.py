import streamlit as st
import pandas as pd
import datetime
import yfinance as yf
import time

class EarningsCalendar:
    def __init__(self, tickers: list, past_quarters:int = 1, future_quarters:int = 1, use_scrape: bool = False):
        self.tickers = tickers
        self.past_quarters = past_quarters
        self.future_quarters = future_quarters
        self.use_scrape = use_scrape
        self.df = pd.DataFrame()

    def _get_from_yfinance(self, ticker: str) -> pd.DataFrame:
        t = yf.Ticker(ticker)
        try:
            ed = t.get_earnings_dates(limit=self.past_quarters + self.future_quarters)
        except Exception as e:
            st.warning(f"yfinance error in ticker {ticker}: {e}")
            return pd.DataFrame()
        if ed is None or ed.empty:
#            st.write(f"No earnings for {ticker}")
            return pd.DataFrame()
        ed = ed.reset_index().rename(columns={'index': 'Earnings Date'})
        ed['Ticker'] = ticker
        ed['Earnings Date'] = pd.to_datetime(ed['Earnings Date'])
        return ed

    def fetch(self):
        rows = []
        for ticker in self.tickers:
            df_t = self._get_from_yfinance(ticker)
            if not df_t.empty:
                rows.append(df_t)
            time.sleep(0.5)
        if rows:
            self.df = pd.concat(rows, ignore_index=True)
        else:
            self.df = pd.DataFrame()

    def display_filtered(self, start_date: datetime.date, end_date: datetime.date):
        if self.df.empty:
            st.write("No Data.")
            return
        df_filtered = self.df[
            (self.df['Earnings Date'].dt.date >= start_date) &
            (self.df['Earnings Date'].dt.date <= end_date)
        ]
        if df_filtered.empty:
            st.info("No upcoming Earnings in time frame.")
        else:
            st.dataframe(df_filtered.sort_values('Earnings Date'))

    def run_app(self):
#        st.title("📅 Earnings Calendar – Wochenansicht")
        self.fetch()

        if self.df.empty:
            st.warning("No data.")
            return

        # 🗓️ Standard-Zeitraum: aktuelle Woche
        today = datetime.date.today()
        start_of_week = today - datetime.timedelta(days=today.weekday())  # Montag
        end_of_week = start_of_week + datetime.timedelta(days=28)  # Sonntag

#        st.subheader("Filter by date")
        start_date, end_date = st.date_input(
            "Time frame",
            (start_of_week, end_of_week),
            min_value=self.df['Earnings Date'].min().date(),
            max_value=self.df['Earnings Date'].max().date()
        )

        if isinstance(start_date, tuple):  # für alte Streamlit-Versionen
            start_date, end_date = start_date

        st.markdown(f"**Show Earnings from {start_date} until {end_date}:**")
        self.display_filtered(start_date, end_date)
