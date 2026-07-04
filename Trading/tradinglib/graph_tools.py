import logging
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

    def get_range_breaks(self, df, exchange=''):
        """Return Plotly rangebreaks for the given DataFrame and exchange code (delegates to NewV3)."""
        breaks = self.get_range_breaks_NewV3(df, exchange=exchange)
        return breaks
    

    def get_range_breaks_NewV3(self, df, exchange=''):
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
            # --- INTRADAY: night break = largest circular gap in the hour histogram.
            # Robust against sessions that cross midnight (US assets traded in
            # Berlin time incl. pre/post-market 10:00 AM -> 02:00 AM next day):
            # a simple day min/max would see 0..23 h and find no break.
            weekday_df = df[dow < 5].copy()
            if weekday_df.empty:
                return []

            dec_hour = (
                weekday_df['Date'].dt.hour + weekday_df['Date'].dt.minute / 60.0
            ).round(2)

            # Only evaluate regularly populated hour slots — individual outliers
            # (e.g. the currently open, partial candle) must not split the gap.
            counts = dec_hour.value_counts()
            n_days = weekday_df['Date'].dt.date.nunique()
            slots = sorted(counts[counts >= max(2, 0.05 * n_days)].index)

            gaps = []
            for i, h in enumerate(slots):
                nxt = slots[(i + 1) % len(slots)]
                raw = (nxt - h) % 24
                width = raw - freq_h  # Slots can be narrower than freq_h (DST mixed-window)
                if width > 0:
                    gaps.append((width, (h + freq_h) % 24, nxt))
            if gaps:
                width, b_start, b_end = max(gaps)
                # Only genuine night gaps — near-24h assets (Forex) have none
                if width >= max(2 * freq_h, 1.0):
                    breaks.append(dict(bounds=[float(b_start), float(b_end)], pattern="hour"))

            breaks.append(dict(bounds=["sat", "mon"]))

            # NO holiday break for intraday:
            # Plotly bug — pattern="hour" + date-range bounds in the same rangebreaks list
            # produces zigzag artefacts on the x-axis. Minor bumps on bank holidays
            # are preferable to the alternative (zigzag across the entire chart).

        else:
            # --- DAILY / WEEKLY ---
            breaks.append(dict(bounds=["sat", "mon"]))

            # Missing trading days (bank holidays) via values — for daily data
            # without a pattern="hour" break the values method is Plotly-safe.
            all_workdays = pd.date_range(
                start=df['Date'].min(), end=df['Date'].max(), freq='B'
            )
            present_days = df['Date'].dt.normalize().unique()
            missing_days = all_workdays[~all_workdays.normalize().isin(present_days)]

            if not missing_days.empty:
                holiday_list = [d.strftime('%Y-%m-%d') for d in missing_days]
                breaks.append(dict(values=holiday_list))

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

