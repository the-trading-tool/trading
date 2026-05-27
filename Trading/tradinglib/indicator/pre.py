import sys
try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    print('No Import')

import plotly.graph_objects as go
from tradinglib.indicator import _indicator
from tradinglib.indicator import indicator
from tradinglib import predictlib as pl

class Pre(_indicator._Indicator):

    is_oszilator = False
    name = 'Predict target range'

    def __init__(self, df, symbol = ""):
        super().__init__(df=df, symbol=symbol)
        self.data()
#        self.add_fig()
                
    def data(self):
	
        self.pr_low = 0
        self.pr_high = 0
        try:
            p = pl.predict(precise = True)
            p.df = self.df.filter(['Open','Close','High','Low','Volume','ema9','ema21'])
            p.prepare_lag()
            p.load_model()
            nd = pl.np.sort(p.next_days)
            self.pr_low = int(nd[0]*100)/100
            self.pr_high = int(nd[-1]*100)/100
        
        except Exception:
            pass
        
    def add_fig(self):

        self.fig = go.Figure()
        try:
            self.df.reset_index(inplace=True)
        except Exception:
            pass
            
        self.fig.add_hline(y=self.pr_high, line_width=0.8, line_dash="dash", line_color="blue",
                      annotation_text=f'{self.pr_high}', 
                      annotation_position="top left",
                      label=dict(
                          font=dict(size=14, color="darkblue"),
                          ),
                      row=1, col=1
                      )    
        self.fig.add_hrect(y0=self.pr_low, y1=self.pr_high, line_width=0, fillcolor="blue", opacity=0.2)
        self.fig.add_hline(y=self.pr_low, line_width=0.8, line_dash="dash", line_color="darkblue",
                      annotation_text=f'{self.pr_low}', 
                      annotation_position="bottom left",
                      label=dict(
                          font=dict(size=14, color="darkblue"),
                          ),
                      row=1, col=1
                      )    
