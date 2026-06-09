import sys
import numpy as np
import plotly.graph_objects as go

try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	pass

from tradinglib.indicator import _indicator
import pandas as pd

def calculate_atr(df, period=14):
    
    df = df.copy()  # Vermeidung von Änderungen im Original-DF
    df['High-Low'] = df['High'] - df['Low']
    df['High-Close'] = abs(df['High'] - df['Close'].shift(1))
    df['Low-Close'] = abs(df['Low'] - df['Close'].shift(1))
    
    df['TrueRange'] = df[['High-Low', 'High-Close', 'Low-Close']].max(axis=1)
    
    # ATR als einfacher gleitender Durchschnitt (SMA) oder EMA berechnen
    df['ATR'] = df['TrueRange'].rolling(window=period).mean()  # SMA-Version

    return df['ATR']

class Bos(_indicator._Indicator):

	is_oszilator = False
	name = 'Break of Structure'

	params = {
		'ignore_pips': {'type': 'float', 'default': 1.0,  'label': 'ATR pip filter (0 = off, 1 = normal)'},
		'color_bull':  {'type': 'color', 'default': '',    'label': 'Bullish breakout line color'},
		'color_bear':  {'type': 'color', 'default': '',    'label': 'Bearish breakout line color'},
	}

	def __init__(self, df, symbol = "", ignore_pips = 1):
		"""Initialize the indicator with the provided DataFrame and optional symbol/params."""

		super().__init__(df=df, symbol=symbol)
		self.break_points = []
		self.ignore_pips = ignore_pips
		self.data()

	
	def data(self):  # BoS mit ATR-basiertem Pip-Filter

		try:
			"""Compute the indicator values and attach them as columns to self.df."""
			self.df = self.df.reset_index()
		except Exception:
			pass

		self.df['bos_atr'] = calculate_atr(self.df, period=14)  # ATR berechnen

		previous_high_index = None
		previous_low_index = None

		for i in range(1, len(self.df)):
			high, low = self.df['High'][i], self.df['Low'][i]
			pip_size = self.df['bos_atr'][i] * 0.5 * self.ignore_pips  # Anpassung je nach Strategie

			# Neuer Hochpunkt im Up-Trend mit ATR-Mindestabstand
			if (
				previous_high_index is not None 
				and high > self.df['High'][previous_high_index] + pip_size
			):
				self.break_points.append(('Up', previous_high_index, i))
				previous_high_index = i  
			elif previous_high_index is None or high > self.df['High'][i - 1]:
				previous_high_index = i  

			# Neuer Tiefpunkt im Down-Trend mit ATR-Mindestabstand
			if (
				previous_low_index is not None 
				and low < self.df['Low'][previous_low_index] - pip_size
			):
				self.break_points.append(('Down', previous_low_index, i))
				previous_low_index = i  
			elif previous_low_index is None or low < self.df['Low'][i - 1]:
				previous_low_index = i  

			pass

	def add_fig(self):
		"""Add the indicator traces to the given Plotly figure."""
					  
		self.fig = go.Figure()
		try:
			self.df = self.df.reset_index()
		except Exception:
			pass

		self.fig = go.Figure()
		
		for trend_type, prev_index, current_index in self.break_points:
			prev_date = self.df['Date'].iloc[prev_index]
			current_date = self.df['Date'].iloc[current_index]
			value = self.df['High'].iloc[prev_index] if trend_type == 'Up' else self.df['Low'].iloc[prev_index]
			
			self.fig.add_trace(go.Scatter(
				x=[prev_date, current_date],
				y=[value, value],
				mode='lines',
				line=dict(dash='dot', color='green' if trend_type == 'Up' else 'red'),
				name=f"{trend_type} Break",
				showlegend=False  # Vermeidet mehrfachen Legenden-Eintrag
			))
		

		pass

