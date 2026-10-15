"""/all /group:ETP,ETF must be the ETP run (asset_simulation_etp.db).

The scheduler job ASSET_PERFORMANCE_ETP used "/group:ETP,ETF"; only exactly
['ETP'] counted, so the job silently simulated every non-ETP ticker into
_all.db again and the ETP simulation stopped updating (2026-10-15).
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asset_perf2


def test_etp_und_etf_gelten_als_etp_lauf():
    for group in (['ETP'], ['ETF'], ['ETP', 'ETF'], ['ETF', 'ETP']):
        assert asset_perf2.is_etp_run(group), group
    for group in ([], None, ['CRYPTO'], ['ETP', 'CRYPTO'], ['^GDAXI']):
        assert not asset_perf2.is_etp_run(group), group


def test_abfrage_liefert_die_mitglieder_beider_gruppen(tmp_path):
    con = sqlite3.connect(tmp_path / 't.db')
    con.executescript("""
        CREATE TABLE stocks (id INTEGER, Ticker TEXT);
        CREATE TABLE indices (id INTEGER, name TEXT);
        CREATE TABLE stock_indices (stock_id INTEGER, index_id INTEGER);
        INSERT INTO stocks VALUES (1,'SXR1.DE'),(2,'ISPA.DE'),(3,'SAP.DE');
        INSERT INTO indices VALUES (1,'ETP'),(2,'ETF'),(3,'^GDAXI');
        INSERT INTO stock_indices VALUES (1,1),(2,1),(2,2),(3,3);
    """)
    rows = {r[0] for r in con.execute(asset_perf2.etp_ticker_query(['ETP', 'ETF']))}
    assert rows == {'SXR1.DE', 'ISPA.DE'}
    assert {r[0] for r in con.execute(asset_perf2.etp_ticker_query(['ETF']))} == {'ISPA.DE'}
