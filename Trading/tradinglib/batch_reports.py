"""batch_reports.py — sequential HTML-report generation for a list of assets.

Generates one self-contained HTML report per ticker (Trend chart, key data, info,
seasonality, signals, market context, AI analysis) — the same report the Asset
Viewer produces on demand, but batched over the "Monitored assets" list.

The AI step is rate-limited on free tiers. When a provider limit is hit after all
providers are exhausted, the batch PAUSES (it does not lose progress): the current
ticker stays pending and the job can be resumed later — it continues exactly where
it stopped. Progress + results live in st.session_state so a resume survives reruns.

Reuse strategy (no logic duplication): the deterministic per-asset text blocks are
built by borrowing render_mainpage's own helper methods via a state-only instance
(``render_mainpage.__new__`` — no __init__, so nothing renders). The chart figure /
price df / asset-info df come from a headless ``tiny_chart``; the prompt, rate
context, sector rotation, correlations and the HTML document come from the same
module-level functions the single-asset AI tab uses.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import zipfile

import streamlit as st

from tradinglib.i18n import t
from tradinglib import tiny_chart as tc
from tradinglib import main_page as mp
from tradinglib import market_overview_page as mo
from tradinglib import asset_report as ar
from tradinglib import tools as ts
from tradinglib.ai_client import AiClient, AiRateLimitError, AiProviderError
from tradinglib.license_manager import has_feature, FEATURE_SEASONALITY, FEATURE_STRATEGY_ENGINE

try:  # premium, may be absent
    from tradinglib.premium import seasonality as sn
except Exception:  # pragma: no cover
    sn = None

logger = logging.getLogger(__name__)

_JOB_KEY = '_batch_reports_job'


# ── helpers ──────────────────────────────────────────────────────────────────

def _clean(v) -> str:
    """Normalize a get_ticker_value scalar to a clean string ('' for empties)."""
    s = str(v).strip()
    return '' if s in ('', '0', 'None', 'nan') else s


def _resolve_index_name(ticker: str) -> str:
    """Primary index of a ticker — same query as search.FullTextSearch.resolve_index_name,
    inlined so no search widget is instantiated. '' when the ticker is in no index."""
    t_sel = (ticker or '').strip()
    if not t_sel:
        return ''
    try:
        from tradinglib.tools import open_db
        db = ts.Tools().get_path(path='database', file_name='yf_tickers.db')
        with open_db(db, readonly=True) as conn:
            row = conn.execute(
                "SELECT i.name FROM stocks s "
                "JOIN stock_indices si ON si.stock_id = s.id "
                "JOIN indices i ON i.id = si.index_id "
                "WHERE s.Ticker = ? "
                "ORDER BY (i.name LIKE '^%') DESC LIMIT 1",
                (t_sel,),
            ).fetchone()
        return row[0] if row and row[0] else ''
    except Exception:
        logger.debug("index-name resolve failed for %s", ticker, exc_info=True)
        return ''


def build_report_html_for_ticker(
    ticker: str, sys_conf, username: str,
    interval: str, period: str, overlays, oszilators,
    compact: bool = True, include_ai: bool = True,
    seasonality_enabled: bool | None = None,
) -> dict:
    """Build the full HTML report for one ticker (headless).

    Returns {'html', 'name', 'ai_model', 'ai_ts', 'ai_ok'}.
    Raises AiRateLimitError / AiProviderError when the AI step is limited/unavailable
    so the caller can PAUSE the batch and retry this ticker on resume — the report is
    only assembled after a successful AI call, so a paused ticker leaves no partial
    output.
    """
    if seasonality_enabled is None:
        seasonality_enabled = bool(sn) and has_feature(FEATURE_SEASONALITY)

    # 1) Headless chart → figure, price df, asset-info df. pips_select/zoom off so
    #    fetch_data renders no slider; the fig is built but never plotted here.
    t_chart = tc.tiny_chart(
        ticker, longname=f"{ticker} - {interval}/{period}",
        interval=interval, period=period,
        candle_chart=True, show_trend=False, range_breaks=True,
        add_sub_plots=list(oszilators or []), add_overlays=list(overlays or []),
        username=username, zoom=False, pips_select=False, add_current=False,
        region=st.empty(),
    )
    price_df = t_chart.df
    info_df  = t_chart.ticker  # asset_info metadata DataFrame (get_ticker_value source)
    if price_df is None or getattr(price_df, 'empty', True):
        raise ValueError(f"no price data for {ticker}")

    # 2) State-only render_mainpage instance to reuse its block builders (no render()).
    ctx = mp.render_mainpage.__new__(mp.render_mainpage)
    ctx.sys_conf = sys_conf
    ctx.df       = price_df
    ctx.username = username

    def _val(key):
        v = ctx.get_ticker_value(info_df, key)
        return _clean(v)

    info_text  = _val('longBusinessSummary')
    quote_type = _val('quoteType')
    is_index   = str(ticker).startswith('^') or quote_type.upper() == 'INDEX'
    idx_name   = _resolve_index_name(ticker)

    # Leitindex context only for single assets; breadth from the index the user follows.
    parent_index = None
    if (not is_index) and idx_name and str(idx_name).startswith('^'):
        idx_yf = mo._YF_TICKER_MAP.get(idx_name, idx_name)
        parent_index = (idx_name, idx_yf, str(idx_name).lstrip('^'))
    stress_src = (idx_name if (idx_name and str(idx_name).startswith('^'))
                  else (ticker if is_index else None))

    asset_info_block = ctx._build_asset_info_block(info_df, info_text)
    market_status    = ctx._build_market_stress_text(stress_src)
    signals_block    = ctx._build_signals_text()
    news_items       = ctx._collect_news_items(ticker, None)
    news_block       = ctx._build_news_text(news_items)

    category   = _val('sector') or (str(idx_name) if idx_name else '') or quote_type or ''
    longname   = _val('longName') or _val('shortName') or ticker
    market     = (_clean(idx_name) or _val('fullExchangeName') or _val('exchange')
                  or _clean(category))
    asset_sector = _val('sector')
    indicators = [i for i in (list(overlays or []) + list(oszilators or [])) if i != 'bar']

    season_block = ''
    season_fig   = None
    if seasonality_enabled and sn is not None:
        try:
            season_block = sn.compute_seasonality_summary(ticker) or ''
        except Exception:
            logger.debug("seasonality summary failed for %s", ticker, exc_info=True)
        try:
            season_fig = sn.build_seasonality_figure(ticker, longname)
        except Exception:
            logger.debug("seasonality figure failed for %s", ticker, exc_info=True)

    # 3) Marktkontext (sector rotation once, reused for prompt + report) + correlations.
    sector_rotation = mo.build_sector_rotation_text(asset_sector)
    context_parts = []
    if sector_rotation:
        context_parts.append("Sektor-Rotation (RRG vs. Markt):\n" + sector_rotation)
    try:
        _corr = mo._correlation_prompt_block()
        if _corr:
            context_parts.append(_corr)
    except Exception:
        logger.debug("correlation block failed", exc_info=True)
    context_text = "\n\n".join(context_parts)
    rate_context = mo.get_rate_context(interval, period, sys_conf)

    # 4) AI analysis — may raise (rate limit / all providers down) → caller pauses.
    ai_analysis, ai_model, ai_ts, ai_ok = '', '', '', False
    if include_ai:
        prompt = mo.build_single_asset_prompt(
            price_df, ticker, longname, str(category), interval, period,
            asset_info=asset_info_block, market_status=market_status,
            signals=signals_block, seasonality=season_block,
            sector_rotation=sector_rotation, news=news_block,
            parent_index=parent_index, indicators=indicators, sys_conf=sys_conf,
            market=market, compact=compact, freetext='',
        )
        client = AiClient(username=username)
        ai_analysis = client.run_question(prompt, max_tokens=2800, groq_brevity=True)  # may raise
        ai_model    = client.model_used
        ai_ts       = dt.datetime.now().strftime('%d.%m.%Y %H:%M')
        ai_ok       = True

    # 5) Assemble the self-contained HTML document.
    labels = {k: t(f'report.{k}') for k in
              ('title', 'trend', 'keydata', 'info', 'seasonality',
               'signals', 'ai', 'generated', 'footer')}
    html_doc = ar.build_asset_report_html(
        ticker=ticker, name=longname, interval=interval, period=period,
        generated_ts=dt.datetime.now().strftime('%d.%m.%Y %H:%M'),
        market=market, rate_context=rate_context, context_text=context_text,
        news_items=news_items or [],
        trend_fig=(t_chart.fig if getattr(t_chart, 'fig', None) else None),
        keydata_text=asset_info_block, info_text=info_text or '',
        season_fig=season_fig, season_text=season_block or '',
        signals_text=signals_block or '',
        ai_analysis=ai_analysis, ai_ts=ai_ts, ai_model=ai_model,
        labels=labels,
    )
    return {'html': html_doc, 'name': longname, 'short': (_val('shortName') or longname),
            'ai_model': ai_model, 'ai_ts': ai_ts, 'ai_ok': ai_ok}


def _sanitize(s: str) -> str:
    """Filesystem-safe token: drop '^', replace path/reserved chars, trim dots/spaces."""
    import re
    s = str(s).replace('^', '')
    s = re.sub(r'[\\/:*?"<>|=]+', '_', s).strip().strip('. ')
    return s or 'asset'


def report_filename(ticker: str, short: str) -> str:
    """Export name: {ticker}-{shortName}-{YYYY-MM-DD}.html."""
    return f"{_sanitize(ticker)}-{_sanitize(short)}-{dt.datetime.now():%Y-%m-%d}.html"


# ── UI ───────────────────────────────────────────────────────────────────────

def render_batch_report_ui(region, tickers, sys_conf, username: str,
                           interval: str, period: str, overlays, oszilators) -> None:
    """Panel in the Asset Summary page: generate one HTML report per given ticker.

    Sequential, resumable across AI rate limits. Gated on FEATURE_STRATEGY_ENGINE
    (same license as the single-asset AI analysis tab).
    """
    if not has_feature(FEATURE_STRATEGY_ENGINE):
        return
    tickers = [str(x).strip() for x in (tickers or []) if str(x).strip()]
    if not tickers:
        return

    exp = region.expander(t('summary.batch_header'), expanded=False)
    exp.caption(t('summary.batch_hint', n=len(tickers)))

    job = st.session_state.get(_JOB_KEY)
    # Reset a finished/foreign job when the ticker set changed.
    if job and job.get('tickers') != tickers and not job.get('running'):
        job = None

    c1, c2 = exp.columns(2)
    include_ai = c1.toggle(t('summary.batch_include_ai'), value=True, key='_batch_ai')
    compact    = c2.toggle(t('summary.batch_compact'), value=True, key='_batch_compact',
                           help=t('asset_ai.compact_help'))

    paused = bool(job and job.get('paused'))
    start_label = t('summary.batch_resume') if paused else t('summary.batch_button')
    start = exp.button(start_label, type='primary', key='_batch_start')

    if start:
        if not job or (job.get('done')):
            job = {'tickers': tickers, 'idx': 0, 'results': {},
                   'running': True, 'paused': False, 'done': False}
        job['running'] = True
        job['paused']  = False
        st.session_state[_JOB_KEY] = job

        prog   = exp.progress(job['idx'] / len(tickers))
        status = exp.empty()
        total  = len(tickers)
        while job['idx'] < total:
            i  = job['idx']
            tk = tickers[i]
            status.markdown(t('summary.batch_progress', i=i + 1, n=total, ticker=tk))
            try:
                res = build_report_html_for_ticker(
                    tk, sys_conf, username, interval, period, overlays, oszilators,
                    compact=compact, include_ai=include_ai,
                )
                job['results'][tk] = {'status': 'ok', **res}
                job['idx'] = i + 1
            except (AiRateLimitError, AiProviderError) as exc:
                # Hard AI limit → pause WITHOUT advancing, so resume retries this ticker.
                job['results'][tk] = {'status': 'ratelimit', 'error': str(exc)}
                job['running'] = False
                job['paused']  = True
                st.session_state[_JOB_KEY] = job
                status.warning(t('summary.batch_ratelimit', ticker=tk, error=str(exc)))
                break
            except Exception as exc:
                logger.exception("batch report failed for %s", tk)
                job['results'][tk] = {'status': 'error', 'error': str(exc)}
                job['idx'] = i + 1
            prog.progress(job['idx'] / total)
            st.session_state[_JOB_KEY] = job
        else:
            job['running'] = False
            job['done']    = True
            st.session_state[_JOB_KEY] = job
            status.success(t('summary.batch_done',
                            ok=sum(1 for r in job['results'].values() if r.get('status') == 'ok'),
                            n=total))

    # ── Results / downloads (persist across reruns) ──
    job = st.session_state.get(_JOB_KEY)
    if not job or not job.get('results'):
        return

    ok_items = [(tk, r) for tk, r in job['results'].items() if r.get('status') == 'ok']
    if ok_items:
        # One ZIP with all finished reports.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for tk, r in ok_items:
                zf.writestr(report_filename(tk, r.get('short', '')), r['html'])
        exp.download_button(
            t('summary.batch_download_all', n=len(ok_items)),
            data=buf.getvalue(), file_name='asset_reports.zip',
            mime='application/zip', key='_batch_zip',
        )

    # Per-ticker status + individual download.
    for tk in job['tickers']:
        r = job['results'].get(tk)
        if not r:
            continue
        row = exp.container()
        if r.get('status') == 'ok':
            cols = row.columns([0.6, 0.4])
            cols[0].markdown(f"✅ **{tk}** — {r.get('name', '')}"
                             + (f"  ·  `{r.get('ai_model')}`" if r.get('ai_model') else ''))
            cols[1].download_button(
                t('summary.batch_download_one'),
                data=r['html'].encode('utf-8'),
                file_name=report_filename(tk, r.get('short', '')),
                mime='text/html', key=f'_batch_dl_{tk}',
            )
        elif r.get('status') == 'ratelimit':
            row.markdown(f"⏸️ **{tk}** — {t('summary.batch_pending')}")
        else:
            row.markdown(f"❌ **{tk}** — {r.get('error', '')[:120]}")
