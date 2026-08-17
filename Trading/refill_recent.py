"""Die von der Reparatur verworfenen juengsten Tage wieder auffuellen.

Die Reparatur verwirft die letzten Tage bewusst (dort liegen beide Zeitsorten
vermischt). Sich darauf zu verlassen, dass die laufenden Jobs das nachholen,
geht nicht auf: die schauen nur zwei Tage zurueck, verworfen wurden aber drei.
Also gezielt nachladen -- Yahoo liefert 1m sieben und 60m sechzig Tage.

Reihenfolge wie bei der Reparatur: Indizes zuerst, ETPs zuletzt.
"""
import sys, os, glob, logging
sys.path.insert(0, '.')
logging.disable(logging.CRITICAL)
from concurrent.futures import ThreadPoolExecutor
from tradinglib import ticker_tools as tt
from tradinglib.tools import Tools
from tradinglib import asset_status as ast
import repair_intraday_tz as R

d = os.path.dirname(Tools().get_path(path='database', file_name='asset_info.db'))
inactive = ast.inactive_tickers()
prio = R._priority_map()
tks = [os.path.basename(f)[3:-3] for f in glob.glob(os.path.join(d, 'yf_*.db'))]
tks = [t for t in tks if t not in inactive]
tks.sort(key=lambda t: (prio.get(t, 1), t))
print(f'{len(tks)} Ticker, Reihenfolge Indizes -> Rest -> ETP')

def one(tk):
    s = tt.StockDataSaver(tk)
    try:
        s.save_all_intervals(intervals=['60m', '1m'], periods=['30d', '7d'],
                             force_remote=True)
        return True
    except Exception:
        return False
    finally:
        try: s.close_connection()
        except Exception: pass

done = ok = 0
with ThreadPoolExecutor(max_workers=3) as ex:
    for r in ex.map(one, tks):
        done += 1; ok += bool(r)
        if done % 500 == 0:
            print(f'   {done}/{len(tks)} ({ok} ok)', flush=True)
print(f'fertig: {ok} von {len(tks)}')
