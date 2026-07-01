import time, sys, numpy as np, pandas as pd
from tradinglib.tools import BuySellSignalGenerator, compute_signal_mask

def make(n_tickers, n_bars):
    rng = np.random.default_rng(0); rows=[]; base=pd.Timestamp('2025-01-01')
    for t in range(n_tickers):
        ewo=rng.normal(0,15,n_bars); close=100+np.cumsum(rng.normal(0,1,n_bars))
        for d in range(n_bars):
            rows.append({'ticker':f'T{t}','Date':base+pd.Timedelta(days=d),
                'Close':close[d],'close':close[d],'ewo_angle':ewo[d],
                'take_profit':close[d]*1.1,'stop_loss':close[d]*0.9})
    return pd.DataFrame(rows)

def timed(label, fn):
    t=time.perf_counter(); fn(); dt=(time.perf_counter()-t)*1000
    print(f"   {label:24s} {dt:9.1f} ms", flush=True); return dt

for nt, nb in [(50,250),(150,250)]:
    df=make(nt,nb); n=len(df)
    print(f"{nt} Ticker x {nb} Bars = {n} Zeilen", flush=True)
    a=timed("A stateless (Multi heute)", lambda: np.where(
        compute_signal_mask(df,"(ewo_angle<-20)").values,-1,
        np.where(compute_signal_mask(df,"(ewo_angle>20)").values,1,0)))
    b=timed("B stateful/fast (kein bp)", lambda:
        BuySellSignalGenerator(df.copy(),"(ewo_angle>20)","(ewo_angle<-20)").apply_signals())
    c=timed("C stateful + buy_price", lambda:
        BuySellSignalGenerator(df.copy(),"(ewo_angle>20)","(close < buy_price*0.95)").apply_signals())
    print(f"   -> C/A = x{c/a:.0f},  C/B = x{c/b:.0f}\n", flush=True)
