import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go

try:
	sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
	pass

from tradinglib.indicator import _indicator


# Forward-return horizons, in trading days counted from the start of the year
# (e.g. 21 trading days ~ 1 month). 'eoy' uses each year's last available value.
_HORIZON_STEPS = {'1mo': 21, '3mo': 63}


class Scr(_indicator._Indicator):

	is_oszilator = True
	name = 'Seasonal Score Oszillator'

	params = {
		'scale':     {'type': 'float', 'default': 10.0, 'min': 1.0, 'max': 50.0, 'label': 'Normalisierungs-Skala (%)'},
		'min_years': {'type': 'int',   'default': 3,    'min': 2,   'max': 20,  'label': 'Min. Vorjahre fuer ein Signal'},
		'threshold': {'type': 'float', 'default': 30.0, 'min': 5.0, 'max': 90.0, 'label': 'Buy/Sell-Schwelle (+/-)'},
	}

	def __init__(self, df, symbol="", scale=10.0, min_years=3, threshold=30.0):
		"""Initialize the indicator with the provided DataFrame and optional symbol/params."""
		self.scale = scale
		self.min_years = min_years
		self.threshold = threshold
		super().__init__(df=df, symbol=symbol)

		self.data()

	def _history(self):
		"""Return a DataFrame with enough years of 'Close' history to compute
		seasonal scores.

		The live chart hands the indicator only the currently displayed window
		(e.g. 6mo), which never contains enough prior years on its own. In that
		case, reload the full local price history for `self.symbol` instead.
		Falls back to self.df if no symbol is set or no local history exists.
		"""
		dates = pd.to_datetime(self.df.index, errors='coerce')
		years_in_df = dates.year.nunique() if len(dates) else 0
		if years_in_df >= self.min_years + 1 or not self.symbol:
			return self.df

		try:
			from tradinglib.fetch_data import FetchData
			from tradinglib.utils import DataUtils
			hist = FetchData(database_path='database').load_price_data(self.symbol, '10y', '1d')
			hist = DataUtils.ensure_datetime_index(hist)
			if hist is not None and not hist.empty and 'Close' in hist.columns:
				return hist
		except Exception:
			pass
		return self.df

	def seasonal_scores(self):
		"""Compute the seasonal score (-100..+100) for each row and horizon.

		For every row at trading-day-of-year position T, average the direct
		forward price return that the *same* position T produced in all strictly
		prior years. Using raw price returns (close.shift(-step)/close - 1)
		instead of within-year normalisation avoids year-boundary NaN gaps: a
		December row naturally looks 21/63 trading days ahead into January of
		the following year.

		Returns a DataFrame (same index as self.df) with columns
		'scr_1mo', 'scr_3mo', 'scr_eoy'.
		"""
		hist = self._history()
		dates = pd.to_datetime(hist.index, errors='coerce')
		close = pd.to_numeric(hist['Close'], errors='coerce')

		year = pd.Series(dates.year, index=hist.index)
		# 1-based trading-day-of-year -> shared alignment grid across years
		tday = year.groupby(year).cumcount() + 1

		idx = pd.MultiIndex.from_arrays([year.to_numpy(), tday.to_numpy()])
		out = {}
		for label, step in (('1mo', _HORIZON_STEPS['1mo']), ('3mo', _HORIZON_STEPS['3mo']), ('eoy', None)):
			if step is not None:
				# Direct forward return — shift on the full series, crosses year
				# boundaries naturally, no end-of-year NaN gap.
				fwd_series = close.shift(-step) / close - 1.0
			else:
				# Year-end: return from this row to the last known close of the
				# same calendar year (for past full years = Dec 31 close).
				year_end = close.groupby(year).transform('last')
				fwd_series = year_end / close - 1.0

			pivot = pd.DataFrame({
				'year': year.to_numpy(),
				'tday': tday.to_numpy(),
				'fwd':  fwd_series.to_numpy(),
			}).pivot(index='year', columns='tday', values='fwd') \
			  .sort_index(axis=0).sort_index(axis=1)

			# Expanding mean down the year axis, shifted by one year so a
			# row's score only uses years strictly before its own year.
			avg_pct = pivot.expanding(min_periods=self.min_years).mean().shift(1) * 100.0
			score = (avg_pct / self.scale * 100.0).clip(-100.0, 100.0)

			out[f'scr_{label}'] = score.stack().reindex(idx).to_numpy()

		return pd.DataFrame(out, index=hist.index).reindex(self.df.index)

	def filter_alternating_signals(self):
		"""Ensure buy/sell markers strictly alternate, based on scr_1mo crossing
		+/-threshold."""
		buy_raw = np.where(self.df['scr_1mo'] > self.threshold, self.df['scr_1mo'], np.nan)
		sell_raw = np.where(self.df['scr_1mo'] < -self.threshold, self.df['scr_1mo'], np.nan)

		state = "NEUTRAL"
		buy_signals, sell_signals = [], []
		for buy, sell in zip(buy_raw, sell_raw):
			if state == "NEUTRAL":
				if not np.isnan(buy):
					buy_signals.append(buy)
					sell_signals.append(np.nan)
					state = "HOLDING"
				else:
					buy_signals.append(np.nan)
					sell_signals.append(np.nan)
			else:  # HOLDING
				if not np.isnan(sell):
					buy_signals.append(np.nan)
					sell_signals.append(sell)
					state = "NEUTRAL"
				else:
					buy_signals.append(np.nan)
					sell_signals.append(np.nan)

		self.df['scr_buy'] = buy_signals
		self.df['scr_sell'] = sell_signals

	def data(self):  # Seasonal Score Oszillator
		scores = self.seasonal_scores()
		self.df['scr_1mo'] = scores['scr_1mo']
		self.df['scr_3mo'] = scores['scr_3mo']
		self.df['scr_eoy'] = scores['scr_eoy']
		self.filter_alternating_signals()

	def add_fig(self):
		"""Add the indicator traces to the given Plotly figure."""

		self.fig = go.Figure()
		try:
			self.df = self.df.reset_index()
		except Exception:
			pass

		self.fig.add_trace(go.Scatter(x=self.df['Date'], y=self.df['scr_1mo'],
			name='scr 1mo', showlegend=False, line=dict(color='black', width=2)))
		self.fig.add_trace(go.Scatter(x=self.df['Date'], y=self.df['scr_3mo'],
			name='scr 3mo', showlegend=False, line=dict(color='orange', width=1.5)))
		self.fig.add_trace(go.Scatter(x=self.df['Date'], y=self.df['scr_eoy'],
			name='scr eoy', showlegend=False, line=dict(color='grey', width=1, dash='dot')))

		self.fig.add_trace(go.Scatter(x=self.df['Date'], y=self.df['scr_buy'],
			mode="markers", marker_symbol="triangle-up", marker_color="darkcyan",
			marker_line_width=1, marker_size=10, name='Buy', showlegend=False, opacity=1))
		self.fig.add_trace(go.Scatter(x=self.df['Date'], y=self.df['scr_sell'],
			mode="markers", marker_symbol="triangle-down", marker_color="darkred",
			marker_line_width=1, marker_size=10, name='Sell', showlegend=False, opacity=1))

		self.fig.add_hline(y=self.threshold, line_width=1, line_dash="dot", line_color="green")
		self.fig.add_hline(y=-self.threshold, line_width=1, line_dash="dot", line_color="red")
		self.fig.add_hline(y=0, line_width=1, line_dash="dot", line_color="grey")
