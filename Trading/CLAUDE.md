# Trading App — Claude Context

Dieses Dokument ist die Übergabe zwischen Claude-Sessions.
Beim Start einer neuen Session: kurz lesen, dann loslegen.

---

## Projektübersicht

Streamlit-basierte lokale Trading-App (noch nicht produktiv).

**Einstiegspunkt:** `asset_analyzer.py` (Hauptapp), läuft via `streamlit run asset_analyzer.py`

**Kern-Module:**
| Datei | Zweck |
|---|---|
| `tradinglib/tools.py` | Basis-Utilities: `Tools`, `Db_tools` (SQLite-Wrapper), `St_tools`, `ExpressionEvaluator*`, `BuySellSignalGenerator` |
| `tradinglib/fetch_data.py` | Lokale OHLC-Daten aus SQLite laden + Yahoo-Finance-Fallback |
| `tradinglib/market_data.py` | Zentraler yfinance-Wrapper mit Caching und lokalem DB-Fallback |
| `tradinglib/ticker_tools.py` | Ticker-Daten, `OHLCQueryPlanner`, Yahoo-Download |
| `tradinglib/system_config.py` | App-Konfiguration via `config.db` (erbt von `Db_tools`) |
| `tradinglib/multi_transaction.py` | Portfolio-Transaktionslogik, Buy/Sell-Signale |
| `tradinglib/scheduler.py` | Job-Scheduler mit SQLite-Backend |
| `tradinglib/indicator/` | 36 technische Indikatoren (ewo, rsi, macd, …) |
| `tradinglib/utils.py` | `DataUtils`: Tabellennamen, OHLC-Save, Exchange-Rates |

**Hilfsskripte (CLI):**
- `get_asset_data.py` — Preisdaten von Yahoo holen und lokal speichern
- `get_asset_info.py` — Asset-Metadaten laden
- `asset_perf2.py` — Performance-Simulation
- `schedserver.py` — Scheduler als Daemon

**Datenbanken (alle lokal unter `./database/`):**
- `asset_info.db` — Ticker-Stammdaten
- `yf_<TICKER>.db` — OHLC-Preisdaten je Ticker (Tabellen: min_data, h60_data, day_data, week_data, month_data)
- `asset_simulation_.db` — Berechnete Performance-Werte (neue Namenskonvention mit `_`-Trenner; ältere Versionen nutzen noch die Namen ohne `_`). WAL-Modus aktiv → parallele Lese-/Schreibzugriffe ohne Lock-Konflikte. `asset_performance_.db` wird nicht mehr benötigt.
- `config.db` — App-Konfiguration (Key-Value)
- `scheduler.db` — Scheduler-Jobs

---

## Abgeschlossene PRs

| PR | Branch | Inhalt |
|---|---|---|
| #1 | `add-readme` | README.md + .gitignore erstellt |
| #2 | `fix/db-connection-safety` | `__enter__`/`__exit__` auf `Db_tools`; `try/finally` in `fetch_data.py`, `utils.py`; `with Db_tools` in `search.py`; parameterisierte Queries in `fetch_data.py` |
| #3 | `fix/code-quality` | SQL-Injection (f-String → `?` params) in `fetch_data.py`, `excel_executor.py`, `multi_transaction.py`; Cache-Begrenzung (512 Einträge) in `market_data.py`; alle `except:` → `except Exception:` in 60 Dateien |
| #4 | `feature/pine-export-and-portfolio-analysis` | Pine Script v5-Exporter (22 Templates, `pine_exporter.py`); Indikator-`params`-Schema (22 Klassen); ⚙-Config-Dialog in `system_config.py`; `MultiCheckboxSelector` lädt Config-Defaults; Markov-Regime-Indikator; Portfolio-Analyse-Tab auf "Own Transactions"-Seite |
| (WIP) | `refactor/indicator-naming` | Einheitliches `{prefix}_{name}`-Schema für alle ~30 Indikatoren; DB-Dateien auf `_`-Trenner umgestellt; pdict-Keys, buy/sell-Query-Defaults, pine_exporter, live_ticker, performance_details synchronisiert. `macd_signal` in pdict. `--backfill` + `--rescore` Fast-Modes in `asset_perf2.py`. SQLite WAL-Modus auf `asset_simulation_.db`; `asset_performance_.db` entfernt. Siehe Naming-Tabelle unten. |
| (WIP) | `feature/sector-rotation` | Sector Rotation Dashboard: `tradinglib/sector_rotation.py` (Backend: SectorRotation, RSC/CMF/OBV/RRG-Indikatoren, US+EU+EM-Universen, Industry-Drill-down); `tradinglib/sector_rotation_page.py` (6-Tab-UI: RRG Weekly, RRG Daily, Treemap, Sector Matrix, Industry Drill-down, Best of Sector); `tradinglib/sector_stocks.py` (DB-Query für Top-Stocks je Sektor + RSC vs ETF Anreicherung); Route in `asset_analyzer.py` via `?rotation=true` |

---

## Indikator-Spalten-Namenskonvention

Regel: `{indikator_prefix}_{semantischer_name}` in `snake_case`.
Gilt für: DataFrame-Spalten, pdict-Keys (= DB-Spalten), buy/sell-Queries.

| Alte(r) Name(n) | Neuer Name | Indikator |
|---|---|---|
| `RelVol_Ratio`, `RelVol_Current`, `RelVol_Past`, `AdjVolume` | `relvol_ratio`, `relvol_current`, `relvol_past`, `relvol_adj_vol` | relvol |
| `plus_di`, `minus_di`, `adx_plus_di`, `adx_minus_di` | `adx_plus`, `adx_minus` | adx |
| — | `adx_angle` | adx (neu) |
| — | `momentum_ema_angle` | stoch/indicator (neu) |
| — | `ema9_angle` | fetch_data (neu) |
| `stoch` (aus rsi.py), `rsi_momentum` | `momentum` | rsi |
| `stoch_ema` | `momentum_ema` | stoch/indicator |
| `MA20`, `MA50`, `MA100`, `MA200` | `sma20`, `sma50`, `sma100`, `sma200` | indicator.sma() |
| `dema_buy_signal`, `dema_sell_signal` | `dema_buy`, `dema_sell` | dema |
| `Resistance`, `Support` | `sup_resistance`, `sup_support` | sup |
| `ewoEma` | `ewo_ema` | ewo |
| `ema_ha_high`, `ema_ha_low`, `HA_Close`, `HA_Open` | `ha_ema_high`, `ha_ema_low`, `ha_close`, `ha_open` | heikin |
| `horcrux` | `hor_val` | hor |
| `ATR` (in bos.py) | `bos_atr` | bos |
| `ATR`, `BB_width`, `Phase`, `Signal`, `HighTF_Phase` (in mmm.py) | `mmm_atr`, `mmm_bb_width`, `mmm_phase`, `mmm_signal`, `mmm_htf_phase` | mmm |
| `Delta`, `CumDelta` (in cumd.py) | `cumd_delta`, `cumd_cum_delta` | cumd |
| `Delta`, `CumDelta`, `CumDelta_SMA` (in vol.py) | `vol_delta`, `vol_cum_delta`, `vol_cum_delta_sma` | vol |
| `Delta`, `CumDelta` (in mmm.py) | `mmm_delta`, `mmm_cum_delta` | mmm |
| `upperDon`, `lowerDon`, `midDon` | `don_upper`, `don_lower`, `don_mid` | don |
| `bbBbm{n}`, `bbBbh{n}`, `bbBbl{n}` | `bol_mid_{n}`, `bol_upper_{n}`, `bol_lower_{n}` | bol |
| `gan_R{i}`, `gan_S{i}` | `gan_r{i}`, `gan_s{i}` | gan |
| `Price Change`, `Buy Volume`, `Sell Volume`, etc. (oft.py) | `oft_price_change`, `oft_buy_vol`, `oft_sell_vol`, etc. | oft |

### pdict-Keys / DB-Spalten (asset_perf2.py → asset_simulation_*.db)

Bei den folgenden Bezeichnern war die DataFrame-Spalte bereits konsistent benannt
(`sup_resistance`/`sup_support`/`relvol_ratio`), aber das pdict (und damit die
persistierten Spalten in `asset_simulation_.db`, `asset_simulation_{year}.db`,
`asset_simulation_all.db`) verwendete abweichende, nicht-snake_case Namen.
Hier wurde **das pdict an die df-Konvention angepasst** (umgekehrte Richtung zur
Tabelle oben — Regel: bei Konflikt gewinnt der bereits etablierte df-/Indikator-Name,
sofern er der `{prefix}_{name}`-Konvention folgt; sonst gewinnt der pdict-Name).
Die Spalten in allen drei DB-Dateien wurden per `ALTER TABLE ... RENAME COLUMN`
migriert (Daten bleiben erhalten):

| Alter pdict-/DB-Name | Neuer Name | Quelle |
|---|---|---|
| `resistance` | `sup_resistance` | sup (Tagesbasis) |
| `wkResistance` | `sup_resistance_wk` | sup (Wochenbasis) |
| `moResistance` | `sup_resistance_mo` | sup (Monatsbasis) |
| `support` | `sup_support` | sup (Tagesbasis) |
| `wkSupport` | `sup_support_wk` | sup (Wochenbasis) |
| `moSupport` | `sup_support_mo` | sup (Monatsbasis) |
| `relVolRatio` | `relvol_ratio` | relvol (Tagesbasis) |
| `wkRelVolRatio` | `relvol_ratio_wk` | relvol (Wochenbasis) |
| `moRelVolRatio` | `relvol_ratio_mo` | relvol (Monatsbasis) |

Hinweis: `relvol_ratio_wk`/`relvol_ratio_mo` stammen bereits architektonisch aus
`relvol.py` — `fetch_data.fetch_data()` instanziiert die `Relvol`-Klasse für jedes
Timeframe (1d/1wk/1mo) separat; `asset_perf2.py` liest lediglich den letzten Wert
der Spalte `relvol_ratio` aus `df_weekly`/`df_monthly` (analog zu `ewo_wk`/`ewo_mo`
aus `ewo.py`). Es war also keine zusätzliche Berechnung in `relvol.py` nötig,
sondern nur die Vereinheitlichung der pdict-Schlüssel.

### Trend-Scores nach ewo.py / macd.py verlagert

Analog zu `adx_angle` (→ adx.py) wurden zwei weitere, bisher nur als Skalare in
`asset_perf2.py` (für `overallValueTrend`) berechnete Trend-Scores als echte
DataFrame-Spalten in die jeweiligen Indikator-Klassen verlagert. Dadurch stehen
sie jetzt auch live im Asset Viewer (z. B. in buy/sell-Queries) zur Verfügung,
und die wk/mo-Varianten ergeben sich automatisch aus der Pro-Timeframe-
Instanziierung in `fetch_data.fetch_data()` (wie bei `ewo`/`relvol_ratio`):

| Alter pdict-Name (Skalar via Helper) | Neue Spalte (Indikator) | Neuer pdict-Name |
|---|---|---|
| `ewoDayTrend` (`trend(df)`) | `ewo_trend` (ewo.py) | `ewo_trend_day` |
| `ewoWeekTrend` (`trend(df_weekly)`) | `ewo_trend` (ewo.py, Wochen-DF) | `ewo_trend_wk` |
| `ewoMonthTrend` (`trend(df_monthly)`) | `ewo_trend` (ewo.py, Monats-DF) | `ewo_trend_mo` |
| `macdTrend` (`macd_trend(df)`) | `macd_trend` (macd.py) | `macd_trend` |
| `wkMacdTrend` (`macd_trend(df_weekly)`) | `macd_trend` (macd.py, Wochen-DF) | `macd_trend_wk` |
| `moMacdTrend` (`macd_trend(df_monthly)`) | `macd_trend` (macd.py, Monats-DF) | `macd_trend_mo` |
| `wkEwo` | `ewo` (ewo.py, Wochen-DF) | `ewo_wk` |
| `moEwo` | `ewo` (ewo.py, Monats-DF) | `ewo_mo` |

- `ewo.py`: neue Spalte `ewo_trend = np.where(ewo.diff() > 0, 1, -1)` (Richtung
  ggü. Vortag, ersetzt die alte `trend(df, id='ewo')`-Helperfunktion 1:1).
- `macd.py`: neue Spalte `macd_trend = (±0.5 je nach Richtung von macd_diff) +
  (±0.5 je nachdem ob macd > macd_signal)`, Wertebereich -1..+1 (ersetzt die alte
  `macd_trend(df)`-Helperfunktion 1:1).
- Die alten Modulfunktionen `trend()` und `macd_trend()` in `asset_perf2.py`
  wurden entfernt; `fill_pdict` liest die Werte jetzt per
  `DataUtils.safe_last(df[_weekly/_monthly], 'ewo_trend'/'macd_trend', ...)`.
- Auch die Spalten in `asset_simulation_.db`, `asset_simulation_{year}.db`,
  `asset_simulation_all.db` wurden per `ALTER TABLE ... RENAME COLUMN` migriert.

**DB-Dateinamen** (ab Branch `refactor/indicator-naming`):
- `asset_performance.db` → `asset_performance_.db`
- `asset_simulation.db` → `asset_simulation_.db`
- `asset_simulation{year}.db` → `asset_simulation_{year}.db`
- `asset_simulationall.db` → `asset_simulation_all.db`

---

## Bekannte Probleme & Backlog

### Prio 1 — Sicherheit (vor Produktiv-Einsatz zwingend)

**A) `eval()` auf Benutzereingaben (Code-Injection-Risiko)**

Wer die Config-DB schreiben kann (z.B. über den SQLite-Editor in der App), kann
beliebigen Python-Code ausführen.

| Datei | Zeile | Problem |
|---|---|---|
| `asset_analyzer.py` | 336 | `tickers = eval(ti)` |
| `banner_page.py` | 43 | `eval(sys_conf.get_value('multi_transactions', …))` |
| `multi_transaction.py` | 404, 504, 508 | `eval()` auf Buy/Sell-Bedingungen aus Config-DB |
| `system_config.py` | 119, 132, 158 | `eval()` auf Overlay/Oszillator-Werte aus DB |
| `indicator/indicator.py` | 123, 130 | `eval()` auf transformierte Ausdrücke |
| `tools.py` (BuySellSignalGenerator) | 718, 760, 768 | Fallback-`eval()` auf rohe Sell-Conditions |

**Fix-Ansatz:** Config-Werte als JSON speichern (`json.loads` statt `eval`). Der
`ExpressionEvaluatorNew.validate_and_transform()` ist bereits vorhanden — kein
Fallback auf bare `eval()` mehr verwenden.

**B) `pd.json.loads` existiert nicht (`fetch_data.py:113`)**
```python
# Falsch — pd hat kein json-Modul:
df[col] = df[col].apply(pd.json.loads)
# Richtig:
df[col] = df[col].apply(json.loads)
```
Fehler wird durch `except Exception: pass` verschluckt → JSON-Spalten in
`asset_info` werden **nie deserialisiert** (stilles Datenproblem).

---

### Prio 2 — Stabilität (kurzfristig)

**C) sqlite3-Verbindungen ohne garantiertes `.close()` bei Exceptions**

Viele Klassen öffnen Connections in `__init__` ohne `try/finally`:
- `asset_simulator.py:417` — `self.ticker_conn`, `self.info_conn`
- `market_map.py:28,55` — `self.ticker_conn`, `self.info_conn`
- `performance_details.py:69,95,211` — `self.ticker_conn`, `self.info_conn`
- `ticker_tools.py:315,411` — lokale `conn` ohne `try/finally`
- `live_ticker.py:89,106,208,234,245` — alle ohne `try/finally`
- `search.py:31,212` — ohne `try/finally`
- `sqlite_editor.py:31,53,70,95` — ohne `try/finally`

**Fix:** `with sqlite3.connect(...) as conn:` oder `try/finally: conn.close()`

**D) `DataUtils._xrate_cache` ist unbegrenzt**

Klasssen-Variable in `utils.py` wächst endlos in langen Sessions.
**Fix:** `_put_cache()` aus `market_data.py` wiederverwenden, max. 256 Einträge.

**E) Strftime-Tippfehler in `pushover_notifier.py:27`**
```python
# %M = Minuten (falsch), %m = Monat (richtig):
now = datetime.now().strftime("%Y-%M-%d 00:00:00")
```
`now` wird danach nicht verwendet (toter Code), aber der Tippfehler zeigt
ein Muster, das auch an echten Stellen auftreten könnte.

---

### Prio 3 — Code-Qualität (mittelfristig)

**F) 131 `print()`-Statements** — sollten durch `logger.debug/info/warning` ersetzt
werden. Structured Logging ist in ~15 Dateien bereits vorhanden.

**G) 134x `pandas inplace=True`** — in Pandas 2.0+ deprecated, wird irgendwann
entfernt. Migration: `df = df.sort_values(...)` statt `df.sort_values(inplace=True)`

**H) Zwei parallele `ExpressionEvaluator`-Klassen in `tools.py`**

`ExpressionEvaluator` (alt) und `ExpressionEvaluatorNew` (neu) koexistieren.
`BuySellSignalGenerator` probiert zuerst den neuen, fällt auf den alten zurück —
inkonsistentes Verhalten je nach Expression.
**Fix:** Alten Evaluator entfernen, sobald der neue alle Fälle abdeckt.

**I) Zwei `PortfolioAnalysis`-Dateien** — `PortfolioAnalysis.py` und
`PortfolioAnalysis1.py` liegen parallel. Unklar welche aktuell ist → eine löschen.

**J) Stale `.tmp.*`-Dateien** in `tradinglib/` bereinigen:
```
tradinglib/market_data.py.tmp.19064.*
tradinglib/multi_transaction.py.tmp.19064.*
tradinglib/tools.py.tmp.19064.*
tradinglib/search.py.tmp.19064.*
```

---

### Prio 4 — Architektur (langfristig)

**K) `tools.py` ist ein God Object** — enthält Utilities, SQLite-Wrapper,
Streamlit-Helpers, zwei Evaluatoren und einen Signal-Generator. Schwer testbar.
Aufteilen in: `db_tools.py`, `st_tools.py`, `expression_eval.py`.

**L) Keine `st.session_state` für DB-Verbindungen** — Klassen wie `AssetSimulator`,
`MarketMap`, `PerformanceDetails` öffnen Connections in `__init__`. Bei jedem
Streamlit-Rerun wird eine neue Instanz gebaut → Connection-Leak.
**Fix:** Instanzen in `st.session_state` cachen.

**M) Keine Tests** — kein einziger Test im Repository. Für eine App, die
Handelsentscheidungen trifft, ist das das größte Stabilitätsrisiko.
Einstieg: `pytest` + Smoke-Tests für `fetch_data.py` und `tools.py`.

**N) `Scheduler` nicht thread-safe genug** — `check_same_thread=False` mit nur
einem `Lock`, aber `load_schedule_from_db` und `save_schedule_to_db` sind nicht
beide unter dem Lock geschützt.

---

## asset_perf2.py — Fast-Modes (backfill / rescore)

Nach einem `init`-Lauf können fehlende Indikator-Spalten ohne Neuberechnung befüllt werden:

```bash
# 1. Fehlende Spalten aus lokaler OHLCV auffüllen (kein Internet):
python asset_perf2.py /backfill:heikin,markov,macd
python asset_perf2.py /backfill:heikin,markov,macd /year:2024
python asset_perf2.py /backfill:heikin,markov,macd /all

# Bekannte Indikatoren für --backfill (siehe INDICATOR_BACKFILL_MAP):
#   heikin  → ha_close, ha_open, ha_ema_high, ha_ema_low
#   markov  → markov_regime
#   macd    → macd, macd_diff, macd_signal, macd_trend
#   rsi     → rsi, rsi_ema, momentum
#   ewo     → ewo, ewo_ema, ewo_diff, ewo_angle, ewo_trend
#   adx     → adx, adx_plus, adx_minus, adx_angle
#   dema    → dema_ema_fast, dema_ema_slow, dema_buy, dema_sell
#   hor     → hor_val, hor_threshold
#   sup     → sup_support, sup_resistance
#   relvol  → relvol_ratio
#   atc     → atc_top_high, atc_bot_low

# 2. Scores neu berechnen (verwendet gespeicherte DB-Spalten + asset_info):
python asset_perf2.py /rescore
python asset_perf2.py /rescore /year:2024
```

**Hinweis:** `macd_signal` ist in allen 8 `asset_simulation_*.db` via `ALTER TABLE ADD COLUMN` ergänzt (Wert=0).
Nach `--backfill macd` werden echte Werte eingetragen.

---

## Coding-Konventionen (bisher beobachtet)

- SQLite-Zugriff idealerweise über `Db_tools` (aus `tools.py`) — direkte
  `sqlite3.connect()`-Calls sind Legacy und sollten schrittweise ersetzt werden
- Parameterisierte Queries: immer `?`-Platzhalter + `params=(value,)` — kein f-String
  mit Benutzerwerten
- Exception-Handling: `except Exception:` (nie bare `except:`) — spezifische Typen
  wo bekannt (`ValueError`, `TypeError`, `IndexError`, `KeyError`, `AttributeError`)
- Cache-Dicts: mit `_put_cache(cache, key, value)` aus `market_data.py` befüllen,
  max. 512 Einträge
- Logging: `logger = logging.getLogger(__name__)` pro Modul, kein `print()` in Prod-Code

---

## Git-Workflow

```bash
# Neues Feature:
git checkout -b feature/mein-feature
# ...
gh pr create --title "feat: ..." --body "..."

# Bugfix:
git checkout -b fix/mein-fix
```

GitHub-Repo: https://github.com/the-trading-tool/trading

---

## Neu in dieser Session (2026-06-01)

### Umgebungen
- **Dev/Test:** `C:\Users\kurtl\Claude\Trading` und `C:\Users\kurtl\trading.cloogidoo.com.V2`
- **Produktion:** `C:\Users\kurtl\trading.cloogidoo.com` — **NICHT anfassen**
- Nach jeder Änderung in `Claude\Trading` die geänderten Dateien nach `trading.cloogidoo.com.V2` kopieren
- Korrekte pip-Installation: immer `.venv\Scripts\python.exe -m pip install ...` (nicht `.venv\Scripts\pip.exe`)
- Datenbank-Pfad wird via `TradingDB`-Env-Var überschrieben; Produktions-DBs liegen in `C:\Users\kurtl\Development\database\`

### KI-Integration (`tradinglib/ai_client.py`, `tradinglib/banner_ai.py`, `tradinglib/gemini_api.py`)

**`ai_client.py`** — Unified AI Client mit Provider-Fallback:
- `AiClient(provider='auto'|'groq'|'gemini'|'ollama')` — Provider-Auswahl via `config.db` key `ai_provider`
- Fallback-Reihenfolge: Groq → Gemini → Ollama
- `GeminiRateLimitError` = Alias auf `AiRateLimitError` (Rückwärtskompatibilität)
- Groq-Modelle: `llama-3.3-70b-versatile` → `llama-3.1-8b-instant` → `gemma2-9b-it`
- Gemini-Modelle: `gemini-2.0-flash-lite` → `gemini-2.5-flash-lite` → `gemini-2.5-flash` → `gemini-2.0-flash`
- Ollama: lokale Instanz unter `http://localhost:11434`, Modell aus ksp-Entry `ollama`
- KSP-Keys: `gapi` (Gemini), `groq` (Groq), `ollama` (URL+Modell)
- Installed in both venvs: `groq>=1.4.0`, `google-genai>=2.7.0`

**`banner_ai.py`** — Auto-Analyse des letzten Trade-Signals:
- `BannerAiGenerator.run(force=False)` — liest `trades{year}.db` → Top-Asset → Simulation → Gemini/Groq-Analyse → `banner_notes.db`
- `BannerAiGenerator.build_debug_info()` — sammelt alle Daten ohne API-Call (für Debug-Ansicht in Admin)
- Datenquellen: `trades{year}.db` (letzter Kauf), `asset_simulation_all.db` (85 Spalten), `asset_info.db`, `yf_<ticker>.db`
- Upsert in `banner_notes.db`: alte Einträge für denselben Ticker werden gelöscht
- `buyDate`-Spalte wird via `ALTER TABLE ADD COLUMN` nachgerüstet falls sie fehlt

**`gemini_api.py`** — Backward-Compat-Wrapper um `ai_client.AiClient`

**Admin-UI (`admin.py`):**
- Banner Note Expander: Provider-Dropdown, Debug-Checkbox, Analyse-Button
- Debug-Ergebnis wird in `st.session_state['_bn_dbg']` gespeichert (überlebt Reruns)
- `from tradinglib import system_config as sysconf` ist als Import vorhanden (wurde in dieser Session ergänzt)

### Banner Page (`tradinglib/banner_page.py`, `locales/en.json`, `locales/de.json`)

**Struktur:**
1. 🌐 Sprachauswahl (Deutsch als Default, gespeichert in `config.db` key `language`)
2. 4 Metriken: Startkapital · Portfoliowert · Performance · Max. Positionen
3. Strategien-Label + Indizes-Label (korrekte Terminologie)
4. Expander "Wie die Strategie funktioniert" mit Detail-Tabelle
5. KI Trading-Tipp aus `banner_notes.db`
6. Trades-Tabelle + Cumulative-Gain-Chart
7. **Strategie-Performance-Analyse** (Kennzahlen je Strategie aus `trades{year}.db`)
8. **Disclaimer-Expander** (zugeklappt)

**Terminologie (wichtig!):**
- Äußere `multi_transactions`-Keys = **Strategie-Namen** (z.B. "Value Trend Strategy")
- Innere Keys = **Index-Namen** (z.B. "SPX", "GDAXI")
- `num_assets`-Gesamtmenge = Summe der **Maxima** pro Index über alle Strategien
  (Wenn Strategy1/SPX=3 und Strategy2/SPX=4 → 4 wird addiert, nicht 3+4)

**Strategie-Kennzahlen (`_render_strategy_analysis`):**
- Offene Positionen, Profit-Faktor, Trefferquote, Ø Gewinn/Trade, Realisierter Gewinn
- Längste Gewinn-/Verlustserie
- Beste/Schlechteste Trades als 2-Spalten-DataFrame (3M + 1J)
- Automatischer `⚠️ Geopolitik-Hinweis` bei Strategien die "value trend" im Namen enthalten

**Strategien sind 2019–heute getestet, auch 2020 positiv:**
- Support/Resistance Strategy: PF 16.77, Win-Rate 75% (84 Trades)
- Value Trend Strategy: PF 3.02, Win-Rate 50.6% (233 Trades)

### Nächste Session — Priorisierte Optimierungen

**Prio 1 — Pushover-Alert bei neuem Buy-Signal** ✅ implementiert
- **`tradinglib/signal_notifier.py`** — zentrales Notifier-Modul (kein Streamlit-/MTP-Overhead):
  - Liest Buys/Sells direkt aus `trades{year}.db`
  - Buy: `stop_loss`/`take_profit` aus `asset_simulation_all.db` + KI-Text aus `banner_notes.db`
  - Sell: `gainPct`/`gain` direkt aus trades-DB
  - Deduplication via `pushover_notifier.json` — kein Doppelversand
  - CLI: `python -m tradinglib.signal_notifier [/force] [/date:YYYY-MM-DD] [/user:admin]`
- **`tradinglib/premium/multi_transaction.py` `run_notifier()`** → dünner Wrapper auf `signal_notifier.run()`
- **`tradinglib/pushover_notifier.py`**: `%M`-Bug gefixt, alle `print()` → `logger`
- `asset_perf2.py` ruft weiterhin `mt.run_notifier()` auf — keine Änderung nötig
- **Hinweis:** `tradinglib/multi_transaction.py` in V2 ist eine Leiche (Datei nach `premium/` verschoben)

**Prio 2 — Portfolio-Overlap-Check** ✅ implementiert (auf Banner Page + Multi Strategies)
**Prio 3 — Monatliche Performance-Heatmap** ✅ implementiert (auf Banner Page + Multi Strategies + Strategy Finder)

### Shared Backtest Widgets (`tradinglib/backtest_widgets.py`)
Zentrales Modul — alle Widgets einmalig implementiert, von allen 3 Seiten importiert:
- `render_monthly_heatmap(df, region, system_currency)` — RdYlGn Heatmap Monat×Jahr
- `render_strategy_analysis(df, region, system_currency)` — KPI-Block je Strategy (Banner + Multi)
- `render_portfolio_overlap(df, region)` — WARNING bei Doppelpositionen (Banner + Multi)
- `render_compact_analysis(df, region, system_currency)` — KPIs ohne Strategy-Gruppierung (Strategy Finder)

Einstiegspunkte:
- `banner_page.py` — delegiert via `_render_*`-Wrapper-Methoden
- `premium/multi_transaction.py` — Block nach Cumulative-Gain-Chart (vor `buy_gain = float(...)`)
- `premium/asset_simulator.py` — Block nach `total_gain_expander`, vor Parity-Render

### In dieser Session erledigt (2026-06-02)

**DB-Optimierungen:**
- `open_db()` in `tools.py` — zentraler SQLite-Wrapper mit WAL, synchronous=NORMAL, mmap, cache (64 MB)
- Alle ~64 raw `sqlite3.connect()`-Calls auf `open_db()` migriert (22 Dateien)
- `@st.cache_resource` für `market_map.py` + `performance_details.py` — Connection-Reuse über Reruns
  - **Wichtig:** `check_same_thread=False` nötig, da Connection thread-übergreifend genutzt wird
- `Scheduler`: `Lock()` → `RLock()`, `load_schedule_from_db` + `save_schedule_to_db` unter Lock

**Code-Qualität:**
- `eval()` → `ast.literal_eval()` in allen Config-DB-Lese-Stellen (banner_page, banner_ai, multi_select, system_config, asset_analyzer)
- `pd.json.loads` → `json.loads` in fetch_data.py
- `print()` → `logger.*` in 47 Dateien (84 → 8 intentionale Print-Calls)
- `inplace=True` → `df = df.method()` in 109 → 0 aktiven Stellen
- `ExpressionEvaluatorNew` → `ExpressionEvaluator` (alte Klasse gelöscht)
- `PortfolioAnalysis1.py` gelöscht, 133 `.tmp.*`-Dateien gelöscht
- `_xrate_cache` bounded auf 256 Einträge
- `duckdb>=1.0.0`, `scipy>=1.11.0`, `tqdm>=4.0.0` in `requirements.txt`

**DuckDB-Integration:**
- `duckdb` installiert in beiden venvs (V2 + Claude\Trading)
- `_duckdb_fetch_years()` in `performance_details.py` — analytische Multi-Jahr-Engine
  - `latest_only=True` für Performance-Seite (1 Zeile/Ticker)
  - `latest_only=False` für Simulation (alle Zeilen = Zeitreihe)
- **Wichtig:** nach duckdb-Installation Streamlit neu starten (Import-Cache!)
- Datenbank-Namenskonvention: `asset_simulation_.db` = aktuelles Jahr (kein Suffix!), `asset_simulation_2025.db` = 2025

**Strategy Finder — Multi-Jahr:**
- `selectbox` → `multiselect` für Jahresauswahl in `asset_simulator.py`
- `📅 Stand per` Datumspicker für historische Snapshots (Backtesting)
- Multi-Jahr-Loop via SQLite: für jedes Jahr `fetch_combined_data_with_attach()` + `pd.concat()` + `drop_duplicates()`
- DuckDB als optionale Beschleunigung (kein Hard-Requirement)

**Performance-Seite:**
- `selectbox` → `multiselect` in `performance_details.py`
- DuckDB-Cache via `@st.cache_data(ttl=300)`
- Thread-Bug behoben: `check_same_thread=False` in `_cached_ticker_conn` + `_cached_info_conn`

**Backlog-Status (alle Prio 1-3 erledigt):**
- ✅ 1-A: eval() → ast.literal_eval
- ✅ 1-B: pd.json.loads fix
- ✅ 2-C: sqlite3 Connections via open_db
- ✅ 2-D: _xrate_cache bounded
- ✅ 2-E: strftime Bug (war schon gefixt)
- ✅ 3-F: print() → logger
- ✅ 3-G: inplace=True
- ✅ 3-H: ExpressionEvaluator konsolidiert
- ✅ 3-I: PortfolioAnalysis1.py gelöscht
- ✅ 3-J: .tmp-Dateien gelöscht
- ✅ 4-N: Scheduler thread-safe (RLock)
- ⬜ 4-K: tools.py God Object (große Refaktorierung)
- ⬜ 4-M: Keine Tests
