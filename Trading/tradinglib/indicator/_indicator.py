import numpy as np
import ta
import talib
import pandas as pd
import sys

try:
    sys.path.insert(0, "../../tradinglib/indicator")
except ImportError:
    print('No Import')

class _Indicator():

    fig = None
    df = None
    is_oszilator = False
    name = '-'
    params = {}  # override in subclass to declare configurable parameters

    # Style params present on every indicator (merged into params dialog + Pine export)
    style_params: dict = {
        'line_color': {
            'type':    'color',
            'default': '',
            'label':   'Line color (empty = indicator default)',
        },
        'line_width': {
            'type':    'int',
            'default': 2,
            'min':     1,
            'max':     5,
            'label':   'Line width',
        },
        'line_style': {
            'type':    'select',
            'default': 'solid',
            'options': ['solid', 'dashed', 'dotted'],
            'label':   'Line style',
        },
    }
    
    def __init__(self, symbol=None, df=pd.DataFrame()):
                self.symbol = symbol
                df = df.copy()
                self.df = df                

    def has_index(self, df):
    
        return True if df.index.names[0] else False
    
    def data(self): 
        """ 
        This class should master the data we pass it to the __init__ procedure
        as a df and allow to reuse it to draw a figure with plotly
        Mind to import pandas and plotly into your own class
        """
        raise NotImplementedError("You need to implement this class before using it.")
        pass

    def add_fig(self):
        """
        This class should contain figures with all trace specific information to allow to combine it later
        into a new fig object by using the pre-calculated df
        """

        raise NotImplementedError("You need to implement this class before using it.")
        pass


