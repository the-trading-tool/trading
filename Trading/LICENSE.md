# LICENSE

## MIT License

Copyright (c) 2024–2025 Kurt

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

---

## Third-Party Libraries

This project uses the following open-source libraries. Each is distributed
under its own license; all are compatible with the MIT License above.

| Library | License |
|---|---|
| streamlit | Apache 2.0 |
| plotly | MIT |
| pandas | BSD-3-Clause |
| numpy | BSD-3-Clause |
| yfinance | Apache 2.0 |
| ta | MIT |
| talib (TA-Lib) | BSD |
| scikit-learn | BSD-3-Clause |
| xgboost | Apache 2.0 |
| scipy | BSD-3-Clause |
| nltk | Apache 2.0 |
| feedparser | MIT |
| requests | Apache 2.0 |

---

## Algorithmic Attributions

The technical indicators and analytical methods implemented in this project
are based on well-known, publicly documented financial algorithms and
methodologies. The Python implementations are original work by the author,
inspired in part by articles on [medium.com](https://medium.com) and
publicly available TradingView Pine Script examples.

### Public Domain Algorithms

The following algorithms are in the public domain. Their formulas are
reproduced here as independent implementations:

- **Relative Strength Index (RSI)** — J. Welles Wilder, *New Concepts in
  Technical Trading Systems*, 1978
- **MACD** — Gerald Appel, 1970s
- **Bollinger Bands** — John Bollinger, 1980s
- **Stochastic Oscillator** — George Lane, 1950s
- **Commodity Channel Index (CCI)** — Donald Lambert, 1980
- **Average Directional Index (ADX)** — J. Welles Wilder, 1978
- **Donchian Channel** — Richard Donchian
- **Ichimoku Cloud** — Goichi Hosoda, 1969
- **Double EMA (DEMA)** — Patrick Mulloy, 1994
- **Black-Scholes Option Pricing** — Fischer Black & Myron Scholes, 1973
- **Fibonacci Retracement Levels** — classical technical analysis
- **Gann Levels** — W. D. Gann methodology
- **VWAP** — standard exchange algorithm
- **Heikin Ashi Candles** — classical Japanese charting technique
- **Renko Candles** — classical Japanese charting technique
- **Elliott Wave Oscillator** — derived from Ralph Elliott's wave theory
- **Mansfield Relative Strength** — Stan Weinstein, *Secrets for Profiting
  in Bull and Bear Markets*, 1988
- **Chaikin Money Flow (CMF)** — Marc Chaikin

### Third-Party Concept Attributions

- **NSDT HAMA Candles** (`indicator/nsdt.py`): The HAMA Candle concept was
  originally published on TradingView by **NSDT** (North Star Developer Tools)
  as a public Pine Script. The implementation here is an independent Python
  port based on the published description.
  > *NSDT HAMA Candle concept originally published on TradingView by NSDT.*

- **Relative Rotation Graph (RRG)** (`sector_rotation.py`): The RRG
  methodology and the JdK RS-Ratio / RS-Momentum formulas were developed and
  published by **Julius de Kempenaer** / RRG Research. "Relative Rotation
  Graph" and "RRG" are trademarks of RRG Research. The formulas used here
  are reproduced from publicly available descriptions for non-commercial
  research purposes.

- **Keltner Channel Squeeze** (used in `indicator/qtrend.py`): The squeeze
  concept combining Bollinger Bands and Keltner Channels was popularized by
  **John Carter** and widely described in the TradingView community
  (LazyBear's Squeeze Momentum indicator).

- **VADER Sentiment Analysis** (`sentiment.py`): VADER (Valence Aware
  Dictionary and sEntiment Reasoner) by C.J. Hutto & Eric Gilbert (2014),
  distributed as part of NLTK under the Apache 2.0 License.

- **Smart Money Concepts** — Fair Value Gap (`indicator/fvg.py`),
  Break of Structure (`indicator/bos.py`), Order Flow Tracker
  (`indicator/oft.py`): These concepts originate from the Smart Money
  Concepts (SMC) trading methodology, widely documented in the trading
  community and on medium.com. Implementations are original work by the author.

### Data Sources

- **Yahoo Finance**: Market data is retrieved via `yfinance` and direct
  Yahoo Finance API calls. Usage is subject to the
  [Yahoo Finance Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforyf/index.html).
  This software is intended for personal and non-commercial use only with
  respect to Yahoo Finance data.

---

*For a detailed breakdown of all indicators and their sources, see
[ATTRIBUTION.md](ATTRIBUTION.md).*
