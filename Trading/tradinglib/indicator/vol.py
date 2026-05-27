import ta
import sys
import pandas as pd
import plotly.graph_objects as go

try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	print('No Import')

from tradinglib.indicator import _indicator

class Vol(_indicator._Indicator):

	is_oszilator = True
	name = 'Volume'

	params = {
		'cumd':        {'type': 'bool',  'default': False, 'label': 'Show cumulative delta'},
		'color_bull':  {'type': 'color', 'default': '',    'label': 'Bull bar color'},
		'color_bear':  {'type': 'color', 'default': '',    'label': 'Bear bar color'},
	}

	def __init__(self, df, symbol = "", cumd=False):
		super().__init__(df=df, symbol=symbol)
		self.cumd = cumd
		
		self.data()
#		self.add_fig()
		
		
	def data(self): # Volume
	
		pass
		
	def calculate_cumulative_delta(self, df, ma_window=21):
		"""
		Berechnet kumulatives Delta und optional einen gleitenden Durchschnitt.
		"""
		delta = []
		for i in range(len(df)):
			if i == 0:
				delta.append(0)
			else:
				if df['Close'].iloc[i] > df['Close'].iloc[i-1]:
					delta.append(df['Volume'].iloc[i])   # Kaufvolumen
				elif df['Close'].iloc[i] < df['Close'].iloc[i-1]:
					delta.append(-df['Volume'].iloc[i])  # Verkaufvolumen
				else:
					delta.append(0)

		df['vol_delta'] = delta
		df['vol_cum_delta'] = pd.Series(delta).cumsum()

		# SMA über CumDelta
		df['vol_cum_delta_sma'] = df['vol_cum_delta'].rolling(ma_window).mean()

		return df

	def add_cumulative_delta(self, ma_window=20):
		"""
		Zeichnet den kumulativen Delta Indikator + Durchschnitt in die Figur.
		"""
		if 'vol_cum_delta' not in self.df.columns or 'vol_cum_delta_sma' not in self.df.columns:
			self.df = self.calculate_cumulative_delta(self.df, ma_window=ma_window)

		# CumDelta Balken
		colors = ['green' if row['CumDelta'] >= 0 else 'red' for _, row in self.df.iterrows()]
		self.fig.add_trace(go.Bar(
			x=self.df['Date'], 
			y=self.df['vol_cum_delta'],
			name='CumDelta',
			marker_color=colors,
			showlegend=False,
		))

		# SMA Linie
		self.fig.add_trace(go.Scatter(
			x=self.df['Date'],
			y=self.df['vol_cum_delta_sma'],
			mode='lines',
			line=dict(color='blue', width=2),
			name=f'CumDelta SMA({ma_window})',
			showlegend=False
		))


	def add_fig(self):

		self.fig = go.Figure()
		try:
			self.df.reset_index(inplace=True)
		except Exception:
			pass

		# Plot volume trace
		colors = ['green' if row['Open'] - row['Close'] <= 0 
		  else 'red' for index, row in self.df.iterrows()]

		self.fig.add_trace(go.Bar(x=self.df['Date'], 
			y=self.df['Volume'],
			name = 'Volume',
			marker_color=colors,
			showlegend = False,
			))

		if self.cumd:
			self.add_cumulative_delta()
