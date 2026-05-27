import numpy as np
import sys
import plotly.graph_objects as go

try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	print('No Import')

from tradinglib.indicator import _indicator

class Cci(_indicator._Indicator):

	is_oszilator = True
	name = 'Comodity Channel Index'

	params = {
		'window': {'type': 'int', 'default': 14, 'min': 2, 'max': 200, 'label': 'CCI period'},
	}

	def __init__(self, df, symbol = "", window=14):

		self.window = window
		super().__init__(df=df, symbol=symbol)

		self.data()
#		self.add_fig()
		
		
	def data(self): #Cci

		TP = (self.df["High"] + self.df["Low"] + self.df["Close"]) / 3
		SMA = TP.rolling(window=self.window).mean()
		mad = TP.rolling(window=self.window).apply(
			lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
	    )
		self.df['cci'] = (TP - SMA) / (0.015 * mad)


	def add_fig(self):
					  
		self.fig = go.Figure()
		try:
			self.df.reset_index(inplace=True)
		except Exception:
			pass
  		# Plot cci trace on nth row
		self.fig.add_trace(
			go.Scatter(x=self.df['Date'],
					y=self.df['cci'],
#					visible = "legendonly",
					line_color = 'darkblue',
					name = f'cci',
					showlegend = False,
					)
	        )
			
		self.fig.add_hline(y=100, line_width=1, line_dash="dash", line_color="green", opacity = 1 )
		self.fig.add_hline(y=-100, line_width=1, line_dash="dash", line_color="red", opacity = 1 )
										
		pass
