# Attribution & License Notes

Dieses Dokument beschreibt die algorithmischen Quellen, Inspirationen und
verwendeten Drittanbieter-Bibliotheken des Projekts. Es dient als Grundlage
für die Lizenzvergabe bei einer eventuellen Veröffentlichung.

---

## 1. Lizenzvorschlag

**MIT License** (empfohlen)

Die MIT-Lizenz ist die sinnvollste Wahl, weil:
- alle verwendeten Drittanbieter-Bibliotheken MIT/BSD/Apache-2.0-kompatibel sind,
- der Code überwiegend Eigenimplementierungen bekannter, nicht urheberrechtlich
  geschützter Algorithmen enthält,
- keine direkte Code-Übernahme aus Medium-Artikeln nachweisbar ist — die Artikel
  dienten als Ideen- und Methodenbeschreibung, die Implementierung erfolgte
  eigenständig in Python/Plotly/Streamlit.

```
MIT License

Copyright (c) 2024-2025 Kurt

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 2. Algorithmen und konzeptionelle Quellen

Die nachfolgenden Techniken und Konzepte basieren auf allgemein bekannten
Finanzmarkt-Algorithmen. Viele Implementierungsdetails wurden durch Artikel
auf **medium.com** sowie durch TradingView Pine-Script-Vorlagen angeregt.
Da es sich um eigenständige Python-Neuimplementierungen handelt, entsteht
keine Copyleft-Pflicht; eine informelle Nennung der Herkunft ist jedoch
Best Practice.

### 2.1 Klassische Technische Indikatoren
*(allgemein bekannte Algorithmen, keine spezifischen Autoren)*

| Datei | Indikator | Algorithmus / Formel | Quelle |
|---|---|---|---|
| `indicator/rsi.py` | Relative Strength Index | J. Welles Wilder (1978), *New Concepts in Technical Trading Systems* | Public domain |
| `indicator/macd.py` | MACD | Gerald Appel (1970er), via `ta`-Library | Public domain |
| `indicator/bol.py` | Bollinger Bands | John Bollinger (1980er), via `ta`-Library | Public domain |
| `indicator/stoch.py` | Stochastic Oscillator | George Lane (1950er), via `ta`-Library | Public domain |
| `indicator/cci.py` | Commodity Channel Index | Donald Lambert (1980), via `ta`-Library | Public domain |
| `indicator/adx.py` | Average Directional Index | J. Welles Wilder (1978), via `talib` | Public domain |
| `indicator/don.py` | Donchian Channel | Richard Donchian, via `ta`-Library | Public domain |
| `indicator/ici.py` | Ichimoku Cloud | Goichi Hosoda (1969) | Public domain |
| `indicator/vwap.py` | VWAP | Standard-Börsenalgorithmus | Public domain |
| `indicator/mam.py` | Moving Average Multi (SMA/EMA/WMA/DEMA) | Eigenimplementierung | — |
| `indicator/dema.py` | Double EMA | Patrick Mulloy (1994) | Public domain |
| `indicator/fib.py` | Fibonacci Levels | Leonardo Fibonacci / klassische TA | Public domain |
| `indicator/vol.py` | Volume Analyse | Klassisch | — |
| `indicator/relvol.py` | Relative Volume | Klassisch | — |
| `indicator/cumd.py` | Cumulative Delta | Klassisch | — |

### 2.2 Erweiterte / modernere Indikatoren
*(oft durch Medium-Artikel oder TradingView-Skripte beschrieben)*

| Datei | Indikator | Konzept / Herkunft | Typ der Inspiration |
|---|---|---|---|
| `indicator/renko.py` | Renko Candles | Klassisch japanisch, Brick-Sizing via ATR — Implementierungsansätze verbreitet auf Medium | Medium-Artikel (ATR-basiertes Renko in Python) |
| `indicator/heikin.py` | Heikin Ashi Candles | Klassisch japanisch, erweitertes Export-Interface eigenständig | Public domain / eigene Erweiterung |
| `indicator/nsdt.py` | NSDT HAMA Candles | TradingView-Skript von **NSDT** (North Star Developer Tools); Pine-Script-Logik nach Python portiert | TradingView Public Script — ursprünglicher Autor: NSDT |
| `indicator/fvg.py` | Fair Value Gap | Smart Money Concepts (SMC) — Konzept verbreitet auf Medium und in Trading-Blogs | Medium / Trading-Community |
| `indicator/bos.py` | Break of Structure | Smart Money Concepts (SMC) | Medium / Trading-Community |
| `indicator/lqz.py` | Liquidity Zones | Volume-Profile-Ansatz, Clustering via Preis-Bins | Medium-Artikel (Volume Profile in Python) |
| `indicator/oft.py` | Order Flow Tracker | Order-Block-Analyse (SMC), Eigenimplementierung | Medium / Trading-Community |
| `indicator/sup.py` | Support / Resistance | Pivot-basierter Ansatz, klassisch | Medium-Artikel (S&R in Python) |
| `indicator/bsz.py` | Buy Sell Zones | Aufbauend auf `ewo.py` — Eigenentwicklung | — |
| `indicator/ewo.py` | Elliott Wave Oscillator | SMA-Differenz-Ansatz, bekannt aus Trading-Foren und Medium | Medium / TradingView-Community |
| `indicator/hor.py` | Horizontal Levels | Swing-High/Low-Pivot-Logik | Medium-Artikel (Pivot-Points in Python) |
| `indicator/wml.py` | Week/Month Levels | Wochenhoch/-tief Eigenimplementierung | — |
| `indicator/can.py` | Candlestick Patterns | `ta`-Library + klassische Muster | Public domain / `ta`-Library |
| `indicator/gan.py` | Gann Levels | W.D. Gann-Methodik, Octant-Scaling eigenständig | Public domain |
| `indicator/mmm.py` | Market Maker Master Pattern | Konzept aus Trading-Community (ATR-Phasen + Fibonacci) | Medium / Trading-Blogs |
| `indicator/qtrend.py` | QTrend + Keltner Squeeze | Kombination aus Keltner Channel + Bollinger Band Squeeze (LazyBear-Konzept) | TradingView / Medium |
| `indicator/atc.py` | Auto Trend Channels | LinearRegression (scikit-learn) auf OHLC-Daten — Ansatz aus Python-Finance-Artikeln | Medium-Artikel (sklearn Trendlinien) |
| `indicator/atl.py` | Auto Trend Lines | Wie `atc.py` | Medium-Artikel (sklearn Trendlinien) |
| `indicator/zcr.py` | Z-Score Indicator | Statistik-Standardmethode, Anwendung im Trading durch Medium-Artikel populär | Medium-Artikel (Z-Score Trading) |
| `indicator/markov.py` | Markov Regime Detection | Hidden Markov / Regime-Detection; Ansatz für Bull/Bear/Sideways-Klassifikation via Übergansmatrix auf Medium verbreitet | Medium-Artikel (Markov Chains in Finance) |
| `indicator/bar.py` | Bar Patterns | Eigenimplementierung | — |
| `indicator/pre.py` | Pre-Market Levels | Eigenimplementierung | — |

### 2.3 Analyse-Module

| Datei | Funktion | Konzept / Herkunft |
|---|---|---|
| `predictlib.py` | Kursprognose mit XGBoost | XGBoost + Lag-Features für Time-Series-Prognose — in vielen Medium-Artikeln beschrieben (z. B. *"Stock Price Prediction with XGBoost"*) |
| `PortfolioAnalysis.py` | Portfolio-Performance vs. Index | Eigenimplementierung auf Basis von `yfinance` und `pandas` |
| `sector_rotation.py` | Sektor-Rotation, RRG-Graph | **Mansfield Relative Strength** (Stan Weinstein, 1988); **JdK RS-Ratio / RS-Momentum** (Julius de Kempenaer — Relative Rotation Graph); **Chaikin Money Flow** (Marc Chaikin) — alle Public-Domain-Algorithmen, RRG-Konzept durch Medium-Artikel angeregt |
| `option_calculator.py` | Optionsscheinrechner | **Black-Scholes-Formel** (Fischer Black, Myron Scholes, 1973) — Public domain; Implementierung via `scipy.stats.norm` |
| `sentiment.py` | Nachrichten-Sentiment | NLTK VADER Sentiment Analyser (Hutto & Gilbert, 2014, MIT) auf Yahoo-Finance-RSS-Feed |
| `yahoolib.py` | Yahoo Finance Scraping | Eigenimplementierung des Consent/Cookie-Flows; allgemein dokumentierter Ansatz in der Python-Community |
| `meta_indicator.py` | Übergeordnete Signale | Eigenimplementierung | |
| `market_data.py` | Marktdaten-Cache | Eigenimplementierung |

---

## 3. Verwendete Drittanbieter-Bibliotheken

Alle Bibliotheken sind mit der MIT-Lizenz kompatibel.

| Bibliothek | Version (ca.) | Lizenz | Verwendung |
|---|---|---|---|
| `streamlit` | ≥ 1.30 | Apache 2.0 | Web-UI-Framework |
| `plotly` | ≥ 5.0 | MIT | Charts und Visualisierungen |
| `pandas` | ≥ 2.0 | BSD-3 | Datenverarbeitung |
| `numpy` | ≥ 1.24 | BSD-3 | Numerische Berechnungen |
| `yfinance` | ≥ 0.2 | Apache 2.0 | Marktdaten von Yahoo Finance |
| `ta` | ≥ 0.10 | MIT | Technische Indikatoren (Basisberechnungen) |
| `talib` | optional | BSD | Technische Indikatoren (ADX, RSI) |
| `scikit-learn` | ≥ 1.0 | BSD-3 | LinearRegression für Trendkanäle |
| `xgboost` | ≥ 2.0 | Apache 2.0 | ML-Kursprognose |
| `scipy` | ≥ 1.10 | BSD-3 | Black-Scholes-Berechnung |
| `nltk` | ≥ 3.8 | Apache 2.0 | VADER Sentiment-Analyse |
| `feedparser` | ≥ 6.0 | MIT | RSS-Feed-Parsing |
| `requests` | ≥ 2.28 | Apache 2.0 | HTTP-Requests |

---

## 4. Hinweise zur Veröffentlichung

1. **Yahoo Finance**: `yfinance` und direkte API-Aufrufe unterliegen den
   [Yahoo Finance Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforyf/index.html).
   Für eine kommerzielle Veröffentlichung ist eine lizenzierte Datenquelle
   (z. B. Polygon.io, Alpha Vantage) zu erwägen.

2. **NSDT HAMA Candles** (`indicator/nsdt.py`): Der Originalcode ist ein
   TradingView-Public-Script unter der Mozilla Public License 2.0 (TradingView-Standard).
   Die Python-Portierung ist eine unabhängige Neuimplementierung nach der
   veröffentlichten Beschreibung, sollte aber mit einem Hinweis auf den
   ursprünglichen TradingView-Autor versehen werden:
   > *NSDT HAMA Candle concept originally published on TradingView by NSDT.*

3. **Relative Rotation Graph (RRG)**: Das RRG-Konzept ist eine eingetragene
   Marke von **Julius de Kempenaer / RRG Research**. Die Algorithmusformeln
   (JdK RS-Ratio / RS-Momentum) sind veröffentlicht und frei verwendbar;
   der Begriff "RRG" sollte in der Benutzeroberfläche als Referenz auf die
   Herkunft gekennzeichnet werden.

4. **Medium-Artikel**: Da keine direkten Code-Snippets übernommen wurden
   (Medium-Artikel nutzen in der Regel eigene Lizenzen pro Autor), entstehen
   keine Lizenzverpflichtungen. Ein allgemeiner Dankeshinweis in der README ist
   angemessen.
