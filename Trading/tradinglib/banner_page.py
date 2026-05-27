from tradinglib import tools
import datetime as dt
import pandas as pd
import streamlit as st
from tradinglib import system_config as sysconf
import plotly.express as px
import math

class BannerPage():

    ttl = 'The Trading Tools'

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
            df = pd.read_sql('select * from trades', db.conn)
            db.conn.close()


            self.assets = ""
            self.total_invest = 0
            self.num_assets = 0
            transactions = eval(self.sys_conf.get_value('multi_transactions',self.sys_conf.transactions))
            for item in transactions:
                for id in transactions[item]:
#                    total_transactions += 1                    
                    num_assets = transactions[item][id]['num_assets']
                    invest = transactions[item][id]['invest']
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
                We started {year} with an investment of {self.total_invest} {self.system_currency}.</br>
                Invested into {self.assets} at a maximum portfolio asset amount of {self.num_assets}.</br>
                </br>
                Today total portfoilio value is: {round(gain+self.total_invest,2)} {self.system_currency},</br>
                at a portfolio performance of: {pct_gain}%</h5>.</br>
                 """, unsafe_allow_html=True)

            db_table = 'banner_notes'
            db = tools.Db_tools(db_path=self.db_path, database_name=f"{db_table}.db")
            b_df = pd.read_sql(f'select * from {db_table}', db.conn)
#            b_df.reset_index(inplace=True)
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
                    self.region.write(f"<h2>Latest trading tip goes for - {longname} -</h2> (Date: {date})", unsafe_allow_html=True)
                    self.region.write(f"""<font size="+4">{text}</font>""", unsafe_allow_html=True)
            except Exception:
                pass
            self.region.html(f"<h3>Executed trades since beginning of {year}: </h3>")
            df.sort_values(['sellDate','ticker'], ascending=[False,False],inplace=True)
            self.region.dataframe(df)
            
#            df.sort_values(['sellDate','ticker'], ascending=[False,True],inplace=True)
            fig1 = px.line(
                        df,
                        x='sellDate',
                        y='cumulative_gain',
                       title='Total gain',
                        labels={'buyDate': 'Date', 'cumulative_gain': 'Gain'}
                    )
            st.plotly_chart(
                                fig1,
                                use_container_width = True,
                                #sharing="streamlit",
                    )
    
        
