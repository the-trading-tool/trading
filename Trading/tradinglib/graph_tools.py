from turtle import st
import pandas as pd
import pandas_market_calendars as mcal
import time 
from tradinglib import tools

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
        fields = time_string.split(":")
        hours = fields[0] if len(fields) > 0 else 0.0
        minutes = fields[1] if len(fields) > 1 else 0.0
        seconds = fields[2] if len(fields) > 2 else 0.0
    
        return float(hours) + (float(minutes) / 60.0) + (float(seconds) / pow(60.0, 2))

    def prepare_df(self,df):

        df.reset_index(drop=True, inplace=True)
        df['dt_idx'] = df['Date']
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('dt_idx', inplace=True)

        # Sicherstellen, dass der Index ein DatetimeIndex ist
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # Schritt 2: Ermitteln der Handelszeiten und nicht gehandelten Tage
        df['date'] = df.index.date
        df['time'] = df.index.time

        return df

    def get_range_breaks(self, df, exchange=''):
        breaks = self.get_range_breaks_NewV3(df, exchange=exchange)
#        import streamlit as st
#        st.write(f"Determined range breaks: {breaks} exchange: {exchange}")
        return breaks
    

    def get_range_breaks_NewV3(self, df, exchange=''):
        df = df.copy()
        df['Date'] = pd.to_datetime(df['Date'])

        # 1. Intervall bestimmen
        dt_counts = df['Date'].diff().value_counts()
        if dt_counts.empty:
            return []
        freq = dt_counts.index[0]
        freq_h = freq.total_seconds() / 3600

        # 2. Krypto-Erkennung: nur echte 24/7-Assets haben Daten an BEIDEN
        #    Samstagen UND Sonntagen. Forex (CCY) und Futures öffnen Sonntag-Nacht,
        #    haben aber KEINEN Samstag → werden korrekt als nicht-24/7 erkannt.
        has_saturday = df['Date'].dt.dayofweek.eq(5).any()
        has_sunday   = df['Date'].dt.dayofweek.eq(6).any()
        is_24_7 = (exchange in ['CCX', 'CCC']) or (has_saturday and has_sunday)
        if is_24_7:
            return []

        breaks = []

        if freq < pd.Timedelta(days=1):
            # --- INTRADAY: Handelszeiten direkt aus den UTC-Zeitstempeln ableiten ---
            weekday_df = df[df['Date'].dt.dayofweek < 5].copy()
            if weekday_df.empty:
                return []

            weekday_df['dec_hour'] = (
                weekday_df['Date'].dt.hour + weekday_df['Date'].dt.minute / 60.0
            )

            # Pro Tag früheste und späteste Kerze — Median über alle Tage
            daily = weekday_df.groupby(weekday_df['Date'].dt.date)['dec_hour']
            h_start = daily.min().median()
            h_end   = daily.max().median()

            # Kein Nacht-Break für Near-24h-Assets (Forex, Futures ≥ 20h Handelszeit)
            trading_duration = h_end - h_start + freq_h
            if trading_duration < 20:
                b_start = (h_end + freq_h) % 24
                b_end   = h_start
                breaks.append(dict(bounds=[b_start, b_end], pattern="hour"))

            breaks.append(dict(bounds=["sat", "mon"]))

            # KEIN Feiertagsbreak für Intraday:
            # Plotly-Bug — pattern="hour" + date-range bounds in einer rangebreaks-Liste
            # erzeugt Zigzack-Artefakte auf der x-Achse. Kleinere Beulen an
            # Bankfeiertagen sind der Alternative (Zigzack durch ganzen Chart) vorzuziehen.

        else:
            # --- TÄGLICH / WÖCHENTLICH ---
            breaks.append(dict(bounds=["sat", "mon"]))

            # Fehlende Handelstage (Bankfeiertage) via values — für Tages-Daten
            # ohne pattern="hour"-Break ist die values-Methode Plotly-sicher.
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
        """
        1. Berechnet die Rangebreaks.
        2. Filtert den DataFrame, damit keine 'illegalen' Daten die Achse verbiegen.
        """
        df = df.copy()
        df['Date'] = pd.to_datetime(df['Date'])
        
        # Intervall in Stunden
        dt_counts = df['Date'].diff().value_counts()
        freq = dt_counts.index[0] if not dt_counts.empty else pd.Timedelta(minutes=15)
        freq_h = freq.total_seconds() / 3600

        # FILTER: Nur Daten behalten, die innerhalb h_start und h_end liegen
        # Das verhindert, dass Nachbörse/Vorbörse die Achse 'falten'
        df['decimal_time'] = df['Date'].dt.hour + df['Date'].dt.minute / 60
        
        # Wir erlauben Daten von h_start bis EXAKT h_end
        df = df[(df['decimal_time'] >= h_start) & (df['decimal_time'] <= h_end)]

        # BREAKS BERECHNEN
        breaks = []
        
        # Intraday-Lücke: Von (h_end + Kerzendauer) bis h_start
        # Modulo 24 verhindert Fehler bei Mitternacht
        b_start = (h_end + freq_h) % 24
        b_end = h_start
        
        if freq < pd.Timedelta(days=1):
            breaks.append(dict(bounds=[b_start, b_end], pattern="hour"))

        # Wochenenden
        breaks.append(dict(bounds=["sat", "mon"]))

        # Feiertage (Ostern/Weihnachten)
        all_workdays = pd.date_range(start=df['Date'].min(), end=df['Date'].max(), freq='B')
        present_days = df['Date'].dt.normalize().unique()
        missing_days = all_workdays[~all_workdays.normalize().isin(present_days)]
        
        if not missing_days.empty:
            holiday_list = [d.strftime('%Y-%m-%d') for d in missing_days]
            breaks.append(dict(values=holiday_list))

        return breaks

    def get_range_breaks_1304261323(self, df, exchange=''):
        df = df.copy()
        df['Date'] = pd.to_datetime(df['Date'])
        
        # 1. Intervall bestimmen
        dt_counts = df['Date'].diff().value_counts()
        freq = dt_counts.index[0] if not dt_counts.empty else pd.Timedelta(minutes=15)
        freq_in_hours = freq.total_seconds() / 3600
        
        # 2. Definition der Handelszeiten (DEZIMAL)
        h_start = 8.0
        h_end = 16.5 

        # Börsenspezifische Logik (Beispiele)
        if exchange in ['NYSE', 'NMS', 'NYQ']:
            h_start, h_end = 15.5, 22.0
        elif exchange == 'XETR':
            h_start, h_end = 9.0, 17.5

        # Ende berechnen: Letzte Kerze + ihre Dauer
        h_break_start = h_end + freq_in_hours
        h_break_end = h_start

        breaks = []

        # 3. Rangebreaks (Float-basiert)
        if freq < pd.Timedelta(days=1):
            # Plotly akzeptiert Floats in bounds (0 bis 24)
            breaks.append(dict(
                bounds=[h_break_start, h_break_end], 
                pattern="hour"
            ))

        # Wochenenden
        breaks.append(dict(bounds=["sat", "mon"]))

        # Dynamische Feiertage
        all_workdays = pd.date_range(start=df['Date'].min(), end=df['Date'].max(), freq='B')
        present_days = df['Date'].dt.normalize().unique()
        missing_days = all_workdays[~all_workdays.normalize().isin(present_days)]
        
        if not missing_days.empty:
            holiday_list = [d.strftime('%Y-%m-%d') for d in missing_days]
            breaks.append(dict(values=holiday_list))

        return breaks

    def get_range_breaks_1304261310(self, df, exchange=''):
        # 1. Daten-Vorbereitung
        df['Date'] = pd.to_datetime(df['Date'])
        
        # Mapping für Handelsplätze
        YFINANCE_TO_MCAL = {
            'NMS': 'NASDAQ', 'NYQ': 'NYSE', 'NYS': 'NYSE', 'ASE': 'NYSE', 'PCX': 'NYSE', 
            'DJI': 'NYSE', 'FRA': 'XETR', 'GER': 'XETR', 'BER': 'XETR', 'DUS': 'XETR', 
            'FGI': 'XETR', 'AMS': 'XETR', 'WCB': 'IDX', 'NIM': 'IDX', 'CCY': 'CCX', 
            'CCC': 'CCX', 'CGI': 'CGI', 'CMX': 'CMX', 'JPX': 'TSE', 'EBS': 'EBS', 
            'HKG': 'HKG', 'OSA': 'OSA', 'MIL': 'MIL', 'PAR': 'PAR', 'HEL': 'HEL', 
            'ZRH': 'ZRH', 'LSE': 'LSE', 'ASX': 'ASX'
        }
        stock = YFINANCE_TO_MCAL.get(exchange)

        # 2. Handelszeiten definieren (Start/Ende in Dezimalstunden)
        # Default (z.B. NYSE)
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

        # 3. Dynamische Feiertags-Ermittlung (Ostern-Fix)
        # Wir schauen nach Werktagen (Mo-Fr), die komplett in den Daten fehlen
        all_workdays = pd.date_range(start=df['Date'].min(), end=df['Date'].max(), freq='B')
        # Wir normalisieren auf das Datum, um Stunden zu ignorieren
        present_days = df['Date'].dt.normalize().unique()
        missing_days = all_workdays[~all_workdays.normalize().isin(present_days)]
        holiday_list = [d.strftime('%Y-%m-%d') for d in missing_days]

        # 4. Breaks zusammenstellen
        # Basis: Wochenenden immer entfernen
        breaks = [dict(bounds=["sat", "mon"])]

        # Feiertage hinzufügen, falls vorhanden
        if holiday_list:
            breaks.append(dict(values=holiday_list))

        # 5. Intervall-spezifische Anpassung
        interval_str = str(self.interval).lower()
        
        # Wenn NICHT Tages/Wochen/Monatsdaten (also Intraday/Stunden):
        if not any(x in interval_str for x in ['d', 'wk', 'mo']):
            # Füge den stündlichen Break für die Nacht hinzu
            breaks.append(dict(bounds=[dec_end, dec_start], pattern="hour"))

        return breaks

    def get_range_breaks_1304261222(self, df, exchange=''):
        # Sicherstellen, dass 'Date' im datetime-Format vorliegt
        df['Date'] = pd.to_datetime(df['Date'])
        
        # Mapping für Handelszeiten (deine bestehende Logik)
        YFINANCE_TO_MCAL = {
        'NMS': 'NASDAQ', 'NYQ': 'NYSE', 'NYS': 'NYSE', 'ASE': 'NYSE', 'PCX': 'NYSE', 'DJI': 'NYSE', 'FRA': 'XETR', 'GER': 'XETR', 'BER': 'XETR', 'DUS': 'XETR', 'FGI': 'XETR', 'AMS': 'XETR', 'WCB': 'IDX', 'NIM': 'IDX', 'CCY': 'CCX', 'CCC': 'CCX', 'CGI': 'CGI', 'CMX': 'CMX', 'JPX': 'TSE', 'EBS': 'EBS', 'HKG': 'HKG', 'OSA': 'OSA', 'MIL': 'MIL', 'PAR': 'PAR', 'HEL': 'HEL', 'ZRH': 'ZRH', 'LSE': 'LSE', 'ASX': 'ASX', # Unverfügbare oder unsichere Märkte hier erstmal auslassen
        }
        stock = YFINANCE_TO_MCAL.get(exchange)

        # Zeiten definieren (Beispielwerte aus deinem Code)
        dec_start, dec_end = 14.5, 21.5 # Standardfall NYSE
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


        # 1. FEIERTAGS-ERMITTLUNG (Dynamisch aus Daten)
        # Wir erstellen eine Liste aller Wochentage im Bereich deiner Daten
        all_weekdays = pd.date_range(start=df['Date'].min(), end=df['Date'].max(), freq='B')
        
        # Tage, die in den Daten fehlen (das sind die Feiertage/Handelspausen)
        # Wir formatieren sie strikt als 'YYYY-MM-DD'
        missing_days = all_weekdays[~all_weekdays.normalize().isin(df['Date'].dt.normalize())]
        holiday_list = [d.strftime('%Y-%m-%d') for d in missing_days]

        # 2. DEFINITION DER BREAKS
        # Wichtig: 'values' nur hinzufügen, wenn die Liste nicht leer ist
        rb_daily = [
            dict(bounds=["sat", "mon"]), # Wochenenden
            dict(bounds=[dec_end, dec_start], pattern="hour") # Handelsfreie Zeit
        ]
        
        if holiday_list:
            rb_daily.append(dict(values=holiday_list))

        rb_weekly = [dict(bounds=["sat", "mon"])]
        if holiday_list:
            rb_weekly.append(dict(values=holiday_list))

        # 3. RÜCKGABE BASIEREND AUF INTERVALL
        try:
            # Falls Tages- oder Wochenwerte: Nur Wochenenden & Feiertage ausschließen
            if any(x in str(self.interval) for x in ['d', 'wk', 'mo']):
                return rb_weekly
            # Sonst (Intraday): Auch Stunden-Breaks einbeziehen
            return rb_daily
        except Exception:
            return rb_daily


    def get_range_breaks_old(self, df, exchange = ''):
        
        df = self.prepare_df(df)
        last_w_day = -1
        try:
            last_w_day = time.strptime(df['Date'].iloc[-1], tools.Tools().ftime_str).tm_wday
        except Exception:
            pass

        YFINANCE_TO_MCAL = {
        'NMS': 'NASDAQ', 'NYQ': 'NYSE', 'NYS': 'NYSE', 'ASE': 'NYSE', 'PCX': 'NYSE', 'DJI': 'NYSE', 'FRA': 'XETR', 'GER': 'XETR', 'BER': 'XETR', 'DUS': 'XETR', 'FGI': 'XETR', 'AMS': 'XETR', 'WCB': 'IDX', 'NIM': 'IDX', 'CCY': 'CCX', 'CCC': 'CCX', 'CGI': 'CGI', 'CMX': 'CMX', 'JPX': 'TSE', 'EBS': 'EBS', 'HKG': 'HKG', 'OSA': 'OSA', 'MIL': 'MIL', 'PAR': 'PAR', 'HEL': 'HEL', 'ZRH': 'ZRH', 'LSE': 'LSE', 'ASX': 'ASX', # Unverfügbare oder unsichere Märkte hier erstmal auslassen
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

#        dec_start = 0
#        dec_end = 0
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
        df = self.prepare_df(df)
        
        # ... Dein bestehendes YFINANCE_TO_MCAL Mapping ...
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
        # Unverfügbare oder unsichere Märkte hier erstmal auslassen
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

#        dec_start = 0
#        dec_end = 0
                
        # ... Deine bestehende Zeitlogik (dec_start, dec_end) ...
        # Angenommen dec_start/end sind definiert wie in deinem Code

        # --- NEU: FEIERTAGS-LOGIK ---
        holiday_breaks = []
        if stock:
            try:
                # 1. Kalender laden
                try:
                    try_exchange = stock if stock != 'CCX' else 'CME_Equity' # Fallback für Krypto/Spezial
                    calendar = mcal.get_calendar(try_exchange)
                except Exception:
                    calendar = mcal.get_calendar('NYSE') # Globaler Standard-Fallback

                # 2. Datumsbereich des Dataframes bestimmen
                start_d = df['Date'].min()
                end_d = df['Date'].max()

                # 3. Alle HANDELSTAGE laut Kalender holen
                schedule = calendar.schedule(start_date=start_d, end_date=end_d)
                valid_days = pd.to_datetime(schedule.index).date

                # 4. Alle KALENDERTAGE (Mo-Fr) im Zeitraum generieren
                all_business_days = pd.date_range(start=start_d, end=end_d, freq='B').date

                # 5. Differenz ermitteln = Feiertage, an denen nicht gehandelt wurde
                holidays = [d.strftime('%Y-%m-%d') for d in all_business_days if d not in valid_days]
                
                if holidays:
                    holiday_breaks.append(dict(values=holidays)) # Fügt die spezifischen Tage hinzu
            except Exception as e:
                print(f"Fehler beim Kalender-Lookup: {e}")

        # --- RANGE BREAKS ZUSAMMENSTELLEN ---
        
        # Deine stündlichen Breaks
        rb_hourly = [dict(bounds=[dec_end, dec_start], pattern="hour")]
        rb_hourly.extend(holiday_breaks) # Feiertage hinzufügen

        # Wochenend-Breaks (Standard)
        weekend_break = dict(bounds=["sat", "mon"])

        rb_daily = [weekend_break] + holiday_breaks + [dict(bounds=[dec_end, dec_start], pattern="hour")]
        rb_weekly = [weekend_break] + holiday_breaks

        # Rückgabe-Logik (unverändert)
        try:
            interval_suffix = self.interval[-1] if hasattr(self, 'interval') else 'd'
            if interval_suffix in ['k', 'o', 'd']: # wk, mo, d
                return rb_weekly
            else:
                return rb_daily
        except Exception:
            return rb_daily
