"""Sector Rotation backend: data fetching and all indicator calculations.

Computes Mansfield RSC, Chaikin Money Flow, OBV trend, and the JdK RS-Ratio /
RS-Momentum coordinates used for the Relative Rotation Graph (RRG).
"""
import logging
import numpy as np
import pandas as pd

from tradinglib import market_data as md

logger = logging.getLogger(__name__)

# ─── Sector universe ──────────────────────────────────────────────────────────

US_SECTOR_ETFS: dict[str, str] = {
    "Technology":        "XLK",
    "Financials":        "XLF",
    "Energy":            "XLE",
    "Health Care":       "XLV",
    "Consumer Discret.": "XLY",
    "Consumer Staples":  "XLP",
    "Industrials":       "XLI",
    "Materials":         "XLB",
    "Real Estate":       "XLRE",
    "Communication":     "XLC",
    "Utilities":         "XLU",
}

DEFAULT_BENCHMARK = "SPY"
DEFAULT_PERIOD = "2y"

# Approximate S&P 500 sector weights — used for treemap tile sizing
SECTOR_WEIGHTS: dict[str, float] = {
    "Technology":        31.0,
    "Financials":        13.5,
    "Health Care":       11.5,
    "Consumer Discret.": 10.5,
    "Industrials":        8.5,
    "Communication":      8.5,
    "Consumer Staples":   5.5,
    "Energy":             4.0,
    "Utilities":          2.5,
    "Real Estate":        2.5,
    "Materials":          2.0,
}

# ─── European universe (iShares STOXX Europe 600 sub-index ETFs, XETRA/EUR) ──

EU_SECTOR_ETFS: dict[str, str] = {
    "Technology":      "EXHF.DE",   # iShares STOXX Europe 600 Technology
    "Banks":           "EXH2.DE",   # iShares STOXX Europe 600 Banks
    "Health Care":     "EXH8.DE",   # iShares STOXX Europe 600 Health Care
    "Industrials":     "EXH9.DE",   # iShares STOXX Europe 600 Ind. Goods
    "Oil & Gas":       "EXHC.DE",   # iShares STOXX Europe 600 Oil & Gas
    "Food & Beverage": "EXH7.DE",   # iShares STOXX Europe 600 Food & Bev.
    "Fin. Services":   "EXH6.DE",   # iShares STOXX Europe 600 Fin. Services
    "Insurance":       "EXHA.DE",   # iShares STOXX Europe 600 Insurance
    "Basic Resources": "EXH3.DE",   # iShares STOXX Europe 600 Basic Res.
    "Chemicals":       "EXH4.DE",   # iShares STOXX Europe 600 Chemicals
    "Utilities":       "EXHI.DE",   # iShares STOXX Europe 600 Utilities
    "Automobiles":     "EXH1.DE",   # iShares STOXX Europe 600 Auto & Parts
    "Telecom":         "EXHG.DE",   # iShares STOXX Europe 600 Telecom
}

EU_BENCHMARK = "EXSA.DE"           # iShares Core STOXX Europe 600 UCITS ETF

# Approximate STOXX 600 sector weights
EU_SECTOR_WEIGHTS: dict[str, float] = {
    "Industrials":     17.0,
    "Health Care":     16.0,
    "Fin. Services":    9.0,
    "Banks":            8.5,
    "Consumer Discr.":  8.0,
    "Technology":       7.5,
    "Food & Beverage":  7.0,
    "Oil & Gas":        5.5,
    "Insurance":        5.0,
    "Utilities":        4.5,
    "Basic Resources":  4.0,
    "Chemicals":        3.5,
    "Automobiles":      3.5,
    "Telecom":          3.0,
}

# ─── Emerging Markets universe (country ETFs, USD) ────────────────────────────

EM_COUNTRY_ETFS: dict[str, str] = {
    "China":       "MCHI",   # iShares MSCI China
    "India":       "INDA",   # iShares MSCI India
    "Taiwan":      "EWT",    # iShares MSCI Taiwan
    "S. Korea":    "EWY",    # iShares MSCI South Korea
    "Brazil":      "EWZ",    # iShares MSCI Brazil
    "S. Africa":   "EZA",    # iShares MSCI South Africa
    "Mexico":      "EWW",    # iShares MSCI Mexico
    "Indonesia":   "EIDO",   # iShares MSCI Indonesia
    "Thailand":    "THD",    # iShares MSCI Thailand
    "Vietnam":     "VNM",    # VanEck Vietnam ETF
}

EM_BENCHMARK = "EEM"               # iShares MSCI Emerging Markets

# Approximate MSCI EM country weights
EM_COUNTRY_WEIGHTS: dict[str, float] = {
    "China":     27.0,
    "India":     19.0,
    "Taiwan":    17.0,
    "S. Korea":   9.0,
    "Brazil":     5.0,
    "S. Africa":  3.5,
    "Mexico":     2.5,
    "Indonesia":  2.0,
    "Thailand":   2.0,
    "Vietnam":    1.0,
}

# ─── Industry drill-down (US only, GICS level 3) ─────────────────────────────
# Keys must match the names used in US_SECTOR_ETFS.
# Benchmark for each drill-down = the parent sector ETF (e.g. XLK for Technology).

US_INDUSTRY_ETFS: dict[str, dict[str, str]] = {
    "Technology": {
        "Semiconductors": "SOXX",   # iShares Semiconductor
        "Software":       "IGV",    # iShares Software
        "Cybersecurity":  "HACK",   # ETFMG Prime Cyber Security
        "Cloud":          "SKYY",   # First Trust Cloud Computing
        "AI & Robotics":  "ROBO",   # Robo Global Robotics & Automation
    },
    "Health Care": {
        "Biotech":        "IBB",    # iShares Biotechnology
        "Pharma":         "XPH",    # SPDR Pharmaceuticals
        "Med. Devices":   "IHI",    # iShares Medical Devices
        "Health Svcs":    "IHF",    # iShares Healthcare Providers
    },
    "Financials": {
        "Large Banks":    "KBE",    # SPDR Bank ETF
        "Regional Banks": "KRE",    # SPDR Regional Banking
        "Insurance":      "KIE",    # SPDR Insurance
        "FinTech":        "FINX",   # Global X FinTech
    },
    "Energy": {
        "Oil & Gas E&P":  "XOP",    # SPDR Oil & Gas Exploration
        "Clean Energy":   "ICLN",   # iShares Global Clean Energy
        "MLP/Pipelines":  "AMLP",   # Alerian MLP
    },
    "Consumer Discret.": {
        "Retail":         "XRT",    # SPDR Retail
        "Homebuilders":   "XHB",    # SPDR Homebuilders
        "Airlines":       "JETS",   # US Global Jets
    },
    "Industrials": {
        "Aerospace & Def":"ITA",    # iShares Aerospace & Defense
        "Transport":      "IYT",    # iShares Transportation
        "Defense":        "PPA",    # Invesco Aerospace & Defense
    },
    "Materials": {
        "Gold Miners":    "GDX",    # VanEck Gold Miners
        "Silver Miners":  "SIL",    # Global X Silver Miners
        "Metals & Mining":"XME",    # SPDR Metals & Mining
    },
    "Communication": {
        "Telecom":        "IYZ",    # iShares US Telecom
        "Media & Entmt":  "PBS",    # Invesco Dynamic Media
    },
    "Real Estate": {
        "Broad REIT":     "VNQ",    # Vanguard Real Estate
        "Mortgage REIT":  "REM",    # iShares Mortgage Real Estate
    },
    "Consumer Staples": {
        "Food & Beverage":"PBJ",    # Invesco Dynamic Food & Beverage
        "Agribusiness":   "MOO",    # VanEck Agribusiness
    },
    "Utilities": {
        "Water":          "PHO",    # Invesco Water Resources
        "Nuclear":        "NLR",    # VanEck Uranium & Nuclear
    },
}

# ─── Universe registry ────────────────────────────────────────────────────────

UNIVERSES: dict[str, dict] = {
    "US Sectors": {
        "etfs":      US_SECTOR_ETFS,
        "benchmark": DEFAULT_BENCHMARK,
        "weights":   SECTOR_WEIGHTS,
        "note":      "",
    },
    "EU Sectors": {
        "etfs":      EU_SECTOR_ETFS,
        "benchmark": EU_BENCHMARK,
        "weights":   EU_SECTOR_WEIGHTS,
        "note":      "ETFs in EUR (XETRA). RSC/RRG vollständig verfügbar. CMF/OBV etwas weniger liquide als US.",
    },
    "EM Countries": {
        "etfs":      EM_COUNTRY_ETFS,
        "benchmark": EM_BENCHMARK,
        "weights":   EM_COUNTRY_WEIGHTS,
        "note":      "Länder-ETFs in USD. RSC enthält Währungseffekt (FX + Aktienmarkt kombiniert) — gewollt bei Länder-Rotation.",
    },
}


# ─── Backend class ────────────────────────────────────────────────────────────

class SectorRotation:
    """Fetches OHLCV data and computes all sector-rotation indicators."""

    def __init__(
        self,
        sector_etfs: dict[str, str] | None = None,
        benchmark: str = DEFAULT_BENCHMARK,
        period: str = DEFAULT_PERIOD,
        weights: dict[str, float] | None = None,
    ) -> None:
        """Configure the sector rotation engine with ETF map, benchmark, period, and weights."""
        self.sector_etfs = sector_etfs or dict(US_SECTOR_ETFS)
        self.benchmark = benchmark
        self.period = period
        self.weights = weights or SECTOR_WEIGHTS
        self.sector_data: dict[str, pd.DataFrame] = {}
        self._summary: pd.DataFrame | None = None

    # ── Data loading ──────────────────────────────────────────────────────────

    def fetch_all(self) -> None:
        """Download daily OHLCV for all sector ETFs and the benchmark."""
        all_tickers = list(self.sector_etfs.values()) + [self.benchmark]
        try:
            raw = md.download(
                tickers=all_tickers,
                period=self.period,
                interval="1d",
                auto_adjust=False,
                progress=False,
            )
        except Exception as exc:
            logger.error("SectorRotation.fetch_all failed: %s", exc)
            return

        if raw is None or raw.empty:
            return

        if isinstance(raw.columns, pd.MultiIndex):
            # Standard structure: level-0 = field, level-1 = ticker
            for ticker in all_tickers:
                try:
                    df = pd.DataFrame({
                        col: raw[col][ticker]
                        for col in ("Open", "High", "Low", "Close", "Volume")
                        if col in raw.columns.get_level_values(0)
                    }).dropna(how="all")
                    if not df.empty:
                        self.sector_data[ticker] = df
                except Exception:
                    pass
        else:
            # Fallback: single-ticker download
            self.sector_data[all_tickers[0]] = raw[
                [c for c in ("Open", "High", "Low", "Close", "Volume") if c in raw.columns]
            ].dropna(how="all")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _close_weekly(self, ticker: str) -> pd.Series:
        """Return a weekly-resampled (Friday close) price series for ticker."""
        df = self.sector_data.get(ticker)
        if df is None or df.empty:
            return pd.Series(dtype=float)
        return df["Close"].resample("W-FRI").last().dropna()

    # ── Indicators ────────────────────────────────────────────────────────────

    def calc_mansfield_rsc(self, ticker: str, period_weeks: int = 30) -> float:
        """
        Mansfield Relative Strength Comparison (RSC) vs benchmark.

        RSC = (sector / benchmark) / MA(sector / benchmark, N weeks) − 1, in %.
        A positive value indicates the sector outperforms its own rolling average
        relative to the benchmark (genuine capital inflow signal).
        """
        sector_w = self._close_weekly(ticker)
        bench_w = self._close_weekly(self.benchmark)
        aligned = pd.concat([sector_w, bench_w], axis=1, join="inner")
        if aligned.shape[1] < 2 or len(aligned) < period_weeks + 1:
            return float("nan")
        aligned.columns = ["sector", "bench"]
        rs = aligned["sector"] / aligned["bench"]
        ma = rs.rolling(period_weeks).mean()
        rsc = (rs / ma - 1) * 100
        return float(rsc.iloc[-1])

    def calc_cmf(self, ticker: str, period: int = 14) -> float:
        """
        Chaikin Money Flow (CMF).

        CMF > 0 → buying pressure (accumulation).
        CMF < 0 → selling pressure (distribution).
        """
        df = self.sector_data.get(ticker)
        if df is None or df.empty or len(df) < period:
            return float("nan")
        h, lo, c, v = df["High"], df["Low"], df["Close"], df["Volume"]
        hl_range = (h - lo).replace(0, np.nan)
        mfm = ((c - lo) - (h - c)) / hl_range
        mfv = mfm * v
        cmf = mfv.rolling(period).sum() / v.rolling(period).sum()
        val = cmf.iloc[-1]
        return float(val) if pd.notna(val) else float("nan")

    def calc_obv_trend(self, ticker: str, period: int = 20) -> float:
        """
        OBV slope over `period` days, normalized by mean volume (×100).

        Positive = rising OBV (accumulation); negative = distribution.
        """
        df = self.sector_data.get(ticker)
        if df is None or df.empty or len(df) < period + 1:
            return float("nan")
        c, v = df["Close"], df["Volume"]
        direction = np.sign(c.diff().fillna(0))
        obv = (direction * v).cumsum()
        obv_w = obv.iloc[-period:]
        mean_vol = v.iloc[-period:].mean()
        if mean_vol == 0 or len(obv_w) < 2:
            return float("nan")
        x = np.arange(len(obv_w))
        slope, _ = np.polyfit(x, obv_w.values.astype(float), 1)
        return float(slope / mean_vol * 100)

    def calc_above_sma(self, ticker: str, sma_period: int = 200) -> str:
        """Whether the ETF currently trades above its own SMA."""
        df = self.sector_data.get(ticker)
        if df is None or df.empty or len(df) < sma_period:
            return "N/A"
        close = df["Close"].dropna()
        sma = close.rolling(sma_period).mean().iloc[-1]
        return "Yes" if close.iloc[-1] > sma else "No"

    def calc_rrg_coordinates(self, tail_weeks: int = 5) -> dict:
        """
        JdK RS-Ratio and RS-Momentum for each sector ETF (weekly).

        RS-Ratio  = 100 + (EWM_short / EWM_long − 1) × 100  [normalized around 100]
        RS-Momentum = 100 + (RS-Ratio / EWM(RS-Ratio) − 1) × 100

        Quadrant mapping:
          RS-Ratio ≥ 100 & RS-Momentum ≥ 100 → Leading
          RS-Ratio ≥ 100 & RS-Momentum < 100  → Weakening
          RS-Ratio < 100  & RS-Momentum < 100 → Lagging
          RS-Ratio < 100  & RS-Momentum ≥ 100 → Improving
        """
        bench_w = self._close_weekly(self.benchmark)
        results: dict = {}

        for sector, ticker in self.sector_etfs.items():
            sector_w = self._close_weekly(ticker)
            aligned = pd.concat([sector_w, bench_w], axis=1, join="inner")
            if len(aligned) < 52:
                continue
            aligned.columns = ["sector", "bench"]

            rs = aligned["sector"] / aligned["bench"]
            rs_ema_s = rs.ewm(span=10, adjust=False).mean()  # short-term
            rs_ema_l = rs.ewm(span=40, adjust=False).mean()  # long-term baseline

            rs_ratio = 100 + (rs_ema_s / rs_ema_l - 1) * 100
            rs_mom = 100 + (rs_ratio / rs_ratio.ewm(span=5, adjust=False).mean() - 1) * 100

            tail = slice(-(tail_weeks + 1), None)
            results[sector] = {
                "ticker":               ticker,
                "rs_ratio":             rs_ratio.iloc[tail].tolist(),
                "rs_momentum":          rs_mom.iloc[tail].tolist(),
                "dates":                [d.strftime("%Y-%m-%d") for d in rs_ratio.iloc[tail].index],
                "current_rs_ratio":     float(rs_ratio.iloc[-1]),
                "current_rs_momentum":  float(rs_mom.iloc[-1]),
            }

        return results

    def calc_rrg_coordinates_daily(self, tail_days: int = 15) -> dict:
        """
        Daily RS-Ratio / RS-Momentum — captures intra-week rotation.

        Uses shorter EWM spans (5 / 20 days) vs. the weekly variant (10 / 40 weeks)
        so recent shifts surface faster.  Minimum 60 trading days of data required.
        """
        bench = self.sector_data.get(self.benchmark)
        if bench is None or bench.empty:
            return {}
        bench_close = bench["Close"].dropna()
        results: dict = {}

        for sector, ticker in self.sector_etfs.items():
            df = self.sector_data.get(ticker)
            if df is None or df.empty:
                continue
            sector_close = df["Close"].dropna()
            aligned = pd.concat([sector_close, bench_close], axis=1, join="inner")
            if len(aligned) < 60:
                continue
            aligned.columns = ["sector", "bench"]

            rs = aligned["sector"] / aligned["bench"]
            rs_ema_s = rs.ewm(span=5,  adjust=False).mean()   # ~1 week
            rs_ema_l = rs.ewm(span=20, adjust=False).mean()   # ~1 month

            rs_ratio = 100 + (rs_ema_s / rs_ema_l - 1) * 100
            rs_mom   = 100 + (rs_ratio / rs_ratio.ewm(span=5, adjust=False).mean() - 1) * 100

            tail = slice(-(tail_days + 1), None)
            results[sector] = {
                "ticker":              ticker,
                "rs_ratio":            rs_ratio.iloc[tail].tolist(),
                "rs_momentum":         rs_mom.iloc[tail].tolist(),
                "dates":               [d.strftime("%Y-%m-%d") for d in rs_ratio.iloc[tail].index],
                "current_rs_ratio":    float(rs_ratio.iloc[-1]),
                "current_rs_momentum": float(rs_mom.iloc[-1]),
            }

        return results

    # ── Multi-period RSC ──────────────────────────────────────────────────────

    def calc_mansfield_rsc_multi(self, ticker: str, periods: tuple = (4, 12, 30)) -> dict:
        """RSC for several weekly look-back periods. Returns {weeks: value}."""
        return {p: self.calc_mansfield_rsc(ticker, period_weeks=p) for p in periods}

    @staticmethod
    def _rsc_trend(r4: float, r12: float, r30: float) -> str:
        """
        Classify the multi-period RSC pattern into 5 actionable states.

        Monotonically rising (4W > 12W > 30W) = momentum building.
        Monotonically falling (4W < 12W < 30W) = momentum fading.
        The sign of RSC_30W separates established from nascent moves.
        """
        if any(np.isnan(v) for v in (r4, r12, r30)):
            return "N/A"
        if r4 > r12 > r30:
            return "↗ Turning" if r30 <= 0 else "↗ Strong"
        if r4 < r12 < r30:
            return "↘ Fading" if r30 >= 0 else "↘ Weak"
        return "→ Stable"

    # ── Summary table ─────────────────────────────────────────────────────────

    @staticmethod
    def rrg_status(rs_ratio: float, rs_momentum: float) -> str:
        """Return the RRG quadrant label: Leading / Weakening / Improving / Lagging / N/A."""
        if np.isnan(rs_ratio) or np.isnan(rs_momentum):
            return "N/A"
        if rs_ratio >= 100 and rs_momentum >= 100:
            return "Leading"
        if rs_ratio >= 100:
            return "Weakening"
        if rs_momentum >= 100:
            return "Improving"
        return "Lagging"

    def _get_forward_pe(self, ticker: str) -> float:
        """Fetch the forward PE ratio for ticker via yfinance; returns NaN on failure."""
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info
            pe = info.get("forwardPE") or info.get("trailingPE")
            return float(pe) if pe else float("nan")
        except Exception:
            return float("nan")

    def build_summary(self, include_pe: bool = False) -> pd.DataFrame:
        """Aggregate all indicators into a single sector summary DataFrame."""
        if not self.sector_data:
            self.fetch_all()

        rrg = self.calc_rrg_coordinates()
        rows = []

        for sector, ticker in self.sector_etfs.items():
            if ticker not in self.sector_data:
                continue

            rrg_d = rrg.get(sector, {})
            rsr = rrg_d.get("current_rs_ratio", float("nan"))
            rsm = rrg_d.get("current_rs_momentum", float("nan"))

            rsc = self.calc_mansfield_rsc_multi(ticker, periods=(4, 12, 30))
            r4, r12, r30 = rsc[4], rsc[12], rsc[30]

            row: dict = {
                "Sector":       sector,
                "ETF":          ticker,
                "RSC_4W":       round(r4,  2),
                "RSC_12W":      round(r12, 2),
                "RSC_30W":      round(r30, 2),
                "RSC_Trend":    self._rsc_trend(r4, r12, r30),
                "CMF_14D":      round(self.calc_cmf(ticker, 14), 3),
                "OBV_Trend":    round(self.calc_obv_trend(ticker, 20), 2),
                "Above_SMA200": self.calc_above_sma(ticker, 200),
                "Above_SMA50":  self.calc_above_sma(ticker, 50),
                "RS_Ratio":     round(rsr, 2) if not np.isnan(rsr) else float("nan"),
                "RS_Momentum":  round(rsm, 2) if not np.isnan(rsm) else float("nan"),
                "Status":       self.rrg_status(rsr, rsm),
                "Weight_%":     self.weights.get(sector, 1.0),
            }

            if include_pe:
                row["Forward_PE"] = self._get_forward_pe(ticker)

            rows.append(row)

        self._summary = pd.DataFrame(rows)
        return self._summary

    @property
    def summary(self) -> pd.DataFrame:
        """Return the cached summary DataFrame, building it on first access if needed."""
        if self._summary is None:
            self.build_summary()
        return self._summary


def sector_context_text(summary, asset_etf: str = '', asset_sector: str = '') -> str:
    """Format a sector-rotation summary DataFrame into a compact German text block for
    the single-asset AI analysis / report.

    Groups the sectors by RRG quadrant (Leading → Lagging) and, if the target asset's
    sector ETF is given, appends a line highlighting where that sector stands. Returns
    '' for an empty/missing summary.
    """
    if summary is None or getattr(summary, 'empty', True):
        return ''
    try:
        df = summary.sort_values(by='RS_Ratio', ascending=False)
    except Exception:
        df = summary

    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for _, r in df.iterrows():
        try:
            groups[r['Status']].append(
                f"{r['Sector']} ({r['RS_Ratio']:.1f}/{r['RS_Momentum']:.1f})")
        except Exception:
            continue

    lines: list[str] = []
    for status in ('Leading', 'Improving', 'Weakening', 'Lagging'):
        if groups.get(status):
            lines.append(f"  {status}: " + ", ".join(groups[status]))

    if asset_etf:
        row = df[df['ETF'] == asset_etf]
        if not row.empty:
            r = row.iloc[0]
            lines.append(
                f"» Sektor des Ziel-Assets: {r['Sector']} — Status {r['Status']} "
                f"(RS-Ratio {r['RS_Ratio']:.1f}, RS-Momentum {r['RS_Momentum']:.1f}, "
                f"RSC-Trend {r['RSC_Trend']}, über SMA200: {r['Above_SMA200']})")
        elif asset_sector:
            lines.append(f"» Sektor des Ziel-Assets: {asset_sector} — kein direktes "
                         "RRG-Match im US-Sektor-Set.")

    return "\n".join(lines)
