import ta
import sys
import plotly.graph_objects as go

try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	pass

from tradinglib.indicator import _indicator
from tradinglib.indicator import indicator

class Stoch(_indicator._Indicator):

	is_oszilator = True
	name = 'Stochastic Oszilator (Momentum)'

	params = {
		'window':        {'type': 'int', 'default': 14, 'min': 2, 'max': 100, 'label': 'Stochastic window'},
		'smooth_window': {'type': 'int', 'default': 3,  'min': 1, 'max': 20,  'label': 'Smoothing window'},
	}

	def __init__(self, df, symbol = "", window=14, smooth_window=3):
		"""Initialize the indicator with the provided DataFrame and optional symbol/params."""
		self.window = window
		self.smooth_window = smooth_window
		super().__init__(df=df, symbol=symbol)
		
		self.data()
		
		
	def data(self): # Momentum
		"""Compute the indicator values and attach them as columns to self.df."""
	
		stoch = ta.momentum.StochasticOscillator(high=self.df['High'],
					close=self.df['Close'],
					low=self.df['Low'],
					window=self.window,
					smooth_window=self.smooth_window)
	 	
		self.df['stoch'] = stoch.stoch()
		self.df['stoch_signal'] = stoch.stoch_signal()
		self.df['momentum_ema'] = self.df['stoch'].rolling(self.window).mean()
		self.df['momentum_ema_angle'] = indicator.angle(self.df['momentum_ema'])


	def add_fig(self):
		"""Add the indicator traces to the given Plotly figure."""

		self.fig = go.Figure()
		try:
			self.df = self.df.reset_index()
		except Exception:
			pass

		# Plot stochastics trace on 4th row
		self.fig.add_trace(go.Scatter(x=self.df['Date'],
						y=self.df['stoch'],
						name = f'Momentum',
						line=dict(color='black', width=2),
						showlegend = False,
						))
		
		self.fig.add_trace(go.Scatter(x=self.df['Date'],
						y=self.df['stoch_signal'],
						name = f'Mom. signal',
						visible = "legendonly",
						line=dict(color='blue', width=1),
						showlegend = False,
						))

