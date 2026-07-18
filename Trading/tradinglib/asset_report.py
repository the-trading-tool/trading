"""
asset_report.py — Self-contained HTML report for a single asset.

Bundles the trend chart, key figures, business info, seasonality and the AI
analysis into ONE self-contained HTML document (Plotly.js inlined once) that the
user downloads and prints to PDF via the browser (Ctrl+P → "Save as PDF").

No server-side PDF engine / Kaleido is used — the charts stay the real interactive
Plotly figures, which also print cleanly. Dependency-free: a tiny built-in
Markdown→HTML converter renders the AI text (no `markdown` package required).
"""
import html
import logging
import re

import plotly.graph_objects as go
import plotly.io as pio

logger = logging.getLogger(__name__)

_PLOTLY_CONFIG = {'displayModeBar': False, 'responsive': True}


# ── Plotly figure → embeddable HTML ──────────────────────────────────────────

def _fig_to_html(fig, include_js: bool) -> str:
    """Render a Plotly figure as an embeddable, full-width <div>. plotly.js is inlined
    only on the first call (include_js=True); later figures reuse the loaded lib.

    The on-screen trend figure carries a fixed pixel width from the Streamlit chart
    context, which would render tiny/compressed in the report. We render a COPY with
    width cleared + autosize on (never mutating the live figure) so it fills the page.
    """
    if fig is None:
        return ''
    try:
        f = go.Figure(fig)                       # copy — do not touch the on-screen fig
        f.update_layout(width=None, autosize=True)
        return pio.to_html(
            f,
            include_plotlyjs=('inline' if include_js else False),
            full_html=False,
            config=_PLOTLY_CONFIG,
            default_width='100%',
            default_height='520px',
        )
    except Exception:
        logger.warning("asset_report: figure render failed", exc_info=True)
        return ''


# ── Minimal Markdown → HTML (dependency-free) ────────────────────────────────

def _inline_md(text: str) -> str:
    """Inline markdown on an already HTML-escaped string: bold, italic, code."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)', r'<em>\1</em>', text)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    return text


def _md_to_html(md: str) -> str:
    """Very small Markdown → HTML converter for the AI analysis text.

    Handles: #/##/### headings, unordered (-, *, •) and ordered (1.) lists,
    GitHub-style pipe tables, horizontal rules, blank-line paragraphs and inline
    bold/italic/code. Anything else falls through as an escaped paragraph.
    """
    if not md:
        return ''
    lines = md.replace('\r\n', '\n').split('\n')
    out: list[str] = []
    i = 0
    n = len(lines)

    def esc(s):
        return _inline_md(html.escape(s))

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Horizontal rule
        if re.fullmatch(r'(-{3,}|_{3,}|\*{3,})', stripped):
            out.append('<hr>')
            i += 1
            continue

        # Heading
        m = re.match(r'(#{1,6})\s+(.*)', stripped)
        if m:
            lvl = min(len(m.group(1)) + 1, 6)   # shift down one level under the section
            out.append(f'<h{lvl}>{esc(m.group(2))}</h{lvl}>')
            i += 1
            continue

        # Pipe table: header row followed by a separator row of ---/:--
        if '|' in line and i + 1 < n and re.match(r'^\s*\|?\s*:?-{2,}', lines[i + 1]) \
                and '|' in lines[i + 1]:
            def _cells(row):
                row = row.strip().strip('|')
                return [c.strip() for c in row.split('|')]
            header = _cells(line)
            i += 2
            body = []
            while i < n and '|' in lines[i] and lines[i].strip():
                body.append(_cells(lines[i]))
                i += 1
            thead = ''.join(f'<th>{esc(c)}</th>' for c in header)
            rows = ''
            for r in body:
                cells = ''.join(f'<td>{esc(c)}</td>' for c in r)
                rows += f'<tr>{cells}</tr>'
            out.append(f'<table class="md"><thead><tr>{thead}</tr></thead><tbody>{rows}</tbody></table>')
            continue

        # Unordered list
        if re.match(r'^\s*[-*•]\s+', line):
            items = []
            while i < n and re.match(r'^\s*[-*•]\s+', lines[i]):
                item = re.sub(r'^\s*[-*•]\s+', '', lines[i])
                items.append(f'<li>{esc(item)}</li>')
                i += 1
            out.append('<ul>' + ''.join(items) + '</ul>')
            continue

        # Ordered list
        if re.match(r'^\s*\d+[.)]\s+', line):
            items = []
            while i < n and re.match(r'^\s*\d+[.)]\s+', lines[i]):
                item = re.sub(r'^\s*\d+[.)]\s+', '', lines[i])
                items.append(f'<li>{esc(item)}</li>')
                i += 1
            out.append('<ol>' + ''.join(items) + '</ol>')
            continue

        # Paragraph (gather consecutive non-blank, non-special lines)
        para = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r'^\s*(#{1,6}\s|[-*•]\s|\d+[.)]\s|\|)', lines[i]) \
                and not re.fullmatch(r'\s*(-{3,}|_{3,}|\*{3,})\s*', lines[i]):
            para.append(lines[i])
            i += 1
        out.append('<p>' + '<br>'.join(esc(p) for p in para) + '</p>')

    return '\n'.join(out)


# ── Key-data lines ("Section: a · b · c") → HTML table ───────────────────────

def _keydata_to_html(text: str) -> str:
    """Render the fundamentals block (lines 'Section: item · item') as a 2-col table."""
    if not text or not text.strip():
        return ''
    rows = ''
    for line in text.strip().split('\n'):
        if ':' in line:
            label, _, val = line.partition(':')
            rows += (f'<tr><th class="k">{html.escape(label.strip())}</th>'
                     f'<td>{html.escape(val.strip())}</td></tr>')
        else:
            rows += f'<tr><td colspan="2">{html.escape(line.strip())}</td></tr>'
    return f'<table class="kv">{rows}</table>' if rows else ''


# ── Full document ────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
       color: #1a1f2b; margin: 0; padding: 28px 34px; line-height: 1.5; }
h1 { font-size: 24px; margin: 0 0 2px; }
.sub { color: #5b6472; font-size: 13px; margin-bottom: 18px; }
section { margin: 22px 0; padding-top: 6px; }
section > h2 { font-size: 17px; color: #2a3446; border-bottom: 2px solid #e3e7ee;
       padding-bottom: 5px; margin: 0 0 12px; }
table.kv { border-collapse: collapse; width: 100%; font-size: 13px; }
table.kv th.k { text-align: left; width: 190px; color: #46506a; font-weight: 600;
       vertical-align: top; padding: 4px 10px 4px 0; white-space: nowrap; }
table.kv td { padding: 4px 0; }
table.kv tr + tr th, table.kv tr + tr td { border-top: 1px solid #f0f2f6; }
table.md { border-collapse: collapse; width: 100%; font-size: 13px; margin: 8px 0; }
table.md th, table.md td { border: 1px solid #dde2ea; padding: 5px 8px; text-align: left; }
table.md th { background: #f4f6fa; }
.info { font-size: 13.5px; color: #2a3140; text-align: justify; }
.ai h2, .ai h3, .ai h4 { color: #2a3446; margin: 14px 0 6px; }
.ai p { margin: 8px 0; }
.ai ul, .ai ol { margin: 6px 0 6px 22px; }
.ai code { background: #f2f4f8; padding: 1px 4px; border-radius: 3px; font-size: 12px; }
pre.mono { white-space: pre-wrap; font-family: Consolas, monospace; font-size: 12px;
       background: #f6f8fb; padding: 10px 12px; border-radius: 6px; }
.foot { margin-top: 26px; padding-top: 10px; border-top: 1px solid #e3e7ee;
       color: #8b93a3; font-size: 11px; }
@media print {
  body { padding: 0; }
  section { page-break-inside: avoid; }
  .no-print { display: none; }
}
"""


def build_asset_report_html(
    *, ticker: str, name: str = '', interval: str = '', period: str = '',
    generated_ts: str = '',
    trend_fig=None, keydata_text: str = '', info_text: str = '',
    season_fig=None, season_text: str = '', signals_text: str = '',
    ai_analysis: str = '', ai_ts: str = '', ai_model: str = '',
    labels: dict | None = None,
) -> str:
    """Assemble the self-contained HTML report. Sections with no data are omitted.

    labels: optional dict of section titles for i18n; falls back to German defaults.
    """
    L = {
        'title':       'Asset-Report',
        'trend':       'Trend-Chart',
        'keydata':     'Kennzahlen',
        'info':        'Unternehmens-/Asset-Info',
        'seasonality': 'Saisonalität',
        'signals':     'Signale',
        'ai':          'KI-Analyse',
        'generated':   'Erstellt',
        'footer':      'Automatisch generierter Report — keine Anlageberatung.',
    }
    if labels:
        L.update(labels)

    head = html.escape(name or ticker)
    subparts = [html.escape(ticker)]
    if interval or period:
        subparts.append(html.escape(f"{interval}/{period}"))
    if generated_ts:
        subparts.append(f"{L['generated']}: {html.escape(generated_ts)}")

    parts = [f'<h1>{head}</h1>', f'<div class="sub">{" · ".join(subparts)}</div>']

    # 1. Trend chart (inlines plotly.js)
    trend_html = _fig_to_html(trend_fig, include_js=True)
    if trend_html:
        parts.append(f'<section><h2>{L["trend"]}</h2>{trend_html}</section>')

    # 2. Key data
    kv = _keydata_to_html(keydata_text)
    if kv:
        parts.append(f'<section><h2>{L["keydata"]}</h2>{kv}</section>')

    # 3. Info (business summary)
    if info_text and info_text.strip():
        parts.append(f'<section><h2>{L["info"]}</h2>'
                     f'<div class="info">{html.escape(info_text.strip())}</div></section>')

    # 4. Seasonality — reuses the already-loaded plotly.js, unless the trend chart
    #    was absent (then this figure must inline the library itself).
    season_html = _fig_to_html(season_fig, include_js=not bool(trend_html))
    if season_html or (season_text and season_text.strip()):
        body = season_html
        if season_text and season_text.strip():
            body += f'<pre class="mono">{html.escape(season_text.strip())}</pre>'
        parts.append(f'<section><h2>{L["seasonality"]}</h2>{body}</section>')

    # 5. Signals
    if signals_text and signals_text.strip():
        parts.append(f'<section><h2>{L["signals"]}</h2>'
                     f'<pre class="mono">{html.escape(signals_text.strip())}</pre></section>')

    # 6. AI analysis
    if ai_analysis and ai_analysis.strip():
        meta = []
        if ai_ts:
            meta.append(html.escape(ai_ts))
        if ai_model:
            meta.append(html.escape(ai_model))
        meta_line = f'<div class="sub">{" · ".join(meta)}</div>' if meta else ''
        parts.append(f'<section class="ai"><h2>{L["ai"]}</h2>{meta_line}'
                     f'{_md_to_html(ai_analysis)}</section>')

    parts.append(f'<div class="foot">{L["footer"]}</div>')

    return (
        '<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">'
        f'<title>{L["title"]} — {head}</title>'
        f'<style>{_CSS}</style></head><body>'
        + '\n'.join(parts) +
        '</body></html>'
    )
