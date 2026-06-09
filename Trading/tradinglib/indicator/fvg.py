import numpy as np
import sys
import plotly.graph_objects as go

try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	pass

from tradinglib.indicator import _indicator

class Fvg(_indicator._Indicator):

	is_oszilator = False
	name = 'Fair Value Gap'

	params = {
		'percentage': {'type': 'int', 'default': 50, 'min': 1, 'max': 100, 'label': 'Gap filter percentage'},
	}

	def __init__(self, df, symbol = "", percentage=50):
		"""Initialize the indicator with the provided DataFrame and optional symbol/params."""

		self.percentage = percentage
		super().__init__(df=df, symbol=symbol)
		self.df['fvg'] = None #self.df['High'] - (self.df['High']-self.df['Low'])

		self.data()

	def data(self): #Fvg
    
		if self.has_index(self.df):
			"""Compute the indicator values and attach them as columns to self.df."""
			self.df = self.df.reset_index()

		high_shifted = self.df['High'].shift(periods=2)
		condition = (high_shifted < self.df['Low'])
		self.df['fvg'] = np.where(condition, (high_shifted + self.df['Low']) / 2, self.df['fvg'])
		self.df['gap_type'] = np.where(condition, 'bullish', None)
		self.df['gap_start_date'] = np.where(condition, self.df['Date'].shift(periods=2), None)
		self.df['gap_end_date'] = np.where(condition, self.df['Date'], None)

		low_shifted = self.df['Low'].shift(periods=2)
		condition = (low_shifted > self.df['High'])
		self.df['fvg'] = np.where(condition, (low_shifted + self.df['High']) / 2, self.df['fvg'])
		self.df['gap_type'] = np.where(condition, 'bearish', self.df['gap_type'])
		self.df['gap_start_date'] = np.where(condition, self.df['Date'].shift(periods=2), self.df['gap_start_date'])
		self.df['gap_end_date'] = np.where(condition, self.df['Date'], self.df['gap_end_date'])

		try:
			self.df = self.df.set_index('Date')
		except Exception:
			pass

	def add_fig(self):
		"""Add the indicator traces to the given Plotly figure."""
					  
		self.fig = go.Figure()
		try:
			self.df = self.df.reset_index()
		except Exception:
			pass

		"""
		gap_data = self.df[self.df['fvg'].notna()]
		for index, row in gap_data.iterrows():
			color = 'red' if row['gap_type'] == 'bearish' else 'green'
			self.fig.add_shape(
				type="line",
				x0=row['gap_start_date'],
				x1=row['gap_end_date'],
				y0=row['fvg'],
				y1=row['fvg'],
				line=dict(color=color, width=2, dash='dash')
			)
		"""
  		# mark fvg 
		self.fig.add_trace(
			go.Scatter(x=self.df['Date'].shift(periods=1),
					y=self.df['fvg'],
                    mode = "markers",
					line_color = 'gray',
                    marker_symbol= "line-ew", #"square-cross", 
                    marker_color="blue",
                    marker_line_width=2, marker_size=20,
					name = f'fvg',
					showlegend = False,
					)
			)
		pass

