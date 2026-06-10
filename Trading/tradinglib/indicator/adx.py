import numpy as np
import plotly.graph_objects as go

try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    talib = None
    TALIB_AVAILABLE = False

import sys
try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	pass

from tradinglib.indicator import macd
from tradinglib.indicator import indicator

class Adx(macd.Macd):

	is_oszilator = True
	name = 'Average Directional Index'

	params = {
		'window':          {'type': 'int',   'default': 14, 'min': 2,  'max': 100, 'label': 'ADX period'},
		'window_fast':     {'type': 'int',   'default': 12, 'min': 2,  'max': 100, 'label': 'MACD fast period'},
		'window_slow':     {'type': 'int',   'default': 26, 'min': 2,  'max': 200, 'label': 'MACD slow period'},
		'down_level':      {'type': 'int',   'default': 25, 'min': 1,  'max': 100, 'label': 'ADX trend threshold'},
		'color_adx':       {'type': 'color', 'default': '',             'label': 'ADX line color'},
		'color_plus_di':   {'type': 'color', 'default': '',             'label': '+DI line color'},
		'color_minus_di':  {'type': 'color', 'default': '',             'label': '-DI line color'},
	}

	def __init__(self, df, symbol = "", window=14, window_fast=12, window_slow=26, down_level=25):
		"""Initialize the indicator with the provided DataFrame and optional symbol/params."""

		self.window = window
		self.window_fast = window_fast
		self.window_slow = window_slow
		self.down_level = down_level
		#Init		
		self.df = macd.Macd(df, window_fast=window_fast, window_slow=window_slow, window_sign=window).df
		super().__init__(df=self.df, symbol=symbol)

		self.data()
		
		
	def data(self): #Adx

		self.df["returns"] = np.log(self.df['Close'] / self.df['Close'].shift(1))

		if talib is not None:
			"""Compute the indicator values and attach them as columns to self.df."""
			self.df['adx']=talib.ADX(self.df['High'],self.df['Low'], self.df['Close'], self.window)
			self.df['adx_plus']=talib.PLUS_DI(self.df['High'],self.df['Low'], self.df['Close'], self.window)
			self.df['adx_minus']=talib.MINUS_DI(self.df['High'],self.df['Low'], self.df['Close'], self.window)
		else:
			self.df['adx'] = 0.0
			self.df['adx_plus'] = 0.0
			self.df['adx_minus'] = 0.0

		self.df['adx_angle'] = indicator.angle(self.df['adx'])

		conditions=[ (self.df['adx_plus']>self.df['adx_minus']) & (self.df['adx']>self.down_level) & (self.df['macd'] > 0),
					(self.df['adx_minus']>=self.df['adx_plus']) & (self.df['adx']>self.down_level) & (self.df['macd'] <= 0)
					]
					
		values=[1,-1]

		self.df['adx_position']=np.select(conditions,values,0)
		self.df['adx_bs_signal'] = self.df['adx_position'].diff()
		self.df['adx_buy'] = np.where(self.df['adx_bs_signal'] > 0,self.df['adx'],np.nan)
		self.df['adx_sell'] = np.where(self.df['adx_bs_signal'] < 0,self.df['adx'],np.nan)
		self.df['adx_buy_close'] = np.where(self.df['adx_bs_signal'] > 0,self.df['Close'],np.nan)
		self.df['adx_sell_close'] = np.where(self.df['adx_bs_signal'] < 0,self.df['Close'],np.nan)


	def add_fig(self):
		"""Add the indicator traces to the given Plotly figure."""
					  
		self.fig = go.Figure()
		try:
			self.df = self.df.reset_index()
		except Exception:
			pass
		try:
			self.fig.add_trace(
				go.Scatter(x =self.df['Date'],
						y = self.df['adx_buy'],
						mode = "markers",
						line_color = 'darkcyan',
						marker_symbol="triangle-up",
						marker_color="darkcyan",
						showlegend = False,
						marker_line_width=1, marker_size=10,
						name = 'Buy',
						opacity = 1)
     			)

		except Exception:
			pass

		try:
			self.fig.add_trace(
				go.Scatter(x = self.df['Date'],
						y = self.df['adx_sell'],
						mode = "markers",
						line_color = 'darkred',
						marker_symbol="triangle-down",
						marker_color="darkred",
						showlegend = False,
						marker_line_width=1, marker_size=10,
						name = 'Sell',
						opacity = 1)
				)

		except Exception:
			pass

		# Plot ADX trace on nth row
		self.fig.add_trace(
			go.Scatter(x=self.df['Date'],
					y=self.df['adx'],
					line_color = 'blue',
					name = f'adx',
					showlegend = False,
					)
	        )
					
		# Plot plus di trace on nth row
		self.fig.add_trace(
			go.Scatter(x=self.df['Date'],
					y=self.df['adx_plus'],
					line_color = 'green',
					name = f'adx plus',
					showlegend = False,
					)
  			)
					
		# Plot minus di trace on nth row
		self.fig.add_trace(
			go.Scatter(x=self.df['Date'],
					y=self.df['adx_minus'],
					line_color = 'red',
					name = f'adx minus',
					showlegend = False,
					)
			)
  		
	    #scale the y axis 0-100
		self.fig.add_hline(y=100, line_width=0.1, line_dash="dash", line_color="white", opacity = 1)
		self.fig.add_hline(y=0, line_width=0.1, line_dash="dash", line_color="white", opacity = 1)
		self.fig.add_hline(y=75, line_width=1, line_dash="dash", line_color="grey")
		self.fig.add_hline(y=25, line_width=1, line_dash="dash", line_color="green")		

