import numpy as np
from finta import TA
import ta
import math
import pandas as pd
import importlib.util
import inspect
import os
from tradinglib.indicator import _indicator
from tradinglib import tools
from tradinglib.utils import DataUtils

class IndicatorLoader:

    def __init__(self, directory: str):
        self.directory = directory
        self.oszilator_indicators = []
        self.overlay_indicators = []
        self._classes = {}  # short_name -> class
        self.indicator_classes = self._load_indicators()

    def _load_indicators(self):
        for file in os.listdir(self.directory):
            if file.endswith(".py") and file != "_indicator.py" and file != "__init__.py" and file != "indicator.py":
                module_name = file[:-3]  # Dateiendung ".py" entfernen
                module_path = os.path.join(self.directory, file)
                # Modul dynamisch importieren
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Durchsuche das Modul nach Klassen, die von Indicator erben
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, _indicator._Indicator) and obj is not _indicator._Indicator:
                        short_name = name.lower()
                        self._classes[short_name] = obj
                        if hasattr(obj, 'is_oszilator'):
                            if obj.is_oszilator:  # Wenn indicator_type True ist
                                self.oszilator_indicators.append(f"{short_name} - {obj.name}")
                            else:
                                self.overlay_indicators.append(f"{short_name} - {obj.name}")

    def get_oszilator_indicators(self):
        self.oszilator_indicators.sort()
        return self.oszilator_indicators

    def get_overlay_indicators(self):
        self.overlay_indicators.sort()
        return self.overlay_indicators

    def get_class(self, short_name: str):
        """Returns the indicator class for the given short name, or None."""
        return self._classes.get(short_name)


def get_num_rows(df):
    return DataUtils.get_num_rows(df)

def ema(df, n=9, name='Close'):

    df = df.copy()
    df[f'ema{n}'] = df[name].ewm(span=n, adjust=False, min_periods=n).mean()

    return df


def sma(df, n=9, name='Close'):
    
    df = df.copy()
    df[f'MA{n}'] = df[name].rolling(int(n)).mean()

    return df
        

def rsi(df, lookback=8, window=14):

    df = df.copy()
    close = df['Close']
    ret = close.diff()    
    up = []
    down = []
    for i in range(len(ret)):
        if ret.iloc[i] < 0:
            up.append(0)
            down.append(ret.iloc[i])
        else:
            up.append(ret.iloc[i])
            down.append(0)
    up_series = pd.Series(up)
    down_series = pd.Series(down).abs()
    up_ewm = up_series.ewm(com = lookback - 1, adjust = False).mean()
    down_ewm = down_series.ewm(com = lookback - 1, adjust = False).mean()
    rs = up_ewm/down_ewm
    rsi = 100 - (100 / (1 + rs))
    rsi_df = pd.DataFrame(rsi).rename(columns = {0:'rsi'}).set_index(close.index)
    df['rsi'] = rsi_df.fillna(0)
    df['rsi_ema'] = df['rsi'].rolling(window).mean()
    
    return df


def momentum(df, window=14, smooth_window=3):
    # stochastic
    df = df.copy()
    stoch = ta.momentum.StochasticOscillator(high=df['High'],
                             close=df['Close'],
                             low=df['Low'],
                             window=window,
                             smooth_window=smooth_window)

    df['stoch'] = stoch.stoch()
    df['stoch_signal'] = stoch.stoch_signal()
    df['stoch_ema'] = df['stoch'].rolling(window).mean()
    
    return df

def angle(df):
    slope = df.diff()
    angle = np.degrees(np.arctan(slope))
    return angle

def buy_sell(df: pd.DataFrame, buy_query = '(zcr>-0.7)&(ewo>ewo_ema)', sell_query = '(zcr<0.7)|(ewo<ewo_ema)'):
    
    df = df.copy()
    evaluator = tools.ExpressionEvaluator(df,"df")
    buy_assets_transformed = evaluator.validate_and_transform(buy_query)        
    sell_assets_transformed = evaluator.validate_and_transform(sell_query)        
    df['crosszero'] = 0
    try:
        df['crosszero'] = np.where((eval(buy_assets_transformed)), 1,0)
    except Exception:
        pass
    df['position'] = df['crosszero'].diff()

    df['crosszero'] = 0
    try:
        df['crosszero'] = np.where((eval(sell_assets_transformed)), -1,0)
    except Exception:
        pass
    df['position'] = df['position'] + df['crosszero'].diff()

    df['buy_close'] =  np.where(df['position'] > 0,df['Close'],np.nan)
    df['sell_close'] =  np.where(df['position'] < 0,df['Close'],np.nan)
    return df


def log_return(df, window=50):

    df = df.copy()
    # Compute the logarithmic returns using the Closing price
    df['log_ret'] = np.log(df['Close'] / df['Close'].shift(1))
            
    # Compute Volatility using the pandas rolling standard deviation function
    df['log_vola'] = df['log_ret'].rolling(window=window).std() * np.sqrt(window)

    return df


def trend(df, trend_length=20, trend_start=0, trend_end=0):

    df = df.copy()
    def best_fit(y: np.array):
        '''
        Find the line of best fit gradient and intercept parameters via simple
        Linear Regression
        Parameters
        ----------
        y : np.array
            The time-series to find the linear regression line for
        Returns
        -------
        [m, c] : floats
            The gradient and intercept parameters, respectively
        '''
    
        x = np.arange(0, y.shape[0])
    
        x_bar = np.mean(x)
        y_bar = np.mean(y)

    
        xb = np.sum((x-x_bar)**2)
        if xb != 0:
            m = np.sum((x-x_bar)*(y-y_bar))/np.sum((x-x_bar)**2)
        else:
            m = 1
    
        c = y_bar - m*x_bar
    
        return m, c


    # update trend with most current data
    if trend_length < 3:
            trend_length = 3

    #    print('start:',trend_end, 'length:', trend_length)
    start = trend_end-trend_length
    end = trend_end
    #    print('start:',start, 'end:', end)
    if trend_end == 0:
        end = None
        
    m_high, c_high = best_fit(df['High'].values[start:end])
    m_low, c_low = best_fit(df['Low'].values[start:end])

    df['highTrend'] = np.nan
    df['lowTrend'] = np.nan
    
    for i in range(0,-trend_length,-1):
        high_t = float(m_high * -i + c_high)
        low_t = float(m_low * -i + c_low)
        try:
            df.loc[df.index[start-i],'highTrend'] = high_t
            df.loc[df.index[start-i],'lowTrend'] = low_t
        except Exception:
            pass

    return df


def trend_pct_df(df: pd.DataFrame, column='Close', date = None):
    
    df_n = df.copy()
    if not date == None:
#        try:
#            df_n.set_index('Date', inplace=True)
#           df_n.sort_index(inplace=True)
#        except Exception as e:
#            print(f"Error: {e}")
#            pass
        try:
            df_n.index = pd.to_datetime(df_n.index).normalize()
            cutoff = pd.Timestamp(date[:10])
            df_n = df_n[df_n.index <= cutoff]
        except Exception:
            pass
        
    pct = 0
    try:
        a = df_n[column].iloc[-1]
        b = df_n[column].iloc[-2]
        # just build the percentage of two values
        try:
            if a > 0:
                pct = round((a - b) / a, 4) * 100
        except Exception:
            pass
    
    except Exception:
        pass
    
    return round(pct,2)

def trend_pct(a, b):
    
    pct = 0
    try:
        # just build the percentage of two values
        try:
            if a > 0:
                pct = round((a - b) / a, 3) * 100
        except Exception:
            pass
    
    except Exception:
        pass
    
    return pct

def support_resistance(df, window=21):
    
    df = df.copy()
    if get_num_rows(df) == 0:
        return 0,0
    df['Support'] = df['Low'].rolling(window=window).min()
    df['Resistance'] = df['High'].rolling(window=window).max()
        
    return df['Support'].iloc[-1], df['Resistance'].iloc[-1]

def sharpe_ratio(df, N=252, rf=0.01):

    df = df.copy()
    series = df['Close']
    if len(df['Close']) > N:
        series = df['Close'][-N:]
            
    returns = series.pct_change(fill_method=None)
    mean = returns.mean() * N -rf
    sigma = returns.std() * np.sqrt(N)
    sharpe = mean / sigma

    return sharpe


def sortino_ratio(df, N=252, rf=0.01):
    
    df = df.copy()
    series = df['Close']
    if len(df['Close']) > N:
        series = df['Close'][-N:]

    # Handling zero or negative values
    series = series.replace(0, np.nan)  # Replace zeros with NaN

    log_returns= np.log(series.div(series.shift()))# Calculate Log-Returns
    log_returns.dropna(inplace=True)

    # Calculate the annualized average log return
    ann_returns = log_returns.mean() * N
    # Calculate the annualized standard deviation of log returns
    ann_std =  log_returns.std() * np.sqrt(N)

    # Select the Log Returns values that are below zero (negative returns)
    downside = log_returns[log_returns<0]
    # Calculate the annualized standard deviation of downside returns
    downside_ann_std =  downside.std() * np.sqrt(N)

    # Creating Summary DataFrame
    #summary = pd.DataFrame(data={"A. Returns":ann_returns,"A. Risk":ann_std })
    # Calculating and Adding Sortino Ratio
    #summary["Sortino Ratio"] = (ann_returns -rf) / downside_ann_std
    #summary.sort_values(by ="Sortino Ratio", ascending=False) #Sorting the DataFrame by Sortino Ratio

    sortino = 0

    if downside_ann_std > 0:
        sortino = (ann_returns -rf) / downside_ann_std

    return sortino

import numpy as np
import pandas as pd


def semi_volatility(df, N=252):
    """
    Berechnet die annualisierte Semi-Volatilität (Downside Volatility)
    basierend auf den logarithmischen Renditen der 'Close'-Preise.

    Parameter:
    ----------
    df : pd.DataFrame
        DataFrame mit einer Spalte 'Close', die die Schlusskurse enthält.
    N : int, optional
        Anzahl der Perioden pro Jahr (Standard: 252 für Handelstage).

    Rückgabe:
    ----------
    float
        Annualisierte Semi-Volatilität (nur negative Renditen werden berücksichtigt).
    """
    
    df = df.copy()
    series = df['Close']
    if len(series) > N:
        series = series[-N:]

    # Nullwerte behandeln
    series = series.replace(0, np.nan)
    series = series.dropna()

    # Logarithmische Renditen berechnen
    log_returns = np.log(series / series.shift(1))
    log_returns.dropna(inplace=True)

    # Nur negative Renditen (Downside)
    downside = log_returns[log_returns < 0]

    # Annualisierte Semi-Volatilität
    semi_vol = downside.std() * np.sqrt(N)

    return semi_vol


def target_prices(price, scaling = 0):

#    factor = round(price/1000,3)
    if price < 1000:
        factor = 1
    if price < 10:
        factor = 0.01
    if price < 2:
        factor = 0.001
#    if price > 100:
#        factor = 1
    if price > 1000:
        factor = 10
    if price > 50000:
        factor = 100
    if price > 100000:
        factor = 1000
    if scaling > 0:
        factor = scaling


    num = price / factor
    y = num

    def checker(x, z):
        if x < y < z:
            return y
        else:
            return ""
    
    if num:
        root = num ** 0.5
        minus_two = root - 2
        rounded = math.ceil(minus_two)
        result = rounded ** 2

        x = []
        sqr_x = []
        sqr_x_rounded = []
        sqr_x_rounded_root = []

        for i in range(24):
            x.append(rounded + 0.125 * (i + 1))
            sqr_x.append(x[i] * x[i])
            sqr_x_rounded.append(round(sqr_x[i] * 1000) / 1000)
            sqr_x_rounded_root.append(round((num - sqr_x_rounded[i]) * 1000) / 1000)

        min_positive_index = -1
        for i in range(len(sqr_x_rounded_root)):
            if sqr_x_rounded_root[i] < 0:
                min_positive_index = i
                break
        support = ''
        resistance = ''
        buy_target = ''
        sell_target = ''

        def roundup(number, places):
            factor = 10 ** places
            return round(number * factor) / factor
 
        if min_positive_index >= 0:
            buy_above = sqr_x_rounded[min_positive_index]
            sell_below = sqr_x_rounded[min_positive_index - 1]            
            support = (sqr_x_rounded[min_positive_index - 2])
            sell_target = (sqr_x_rounded[min_positive_index - 3])
            sell_target2 = (sqr_x_rounded[min_positive_index - 4])
            resistance = (sqr_x_rounded[min_positive_index + 1])
            buy_target = (sqr_x_rounded[min_positive_index + 2])
            buy_target2 = (sqr_x_rounded[min_positive_index + 3])
            buy_target3 = (sqr_x_rounded[min_positive_index + 4]) #(roundup(buy_target2 * 0.995, 2))
            sell_target3 = (sqr_x_rounded[min_positive_index -5])# (roundup(sell_target2 * 1.005, 2))
        else:
            buy_above = ""
            sell_below = ""
            support = ""
            resistance = ""
            buy_target = ""
            sell_target = ""
            buy_target2 = ""
            sell_target2 = ""
            buy_target3 = ""
            sell_target3 = ""

    fix = 4
    
    buy_above = round(buy_above * factor,fix)
    sell_below = round(sell_below * factor,fix)
    buy_target = round(buy_target * factor,fix)
    sell_target = round(sell_target * factor,fix)
    resistance = round(resistance * factor,fix)
    support = round(support * factor,fix)
    buy_target2 = round(buy_target2 * factor,fix)
    sell_target2 = round(sell_target2 * factor,fix)
    buy_target3 = round(buy_target3 * factor,fix)
    sell_target3 = round(sell_target3 * factor,fix)

    return (buy_above, buy_target, sell_below, sell_target, support, resistance, buy_target2, buy_target3, sell_target2, sell_target3 )
