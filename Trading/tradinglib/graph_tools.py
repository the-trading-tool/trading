import logging
import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import time
from tradinglib import tools

logger = logging.getLogger(__name__)

chart_config = {
                    "scrollZoom": True,
                    "displayModeBar": True,
                    'editSelection': True,
                    'editable': False,
                    "modeBarButtonsToAdd": [
                        "drawline",
                        "drawopenpath",
                        "drawclosedpath",
                        "drawcircle",
                        "drawrect",
                        "eraseshape"]
                    }    

class GraphTools:
    
    def time_string_to_decimals(self, time_string):
        """Convert an 'HH:MM:SS' string to a decimal hour value (e.g. '09:30:00' → 9.5)."""
        fields = time_string.split(":")
        hours = fields[0] if len(fields) > 0 else 0.0
        minutes = fields[1] if len(fields) > 1 else 0.0
        seconds = fields[2] if len(fields) > 2 else 0.0
    
        return float(hours) + (float(minutes) / 60.0) + (float(seconds) / pow(60.0, 2))

    def prepare_df(self, df):
        """Reset the index, add a dt_idx column, and ensure a DatetimeIndex for range-break calculations."""
        df = df.reset_index(drop=True)
        df['dt_idx'] = df['Date']
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.set_index('dt_idx')

        # Ensure the index is a DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # Step 2: Determine trading hours and non-trading days
        df['date'] = df.index.date
        df['time'] = df.index.time

        return df

    def get_range_breaks(self, df, exchange='', extended_hours=False):
        """Return Plotly rangebreaks for the given DataFrame and exchange code (delegates to NewV3)."""
        breaks = self.get_range_breaks_NewV3(df, exchange=exchange,
                                             extended_hours=extended_hours)
        return breaks
    

    def get_range_breaks_NewV3(self, df, exchange='', extended_hours=False):
        """Compute Plotly rangebreaks by deriving trading hours directly from the data.

        Automatically detects 24/7 assets (crypto) and skips overnight breaks for them.
        For intraday data, night gaps are added; for daily data, missing weekdays
        (holidays) are added as explicit value breaks.
        """
        df = df.copy()
        df['Date'] = pd.to_datetime(df['Date'])

        # 1. Determine interval
        dt_counts = df['Date'].diff().value_counts()
        if dt_counts.empty:
            return []
        freq = dt_counts.index[0]
        freq_h = freq.total_seconds() / 3600

        dow = df['Date'].dt.dayofweek

        # 2. Crypto/24-7 detection. Since the tz_info display conversion,
        #    US post-market (Sat 0-2 AM Berlin time) and Forex candles can
        #    land on Saturday — mere presence of Sat+Sun is therefore no longer
        #    sufficient for intraday: substantial coverage of both days is required.
        if freq < pd.Timedelta(days=1):
            sat_cov = df.loc[dow == 5, 'Date'].dt.hour.nunique()
            sun_cov = df.loc[dow == 6, 'Date'].dt.hour.nunique()
            is_24_7 = (exchange in ['CCX', 'CCC']) or (sat_cov >= 6 and sun_cov >= 6)
        else:
            is_24_7 = (exchange in ['CCX', 'CCC']) or (dow.eq(5).any() and dow.eq(6).any())
        if is_24_7:
            return []

        breaks = []

        if freq < pd.Timedelta(days=1):
            # --- INTRADAY: one break per real gap between consecutive session bars.
            #
            # The former approach guessed ONE hour window for all days
            # (pattern="hour") plus a weekend break. That fails as soon as the
            # data is irregular: bank holidays (Labor Day left a whole day of
            # empty axis), half days, trading halts, and illiquid names where
            # scattered pre-market trades filled the hour histogram (FMAO 30m:
            # computed night 02:00-10:00, the axis showed 16 h per day).
            # Explicit date bounds per gap cover all of these with one rule and
            # never mix pattern + date bounds (the zigzag bug).
            return self._dynamic_intraday_breaks(df, freq, extended_hours)

        else:
            # --- DAILY (only) ---
            # Rangebreaks only make sense for DAILY bars. Weekly/monthly bars are
            # >= ~7 days apart: there are no intra-bar gaps worth collapsing, so
            # breaks give no benefit and actively harm the render:
            #   * the weekend break ["sat","mon"] on weekly bars — which sit on
            #     Mondays, exactly on the break's end boundary — makes Plotly draw
            #     the connecting lines (oscillators, MAs) as broken dashes;
            #   * the holiday `values` break would flag ~4 of every 5 business days
            #     as "absent" (one bar per week) and blank out ~80% of the axis.
            # So return no breaks for anything coarser than daily.
            if freq > pd.Timedelta(days=1):
                return []

            breaks.append(dict(bounds=["sat", "mon"]))

            # Missing trading days (bank holidays) via values — daily only.
            all_workdays = pd.date_range(
                start=df['Date'].min(), end=df['Date'].max(), freq='B'
            )
            present_days = df['Date'].dt.normalize().unique()
            missing_days = all_workdays[~all_workdays.normalize().isin(present_days)]

            if not missing_days.empty:
                holiday_list = [d.strftime('%Y-%m-%d') for d in missing_days]
                breaks.append(dict(values=holiday_list))

        return breaks

    # Upper bound on explicit intraday breaks. Plotly handles a few hundred
    # without noticeable cost; beyond that only the longest gaps are kept (nights,
    # weekends and holidays are always the longest, so they survive the cut).
    MAX_INTRADAY_BREAKS = 400
    # A time bin belongs to the regular session when it is populated on at least
    # this share of the days of the best-covered bin.
    SESSION_MIN_COVERAGE = 0.25

    @staticmethod
    def _second_series_filter(df, extended_hours):
        """Rows that define the session (volume block filter).

        Hides off-exchange bars by default: for AAPL the 8,634 regular-session
        bars carry volume and sit on :30, the 8,200 pre/post-market bars sit on
        :00 with volume 0.

        Two cases are left alone on purpose: series WITHOUT any volume (indices
        such as ^GDAXI) and series where every bar carries volume. The volume-less
        part must also be a multi-hour BLOCK, otherwise the filter would hit the
        opening auction of European single stocks (SAP.DE, VOW.DE, AZN.L -- ONE
        hour each without volume).
        """
        if extended_hours or 'Volume' not in df.columns:
            return df
        vol = pd.to_numeric(df['Volume'], errors='coerce').fillna(0)
        leer = df[vol <= 0]
        anteil = len(leer) / max(len(df), 1)
        stunden = leer['Date'].dt.hour.nunique() if len(leer) else 0
        if (vol > 0).any() and stunden >= 2 and anteil >= 0.20:
            kept = df[vol > 0]
            return kept if not kept.empty else df
        return df

    def session_mask(self, df, freq, extended_hours=False):
        """Bool Series: which bars belong to the regular session.

        With ``extended_hours`` every bar counts. Otherwise the session is the
        largest contiguous block of time bins that are populated on enough DAYS
        (not bars): scattered pre-market trades of an illiquid name hit a bin on
        a few days only, the regular session on almost all of them. Contiguity is
        circular, so sessions that cross midnight in display time (US names in
        Berlin time) stay one block. Isolated populated bins -- e.g. a nightly
        post-market print -- form their own small block and are left out.
        """
        dates = df['Date']
        if extended_hours or dates.empty:
            return pd.Series(True, index=df.index)
        freq_h = freq.total_seconds() / 3600.0
        bin_h = min(max(freq_h, 0.5), 1.0)
        tol = max(freq_h, bin_h) + 1e-9

        def _bin(s):
            dec = s.dt.hour + s.dt.minute / 60.0
            return (dec // bin_h) * bin_h

        weekday = df[dates.dt.dayofweek < 5]
        ref = self._second_series_filter(weekday if not weekday.empty else df,
                                         extended_hours)
        cov = ref.groupby(_bin(ref['Date']))['Date'].agg(lambda s: s.dt.date.nunique())
        if cov.empty:
            return pd.Series(True, index=df.index)
        keep = sorted(cov[cov >= max(1, self.SESSION_MIN_COVERAGE * cov.max())].index)
        if not keep:
            return pd.Series(True, index=df.index)

        # circular runs of adjacent bins
        runs = [[keep[0]]]
        for prev, cur in zip(keep, keep[1:]):
            if cur - prev <= tol:
                runs[-1].append(cur)
            else:
                runs.append([cur])
        if len(runs) > 1 and (keep[0] + 24 - keep[-1]) <= tol:
            runs[0] = runs.pop() + runs[0]
        best = max(runs, key=lambda r: cov.loc[r].sum())
        return _bin(dates).isin(set(best))

    def _dynamic_intraday_breaks(self, df, freq, extended_hours=False):
        """Explicit date-bound breaks for every gap between session bars.

        Bars outside the session (pre/post market, when not shown) lie inside
        these gaps and are hidden by them, exactly as the old hour pattern did.
        """
        dates = df['Date']
        mask = self.session_mask(df, freq, extended_hours)
        sess = dates[mask].drop_duplicates().sort_values()
        if sess.empty:
            return []
        fmt = '%Y-%m-%d %H:%M:%S'
        gaps = []
        sess = sess.reset_index(drop=True)
        nxt = sess.shift(-1)
        # A bar occupies at most one interval, but a closer predecessor makes it
        # narrower: the Xetra closing auction sits at 17:30 after the 17:00 bar,
        # and reserving a full hour behind it left an empty strip after every day.
        step = sess.diff().clip(upper=freq).fillna(freq)
        step = step.where(step > pd.Timedelta(0), freq)
        start = sess + step
        # An off-session bar that starts before prev+freq (e.g. a volume-less
        # 22:00 bar after the 21:30 session bar) must fall inside the break too.
        off = dates[~mask].sort_values()
        if not off.empty:
            pos = np.searchsorted(sess.values, off.values, side='right') - 1
            earliest = pd.Series(off.values, index=pos)
            earliest = earliest[earliest.index >= 0].groupby(level=0).min()
            start.loc[earliest.index] = np.minimum(start.loc[earliest.index].values,
                                                   earliest.values)
        has_gap = nxt.notna() & (nxt > start)
        for s, e in zip(start[has_gap], nxt[has_gap]):
            gaps.append((e - s, s, e))
        if len(gaps) > self.MAX_INTRADAY_BREAKS:
            gaps = sorted(gaps, key=lambda g: g[0], reverse=True)[:self.MAX_INTRADAY_BREAKS]
        breaks = [dict(bounds=[s.strftime(fmt), e.strftime(fmt)])
                  for _, s, e in sorted(gaps, key=lambda g: g[1])]
        # Off-session bars before the first / after the last session bar
        first, last = sess.iloc[0], sess.iloc[-1]
        if dates.min() < first:
            breaks.insert(0, dict(bounds=[dates.min().strftime(fmt), first.strftime(fmt)]))
        if dates.max() > last:
            breaks.append(dict(bounds=[start.iloc[-1].strftime(fmt),
                                       (dates.max() + freq).strftime(fmt)]))
        return breaks

    def get_clean_plot_data_and_breaks(self, df, h_start=8.0, h_end=16.5):
        """Filter df to the trading window [h_start, h_end] and compute matching Plotly rangebreaks.

        Strips pre-/post-market rows that would otherwise distort the x-axis, then
        adds night, weekend, and holiday breaks consistent with the filtered data.
        """
        df = df.copy()
        df['Date'] = pd.to_datetime(df['Date'])
        
        # Interval in hours
        dt_counts = df['Date'].diff().value_counts()
        freq = dt_counts.index[0] if not dt_counts.empty else pd.Timedelta(minutes=15)
        freq_h = freq.total_seconds() / 3600

        # FILTER: keep only data within h_start and h_end
        # This prevents pre/post-market data from 'folding' the axis
        df['decimal_time'] = df['Date'].dt.hour + df['Date'].dt.minute / 60

        # Allow data from h_start up to EXACTLY h_end
        df = df[(df['decimal_time'] >= h_start) & (df['decimal_time'] <= h_end)]

        # COMPUTE BREAKS
        breaks = []

        # Intraday gap: from (h_end + candle duration) to h_start
        # Modulo 24 prevents errors at midnight
        b_start = (h_end + freq_h) % 24
        b_end = h_start
        
        if freq < pd.Timedelta(days=1):
            breaks.append(dict(bounds=[b_start, b_end], pattern="hour"))

        # Weekends
        breaks.append(dict(bounds=["sat", "mon"]))

        # Holidays (Easter/Christmas)
        all_workdays = pd.date_range(start=df['Date'].min(), end=df['Date'].max(), freq='B')
        present_days = df['Date'].dt.normalize().unique()
        missing_days = all_workdays[~all_workdays.normalize().isin(present_days)]
        
        if not missing_days.empty:
            holiday_list = [d.strftime('%Y-%m-%d') for d in missing_days]
            breaks.append(dict(values=holiday_list))

        return breaks

    def get_range_breaks_1304261323(self, df, exchange=''):
        """Legacy rangebreak implementation (superseded by NewV3) — kept for reference."""
        df = df.copy()
        df['Date'] = pd.to_datetime(df['Date'])
        
        # 1. Determine interval
        dt_counts = df['Date'].diff().value_counts()
        freq = dt_counts.index[0] if not dt_counts.empty else pd.Timedelta(minutes=15)
        freq_in_hours = freq.total_seconds() / 3600

        # 2. Define trading hours (DECIMAL)
        h_start = 8.0
        h_end = 16.5

        # Exchange-specific logic (examples)
        if exchange in ['NYSE', 'NMS', 'NYQ']:
            h_start, h_end = 15.5, 22.0
        elif exchange == 'XETR':
            h_start, h_end = 9.0, 17.5

        # Calculate end: last candle + its duration
        h_break_start = h_end + freq_in_hours
        h_break_end = h_start

        breaks = []

        # 3. Rangebreaks (float-based)
        if freq < pd.Timedelta(days=1):
            # Plotly accepts floats in bounds (0 to 24)
            breaks.append(dict(
                bounds=[h_break_start, h_break_end],
                pattern="hour"
            ))

        # Weekends
        breaks.append(dict(bounds=["sat", "mon"]))

        # Dynamic holidays
        all_workdays = pd.date_range(start=df['Date'].min(), end=df['Date'].max(), freq='B')
        present_days = df['Date'].dt.normalize().unique()
        missing_days = all_workdays[~all_workdays.normalize().isin(present_days)]
        
        if not missing_days.empty:
            holiday_list = [d.strftime('%Y-%m-%d') for d in missing_days]
            breaks.append(dict(values=holiday_list))

        return breaks

    def get_range_breaks_1304261310(self, df, exchange=''):
        """Legacy rangebreak implementation with exchange-specific hour windows (superseded by NewV3)."""
        # 1. Data preparation
        df['Date'] = pd.to_datetime(df['Date'])

        # Mapping for exchanges
        YFINANCE_TO_MCAL = {
            'NMS': 'NASDAQ', 'NYQ': 'NYSE', 'NYS': 'NYSE', 'ASE': 'NYSE', 'PCX': 'NYSE', 
            'DJI': 'NYSE', 'FRA': 'XETR', 'GER': 'XETR', 'BER': 'XETR', 'DUS': 'XETR', 
            'FGI': 'XETR', 'AMS': 'XETR', 'WCB': 'IDX', 'NIM': 'IDX', 'CCY': 'CCX', 
            'CCC': 'CCX', 'CGI': 'CGI', 'CMX': 'CMX', 'JPX': 'TSE', 'EBS': 'EBS', 
            'HKG': 'HKG', 'OSA': 'OSA', 'MIL': 'MIL', 'PAR': 'PAR', 'HEL': 'HEL', 
            'ZRH': 'ZRH', 'LSE': 'LSE', 'ASX': 'ASX'
        }
        stock = YFINANCE_TO_MCAL.get(exchange)

        # 2. Define trading hours (start/end in decimal hours)
        # Default (e.g. NYSE)
        dec_start, dec_end = 14.5, 21.5

        if exchange == 'MCE':
            dec_start, dec_end = 8, 17.5
        elif stock == 'EBS':
            dec_start, dec_end = 11, 16.5
        elif stock in ['TSE', 'ASX']:
            dec_start, dec_end = 0.5, 7.5
        elif stock == 'LSE':
            dec_start, dec_end = 8, 17
        elif stock == 'HKG':
            dec_start, dec_end = 2, 9.5
        elif stock == 'OSA':
            dec_start, dec_end = 1, 7.5
        elif stock in ['ZRH', 'MIL', 'PAR', 'HEL']:
            dec_start, dec_end = 9, 16.5
        elif stock == 'XETR':
            dec_start, dec_end = 8.5, 17.5
        elif stock == 'IDX':
            dec_start, dec_end = 14.5, 22
        elif stock == 'CGI':
            dec_start, dec_end = 13, 19.5
        elif stock == 'NYSE':
            dec_start, dec_end = 14.5, 21.5
        elif stock in ['CCX', 'CMX']:
            dec_start, dec_end = 0, 23.9
        elif not stock:
            dec_start, dec_end = 8, 16.5
        else:
            dec_start, dec_end = 11.5, 20.5

        # 3. Dynamic holiday detection (Easter fix)
        # Look for weekdays (Mon-Fri) that are completely absent from the data
        all_workdays = pd.date_range(start=df['Date'].min(), end=df['Date'].max(), freq='B')
        # Normalize to date to ignore hours
        present_days = df['Date'].dt.normalize().unique()
        missing_days = all_workdays[~all_workdays.normalize().isin(present_days)]
        holiday_list = [d.strftime('%Y-%m-%d') for d in missing_days]

        # 4. Assemble breaks
        # Base: always remove weekends
        breaks = [dict(bounds=["sat", "mon"])]

        # Add holidays if any
        if holiday_list:
            breaks.append(dict(values=holiday_list))

        # 5. Interval-specific adjustment
        interval_str = str(self.interval).lower()

        # If NOT daily/weekly/monthly data (i.e. intraday/hourly):
        if not any(x in interval_str for x in ['d', 'wk', 'mo']):
            # Add the hourly break for the night
            breaks.append(dict(bounds=[dec_end, dec_start], pattern="hour"))

        return breaks

    def get_range_breaks_1304261222(self, df, exchange=''):
        """Legacy rangebreak implementation with hardcoded exchange hour maps (superseded by NewV3)."""
        # Ensure 'Date' is in datetime format
        df['Date'] = pd.to_datetime(df['Date'])

        # Mapping for trading hours (existing logic)
        YFINANCE_TO_MCAL = {
        'NMS': 'NASDAQ', 'NYQ': 'NYSE', 'NYS': 'NYSE', 'ASE': 'NYSE', 'PCX': 'NYSE', 'DJI': 'NYSE', 'FRA': 'XETR', 'GER': 'XETR', 'BER': 'XETR', 'DUS': 'XETR', 'FGI': 'XETR', 'AMS': 'XETR', 'WCB': 'IDX', 'NIM': 'IDX', 'CCY': 'CCX', 'CCC': 'CCX', 'CGI': 'CGI', 'CMX': 'CMX', 'JPX': 'TSE', 'EBS': 'EBS', 'HKG': 'HKG', 'OSA': 'OSA', 'MIL': 'MIL', 'PAR': 'PAR', 'HEL': 'HEL', 'ZRH': 'ZRH', 'LSE': 'LSE', 'ASX': 'ASX', # Omit unavailable or uncertain markets for now
        }
        stock = YFINANCE_TO_MCAL.get(exchange)

        # Define times (example values)
        dec_start, dec_end = 14.5, 21.5  # Default case NYSE
        if exchange == 'MCE':
            dec_start = 8
            dec_end = 17.5
        elif stock in ['EBS']:
            dec_start = 11
            dec_end = 16.5
        elif stock in ['TSE','ASX']:
            dec_start = 0.5
            dec_end = 7.5
        elif stock in ['LSE']:
            dec_start = 8
            dec_end = 17
        elif stock in ['HKG']:
            dec_start = 2
            dec_end = 9.5
        elif stock in ['OSA']:
            dec_start = 1
            dec_end = 7.5
        elif stock in ['ZRH','MIL','PAR','HEL']:
            dec_start = 9
            dec_end = 16.5
        elif stock in ['XETR']:
            dec_start = 8.5
            dec_end = 17.5
        elif stock in ['IDX']:
            dec_start = 14.5
            dec_end = 22     
        elif stock in ['CGI']:
            dec_start = 13
            dec_end = 19.5     
        elif stock in ['NYSE']:
            dec_start = 14.5
            dec_end = 21.5
        elif stock in ['CCX','CMX']:
            dec_start = 0
            dec_end = 23.9        
        elif stock in [''] or stock == None:
            dec_start = 8
            dec_end = 16.5        
        else:
            dec_start = 11.5
            dec_end = 20.5


        # 1. HOLIDAY DETECTION (dynamic from data)
        # Create a list of all weekdays in the data range
        all_weekdays = pd.date_range(start=df['Date'].min(), end=df['Date'].max(), freq='B')

        # Days missing from the data (these are holidays/trading halts)
        # Format strictly as 'YYYY-MM-DD'
        missing_days = all_weekdays[~all_weekdays.normalize().isin(df['Date'].dt.normalize())]
        holiday_list = [d.strftime('%Y-%m-%d') for d in missing_days]

        # 2. DEFINE BREAKS
        # Important: only add 'values' when the list is non-empty
        rb_daily = [
            dict(bounds=["sat", "mon"]),  # Weekends
            dict(bounds=[dec_end, dec_start], pattern="hour")  # Non-trading hours
        ]
        
        if holiday_list:
            rb_daily.append(dict(values=holiday_list))

        rb_weekly = [dict(bounds=["sat", "mon"])]
        if holiday_list:
            rb_weekly.append(dict(values=holiday_list))

        # 3. RETURN BASED ON INTERVAL
        try:
            # For daily or weekly values: exclude only weekends & holidays
            if any(x in str(self.interval) for x in ['d', 'wk', 'mo']):
                return rb_weekly
            # Otherwise (intraday): also include hourly breaks
            return rb_daily
        except Exception:
            return rb_daily


    def get_range_breaks_old(self, df, exchange=''):
        """Legacy rangebreak implementation using exchange-mapped mcal hours (superseded by NewV3)."""
        df = self.prepare_df(df)
        last_w_day = -1
        try:
            last_w_day = time.strptime(df['Date'].iloc[-1], tools.Tools().ftime_str).tm_wday
        except Exception:
            pass

        YFINANCE_TO_MCAL = {
        'NMS': 'NASDAQ', 'NYQ': 'NYSE', 'NYS': 'NYSE', 'ASE': 'NYSE', 'PCX': 'NYSE', 'DJI': 'NYSE', 'FRA': 'XETR', 'GER': 'XETR', 'BER': 'XETR', 'DUS': 'XETR', 'FGI': 'XETR', 'AMS': 'XETR', 'WCB': 'IDX', 'NIM': 'IDX', 'CCY': 'CCX', 'CCC': 'CCX', 'CGI': 'CGI', 'CMX': 'CMX', 'JPX': 'TSE', 'EBS': 'EBS', 'HKG': 'HKG', 'OSA': 'OSA', 'MIL': 'MIL', 'PAR': 'PAR', 'HEL': 'HEL', 'ZRH': 'ZRH', 'LSE': 'LSE', 'ASX': 'ASX', # Omit unavailable or uncertain markets for now
        }

        stock = YFINANCE_TO_MCAL.get(exchange)
        if exchange == 'MCE':
            dec_start = 8
            dec_end = 17.5
        elif stock in ['EBS']:
            dec_start = 11
            dec_end = 16.5
        elif stock in ['TSE','ASX']:
            dec_start = 0.5
            dec_end = 7.5
        elif stock in ['LSE']:
            dec_start = 8
            dec_end = 17
        elif stock in ['HKG']:
            dec_start = 2
            dec_end = 9.5
        elif stock in ['OSA']:
            dec_start = 1
            dec_end = 7.5
        elif stock in ['ZRH','MIL','PAR','HEL']:
            dec_start = 9
            dec_end = 16.5
        elif stock in ['XETR']:
            dec_start = 9
            dec_end = 18
        elif stock in ['IDX']:
            dec_start = 14.5
            dec_end = 22     
        elif stock in ['CGI']:
            dec_start = 13
            dec_end = 19.5     
        elif stock in ['NYSE']:
            dec_start = 14.5
            dec_end = 21.5
        elif stock in ['','CCX','CMX'] or stock == None:
            dec_start = 5
            dec_end = 21.1        
        else:
            dec_start = 11.5
            dec_end = 20.5

        rb_hourly = [
            dict(bounds=[dec_end, dec_start], pattern="hour")
        ]

        if last_w_day < 6:
            rb_hourly.append(dict(bounds=["sat", "mon"]))

        rb_daily = [
            dict(bounds=["sat", "mon"]),
            dict(bounds=[dec_end, dec_start], pattern="hour"),
        ]

        rb_weekly = [
            dict(bounds=["sat", "mon"]),            
        ]


        try:
            if self.interval[1:] in ['wk','mo','d']:
                return rb_weekly
            else:
                return rb_daily
    
        except Exception:
            if not (df['Date'].dt.dayofweek == 5).any():
                return rb_daily

    def get_range_breaks_newV1(self, df, exchange=''):
        """Legacy rangebreak implementation with pandas_market_calendars holiday lookup (superseded by NewV3)."""
        df = self.prepare_df(df)
        
        # ... Existing YFINANCE_TO_MCAL mapping ...
        YFINANCE_TO_MCAL = {
        'NMS': 'NASDAQ',
        'NYQ': 'NYSE',
        'NYS': 'NYSE',
        'ASE': 'NYSE',
        'PCX': 'NYSE',
        'DJI': 'NYSE',
        'FRA': 'XETR',
        'GER': 'XETR',
        'BER': 'XETR',
        'DUS': 'XETR',
        'FGI': 'XETR',
        'AMS': 'XETR',
        'WCB': 'IDX',
        'NIM': 'IDX',
        'CCY': 'CCX',
        'CCC': 'CCX',
        'CGI': 'CGI',
        'CMX': 'CMX',
        'JPX': 'TSE',
        'EBS': 'EBS',
        'HKG': 'HKG',
        'OSA': 'OSA',
        'MIL': 'MIL',
        'PAR': 'PAR',
        'HEL': 'HEL',
        'ZRH': 'ZRH',
        'LSE': 'LSE',
        'ASX': 'ASX',
        # Omit unavailable or uncertain markets for now
        }
        stock = YFINANCE_TO_MCAL.get(exchange)
        if exchange == 'MCE':
            dec_start = 8
            dec_end = 17.5
        elif stock in ['EBS']:
            dec_start = 11
            dec_end = 16.5
        elif stock in ['TSE','ASX']:
            dec_start = 0.5
            dec_end = 7.5
        elif stock in ['LSE']:
            dec_start = 8
            dec_end = 17
        elif stock in ['HKG']:
            dec_start = 2
            dec_end = 9.5
        elif stock in ['OSA']:
            dec_start = 1
            dec_end = 7.5
        elif stock in ['ZRH','MIL','PAR','HEL']:
            dec_start = 9
            dec_end = 16.5
        elif stock in ['XETR']:
            dec_start = 8
            dec_end = 17.5
        elif stock in ['IDX']:
            dec_start = 14.5
            dec_end = 22     
        elif stock in ['CGI']:
            dec_start = 13
            dec_end = 19.5     
        elif stock in ['NYSE']:
            dec_start = 14.5
            dec_end = 21.5
        elif stock in ['','CCX','CMX'] or stock == None:
            dec_start = 0
            dec_end = 22.1        
        else:
            dec_start = 11.5
            dec_end = 20.5

                
        # ... Existing time logic (dec_start, dec_end) ...
        # Assuming dec_start/end are defined as in the existing code

        # --- NEW: HOLIDAY LOGIC ---
        holiday_breaks = []
        if stock:
            try:
                # 1. Kalender laden
                try:
                    try_exchange = stock if stock != 'CCX' else 'CME_Equity'  # Fallback for crypto/special
                    calendar = mcal.get_calendar(try_exchange)
                except Exception:
                    calendar = mcal.get_calendar('NYSE')  # Global default fallback

                # 2. Determine the date range of the DataFrame
                start_d = df['Date'].min()
                end_d = df['Date'].max()

                # 3. Fetch all TRADING DAYS according to the calendar
                schedule = calendar.schedule(start_date=start_d, end_date=end_d)
                valid_days = pd.to_datetime(schedule.index).date

                # 4. Generate all CALENDAR DAYS (Mon-Fri) in the range
                all_business_days = pd.date_range(start=start_d, end=end_d, freq='B').date

                # 5. Compute difference = holidays on which no trading occurred
                holidays = [d.strftime('%Y-%m-%d') for d in all_business_days if d not in valid_days]

                if holidays:
                    holiday_breaks.append(dict(values=holidays))  # Add the specific days
            except Exception as e:
                logger.warning("Error during calendar lookup: %s", e)

        # --- ASSEMBLE RANGE BREAKS ---

        # Hourly breaks
        rb_hourly = [dict(bounds=[dec_end, dec_start], pattern="hour")]
        rb_hourly.extend(holiday_breaks)  # Add holidays

        # Weekend breaks (standard)
        weekend_break = dict(bounds=["sat", "mon"])

        rb_daily = [weekend_break] + holiday_breaks + [dict(bounds=[dec_end, dec_start], pattern="hour")]
        rb_weekly = [weekend_break] + holiday_breaks

        # Return logic (unchanged)
        try:
            interval_suffix = self.interval[-1] if hasattr(self, 'interval') else 'd'
            if interval_suffix in ['k', 'o', 'd']:  # wk, mo, d
                return rb_weekly
            else:
                return rb_daily
        except Exception:
            return rb_daily

