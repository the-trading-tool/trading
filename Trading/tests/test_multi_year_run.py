"""Mehrjahres-Lauf und Index-Auswahl in Multi Strategies.

Mehrere gewaehlte Jahre ergeben EINEN durchgehenden Lauf: die Jahresscheiben
werden je Index aneinandergehaengt, das Kapital laeuft durch, Positionen duerfen
ueber den Jahreswechsel gehalten werden. Vorher endete jede offene Position
zwangsweise am 31.12. — in einer Signalstudie ueber 2020-2026 betraf das 62,5 %
aller Positionen.

Die Tests decken die Punkte ab, an denen ein Fehler still bliebe:
  * performance_db muss nach dem Lauf wieder auf dem Ausgangswert stehen, sonst
    liest der naechste Index aus der zuletzt eingehaengten Jahres-DB
  * doppelte (Date, ticker) an den Scheibenraendern muessen weg
  * ein Mehrjahres-Lauf darf die Produktionsdatei trades{Jahr}.db nicht anfassen
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from tradinglib.premium.multi_transaction import MultiTransactionProcessor as MTP


class _Sim:
    """Simulator-Attrappe: liefert je eingehaengter DB eine eigene Jahresreihe."""

    def __init__(self, jahre=(2021, 2022), fehler_bei=None):
        self.performance_db = 'asset_simulation_.db'
        self.index_column = '^X'
        self.attach_calls = []
        self._jahre = jahre
        self._fehler_bei = fehler_bei

    def attach_dbs(self):
        self.attach_calls.append(self.performance_db)

    def fetch_combined_data_with_attach(self, **kw):
        db = self.performance_db
        if self._fehler_bei and self._fehler_bei in db:
            raise RuntimeError('DB kaputt')
        jahr = ''.join(c for c in db if c.isdigit()) or '2026'
        if int(jahr) not in self._jahre:
            return pd.DataFrame()
        return pd.DataFrame({
            'Date': [f'{jahr}-01-04 00:00:00', f'{jahr}-01-05 00:00:00'],
            'ticker': ['AAA', 'AAA'], 'close': [1.0, 2.0]})


def _proc(sim, use_years):
    p = object.__new__(MTP)
    p.db_name = 'asset_simulation'
    p.use_year = max(use_years)
    p.use_years = list(use_years)
    p.simulator = sim
    return p


# ------------------------------------------------------------- Verkettung

def test_jahre_werden_aneinandergehaengt():
    sim = _Sim(jahre=(2021, 2022))
    df = _proc(sim, [2021, 2022])._fetch_years([2021, 2022], 'sortino', 'ANY', False)
    assert sorted(df['Date'].str[:4].unique()) == ['2021', '2022']
    assert len(df) == 4


def test_performance_db_wird_zurueckgeschaltet():
    """Sonst laeuft der naechste Index auf der zuletzt eingehaengten Jahres-DB."""
    sim = _Sim()
    vorher = sim.performance_db
    _proc(sim, [2021, 2022])._fetch_years([2021, 2022], 'sortino', 'ANY', False)
    assert sim.performance_db == vorher


def test_zurueckschalten_auch_nach_fehler():
    sim = _Sim(jahre=(2021, 2022), fehler_bei='2022')
    vorher = sim.performance_db
    df = _proc(sim, [2021, 2022])._fetch_years([2021, 2022], 'sortino', 'ANY', False)
    assert sim.performance_db == vorher
    assert sorted(df['Date'].str[:4].unique()) == ['2021']   # 2021 bleibt nutzbar


def test_fehlendes_jahr_wird_uebersprungen():
    """Eine Jahresscheibe ohne Zeilen fuer diesen Index darf den Lauf nicht
    abbrechen — die uebrigen Jahre bleiben gueltig."""
    sim = _Sim(jahre=(2021,))
    df = _proc(sim, [2021, 2022])._fetch_years([2021, 2022], 'sortino', 'ANY', False)
    assert sorted(df['Date'].str[:4].unique()) == ['2021']


def test_alle_jahre_leer_gibt_leeren_frame():
    sim = _Sim(jahre=())
    df = _proc(sim, [2021, 2022])._fetch_years([2021, 2022], 'sortino', 'ANY', False)
    assert df.empty


def test_doppelte_tage_an_den_raendern_fallen_weg():
    """Scheiben koennen sich am Rand ueberlappen; zwei Kerzen fuer denselben
    Handelstag wuerden dem Simulator einen Tag doppelt vorlegen."""
    class _Overlap(_Sim):
        def fetch_combined_data_with_attach(self, **kw):
            return pd.DataFrame({'Date': ['2021-12-31 00:00:00'],
                                 'ticker': ['AAA'], 'close': [1.0]})
    sim = _Overlap()
    df = _proc(sim, [2021, 2022])._fetch_years([2021, 2022], 'sortino', 'ANY', False)
    assert len(df) == 1


# ------------------------------------------------- Lesecache je Jahresmenge

def test_lesecache_trennt_die_jahresmengen():
    """Derselbe Index mit anderer Jahresauswahl darf nicht den alten Frame
    bekommen — sonst zeigte ein Wechsel der Jahre stumm die alten Zahlen."""
    sim = _Sim()
    p = _proc(sim, [2021, 2022])
    cache = {}
    p._fetch_index_data(cache, 'sortino', 'ANY', False)
    p.use_years = [2021]
    p.use_year = 2021
    p._fetch_index_data(cache, 'sortino', 'ANY', False)
    assert len(cache) == 2, list(cache)


# ------------------------------------------------------------ Index-Auswahl

@pytest.mark.parametrize('gespeichert,erwartet', [
    (['^SPX'], ['^SPX']),
    ([], []),
    ('kein liste', []),
    (None, []),
])
def test_abgewaehlte_indizes_werden_gelesen(gespeichert, erwartet):
    class _Cfg:
        def get_value(self, key, default=None):
            return gespeichert
    p = object.__new__(MTP)
    p.sys_config = _Cfg()
    assert p._hidden_indices() == erwartet


def test_index_auswahl_speichert_die_abwahl_nicht_die_auswahl():
    """Gespeichert wird, was ABGEWAEHLT ist — sonst fiele ein neu im JSON
    eingetragener Index stillschweigend aus dem Lauf."""
    import inspect
    from tradinglib.premium import multi_transaction as m
    src = inspect.getsource(m.MultiTransactionProcessor.render)
    assert "set_value(\n                        'multi_hidden_indices'," in src \
        or "'multi_hidden_indices'" in src
    assert '_chosen_idx' in src and 'if i not in _chosen_idx' in src


# --------------------------------------- Auffaellige Ergebnisse: nur melden

def test_es_wird_kein_gewinn_mehr_verrechnet():
    """Drei Kriterien wurden hier nacheinander versucht und alle drei waren
    falsch: ein fester Euro-Deckel (traf METALS +235 %), eine Prozentschwelle
    (traf BTC +1.519 % und ETH +1.691 %) und der Stufensprung-Detektor (schlaegt
    bei Krypto schon bei einem normalen -42 %-Tag an). Ob ein Ergebnis echt ist,
    steht nicht im Ergebnis — deshalb wird nichts mehr genullt."""
    import inspect
    from tradinglib.premium import multi_transaction as m
    src = inspect.getsource(m)
    assert "gain'] > 10000" not in src
    assert "gain'] < -10000" not in src
    assert "_zero_artefact_gains" not in src
    quelle = inspect.getsource(m.MultiTransactionProcessor._flag_implausible_gains)
    assert "'gain'] = 0" not in quelle and "at[idx, 'gain']" not in quelle


def test_schwelle_ist_nur_eine_meldeschwelle():
    assert MTP.GAIN_NOTABLE_PCT > 0
    import inspect
    from tradinglib.premium import multi_transaction as m
    quelle = inspect.getsource(m.MultiTransactionProcessor._flag_implausible_gains)
    assert 'st.info' in quelle          # Hinweis, keine Warnung, keine Korrektur


def test_meldung_bleibt_ohne_kandidaten_aus():
    p = object.__new__(MTP)
    p.trades_df = pd.DataFrame({'ticker': ['A'], 'gainPct': [12.0], 'gain': [100.0]})
    p.disable_streamlit = True
    p._flag_implausible_gains()
    assert p.trades_df['gain'].iloc[0] == 100.0


def test_grosse_kryptogewinne_bleiben_unveraendert():
    p = object.__new__(MTP)
    p.db_path = 'database'
    p.disable_streamlit = True
    p.trades_df = pd.DataFrame({
        'ticker': ['BTC-EUR', 'ETH-EUR'],
        'gainPct': [1519.0, 1691.0],
        'gain': [99956.0, 84561.0],
        'buyDate': ['2020-01-03 00:00:00'] * 2,
        'sellDate': ['2025-10-06 00:00:00', '2026-08-21 00:00:00']})
    p._flag_implausible_gains()
    assert list(p.trades_df['gain']) == [99956.0, 84561.0]
