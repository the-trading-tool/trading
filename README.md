# Trading Tool

Eine lokale, Streamlit-basierte App zur technischen Analyse von Aktien, ETFs und Krypto-Assets — mit Indikator-Bibliothek, Portfolio-Tracking, Sektor-Rotation und Pine-Script-Export.

> ⚖️ **Hinweis / Disclaimer:** Dieses Projekt ist ein persönliches Tool für den privaten, nicht-kommerziellen Gebrauch und stellt **keine Anlageberatung** dar. Es enthält keine eigenen Marktdaten — diese werden zur Laufzeit von Drittanbietern (Yahoo Finance, Financial Modeling Prep) abgerufen. Details siehe [Trading/README.md](Trading/README.md).
>
> This project is a personal tool for private, non-commercial use and does **not constitute financial advice**. It contains no market data of its own — data is fetched at runtime from third-party providers (Yahoo Finance, Financial Modeling Prep). See [Trading/README.md](Trading/README.md) for details.

---

## Features

- **36 technische Indikatoren** (RSI, MACD, Bollinger Bands, Ichimoku, ADX, Heikin Ashi, Smart-Money-Concepts u. v. m.)
- **Portfolio-Tracking** mit Buy/Sell-Signalen und Performance-Simulation
- **Sektor-Rotation-Dashboard** (Relative Rotation Graphs, Treemap, Industry-Drill-down) für US-, EU- und Schwellenländer-Universen
- **Pine-Script-v5-Export** der Indikatoren für TradingView
- **Earnings-Kalender, Marktübersicht (Market Map), Asset-Vergleich**
- **Mehrsprachig** (Deutsch/Englisch)
- **Scheduler** für automatisierte Datenaktualisierung

## Quick Start

Eine ausführliche Schritt-für-Schritt-Anleitung für Windows und Linux findest du in [Trading/README.md](Trading/README.md).

Kurzfassung:

```bash
git clone https://github.com/the-trading-tool/trading
cd trading/Trading
pip install -r requirements.txt
streamlit run asset_analyzer.py
```

## Tech-Stack

Python · Streamlit · Plotly · pandas · yfinance · TA-Lib · scikit-learn

## Lizenz

MIT License — siehe [Trading/LICENSE.md](Trading/LICENSE.md). Verwendete Algorithmen und Drittanbieter-Bibliotheken sind in [Trading/ATTRIBUTION.md](Trading/ATTRIBUTION.md) dokumentiert.
