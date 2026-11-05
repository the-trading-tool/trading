"""Full-screen "Loading" overlay -- and the per-user switch to turn it off.

Two forms share one look: SPINNER_CSS restyles every st.spinner into a
full-viewport overlay (injected once per page by asset_analyzer), and
tab_overlay() is the same overlay as HTML for st.empty() placeholders. Both
follow the config value 'loading_overlay' (default on); off = Streamlit's
small inline spinner and a one-line note.
"""
import streamlit as st

_STATE_KEY = '_loading_overlay'
SPINNER_CSS = """
            <style>
                    /* Large full-screen spinner overlay */
                    @keyframes tt-spin {
                        to { transform: rotate(360deg); }
                    }
                    div[data-testid="stSpinner"] {
                        position: fixed !important;
                        top: 0 !important;
                        left: 0 !important;
                        right: 0 !important;
                        bottom: 0 !important;
                        width: 100vw !important;
                        height: 100vh !important;
                        margin: 0 !important;
                        background: rgba(10, 12, 20, 0.55) !important;
                        z-index: 99999 !important;
                        display: flex !important;
                        align-items: center !important;
                        justify-content: center !important;
                    }
                    div[data-testid="stSpinner"] > div {
                        display: flex !important;
                        flex-direction: column !important;
                        align-items: center !important;
                        gap: 2rem !important;
                        padding: 3rem 4rem !important;
                        border-radius: 1.5rem !important;
                        background: rgba(255, 255, 255, 0.08) !important;
                        backdrop-filter: blur(12px) !important;
                        -webkit-backdrop-filter: blur(12px) !important;
                    }
                    div[data-testid="stSpinner"] svg {
                        display: none !important;
                    }
                    div[data-testid="stSpinner"] > div::before {
                        content: '' !important;
                        display: block !important;
                        width: 80px !important;
                        height: 80px !important;
                        border-radius: 50% !important;
                        border: 6px solid rgba(255, 255, 255, 0.15) !important;
                        border-top-color: #5b9cf6 !important;
                        animation: tt-spin 0.85s linear infinite !important;
                        flex-shrink: 0 !important;
                    }
                    div[data-testid="stSpinner"] p {
                        color: #dce4ef !important;
                        font-size: 1.4rem !important;
                        font-weight: 500 !important;
                        margin: 0 !important;
                        letter-spacing: 0.04em !important;
                    }
                    div[data-testid="stSpinner"] p::before {
                        content: 'Loading: ' !important;
                        opacity: 0.5 !important;
                        font-size: 0.8em !important;
                        font-weight: 400 !important;
                        letter-spacing: 0.1em !important;
                        text-transform: uppercase !important;
                    }
            </style>
"""

def enabled() -> bool:
    """Overlay on for this session (set by asset_analyzer from the user's config)."""
    return bool(st.session_state.get(_STATE_KEY, True))


def read_setting(username) -> bool:
    """The user's 'loading_overlay' value (default on), stored for enabled()."""
    on = True
    if username:
        try:
            from tradinglib import system_config as sysconf
            value = sysconf.SystemConfig(username=username, bare_mode=True).get_value(
                'loading_overlay', True)
            on = str(value).strip().lower() not in ('false', '0', 'no', 'none', '')
        except Exception:
            on = True
    st.session_state[_STATE_KEY] = on
    return on


def tab_overlay(label: str) -> str:
    """Full-viewport loading overlay for use with st.empty() outside tab context.

    Must be placed in a placeholder that lives OUTSIDE any `with tab_X:` block so
    that `position:fixed` is resolved against the real viewport, not the tab-panel's
    transform stacking context (which would make the overlay small and mis-positioned).
    Switched off: a small inline note instead.
    """
    if not enabled():
        return f'<p style="opacity:0.7;margin:0.25rem 0;">⏳ {label} …</p>'
    return (
        '<style>@keyframes _tt_spin{to{transform:rotate(360deg)}}</style>'
        '<div style="position:fixed;top:0;left:0;right:0;bottom:0;'
        'width:100vw;height:100vh;background:rgba(10,12,20,0.55);'
        'z-index:99999;display:flex;align-items:center;justify-content:center;">'
        '<div style="display:flex;flex-direction:column;align-items:center;'
        'gap:2rem;padding:3rem 4rem;border-radius:1.5rem;'
        'background:rgba(255,255,255,0.08);backdrop-filter:blur(12px);'
        '-webkit-backdrop-filter:blur(12px);">'
        '<div style="width:80px;height:80px;border-radius:50%;flex-shrink:0;'
        'border:6px solid rgba(255,255,255,0.15);border-top-color:#5b9cf6;'
        'animation:_tt_spin 0.85s linear infinite;"></div>'
        f'<p style="color:#dce4ef;font-size:1.4rem;font-weight:500;margin:0;'
        f'letter-spacing:0.04em;">'
        f'<span style="opacity:0.5;font-size:0.8em;font-weight:400;'
        f'letter-spacing:0.1em;text-transform:uppercase;">Loading: </span>'
        f'{label} …</p>'
        '</div></div>'
    )