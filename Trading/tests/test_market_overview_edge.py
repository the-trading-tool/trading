"""Edge Scores context (macro / COT) in the AI market overview prompt."""
import json
from pathlib import Path

import pandas as pd

from tradinglib import market_overview_page as mo

LOCALES = Path(__file__).resolve().parent.parent / 'locales'


def _lat():
    rows = [
        ('USD', 'rate', '2026-10-01', 'M', 3.875, 3.625, 0.25),
        ('USD', 'cpi', '2026-09-01', 'M', 3.74, 3.53, 0.21),
        ('USD', 'gdp', '2026-07-01', 'Q', 0.83, 0.55, 0.28),
        ('USD', 'cli', '2026-10-01', 'M', 101.0, 100.97, 0.03),
        ('CHF', 'unemp', '2026-04-01', 'Q', 5.03, 5.06, -0.03),
        ('JPY', 'rate', '2026-10-01', 'M', 1.25, None, None),
    ]
    df = pd.DataFrame(rows, columns=['ccy', 'indicator', 'period', 'freq', 'value', 'prev', 'change'])
    df['period'] = pd.to_datetime(df['period'])
    return df


def _card():
    return pd.DataFrame([{
        'key': 'GOLD', 'market': 'Gold', 'group': 'commodities',
        'report_date': '2026-11-17', 'release_date': '2026-11-20',
        'long_pct': 82.7, 'long_rank': 49.0, 'd_long_pct': -3.5, 'cot_index': 12.0,
        'score': 2, 'bias': 'bull',
    }])


def test_macro_block_values_periods_and_changes():
    txt = mo.format_macro_block(_lat(), '2026-11-26T17:08:08')
    assert 'Abruf: 2026-11-26' in txt
    assert 'Leitzins 3.88 % (10/2026; 6 M: +0.25 Pp)' in txt
    assert 'Inflation J/J 3.74 % (09/2026; 3 M: +0.21 Pp)' in txt
    assert 'BIP Q/Q +0.83 % (Q3/2026; Vorquartal +0.55 %)' in txt
    assert 'Frühindikator CLI 101.0 (10/2026; 1 M: +0.03)' in txt
    # quarterly unemployment compares with the previous quarter, not "3 M"
    assert 'Arbeitslosigkeit 5.03 % (Q2/2026; ggü. Vorquartal: -0.03 Pp)' in txt
    # missing change -> value without change, no "nan"
    assert 'Leitzins 1.25 % (10/2026)' in txt and 'nan' not in txt
    assert txt.index('─── USD') < txt.index('─── JPY') < txt.index('─── CHF')


def test_macro_block_empty():
    assert mo.format_macro_block(pd.DataFrame()) == ''
    assert mo.format_macro_block(None) == ''


def test_blocks_carry_no_scores_or_bias():
    """Only raw values: the COT score had no predictive power, the index total worked contrarian."""
    txt = mo.format_macro_block(_lat()) + mo.format_cot_block(_card())
    data = [ln for ln in txt.splitlines() if ln.startswith('  ')]
    assert len(data) == 4
    for word in ('bull', 'bear', 'Score', 'Bias', '+2'):
        assert not any(word in ln for ln in data), word
    assert 'KEINE Vorhersagekraft' in txt
    assert 'Gold: 82.7 % long (Rang 3J: 49) · Δ Woche -3.5 Pp · COT-Index 52W 12' in txt


def test_cot_block_empty():
    assert mo.format_cot_block(pd.DataFrame()) == ''


def test_prompt_includes_blocks_and_usage_only_when_given():
    sec = {'correlations': False}
    p = mo._build_market_prompt([], [], '1d', '1y', [], sections=sec,
                                macro=mo.format_macro_block(_lat()), cot=mo.format_cot_block(_card()))
    assert 'MAKRO-UMFELD' in p and 'MAKRO-NUTZUNG' in p
    assert 'COT-POSITIONIERUNG' in p and 'COT-NUTZUNG' in p
    p = mo._build_market_prompt([], [], '1d', '1y', [], sections=sec, macro='', cot=None)
    assert 'MAKRO' not in p.replace('Makro-Bild', '') and 'COT' not in p


def test_load_edge_context_off_does_nothing(monkeypatch):
    import tradinglib.macro_data as md
    monkeypatch.setattr(md, 'ensure_fresh', lambda: (_ for _ in ()).throw(AssertionError('no fetch')))
    assert mo.load_edge_context(False, False) == ('', '')


def test_sections_defaults_and_single_asset_off():
    assert mo._SECTION_DEFAULTS['macro'][1] is True
    assert mo._SECTION_DEFAULTS['cot'][1] is False
    assert mo._SINGLE_ASSET_SECTIONS['macro'] is False and mo._SINGLE_ASSET_SECTIONS['cot'] is False


def test_section_labels_in_both_languages():
    for lang in ('de', 'en'):
        d = json.loads((LOCALES / f'{lang}.json').read_text(encoding='utf-8'))
        for key in mo._SECTION_DEFAULTS:
            assert f'mv.section.{key}' in d, (lang, key)
        assert 'mv.spinner_edge' in d
