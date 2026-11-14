"""Asset summary chart grid: same plot view as the Asset Viewer + Edge score badge."""
from tradinglib import tiny_chart_grid as tcg


class _Conf:
    def __init__(self, **v):
        self.v = v

    def get_value(self, key, default=None):
        return self.v.get(key, default)


def test_no_plot_from_config_case_insensitive():
    conf = _Conf(overlay_no_plot=["ATC", "sup"], oszilator_no_plot=["prof"])
    ov, oz = tcg.no_plot_from_config(conf, ["atc", "heikin", "sup"], ["rsi", "Prof"])
    assert ov == ["atc", "sup"] and oz == ["Prof"]
    assert tcg.no_plot_from_config(_Conf(), ["atc"], None) == ([], [])


def test_score_badge():
    t = lambda k, **kw: k
    assert tcg.score_badge_html(None, t) == "" and tcg.score_badge_html({"total": None}, t) == ""
    h = tcg.score_badge_html({"total": 3.25, "bias": "bull", "chg": -1.5}, t)
    assert "+3.2" in h or "+3.3" in h
    assert "cot.bias_bull" in h and "▼ -1.5" in h and "#7cb342" in h
    assert "▲" not in tcg.score_badge_html({"total": -6.0, "bias": "strong_bear", "chg": None}, t)


def test_render_passes_no_plot_and_score(monkeypatch):
    calls = []

    class _TC:
        @staticmethod
        def tiny_chart(symbol, name, **kw):
            calls.append(kw)

            class C:
                fig, df = None, None
            return C()

    class _Col:
        def __init__(self, out):
            self.out = out

        def markdown(self, html, **kw):
            self.out.append(html)

        def plotly_chart(self, *a, **kw):
            pass

    out = []

    class _Empty:
        def columns(self, n):
            return [_Col(out) for _ in range(n)]

    class _Region:
        def empty(self):
            return _Empty()

    from tradinglib import ticker_scorecard as tsc
    monkeypatch.setattr(tsc, "quick_scores", lambda tickers: {"AAA": {"total": 4.0, "bias": "bull", "chg": 1.0}})
    r = tcg.ChartsGridRenderer(columns=2)
    r.no_plot_overlays, r.no_plot_oszilators = ["atc"], ["prof"]
    r.render(tickers=["AAA", "BBB"], tc=_TC, period="1y", interval="1d", overlays=["atc", "sup"],
             oszilators=["rsi", "prof"], username="u", url="", chart_config={}, region=_Region(),
             show_score=True)
    assert all(c["no_plot_overlays"] == ["atc"] and c["no_plot_oszilators"] == ["prof"] for c in calls)
    assert len(calls) == 2 and len(out) == 1 and "+4.0" in out[0]   # BBB has no score -> no badge
    # explicit lists win over what get_selectors stored
    calls.clear()
    r.render(tickers=["AAA"], tc=_TC, period="1y", interval="1d", overlays=["atc"], oszilators=[],
             username="u", url="", chart_config={}, region=_Region(), no_plot_overlays=[], no_plot_oszilators=[])
    assert calls[0]["no_plot_overlays"] == []
