r"""Tests fuer find_files_with_pattern - die Jahresauswahl der Sim-Datenbanken.

Die Auswahl "Daten vom Jahr" listete jedes Jahr dreifach. Ursache war re.match:
es verankert nur am Anfang, und die Sim-DBs laufen im WAL-Modus. Neben
asset_simulation_2025.db liegen also .db-wal und .db-shm, und auf das Muster
`asset_simulation_(\d+)\.db` passen die genauso - der Rest hinten wird ignoriert.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tradinglib.premium.multi_transaction import MultiTransactionProcessor as MTP
from tradinglib.premium.asset_simulator import AssetSimulator
from tradinglib.performance_details import Performance

PATTERN = r'asset_simulation_(\d+)\.db'


def _dir(tmp_path):
    """Ein Verzeichnis wie in Produktion: DB plus WAL-Seitendateien."""
    for y in (2024, 2025):
        for suffix in ('.db', '.db-wal', '.db-shm'):
            (tmp_path / f'asset_simulation_{y}{suffix}').write_text('')
    # laufendes Jahr ohne Jahreszahl + Sonderfaelle, die nicht mitzaehlen duerfen
    (tmp_path / 'asset_simulation_.db').write_text('')
    (tmp_path / 'asset_simulation_all.db').write_text('')
    (tmp_path / 'asset_simulation_2023.db.bak').write_text('')
    return tmp_path


def _finder(cls, directory):
    """Instanz ohne __init__ (das oeffnet DBs und Streamlit-Widgets)."""
    obj = object.__new__(cls)
    obj.get_path = lambda d: str(directory)
    return obj


@pytest.mark.parametrize('cls', [MTP, AssetSimulator, Performance])
def test_wal_dateien_zaehlen_nicht_mit(cls, tmp_path):
    """Der eigentliche Fehler: 2 Jahre, nicht 6 Eintraege."""
    got = _finder(cls, _dir(tmp_path)).find_files_with_pattern('database', PATTERN)
    assert sorted(got) == ['2024', '2025']


@pytest.mark.parametrize('cls', [MTP, AssetSimulator, Performance])
def test_backup_und_all_bleiben_draussen(cls, tmp_path):
    """asset_simulation_all.db hat keine Jahreszahl, .bak ist kein Jahresstand."""
    got = _finder(cls, _dir(tmp_path)).find_files_with_pattern('database', PATTERN)
    assert '2023' not in got          # nur als .db.bak vorhanden
    assert all(y.isdigit() for y in got)


def test_leeres_verzeichnis(tmp_path):
    assert _finder(MTP, tmp_path).find_files_with_pattern('database', PATTERN) == []


def test_jedes_jahr_genau_einmal(tmp_path):
    got = _finder(MTP, _dir(tmp_path)).find_files_with_pattern('database', PATTERN)
    assert len(got) == len(set(got))
