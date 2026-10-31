import os as _os

# Overrides the hardcoded light-mode styles inside the HELP/*.html files.
# Injected AFTER the page's own <style> block would load, and uses !important,
# so it wins regardless of source order. Toggled deterministically from Python
# via set_dark_mode() — no browser-side theme guessing.
_DARK_MODE_CSS = """<style>
body { background: transparent !important; color: #d6dbe4 !important; }
h1 { color: #6fb1ff !important; border-bottom-color: #6fb1ff !important; }
h2 { color: #6fb1ff !important; }
h3 { color: #b8bfc9 !important; }
a  { color: #6fb1ff !important; }
b, strong { color: #f0f3f7 !important; }
code, pre { background: #1c2433 !important; border-color: #3a4560 !important; color: #d8e2f0 !important; }
table { color: #d6dbe4 !important; }
th { background: #1c3252 !important; color: #a8c8f0 !important; border-color: #3a4560 !important; }
td { border-color: #3a4560 !important; background: transparent !important; }
tr:nth-child(even) td { background: #161d2b !important; }
.info { background: #12291b !important; border-left-color: #34a853 !important; color: #c8e6c9 !important; }
.warn { background: #2e2612 !important; border-left-color: #fbbc04 !important; color: #f5e9c0 !important; }
.hero { background: linear-gradient(135deg,#14263f 0%,#0f1c30 100%) !important; border-color: #2a4a7f !important; color: #d6dbe4 !important; }
.step { background: #151c2a !important; border-color: #2e3c58 !important; color: #d6dbe4 !important; }
.step b { color: #6fb1ff !important; }
.tag { background: #1c2c4a !important; border-color: #4a6aba !important; color: #a8c8f0 !important; }
</style>"""

# Module-level flag, set once per render pass (e.g. in system_config.render_help)
# before the load_help calls. Defaults to dark since that is the app default.
DARK_MODE = True


def set_dark_mode(enabled: bool) -> None:
    """Set whether load_help() prepends the dark-mode override CSS."""
    global DARK_MODE
    DARK_MODE = bool(enabled)


def load_help(module_name: str, lang: str | None = None) -> str:
    """Load a help HTML file from the HELP/ folder next to the Trading root.

    Language-aware: prefers ``<module>_<lang>.html`` (e.g. market_map_en.html) and
    falls back to the base ``<module>.html`` when no localised variant exists. The
    base files are German; add ``_en.html`` siblings to provide English versions.
    ``lang`` defaults to the active i18n language. Explicit calls that already carry
    a language suffix (e.g. ``scalable_de``) still resolve via the fallback.
    """
    if lang is None:
        try:
            from tradinglib import i18n
            lang = i18n.current_language()
        except Exception:
            lang = None

    _help_dir = _os.path.join(_os.path.dirname(__file__), '..', 'HELP')
    candidates = []
    if lang:
        candidates.append(f'{module_name}_{lang}.html')
    candidates.append(f'{module_name}.html')

    content = None
    for _name in candidates:
        try:
            with open(_os.path.join(_help_dir, _name), encoding='utf-8') as _f:
                content = _f.read()
            break
        except FileNotFoundError:
            continue
    if content is None:
        return f'<p style="color:#888">Keine Hilfe verfügbar für: <code>{module_name}</code></p>'
    return (_DARK_MODE_CSS + content) if DARK_MODE else content
