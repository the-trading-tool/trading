"""Asset report: Scorecard and Fundamental tabs as report sections."""
import plotly.graph_objects as go

from tradinglib import asset_report as ar
from tradinglib import report_sections as rs


def _fig():
    return go.Figure(go.Bar(x=[1, 2], y=[3, 4]))


def test_sections_are_placed_and_plotly_is_inlined_once():
    doc = ar.build_asset_report_html(
        ticker='T', scorecard_items=['<p>card</p>', _fig()],
        fundamentals_items=['<p>fund</p>', _fig(), _fig()],
        info_text='info', labels={'scorecard': 'SC', 'fundamentals': 'FU'})
    assert doc.count('<h2>SC</h2>') == 1 and doc.count('<h2>FU</h2>') == 1
    assert doc.index('<h2>SC</h2>') < doc.index('<div class="info">') < doc.index('<h2>FU</h2>')
    assert doc.count('plotly.js v') == 1               # library once, three figures
    assert doc.count('class="plotly-graph-div"') == 3


def test_trend_chart_keeps_the_library_and_sections_reuse_it():
    doc = ar.build_asset_report_html(ticker='T', trend_fig=_fig(), scorecard_items=[_fig()])
    assert doc.count('plotly.js v') == 1
    assert doc.index('plotly.js v') < doc.index('Scorecard')


def test_empty_sections_are_omitted():
    doc = ar.build_asset_report_html(ticker='T', scorecard_items=[], fundamentals_items=None)
    assert 'Scorecard' not in doc and 'Fundamentaldaten' not in doc


def test_no_data_gives_no_section(monkeypatch):
    from tradinglib import fundamentals_page as fdp
    monkeypatch.setattr(fdp, '_payload', lambda ticker: None)
    monkeypatch.setattr(fdp, '_quality_row', lambda ticker: None)
    assert rs.fundamentals_items('NOPE') == []


def test_failures_do_not_break_the_report(monkeypatch):
    from tradinglib import ticker_scorecard as tsc

    def boom(_t):
        raise RuntimeError('no db')
    monkeypatch.setattr(tsc, 'edge_market_for', boom)
    assert rs.scorecard_items('T', '^SPX') == []


def test_short_history_gives_no_scorecard(monkeypatch):
    import pandas as pd
    from tradinglib import ticker_scorecard as tsc
    from tradinglib import ticker_scorecard_page as tsp
    monkeypatch.setattr(tsc, 'edge_market_for', lambda _t: None)
    monkeypatch.setattr(tsp, '_data', lambda *a: (pd.Series([1.0] * 50), None, None,
                                                  pd.DataFrame(), {}, None))
    assert rs.scorecard_items('T', '') == []


def _prompt(monkeypatch, **kw):
    import pandas as pd
    from tradinglib import market_overview_page as mo
    monkeypatch.setattr(mo, '_result_from_df', lambda df, d, i: {'ticker': d, 'error': 'no data'})
    return mo.build_single_asset_prompt(pd.DataFrame(), 'T', 'Test', 'Tech', '1d', '1y',
                                        include_tnx=False, **kw)


def test_prompt_carries_both_tabs_and_asks_for_them(monkeypatch):
    p = _prompt(monkeypatch, scorecard='Gesamt-Score: +5.0', fundamentals='Qualitäts-Score: 80/100')
    assert 'SCORECARD (Edge-Score' in p and 'Gesamt-Score: +5.0' in p
    assert 'FUNDAMENTALDATEN' in p and 'Qualitäts-Score: 80/100' in p
    assert '• Scorecard:' in p and 'Nutze Qualitäts-Score' in p
    assert p.index('Qualitäts-Score: 80/100') < p.index('ANALYSE-AUFGABE')


def test_prompt_without_tabs_is_unchanged(monkeypatch):
    p = _prompt(monkeypatch)
    assert 'SCORECARD (Edge-Score' not in p and 'FUNDAMENTALDATEN' not in p
    assert '• Scorecard:' not in p and 'TEIL 3' not in p


def test_fundamentals_text_compact_drops_metric_lines(monkeypatch):
    import pandas as pd
    from tradinglib import fundamentals as fu
    from tradinglib import fundamentals_page as fdp
    data = {'ranks': {g: {'industry': 5.0, 'sector': 4.0} for g in fu.RANK_GROUPS},
            'industry': 'Software', 'sector': 'Technology', 'warnings': [], 'positives': [],
            'piotroski': (7, 9, []), 'metrics': {'pe': 20.0, 'altman_z': 3.5},
            'peers_industry': {'pe': [10.0, 30.0]}, 'peers_sector': {'pe': [15.0, 25.0]},
            'timestamp': '2026-11-16', 'years': ['a', 'b', 'c', 'd'], 'report_currency': 'USD',
            'history': pd.DataFrame(), 'val_hist': None}
    monkeypatch.setattr(fdp, '_payload', lambda ticker: data)
    monkeypatch.setattr(fdp, '_quality_row', lambda ticker: {
        'quality_score': 80, 'quality_tier': 2, 'quality_pct': 90, 'quality_avail': 92,
        'quality_flags': '', 'roe_ok': 1, 'peg_ok': 0})
    full = rs.fundamentals_text('T')
    short = rs.fundamentals_text('T', compact=True)
    assert 'Qualitäts-Score: 80/100' in short and 'Piotroski F-Score 7/9' in short
    assert 'Altman Z 3.50 (sicher)' in short
    assert 'P = Perzentil' in full and 'P = Perzentil' not in short
    assert 'nicht erfüllt' in short                    # peg_ok == 0


def test_report_labels_exist():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'locales'
    for lang in ('de', 'en'):
        keys = json.loads((root / f'{lang}.json').read_text(encoding='utf-8'))
        assert 'report.scorecard' in keys and 'report.fundamentals' in keys
