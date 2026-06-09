"""Einmalinvestition-Simulation mit Zinseszins und Inflationsbereinigung.

Zwei Modi:
  Prognose   — konstante Raten (heutiger ^TNX + aktuelle YoY-CPI)
  Historisch — tatsächliche monatliche ^TNX- und CPI-Werte der letzten N Jahre

Datenquellen:
  ^TNX       — tagesbasierter Cache (aktueller Wert) + 24h-Cache (Historie)
  CPI FRED   — 24h-Cache; CPIAUCSL monatlich
"""
import datetime as dt
import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from tradinglib.i18n import t as _t

logger = logging.getLogger(__name__)

_FRED_CPI_URL     = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"
_CPI_CACHE_KEY    = "_compound_sim_cpi_cache"
_CPI_CACHE_TS_KEY = "_compound_sim_cpi_ts"
_CPI_CACHE_TTL_H  = 24

_TNX_CACHE_KEY    = "_compound_sim_tnx_value"
_TNX_CACHE_DATE   = "_compound_sim_tnx_date"
_TNX_HIST_KEY     = "_compound_sim_tnx_hist"
_TNX_HIST_TS_KEY  = "_compound_sim_tnx_hist_ts"

_TNX_TICKER = "^TNX"


# ─────────────────────────────────────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_tnx_yield(force: bool = False) -> Optional[float]:
    """Fetch the current ^TNX yield — day-based session-state cache."""
    today = dt.date.today()
    if not force:
        if st.session_state.get(_TNX_CACHE_DATE) == today:
            return st.session_state.get(_TNX_CACHE_KEY)
    try:
        from tradinglib import market_data as md
        df = md.download(_TNX_TICKER, period="5d", interval="1d", force_remote=True)
        if df is not None and not df.empty:
            col = "Close" if "Close" in df.columns else df.columns[-1]
            val = df[col].dropna()
            if not val.empty:
                result = float(val.iloc[-1])
                st.session_state[_TNX_CACHE_KEY]  = result
                st.session_state[_TNX_CACHE_DATE] = today
                return result
    except Exception as e:
        logger.warning("^TNX-Abruf fehlgeschlagen: %s", e)
    return st.session_state.get(_TNX_CACHE_KEY)


def _extract_close(df: pd.DataFrame) -> Optional[pd.Series]:
    """Extract the Close series from a DataFrame, handling yfinance MultiIndex (≥0.2)."""
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance liefert ('Close', '^TNX') — erste Close-Spalte nehmen
        close_cols = [c for c in df.columns if c[0] == "Close"]
        if close_cols:
            return df[close_cols[0]].dropna()
        return df.iloc[:, 0].dropna()
    col = "Close" if "Close" in df.columns else df.columns[0]
    return df[col].dropna()


def _fetch_tnx_history(years: int = 20, force: bool = False) -> Optional[pd.Series]:
    """Fetch monthly ^TNX closing yields for up to years history — 24h session-state cache.

    Returns pd.Series with month-start index and yield-in-percent values.
    """
    now = dt.datetime.utcnow()
    if not force:
        ts = st.session_state.get(_TNX_HIST_TS_KEY)
        if ts and (now - ts).total_seconds() < _CPI_CACHE_TTL_H * 3600:
            cached = st.session_state.get(_TNX_HIST_KEY)
            if cached is not None:
                return cached
    try:
        from tradinglib import market_data as md
        # Lokalen Cache nutzen (yf_^TNX.db), kein force_remote
        df = md.download(_TNX_TICKER, period="max", interval="1mo")
        series = _extract_close(df)
        if series is None or series.empty:
            raise ValueError("Keine Close-Daten im DataFrame")
        # Auf Monatsanfang normieren (tz-naiv)
        series.index = (pd.to_datetime(series.index, utc=False)
                        .normalize()
                        .to_period("M")
                        .to_timestamp())
        series.index.freq = None          # freq-Attribut entfernen → saubere Intersection
        st.session_state[_TNX_HIST_KEY]    = series
        st.session_state[_TNX_HIST_TS_KEY] = now
        return series
    except Exception as e:
        logger.warning("^TNX-Historie-Abruf fehlgeschlagen: %s", e)
        return st.session_state.get(_TNX_HIST_KEY)


def _fetch_cpi_series(force: bool = False) -> Optional[pd.Series]:
    """Fetch US CPI (CPIAUCSL) from FRED — 24h session-state cache; falls back to _cpi_fallback."""
    now = dt.datetime.utcnow()
    if not force:
        ts = st.session_state.get(_CPI_CACHE_TS_KEY)
        if ts and (now - ts).total_seconds() < _CPI_CACHE_TTL_H * 3600:
            cached = st.session_state.get(_CPI_CACHE_KEY)
            if cached is not None:
                return cached
    try:
        df = pd.read_csv(_FRED_CPI_URL, parse_dates=["DATE"], index_col="DATE")
        series = df["CPIAUCSL"].dropna()
        series.index = (pd.to_datetime(series.index, utc=False)
                        .normalize()
                        .to_period("M")
                        .to_timestamp())
        series.index.freq = None
        st.session_state[_CPI_CACHE_KEY]    = series
        st.session_state[_CPI_CACHE_TS_KEY] = now
        return series
    except Exception as e:
        logger.warning("CPI-Abruf fehlgeschlagen: %s", e)

    # Stale-Cache besser als nichts
    stale = st.session_state.get(_CPI_CACHE_KEY)
    if stale is not None:
        return stale

    # Letzter Ausweg: bekannte CPIAUCSL-Eckwerte interpolieren
    return _cpi_fallback()


def _cpi_fallback() -> pd.Series:
    """Return an interpolated CPIAUCSL series from known annual anchor values.

    Used only when FRED is unreachable and no session cache exists.
    Source: US Bureau of Labor Statistics / FRED historical annual averages.
    """
    # Jahresanfangswerte CPIAUCSL (index = Monatsdurchschnitt Jan des jeweiligen Jahres)
    anchors = {
        1990: 127.4, 1995: 152.4, 2000: 168.8, 2005: 195.3,
        2010: 218.1, 2015: 233.7, 2019: 252.9, 2020: 258.8,
        2021: 261.6, 2022: 281.1, 2023: 299.2, 2024: 309.7, 2025: 314.5,
    }
    years = sorted(anchors)
    all_months, all_vals = [], []
    for i in range(len(years) - 1):
        y0, y1 = years[i], years[i + 1]
        v0, v1 = anchors[y0], anchors[y1]
        n = (y1 - y0) * 12
        for m in range(n):
            all_months.append(pd.Timestamp(year=y0, month=1, day=1)
                              + pd.DateOffset(months=m))
            all_vals.append(v0 + (v1 - v0) * m / n)
    # letzten Ankerpunkt anhängen
    all_months.append(pd.Timestamp(year=years[-1], month=1, day=1))
    all_vals.append(anchors[years[-1]])
    s = pd.Series(all_vals, index=pd.DatetimeIndex(all_months))
    s.index = s.index.to_period("M").to_timestamp()
    s.index.freq = None
    logger.warning("CPI: nutze eingebettete Näherungswerte (FRED offline)")
    return s


def _inflation_rates(cpi: Optional[pd.Series]) -> Tuple[float, float, str]:
    """Return (yoy_rate, 10y_cagr, as_of_date_str) from a CPI Series."""
    if cpi is None or len(cpi) < 2:
        return 0.025, 0.025, "–"
    try:
        latest_val  = float(cpi.iloc[-1])
        latest_date = cpi.index[-1]
        as_of_str   = pd.Timestamp(latest_date).strftime("%b %Y")
        yoy = (latest_val / float(cpi.iloc[-13]) - 1) if len(cpi) >= 13 else (latest_val / float(cpi.iloc[0]) - 1)
        cutoff = pd.Timestamp(latest_date) - pd.DateOffset(years=10)
        window = cpi[cpi.index >= cutoff]
        if len(window) >= 2:
            n_years = (pd.Timestamp(window.index[-1]) - pd.Timestamp(window.index[0])).days / 365.25
            avg10 = (float(window.iloc[-1]) / float(window.iloc[0])) ** (1 / max(n_years, 0.5)) - 1
        else:
            avg10 = yoy
        return yoy, avg10, as_of_str
    except Exception:
        return 0.025, 0.025, "–"


# ─────────────────────────────────────────────────────────────────────────────
# Simulation engines
# ─────────────────────────────────────────────────────────────────────────────

def _build_forecast(principal: float, annual_rate: float,
                    horizon_years: int, inflation_rate: float) -> pd.DataFrame:
    """Build a constant-rate compound interest forecast with nominal and real columns."""
    months = horizon_years * 12
    r_nom  = (1 + annual_rate)    ** (1 / 12) - 1
    r_real = (1 + annual_rate)    ** (1 / 12) / (1 + inflation_rate) ** (1 / 12) - 1
    today  = dt.date.today().replace(day=1)
    dates  = [today + pd.DateOffset(months=i) for i in range(months + 1)]
    nom    = principal * (1 + r_nom)  ** np.arange(months + 1)
    real   = principal * (1 + r_real) ** np.arange(months + 1)
    return pd.DataFrame({"Datum": dates, "Nominal": nom, "Real": real,
                         "Inflation_Drain": nom - real})


def _build_historical(principal: float,
                      tnx_hist: pd.Series,
                      cpi: pd.Series,
                      years: int) -> pd.DataFrame:
    """Simulate compound growth using actual historical monthly ^TNX and CPI data.

    For each month:
      nominal[t] = nominal[t-1] * (1 + (TNX[t]/100)^(1/12) - 1)
      real[t]    = nominal[t]   / (CPI[t] / CPI[t0])
    Returns DataFrame with Datum, Nominal, Real, Inflation_Drain, and TNX columns.
    """
    # Beide Reihen auf Monatsanfang normieren (tz-naiv, freq=None)
    def _norm_idx(s: pd.Series) -> pd.Series:
        s = s.copy()
        s.index = (pd.to_datetime(s.index, utc=False)
                   .normalize()
                   .to_period("M")
                   .to_timestamp())
        s.index.freq = None
        return s

    tnx   = _norm_idx(tnx_hist if isinstance(tnx_hist, pd.Series)
                      else tnx_hist.iloc[:, 0])
    cpi_m = _norm_idx(cpi if isinstance(cpi, pd.Series) else cpi.iloc[:, 0])

    # Schnittmenge und Zeitfenster
    cutoff = pd.Timestamp.today().normalize() - pd.DateOffset(years=years)
    idx = tnx.index.intersection(cpi_m.index)
    idx = idx[idx >= cutoff]
    if len(idx) < 2:
        return pd.DataFrame()

    tnx_w = tnx.loc[idx]
    cpi_w = cpi_m.loc[idx]

    # Monatliche Nominal-Raten aus annualisiertem ^TNX
    monthly_rates = (1 + tnx_w.values / 100) ** (1 / 12) - 1

    # CPI-Deflator: jeder Monat relativ zum Startmonat
    cpi_deflator = cpi_w.values / cpi_w.values[0]

    # Simulation
    nom  = np.zeros(len(idx))
    nom[0] = principal
    for i in range(1, len(idx)):
        nom[i] = nom[i - 1] * (1 + monthly_rates[i])

    real = nom / cpi_deflator

    # Durchschnittswerte für Anzeige
    avg_rate  = float(np.mean(tnx_w.values))
    avg_infl  = float(
        (cpi_w.values[-1] / cpi_w.values[0]) ** (12 / max(len(idx) - 1, 1)) - 1
    ) * 100

    df = pd.DataFrame({
        "Datum":           idx,
        "Nominal":         nom,
        "Real":            real,
        "Inflation_Drain": nom - real,
        "TNX":             tnx_w.values,
    })
    df.attrs["avg_rate"] = avg_rate
    df.attrs["avg_infl"] = avg_infl
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Rendering helpers
# ─────────────────────────────────────────────────────────────────────────────

def _metrics_row(region, principal, df, currency="€"):
    """Render a 4-metric row: end nominal value, real value, inflation loss, and real annual rate."""
    end_nom  = df["Nominal"].iloc[-1]
    end_real = df["Real"].iloc[-1]
    gain_nom = end_nom  - principal
    gain_real = end_real - principal
    loss      = gain_nom - gain_real
    real_rate_ann = (end_real / principal) ** (12 / max(len(df) - 1, 1)) - 1

    def _f(v): return f"{currency}{v:,.0f}"

    m1, m2, m3, m4 = region.columns(4)
    m1.metric(_t("cs.metric_end_nominal"),    _f(end_nom),  _f(gain_nom))
    m2.metric(_t("cs.metric_end_real"),       _f(end_real), _f(gain_real))
    m3.metric(_t("cs.metric_inflation_loss"), _f(loss))
    m4.metric(_t("cs.metric_real_rate"),      f"{real_rate_ann * 100:.2f} %")


def _chart(region, df_main, df_compare=None,
           show_real=True, currency="€",
           label_main=None, label_compare=None):
    """Render the compound simulation Plotly chart with nominal, real, and inflation-drain fill."""
    import plotly.graph_objects as go

    fig = go.Figure()

    def _add_curves(df, nom_color, real_color, suffix=""):
        lbl_nom  = (label_main or _t("cs.series_nominal"))  + suffix
        lbl_real = (label_main or _t("cs.series_real"))     + suffix
        if suffix and label_compare:
            lbl_nom  = label_compare + f" ({_t('cs.series_nominal')})"
            lbl_real = label_compare + f" ({_t('cs.series_real')})"

        fig.add_trace(go.Scatter(
            x=df["Datum"], y=df["Nominal"],
            mode="lines", name=lbl_nom,
            line=dict(color=nom_color, width=2),
            hovertemplate=f"%{{x|%b %Y}}<br>%{{y:,.0f}} {currency}<extra></extra>",
        ))
        if show_real:
            fig.add_trace(go.Scatter(
                x=df["Datum"], y=df["Real"],
                mode="lines", name=lbl_real,
                line=dict(color=real_color, width=2, dash="dash"),
                hovertemplate=f"%{{x|%b %Y}}<br>%{{y:,.0f}} {currency}<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=pd.concat([df["Datum"], df["Datum"][::-1]]).reset_index(drop=True),
                y=pd.concat([df["Nominal"], df["Real"][::-1]]).reset_index(drop=True),
                fill="toself",
                fillcolor="rgba(255,127,14,0.10)",
                line=dict(color="rgba(0,0,0,0)"),
                name=_t("cs.series_inflation_drain") + suffix,
                hoverinfo="skip",
                showlegend=True,
            ))

    _add_curves(df_main, "#1f77b4", "#2ca02c")
    if df_compare is not None and not df_compare.empty:
        _add_curves(df_compare, "#aec7e8", "#98df8a", suffix=" *")

    principal = float(df_main["Nominal"].iloc[0])
    fig.add_hline(y=principal, line_dash="dot", line_color="gray",
                  annotation_text=_t("cs.annotation_principal"),
                  annotation_position="bottom right")

    fig.update_layout(
        xaxis_title=_t("cs.xaxis"),
        yaxis_title=_t("cs.yaxis", currency=currency),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        height=460,
        margin=dict(l=10, r=10, t=30, b=10),
    )
    region.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Main render
# ─────────────────────────────────────────────────────────────────────────────

def render_compound_simulation(region=None) -> None:
    """Render the full compound simulation page with forecast and historical tabs."""
    if region is None:
        region = st

    region.title(_t("cs.title"))

    col_cap, col_ref = region.columns([6, 1])
    col_cap.caption(_t("cs.caption"))
    force_refresh = col_ref.button("🔄", key="_cs_refresh", help=_t("cs.refresh_help"))
    if force_refresh:
        for k in (_CPI_CACHE_KEY, _CPI_CACHE_TS_KEY,
                  _TNX_CACHE_KEY, _TNX_CACHE_DATE,
                  _TNX_HIST_KEY,  _TNX_HIST_TS_KEY):
            st.session_state.pop(k, None)

    # ── Daten laden ───────────────────────────────────────────────────────
    with st.spinner(_t("cs.loading_inflation")):
        cpi = _fetch_cpi_series(force=force_refresh)
    with st.spinner(_t("cs.loading_tnx")):
        tnx_value = _fetch_tnx_yield(force=force_refresh)

    yoy_infl, avg10_infl, cpi_as_of = _inflation_rates(cpi)
    tnx_date = st.session_state.get(_TNX_CACHE_DATE)
    tnx_date_str  = tnx_date.strftime("%d.%m.%Y") if tnx_date else "–"
    cpi_src_label = (
        _t("cs.cpi_source_fred", as_of=cpi_as_of)
        if cpi is not None else _t("cs.cpi_source_fallback")
    )
    default_rate = (tnx_value / 100.0) if tnx_value else 0.045
    currency = "€"

    # ── Modus-Tabs ────────────────────────────────────────────────────────
    tab_forecast, tab_hist = region.tabs([
        _t("cs.tab_forecast"), _t("cs.tab_historical"),
    ])

    # ════════════════════════════════════════════════════════════════════════
    # TAB 1 — Prognose (konstante Raten)
    # ════════════════════════════════════════════════════════════════════════
    with tab_forecast:
        st.subheader(_t("cs.params_header"))
        c1, c2, c3 = st.columns(3)
        with c1:
            principal = st.number_input(
                _t("cs.principal"), min_value=100.0, max_value=10_000_000.0,
                value=10_000.0, step=500.0, format="%.0f", key="fc_principal",
            )
        with c2:
            rate_pct = st.number_input(
                _t("cs.annual_rate"), min_value=0.0, max_value=30.0,
                value=round(default_rate * 100, 2), step=0.1, format="%.2f",
                key="fc_rate",
                help=_t("cs.annual_rate_help", ticker=_TNX_TICKER,
                        value=tnx_value or 0.0, as_of=tnx_date_str),
            )
        with c3:
            horizon = st.number_input(
                _t("cs.horizon"), min_value=1, max_value=50,
                value=10, step=1, key="fc_horizon",
            )

        ca, cb, cc = st.columns([2, 1, 1])
        with ca:
            infl_pct = st.number_input(
                _t("cs.inflation_rate"), min_value=0.0, max_value=20.0,
                value=round(yoy_infl * 100, 2), step=0.1, format="%.2f",
                key="fc_infl",
                help=_t("cs.inflation_help", source=cpi_src_label,
                        yoy=round(yoy_infl * 100, 2),
                        avg10=round(avg10_infl * 100, 2)),
            )
        with cb:
            st.write(""); st.write("")
            show_real = st.toggle(_t("cs.show_real"), value=True, key="fc_show_real")
        with cc:
            st.write(""); st.write("")
            if st.toggle(_t("cs.use_avg10"), value=False, key="fc_avg10",
                         help=_t("cs.use_avg10_help", avg10=round(avg10_infl * 100, 2))):
                infl_pct = round(avg10_infl * 100, 2)

        df_fc = _build_forecast(principal, rate_pct / 100, horizon, infl_pct / 100)

        st.subheader(_t("cs.metrics_header", horizon=horizon))
        _metrics_row(st, principal, df_fc, currency)

        st.subheader(_t("cs.chart_header"))
        _chart(st, df_fc, show_real=show_real, currency=currency)
        st.caption(_t("cs.source_note", source=cpi_src_label,
                      tnx=_TNX_TICKER, tnx_date=tnx_date_str))

        with st.expander(_t("cs.raw_data_expander")):
            d = df_fc.copy()
            d["Datum"] = d["Datum"].dt.strftime("%Y-%m")
            d = d.set_index("Datum")
            d.columns = [_t("cs.series_nominal"), _t("cs.series_real"),
                         _t("cs.series_inflation_drain")]
            st.dataframe(d.style.format("{:,.2f}"), use_container_width=True)

    # ════════════════════════════════════════════════════════════════════════
    # TAB 2 — Historisch (variable monatliche Raten)
    # ════════════════════════════════════════════════════════════════════════
    with tab_hist:
        st.caption(_t("cs.hist_caption"))

        ch1, ch2, ch3 = st.columns(3)
        with ch1:
            h_principal = st.number_input(
                _t("cs.principal"), min_value=100.0, max_value=10_000_000.0,
                value=10_000.0, step=500.0, format="%.0f", key="hs_principal",
            )
        with ch2:
            h_years = st.selectbox(
                _t("cs.hist_years"), options=[5, 10, 15, 20],
                index=1, key="hs_years",
            )
        with ch3:
            st.write(""); st.write("")
            h_show_real = st.toggle(_t("cs.show_real"), value=True, key="hs_show_real")

        show_compare = st.toggle(_t("cs.hist_show_forecast"), value=True,
                                 key="hs_compare",
                                 help=_t("cs.hist_show_forecast_help"))

        # Historische ^TNX-Daten laden
        with st.spinner(_t("cs.loading_tnx_hist")):
            tnx_hist = _fetch_tnx_history(years=h_years + 1, force=force_refresh)

        # ── Diagnose bei fehlenden Daten ──────────────────────────────────
        _diag_ok = True
        if tnx_hist is None:
            st.error(f"^TNX-Daten konnten nicht geladen werden "
                     f"(weder lokal noch remote). Bitte `yf_^TNX.db` prüfen.")
            _diag_ok = False
        if cpi is None:
            st.error("CPI-Daten nicht verfügbar (FRED nicht erreichbar und kein Cache). "
                     "Bitte Internetverbindung prüfen oder 🔄 drücken.")
            _diag_ok = False

        if _diag_ok:
            df_hist = _build_historical(h_principal, tnx_hist, cpi, h_years)
            if df_hist.empty:
                # Intersection-Diagnose
                try:
                    import pandas as _pd
                    _tnx_first = tnx_hist.index[0] if hasattr(tnx_hist, 'index') else '?'
                    _tnx_last  = tnx_hist.index[-1] if hasattr(tnx_hist, 'index') else '?'
                    _cpi_first = cpi.index[0]
                    _cpi_last  = cpi.index[-1]
                    _cutoff    = _pd.Timestamp.today().normalize() - _pd.DateOffset(years=h_years)
                    st.error(
                        f"Keine überlappenden Daten im gewählten Zeitraum.\n\n"
                        f"^TNX verfügbar: {_tnx_first} – {_tnx_last}\n\n"
                        f"CPI verfügbar: {_cpi_first} – {_cpi_last}\n\n"
                        f"Gesuchter Schnitt ab: {_cutoff.date()}"
                    )
                except Exception:
                    st.error(_t("cs.hist_no_data"))
            else:
                avg_rate = df_hist.attrs.get("avg_rate", 0.0)
                avg_infl = df_hist.attrs.get("avg_infl", 0.0)
                start_date = pd.Timestamp(df_hist["Datum"].iloc[0])
                end_date   = pd.Timestamp(df_hist["Datum"].iloc[-1])

                # Info-Zeile
                st.info(_t("cs.hist_info",
                           start=start_date.strftime("%b %Y"),
                           end=end_date.strftime("%b %Y"),
                           avg_rate=round(avg_rate, 2),
                           avg_infl=round(avg_infl, 2)))

                # Vergleichs-Prognose: konstante Raten ab Startdatum
                df_compare = None
                if show_compare:
                    fc_months = len(df_hist) - 1
                    r_nom  = (1 + avg_rate / 100) ** (1 / 12) - 1
                    r_real = (1 + avg_rate / 100) ** (1 / 12) / (1 + avg_infl / 100) ** (1 / 12) - 1
                    nom_fc   = h_principal * (1 + r_nom)  ** np.arange(fc_months + 1)
                    real_fc  = h_principal * (1 + r_real) ** np.arange(fc_months + 1)
                    df_compare = pd.DataFrame({
                        "Datum":           df_hist["Datum"].values,
                        "Nominal":         nom_fc,
                        "Real":            real_fc,
                        "Inflation_Drain": nom_fc - real_fc,
                    })

                # Metriken
                actual_years = (end_date - start_date).days / 365.25
                st.subheader(_t("cs.metrics_header", horizon=round(actual_years, 1)))
                _metrics_row(st, h_principal, df_hist, currency)

                # TNX-Verlauf
                with st.expander(_t("cs.hist_rate_expander"), expanded=False):
                    import plotly.graph_objects as go
                    fig_tnx = go.Figure()
                    fig_tnx.add_trace(go.Scatter(
                        x=df_hist["Datum"], y=df_hist["TNX"],
                        mode="lines", name=_TNX_TICKER,
                        line=dict(color="#e377c2", width=1.5),
                        hovertemplate="%{x|%b %Y}<br>%{y:.2f} %<extra></extra>",
                        fill="tozeroy", fillcolor="rgba(227,119,194,0.15)",
                    ))
                    fig_tnx.add_hline(y=avg_rate, line_dash="dot", line_color="gray",
                                      annotation_text=f"Ø {avg_rate:.2f} %",
                                      annotation_position="right")
                    fig_tnx.update_layout(
                        height=200,
                        yaxis_title="% p.a.",
                        margin=dict(l=10, r=10, t=10, b=10),
                        hovermode="x unified",
                    )
                    st.plotly_chart(fig_tnx, use_container_width=True)

                # Hauptchart
                st.subheader(_t("cs.chart_header"))
                _chart(st, df_hist, df_compare=df_compare,
                       show_real=h_show_real, currency=currency,
                       label_main=_t("cs.hist_label_actual"),
                       label_compare=_t("cs.hist_label_forecast"))

                st.caption(_t("cs.source_note", source=cpi_src_label,
                              tnx=_TNX_TICKER, tnx_date=tnx_date_str))

                with st.expander(_t("cs.raw_data_expander")):
                    d = df_hist[["Datum", "Nominal", "Real",
                                 "Inflation_Drain", "TNX"]].copy()
                    d["Datum"] = pd.to_datetime(d["Datum"]).dt.strftime("%Y-%m")
                    d = d.set_index("Datum")
                    d.columns = [
                        _t("cs.series_nominal"), _t("cs.series_real"),
                        _t("cs.series_inflation_drain"), f"{_TNX_TICKER} %",
                    ]
                    st.dataframe(d.style.format("{:,.2f}"), use_container_width=True)
