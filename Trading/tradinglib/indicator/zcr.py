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

from tradinglib.indicator import _indicator

class Zcr(_indicator._Indicator): # Zscore

	is_oszilator = True
	name = 'Z-Score Indicator'

	params = {
		'window': {'type': 'int', 'default': 20, 'min': 2, 'max': 200, 'label': 'Z-Score window'},
	}

	def __init__(self, df, symbol = "", window=20):
		"""Initialize the indicator with the provided DataFrame and optional symbol/params."""

		self.window = window
		self.symbol = symbol
		self.df = df
		self.data()
		
	def calculate_z_score(self):
		mean = self.df['Close'].rolling(window=self.window).mean()
		std = self.df['Close'].rolling(window=self.window).std()
		z_score = (self.df['Close'] - mean) / std
		return z_score

	def data(self): #Z-Score
		# Calculate Z-score based on the closing price
		self.df['zcr'] = self.calculate_z_score()


	def add_fig(self):
		"""Compute the indicator values and attach them as columns to self.df."""
		"""Add the indicator traces to the given Plotly figure."""
					  
		self.fig = go.Figure()
		try:
			self.df = self.df.reset_index()
		except Exception:
			pass

		# Plot zcr trace on nth row
		self.fig.add_trace(
			go.Scatter(x=self.df['Date'],
					y=self.df['zcr'],
					line_color = 'blue',
					name = f'Z-score',
					showlegend = False,
					)
	        )
					  		
	    #scale the y axis -2 to +2
#		self.fig.add_hline(y=.25, line_width=1, line_dash="dash", line_color="red", opacity = 1)
#		self.fig.add_hline(y=-.25, line_width=1, line_dash="dash", line_color="green", opacity = 1)
		self.fig.add_hline(y=1, line_width=1, line_dash="dash", line_color="red", opacity = 1)
		self.fig.add_hline(y=-1, line_width=1, line_dash="dash", line_color="green", opacity = 1)
		self.fig.add_hline(y=2, line_width=.5, line_dash="dash", line_color="grey", opacity = .8)
		self.fig.add_hline(y=-2, line_width=.5, line_dash="dash", line_color="grey", opacity = .8)

