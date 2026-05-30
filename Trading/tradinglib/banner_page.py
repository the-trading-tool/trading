from tradinglib import tools
import datetime as dt
import pandas as pd
import streamlit as st
from tradinglib import system_config as sysconf
from tradinglib.i18n import t
import plotly.express as px
import math

class BannerPage():

    @property
    def ttl(self):
        return t('banner.title')

    def __init__(self, username='admin', region = st):
        self.db_path = 'database'
        self.username = username
        self.region = region
        self.sys_conf = sysconf.SystemConfig(username=self.username)
        self.system_currency = self.sys_conf.get_value('system_currency','EUR')
        self.render()    
    
    
    def render(self):
        
        if 1: 
            st.title(self.ttl)
#            asset_date = st.text_input('Filter date from: ',dt.datetime.now().strftime("%Y-%m-%d"))
#            st.html('<h3>Buy</h3>')
#            buy_df = buy_df.loc[buy_df['buyDate'] >= asset_date]
#            buy_df.sort_values(['buyDate'], ascending=[False], inplace=True)
#            st.write(buy_df)

            year = dt.datetime.now().year
            # Save the data to database

            db = tools.Db_tools(db_path=self.db_path, database_name=f'trades{year}.db')
            db.conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT,
                    buyDate TEXT,
                    sellDate TEXT,
                    num_assets REAL,
                    invest REAL,
                    profit REAL
                )
            """)
            db.conn.commit()
            df = pd.read_sql('select * from trades', db.conn)
            db.conn.close()


            self.assets = ""
            self.total_invest = 0
            self.num_assets = 0
            _raw_transactions = self.sys_conf.get_value('multi_transactions', self.sys_conf.transactions)
            transactions = eval(_raw_transactions) if isinstance(_raw_transactions, str) else _raw_transactions
            for item in transactions:
                inner = transactions[item]
                # Unterstuetze beide Formate:
                # 2-stufig: {gruppe: {num_assets: x, invest: y, ...}}  (Default aus system_config)
                # 3-stufig: {gruppe: {id: {num_assets: x, invest: y, ...}}}  (gespeichertes Format)
                if isinstance(inner, dict) and 'num_assets' in inner:
                    positions = {item: inner}
                else:
                    positions = inner
                for id in positions:
                    pos = positions[id]
                    if not isinstance(pos, dict):
                        continue
                    num_assets = pos.get('num_assets', 0)
                    invest = pos.get('invest', 0)
                    self.total_invest += invest
                    self.num_assets += num_assets
                    self.assets += id+", "
            gain = 0
            for i, row in df.iterrows():
                if not math.isnan(row['cumulative_gain']):
                    gain = row['cumulative_gain']

            pct_gain = round((gain/self.total_invest)*100,1)
            self.region.write(f"""
                <h5>
                {t('banner.intro', year=year, invest=self.total_invest, currency=self.system_currency)}</br>
                {t('banner.portfolio_max', assets=self.assets, num=self.num_assets)}</br>
                </br>
                {t('banner.total_value', value=round(gain+self.total_invest,2), currency=self.system_currency)}</br>
                {t('banner.performance', pct=pct_gain)}</h5>.</br>
                 """, unsafe_allow_html=True)

            db_table = 'banner_notes'
            db = tools.Db_tools(db_path=self.db_path, database_name=f"{db_table}.db")
            db.conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {db_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT,
                    text TEXT,
                    buyDate TEXT
                )
            """)
            db.conn.commit()
            b_df = pd.read_sql(f'select * from {db_table}', db.conn)
            db.conn.close()
            try:
                ticker = b_df.iloc[-1]['ticker']
                longname = ""
                date = ''
                text = b_df[b_df['ticker']==ticker]['text'].iloc[0]
                try:
                    longname = df[df['ticker']==ticker]['longName'].iloc[0]
                    date = df[df['ticker']==ticker]['buyDate'].iloc[-1]
                except Exception:
                    pass
                if not text == '' and not longname =='':
                    self.region.write(f"<h2>{t('banner.trading_tip', name=longname)}</h2> (Date: {date})", unsafe_allow_html=True)
                    self.region.write(f"""<font size="+4">{text}</font>""", unsafe_allow_html=True)
            except Exception:
                pass
            self.region.html(f"<h3>{t('banner.trades_since', year=year)}</h3>")
            df.sort_values(['sellDate','ticker'], ascending=[False,False],inplace=True)
            self.region.dataframe(df)
            
#            df.sort_values(['sellDate','ticker'], ascending=[False,True],inplace=True)
            fig1 = px.line(
                        df,
                        x='sellDate',
                        y='cumulative_gain',
                       title=t('banner.total_gain'),
                        labels={'buyDate': 'Date', 'cumulative_gain': 'Gain'}
                    )
            st.plotly_chart(
                                fig1,
                                use_container_width = True,
                                #sharing="streamlit",
                    )
    
        
