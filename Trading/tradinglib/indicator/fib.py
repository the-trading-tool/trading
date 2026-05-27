import ta
import sys
import plotly.graph_objects as go

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    print('No Import')

from tradinglib.indicator import _indicator

class Fib(_indicator._Indicator):

    is_oszilator = False
    name = 'Fibonacci Levels'

    params = {
        'period': {'type': 'int', 'default': 100, 'min': 10, 'max': 1000, 'label': 'Lookback period (bars)'},
    }

    def __init__(self, df, symbol = "", period = 100):
        super().__init__(df=df, symbol=symbol)
        self.period = period
#        self.data()
#        self.add_fig()
                
    def data(self): # Fibonacci
        
        pass
        
    def add_fig(self):

        self.fig = go.Figure()
        try:
            self.df.reset_index(inplace=True)
        except Exception:
            pass
			
        max_value = self.df['Close'][-self.period:].max()
        min_value = self.df['Close'][-self.period:].min()

        # Fibonacci constants
        difference = max_value - min_value

        # Set Fibonacci levels
#        up_third = max_value + difference * 0.5
#        up_second = max_value + difference * 0.382
        up_first = max_value + difference * 0.236
        first_level = max_value - difference * 0.236
        second_level = max_value - difference * 0.382
        third_level = max_value - difference * 0.5
        fourth_level = max_value - difference * 0.618
        low_first = min_value - difference * 0.236
#        low_second = min_value - difference * 0.382
#        low_third =  min_value - difference * 0.5
        
    # Plot Fibonacci graph
        self.fig.add_hline(y=up_first,
                      line_width=0.8,
                      line_dash="dash", line_color="lightgreen",
                      annotation_text=round(up_first,1), 
                      annotation_position="bottom left",
                      )    
 #       self.fig.add_hline(y=up_second,
 #                     line_width=0.8,
 #                     line_dash="dash", line_color="green",
 #                     annotation_text=round(up_second,1), 
 #                     annotation_position="bottom left",
 #                     )    
 #       self.fig.add_hline(y=up_third,
 #                     line_width=0.8,
 #                     line_dash="dash", line_color="darkgreen",
 #                     annotation_text=round(up_third,1), 
 #                     annotation_position="bottom left",
 #                     )    
        self.fig.add_hline(y=low_first,
                      line_width=0.8,
                      line_dash="dash", line_color="brown",
                      annotation_text=round(low_first,1), 
                      annotation_position="bottom left",
                      )    
 #       self.fig.add_hline(y=low_second,
 #                     line_width=0.8,
 #                     line_dash="dash", line_color="darkred",
 #                     annotation_text=round(low_second,1), 
 #                     annotation_position="bottom left",
 #                     )    
 #       self.fig.add_hline(y=low_third,
 #                     line_width=0.8,
 #                     line_dash="dash", line_color="darkred",
 #                     annotation_text=round(low_third,1), 
 #                     annotation_position="bottom left",
 #                     )    
        self.fig.add_hline(y=up_first,
                      line_width=0.8,
                      line_dash="dash", line_color="royalblue",
                      annotation_text=round(up_first,1), 
                       annotation_position="bottom left",
                     )    
        self.fig.add_hline(y=max_value,
                      line_width=0.8,
                      line_dash="dash", line_color="purple",
                      annotation_text=round(max_value,1), 
                      annotation_position="bottom left",
                      )    
        self.fig.add_hrect(y0=first_level, y1=max_value, line_width=0, fillcolor="blue", opacity=0.2)
        self.fig.add_hline(y=first_level,
                      line_width=0.8,
                      line_dash="dash", line_color="blue",
                      annotation_text=round(first_level,1), 
                      annotation_position="bottom left",
                      )    
        self.fig.add_hrect(y0=second_level, y1=first_level, line_width=0, fillcolor="green", opacity=0.2)
        self.fig.add_hline(y=second_level, line_width=0.8, line_dash="dash", line_color="green",
                      annotation_text=round(second_level,1), 
                      annotation_position="bottom left",
                      )    
        self.fig.add_hrect(y0=third_level, y1=second_level, line_width=0, fillcolor="red", opacity=0.2)
        self.fig.add_hline(y=third_level, line_width=0.8, line_dash="dash", line_color="red",
                      annotation_text=round(third_level,1), 
                      annotation_position="bottom left",
                      )    
        self.fig.add_hrect(y0=fourth_level, y1=third_level, line_width=0, fillcolor="orange", opacity=0.2)

        self.fig.add_hrect(y0=fourth_level, y1=second_level, line_width=0, fillcolor="orange", opacity=0.2)
        self.fig.add_hline(y=fourth_level, line_width=0.8, line_dash="dash", line_color="orange",
                      annotation_text=round(fourth_level,1), 
                      annotation_position="bottom left",
                      )    
        self.fig.add_hrect(y0=min_value, y1=fourth_level, line_width=0, fillcolor="yellow", opacity=0.2)
        self.fig.add_hline(y=min_value, line_width=0.8, line_dash="dash", line_color="black",
                      annotation_text=round(min_value,1), 
                      annotation_position="bottom left",
                      )    
