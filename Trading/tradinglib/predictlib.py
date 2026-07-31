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
        """Add num_days lag columns (lag_1 … lag_N) of the Close price to self.df."""
        for i in range(1, 1+self.num_days):
            self.df[f'lag_{i}'] = self.df['Close'].shift(i)

    def prepare_data(self, standard_model=False):
        """Add shifted OHLC columns and lag features to self.df for model training."""
        if standard_model:

            # add RSI

            # add mean average

            pass

        # high lows 
        self.df['High_shifted'] = self.df['High'].shift(1)
        self.df['Low_shifted'] = self.df['Low'].shift(1)
        self.df['Close_shifted'] = self.df['Close'].shift(1)
        self.df['Open_shifted'] = self.df['Open'].shift(1)

        self.prepare_lag()

    def train_model(self, X_train, y_train):
        """Fit the XGBoost model on the training split."""
        self.model.fit(X_train, y_train)

    def evaluate_model(self, X_test, y_test):
        """Generate predictions on the test split and store them in self.y_pred."""
        self.y_pred = self.model.predict(X_test)

    def predict_next_days(self, recent_data):
        """Predict the next N price values from the most recent feature rows."""
        self.next_days = self.model.predict(recent_data)

    def _build_model(self):
        """Return a fresh XGBRegressor configured per self.precise.

        Extracted from __init__ so the recursive forecaster can spin up its own
        model with the same tuning without touching the range-band model.
        """
        if self.precise:
            return XGBRegressor(
                objective='reg:squarederror',
                seed=self.model_seed,
                n_estimators=self.n_estimators,
                max_depth=self.max_depth,
                learning_rate=self.learning_rate,
                min_child_weight=self.min_child_weight,
                subsample=self.subsample,
                colsample_bytree=self.colsample_bytree,
                colsample_bylevel=self.colsample_bylevel,
                gamma=self.gamma,
            )
        return XGBRegressor()

    def forecast_future(self, horizon=3, n_lags=None):
        """Leakage-free recursive multi-step forecast of the next `horizon` closes.

        Unlike `load_model`/`predict_next_days` (which regress today's Close on
        *today's* OHLC — a contemporaneous fit, not a forecast — and then re-score
        the last known bars), this rolls genuinely into the future:

        * **Target = log-return** r_t = ln(C_t / C_{t-1}). Returns are stationary,
          so the tree model isn't capped by the historical price range (XGBoost
          can't extrapolate a raw price above/below anything it saw in training).
        * **Features = only past returns** r_{t-1} … r_{t-n_lags}. No same-day
          OHLC ever enters → no leakage.
        * **Recursive roll-forward:** predict r̂_{t+1}, turn it into a price via
          C_{t+1} = C_t·exp(r̂), feed r̂ back in as the newest lag, repeat until
          `horizon` steps are produced.

        Returns a list of `horizon` predicted close prices (chronological,
        t+1 … t+horizon), or `[]` if there isn't enough history.
        """
        if n_lags is None:
            n_lags = self.num_days

        closes = pd.to_numeric(self.df['Close'], errors='coerce').dropna().astype(float)
        # Need enough rows to build n_lags return-lags plus a usable training set.
        if len(closes) < n_lags + 15:
            return []

        log_ret = np.log(closes / closes.shift(1))

        # Supervised frame: y = r_t, X = [r_{t-1} … r_{t-n_lags}]
        cols = {f'rlag_{i}': log_ret.shift(i) for i in range(1, n_lags + 1)}
        frame = pd.concat([pd.DataFrame(cols), log_ret.rename('y')], axis=1).dropna()
        if frame.empty:
            return []

        feat_cols = [f'rlag_{i}' for i in range(1, n_lags + 1)]
        model = self._build_model()
        model.fit(frame[feat_cols].values, frame['y'].values)

        # Roll forward. ret_hist holds the most recent n_lags returns, oldest→newest;
        # ret_hist[-i] is r_{t-(i-1)}, matching feature column rlag_i.
        ret_hist = list(log_ret.dropna().values[-n_lags:])
        price = float(closes.iloc[-1])
        preds = []
        for _ in range(horizon):
            feat = np.array([ret_hist[-i] for i in range(1, n_lags + 1)]).reshape(1, -1)
            r_hat = float(model.predict(feat)[0])
            price = price * float(np.exp(r_hat))
            preds.append(price)
            ret_hist.append(r_hat)
        return preds
        
    def load_model(self):
            """Split data 80/20, train the model, evaluate it, and predict the next num_days prices."""
            #Downcasting object dtype arrays on .fillna, .ffill, .bfill is deprecated and will change in a future version. Call result.infer_objects(copy=False) instead. To opt-in to the future behavior, set `pd.set_option('future.no_silent_downcasting', True)`
            self.df.index = pd.to_datetime(self.df.index).strftime(self.ftime_str)

            # split data into train and test 
            train_size = int(len(self.df) * 0.8)
            train, test = self.df[:train_size], self.df[train_size:]

            X_train, y_train = train.drop('Close', axis=1), train['Close']
            X_test, y_test = test.drop('Close', axis=1), test['Close']

            # Train model
            self.train_model(X_train, y_train)

            # Evaluate the model
            self.evaluate_model(X_test, y_test)            
            
            # predict prices
            recent_data = self.df[-self.num_days:].drop('Close', axis=1)
            self.predict_next_days(recent_data)

        
    def __init__(self, symbol=None, precise=False):
        """Initialize the XGBoost price predictor; fetches data and trains immediately when symbol is given.

        precise=True uses a tuned hyperparameter set; False uses XGBoost defaults.
        """
        self.precise = precise
        self.model = self._build_model()

        if not symbol==None:
        
            # get the data
            (self.df, _) = fd.FetchData().fetch_data(symbol)
                        
            # add lag info
            self.prepare_data(True)

            self.load_model()

