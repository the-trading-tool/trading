import numpy as np
import pandas as pd
import sys
import plotly.graph_objects as go
import streamlit as st 

try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	pass

from tradinglib.indicator import _indicator
from tradinglib.indicator import _elliott
from tradinglib.indicator import indicator

WAVE_COLORS = {'impulse': 'royalblue', 'abc': 'darkorange', 'running': 'grey'}


class Ewo(_indicator._Indicator):

	is_oszilator = True
	name = 'Elliot Wave Oszillator'

	params = {
		'short_window': {'type': 'int',   'default': 5,    'min': 2,     'max': 50,  'label': 'EWO short SMA period'},
		'long_window':  {'type': 'int',   'default': 21,   'min': 5,     'max': 200, 'label': 'EWO long SMA period'},
		'ema_span':     {'type': 'int',   'default': 9,    'min': 2,     'max': 50,  'label': 'EMA span'},
		'angle':        {'type': 'float', 'default': 0.01, 'min': 0.001, 'max': 1.0, 'label': 'Signal angle threshold'},
		# Elliott wave labels (see _elliott.py). The ewo_wave column is always
		# computed; these switches only control what the chart draws.
		'show_waves':       {'type': 'bool',  'default': False, 'label': 'Elliott waves: label impulse 1-5'},
		'show_abc':         {'type': 'bool',  'default': True,  'label': 'Elliott waves: label correction A-B-C'},
		'show_pending':     {'type': 'bool',  'default': True,  'label': 'Elliott waves: label unfinished count (grey, ?)'},
		'require_ewo_peak': {'type': 'bool',  'default': True,  'label': 'Elliott waves: wave 3 must carry the EWO peak'},
		'require_w4_ewo_zero': {'type': 'bool', 'default': False,
		                     'label': 'Elliott waves: EWO must pull back to zero in wave 4'},
		'allow_truncation': {'type': 'bool',  'default': False, 'label': 'Elliott waves: allow truncated wave 5'},
		'wave_ewo_basis':   {'type': 'select', 'default': 'ewo', 'options': ['ewo', 'joseph_5_35'],
		                     'label': 'Elliott waves: EWO for the wave rules (joseph_5_35 = SMA 5/35 of (High+Low)/2)'},
		'wave_atr_mult':    {'type': 'float', 'default': 3.0,   'min': 1.0, 'max': 10.0, 'step': 0.5,
		                     'label': 'Elliott waves: pivot reversal (x ATR)'},
	}

	def __init__(self, df, symbol="", short_window=5, long_window=21, ema_span=9, angle=0.01,
				 show_waves=False, show_abc=True, show_pending=True, require_ewo_peak=True,
				 wave_atr_mult=3.0, require_w4_ewo_zero=False, allow_truncation=False,
				 wave_ewo_basis='ewo'):
		"""Initialize the indicator with the provided DataFrame and optional symbol/params."""
		self.short_window = short_window
		self.long_window = long_window
		self.ema_span = ema_span
		self.angle = angle
		self.show_waves = bool(show_waves)
		self.show_abc = bool(show_abc)
		self.show_pending = bool(show_pending)
		self.require_ewo_peak = bool(require_ewo_peak)
		self.wave_atr_mult = float(wave_atr_mult)
		self.require_w4_ewo_zero = bool(require_w4_ewo_zero)
		self.allow_truncation = bool(allow_truncation)
		self.wave_ewo_basis = str(wave_ewo_basis or 'ewo')
		self.wave_labels = []
		super().__init__(df=df, symbol=symbol)

		self.data()
		
	def find_local_extrema(self, series, window=3):
		"""Find local price extrema within the configured window."""
		highs = []
		lows = []
	
		for i in range(window, len(series) - window):
			local_window = series[i - window : i + window + 1]  # local slice

			if series[i] == max(local_window):  # highest value in window → High
				highs.append(i)
			elif series[i] == min(local_window):  # lowest value in window → Low
				lows.append(i)
	
		return np.array(highs), np.array(lows)	

	def compute_ewo(self, df, short_window=5, long_window=21):
		"""
		Computes the Elliott Wave Oscillator (EWO).

		Parameters:
			df (pd.DataFrame): DataFrame with 'Close' prices.
			short_window (int): Period for the short SMA (default: 5).
			long_window (int): Period for the long SMA (default: 34).

		Returns:
			pd.Series: EWO values.
		"""
		short_sma = df['Close'].rolling(window=short_window).mean()
		long_sma = df['Close'].rolling(window=long_window).mean()

		return short_sma - long_sma		

	def filter_alternating_signals(self,angle=0.01):
		"""Ensure buy/sell signals strictly alternate."""
		state = "NEUTRAL"  # initial state
		buy_signals = []
		sell_signals = []
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
		# Direction of EWO relative to the previous day's value (+1 rising, -1 falling);
		# used e.g. for ewo_trend_day/wk/mo (asset_perf2)
		self.df['ewo_trend'] = np.where(self.df['ewo'].diff() > 0, 1, -1)
		self.filter_alternating_signals(self.angle)
		self.elliott_waves()

	def elliott_waves(self):
		"""Causal Elliott count: ewo_wave column + chart labels (see _elliott.py)."""
		self.wave_labels = []
		try:
			# The Joseph variant only feeds the wave rules; the ewo column (used
			# by formulas and backtests) keeps the configured SMA windows.
			if self.wave_ewo_basis == 'joseph_5_35':
				basis = _elliott.joseph_ewo(self.df)
			else:
				basis = self.df['ewo'].to_numpy(dtype=float)
			wave, labels = _elliott.elliott(self.df, basis,
											mult=self.wave_atr_mult,
											require_ewo_peak=self.require_ewo_peak,
											allow_truncation=self.allow_truncation,
											require_w4_ewo_zero=self.require_w4_ewo_zero)
		except (KeyError, ValueError, TypeError):
			self.df['ewo_wave'] = 0
			return
		self.df['ewo_wave'] = wave
		dates = self.df['Date'] if 'Date' in self.df.columns else self.df.index
		for lb in labels:
			lb['date'] = dates[lb['i']] if isinstance(dates, pd.Index) else dates.iloc[lb['i']]
			self.wave_labels.append(lb)

	def _add_wave_labels(self):
		"""Draw the wave labels as a text trace on the EWO line.

		A text trace, not layout annotations: tiny_chart moves sub-plot
		annotations to row 1 and breaks their x reference.
		"""
		if not self.show_waves or not self.wave_labels or 'Date' not in self.df.columns:
			return
		ewo_by_date = dict(zip(self.df['Date'], self.df['ewo']))
		groups = {}
		for lb in self.wave_labels:
			if lb['group'] == 'abc' and not self.show_abc:
				continue
			if lb['state'] != 'done' and not self.show_pending:
				continue
			y = ewo_by_date.get(lb['date'])
			if y is None or y != y:
				continue
			color = WAVE_COLORS['running'] if lb['state'] != 'done' else WAVE_COLORS[lb['group']]
			pos = 'top center' if lb['kind'] > 0 else 'bottom center'
			g = groups.setdefault((color, pos), {'x': [], 'y': [], 't': [], 'h': []})
			g['x'].append(lb['date'])
			g['y'].append(y)
			g['t'].append(f"<b>{lb['text']}</b>")
			form = lb.get('form')
			g['h'].append(f"{lb['text']} ({form})" if form else lb['text'])
		for (color, pos), g in groups.items():
			self.fig.add_trace(
				go.Scatter(x=g['x'], y=g['y'], text=g['t'],
						   hovertext=g['h'],
						   mode='markers+text',
						   textposition=pos,
						   textfont=dict(color=color, size=14),
						   marker=dict(color=color, size=5),
						   showlegend=False,
						   hoverinfo='x+text',
						   name='Elliott'))

	def add_fig(self):
		"""Compute the indicator values and attach them as columns to self.df."""
		"""Add the indicator traces to the given Plotly figure."""

		show_ewo = True
		show_ewo_diff = True
		self.fig = go.Figure()
		try:
			self.df = self.df.reset_index()
		except Exception:
			pass

		if show_ewo_diff:
			colors = ['green' if val >= 0 
				else 'red' for val in self.df['ewo_diff']]
		
			self.fig.add_trace(
				go.Bar(x=self.df['Date'],
					y=self.df['ewo_diff'],
					marker_color=colors,
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
					showlegend = False,
					line=dict(color='black', width=2)
					))

#					))

			self.fig.add_trace(
				go.Scatter(x=self.df['Date'],
					y=self.df['ewo_ema'],
					name = f'EMA',
					showlegend = False,
					line=dict(color='orange', width=2)
					))

			self._add_wave_labels()

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

