"""report_sections.py — the Asset Viewer tabs "Scorecard" and "Fundamental" for the HTML report.

Both tabs draw with Streamlit, which a downloaded HTML file cannot use. This
module rebuilds them as report content from the same data functions and the
same HTML panels the tabs use, so the report shows the numbers of the tabs:

* scorecard: :meth:`AssetCardPage.market_card` for Edge markets, otherwise the
  single-stock card of :mod:`tradinglib.ticker_scorecard_page`.
* fundamentals: ``fundamentals_page._payload`` / ``_quality_row`` plus its chart
  builders; the interactive tables become plain HTML tables.

Each function returns a list of report items -- an HTML string or a Plotly
figure -- that :func:`asset_report.build_asset_report_html` lays out. An empty
list means "no section" (not an equity, too little history, no data).
"""
from __future__ import annotations

import datetime as dt
import html
import logging


from tradinglib.i18n import t

logger = logging.getLogger(__name__)


def _esc(v) -> str:
    return html.escape(str(v))


def _note(text: str) -> str:
    return f"<div class='rs-note'>{_esc(text)}</div>"


def _grid(items_left: list, items_right: list, cls: str = 'rs-cols') -> list:
    """Two columns in the report; marker strings open/close the grid around the items."""
    return ([f"<div class='{cls}'><div>"] + items_left + ["</div><div>"]
            + items_right + ["</div></div>"])


# ── Scorecard ─────────────────────────────────────────────────────────────────

def scorecard_items(ticker: str, index_name: str | None = None) -> list:
    """The Scorecard tab as report items; [] when the tab would show no card."""
    try:
        from tradinglib import ticker_scorecard as tsc
        market = tsc.edge_market_for(ticker)
        return _market_items(market) if market else _stock_items(ticker, index_name or '')
    except Exception:
        logger.warning("report: scorecard for %s failed", ticker, exc_info=True)
        return []


def _banner(bias: str, notes: list[str]) -> str:
    from tradinglib.asset_card_page import _BIAS_COLORS
    extra = ''.join(f"<div style='font-size:.8rem;font-weight:500'>{_esc(n)}</div>" for n in notes)
    return (f"<div class='ac-banner' style='background:{_BIAS_COLORS[bias]}'>"
            f"{_esc(t('cot.bias_' + bias))}{extra}</div>")


def _market_items(key: str) -> list:
    from tradinglib import cot_data as cd
    from tradinglib import macro_data as md
    from tradinglib import trend_data as td
    from tradinglib.asset_card_page import (_BIAS_COLORS, _CSS, AssetCardPage, _f, _gauge,
                                            _history_chart, _panel, _score_cell)
    page = AssetCardPage()
    c = page.market_card(key, 52)
    if c is None:
        return []
    notes = ([t('asset.only_trend')] if c['n_comp'] == 1 else []) + \
            ([t('asset.unconfirmed')] if c['unconfirmed'] else [])
    comp = [
        [t('asset.total'), (_f(c['total'], '{:+.1f}'), _BIAS_COLORS[c['bias']]), '−10 … +10'],
        [t('asset.comp_trend'), _score_cell(c['trend']['score'], td.bias_label), '−4 … +4'],
        [t('asset.comp_cot'), _score_cell(c['cot_score'], cd.bias_label), '−3 … +3'],
        [t('asset.comp_macro'), _score_cell(c['macro_score'], md.bias_label), '−6 … +6'],
    ]
    caption = t('asset.caption', source=c['src']) + (' · ' + t('asset.inverted') if c['sign'] < 0 else '')
    left = [_banner(c['bias'], notes), _gauge(c['total']), _panel(t('asset.scores'), None, [], comp)]
    if not c['hist'].empty:
        left += [f"<h3>{_esc(t('asset.history_title'))}</h3>", _history_chart(c['hist'])]
    right = [page._trend_panel(c['trend'], c['s'], c['sign']),
             page._cot_panel(key, c['cot_row']),
             page._macro_panel(key, c['macro_tab'], c['macro_lat']),
             page._tech_panel(c['s'], c['sign'], page._rel_strength(key))]
    return [_CSS, _note(t('tsc.is_market', market=t(f'cot.mkt_{key}'))), _note(caption)] + _grid(left, right)


def _stock_items(ticker: str, index_name: str) -> list:
    from tradinglib import ticker_scorecard as tsc
    from tradinglib import total_data as tot
    from tradinglib import trend_data as td
    from tradinglib.asset_card_page import (_BIAS_COLORS, _CSS, AssetCardPage, _f, _gauge,
                                            _history_chart, _panel, _score_cell, _state)
    from tradinglib.ticker_scorecard_page import _context_panel, _data, _rs_label
    today = dt.date.today()
    s, idx, idx_key, hist, ctx, ccy = _data(ticker, index_name, today.isoformat(), 52)
    if len(s) < 200:
        return []
    c = tsc.card_at(s, idx, today)
    idx_label = t(f'cot.mkt_{idx_key}') if idx_key else (index_name or '–')
    notes = ([t('tsc.only_one')] if c['n'] == 1 else []) + \
            ([t('asset.unconfirmed')] if c['unconfirmed'] else [])
    comp = [
        [t('asset.total'), (_f(c['total'], '{:+.1f}'), _BIAS_COLORS[c['bias']]), '−10 … +10'],
        [t('asset.comp_trend'), _score_cell(c['trend'], td.bias_label), '−4 … +4'],
        [t('tsc.comp_rs'), _score_cell(c['rs'], _rs_label), '−2 … +2'],
        [t('tsc.comp_market', index=idx_label), _score_cell(c['market'], tot.bias_label), '−10 … +10'],
    ]
    if not hist.empty:
        for w in (1, 4):
            comp.append([t(f'tsc.chg_{w}w'), _f(tsc.change(hist, w), '{:+.1f}'), ''])
    left = [_banner(c['bias'], notes), _gauge(c['total']), _panel(t('asset.scores'), None, [], comp)]
    if not hist.empty:
        left += [f"<h3>{_esc(t('asset.history_title'))}</h3>", _history_chart(hist),
                 _note(t('tsc.history_hint'))]
    page = AssetCardPage()
    rs_rows = [
        [t('tsc.ret_13w'), _f(c['ret'], '{:+.1f} %'), ''],
        [t('tsc.idx_ret_13w', index=idx_label), _f(c['idx_ret'], '{:+.1f} %'), ''],
        [t('tsc.rs_diff'), _f(c['rs_diff'], '{:+.1f} Pp'), _state(c['rs'])],
        [t('tsc.market_trend', index=idx_label), _f(c['market_trend'], '{:+.0f}'), _state(c['market'])],
    ]
    right = [page._trend_panel(c['trend_detail'], s, 1),
             _panel(t('tsc.panel_rs'), None,
                    [t('asset.col_component'), t('asset.col_value'), t('asset.col_signal')], rs_rows),
             _context_panel(ctx, ccy),
             page._tech_panel(s, 1)]
    caption = t('tsc.caption', index=idx_label) if index_name else t('tsc.caption_no_index')
    return [_CSS, _note(caption)] + _grid(left, right)


# ── Fundamentals ──────────────────────────────────────────────────────────────

def _table(header: list[str], rows: list[list]) -> str:
    """Plain HTML table; a cell is text or (text, css colour) for coloured text."""
    def cell(c):
        if isinstance(c, tuple):
            return f"<td style='color:{c[1]};font-weight:600'>{_esc(c[0])}</td>"
        return f"<td>{_esc(c)}</td>"
    head = ''.join(f'<th>{_esc(h)}</th>' for h in header)
    body = ''.join('<tr>' + ''.join(cell(c) for c in r) + '</tr>' for r in rows)
    return f"<table class='rs-tab'><tr>{head}</tr>{body}</table>"


def fundamentals_items(ticker: str) -> list:
    """The Fundamental tab as report items; [] for non-equities or without data."""
    try:
        from tradinglib import fundamentals_page as fdp
        quality = _quality_items(ticker, fdp)
        data = fdp._payload(ticker)
    except Exception:
        logger.warning("report: fundamentals for %s failed", ticker, exc_info=True)
        return []
    if data is None:
        return quality
    try:
        return quality + _fund_items(data, fdp)
    except Exception:
        logger.warning("report: fundamentals render for %s failed", ticker, exc_info=True)
        return quality


def _quality_items(ticker: str, fdp) -> list:
    from tradinglib import quality_score as qs
    try:
        row = fdp._quality_row(ticker)
    except Exception:
        row = None
    if not row:
        return []
    score, tier = row.get('quality_score'), row.get('quality_tier')
    tier = int(tier) if tier is not None else None
    pct = row.get('quality_pct')
    if score is None:
        first = fdp._q_card(t('fund.q_score'), '–',
                            sub=t('fund.q_too_little', n=int(row.get('quality_avail') or 0)))
    else:
        first = fdp._q_card(t('fund.q_score'), f'{score:.0f}', fdp._TIER_COLORS.get(tier, 'inherit'),
                            t('fund.q_of_100'))
    cards = [
        first,
        fdp._q_card(t('fund.q_tier'), t(f'fund.qtier_{tier}') if tier else '–',
                    fdp._TIER_COLORS.get(tier, 'inherit'), t('fund.q_tier_sub', n=tier) if tier else ''),
        fdp._q_card(t('fund.q_rank'), '–' if pct is None else f'{pct:.0f}',
                    fdp._rank_color(None if pct is None else pct / 10), t('fund.q_rank_sub')),
        fdp._q_card(t('fund.q_avail'), f"{int(row.get('quality_avail') or 0)}", sub=t('fund.q_avail_sub')),
    ]
    out = [f"<h3>{_esc(t('fund.q_header'))}</h3>",
           "<div class='rs-cards'>" + ''.join(f'<div>{c}</div>' for c in cards) + '</div>']
    flags = [f for f in str(row.get('quality_flags') or '').split(',') if f]
    out.append(_note(t('fund.q_flags', flags=', '.join(t(f'cand.qflag_{f}') for f in flags))
                     if flags else t('fund.q_no_flags')))
    rows = []
    financial = bool(row.get('financial'))
    for key, group, points, _higher in qs.CRITERIA:
        ok = row.get(f'{key}_ok')
        if financial and key in qs.FINANCIAL_SKIP:
            shown = t('fund.q_skipped')
        elif key == 'fcf_years' and ok is None and row.get(key) is not None:
            shown = t('fund.q_years_short', n=int(row[key]))
        else:
            shown = fdp._q_value(key, row.get(key))
        rows.append([t(f'fund.qg_{group}'), t(f'fund.qc_{key}'), shown,
                     '–' if ok is None else ('✅' if ok else '❌'),
                     f'– / {points}' if ok is None else f'{points if ok else 0} / {points}'])
    out.append(_table([t('fund.col_group'), t('fund.col_metric'), t('fund.col_value'),
                       t('fund.q_col_result'), t('fund.q_col_points')], rows))
    out.append(_note(t('fund.q_method', date=str(row.get('asof') or '')[:10])))
    return out


def _pct_cell(value) -> tuple | str:
    if value is None:
        return ''
    return f'{value:.0f}', _pct_color(value)


def _pct_color(value) -> str:
    from tradinglib import fundamentals_page as fdp
    return fdp._rank_color(value / 10)


def _fund_items(data: dict, fdp) -> list:
    from tradinglib import fundamentals as fu
    out = []
    # Headline ranks (industry | sector) and the price, as on the tab.
    cards = [fdp._rank_pair(t(f'fund.rank_{g}'), (data['ranks'].get(g) or {}).get('industry'),
                            (data['ranks'].get(g) or {}).get('sector')) for g in fu.RANK_GROUPS]
    if data['price']:
        cards.append(fdp._q_card(t('fund.price'), f"{data['price']:,.2f}", sub=_esc(data['currency'])))
    out.append("<div class='rs-cards'>" + ''.join(f'<div>{c}</div>' for c in cards) + '</div>')
    out.append(_note(t('fund.provenance', industry=data['industry'] or '—', n_industry=data['n_industry'],
                       sector=data['sector'] or '—', n_sector=data['n_sector'],
                       date=str(data['timestamp'] or '')[:10], years=len(data['years']),
                       currency=data['report_currency'])))
    if data['thin_industry']:
        out.append(_note(t('fund.industry_thin', n=data['thin_industry'],
                           industry=data['industry'] or '—', peers=data['n_industry'])))
    if data['fx_missing']:
        out.append(_note(t('fund.fx_missing', quote=data['currency'], report=data['report_currency'])))

    # Signals: warnings | positives.
    if data['warnings'] or data['positives']:
        cols = []
        for items, prefix, title in ((data['warnings'], 'warn', t('fund.warnings')),
                                     (data['positives'], 'good', t('fund.positives'))):
            lis = ''.join(f"<li>{_esc(t(f'fund.{prefix}_{k}', **p))}</li>" for k, p in items)
            cols.append([f"<h3>{_esc(title)}</h3>",
                         f'<ul>{lis}</ul>' if lis else _note(t('fund.none'))])
        out += _grid(cols[0], cols[1], 'rs-cols rs-even')

    # Metric panels with peer medians and percentiles.
    metrics, ind, sec = data['metrics'], data['peers_industry'], data['peers_sector']
    header = [t('fund.col_metric'), t('fund.col_value'), t('fund.col_median_industry'),
              t('fund.col_pct_industry'), t('fund.col_median_sector'), t('fund.col_pct_sector')]
    for group in fu.GROUPS:
        rows = []
        for key, (grp, unit, higher) in fu.METRICS.items():
            value = metrics.get(key)
            if grp != group or value is None:
                continue
            rows.append([t(f'fund.m_{key}'), fdp._fmt(value, unit),
                         fdp._median(ind.get(key), unit), _pct_cell(fu.percentile(value, ind.get(key), higher)),
                         fdp._median(sec.get(key), unit), _pct_cell(fu.percentile(value, sec.get(key), higher))])
        if not rows:
            continue
        out += [f"<h3>{_esc(t(f'fund.group_{group}'))}</h3>", _table(header, rows)]
        if group == 'strength':
            score, maximum, checks = data['piotroski']
            if maximum:
                known = [(k, v) for k, v in checks if v is not None]
                marks = ' '.join(f"{'✅' if ok else '❌'} {t(f'fund.pio_{k}')}" for k, ok in known)
                out.append(_note(t('fund.piotroski_detail', score=score, max=maximum, checks=marks)))
                if maximum < 9:
                    out.append(_note(t('fund.piotroski_partial', missing=9 - maximum)))
            z = metrics.get('altman_z')
            if z is not None:
                band = ('distress' if z < fdp._ALTMAN_DISTRESS else 'safe' if z > fdp._ALTMAN_SAFE else 'grey')
                out.append(_note(t('fund.altman_band', z=round(z, 2), band=t(f'fund.altman_{band}'))))

    hist = data['history']
    if hist is not None and not hist.empty:
        out.append(f"<h3>{_esc(t('fund.history_header', years=len(hist)))}</h3>")
        out += _grid([fdp._bar_line_chart(hist, data['report_currency'])], [fdp._margin_chart(hist)],
                     'rs-cols rs-even')
        out += _grid([fdp._balance_chart(hist, data['report_currency'])], [fdp._shares_chart(hist)],
                     'rs-cols rs-even')

    val_hist = data['val_hist']
    if val_hist is not None and not val_hist.empty:
        bands = [fdp._band_chart(val_hist[k], t(f'fund.m_{k}'))
                 for k in ('pe', 'ps', 'pb') if k in val_hist and val_hist[k].notna().any()]
        if bands:
            out.append(f"<h3>{_esc(t('fund.bands_header'))}</h3>")
            out.append("<div class='rs-cols3'>")
            for f in bands:
                out += ['<div>', f, '</div>']
            out.append('</div>')
            out.append(_note(t('fund.bands_note')))
    out.append(_note(t('fund.disclaimer')))
    return out


# ── Prompt text ───────────────────────────────────────────────────────────────
# The AI prompt gets the same two tabs as compact text. German like the rest of
# the prompt scaffold (market_overview_page); metric names come from i18n.

_BIAS_DE = {'strong_bull': 'stark bullisch', 'bull': 'bullisch', 'neutral': 'neutral',
            'bear': 'bärisch', 'strong_bear': 'stark bärisch'}


def _n(v, fmt: str) -> str:
    try:
        if v is None or v != v:
            return '–'
        return fmt.format(v)
    except (TypeError, ValueError):
        return '–'


def scorecard_text(ticker: str, index_name: str | None = None) -> str:
    """The Scorecard tab as a few prompt lines; '' when the tab shows no card."""
    try:
        from tradinglib import ticker_scorecard as tsc
        market = tsc.edge_market_for(ticker)
        return _market_text(market) if market else _stock_text(ticker, index_name or '')
    except Exception:
        logger.warning("prompt: scorecard text for %s failed", ticker, exc_info=True)
        return ''


def _trend_line(tr: dict) -> str:
    m, sea = tr.get('metrics') or {}, tr.get('season') or {}
    if not m:
        return ''
    line = (f"Trend-Details: Abstand SMA200 {_n(m.get('dist200'), '{:+.1f} %')} · "
            f"Momentum 3 M {_n(m.get('r63'), '{:+.1f} %')}")
    if sea.get('n'):
        line += (f" · Saison nächste 30 T Ø {_n(sea.get('mean'), '{:+.2f} %')}, "
                 f"Trefferquote {_n(sea.get('hit'), '{:.0f} %')} ({sea['n']} J.)")
    return line


def _head_line(total, bias, notes) -> str:
    extra = ''.join(f' · {n}' for n in notes)
    return (f"Gesamt-Score: {_n(total, '{:+.1f}')} (Skala −10 … +10) · "
            f"Bias: {_BIAS_DE.get(bias, bias)}{extra}")


def _market_text(key: str) -> str:
    from tradinglib.asset_card_page import AssetCardPage
    c = AssetCardPage().market_card(key, 52)
    if c is None:
        return ''
    notes = (['nur eine Komponente'] if c['n_comp'] == 1 else []) + \
            (['vom Kurs nicht bestätigt'] if c['unconfirmed'] else [])
    lines = [f"Edge-Markt {t(f'cot.mkt_{key}')} (Markt-Scorecard)",
             _head_line(c['total'], c['bias'], notes),
             f"Komponenten: Trend & Saison {_n(c['trend']['score'], '{:+.0f}')} (−4 … +4) · "
             f"COT-Positionierung {_n(c['cot_score'], '{:+.0f}')} (−3 … +3) · "
             f"Makro {_n(c['macro_score'], '{:+.0f}')} (−6 … +6)"]
    hist = c['hist']
    if hist is not None and not hist.empty and {'date', 'total'} <= set(hist.columns):
        from tradinglib import ticker_scorecard as tsc
        lines.append(_change_line(tsc, hist))
    lines.append(_trend_line(c['trend']))
    return '\n'.join(x for x in lines if x)


def _change_line(tsc, hist) -> str:
    return (f"Veränderung Gesamt-Score: 1 W {_n(tsc.change(hist, 1), '{:+.1f}')} · "
            f"4 W {_n(tsc.change(hist, 4), '{:+.1f}')}")


def _stock_text(ticker: str, index_name: str) -> str:
    from tradinglib import ticker_scorecard as tsc
    from tradinglib.ticker_scorecard_page import _data, _macro_score
    today = dt.date.today()
    s, idx, _idx_key, hist, ctx, ccy = _data(ticker, index_name, today.isoformat(), 52)
    if len(s) < 200:
        return ''
    c = tsc.card_at(s, idx, today)
    has_idx = idx is not None and not idx.empty
    notes = (['nur eine Komponente'] if c['n'] == 1 else []) + \
            (['vom Kurs nicht bestätigt'] if c['unconfirmed'] else [])
    lines = [_head_line(c['total'], c['bias'], notes)]
    if has_idx:
        lines.append(f"Komponenten: Trend & Saison {_n(c['trend'], '{:+.0f}')} (−4 … +4) · "
                     f"Relative Stärke vs. {index_name} {_n(c['rs'], '{:+.0f}')} (−2 … +2) · "
                     f"Marktumfeld {index_name} {_n(c['market'], '{:+.0f}')} (−10 … +10)")
    else:
        lines.append(f"Komponenten: Trend & Saison {_n(c['trend'], '{:+.0f}')} (−4 … +4) · "
                     "kein Vergleichsindex, relative Stärke und Marktumfeld fehlen")
    if not hist.empty:
        lines.append(_change_line(tsc, hist))
    if c['ret'] is not None:
        lines.append(f"Rendite 13 W: {_n(c['ret'], '{:+.1f} %')}"
                     + (f" vs. Index {_n(c['idx_ret'], '{:+.1f} %')} "
                        f"(Differenz {_n(c['rs_diff'], '{:+.1f}')} Pp)" if has_idx else ''))
    lines.append(_trend_line(c['trend_detail']))

    def ctx_num(k, fmt):
        v = ctx.get(k)
        if v is None or v != v or (v == 0 and k in ('riskBucket', 'trendScore', 'mtt_rs')):
            return '–'
        return fmt.format(v)
    markov = {1: 'bullisch', 0: 'neutral', -1: 'bärisch'}.get(ctx.get('markov_regime'), '–')
    macro = _macro_score(ccy)
    lines.append(f"Kontext (nicht im Score): Risikostufe {ctx_num('riskBucket', '{:.0f}')}/4 · "
                 f"trendScore {ctx_num('trendScore', '{:.0f}')} · 4PS-Phase {ctx_num('fps_phase', '{:.0f}')} · "
                 f"Markov-Regime {markov} · Minervini-RS {ctx_num('mtt_rs', '{:.0f}')}"
                 + (' (Trend-Template erfüllt)' if ctx.get('mtt_pass') == 1 else '')
                 + (f" · Makro-Score {ccy} {_n(macro, '{:+.0f}')}" if ccy else ''))
    return '\n'.join(x for x in lines if x)


def fundamentals_text(ticker: str, compact: bool = False) -> str:
    """The Fundamental tab as prompt lines; '' for non-equities or without data.

    compact drops the per-metric lines (peer percentiles of every ratio) and keeps
    quality score, ranks, signals, Piotroski and Altman Z.
    """
    try:
        from tradinglib import fundamentals_page as fdp
        lines = _quality_text(ticker, fdp)
        data = fdp._payload(ticker)
        if data is not None:
            lines += _fund_text(data, fdp, compact)
        return '\n'.join(lines)
    except Exception:
        logger.warning("prompt: fundamentals text for %s failed", ticker, exc_info=True)
        return ''


def _quality_text(ticker: str, fdp) -> list[str]:
    from tradinglib import quality_score as qs
    try:
        row = fdp._quality_row(ticker)
    except Exception:
        row = None
    if not row:
        return []
    score, tier, pct = row.get('quality_score'), row.get('quality_tier'), row.get('quality_pct')
    tier = int(tier) if tier is not None else None
    flags = [f for f in str(row.get('quality_flags') or '').split(',') if f]
    line = (f"Qualitäts-Score: {_n(score, '{:.0f}')}/100"
            + (f" · Stufe {tier} von 4 ({t(f'fund.qtier_{tier}')})" if tier else '')
            + f" · sektor-relativer Rang {_n(pct, '{:.0f}')}. Perzentil"
            + f" · Datenabdeckung {int(row.get('quality_avail') or 0)}/100 Punkte"
            + f" · Warnzeichen: {', '.join(t(f'cand.qflag_{f}') for f in flags) if flags else 'keine'}")
    failed = [t(f'fund.qc_{k}') for k, _g, _p, _h in qs.CRITERIA if row.get(f'{k}_ok') == 0]
    out = [line]
    if failed:
        out.append('Qualitäts-Kriterien nicht erfüllt: ' + ', '.join(failed))
    return out


def _fund_text(data: dict, fdp, compact: bool) -> list[str]:
    from tradinglib import fundamentals as fu
    out = []
    ranks = []
    for g in fu.RANK_GROUPS:
        r = data['ranks'].get(g) or {}
        ranks.append(f"{t(f'fund.rank_{g}')} {_n(r.get('industry'), '{:.1f}')}/{_n(r.get('sector'), '{:.1f}')}")
    out.append(f"Ränge 0–10 (Branche {data['industry'] or '–'} / Sektor {data['sector'] or '–'}): "
               + ' · '.join(ranks))
    if data['warnings']:
        out.append('Auffälligkeiten: ' + '; '.join(t(f'fund.warn_{k}', **p) for k, p in data['warnings']))
    if data['positives']:
        out.append('Pluspunkte: ' + '; '.join(t(f'fund.good_{k}', **p) for k, p in data['positives']))
    score, maximum, _checks = data['piotroski']
    z = data['metrics'].get('altman_z')
    extra = []
    if maximum:
        extra.append(f"Piotroski F-Score {score}/{maximum}")
    if z is not None:
        zone = ('Gefahrenzone' if z < fdp._ALTMAN_DISTRESS
                else 'sicher' if z > fdp._ALTMAN_SAFE else 'Grauzone')
        extra.append(f"Altman Z {z:.2f} ({zone})")
    if extra:
        out.append(' · '.join(extra))
    if not compact:
        metrics, ind, sec = data['metrics'], data['peers_industry'], data['peers_sector']
        for group in fu.GROUPS:
            cells = []
            for key, (grp, unit, higher) in fu.METRICS.items():
                v = metrics.get(key)
                if grp != group or v is None:
                    continue
                pi = fu.percentile(v, ind.get(key), higher)
                ps = fu.percentile(v, sec.get(key), higher)
                cells.append(f"{t(f'fund.m_{key}')} {fdp._fmt(v, unit)} "
                             f"(P {_n(pi, '{:.0f}')}/{_n(ps, '{:.0f}')})")
            if cells:
                label = t(f'fund.group_{group}').split(' ', 1)[-1]       # drop the emoji
                out.append(f"{label}: " + ' · '.join(cells))
        out.append('P = Perzentil gegen Branche/Sektor, 100 = bester Wert der Gruppe.')
    out.append(f"Stand Stammdaten {str(data['timestamp'] or '')[:10]}, {len(data['years'])} Geschäftsjahre, "
               f"Bilanzwährung {data['report_currency']}.")
    return out
