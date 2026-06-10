import numpy as np
import sys
import plotly.graph_objects as go

try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	pass

from tradinglib.indicator import _indicator

class Lqz(_indicator._Indicator):

	is_oszilator = False
	name = 'Liquiditiy zones'

	params = {
		'window':    {'type': 'int',   'default': 14,  'min': 2, 'max': 200, 'label': 'Volume window'},
		'threshold': {'type': 'float', 'default': 1.5,             'label': 'Volume threshold (std multiplier)'},
	}

	def __init__(self, df, symbol = "",  window=14, threshold=1.5):
		"""Initialize the indicator with the provided DataFrame and optional symbol/params."""

		self.threshold = threshold
		self.window = window
		super().__init__(df=df, symbol=symbol)

		self.data()
		
		
	def data(self): #Ici

		# Create price bins (clusters) based on price volatility
		bin_size = self.df['Close'].std() * 0.005
		self.df['price_bin'] = (self.df['Close'] / bin_size).round() * bin_size
		# Sum the volume for each price bin
		try:
			"""Compute the indicator values and attach them as columns to self.df."""
			volume_profile = self.df.groupby('price_bin')['Volume'].sum()
			volume_profile = volume_profile.sort_values(ascending=False)
			# Find zones with abnormally high volume
			mean_vol = volume_profile.mean()
			std_vol = volume_profile.std()
			self.liquidity_zones = volume_profile[volume_profile > mean_vol + self.threshold * std_vol]
		except Exception:
			pass

	def add_fig(self):
		"""Add the indicator traces to the given Plotly figure."""
					  
		self.fig = go.Figure()
		try:
			self.df = self.df.reset_index()
		except Exception:
			pass

		# Normalize volume data for color mapping
		try:
			volume_values = self.liquidity_zones.values
#			normalized_volumes = (volume_values - volume_values.min()) / (volume_values.max() - volume_values.min())
		
			# Add liquidity zones as horizontal lines with varying color depth
			for zone, volume in self.liquidity_zones.items():
				normalized_volume = (volume - volume_values.min()) / (volume_values.max() - volume_values.min())
				gray_scale = int(200 - (normalized_volume * 200))  # Map volume to grayscale (0 .. 200)
				color = f'rgba({gray_scale}, {gray_scale}, {gray_scale}, 0.7)'  # Gray with transparency
				try:
					self.fig.add_hline(
						y=zone,
						opacity=1,
						line=dict(color=color, width=2, dash="dash"),
					)
					self.fig.add_annotation(
						x=0.0,
						y=zone,
						xref='paper',
						yref='y',
						text=f' {round(zone, 1)} ',
						showarrow=False,
						xanchor='left',
						yanchor='middle',
						bgcolor=color,
						font=dict(color='black', size=13),
						bordercolor=color,
						borderpad=3,
						opacity=0.9,
					)
				except Exception:
					pass
		except Exception:
			pass

