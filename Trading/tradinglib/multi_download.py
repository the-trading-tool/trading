import sqlite3
import pandas as pd
from typing import List
from itertools import islice
import os
import yfinance as yf
from tradinglib import market_data as md
from tradinglib import tools

class MultiDataDownloader:
    def __init__(self, tickers: List[str], chunk_size: int = 100,
                 interval: str = '1m', period: str = '1d', db_folder: str = 'database'):
        self.tickers = tickers
        self.chunk_size = chunk_size
        self.interval = interval
        self.period = period
        self.db_folder = tools.Tools().get_path(path = db_folder, file_name='') 
        
    def chunked(self, iterable, size):
        it = iter(iterable)
        while chunk := list(islice(it, size)):
            yield chunk

    def save_single_ticker_to_sqlite(self, ticker: str, df: pd.DataFrame):
        if df.empty:
            print(f"No data for {ticker}")
            return

        df = df.reset_index().rename(columns={'index': 'Datetime'})
        df['Date'] = df['Datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')
        df = df.drop(columns=['Datetime'])
        cols = df.columns.tolist()

        db_path = os.path.join(self.db_folder, f"yf_{ticker}.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        col_defs = ", ".join([
            f"'{col}' REAL" if col != "Date" else "'Date' TEXT PRIMARY KEY"
            for col in cols
        ])
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS minute_data (
                {col_defs}
            )
        """)
        
        placeholders = ", ".join(["?"] * len(cols))
        columns_sql = ", ".join([f"'{col}'" for col in cols])
        update_sql = ", ".join([f"'{col}'=excluded.'{col}'" for col in cols if col != "Date"])
        sql = f"""
            INSERT INTO minute_data ({columns_sql}) 
            VALUES ({placeholders})
            ON CONFLICT(Date) DO UPDATE SET {update_sql}
        """

        try:
            cursor.executemany(sql, df[cols].values.tolist())
            conn.commit()
            print(f"{ticker}: Saved {len(df)} rows.")
        except Exception as e:
            print(f"Error saving data for {ticker}: {e}")
        finally:
            conn.close()

    def process_chunk(self, ticker_chunk: List[str]):
        try:
            data = md.download( 
                tickers=ticker_chunk,
                interval=self.interval,
                period=self.period,
                group_by='ticker',
                actions=False, 
                progress=False, 
                auto_adjust=False, 
                prepost=True            
                )
            for ticker in ticker_chunk:
                try:
                    df = data[ticker].filter(['Date','Close','Open','High','Low','Volume'])
                    self.save_single_ticker_to_sqlite(ticker, df)
                except KeyError:
                    print(f"No data found for {ticker} in chunk.")
        except Exception as e:
            print(f"Chunk failed: {e}")

    def run(self):
        chunks = list(self.chunked(self.tickers, self.chunk_size))
        for chunk in chunks:
            try:
                self.process_chunk(chunk) 
            except Exception as e:
                print(f"Chunk {chunk} failed: {e}")

# Beispielnutzung:
if __name__ == "__main__":
    tickers = ['AAPL','AMZN']
    downloader = MultiDataDownloader(tickers)
    downloader.run()
