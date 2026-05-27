import numpy as np
import sys
import plotly.graph_objects as go
import streamlit as st 

try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	print('No Import')

from tradinglib.indicator import _indicator
from tradinglib.indicator import indicator

#import streamlit as st 

class Ewo(_indicator._Indicator):

	is_oszilator = True
	name = 'Elliot Wave Oszillator'

	params = {
		'short_window': {'type': 'int',   'default': 5,    'min': 2,     'max': 50,  'label': 'EWO short SMA period'},
		'long_window':  {'type': 'int',   'default': 21,   'min': 5,     'max': 200, 'label': 'EWO long SMA period'},
		'ema_span':     {'type': 'int',   'default': 9,    'min': 2,     'max': 50,  'label': 'EMA span'},
		'angle':        {'type': 'float', 'default': 0.01, 'min': 0.001, 'max': 1.0, 'label': 'Signal angle threshold'},
	}

	def __init__(self, df, symbol="", short_window=5, long_window=21, ema_span=9, angle=0.01):
		self.short_window = short_window
		self.long_window = long_window
		self.ema_span = ema_span
		self.angle = angle
		super().__init__(df=df, symbol=symbol)

		self.data()
#		self.add_fig()
		
	def find_local_extrema(self, series, window=3):
		highs = []
		lows = []
	
		for i in range(window, len(series) - window):
			local_window = series[i - window : i + window + 1]  # Lokaler Ausschnitt
		
			if series[i] == max(local_window):  # Höchster Wert im Fenster → High
				highs.append(i)
			elif series[i] == min(local_window):  # Niedrigster Wert im Fenster → Low
				lows.append(i)
	
		return np.array(highs), np.array(lows)	

	def compute_ewo(self, df, short_window=5, long_window=21):
		"""
		Berechnet den Elliott Wave Oscillator (EWO).
	
		Parameter:
			df (pd.DataFrame): DataFrame mit 'Close'-Preisen.
			short_window (int): Zeitraum für den kurzen SMA (Standard: 5).
			long_window (int): Zeitraum für den langen SMA (Standard: 34).
	
		Rückgabe:
			pd.Series: Werte des EWO.
		"""
		short_sma = df['Close'].rolling(window=short_window).mean()
		long_sma = df['Close'].rolling(window=long_window).mean()

		return short_sma - long_sma		

	def filter_alternating_signals(self,angle=0.01):
		state = "NEUTRAL"  # Initialzustand
		buy_signals = []
		sell_signals = []
#		st.write(self.df)
		self.df['ewo_buy_signal'] = np.where((self.df['ewo_angle']>angle),self.df['ewo'],np.nan)
		self.df['ewo_sell_signal'] = np.where((self.df['ewo_angle']<-angle),self.df['ewo'],np.nan)

		for buy, sell in zip(self.df['ewo_buy_signal'], self.df['ewo_sell_signal']):
			if state == "NEUTRAL":
				if not np.isnan(buy):
					buy_signals.append(buy)
					sell_signals.append(np.nan)
					state = "HOLDING"
				else:
					buy_signals.append(np.nan)
					sell_signals.append(np.nan)
			elif state == "HOLDING":
				if not np.isnan(sell):
					buy_signals.append(np.nan)
					sell_signals.append(sell)
					state = "NEUTRAL"
				else:
					buy_signals.append(np.nan)
					sell_signals.append(np.nan)

		self.df['ewo_buy'] = buy_signals
		self.df['ewo_sell'] = sell_signals
		self.df.drop(columns=["ewo_buy_signal","ewo_sell_signal"])

	def data(self): # EWO

		self.df['ewo'] = self.compute_ewo(self.df, self.short_window, self.long_window)
		self.df['ewo_ema'] = self.df['ewo'].transform(lambda x: x.ewm(span=self.ema_span, adjust=False).mean())
		self.df['ewo_diff'] = self.df['ewo_ema'].diff()
		self.df['ewo_angle'] = indicator.angle(self.df['ewo'])
		self.filter_alternating_signals(self.angle)

	def add_fig(self):

		show_ewo = True
		show_ewo_diff = True
		self.fig = go.Figure()
		try:
			self.df.reset_index(inplace=True)
		except Exception:
			pass

		if show_ewo_diff:
			colors = ['green' if val >= 0 
				else 'red' for val in self.df['ewo_diff']]
		
			self.fig.add_trace(
				go.Bar(x=self.df['Date'],
					y=self.df['ewo_diff'],
					marker_color=colors,
#					 visible = "legendonly",
					showlegend = False,
					name = f'EWO diff',
					))

		if show_ewo:
			self.fig.add_trace(
				go.Scatter(x =self.df['Date'],
						y = self.df['ewo_buy'],
						mode = "markers",
						line_color = 'darkcyan',
						marker_symbol="triangle-up",
						marker_color="darkcyan",
						showlegend = False,
						marker_line_width=1, marker_size=10,
						name = 'Buy',
						opacity = 1)
				)
			
			self.fig.add_trace(
				go.Scatter(x =self.df['Date'],
						y = self.df['ewo_sell'],
						mode = "markers",
						line_color = 'darkred',
						marker_symbol="triangle-down",
						marker_color="darkred",
						showlegend = False,
						marker_line_width=1, marker_size=10,
						name = 'Sell',
						opacity = 1)
	 			)

			self.fig.add_trace(
				go.Scatter(x=self.df['Date'],
					y=self.df['ewo'],
					name = f'EWO',
#					visible = "legendonly",
					showlegend = False,
					line=dict(color='black', width=2)
					))

#			self.fig.add_trace(
#				go.Scatter(x=self.df['Date'],
#					y=self.df['ewo_cum'],
#					name = f'EWO Cum',
#					visible = "legendonly",
#					showlegend = False,
#					line=dict(color='green', width=2)
#					))

			self.fig.add_trace(
				go.Scatter(x=self.df['Date'],
					y=self.df['ewo_ema'],
					name = f'EMA',
#					visible = "legendonly",
					showlegend = False,
					line=dict(color='orange', width=2)
					))

			# we assume a positive market mude and plot a green dotted line if ewo avg is > 0 
			val = self.df['ewo'].sum() #.median()

			colors = 'red'
			if val >= 0:
				colors = 'green'  

			max = self.df['ewo'].max()
			min = self.df['ewo'].min()

			self.fig.add_hline(y=max,
					  line_width=2,
					  name = '',
					  line_dash="dot",
					  line_color=colors,
					  )

			self.fig.add_hline(y=min,
					  line_width=2,
					  name = '',
					  line_dash="dot",
					  line_color=colors,
					  )

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
