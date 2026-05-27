#from tradinglib import lib as tl
import yfinance as yf
import pandas as pd
import numpy as np
from xgboost import XGBRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error
from sklearn.preprocessing import MinMaxScaler
from talib import RSI
import pandas as pd
from datetime import timedelta, datetime
from tradinglib import fetch_data as fd

class predict():

    ftime_str = '%Y-%m-%d'
    next_days = []
    y_pred = []
    mse = 0
    model = None
    df = None
    num_days = 10
    precise = False
    
    # tuning
    model_seed = 100 #100
    n_estimators = 200 #500
    max_depth = 5 # 15
    learning_rate = 0.2 # 0.1 
    min_child_weight = 4
    subsample = 1
    colsample_bytree = 1
    colsample_bylevel = 1
    gamma = 0.005


    # prepare data
    def prepare_lag(self):
        # Füge Lag-Funktionen hinzu
        for i in range(1, 1+self.num_days):
            self.df[f'lag_{i}'] = self.df['Close'].shift(i)

    def prepare_data(self, standard_model=False):

        if standard_model:

            # add RSI
#            self.df['RSI'] = RSI(self.df['Close'], timeperiod=14)

            # add mean average
#            self.df['MA10'] = self.df['Close'].rolling(window=10).mean()
#            self.df['MA20'] = self.df['Close'].rolling(window=20).mean()
#            self.df['MA50'] = self.df['Close'].rolling(window=50).mean()
#            self.df['MA200'] = self.df['Close'].rolling(window=200).mean()

            pass

        # high lows 
        self.df['High_shifted'] = self.df['High'].shift(1)
        self.df['Low_shifted'] = self.df['Low'].shift(1)
        self.df['Close_shifted'] = self.df['Close'].shift(1)
        self.df['Open_shifted'] = self.df['Open'].shift(1)

        self.prepare_lag()

    # train model
    def train_model(self,X_train, y_train):
        self.model.fit(X_train, y_train)

    # eval models
    def evaluate_model(self, X_test, y_test):
        self.y_pred = self.model.predict(X_test)

    # predict future price range
    def predict_next_days(self, recent_data):
        self.next_days = self.model.predict(recent_data)
        
    def load_model(self):

            #self.df.astype(float).fillna(0, inplace=True)
            #Downcasting object dtype arrays on .fillna, .ffill, .bfill is deprecated and will change in a future version. Call result.infer_objects(copy=False) instead. To opt-in to the future behavior, set `pd.set_option('future.no_silent_downcasting', True)`
            self.df.index = pd.to_datetime(self.df.index).strftime(self.ftime_str)

            # split data into train and test 
            train_size = int(len(self.df) * 0.8)
            train, test = self.df[:train_size], self.df[train_size:]

            X_train, y_train = train.drop('Close', axis=1), train['Close']
            X_test, y_test = test.drop('Close', axis=1), test['Close']

            # Train model
            self.train_model(X_train, y_train)

            # Evaluieren des Modells
            self.evaluate_model(X_test, y_test)            
            
            # predict prices
            recent_data = self.df[-self.num_days:].drop('Close', axis=1)
            self.predict_next_days(recent_data)

        
    # init module
    def __init__(self, symbol=None, precise = False):

        if precise:

            self.model = XGBRegressor(
                     objective='reg:squarederror',
                     seed=self.model_seed,
                     n_estimators=self.n_estimators,
                     max_depth=self.max_depth,
                     learning_rate=self.learning_rate,
                     min_child_weight=self.min_child_weight,
                     subsample=self.subsample,
                     colsample_bytree=self.colsample_bytree,
                     colsample_bylevel=self.colsample_bylevel,
                     gamma=self.gamma)
        else:
            self.model = XGBRegressor()

        if not symbol==None:
        
            # get the data
            (self.df, _) = fd.FetchData().fetch_data(symbol)
                        
            # add lag info
            self.prepare_data(True)

            self.load_model()

    
