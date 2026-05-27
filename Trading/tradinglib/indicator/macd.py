import ta
import sys
import plotly.graph_objects as go

try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	print('No Import')

from tradinglib.indicator import _indicator

class Macd(_indicator._Indicator):

	is_oszilator = True
	name = 'Moving Average Convergence Divergence'

	params = {
		'window_sign':    {'type': 'int',   'default': 9,  'min': 2, 'max': 100, 'label': 'Signal period'},
		'window_fast':    {'type': 'int',   'default': 12, 'min': 2, 'max': 100, 'label': 'Fast EMA period'},
		'window_slow':    {'type': 'int',   'default': 26, 'min': 2, 'max': 200, 'label': 'Slow EMA period'},
		'down_level':     {'type': 'int',   'default': 25, 'min': 1, 'max': 100, 'label': 'Down level threshold'},
		'color_macd':     {'type': 'color', 'default': '',             'label': 'MACD line color'},
		'color_signal':   {'type': 'color', 'default': '',             'label': 'Signal line color'},
	}

	def __init__(self, df, symbol = "", window_sign=9, window_fast=12, window_slow=26, down_level=25):
		self.window = window_sign
		self.window_fast = window_fast
		self.window_slow = window_slow
		self.down_level = down_level
		super().__init__(df=df, symbol=symbol)
		
		self.data()
#		self.add_fig()
		
		
	def data(self): # MACD
	
		macd = ta.trend.MACD(close=self.df['Close'],
			window_slow=self.window_slow,
			window_fast=self.window_fast,
			window_sign=self.window)

		self.df['macd'] = macd.macd()
		self.df['macd_diff'] = macd.macd_diff()
		self.df['macd_signal'] = macd.macd_signal()

	def add_fig(self):

		self.fig = go.Figure()
		try:
			self.df.reset_index(inplace=True)
		except Exception:
			pass

		colors = ['green' if val >= 0 
			else 'red' for val in self.df['macd_diff']]
		
		self.fig.add_trace(go.Bar(x=self.df['Date'],
					y=self.df['macd_diff'],
					marker_color=colors,
#					 visible = "legendonly",
					showlegend = False,
					name = f'MACD diff',
					))

		self.fig.add_trace(go.Scatter(x=self.df['Date'],
					y=self.df['macd'],
					name = f'MACD',
#					visible = "legendonly",
					showlegend = False,
					line=dict(color='black', width=2)
					))

		self.fig.add_trace(go.Scatter(x=self.df['Date'],
					y=self.df['macd_signal'],
#					visible = "legendonly",
					name = f'MACD signal',
					line=dict(color='blue', width=2),
					showlegend = False,
					))
"""		
		level = 0.3
		if self.df['macd_signal'].iloc[0] < level:
			level = 0.03
		self.fig.add_hline(y=level,
					line_width=1,
					name = '',
					line_dash="dot",
					line_color="darkgrey",
					)

		self.fig.add_hline(y=-level,
					  line_width=1,
					  name = '',
					  line_dash="dot",
					  line_color="darkgrey",
					  )

"""
