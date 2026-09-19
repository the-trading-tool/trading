"""Gehaltene Titel laufen im Tageslauf mit, auch ohne Indexbindung.

AAD.DE wurde am 2026-08-09 von ^SDAXI geloest, lag aber weiter im Depot. Der
Tageslauf (nur Index-Mitglieder) rechnete es ab da nicht mehr, und die
Kennzahlen-Seite zeigte einen Stop-Loss aus einem Schlusskurs vom August.
"""
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tradinglib import summary_sources as ss


@pytest.fixture()
def dbdir(tmp_path, monkeypatch):
    monkeypatch.setenv('TradingDB', str(tmp_path))
    return tmp_path


def _exec(path, *stmts):
    with sqlite3.connect(path) as conn:
        for s in stmts:
            conn.execute(s)


def test_eigene_paper_und_multi_positionen(dbdir):
    _exec(dbdir / 'trades.db',
          "CREATE TABLE trades (ticker TEXT, action TEXT, shares REAL)",
          "INSERT INTO trades VALUES ('AAD.DE','buy',50), ('SAP.DE','buy',10), ('SAP.DE','sell',10)")
    _exec(dbdir / 'trading.db',
          "CREATE TABLE broker_orders (mode TEXT, ticker TEXT, broker_symbol TEXT, action TEXT, qty REAL, status TEXT)",
          "INSERT INTO broker_orders VALUES ('paper','MU','MU','buy',1,'filled')")
    _exec(dbdir / f'trades{datetime.now().year}.db',
          "CREATE TABLE trades (ticker TEXT, sellVolume REAL)",
          "INSERT INTO trades VALUES ('SI=F', NULL), ('ALV.DE', 5)")
    assert ss.held_tickers() == ['AAD.DE', 'MU', 'SI=F']


def test_fehlende_datenbanken_liefern_leere_liste(dbdir):
    assert ss.held_tickers() == []


def test_tageslaeufe_nehmen_gehaltene_titel_auf():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fname in ('asset_perf2.py', 'get_asset_data.py'):
        src = open(os.path.join(root, fname), encoding='utf-8').read()
        assert 'held_tickers' in src, fname
