import sys
import pandas as pd
import plotly.graph_objects as go

try:
	sys.path.insert(0, "../../tradinglib.indicators")
except ImportError:
	print('No Import')

from tradinglib.indicator import _indicator
from tradinglib.indicator import indicator

class Rsi(_indicator._Indicator):

	is_oszilator = True
	name = 'Relative Strenght Index'

	params = {
		'lookback': {'type': 'int', 'default': 8,  'min': 2, 'max': 50,  'label': 'EWM lookback'},
		'window':   {'type': 'int', 'default': 14, 'min': 2, 'max': 100, 'label': 'Smoothing window'},
	}

	def __init__(self, df, symbol = "", lookback=8, window=14):

		self.window = window
		self.lookback = lookback
		super().__init__(df=df, symbol=symbol)
		
		self.data()
#		self.add_fig()
		
		
	def data(self): # MACD
	
		# momentum helper for RSI sub-plot (stored separately to avoid collision with stoch indicator)
		self.df['rsi_momentum'] = indicator.momentum(self.df)['stoch']
		close = self.df['Close']
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
		up_ewm = up_series.ewm(com = self.lookback - 1, adjust = False).mean()
		down_ewm = down_series.ewm(com = self.lookback - 1, adjust = False).mean()
		rs = up_ewm/down_ewm
		rsi = 100 - (100 / (1 + rs))
		rsi_df = pd.DataFrame(rsi).rename(columns = {0:'rsi'}).set_index(close.index)
		self.df['rsi'] = rsi_df.fillna(0)
		self.df['rsi_ema'] = self.df['rsi'].rolling(self.window).mean()


	def add_fig(self):

		self.fig = go.Figure()
		try:
			self.df.reset_index(inplace=True)
		except Exception:
			pass

		# Plot RSI
		self.fig.add_trace(go.Scatter(x=self.df['Date'],
						y=self.df['rsi'],
						name = f'RSI',
						line=dict(color='blue', width=2),
     					showlegend = False,
						))

		self.fig.add_trace(go.Scatter(x=self.df['Date'],
						y=self.df['rsi_ema'],
						name = f'RSI EMA',
						line=dict(color='grey', width=2),
						showlegend = False,
      					))

		self.fig.add_trace(go.Scatter(x=self.df['Date'],
						y=self.df['rsi_momentum'],
						name = f'Momentum',
						line=dict(color='green', width=2),
						showlegend = False,
						))
		
		self.fig.add_hline(y=75,
					  line_width=1,
					  name = '',
					  line_dash="dot",
					  line_color="darkgreen",
					  )

		self.fig.add_hline(y=25,
					  line_width=1,
					  name = '',
					  line_dash="dot",
					  line_color="darkred",
					  )

		self.fig.add_hline(y=50,
					  line_width=1,
					  name = '',
					  line_dash="dash",
					  line_color="darkgrey",
					  )


