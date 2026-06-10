import numpy as np
import sys
import plotly.graph_objects as go

try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	pass

from tradinglib.indicator import _indicator

class Ici(_indicator._Indicator):

	is_oszilator = False
	name = 'Ichimoku indicators'

	params = {
		'window':             {'type': 'int',   'default': 14, 'min': 2, 'max': 100, 'label': 'Tenkan-sen period'},
		'color_tenkan':       {'type': 'color', 'default': '',  'label': 'Tenkan-sen color'},
		'color_kijun':        {'type': 'color', 'default': '',  'label': 'Kijun-sen color'},
		'color_chikou':       {'type': 'color', 'default': '',  'label': 'Chikou span color'},
		'color_ema':          {'type': 'color', 'default': '',  'label': 'EMA color'},
		'color_cloud_bull':   {'type': 'color', 'default': '',  'label': 'Bullish cloud color'},
		'color_cloud_bear':   {'type': 'color', 'default': '',  'label': 'Bearish cloud color'},
	}

	def __init__(self, df, symbol = "", window=14):
		"""Initialize the indicator with the provided DataFrame and optional symbol/params."""

		super().__init__(df=df, symbol=symbol)
		self.window = window

		self.data()
		
		
	def data(self): #Ici
		"""Compute the indicator values and attach them as columns to self.df."""

		# Calculate the Ichimoku indicators
		self.df['tenkan_sen'] = (self.df['High'] + self.df['Low']) / 2
		self.df['kijun_sen'] = (self.df['High'].rolling(window=26).max() + self.df['Low'].rolling(window=26).min()) / 2
#		self.df['senkou_span_a'] = (self.df['tenkan_sen'] + self.df['kijun_sen']) / 2
#		self.df['senkou_span_b'] = (self.df['High'].rolling(window=52).max() + self.df['Low'].rolling(window=52).min()) / 2
		self.df['senkou_span_a'] = (
			(self.df['tenkan_sen'] + self.df['kijun_sen']) / 2
		).shift(26)

		self.df['senkou_span_b'] = (
			(self.df['High'].rolling(window=52).max() +
			self.df['Low'].rolling(window=52).min()) / 2
		).shift(26)
		self.df['chikou_span'] = self.df['Close'].shift(-26)

		# Oberkante / Unterkante der projizierten Kumo-Cloud
		self.df['cloud_top']    = self.df[['senkou_span_a', 'senkou_span_b']].max(axis=1)
		self.df['cloud_bottom'] = self.df[['senkou_span_a', 'senkou_span_b']].min(axis=1)

		# Calculate the 14 EMA
		self.df[f'ema{self.window}'] = self.df['Close'].ewm(span=self.window, adjust=False, min_periods=self.window).mean()


		# Create the signals, Create buy and sell signals column for the Ichimoku strategy
		signal_conditions = [
			(self.df['Close'] > self.df['cloud_top']),
			(self.df['Close'] < self.df['cloud_bottom']),
		]
		signal_choices = [1, -1]

		self.df['signal'] = np.select(
			signal_conditions,
			signal_choices,
			default=0
		)
		# remove the look ahead bias by creating a signal lag of one period
		self.df['signal'] = self.df['signal'].shift(1)
		prev = self.df['signal'].shift(1).fillna(0)
		self.df['ici_buy']  = np.where((self.df['signal'] > 0) & (prev <= 0), self.df['Close'], np.nan)
		self.df['ici_sell'] = np.where((self.df['signal'] < 0) & (prev >= 0), self.df['Close'], np.nan)
       
		bull_mask = self.df['senkou_span_a'] >= self.df['senkou_span_b']
		bear_mask = ~bull_mask

		self.df['bullish_a'] = self.df['senkou_span_a'].where(bull_mask)
		self.df['bullish_b'] = self.df['senkou_span_b'].where(bull_mask)

		self.df['bearish_a'] = self.df['senkou_span_a'].where(bear_mask)
		self.df['bearish_b'] = self.df['senkou_span_b'].where(bear_mask)

	def add_fig(self):
		"""Add the indicator traces to the given Plotly figure."""

		self.fig = go.Figure()

		try:
			self.df = self.df.reset_index()
		except Exception:
			pass

		# =========================================================
		# ICHIMOKU CLOUD (SEGMENT-BASED POLYGON VERSION)
		# Jeden zusammenhängenden Bull/Bear-Abschnitt als eigenes
		# Polygon zeichnen, damit keine Überlappungen entstehen.
		# =========================================================

		x = self.df['Date']
		span_a = self.df['senkou_span_a']
		span_b = self.df['senkou_span_b']

		valid = span_a.notna() & span_b.notna()
		bull_int = (span_a >= span_b).astype(int)
		# NaN-Zeilen → -1, damit sie nicht das erste echte Segment kontaminieren
		bull_int = bull_int.where(valid, -1)

		segment_ids = (bull_int != bull_int.shift()).cumsum()

		a_arr    = span_a.values.astype(float)
		b_arr    = span_b.values.astype(float)
		x_arr    = x.values
		valid_arr = valid.values
		n = len(a_arr)

		def _cross_y(i, j):
			da, db = a_arr[i] - b_arr[i], a_arr[j] - b_arr[j]
			denom = da - db
			if abs(denom) < 1e-10:
				return (a_arr[i] + a_arr[j]) / 2.0
			alpha = da / denom
			return float(a_arr[i] + alpha * (a_arr[j] - a_arr[i]))

		for seg_id in segment_ids.unique():
			seg_mask = (segment_ids == seg_id).values
			seg_type = int(bull_int[seg_mask].iloc[0])

			if seg_type == -1:
				continue

			is_bull = (seg_type == 1)
			positions  = np.where(seg_mask)[0]
			first_pos  = positions[0]
			last_pos   = positions[-1]

			x_list = list(x_arr[positions])
			a_list = list(a_arr[positions])
			b_list = list(b_arr[positions])

			# Schnittpunkt am Anfang einfügen (Übergang vom vorigen Segment)
			if first_pos > 0 and valid_arr[first_pos - 1]:
				yc = _cross_y(first_pos - 1, first_pos)
				x_list.insert(0, x_arr[first_pos])
				a_list.insert(0, yc)
				b_list.insert(0, yc)

			# Schnittpunkt am Ende einfügen (Übergang zum nächsten Segment)
			if last_pos + 1 < n and valid_arr[last_pos + 1]:
				yc = _cross_y(last_pos, last_pos + 1)
				x_list.append(x_arr[last_pos + 1])
				a_list.append(yc)
				b_list.append(yc)

			x_seg = np.array(x_list)
			a_seg = np.array(a_list)
			b_seg = np.array(b_list)

			fill_color = 'rgba(0,255,0,0.22)' if is_bull else 'rgba(255,0,0,0.22)'
			cloud_name = 'Bullish Cloud' if is_bull else 'Bearish Cloud'

			self.fig.add_trace(
				go.Scatter(
					x=np.concatenate([x_seg, x_seg[::-1]]),
					y=np.concatenate([a_seg, b_seg[::-1]]),
					fill='toself',
					fillcolor=fill_color,
					line=dict(color='rgba(0,0,0,0)'),
					showlegend=False,
					hoverinfo='skip',
					name=cloud_name
				)
			)

		# =========================================================
		# CANDLESTICKS
		# =========================================================

		self.fig.add_trace(
			go.Candlestick(
				x=self.df['Date'],
				open=self.df['Open'],
				high=self.df['High'],
				low=self.df['Low'],
				close=self.df['Close'],
				name='Price',
				showlegend=False,
				increasing_line_color='lime',
				decreasing_line_color='red'
			)
		)

		# =========================================================
		# TENKAN SEN
		# =========================================================

		self.fig.add_trace(
			go.Scatter(
				x=self.df['Date'],
				y=self.df['tenkan_sen'],
				mode='lines',
				showlegend=False,
				line=dict(color='deepskyblue', width=1),
				name='Tenkan Sen'
			)
		)

		# =========================================================
		# KIJUN SEN
		# =========================================================

		self.fig.add_trace(
			go.Scatter(
				x=self.df['Date'],
				y=self.df['kijun_sen'],
				mode='lines',
				showlegend=False,
				line=dict(color='orange', width=1),
				name='Kijun Sen'
			)
		)

		# =========================================================
		# EMA
		# =========================================================

		self.fig.add_trace(
			go.Scatter(
				x=self.df['Date'],
				y=self.df[f'ema{self.window}'],
				mode='lines',
				line=dict(
					color='yellow',
					width=1,
					dash='dash'
				),
				showlegend=False,
				name=f'EMA {self.window}'
			)
		)

		# =========================================================
		# CHIKOU SPAN
		# =========================================================

		self.fig.add_trace(
			go.Scatter(
				x=self.df['Date'],
				y=self.df['chikou_span'],
				mode='lines',
				line=dict(
					color='gray',
					width=1
				),
				showlegend=False,
				opacity=0.6,
				name='Chikou Span'
			)
		)

		# =========================================================
		# BUY SIGNALS
		# =========================================================

		self.fig.add_trace(
			go.Scatter(
				x=self.df['Date'],
				y=self.df['ici_buy'],
				mode='markers',
				marker=dict(
					symbol='triangle-up',
					color='lime',
					size=10,
					line=dict(width=1)
				),
				showlegend=False,
				name='Buy'
			)
		)

		# =========================================================
		# SELL SIGNALS
		# =========================================================

		self.fig.add_trace(
			go.Scatter(
				x=self.df['Date'],
				y=self.df['ici_sell'],
				mode='markers',
				marker=dict(
					symbol='triangle-down',
					color='red',
					size=10,
					line=dict(width=1)
				),
				showlegend=False,
				name='Sell'
			)
		)

		# =========================================================
		# LAYOUT
		# =========================================================

		self.fig.update_layout(
			template='plotly_dark',

			title=f'{self.symbol} - Ichimoku Cloud',

			xaxis=dict(
				rangeslider=dict(visible=False),
				showgrid=False
			),

			yaxis=dict(
				showgrid=True,
				gridcolor='rgba(255,255,255,0.05)'
			),

			plot_bgcolor='black',
			paper_bgcolor='black',

			hovermode='x unified',

			legend=dict(
				orientation='h',
				yanchor='bottom',
				y=1.02,
				xanchor='right',
				x=1
			),

			margin=dict(
				l=10,
				r=10,
				t=40,
				b=10
			),

			height=900
		)

		# =========================================================
		# REMOVE WEEKENDS
		# =========================================================

		self.fig.update_xaxes(
			rangebreaks=[
				dict(bounds=["sat", "mon"])
			]
		)
