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
| `tradinglib/providers/` | Austauschbare Marktdaten-Provider (`yahoo`/`fmp`/`eodhd`) hinter `get_provider()`; aktive Quelle = `_app:data_provider` in `config.db` |
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

### OHLC in `asset_simulation` (2026-07, Commit `ddde705`)

Roh-OHLC ist in `asset_simulation` als **`Open`/`High`/`Low`/`close`** verfügbar
(Ausnahme von der snake_case-Regel — großgeschrieben `Open/High/Low`).
→ In Buy/Sell-Queries direkt nutzbar, z. B. `(close > Open) & (High > sup_resistance)`.

> **⚠️ SQLite ist bei Spaltennamen case-INSENSITIV.** Das Großschreiben von `Open`
> verhindert **nicht** die Kollision mit `asset_info.open` — für SQLite sind `Open`
> und `open` derselbe Bezeichner. Sobald `asset_simulation` mit `asset_info` gejoint
> wird (z. B. All-Assets-Screener, `all_assets.py`), ist unqualifiziertes `Open`/`High`/
> `Low` in der `WHERE`-Klausel **mehrdeutig** → `ambiguous column name: Open`, die
> ganze Query crasht. Fix in `all_assets.py`: der JOIN exponiert per Subquery nur
> `ticker/longName/exchange` aus `asset_info`, damit `asset_info.open/dayHigh/dayLow`
> nicht im Scope sind. Bei neuen Joins mit `asset_info` dieselbe Vorsicht — entweder
> Spalten qualifizieren (`ap.Open`) oder den Join auf die gebrauchten Spalten narrowen.

- **Umbenannt:** `dayHigh → High`, `dayLow → Low` (in `asset_perf2.py` pdict +
  `score_df: col('Low')`; alle 9 `asset_simulation_*.db` per `ALTER TABLE RENAME COLUMN`).
- **Neu:** `Open` (pdict `safe_last(df,'Open')`; DB-Spalte per `ADD COLUMN`).
- **Kein** rohes `open` (lowercase) — das wäre ein Kollisionsname zu `asset_info.open`.
- Bestandsdaten via `python asset_perf2.py /backfill:ohlc /force` gefüllt
  (Map-Eintrag `'ohlc': ['Open','High','Low']`; liest lokale OHLCV, kein Yahoo).
  **Achtung Windows/Git-Bash:** `/force` wird von MSYS in einen Pfad gemangelt →
  `MSYS2_ARG_CONV_EXCL='*'` voranstellen oder über PowerShell laufen lassen.
- **Vintage-Hinweis:** Der Backfill lädt `Open/High/Low` frisch, `close` bleibt der
  alte Sim-Wert → bei Split-/Adjust-Tickern passt `close` ggf. nicht zu `O/H/L`
  (~0,2 % Zeilen). Heilt beim nächsten vollen `init`-Lauf (alle vier aus einem Fetch).

| Alte(r) Name(n) | Neuer Name | Indikator |
|---|---|---|
| `RelVol_Ratio`, `RelVol_Current`, `RelVol_Past`, `AdjVolume` | `relvol_ratio`, `relvol_current`, `relvol_past`, `relvol_adj_vol` | relvol |
| — | `relvol_direction` | relvol (neu) |
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
| `atc_high` | `atc_top_high` | atc (df-Spalte + Backfill-Map waren schon korrekt) |
| `atc_low` | `atc_bot_low` | atc (df-Spalte + Backfill-Map waren schon korrekt) |

`atc`-Migration (2026-07-02): Der Hauptlauf schrieb `atc_high`/`atc_low`, während
`INDICATOR_BACKFILL_MAP`/`atc.py` schon `atc_top_high`/`atc_bot_low` verwendeten →
8 der 9 `asset_simulation_*.db` hatten **beide** Paare. Migration: bestehende
Backfill-Spalten `atc_top_high`/`atc_bot_low` gelöscht, dann `atc_high`/`atc_low`
per `RENAME COLUMN` an ihre Stelle (Hauptlauf-Datenspur bleibt kanonisch). Hinweis:
die beiden Spuren wichen inhaltlich ~92 % ab (Sim-vs-OHLC-Vintage). Der Backward-
Compat-Alias in `atc.py` (`self.df['atc_high']=atc_top_high` u. `atc_low`) bleibt
als reine Live-DF-Bequemlichkeit bestehen — er wird nicht mehr ins pdict/DB gelesen.

Hinweis: `relvol_ratio_wk`/`relvol_ratio_mo` stammen bereits architektonisch aus
`relvol.py` — `fetch_data.fetch_data()` instanziiert die `Relvol`-Klasse für jedes
Timeframe (1d/1wk/1mo) separat; `asset_perf2.py` liest lediglich den letzten Wert
der Spalte `relvol_ratio` aus `df_weekly`/`df_monthly` (analog zu `ewo_wk`/`ewo_mo`
aus `ewo.py`). Es war also keine zusätzliche Berechnung in `relvol.py` nötig,
sondern nur die Vereinheitlichung der pdict-Schlüssel.

Neu: `relvol_direction` (+ `_wk`/`_mo`) zeigt an, **ob** das Relativvolumen von
Käufen oder Verkäufen ausgelöst wurde: `+1` = Close>Open (kauf-getrieben),
`-1` = Close<Open (verkauf-getrieben), `0` = Doji. Deckt sich mit der Balkenfarbe
in `relvol.add_fig()`. In Formeln nutzbar wie `relvol_ratio > 1.5 & relvol_direction
> 0`. Verdrahtung identisch zu `relvol_ratio`: Spalte in `relvol.py`, pdict +
`INDICATOR_BACKFILL_MAP['relvol']` in `asset_perf2.py`; `_wk`/`_mo` kommen aus der
Pro-Timeframe-Instanziierung (kein TF-Backfill-Eintrag → nur beim vollen `init`,
nicht via `/backfill:relvol`). Neue DB-Spalten legen `bulk_upsert_dicts` bzw.
`_ensure_sim_columns` selbst an — kein manuelles `ALTER TABLE` nötig.

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

**M) Testabdeckung noch dünn** — erste Tests existieren unter `tests/`
(`pytest tests/ -q`, pytest 9.x im venv):
- `tests/test_signal_mask.py` — BuySellSignalGenerator / Multi-Strategies-Masking
- `tests/test_index_normalization.py` — DatetimeIndex-Sort/Dedup (ensure_datetime_index, Bsz)

Für eine App, die Handelsentscheidungen trifft, bleibt die Abdeckung das größte
Stabilitätsrisiko. Nächste sinnvolle Ziele: `fetch_data.load_price_data`
(Lade-Pipeline) und `tools.py` (Evaluator/Db_tools).

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
- Der frühere automatische `⚠️ Geopolitik-Hinweis` für Strategien mit "value trend" im
  Namen wurde 2026-09-05 entfernt (Locale-Key `banner.hint_global_tensions` gelöscht) —
  die Strategie lieferte über alle Jahre die besten Ergebnisse, der pauschale Warnhinweis
  passte nicht dazu. Der allgemeine Disclaimer-Expander bleibt.

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

---

## Neu in dieser Session (2026-06-13) — Ticker-Auswahl & paralleler Download

### `^`-Konvention für die Index-Auswahl (wichtig!)
In der `indices`-Tabelle (`yf_tickers.db`) beginnen **echte Börsen-Indizes
ausnahmslos mit `^`** (`^GDAXI`, `^MDAXI`, `^SPX`, `^N225`, … — 16 Stück).
Alles ohne `^` sind **Kategorie-Gruppen**: `INDEX`, `COMMODITIES`, `METALS`,
`CURRENCIES`, `CRYPTO` und neu `ETP` (3376 Mitglieder).

`/index_member` (get_asset_data.py) bzw. der Default-Lauf (asset_perf2.py)
filtern die Gruppenliste jetzt hart auf den `^`-Präfix:
```python
filtered = [name for name in filtered if name.startswith('^')]
```
→ robuster als die `NON_STOCK_GROUPS`-Blockliste: jede künftige Nicht-Index-Gruppe
ist automatisch ausgeschlossen, ohne Listenpflege. **`NON_STOCK_GROUPS` wurde
bewusst NICHT um ETP erweitert** — die Konstante hat zwei gegensätzliche
Verwendungen (Blockliste in `/index_member`, Auswahlliste in `/index`/`index_only`);
ETP dort einzutragen hätte 3376 ETPs in den `/index`-Pfad und die Sim gezogen.

### `/group:NAME` jetzt auch in asset_perf2.py
`asset_perf2.py` wertet `args.get('group')` jetzt aus (Parsing war in `cli.py`
längst vorhanden). Damit volle Parität mit get_asset_data.py:
- Default / `/index_member` → nur `^`-Indizes (998 Ticker)
- `/group:ETP` → ETPs (3376) im **eigenen Lauf** (so gewollt)
- `/group:CURRENCIES` → die 4 `=X`-FX-Paare
- `/inverse` → die **3650** Ticker ohne jede Gruppe (Einzeltitel wie BABA, AMC) —
  NICHT redundant zu `/group`, „keine Gruppe" ist kein Gruppenname → bleibt erhalten
- `/index:NAME` (asset_perf2) wertet jetzt **nur** den genannten Index aus; das alte
  pauschale `OR i.name IN (NON_STOCK_GROUPS)` war ein Relikt aus der Zeit vor `/group`
  und wurde entfernt.

Aus `get_asset_data.py` `/index_member` zusätzlich das `OR s.Ticker LIKE "%=X"`
entfernt (FX-Paare hängen alle an `CURRENCIES` → über `/group:CURRENCIES` erreichbar).

### Paralleler Download in get_asset_data.py (`/worker:N`)
Download-Schleife auf `ThreadPoolExecutor` umgestellt; `/worker:N` (Default 1).
yfinance ist **nicht threadsafe** (modul-globales `yfinance.shared._DFS`) → ein
`threading.Lock` serialisiert `yf.download` in `yahoo_provider.py` und im Fallback
von `market_data.py`. Empfehlung 2–4 Worker (Yahoo HTTP 429). asset_perf2.py nutzt
weiterhin `ProcessPoolExecutor` (CPU-lastig), get_asset_data ThreadPool (I/O).

Commits: `8137c55` (parallel download), `26e12fb` (get_asset_data ^-Filter),
`0d3c0ca` (asset_perf2 ^-Filter + /group). HELP-Seiten `get_asset_data.html` /
`asset_perf2.html` entsprechend aktualisiert.

### Daten-Pipeline: wann werden importierte Assets in der App sichtbar?
Drei DBs, drei Stufen — wichtig zu verstehen, warum ein frisch importierter Markt
(z.B. ETP) zwar im „Select by market" steht, aber „No options to select" bei den
Firmen zeigt:
1. **`get_asset_data.py`** (Excel-Default oder `/group`) → `yf_tickers.db`
   (stocks/indices/stock_indices) + `yf_<TICKER>.db` (OHLCV).
   → Der **Markt** erscheint sofort (`MarketSearch.get_index_list()` liest
   `SELECT name FROM indices`).
2. **`get_asset_info.py`** → `asset_info.db` (Stammdaten).
   → Erst jetzt füllt sich der **„Select company"-Dropdown** und die Volltextsuche.
   Grund: `make_query(q=7)` nutzt `INNER JOIN info_db.asset_info ON yt.Ticker =
   ai.ticker` ([make_query.py](tradinglib/make_query.py)) — ohne asset_info-Zeile
   kein Dropdown-Eintrag. q=7 joint NICHT auf asset_simulation, d.h. asset_perf2
   muss dafür noch nicht gelaufen sein.
3. **`asset_perf2.py`** → `asset_simulation_.db` (Scores/Signale).
   → Chart-Overlays, Buy/Sell, Kennzahlen-Panel.

**Volltextsuche** (`FullTextSearch`, search.py) liest `asset_info_fts` (FTS5 aus
`asset_info`). `create_fts_table()` befüllt nur, wenn leer. `get_asset_info.py`
baut die FTS-Tabelle am Ende automatisch neu auf (`rebuild_fts_table()`, direktes
SQL, kein Streamlit-Import) → neue Ticker sind sofort volltextsuchbar. Admin-Button
„Update index" (`update_fts_table()`) bleibt als Fallback.

### get_asset_info.py: `/group:NAME` + `/worker:N` ergänzt
Vorher las get_asset_info immer die volle Liste (~8000 Ticker). Jetzt:
- `/group:ETP` → nur Mitglieder dieser Gruppe(n) (via `build_ticker_list(group)`,
  Query mit `UPPER(i.name) IN (...)`) — gezieltes Nachladen statt alles.
- `/worker:N` → parallele Info-Downloads (ThreadPool). `.info/.financials/
  .balance_sheet` sind pro Ticker eigene Requests → threadsicher, **kein** Lock
  nötig (anders als `yf.download`). row_maps werden im Main-Thread per
  `as_completed` eingesammelt, einmal `bulk_upsert_dicts` am Ende.
- Schleifenkörper in `fetch_info_for(ticker)` ausgelagert; Flush sauber in
  `__main__` (vorher Modul-Ebene). HELP `get_asset_info.html` / `get_asset_data.html`
  um Pipeline-Tabelle erweitert.
- **FTS-Auto-Rebuild:** `rebuild_fts_table(conn, 'asset_info')` baut am Ende die
  `asset_info_fts`-Suchtabelle neu (DROP/CREATE fts5(ticker, longName)/INSERT) →
  neue Ticker sofort volltextsuchbar, kein Admin-„Update index" mehr nötig. Struktur
  identisch zu search.py, damit die App die Tabelle übernimmt. In try/except, damit
  ein Rebuild-Fehler den bereits committeten Upsert nicht gefährdet.

### Fix: `/add_current` in asset_perf2 war wirkungslos
Im `ProcessPoolExecutor`-Aufruf war das 3. Positionsargument (`add_current`) hart
auf `False` verdrahtet → die geparste `/add_current`-Option erreichte
`process_symbol` nie (aktuelle, noch nicht geschlossene Tageskerze wurde nie
ergänzt). Jetzt wird die Variable durchgereicht.

### ✅ cli.parse_args-Lowercasing-Bug gefixt
`cli.parse_args` schrieb `pref = (argv[i][1:]).lower()` über das **ganze** Argument
klein, also auch den Wert nach `:` → case-sensitive Werte gingen verloren
(`/index:^SPX` → `^spx` matchte `^SPX` nicht; `/select:… "%.MC"` → `%.mc`).
**Fix (commit s.u.):** erst auf `:` splitten, dann nur den Key (`pref`) lowercasen,
den Wert (`suf`) im Original belassen. Case-insensitive gewollte Werte werden
gezielt normalisiert: `group → .upper()` (+ Query `UPPER(i.name)`),
`backfill → .lower()` (Map-Keys sind lowercase). Verifiziert: `/index:^SPX` → 515,
`/select:… "%.MC"` → 30 Treffer (vorher je 0).

---

## Neu in dieser Session (2026-06-28) — Broker-Handelbarkeits-Filter

Ziel: Signale auf die beim eigenen Broker **tatsächlich orderbaren** Ticker
einschränken (Auslöser: Scalable Capital handelt nicht jeden ^RUT-Small-Cap).

**`tradinglib/broker_tradability.py`** — broker-agnostischer Filter (Plugin-Muster):
- Checker je Broker: `scalable` (Proxy `unofficial-scalable-capital-api`,
  `GET /securities/{isin}/buyable`), `alpaca` (`/v2/assets`-Liste), `ibkr`
  (permissiv: SMART deckt faktisch alles; `config.db ibkr_exclude`), `none`
  (kein Filter, Default). Aktiver Broker: `config.db '<user>:broker'`.
- Öffentliche API: `check_tradable(tickers, broker_id=None)` → `{ticker:
  Tradability}`; `filter_tradable(...)` → `{tradable, not_tradable, unknown}`.
  `drop_unknown=False` (Default) lässt Unbekannte durch (kein Signalverlust bei
  Broker/Proxy-Ausfall); `drop_unknown=True` = strikt.
- Cache: `asset_info.db.broker_tradability_cache` (PK broker+ticker), Re-Check
  nach `REFRESH_DAYS=7` → eine Online-Abfrage pro ISIN, danach offline.
- Scalable-Antwort wird defensiv geparst (`_parse_scalable_payload`); Proxy down
  / keine ISIN → `tradable=None`. Scalable-Config-Keys: `scalable_proxy_url`
  (Default `http://localhost:8080`), `scalable_gateway_token`.
- CLI: `python -m tradinglib.broker_tradability /index:^RUT|/tickers:A,B|/file:x.txt
  [/broker:scalable] [/strict] [/out:report.json]`. Ersetzt den Ad-hoc-
  `rut_*`-Workflow (die `.DE`-Yahoo-Heuristik in `rut_91_check.json` lieferte
  Fehltreffer wie IESC→tonies SE).

**ISIN-Quelle / -Lücke:** ISINs liegen in `yf_tickers.db.stocks.ISIN`
(`backfill_isin.py`). Ursprünglich nur ~43 % gefüllt — gerade junge Small-Caps
(PLUG, OKLO, RGTI…) fehlten, weil `yf.Ticker().isin` für sie nichts liefert.
**Stand 2026-08-28 nach den Backfill-Läufen: 6237/8858 = 70,4 %**
(gemessen gegen `C:\Users\kurtl\Development\database\yf_tickers.db`, gültig =
`ISIN NOT NULL AND TRIM(ISIN) NOT IN ('','None','nan')`; ohne Indizes/FX 70,7 %).
⚠️ **Mitglieder echter `^`-Indizes liegen mit 1977/3526 = 56,1 % deutlich darunter** —
also ausgerechnet die Menge, auf der Screener und Signale laufen. Wer `require_isin`
oder den Broker-Filter scharf stellt, verliert dort weiterhin rund 44 % der Kandidaten.
- `backfill_isin.py fetch_isin()` hat jetzt **Provider-Fallback** nach yfinance.
  (2026-07-15: nicht mehr FMP-fest — läuft über `get_isin_resolver()`, also FMP
  *oder* EODHD, je nach aktivem Provider/hinterlegtem Key. Param hieß `use_fmp`,
  jetzt `use_provider`; Helper `_get_fmp()` → `_get_isin_provider()`.)
- `tradinglib/providers/fmp_provider.py`: neue Methode `profile_isin(ticker)`
  (Inverse zu `search_isin`); `eodhd_provider.py` bietet beide ebenfalls.
- `IsinResolver` (in broker_tradability) liest `stocks.ISIN`, lädt Fehlende bei
  Bedarf nach und **schreibt sie zurück** → Lücke schließt sich progressiv.

**Einfacher `require_isin`-Vorfilter (verdrahtet):** Zusätzlich zum proxy-
basierten Broker-Check gibt es einen leichtgewichtigen Schalter, der die
Selektion auf Werte **mit gültiger ISIN** beschränkt — als Näherung für
„handelbar" ohne jede externe Abhängigkeit.
- Eingehängt in `AssetSimulator.fetch_combined_data_with_attach()`
  (asset_simulator.py) — der **gemeinsame Engpass**: Strategy Finder (Single-
  *und* Multi-Jahr via `_fetch_for_year`) UND Multi-Transactions
  (`multi_transaction.py:541`) laufen dort durch, ein Filter deckt alles ab.
- Schalter: `config.db` key `require_isin` (Default False). UI-Checkbox im
  Strategy-Finder-Sidebar („Nur handelbare (ISIN vorhanden)", Locale-Keys
  `sf.require_isin[_help]`), schreibt den Wert global in config.db (kein
  `on_change` → meidet das Overlay-Korruptions-Muster).
- **Global + per-Index-Override:** `fetch_combined_data_with_attach(...,
  require_isin=None)` — `None` = globaler Schalter, `True/False` = überschreibt
  global. Multi-Transactions liest pro Index das optionale Feld `require_isin`
  aus `multi_transactions` (analog `trailing_stop`) und reicht es durch; akzeptiert
  bool / `'yes'`/`'no'`/`'true'`/`'ja'`. Beispiel-Eintrag im Index-Dict:
  `'^RUT': { ..., 'order_by': 'sortino', 'require_isin': True }`. Fehlt das Feld
  → globaler Default. Strategy Finder ruft ohne Param → global.
- ISIN-Validierung: `AssetSimulator._has_valid_isin()` —
  Regex `[A-Z]{2}[A-Z0-9]{9}[0-9]`; NULL→'None'/'nan', Indizes (`^…`), FX (`=X`)
  fallen korrekt raus.
- **Wichtig:** erst nach vollem `python backfill_isin.py` (jetzt mit FMP-Fallback)
  aktivieren. Vor den Backfill-Läufen filterte er ~57 % bloß-noch-nicht-aufgelöste
  Werte (inkl. handelbarer Large-Caps) mit raus; Stand 2026-08-28 sind es
  **~30 % gesamt, aber ~44 % innerhalb der `^`-Index-Mitglieder** (Zahlen oben).
  „Keine ISIN" heißt also weiterhin eher „nicht aufgelöst" als „nicht handelbar".

**Noch offen:** Der proxy-basierte Scalable/IBKR-Filter (`broker_tradability.py`)
ist als Funktion/CLI nutzbar, aber noch **nicht** in den Live-Signal-Loop
eingehängt (Integrationspunkt: vor dem Trade-Insert). Scalable-Pfad braucht den
lokalen Proxy (Login/2FA, Session wird wiederverwendet).

---

## Neu in dieser Session (2026-07-15) — EODHD als dritter Datenprovider

Die Provider-Schicht kennt jetzt **drei** Quellen: `yahoo` (Default), `fmp`, `eodhd`.
Downstream (`market_data.py`, `fetch_data.py`, `get_asset_data.py`) blieb unverändert —
alles läuft weiter über `get_provider()`.

**`tradinglib/providers/eodhd_provider.py`** (neu, an `fmp_provider.py` angelehnt):
`download()`, `ticker_history()`, `search_isin()`, `profile_isin()`, `test_connection()`.
Key via KSP-Eintrag `eodhd`/`password`, Overrides unter `_app:eodhd_ticker_overrides`.

**Ticker-Format — der Hauptunterschied zu FMP.** EODHD verlangt **immer**
`CODE.EXCHANGE`, auch für US-Werte (`AAPL` allein liefert nichts). Indizes, Krypto
und FX laufen über virtuelle Exchanges:

| Yahoo | EODHD |
|---|---|
| `AAPL` | `AAPL.US` (Suffix wird angehängt) |
| `SAP.DE` | `SAP.XETRA` |
| `VOD.L` | `VOD.LSE` |
| `^GSPC`, `^GDAXI` | `GSPC.INDX`, `GDAXI.INDX` |
| `BTC-USD` | `BTC-USD.CC` |
| `EURUSD=X` / `JPY=X` | `EURUSD.FOREX` / `USDJPY.FOREX` |
| `CT=F` (Futures) | kein verlässliches Äquivalent → Override nötig |

Die meisten Börsensuffixe sind identisch (`.PA`, `.MI`, `.SW`, `.TO`, `.HK` …) →
`_SUFFIX_MAP` enthält **nur** die echten Abweichungen (`.DE→.XETRA`, `.L→.LSE`,
`.AX→.AU`, `.WA→.WAR`, `.KS→.KO`, `.SS→.SHG`, `.SZ→.SHE`, `.KL→.KLSE`, `.BD→.BUD`)
— Quelle ist die offizielle EODHD-Exchange-Liste, nicht geraten. Unsichere Kandidaten
(`.T`, `.NS`, `.BO`, `.SI`) sind bewusst **nicht** gemappt → über Overrides lösen.
`_eodhd_to_yahoo()` ist die Umkehrung (für die ISIN-Auflösung, deren Konsumenten
Yahoo-Ticker erwarten).

**Intervalle:** EODHD liefert intraday nur `1m`/`5m`/`1h`. Statt stillschweigend
5m-Kerzen zurückzugeben, wenn `15m` angefragt wurde, wird die nächstfeinere
Auflösung geholt und **lokal resampled** (`_resample_ohlcv`, O=first/H=max/L=min/
C=last/V=sum). Startdatum wird an die Historien-Limits geklemmt (1m ≈ 120 d,
5m ≈ 600 d, 1h ≈ 7200 d). `1d`/`1wk`/`1mo` → EOD-Endpoint mit `period=d/w/m`.

**Refactorings drumherum (Verhalten für Bestands-FMP-Nutzer unverändert):**
- `providers/__init__.py`: `_read_fmp_key` → generisches `_read_provider_key(name)`
  (der alte Name bleibt als Wrapper, `backfill_isin.py` importiert ihn). Neu:
  `KEYED_PROVIDERS`/`PROVIDERS`, `_build_keyed_provider()`, `get_eodhd_provider()`.
  Fehlender Key → weiterhin stiller Fallback auf Yahoo (nur Log-Eintrag).
- **`get_isin_resolver()`** (neu): ISIN-Auflösung war auf FMP verdrahtet
  (`scalable_import.py`, `backfill_isin.py`). Jetzt gewinnt der **aktive**
  Datenprovider, sonst der andere mit Key → ein EODHD-Key allein genügt, kein
  Zusatz-FMP-Key mehr nötig. Bei `data_provider = yahoo`/`fmp` bleibt FMP erster
  Kandidat → Bestandsverhalten identisch.
- `system_config.py`: der FMP-Block ist jetzt **ein** generischer Pfad über
  `KEYED_PROVIDERS` (Key-Status, Overrides, 🔌-Test). Locale-Keys `cfg.fmp_*` →
  `cfg.provider_*` mit `{provider}`-Platzhalter (de+en). Die FMP-Texte rendern
  wortgleich wie vorher; für Inline-Labels wird der Kurzname genutzt
  (`EODHD-Ticker`, nicht `EOD Historical Data (EODHD)-Ticker`).
- HELP `providers.html`/`providers_en.html` um EODHD-Abschnitt erweitert
  (Ticker-Tabelle, Intervalle, Rate-Limits, ISIN-Hinweis); Labels „Datenquellen
  (Yahoo / FMP / EODHD)" in `index.html`, `setup_scheduler*.html`, `system_config.py`.

**⚠ Noch nicht gegen die Live-API getestet** — im Dev-Env ist kein EODHD-Key
hinterlegt. Verifiziert wurde mit gemockten Responses (Ticker-Mapping hin/zurück,
EOD-/Intraday-Shaping, 5m→15m-Resampling, MultiIndex-Form, ISIN-Suche inkl.
Primary-Listing-Auswahl, Fallback ohne Key). Endpoints/Feldnamen stammen aus der
aktuellen EODHD-Doku. **Erster echter Call = eigentlicher Test:** Key als
KSP-Eintrag `eodhd` anlegen → 🏵-Konfiguration → 🔌 Verbindung testen.

**Rate-Limits:** EODHD-Free nur **20 Anfragen/Tag** (reines Testkontingent — ein
`get_asset_data.py`-Lauf sprengt das sofort), kostenpflichtig 100.000/Tag. Für
echte Hintergrund-Jobs ist FMP-Free (250/Tag) praktikabler.

---

## Neu in dieser Session (2026-07-29) — `ovt`-Indikator (Overall (Value) Trend)

Neuer Oszillator [`tradinglib/indicator/ovt.py`](tradinglib/indicator/ovt.py)
(Klasse `Ovt`), der die Engine-Scores `overallTrend` (technisch) und
`overallValueTrend` (Value/Fundamental) — plus `ovtEma{span}` (EMA über
`overallValueTrend`, gleiche Semantik wie `OvtEmaUpdater`) — **live** als
Sub-Plot bereitstellt. **Hybrid A+B, ohne Logik-Duplizierung:**

- **B (autoritativ):** liest `overallTrend`/`overallValueTrend` direkt aus
  `asset_simulation_*.db` per `ticker`+Datum, über mehrere Jahres-DBs
  (`asset_simulation_.db` = aktuelles Jahr, sonst `_{year}.db`). Verifiziert:
  **100 % exakter Match** gegen die gespeicherten Werte (AAOI, 145 Zeilen).
- **A (Fallback):** für Bars *ohne* gespeicherten Wert (aktueller/Intraday-Bar
  oder nie simulierter Ticker) ruft `data()` **`asset_perf2.score_df` selbst**
  auf (derselbe vektorisierte Scorer). Nur die live fehlenden Skalare
  (`vola`/`sharpe`/`sortino`/`logVola`/`wkTrend`/`roa` + `asset_info`-
  Fundamentals) werden mit denselben Helfern (`indicator.sharpe_ratio`,
  `log_return`, `trend_pct_df`, `asset_perf2.get_roa`) rekonstruiert.
  `score_df`/`get_roa` werden **lazy** importiert → kein Zirkularimport (das
  Modul liegt selbst im `indicator`-Paket).

**Nutzung:** erscheint automatisch in der Oszillator-Auswahl (Auto-Discovery).
Spalten `overallTrend`/`overallValueTrend`/`ovtEma9` sind **namensgleich zur
Backtest-Engine** → Buy/Sell-Formeln laufen live *und* im Backtest identisch.
Kein `INDICATOR_BACKFILL_MAP`-Eintrag nötig (die Spalten existieren in
`asset_simulation` bereits über `asset_perf2`).

**Einschränkung (bewusst):** Der A-Tail ist eine Näherung — `macd_trend_wk/mo`
und `moTrend` liegen live nicht vor, `score_df` zählt deren Gewicht bei Wert 0
→ leichte Dämpfung der *unsimulierten* Randbars. Historie über B ist exakt. Für
besten Tail ewo/macd/adx/rsi/heikin mit-aktivieren (sonst sieht `score_df` auch
deren Spalten als 0).

**⚠️ Oszillatoren dürfen keine Layout-Annotationen via `_add_hline_outside`
setzen.** Bug gefunden: `_add_hline_outside` erzeugt eine Annotation mit
`xref='paper'`/`x=0.0`. Der **Overlay**-Pfad in `tiny_chart` (Zeile ~344ff.)
überträgt Annotationen bewusst **ohne** `row/col` und erhält `xref='paper'` —
darum funktioniert der Helper bei Overlays (`sup`/`gan`/`fib`/`pre`/`pvt`). Der
**Sub-Plot-Pfad** (Zeile ~674) überträgt sie dagegen mit
`add_annotation(an, row=1, col=1)`, wodurch Plotly `xref='paper'`→`'x'`
umschreibt → `x=0.0` wird zur Epoch-Datumskoordinate **1970-01-01** und streckt
die geteilte `type="date"`-Achse (gesamter Chart 1970→heute, Daten rechts
zusammengequetscht). `ovt` nutzt deshalb reine `add_hline`-Referenzlinien (nur
Shapes, die als `x{row} domain` sauber übertragen werden). Latent: der Sub-Plot-
Annotations-Pfad in `tiny_chart` ist generell fehlerhaft (homed nach `row=1`
statt `row=row` + xref-Override) — bisher ohne Konsumenten.

---

## Neu in dieser Session (2026-08-17) — 4PS-Methode (4 Phase Sequence)

Neue Seite + Indikator + Backtest-Spalten für die "4 Phase Sequence"-Methode
(Trendfolge: bewährte Historie → Konsolidierung → Ausbruch → bestätigter Trend).

| Datei | Rolle |
|---|---|
| `tradinglib/four_ps.py` | Rechenkern (kein Streamlit): `compute()`, `analyze()`, `scan()`, `index_regime()`, `zigzag()`, CLI |
| `tradinglib/indicator/fps.py` | Overlay `fps` — lädt die volle lokale Tageshistorie, projiziert das Ergebnis auf die Chart-Zeitebene |
| `tradinglib/four_ps_page.py` | Seite (`?four_ps=true`): Index-Regime, Screener, Detail, Methode |
| `HELP/four_ps_page[_en].html` | Hilfeseite (in `system_config` unter "Hauptansichten" registriert) |
| `tests/test_four_ps.py` | Phasenlauf + Kausalitätstest auf synthetischen Kursen |

**Phasen-Logik (alles kausal):**
- Phase 1: Monats-Zickzack (25 % Umkehr) — ein Aufwärtsschub zählt erst mit
  **bestätigter** Gegenbewegung, nie am (dann noch unbekannten) Extrempunkt.
- Phase 2: längstes Wochenfenster (≥ 8 W) mit Spanne ≤ 25 % **und** nahe 52W-Hoch.
- Phase 3/4: Zustandsautomat auf **Tages**-Closes; Basislevel kommen aus
  `_to_daily(..., completed_only=True)`, also aus abgeschlossenen Wochen/Monaten
  (`PeriodIndex` + `shift(1)`) — deshalb kein Look-ahead.
- Verifiziert: `compute()` auf abgeschnittener Historie == `compute()` auf voller
  Historie für den überlappenden Teil (Test `test_no_look_ahead`, zusätzlich
  gegen echte Ticker geprüft).

**Spalten (live == Backtest):** `fps_phase` (0–4), `fps_best_trend`,
`fps_trend_gain`, `fps_base_high/_low/_weeks`, `fps_breakout`, `fps_buy`,
`fps_sell`, `fps_stop`, `fps_target`, `fps_rs`, `fps_dist_high`.
Verdrahtet in `asset_perf2.py`: `INDICATOR_BACKFILL_MAP['fps']`, `'fps'` in der
Indikatorliste von `process_symbol`, pdict-Schleife (NaN → 0.0).
**Bestandsdaten füllen:** `python asset_perf2.py /backfill:fps /force`
(+ `/year:YYYY` bzw. `/all`) — noch **nicht** gelaufen, das ist der nächste Schritt.

**Performance-Falle:** `scan()` läuft bewusst **single-threaded**
(`workers=1`). Gemessen an 60 SPX-Mitgliedern: 6,3 s mit 1 Worker, 21,6 s mit 8 —
die Arbeit ist CPU-gebunden (pandas + Zustandsautomat), Threads bringen nur
GIL-Contention. Ergebnis wird tageweise in `rotation_cache.db` persistiert
(`rotation_cache.four_ps_key`), ein SPX+DAX-Scan dauert einmalig ~70 s.

**Sonstiges:** Sidebar-Eintrag `four_ps` (Gruppe Assets, Default an), Route in
`_START_PAGE_ROUTES` + `app_edition._ROUTE_PARAMS`, 98 Locale-Keys je Sprache
(`fps.*`, `nav/page/error.four_ps`). Parameter pro Nutzer in `config.db`
(`fps_params`), Universen-Default `^GDAXI,^MDAXI,^SDAXI,^SPX` (`fps_universes`).

### 4PS-Nachbesserung: zwei Filter, die die Methode erst tragfähig machen

Erster Praxistest zeigte Kaufsignale in Abwärtstrends (Beispiel KMB 2026-07-02:
Basis 92,42–111,82, Kurs 26 % unter dem Rekordhoch, **fallender** 30W-SMA):

1. **`near_high` lief gegen das 52-Wochen-Hoch.** Das sinkt mit dem Kurs mit → nach
   einem Jahr Abwärtstrend ist „20 % unter dem 52W-Hoch" praktisch immer erfüllt.
   Jetzt gegen ein **Rekordhoch-Fenster** (`record_weeks`, Default 520 Wochen),
   Toleranz enger (`near_high_pct` 20 → 15 %).
2. **Einstieg hatte keinen Trendfilter, Ausstieg schon.** Verkauft wird bei
   `close < Wochen-SMA30`; gekauft wurde ohne diese Bedingung → Positionen, die am
   Folgetag ausstiegsreif waren. Neu `require_uptrend` (Default True): Ausbruch nur
   über dem SMA30 **und** wenn dieser über `slope_weeks` (8) steigt.

Gemessen über ^SPX+^GDAXI+^MDAXI+^SDAXI (656 Ticker, Signale seit 2015, Signal→Signal,
ohne Kosten):

| Variante | Trades | Trefferquote | Ø | PF |
|---|---|---|---|---|
| alt (52W-Hoch, kein Trendfilter) | 6566 | 37,2 % | +4,2 % | 1,96 |
| nur Rekordhoch-Filter | 4744 | 38,8 % | +4,2 % | 1,95 |
| nur Trendfilter | 5229 | 40,0 % | +4,8 % | 2,08 |
| **neu (beides)** | **4209** | **40,8 %** | **+4,4 %** | **2,02** |

Profil der Methode (neue Defaults): Median −3,3 %, Haltedauer Ø 143 Tage, ~11 % p. a.
je Position; getragen vom rechten Rand (173 Trades > +50 %, 37 > +100 %). Zusätzliche
Entry-Filter (RS > 0, `fps_best_trend` ≥ 150, Abstand zum ATH, lange Basen) brachten
**nichts** — lange Basen (≥ 20 W) sind sogar schlechter (PF 1,81).

**Datenqualität schlägt durch:** die 16 Trades unter −20 % (bis −93 %) sind
Split-Artefakte in `yf_*.db` (ORLY, MNST, FAST, VST …), keine Methodenverluste →
`tradinglib/data_quality.py` gegenprüfen, bevor Ergebnisse interpretiert werden.

### 4PS-Ausstiegsvarianten (Messung 2026-08-17)

Gleiche Einstiege, nur anderes Positionsmanagement (656 Ticker, Signale seit 2015,
Signal→Signal, ohne Kosten). Trades unter −60 % (Split-Artefakte) ausgeschlossen:

| Ausstieg | Trades | Win | Ø | PF | Haltedauer | p. a. |
|---|---|---|---|---|---|---|
| SMA20 | 5111 | 39,7 % | +2,6 % | 1,68 | 99 d | +9,6 % |
| SMA30 (Default) | 4208 | 40,8 % | +4,4 % | 2,03 | 143 d | +11,4 % |
| SMA35 | 3955 | 40,4 % | +5,3 % | 2,18 | 162 d | +12,1 % |
| SMA40 | 3721 | 39,6 % | +6,1 % | 2,32 | 181 d | +12,4 % |
| SMA45 | 3565 | 39,2 % | +6,6 % | 2,39 | 195 d | +12,4 % |
| SMA50 | 3419 | 38,9 % | +7,2 % | 2,46 | 209 d | +12,6 % |
| SMA40 + Stop 12 % | 3615 | 41,2 % | +6,6 % | 2,40 | 190 d | +12,6 % |
| SMA30 + Trailing 20 % | 4227 | 40,9 % | +4,2 % | 1,99 | 141 d | +11,0 % |
| SMA30 + Zielverkauf | 4346 | 40,5 % | +4,1 % | 1,93 | 137 d | +10,8 % |

- **Monotone Rampe, kein Peak:** PF steigt durchgehend mit der SMA-Länge, weil die
  Haltedauer proportional mitwächst — annualisiert bleibt es bei ~12 %. Also kein
  überangepasster Sweet Spot, aber auch kein großer Hebel.
- **Trailing-Stop und Gewinnmitnahme schaden** (kappen den rechten Rand) → bleiben aus.
- Robustheit von SMA40+Stop 12 % gegen SMA30+Stop 8 %: **in allen drei Teilperioden
  (2015–19, 2020–22, 2023–26) und beiden Regionen besser** (PF 6/6, Win 5/6).
  Schwächste Phase bei beiden: 2020–2022 (PF 1,76 bzw. 1,89).
- **Einschränkung:** Alles gerechnet je Position (Signal→Signal), NICHT als Portfolio
  mit begrenzten Slots. Längere Haltedauern binden Kapital — im `multi_transactions`-
  Kontext mit `num_assets` kann das die Reihenfolge der Varianten drehen.

**Entscheidung 2026-08-17:** Defaults auf **SMA40 + Stop 12 %** umgestellt (`trend_sma_weeks` 30→40, `stop_pct` 8→12) — in allen Teilperioden und Regionen besser. Trailing/Zielverkauf bleiben aus.

### 4PS: Sektor-Vergleich (2026-08-17)

Screener + Detail zeigen `sector`, `vs_sector` (eigene 52W-Rendite − Sektor-**Median**, pp)
und `sector_rank` (Perzentil im Sektor) plus Filter „nur über dem Sektor-Median“.
Engine: `four_ps.sector_map/window_return/quick_return/sector_reference/sector_context`.

- **Vergleichsgruppe = Sektor im gewählten Universum** (nicht Sektor-ETF, nicht global).
  `scan()` nutzt die beim Scoring ohnehin berechneten Renditen → keine zusätzliche IO;
  die Detailansicht baut die Referenz über `quick_return` (liest nur den Tail von
  `day_data`, ~8 s für 656 Ticker) und cached sie tageweise in `rotation_cache.db`.
- Sektoren mit < 5 Werten bleiben leer (`_MIN_PEERS`).
- **Bewusst KEINE `fps_*`-Spalte in der Sim-DB**: universumsabhängige Momentaufnahme,
  keine kausale Zeitreihe. Für Backtests bleibt `fps_rs` (gegen den Index) zuständig.

### 4PS: Spaltenerklaerung + zwei Hoch-Referenzen (2026-08-17)

Screener-Spalten sind jetzt im Tab **Methode** (`fps.columns_md`, de/en) und in beiden
HELP-Seiten erklaert. Zwei Punkte, die vorher verwirrten:

- **`bis Ausbruch %`** stand ab Phase 3 auf 0 (las sich wie „direkt am Ausloeser") →
  wird jetzt leer gelassen, sobald der Ausbruch passiert ist.
- **Zwei verschiedene Hoch-Referenzen:** `fps_dist_high` („vom Hoch %") misst gegen das
  **Allzeithoch** (`close.cummax()` ueber die volle lokale Historie), der Phase-2-Filter
  dagegen gegen das **Rekord-Fenster** (`record_weeks`, 10 Jahre). Deshalb kann ein Wert
  86 % unter seinem Hoch stehen und sich trotzdem qualifizieren (CBK.DE: Hoch 2007 bei
  282,86, aktueller Kurs 39,61, 10-Jahres-Hoch 40,13). Bewusst so gelassen — beide
  Aussagen sind fuer sich sinnvoll —, aber in Doku und Spaltentext explizit gemacht.

### 4PS-Fund: Level-Shifts in yf_*.db blenden die Methode (2026-08-17)

Frage aus der Praxis: „Warum loest der IBKR-Anstieg ab Dez 2023 kein Buy aus?" Ursache
ist kein Regelproblem, sondern die Datenreihe: in `yf_IBKR.db` faellt der Kurs am
2023-12-12 von 83,44 auf 20,94 (Faktor 0,251 = **Split 4:1, nicht rueckwirkend
bereinigt**). Das Vor-Bruch-Hoch 94,88 bleibt 10 Jahre im Rekord-Fenster → Phase 2
verlangt eine Basis ueber 80,65, waehrend der Kurs 21–71 lief → 614 Tage Phase 1, erstes
Signal erst 2026-06-17. Gegenprobe mit bereinigten Daten: Kauf am 2024-01-22 zu 22,77
(+313 % bis heute).

- **Neue Fehlerklasse, neuer Detektor:** `data_quality.detect_price_gaps()` /
  `scan_price_gaps()` + CLI `python -m tradinglib.data_quality /gaps /index:^SPX`
  (PowerShell wegen MSYS-`/flag`-Mangling). `scan_ohlc_issues` findet das prinzipbedingt
  **nicht** — dort ist `close` innerhalb `[Low, High]`, die ganze Reihe springt sauber.
- **Verbreitung:** 32 von 656 Tickern (^SPX+DAX-Familie) mit Sprung > 40 % seit 2015.
  Auffaellig sind Cluster auf gleichen Tagen (2023-12-12: IBKR/FAST/ORLY/NFLX/DD;
  2024-08-15/16: BKNG/CRWD/KLAC/NOW/…) → das sind Umstellungen der lokalen Pipeline,
  keine echten Unternehmensereignisse.
- **Sichtbar gemacht:** `analyze()` liefert `gap_date`/`gap_factor`/`gap_cause`; Detail
  zeigt eine Warnung, der Screener eine Spalte „Daten" mit `⚠ Datum ×Faktor`.
- **Bereinigung** bleibt manuell: `get_asset_data.py` fuer die Ticker neu laden, dann
  `asset_perf2.py /index:…` — betrifft alle Konsumenten (Scores, Charts), nicht nur 4PS.

### get_asset_data: `/tickers:A,B,C` statt `/select:'WHERE …'` (2026-08-18)

Der aus `data_quality.build_cleanup_commands()` kopierte Befehl scheiterte in der
Praxis: `python get_asset_data.py /select:'WHERE Ticker IN ("IBKR",…)'` zerbricht in
PowerShell/cmd am ersten Leerzeichen →
`Execution failed on sql 'SELECT Ticker FROM stocks 'WHERE;'`. Ursache ist nicht der
Parser (`cli.parse_args` nimmt alles nach dem ersten `:`), sondern die Shell: der
Wert enthaelt Leerzeichen **und** Anfuehrungszeichen.

- **Neu:** `get_asset_data.py` wertet `/tickers:A,B,C` aus (Parsing lag in `cli.py`
  schon vor, nur die Auswertung fehlte). Keine Leerzeichen, keine Quotes, laeuft in
  jeder Shell. Symbole, die nicht in `stocks` stehen, werden geladen und nur geloggt.
- `build_cleanup_commands()` gibt jetzt
  `python get_asset_data.py /tickers:… 1d:max` aus. **`1d:max` ist entscheidend** —
  ein normaler Tageslauf schreibt nur die juengsten Zeilen und laesst den alten,
  falsch skalierten Teil stehen.
- **Verifiziert gegen ein Scratch-`TradingDB`** (Produktion unberuehrt): nach
  `/tickers:IBKR 1d:max` liefert Yahoo die vollstaendig adjustierte Historie,
  `detect_price_gaps` meldet nichts mehr, und 4PS kauft am 2024-01-22 zu 22,77
  (Phase 4) statt erst 2026 — genau die Gegenprobe aus der Analyse.
- `/select:'WHERE …'` bleibt als maechtigere Variante erhalten (unter PowerShell das
  **ganze** Argument quoten: `"…/select:WHERE Ticker LIKE '%.MC'"`).

### 4PS: weitere Einstiege gemessen — neue Hochs schlagen den Basis-Ausbruch (2026-08-18)

Frage: „Gibt es weitere optimale Einstiegspunkte?“ Testbank (Scratch `fps_entries.py`):
gleiche Qualifikation (Phase 1), gleicher Trendfilter, **gleicher Ausstieg**, nur der
Ausloeser variiert. 656 Ticker, Signale seit 2015, Reihen mit Level-Shift ausgeschlossen:

| Ausloeser | Trades | Win | Ø | PF | p. a. |
|---|---|---|---|---|---|
| breakout (Basis-Ausbruch, Standard) | 3560 | 41,2 % | +6,5 % | 2,41 | +12,6 % |
| **record_high** (neues Rekordhoch) | 3053 | 42,3 % | +7,8 % | **2,59** | **+14,5 %** |
| new_high (52-Wochen-Hoch) | 4175 | 41,3 % | +7,5 % | 2,52 | +14,4 % |
| both (Ausbruch oder Rekordhoch) | 3762 | 41,0 % | +7,2 % | 2,55 | +13,9 % |
| retest (Ruecklauf ans gebrochene Niveau) | 3830 | 40,1 % | +6,2 % | 2,39 | +12,7 % |
| reclaim (SMA-Rueckeroberung) | 6855 | 18,6 % | +1,4 % | 1,87 | +11,2 % |
| pullback (Ruecksetzer an den SMA) | 11023 | 21,1 % | +1,8 % | 2,03 | +12,0 % |

- `record_high` ist in **allen** Teilperioden und beiden Regionen besser als der Ausbruch
  (2020-22 PF 2,12 vs 1,94; 2023-26 PF 3,30 vs 2,74).
- **Nur 861 von ~3000 Einsätzen ueberschneiden sich** → die Arten finden verschiedene
  Trades; `both` nimmt, was zuerst kommt. Werte, die durchmarschieren, bilden nie eine
  enge Basis und waren mit `breakout` grundsaetzlich unerreichbar.
- Ruecksetzer-Varianten sind eine **andere Strategie** (kurze Haltedauer, ~20 % Treffer),
  kein besserer Einstieg in dieselbe.
- Umgesetzt als Parameter `entry_mode` (Engine, Indikator-Dialog, Seiten-Expander,
  Locale de/en, Tests inkl. Look-ahead je Modus). **Default bleibt `breakout`** — die
  Methode des Artikels; die Umstellung ist bewusst eine Nutzerentscheidung.

### 4PS: Legs im Monatschart sind Rueckschau (2026-08-18)

Frage: „Warum passen die Legs nicht zu den Buy/Sell-Signalen (WAT)?“ Antwort: Ein Leg ist
erst mit der **bestaetigenden Gegenbewegung** bekannt. WAT: Schub 10/2023 → 01/2025 (+74 %)
wurde erst 07/2025 bei 288,76 bestaetigt = 30 % unter dem Hoch, ein halbes Jahr spaeter.
Alle vier WAT-Legs zeigen dasselbe Muster (-25 bis -30 %, 4-8 Monate Verzug). Signale
koennen also gar nicht auf Leg-Enden liegen — sonst waeren sie Rueckschau.

- Der Monatschart zeigt jetzt **zusaetzlich** die Kauf-/Verkaufsmarker und ein `X` am
  Bestaetigungspunkt jedes Legs, dazu eine Bildunterschrift, die den Unterschied benennt.
- Wie nah der Einstieg an den Leg-Anfang rueckt, haengt am `entry_mode`: bei WAT kaufte
  `breakout` 11/2024 zu 387, `new_high` schon 02/2024 zu 338 (Leg-Start 10/2023 bei 238).

### 4PS: Wie viel eines Legs die Signale wirklich mitnehmen (2026-08-18)

Einwand: „Wenn die Legs Rueckschau sind, dann sollten die Buy-Signale dazu passen."
Gemessen ueber 1010 qualifizierte Legs (>= 90 %) seit 2015, ALLE Trades im Leg verkettet:

| Einstieg | Legs mit Kauf | Kaeufe/Leg | mitgenommen (Median) | Anteil am Leg |
|---|---|---|---|---|
| breakout | 76,3 % | 3,9 | +25,3 % | 7,4 % |
| record_high | 76,5 % | 3,5 | +21,2 % | 5,8 % |
| **new_high** | **90,6 %** | 3,9 | +28,7 % | 10,3 % |
| both | 78,6 % | 4,0 | +27,0 % | 8,0 % |

**Der Massstab „Anteil am ganzen Leg" ist unfair** (Korrektur meiner ersten Deutung, der
Ausstieg sei die Hauptursache): ein Leg wird ab seinem TIEF gemessen, die Methode kauft
aber bewusst Staerke. Von einem Median-Leg von 236 % sind ab dem ersten moeglichen
Einstieg nur noch **114 %** offen — die untere Haelfte ist strukturell unerreichbar.
Am offenen Teil gemessen (250 SPX-Werte, Einstieg new_high):

| Ausstieg | Anteil am offenen Teil | oberes Viertel | PF | p. a. | Haltedauer |
|---|---|---|---|---|---|
| SMA40 (Default) | 19,9 % | 44,7 % | 2,57 | +14,2 % | 193 d |
| **SMA60** | **26,7 %** | 46,9 % | **3,20** | **+15,9 %** | 272 d |
| nur Trail 25 % | — | — | 3,27 | +13,5 % | 395 d |
| SMA40 + Trail 25 % | — | — | 2,52 | +13,9 % | 191 d |

Also: **rund die Haelfte der Luecke ist strukturell** (Kauf bei Staerke), die andere
Haelfte haengt am Ausstieg und ist verbesserbar. Ein reiner 25-%-Trailing-Stop (= die
Zickzack-Definition) hebt den PF, kostet aber p. a., weil Kapital 395 Tage gebunden ist.
Kein Default geaendert — die Messung ist ^SPX-only mit einem Einstiegsmodus.

**Chart:** Der Monatschart zeigt die tatsaechlich gehaltenen Abschnitte als dicke gruene
Auflage auf der Kurve (`fps.lg_held`), dazu Signale und Bestaetigungspunkte. Damit ist im
Bild sichtbar, welcher Teil eines Legs im Depot lag und welcher nicht.

---

## Neu in dieser Session (2026-08-28) — Scalable MCP, Transaktions-Import, Order-Korb

Scalable Capital bietet seit v1.0.0 zwei Zugaenge (Seite `de.scalable.capital/agentic-investing`):
eine **CLI** (Rust, Apache-2.0, nur macOS/Linux — **kein Windows-Binary**, damit fuer
diesen Stack unbrauchbar) und einen **Remote-MCP-Server** `https://mcp.scalable.capital/mcp`
(OAuth 2.1 + PKCE, Dynamic Client Registration, Scope `offline_access`). Freischaltung im
Konto unter **Profil > Sicherheit**, dann als Connector verbinden. Der MCP-Weg ist der
einzige, der unter Windows laeuft.

### Drei Fallen der MCP-Transaktionsdaten (verifiziert am Live-Depot)

1. **`amount` ist brutto inkl. Transaktionssteuer.** `amount/quantity` ueberschaetzt den
   Einstandskurs, sobald Finanztransaktionssteuer anfaellt (ES/FR/IT). Beleg Solaria:
   `averagePrice` 17,02, `marketValuation` 1191,40, `taxAmount` 2,38 (span. FTT),
   `totalAmount` 1193,78 → Division ergaebe 17,054. Ausfuehrungskurs und Steuer gibt es
   **nur** ueber `get_transaction_details` → N+1 bleibt unvermeidbar.
2. **Zahlen muessen deutsch formatiert eingespeist werden.** `parse_scalable_csv` nutzt
   `_parse_de_number`, das Punkte streicht — aus "17.02" wuerde 1702. Immer ueber
   `_format_de_number`.
3. **Zeitstempel:** `lastEventAt` ist UTC, `history[FILLED].timestamp` lokale Zeit
   (= was die CSV schreibt). Naiv `lastEventAt` nehmen verschiebt neue Trades um 2 h
   gegen die Altbestaende.

**Serverseitiger Bug:** `transactionTypes: ["fee"]` bzw. `["distribution"]` liefern
reproduzierbar `upstream_unavailable`; `["buy"]`/`["sell"]` funktionieren. Workaround:
ungefiltert laden, clientseitig filtern. Gebuehren fallen ohnehin nicht pro Trade an,
sondern als monatliche Cash-Buchung ("Entgelt PRIME+ Broker").

### Neue Module

| Datei | Zweck |
|---|---|
| `tradinglib/scalable_mcp.py` | mappt MCP-JSON auf das CSV-Spaltenlayout → `parse_scalable_csv()` bleibt unveraendert. `split_payloads`, `mcp_to_scalable_frame`, `transaction_ids_needing_details`. Spalte `price_estimated` markiert Zeilen ohne Detaildaten |
| `tradinglib/scalable_orders.py` | Order-Korb: `OrderDraft`, `OrderBasket` (eigene `scalable_orders.db`), `validate`, `to_preview_payload`, Builder fuer Agent-Signale und Positionsverkaeufe, `isin_map_from_trades`, `render_order_basket` |

### Dedup im CSV-Import (behebt einen alten Datenfehler)

`_insert_scalable_rows` warf die vom Parser durchgereichte `ref`-Spalte weg → jeder
erneute Import derselben CSV erzeugte Duplikate. Jetzt: neue Spalte **`ext_id`**
(Broker-Referenz bzw. MCP-Transaktions-ID) plus **Fingerprint-Fallback**
(`timestamp|action|isin|shares|value`) fuer Altzeilen ohne `ext_id`. Abgeglichen wird
mit **Countern, nicht mit Sets** — ein Depot darf zwei identische Zeilen enthalten,
nur der Ueberschuss eines Re-Imports faellt weg. Rueckgabe ist `(inserted, duplicates)`.

### Order-Korb — Grenzen, die der Broker setzt

`preview_*_order` liefert einen signierten `submission`-Block, den `submit_*_order` nur
nach **ausdruecklicher Einzelbestaetigung** akzeptiert. Der Korb bereitet deshalb nur vor;
abgesendet wird bei Scalable. Die Validierung bildet die Schema-Grenzen ab:
ISIN Pflicht, **nur ganze Stuecke**, Verkaeufe ausschliesslich stueckbasiert (kein Betrag),
Ordertypen market/limit/stop (**kein Trailing Stop, keine OTO-/Bracket-Legs**),
Handelsplaetze gettex/Xetra/EIX. Es gibt **kein** `list_orders` — offene Orders ueber
`list_portfolio_transactions` mit `statuses` holen (Statusfilter funktioniert, Typfilter nicht).

**Verdrahtet an drei Stellen:** Trading-Seite ▸ Tab Signale (Kaufsignale, gesized via
`_apply_sizing`, Limit aus `agent_limit_buffer_pct`), Own Transactions ▸ Tab Risiko
(gerissene Trailing Stops als Verkaeufe), Asset Viewer ▸ Order-Dialog (manuell, neben
der Alpaca-Warteschlange). Der Korb selbst sitzt als Panel im Scalable-Tab.

**Wichtig fuer die Verkaufsseite:** die Trailing-Stop-Tabelle fuehrt keine ISIN. Der
Resolver ueber `yf_tickers.db` reicht dort nicht (Index-Mitglieder nur 56 % gefuellt) —
deshalb `isin_map_from_trades()`: `trades.db` traegt fuer alles real Gehaltene die ISIN
aus dem Scalable-Import.

**Fuer den autonomen Agenten bleibt Alpaca zustaendig** — Scalable kennt weder
OTO-Stop-Legs noch Trailing Stops noch eine Marktzeiten-Abfrage (`agent_rth_only` haette
dort keine Datenquelle), und der Bestaetigungsschritt ist bewusst nicht automatisierbar.

### 4PS-UI: leere Zellen zeigten den Text „None" (2026-08-30)

Beim Filter „nur Phase 3" stand in `bis Ausbruch %` ueberall **None** statt einer leeren
Zelle. Der Wert ist ab Phase 3 absichtlich leer (der Ausbruch ist passiert) — der Fehler
lag in der Darstellung: die Spalte wurde als Python-Liste gebaut, und sobald **alle**
Eintraege `None` sind, entsteht eine object-Spalte; `st.dataframe` schreibt darin `None`
als Text. Mit gemischten Werten fiel es nicht auf, deshalb erst mit dem Phasenfilter
sichtbar.

- Fix: Helper `_blank_col()` liefert eine **float64-Series mit NaN** → leere Zelle.
  Genutzt von `bis Ausbruch %` und (gleiche latente Falle) den Sektor-Spalten.
- Merksatz: ausgeblendete Zahlenzellen in `st.dataframe` immer als `np.nan` in einer
  typisierten Spalte liefern, nie als `None` in einer Liste.
- `Basis (W) = 0` bei Phase-3-Zeilen ist dagegen korrekt, wenn ein basisloser
  `entry_mode` (record_high/new_high/both) laeuft — dort gibt es keine Basis.

### 4PS: Setup-Score - die kurze Liste statt langer Suche (2026-08-30)

Frage: "Wie findet man ohne lange Suche die Kandidaten mit der hoechsten
Ausbruchs-Wahrscheinlichkeit?" Dafuer wurden fuer **3.561 Ausbrueche seit 2015** die
zum Einstiegszeitpunkt bekannten Merkmale erfasst (Scratch `fps_features.py`,
Pickle `fps_features.pkl`) und gegen das Trade-Ergebnis gebucketet.

**Trennschaerfe der Einzelmerkmale:**

| Merkmal | gut | schlecht |
|---|---|---|
| Basislaenge | 8-12 W: 48 % / PF 3,38 | 30-60 W: 38 % / 1,72 |
| Basisbreite | 10-15 %: PF 3,26 | 20-26 %: 2,27 |
| SMA-Steigung 8 W | >= 5 %: 47 % / 3,37 | 0-5 %: 40 % / 2,19 |
| Abstand ueber SMA40 | 20-40 %: 49 % / 3,30 | 0-10 %: 36 % / 2,05 |
| RS 52W | > 50 pp: 14,0 % / 3,16 | < 0: 4,9 % / 2,13 |
| **Volumen** | **kein Signal** (hoeher = eher schlechter) | |
| **best_trend** | **kein Signal** (leicht invers) | |

**Score = Anzahl erfuellter Bedingungen (0..5)**, monoton und in allen Teilperioden
stabil: 0 -> 38,1 % / PF 1,86 - 2 -> 42,6 % / 2,86 - 3 -> 48,9 % / 3,37 -
4 -> 51,7 % / 3,86. Score >= 3 schlaegt den Rest in 2015-19 (3,11 vs 2,45),
2020-22 (3,61 vs **1,56**) und 2023-26 (3,72 vs 2,20). Nur **15 %** der Ausbrueche
erreichen Score >= 3.

- Umgesetzt als Spalte `fps_setup` (Engine, Screener-Spalte + Filter "min. Setup-Score",
  Sortierung Phase -> Setup -> RS, Detail-Aufschluesselung der fuenf Kriterien,
  `INDICATOR_BACKFILL_MAP['fps']` + pdict -> in Buy/Sell-Formeln nutzbar).
- **Arbeitsweise:** Phase = 2, min. Setup-Score = 3, max. Abstand zum Ausbruch ~3-5 %.
  Beispiel ^SPX 2026-08-30: 6 Kandidaten von 457 (INCY 5, FTNT 4, BMY/GOOGL/MGM/CMI 3).
- Schwellen bewusst als Konstante `SETUP_RULES`, nicht als Parameter - sie stammen aus
  einer Messung, nicht aus Geschmack; wer sie aendert, sollte neu messen.

### 4PS: Schluss-Validierung und gesetzte Defaults fuer kurt (2026-08-30)

Offene Frage war die Kombination aus Einstiegsart und Ausstiegslaenge. Gemessen ueber
alle vier Universen (656 Ticker, Signale seit 2015, Stop 12 %, Reihen mit Level-Shift
ausgeschlossen), aufgeteilt nach Teilperioden, Regionen und zusaetzlich gefiltert auf
Setup-Score >= 3:

| Variante | gesamt PF | 2015-19 | 2020-22 | 2023-26 | US | EU | Setup>=3 |
|---|---|---|---|---|---|---|---|
| both / SMA40 | 2,50 | 2,49 | 2,18 | 2,83 | 2,48 | 2,62 | 3,67 (+27,6 % p.a.) |
| **both / SMA60** | **3,01** | 2,86 | 2,91 | **3,30** | 2,92 | **3,56** | **3,66** (+24,6 %) |
| new_high / SMA40 | 2,47 | 2,49 | 2,46 | 2,45 | 2,50 | 2,34 | 3,92 (+34,5 % p.a., n 321) |
| new_high / SMA60 | 3,07 | **3,02** | **3,27** | 2,99 | **3,10** | 2,99 | 3,14 (+23,0 %) |

- **SMA60 schlaegt SMA40 bei beiden Einstiegsarten** und in praktisch jeder Teilmenge
  (both: 6 von 6; new_high: 5 von 6). Das ist der belastbare Teil.
- **both vs. new_high liegt im Rauschen** (3,01 vs 3,07 gesamt, je drei gewonnene
  Teilmengen). Ausschlag gab die Arbeitsweise: `both` feuert auch auf klassische
  Basis-Ausbrueche, damit bleibt die Screener-Spalte "bis Ausbruch %" auf das Basis-Hoch
  bezogen — genau die Liste "steht kurz vor dem Ausbruch". Mit Setup-Filter war `both`
  ausserdem vorn (PF 3,66 vs 3,14), wenn auch bei kleiner Stichprobe (377 bzw. 295).
- Die setup-gefilterten Zeilen sind mit 295-446 Trades **klein** — Reihenfolge dort nicht
  ueberinterpretieren; der Score selbst ist ueber 3.561 Ausbrueche belegt.

**Gesetzt in `config.db` fuer kurt** (`kurt:fps_params`, `kurt:fps_universes`):
`entry_mode='both'`, `trend_sma_weeks=60`, `stop_pct=12`, `near_high_pct=15`,
`record_weeks=520`, `require_uptrend=True`, `trail_pct=0`, `take_profit` bleibt aus,
Universum `^GDAXI,^MDAXI,^SDAXI,^SPX,^IBEX,^RUT` (vorher gar nicht gespeichert -> nach
Neustart waeren es wieder die vier Default-Indizes gewesen).

**Ausgelieferter Engine-Default bleibt `breakout` / SMA40** — die Methode des Artikels.
Wer die gemessene Kombination global will, setzt `four_ps.DEFAULTS['entry_mode']='both'`
und `trend_sma_weeks=60`; die Zahlen dafuer stehen oben.

---

## Neu in dieser Session (2026-09-12) — Messgeruest fuer Scores, Gates und Risikoprofile

Ausgangspunkt: Der Auswahl-Score aus `asset_perf2.py`/`indicator/ovt.py` sollte
"Chartdaten gegen inneren Wert und Dynamik" stellen — nachpruefbar war das nicht.
Schritt 1 des Umbaus ist deshalb **kein neuer Score, sondern das Messgeraet**.

| Datei | Rolle |
|---|---|
| `tradinglib/score_eval.py` | Rechenkern: Panel-Aufbau (read-only), IC, Quantilstabellen, Gate-Statistik, Profil-Konformitaet, Turnover |
| `score_eval.py` (Wurzel) | CLI mit Default-Batterie (`/years:`, `/horizon:`, `/columns:`, `/gate:`, `/json:`, `--no-gates`, `--no-profiles`) |
| `tests/test_score_eval.py` | 13 Tests auf synthetischen Panels (perfektes Signal -> IC 1, Turnover 0/1, Vorwaertsziele schauen nicht rueckwaerts) |

**Alle DB-Zugriffe `mode=ro`** — im Gegensatz zum Chart-Pfad, wo ein Yahoo-Fallback
in `yf_*.db` zurueckschreibt. Ein Messlauf kann Produktionsdaten nicht veraendern.

### Messergebnisse 2023-2025 (2,34 Mio. Zeilen, 759 Tage, 3619 Ticker, Horizont 21 T)

- `overallValueTrend` IC **0,065**, `overallTrend` IC **0,099**. Nach Neutralisierung
  gegen `pctTargetHighPrice` nur noch 0,042 bzw. 0,062 — **rund ein Drittel der
  Trennschaerfe haengt an einer einzigen Spalte**.
- Diese Spalte allein hat IC **0,181** (t 53, Trefferquote 97 %, Quintilspreizung
  6,85 % je 21 Tage). Das ist kein Alpha: `asset_info` hat genau **eine Zeile je
  Ticker** mit `timestamp` = letzter Abruf, keine Historie. Jeder `rescore_db()`-Lauf
  schreibt die heutigen Fundamentaldaten in die gesamte Vergangenheit.
- **Rendite ist kaum vorhersagbar, Risiko sehr gut:** Score -> Rendite IC 0,065,
  `logVola` -> realisierte Vola IC **0,703**, Vola -> Drawdown **-0,331**.
- **Trend-Gates liefern Sicherheit, keine Rendite.** Gegen die Universums-Baseline
  (1,28 % / 21 T): `close>sma200 & sma50>sma200` -0,17, voller MA-Stapel -0,36,
  `trendDirection>=2` -0,18, 4PS-Phase 3/4 -0,26, < 5 % unter ATH -0,11; einzig
  `markov_regime>0` +0,15. Vorwaerts-Vola faellt dabei von 0,39 auf 0,31-0,36.
- **Aber: die Reihenfolge entscheidet.** Auf den drei niedrigsten Vola-Quintilen
  (Baseline 0,99 %) steigt der Vorteil des 6M-Momentum-Top-Quintils von +0,25 auf
  **+0,49** bei Trefferquote 54,7 %. Erst Risikoprofil (Universum), dann Trend
  (Ranking) — nicht umgekehrt.

### Vorlaeufige Risikobaender (aus der gemessenen Verteilung)

`DEFAULT_BANDS` in `score_eval.py`; Konformitaet gemessen ueber `logVola`-Schnitte:

| Profil | logVola | Anteil | Vorwaerts-Vola (Band) | eingehalten |
|---|---|---|---|---|
| conservative | <= 0,110 | 24,4 % | [0; 0,25] | 68,9 % |
| balanced | <= 0,170 | 59,1 % | [0; 0,39] | 84,5 % |
| dynamic | <= 0,214 | 74,0 % | [0; 0,49] | 89,6 % |

Die Drawdown-Zusage halten alle drei zu ~99,7 %. Baender sind **(Boden, Decke)** und
auf der harmlosen Seite offen — eine Position, die nie unter den Einstand faellt,
haelt jede Drawdown-Grenze ein.

**Absolut kalibriert, nicht als Perzentilrang** — ein Rang haengt vom Universum des
Tages ab und laesst sich im Live-Chart nicht reproduzieren (genau das Zweipfad-Problem,
das `ovt.py` heute mit dem Stored/Live-Merge umschifft).

### Bekannte Grenzen, die in jedem Bericht stehen

- Universum = heutiger Indexstand ohne Historie -> Ueberlebensverzerrung, begünstigt
  alles Mean-Reversion-artige (`stock_indices` hat keinen Zeitstempel).
- Renditen sind brutto: keine Gebuehren, kein Slippage, kein Spread.
- Jede Kennzahl wird **zusaetzlich je Jahr** ausgewiesen — gepoolte Bucket-Vergleiche
  haben hier schon Vorzeichenwechsel produziert (Simpson-Paradox, `market_phase`).

### Schritt 2 erledigt — `tradinglib/risk_profile.py`

Vier Presets (conservative/balanced/dynamic/offensive) als **absolute Schnitte**, nicht
als Perzentilraenge — ein Rang haengt am Universum des Tages und laesst sich im
Live-Chart nicht reproduzieren.

**Risikomass ist ATR als Anteil am Kurs**, nicht `logVola`: sagt die Vorwaerts-Vola
etwas besser vorher (Spearman 0,71 vs 0,68) und ist die eine Zahl, die man sich
vorstellen kann ("bewegt sich ~2 % am Tag"). `logVola` bleibt Fallback fuer Frames ohne
ATR, geschnitten bei **derselben Abdeckung** (1,8 % der Zeilen haben kein ATR).

| Profil | ATR-Schnitt | logVola | Abdeckung | typ. Vola | Vola-Band (p85) | max. DD (p10) | Stop |
|---|---|---|---|---|---|---|---|
| conservative | <= 2,2 % | 0,1123 | 21,5 % | 0,213 | 0,315 | -10,2 % | 11 % |
| balanced | <= 3,0 % | 0,1543 | 46,3 % | 0,249 | 0,371 | -11,7 % | 12 % |
| dynamic | <= 4,2 % | 0,2147 | 70,6 % | 0,285 | 0,441 | -13,3 % | 13 % |
| offensive | kein | kein | 100 % | 0,339 | 0,623 | -16,9 % | 17 % |

**Das Band ist gemessen, nicht behauptet** — und es steht dabei, auf welchem Quantil.
"Konservativ" heisst nicht "nie mehr als 25 % Vola", sondern "die Haelfte der Zeit unter
21 %, in 85 % der Faelle unter 31,5 %". Kalibriert ueber 2020-2026 (5,02 Mio. Zeilen,
Krise eingeschlossen); eine Kalibrierung nur auf dem Bullenmarkt 2023-2025 haette die
Baender zu eng gesetzt.

**Out-of-sample geprueft** (Kalibrierung 2020-2023, Messung 2024-2026): Vola-Band in
82,6-85,7 % der Faelle eingehalten (Ziel 85), Drawdown-Boden in 89,9-93,5 % (Ziel 90),
Abdeckung stabil. Die Zusage haelt ausserhalb ihres Kalibrierfensters — das ist der
Unterschied zu einem selbsterfuellenden In-Sample-Band.

**Kosten der Ruhe, ebenfalls gemessen:** der Vorteil gegen das Universum ist bei allen
Profilen negativ (conservative -0,98 pp je 21 Tage), am staerksten **2020** (-5,54 pp) —
nach dem Corona-Crash liefen ruhige Titel der Erholung hinterher. Ein Profil wird
deshalb an der Konformitaet gemessen, nicht am Renditevorsprung.

**API:** `settings/save_settings(username)` (per Nutzer, Muster `candidates.py`),
`resolve(username|name, calibration=)`, `apply(df, profile)` -> Maske,
`filter_expression(profile)` -> `(atr / close <= 0.03)` fuer Buy/Sell-Formeln,
`bands(profile)` -> Zusage im Format von `score_eval.conformity`,
`score_frame(df)` -> `riskScore` (0..100, stueckweise linear ueber eingefrorene
Stuetzstellen) + `riskBucket` (1..4). Kalibrierung global unter `_app:risk_profile_calibration`,
Nutzer-Einstellung unter `<user>:risk_profile`.

**CLI:** `python -m tradinglib.risk_profile` zeigt die Tabelle,
`--calibrate` misst neu (read-only), `--calibrate --save` schreibt sie app-weit.
`score_eval.py` zieht seine Profil-Ausdruecke und Baender jetzt aus diesem Modul
(lazy importiert, sonst Zirkel).

**Nicht editierbar per Nutzer** sind die gemessenen Felder (`coverage`, `vol_band`, …) —
sie beschreiben, was der Schnitt geliefert hat, und waeren nach einer Handaenderung
schlicht falsch. Editierbar: `max_atr_pct`, `max_log_vola`, `stop_loss_pct`,
`target_position_vol`. Wer einen Schnitt aendert, muss neu kalibrieren.

### Schritt 3 erledigt — Profil-Spalten in `asset_simulation`

Neuer Indikator `tradinglib/indicator/prof.py` (Klasse `Prof`, Oszillator) liefert
`riskScore` / `riskBucket` / `trendScore` **live und gespeichert unter denselben Namen** —
damit laufen sie in Buy/Sell-Formeln des Strategy Finders und der Multi Strategies
genauso wie im Chart.

Verdrahtung wie bei `fps`: `INDICATOR_BACKFILL_MAP['prof']`, `'prof'` in der
Indikatorliste von `process_symbol`, pdict-Schleife in `fill_pdict`.
Bestandsdaten: `python asset_perf2.py /backfill:prof /force` (PowerShell wegen
MSYS-`/flag`-Mangling). **`/force` ist Pflicht** — `_ensure_sim_columns` legt neue
Spalten mit `DEFAULT 0` an, der „schon gefuellt"-Test prueft auf `IS NOT NULL` und
wuerde sonst alles ueberspringen.

**Warum der Indikator die volle Tageshistorie selbst nachlaedt:** ATR und Rekordhoch
sind pfadabhaengig. Ein auf 2024 gezoomter Chart wuerde sonst einen Wert „am Hoch"
zeigen, der in Wahrheit 40 % darunter steht. Gleiche Loesung wie `fps` — und genau
das macht Live- und Backtest-Wert identisch (per Test abgesichert).

**Rekordhoch = laufendes Maximum der vollen taeglichen Close-Historie**, also die
Definition von `fps_dist_high` — bewusst **nicht** die gespeicherte `ath`-Spalte: die
entsteht aus einem 10-Jahres-Monatsfenster, das am *Laufzeitpunkt* verankert ist
(BAS.DE: gespeichert 62,94 gegen tatsaechliche ~94 — je nach Laufdatum ein anderer Wert
fuer denselben Balken). Die volle Historie schneidet in der Messung ausserdem besser ab.

### trendScore: das gemessene Ergebnis ist bewusst bescheiden

Ueber 2020-2026 (5,02 Mio. Zeilen) sagt **kein** Trend-Ranking die Rendite verlaesslich
vorher: 12M-Momentum IC -0,001, 6M-Momentum -0,001, Abstand zum Hoch +0,003 (t 0,8).
Der fruehere Befund „6M-Momentum-Top-Quintil +0,49 im Profil" war ein Fenstereffekt —
per Jahr aufgeschluesselt steht er 2021/2022 im Minus, und +0,37 gesamt stammten fast
vollstaendig aus 2020 (+5,02).

Was **haelt**, ist die Konsistenz-Achse. Abstand zum Rekordhoch, Quintile 1→5:

| | Q1 (weit weg) | Q2 | Q3 | Q4 | Q5 (am Hoch) |
|---|---|---|---|---|---|
| Rendite 21 T | 1,93 % | 1,06 % | 0,93 % | 0,94 % | 0,85 % |
| Trefferquote | 50,7 % | 50,9 % | 51,7 % | 52,1 % | **52,1 %** |
| Drawdown | -9,70 % | -7,79 % | -7,12 % | -6,39 % | **-5,97 %** |

Trefferquote und Drawdown steigen bzw. fallen **monoton ueber alle fuenf Quintile** —
als einzige der geprueften Varianten (Blends mit Momentum oder SMA-Steigung waren in
einzelnen Jahren besser und verloren die Monotonie). Die hoechste Durchschnittsrendite
liegt dagegen bei den am weitesten gefallenen Titeln — Mean Reversion, zusaetzlich
geschmeichelt davon, dass im Universum nur die Ueberlebenden stehen.

`trendScore` ist deshalb im Code und in der Doku als **Qualitaetsfilter** ausgewiesen,
nicht als Alpha-Quelle. Wer das aendern will, muss es neu messen — `score_eval` kann es.

**0 heisst „nicht berechnet", nicht „Stufe 0":** neue Spalten entstehen per
`ALTER TABLE ... DEFAULT 0`, und auch `fill_pdict` schreibt bei fehlendem Wert 0.
`riskBucket <= 2` wuerde damit alle noch nicht gefuellten Zeilen mitnehmen — in Formeln
deshalb `(riskBucket >= 1) & (riskBucket <= 2)` schreiben.

Die Spalten landen ohne weitere Verdrahtung in Strategy Finder und Multi Strategies:
`make_query(q=3)` baut seine Feldliste per `PRAGMA table_info(asset_simulation)`, neue
Spalten sind also automatisch im `combined_df` und damit im `ExpressionEvaluator`.

**Achtung bei festen Schwellen:** absolute Kalibrierung heisst, dass `trendScore >= 75`
in einem schwachen Markt weniger Titel liefert als in einem starken (ein Quintilschnitt
liefert immer ein Fuenftel). Das ist gewollt, aber es muss bei Positionszahlen
eingeplant werden.

### Schritt 4 erledigt — Seite "Risikoprofil"

`tradinglib/risk_profile_page.py` (Route `?risk_profile=true`, Gruppe Assets neben
Kandidaten). Aufbau: Preset-Auswahl → Zusage des Profils (vier Metriken) → was vom
Universum uebrig bleibt (Anzahl, Verteilung je Risikostufe, Kurzliste nach Trendwert) →
fertiger Ausdruck zum Kopieren → Methoden-Expander.

- **Kein `on_change`**, Speichern nur per Knopf im `st.form` — das Re-Fire bei Widget-GC
  hat schon einmal halbfertige Auswahlen in die Config geschrieben (Overlay-Defaults).
- **Vorschau** ueber `risk_profile.universe_snapshot()`: letzte Zeile je Ticker aus einem
  10-Tage-Fenster, nicht `WHERE Date = MAX(Date)` — der juengste Tag ist regelmaessig nur
  halb geschrieben.
- Verdrahtet in `system_config` (Sidebar-Default, Gruppe, Label-Key, Startseiten,
  HELP-Registrierung), `asset_analyzer` (`_START_PAGE_ROUTES`, Nav, Route) und
  `app_edition._ROUTE_PARAMS`.
- 62 Locale-Keys je Sprache (`risk.*`, `nav/page/error.risk_profile`),
  HELP-Seiten `risk_profile_page[_en].html`.
- Ein Test prueft, dass jeder in der Seite benutzte Locale-Key in **beiden** Sprachen
  existiert — ein Tippfehler faellt damit im Testlauf auf, nicht erst in der UI.

**Die Seite verspricht bewusst keine Rendite.** Sie zeigt die Zusage (Vola-Band,
Drawdown-Boden, jeweils mit Quantil), dass die Zusage out-of-sample geprueft ist, und im
Methoden-Expander auch, dass Ruhe Rendite kostet. Der Trendwert steht als Qualitaetsfilter
da, nicht als Alpha-Quelle.

### Schritt 5 erledigt — die Bewertungsachse ist Rueckschau, nicht Signal

**Erst gemessen, dann entschieden.** Die auffaellige Spalte `pctTargetHighPrice`
(IC 0,181) war weder Alpha noch Ueberlebensverzerrung — der Effekt ist bei den
**groessten und liquidesten** Werten am staerksten (IC 0,151 gegen 0,121 im
illiquidesten Drittel), also genau umgekehrt zu dem, was Delisting-Verzerrung
erzeugen wuerde.

Entscheidend ist das Horizont-Profil:

| Horizont | 5 T | 21 T | 63 T | 126 T | 252 T |
|---|---|---|---|---|---|
| gespeicherte Spalte | 0,072 | 0,151 | 0,253 | 0,340 | **0,453** |
| bewusst gebautes Look-ahead | 0,074 | 0,156 | 0,260 | 0,349 | 0,463 |

Ein echtes Signal **zerfaellt** mit dem Horizont. Dieses wird besser, je weiter man
schaut — die Signatur einer Zahl, die an einem spaeteren Preisniveau verankert ist.
Die Rangkorrelation zur absichtlich gebauten Look-ahead-Variante (heutiges Kursziel
geteilt durch den historischen Close) betraegt **0,976**: fuer die Rangfolge sind es
dieselbe Spalte.

**Das betrifft jede Fundamentalzahl aus dem Snapshot**, nicht nur das Kursziel:
`operatingMargins` IC 0,039, `returnOnAssets` 0,044, `revenueGrowth` 0,039 — heutige
Margen und Wachstumsraten beschreiben Firmen, die im gemessenen Zeitraum bereits gut
gelaufen sind. Rueckschau, kein Signal.

### Konsequenz: `asset_info_history` statt Reparaturversuch

`tradinglib/fundamentals_history.py` haengt bei jedem `get_asset_info.py`-Lauf einen
**datierten Schnappschuss** an, wenn sich eine der 26 verfolgten Kennzahlen geaendert
hat (Append-on-Change, relative Toleranz 1e-4 gegen Yahoo-Rundungsrauschen). Der
Sonntags-Job 09:00 laeuft bereits — die Reihe waechst ab jetzt von selbst.

**Nur preisunabhaengige Groessen** werden gespeichert: `forwardEps` und `bookValue`
statt KGV und KBV. Ein Verhaeltnis mit heutigem Preis im Nenner wuerde genau das
Artefakt zurueckholen; die Kennzahl entsteht beim Lesen gegen den historischen Close.
Ein Test nagelt fest, dass keine preisabgeleitete Spalte in `TRACKED_FIELDS` rutscht.

- Startpunkt gesetzt: `python -m tradinglib.fundamentals_history /seed` → 9.091 Zeilen
  zum 2026-09-12. Zweiter Lauf schreibt 0 (idempotent).
- Lesen: `asof_frame(tickers, datum)` liefert die letzte Zeile **am oder vor** dem
  Datum — und eine **leere** Tabelle fuer Daten vor Beginn der Reihe. Das ist die
  ehrliche Antwort, nicht die neueste Zeile.
- `rescore_db()` hat jetzt Docstring-Warnung **und** Log-Warnung: der Lauf ueberschreibt
  die Historie mit den heutigen Fundamentaldaten. Bis die Reihe einen Marktzyklus
  abdeckt, ist `/backfill` (nur preisabgeleitete Indikatoren) dem `/rescore` vorzuziehen.

**Bewusst NICHT gemacht:** `overallValueTrend` bleibt unveraendert — Live-Strategien
haengen an seinem heutigen Verhalten (siehe `Sum`-Docstring), und die neue Profil-Kette
nutzt ohnehin keine Fundamentaldaten. Die Frage „traegt die Bewertungsachse etwas bei?"
ist damit nicht beantwortet, sondern **beantwortbar gemacht** — in etwa einem Jahr.

---

## Kandidaten-Trichter kennt jetzt das Risikoprofil (2026-09-12)

Zwei neue Schritte in `candidates.find()`, beide optional und beide mit eigener
Trichter-Zeile:

| Schritt | Einstellung | Wirkung |
|---|---|---|
| **Risikoprofil** | `risk_profile` — `''` aus, `'user'` = das auf der Profilseite eingestellte, sonst ein Preset-Name | schneidet auf `atr/close <= Schnitt` (Fallback `logVola`) |
| **Trendwert** | `min_trend_score` 0–100, 0 = aus | `trendScore >= x`, bei Short gespiegelt zu `<= 100-x` |

**Die Reihenfolge ist der Punkt und steht so auch in der HELP-Seite:** Risikoprofil
**vor** dem Trendfilter. Gemessen 2020-2026 bringt eine Trendauswahl ueber das ganze
Universum keinen verlaesslichen Renditevorteil; auf das ruhige Ende beschraenkt
verdoppelt sich der Vorteil derselben Auswahl. Andersherum bleibt nichts davon.

Fallen, die in den Tests festgenagelt sind:

- **`trendScore == 0` heisst „nicht berechnet"**, nicht „am Boden" — solche Zeilen
  fallen raus und rutschen nicht durch. Ohne diese Pruefung waeren bei Short alle
  noch nicht gefuellten Titel plaetzlich Top-Kandidaten.
- **Fehlt die Spalte ganz** (alte Sim-DB), wird der Schritt uebersprungen und als
  „Spalte fehlt" vermerkt — nicht still gefiltert.
- **TEXT-Affinitaet**: `_mask_for` in `risk_profile.py` zieht `atr`/`close`/`logVola`
  jetzt durch `pd.to_numeric`; ein String-Vergleich haette die falsche Haelfte gewaehlt.

`RANK_COLUMNS` kennt zusaetzlich `trendScore` und `riskScore` als Sortierkriterium.

**Datenstand beachten:** `candidates._latest_rows` liest `asset_simulation_all.db`
**zuerst**. Solange dort der `/backfill:prof`-Lauf nicht durch ist, greift der
Trendwert-Schritt auf unbefuellte Zeilen und die Liste wird leer — der Risikoprofil-
Schritt funktioniert dagegen ueber den `atr`/`close`-Fallback sofort.

---

## Positionsgroesse nach Risikoprofil (2026-09-12)

Bisher war die Groesse **relativ**: inverse Vola, normiert auf die Tagesauswahl (live)
bzw. auf die Ø-Vola des Index (Backtest). Beide geben **immer** das ganze Budget aus,
egal was an dem Tag zur Auswahl steht. Neu und optional ist eine **absolute** Regel:

    Budget_i = Einsatz / num_assets × (Ziel-Vola / Vola_i),  geklammert auf [1/f, f]

Ein Titel mit der Ziel-Vola bekommt genau einen Anteil, ein ruhigerer mehr, ein
unruhigerer weniger. An einem Tag mit lauter unruhigen Titeln wird **weniger** gekauft,
statt still ein unruhiges Depot aufzubauen.

**Einheiten-Falle, der eigentliche Knackpunkt:** die gespeicherte Spalte `vola` ist eine
21-Bar-Standardabweichung in **Prozent** (Median ~10,5), die Profile sprechen in
**annualisierten Bruchteilen** (0,21–0,34). Umrechnung in `risk_profile.annualised_vol`:
`vola/100 × sqrt(252/21)` — Faktor 3,46. Ohne sie waere jedes Gewicht am unteren Anschlag
gelandet. Per Test festgenagelt (10,5 → 0,364).

**Drei Pfade, eine Formel** (`risk_profile.position_weight`), weil genau diese drei in der
Vergangenheit auseinandergelaufen sind:

| Pfad | Schalter |
|---|---|
| Strategy Finder / Multi Strategies | `sizing_cap = 'profile'` (Sidebar) |
| Signale-Tab | Profil des Nutzers, wenn `sizing` an |
| Trading-Agent | dito, ueber `risk_profile.sizing_profile(username)` |

`tests/test_profile_sizing.py` vergleicht Agent und Signale-Tab **direkt miteinander** und
beide gegen den Backtest-Pfad.

**Was das kostet, und das steht auch in der UI:** der Kapitaleinsatz liegt im Mittel bei
rund **85 %** statt 100 % (gemessen auf dem heutigen Universum, alle vier Profile:
Median-Gewicht 0,81–0,87). Das ist die Zusage, kein Fehler — wer voll investiert sein
will, laesst den Schalter aus. Vorgabe ist **aus**, damit bestehende Ergebnisse
vergleichbar bleiben.

Weitere Details: Klammer `MAX_WEIGHT_FACTOR = 2.0` (im Backtest `sizing_factor_max`);
`vola = 0` oder fehlend faellt auf den glatten Anteil zurueck statt auf unendlich;
`sizing_cap='profile'` wird zusaetzlich auf das freie Kapital gedeckelt. Der
Signale-Cache-Key enthaelt jetzt das Profil — sonst zeigte der Tab nach einem
Profilwechsel weiter die alten Stueckzahlen. Und `tests/conftest.py` setzt
`_sizing_profile`/`username` mit; die Liste dort muss bei jedem neuen Attribut wachsen,
das `buy_asset` liest.

### Messung: sizing_cap='profile' im Strategy Finder (2026-09-13)

Headless gefahren (PortfolioSimulator direkt mit dem Frame der Seite, keine
Streamlit-Instanz), Value Trend Strategy / ^SPX, 2023-2025, 15.000 EUR, 5 Slots,
ohne Gebuehren. 501 Ticker, 13.538 Kaufsignale.

| Modus | CAGR % | Vola | max DD % | Rendite/Vola | Ø Investitionsquote |
|---|---|---|---|---|---|
| none | 32,34 | 0,093 | -4,76 | 3,48 | 54,0 % |
| cash | 32,40 | 0,092 | -4,75 | 3,53 | 54,0 % |
| **normalized** | **35,80** | 0,097 | **-3,99** | **3,69** | 54,3 % |
| profile (balanced) | 28,00 | **0,079** | -4,10 | 3,53 | 46,4 % |

**Ergebnis, unbequem, aber eindeutig: `profile` ist risikobereinigt NICHT besser.**
Es liefert die niedrigste Portfolio-Vola — also genau das, was es zusagt — aber
`normalized` bleibt in Rendite/Vola (3,69) und Drawdown vorn.

Der Grund ist messbar: Trades (578), Positionszahl (4,50) und Haltedauer (6,3 Tage)
sind in **allen** Modi identisch; nur die Groesse unterscheidet sich. Und das
Ø-Gewicht liegt bei 0,98x — die Kaufsignale dieser Strategie im SPX haben Vola-Werte
so nah am Balanced-Ziel, dass praktisch **kein Tilt** entsteht. `profile` wirkt hier
wie `cash` mit 14 % weniger Kapital: CAGR-Verhaeltnis 0,864, Vola-Verhaeltnis 0,859,
Quoten-Verhaeltnis 0,859 — dieselbe Zahl.

Als **Risiko-Regler** funktioniert es dagegen sauber und monoton:

| Profil | Ziel-Vola | Ø Gewicht | CAGR % | Portfolio-Vola | max DD % | Rendite/Vola | Quote |
|---|---|---|---|---|---|---|---|
| conservative | 0,21 | 0,83x | 25,10 | 0,072 | -3,78 | 3,49 | 41,6 % |
| balanced | 0,25 | 0,98x | 28,00 | 0,079 | -4,10 | 3,53 | 46,4 % |
| dynamic | 0,28 | 1,10x | 30,14 | 0,085 | -4,39 | 3,55 | 49,8 % |
| offensive | 0,34 | 1,32x | 34,13 | 0,096 | -4,90 | 3,57 | 56,1 % |

Rendite und Risiko bewegen sich zusammen, Rendite/Vola bleibt flach (3,49-3,57). Der
Schalter waehlt das Risikoniveau, er erzeugt keinen Vorsprung — und darf auch nicht so
verkauft werden.

**Wo er sich lohnen kann:** Universen mit breiter Vola-Streuung (CRYPTO, COMMODITIES,
Small Caps) oder ein Ziel weit weg von der typischen Vola der Auswahl. Im SPX mit
Value-Trend-Signalen ist beides nicht gegeben.

**Grenzen dieser Messung:** ein Index, eine Strategie, ein Bullenmarkt, keine Kosten.
Die Engine hat ausserdem 510 Bars in BKNG und KLAC wegen inkonsistentem OHLC
uebersprungen (Datenqualitaet, nicht Sizing).

### Gegentest: profile-Sizing auf CRYPTO (2026-09-13)

Gleiche Methode wie der SPX-Lauf, aber bewusst das Gegenteil an Universum: 8 Coins
(BTC/ETH/SOL/XRP/BNB/TRX/XMR/DOGE, alle -EUR), 2023-2025, 3 Slots, 15.000 EUR,
Trendformel `(close > sma50) & (ewo > ewo_ema)` — die Value-Trend-Formel feuert auf
Krypto **nie** (`overallValueTrend` erreicht dort maximal 57 gegen eine Schwelle von 69,
ohne Fundamentaldaten kommen die Punkte nicht zusammen).

| Modus | CAGR % | Vola | max DD % | Rend./Vola | Quote | Ø Gewicht | Streuung |
|---|---|---|---|---|---|---|---|
| none / cash | 39,24 | 0,195 | -17,57 | 2,01 | 33,2 % | — | — |
| normalized | 41,82 | 0,217 | -20,93 | **1,93** | 35,3 % | — | — |
| profile: conservative | 27,42 | 0,134 | -10,61 | 2,05 | 20,3 % | 0,51 | 0,03 |
| profile: balanced | 27,42 | 0,134 | -10,61 | 2,05 | 20,3 % | 0,54 | 0,07 |
| profile: dynamic | 27,67 | 0,135 | -10,73 | 2,06 | 20,6 % | 0,57 | 0,10 |
| profile: offensive | 28,11 | 0,139 | -11,63 | 2,02 | 22,3 % | 0,65 | 0,16 |
| profile: Ziel 0,45 | 31,65 | 0,160 | -14,62 | 1,98 | 27,0 % | 0,83 | 0,23 |
| profile: Ziel 0,58 | 38,08 | 0,190 | -17,19 | 2,01 | 32,3 % | 1,07 | 0,30 |
| profile: Ziel 0,80 | 45,47 | 0,236 | -22,11 | 1,93 | 41,2 % | 1,44 | 0,37 |

**Drei Befunde:**

1. **Die ausgelieferten Profile sind fuer Krypto unbrauchbar als Regler.** Die
   annualisierte Vola der Kaufsignale liegt im Median bei **0,58** (p05 0,35, p95 0,87),
   die Aktien-Ziele bei 0,21-0,34. Konservativ und ausgewogen landen deshalb **beide**
   an der Klammer (Ø Gewicht 0,51/0,54, Streuung 0,03/0,07) und liefern Zeile fuer
   Zeile dasselbe Ergebnis. Mit einem passenden Ziel (0,58 = Median der Auswahl)
   reproduziert die Regel den ungesizten Lauf fast exakt (38,08 gegen 39,24 CAGR,
   0,190 gegen 0,195 Vola) — und der Tilt lebt wieder (Streuung 0,30).
2. **Auch hier kein risikobereinigter Vorsprung.** Rendite/Vola bleibt ueber die ganze
   Reihe zwischen 1,93 und 2,06 — bei echter Vola-Streuung (BTC 12,4 gegen DOGE 22,7).
   Die These „bei breiter Streuung zahlt sich der Tilt aus" ist damit **widerlegt**,
   nicht bestaetigt.
3. **Wofuer es trotzdem taugt:** nur diese Regel bringt den Drawdown eines
   Krypto-Depots von -21 % auf -11 %. `normalized` kann das konstruktiv nicht, weil es
   immer das volle Budget einsetzt — und schneidet hier mit 1,93 sogar am schlechtesten
   ab.

**Daraus umgesetzt:** die Profilseite warnt jetzt, wenn mehr als die Haelfte der Auswahl
an der Klammer haengt (`risk.sizing_saturated`), und nennt typische Vola gegen Ziel. Ohne
diesen Hinweis waere „konservativ und ausgewogen liefern dasselbe" ein stiller Fehler.

---

## Fix: "[BUY-ERROR] name 'trendScore' is not defined" im Asset Viewer (2026-09-13)

Die gespeicherte Buy-Formel `(trendScore>55) …` brach im Chart, sobald der
`prof`-Oszillator nicht ausgewaehlt war. Ursache ist eine Luecke im Versprechen
„gleiche Spaltennamen live und im Backtest": in `asset_simulation` stehen die
Spalten immer, im Live-Frame nur, solange das erzeugende Indikatormodul aktiv ist.
Dieselbe Klasse Fehler gab es vorher schon mit `overallValueTrend` (ovt), dort nur in
`market_overview_page` umgangen (leere Queries), nicht behoben.

**Behoben in `fetch_data.py`:**
- `indicators_for_expressions(expressions, selected)` liest die Tokens einer
  Buy/Sell-Formel und liefert die noch nicht aktiven Indikatoren, deren Spalten sie
  referenziert. Quelle ist `asset_perf2.INDICATOR_BACKFILL_MAP` (lazy importiert,
  einmal gecacht, `ohlc` ausgenommen) plus ovt (`overallTrend`,
  `overallValueTrend`, Praefix `ovtEma`). Faellt der Import aus, bleiben wenigstens
  die Profil-Spalten abgedeckt.
- `FetchData.fetch_data` rechnet diese Indikatoren **mit, zeichnet sie aber nicht** —
  sie kommen in eine lokale Liste, nicht in `self.indicators`.
- **`atr` gab es live gar nicht** (nur `fill_pdict` schreibt die Spalte). Der Ausdruck
  von der Risikoprofil-Seite `(atr / close <= …)` waere also der naechste Fehler
  gewesen. Jetzt als leichter Helfer neben `log_return`, ueber `Prof.atr_series` —
  dieselbe Formel wie die Engine, ein Test vergleicht beide.

**Verifiziert** gegen eine Kopie der DBs (TradingDB auf Scratch, damit ein
Yahoo-Fallback nicht in Produktion schreibt): ^GDAXI mit genau den Oszillatoren aus dem
Screenshot (ewo, rsi) und kurts gespeicherten Formeln — keine Fehlermeldung,
`trendScore`/`riskScore`/`atr` vorhanden, 39 Kaufsignale im Jahr, letzter Close
25.568,56 wie im Chart.

---

## ATC kausal (2026-09-13)

**Befund.** `Atc` legte EINEN Regressionskanal ueber den ganzen Frame und schrieb
dessen Gerade in die Spalten. Jeder vergangene Balken bekam damit einen Wert aus
einem Fit, der die spaeteren Balken schon kannte — im Chart **und** in den
gespeicherten Daten: `process_symbol` rechnet die Indikatoren einmal auf 2 Jahren
und liest sie danach nur zeilenweise aus, `/backfill:atc` sogar einmal auf 10 Jahren.
Gemessen an `asset_simulation_2024/2025` (SAP.DE, AAPL, ALV.DE): 98,8-99,6 % der
Schritte in `atc_top_high` exakt geradlinig, Abweichung vom tagesaktuellen Kanal im
Median 3-40 %.

Folge fuer kurts Sell-Formel `(High>=atc_top_high)&(rsi>=72)&(Low>atc_mid_high)`:
Chart ^GDAXI 1 Jahr **0** Treffer, im Tagesbetrieb (Kanal jeden Tag neu) **16**.
Ueber 31 Werte im Tagesbetrieb Ø 15 Treffer je Wert/Jahr, alle haben ausgeloest.

**Umbau in `tradinglib/indicator/atc.py`:**
- Jede Spalte (`atc_{top,mid,bot}_{high,low,zero}`, `atc_width[_pct]_*`) traegt je
  Balken den letzten Punkt des Kanals ueber die **nachlaufenden `lookback` Balken**
  (neuer Parameter, Default 252), mit denselben Anker-Regeln wie vorher.
- Geschlossene Kleinste-Quadrate-Loesung ueber Praefixsummen statt sklearn je Balken;
  Nullsteigungs-Suche je Balken vektorisiert. 10 Jahre Tageskurse 0,30 s.
  `use_exp_weight` hat keine Praefixsummen-Abkuerzung und faellt auf den (langsamen)
  sklearn-Pfad zurueck.
- **Tagescharts laden die volle lokale Historie nach** (wie `fps`/`prof`), gekappt auf
  `lookback` Balken vor dem ersten Chartbalken — sonst bekaemen die ersten Chart-Balken
  Kanaele ueber eine Handvoll Balken und andere Zahlen als der Backtest. 1-Jahres-Chart
  0,12 s. **Intraday- und Wochenframes** werden auf ihren eigenen Balken gerechnet.
- **Gezeichnet wird weiter der heutige Kanal als Gerade** (`self.channels`), die
  Beschriftung ebenso. Eine Linie durch die kausalen Spalten waere ein Zickzack, weil
  jeder Punkt aus einem anderen Fit stammt.
- Der Wert am **letzten Balken ist unveraendert**, solange der Frame den Lookback
  abdeckt; nur die Historie aendert sich. **Sichtbare Aenderung:** laengere Charts
  (2 Jahre, Intraday mit vielen Balken) zeigen den Kanal nur noch ueber hoechstens
  `lookback` Balken.

`atc_mid_high` steht jetzt auch in `INDICATOR_BACKFILL_MAP['atc']` und im pdict — die
Chart-Variante der Sell-Formel lief bisher nur live, nie im Backtest.

**Tests:** der Test, der die konstante Breite der SPALTE festschrieb, war genau das
alte Verhalten und prueft jetzt den gezeichneten Kanal. Neu: Zukunft abschneiden aendert
keinen vergangenen Wert; jeder Balken == frischer sklearn-Fit auf seinem eigenen
Fenster; geschlossene Loesung == LinearRegression; letzter Balken unveraendert;
Intraday bleibt Intraday.

**Nachweis am gemeldeten Fall** (DB-Kopie, ^GDAXI 1y, ewo+rsi, kurts Formeln):
Sell-Bedingung roh 17 Tage (vorher 0), 5 Verkaufsmarker (vorher 0). Die Kaufmarker
fallen von 39 auf 34 — die Buy-Formel trifft weiter an 39 Tagen, aber an 5 davon
steht jetzt ein Verkauf: bei offener Position hat ein Sell auf demselben Balken
Vorrang (`BuySellSignalGenerator.apply_signals`).

**OFFEN:** die gespeicherten ATC-Werte in allen `asset_simulation_*.db` sind noch die
alten, nicht kausalen. Erst `python asset_perf2.py /backfill:atc /force` (je Jahr mit
`/year:YYYY`, dazu `/all`) bringt den Backtest auf denselben Stand wie den Chart.
Ueberschreibt bestehende Werte → Backtest-Ergebnisse ATC-basierter Formeln aendern sich.
