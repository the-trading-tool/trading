import numpy as np
import pandas as pd
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
		'tenkan_window':      {'type': 'int',   'default': 9,  'min': 2, 'max': 100, 'label': 'Tenkan-sen period'},
		'window':             {'type': 'int',   'default': 14, 'min': 2, 'max': 100, 'label': 'EMA period'},
		'color_tenkan':       {'type': 'color', 'default': '',  'label': 'Tenkan-sen color'},
		'color_kijun':        {'type': 'color', 'default': '',  'label': 'Kijun-sen color'},
		'color_chikou':       {'type': 'color', 'default': '',  'label': 'Chikou span color'},
		'color_ema':          {'type': 'color', 'default': '',  'label': 'EMA color'},
		'fill_cloud':         {'type': 'bool',  'default': True, 'label': 'Fill Kumo cloud'},
		'color_cloud_bull':   {'type': 'color', 'default': '',  'label': 'Bullish cloud color'},
		'color_cloud_bear':   {'type': 'color', 'default': '',  'label': 'Bearish cloud color'},
	}

	def __init__(self, df, symbol = "", window=14, tenkan_window=9,
				 fill_cloud=True,
				 color_tenkan='', color_kijun='', color_chikou='', color_ema='',
				 color_cloud_bull='', color_cloud_bear=''):
		"""Initialize the indicator with the provided DataFrame and optional symbol/params."""

		super().__init__(df=df, symbol=symbol)
		self.window = window
		self.tenkan_window = tenkan_window
		self.fill_cloud       = fill_cloud
		self.color_tenkan     = color_tenkan     or 'deepskyblue'
		self.color_kijun      = color_kijun      or 'orange'
		self.color_chikou     = color_chikou     or 'gray'
		self.color_ema        = color_ema        or 'yellow'
		self.color_cloud_bull = color_cloud_bull or 'rgba(0,255,0,0.22)'
		self.color_cloud_bear = color_cloud_bear or 'rgba(255,0,0,0.22)'

		self.data()
		
		
	def data(self): #Ici
		"""Compute the indicator values and attach them as columns to self.df."""

		# Calculate the Ichimoku indicators
		# Tenkan-sen = midpoint of the highest high / lowest low over the last
		# tenkan_window bars (standard Ichimoku: 9). Using the *current* bar's
		# (High+Low)/2 — i.e. a 1-period Tenkan — makes the line roughly twice as
		# noisy and, via senkou_span_a = (tenkan+kijun)/2, pushes the cloud edge
		# off by ~100 DAX points on average (up to ~2% of price) versus the
		# Pine/TradingView export.
		self.df['tenkan_sen'] = (
			self.df['High'].rolling(window=self.tenkan_window).max() +
			self.df['Low'].rolling(window=self.tenkan_window).min()
		) / 2
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

		# Upper / lower edge of the projected Kumo cloud
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
		# Draw each contiguous bull/bear section as its own polygon
		# to avoid overlapping fills.
		# =========================================================

		x = self.df['Date']
		span_a = self.df['senkou_span_a']
		span_b = self.df['senkou_span_b']

		# =========================================================
		# FORWARD PROJECTION OF THE KUMO CLOUD
		# Senkou A/B are shifted +26 bars. The base values of the
		# most recent 26 bars therefore belong to dates *beyond* the
		# last candle — but there are no future rows to hold them, so
		# .shift(26) drops them and the cloud stops at the last candle.
		# Rebuild those 26 base values and append 26 future bars so the
		# cloud extends into the future (standard Ichimoku behaviour).
		# self.df is left untouched — only the local cloud arrays grow,
		# and Plotly auto-extends the x-axis because this trace carries
		# the future x-values.
		# =========================================================
		try:
			n_fwd = 26
			x_dt = pd.to_datetime(x, errors='coerce')
			valid_dt = x_dt.dropna()

			if len(valid_dt) > n_fwd:
				# Unshifted Senkou bases (same formulas as data(), pre-shift)
				base_a = (self.df['tenkan_sen'] + self.df['kijun_sen']) / 2.0
				base_b = (
					self.df['High'].rolling(window=52).max() +
					self.df['Low'].rolling(window=52).min()
				) / 2.0

				# Median positive bar spacing (rangebreak-agnostic)
				deltas = valid_dt.diff().dropna()
				pos = deltas[deltas > pd.Timedelta(0)]
				step = pos.median()

				# Assets that genuinely trade on weekends (crypto/FX) keep
				# their weekend bars; everything else skips Sat/Sun so no
				# future vertex lands on a collapsed rangebreak day.
				trades_weekend = bool((valid_dt.dt.dayofweek >= 5).mean() > 0.15)

				if pd.notna(step) and step > pd.Timedelta(0):
					last_dt = valid_dt.iloc[-1]
					future_dates = []
					guard = 0

					# Intraday: walk the *observed session slots* rather than a flat
					# step. A flat step marches straight through the night (4h bars →
					# 20:00/00:00/04:00), and those hours sit inside the x-axis'
					# pattern="hour" night rangebreak, so Plotly collapses them: half
					# the projected bars land on hidden positions and the Kumo looks
					# squashed. Cycling the real slots (e.g. 08/12/16) keeps every
					# projected vertex on a visible x position.
					if step < pd.Timedelta(days=1):
						slots = sorted({
							float(h) + float(mi) / 60.0
							for h, mi in zip(valid_dt.dt.hour, valid_dt.dt.minute)
						})
					else:
						slots = []

					if slots:
						last_slot = last_dt.hour + last_dt.minute / 60.0
						day = last_dt.normalize()
						# first slot strictly after the last candle (else: next day)
						idx = next((i for i, s in enumerate(slots) if s > last_slot),
								   len(slots))
						while len(future_dates) < n_fwd and guard < n_fwd * 10:
							guard += 1
							if idx >= len(slots):
								idx = 0
								day = day + pd.Timedelta(days=1)
							if (not trades_weekend) and day.dayofweek >= 5:
								idx = len(slots)   # force advance to the next day
								continue
							future_dates.append(day + pd.Timedelta(hours=slots[idx]))
							idx += 1
					else:
						# Daily and coarser: one bar per day, a flat step is correct.
						cur = last_dt
						while len(future_dates) < n_fwd and guard < n_fwd * 5:
							cur = cur + step
							guard += 1
							if (not trades_weekend) and cur.dayofweek >= 5:
								continue
							future_dates.append(cur)

					fut_a = base_a.iloc[-n_fwd:].to_numpy(dtype=float)
					fut_b = base_b.iloc[-n_fwd:].to_numpy(dtype=float)
					m = min(len(future_dates), n_fwd)

					x = pd.Series(list(x_dt) + future_dates[:m])
					span_a = pd.concat(
						[span_a, pd.Series(fut_a[:m])], ignore_index=True)
					span_b = pd.concat(
						[span_b, pd.Series(fut_b[:m])], ignore_index=True)
				else:
					x = x_dt
			else:
				x = x_dt
		except Exception:
			pass

		valid = span_a.notna() & span_b.notna()
		bull_int = (span_a >= span_b).astype(int)
		# NaN rows → -1 so they don't contaminate the first real segment
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

			# Insert intersection at the start (transition from the previous segment)
			if first_pos > 0 and valid_arr[first_pos - 1]:
				yc = _cross_y(first_pos - 1, first_pos)
				x_list.insert(0, x_arr[first_pos])
				a_list.insert(0, yc)
				b_list.insert(0, yc)

			# Insert intersection at the end (transition to the next segment)
			if last_pos + 1 < n and valid_arr[last_pos + 1]:
				yc = _cross_y(last_pos, last_pos + 1)
				x_list.append(x_arr[last_pos + 1])
				a_list.append(yc)
				b_list.append(yc)

			x_seg = np.array(x_list)
			a_seg = np.array(a_list)
			b_seg = np.array(b_list)

			fill_color = self.color_cloud_bull if is_bull else self.color_cloud_bear
			cloud_name = 'Bullish Cloud' if is_bull else 'Bearish Cloud'

			if not self.fill_cloud:
				continue

			# Split each bull/bear cloud run further at time gaps so no fill
			# polygon spans an x-axis rangebreak (weekend/holiday), which Plotly
			# balloons into a wedge — see _Indicator.segmented_band.
			xs, ys = self.segmented_band(x_seg, a_seg, b_seg)
			self.fig.add_trace(
				go.Scatter(
					x=xs,
					y=ys,
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
				line=dict(color=self.color_tenkan, width=1),
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
				line=dict(color=self.color_kijun, width=1),
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
					color=self.color_ema,
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
					color=self.color_chikou,
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
